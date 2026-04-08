"""Обработчики для простой покупки подписки."""

import html
from datetime import UTC, datetime
from typing import Any

import structlog
from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_happ_download_button_row
from app.localization.texts import get_texts
from app.services.payment_service import PaymentService
from app.services.subscription_purchase_service import SubscriptionPurchaseService
from app.states import SubscriptionStates
from app.utils.decorators import error_handler
from app.utils.pricing_utils import compute_simple_subscription_price
from app.utils.subscription_utils import (
    get_display_subscription_link,
    resolve_simple_subscription_device_limit,
)


logger = structlog.get_logger(__name__)


@error_handler
async def start_simple_subscription_purchase(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Начинает процесс простой покупки подписки."""
    texts = get_texts(db_user.language)

    if not settings.SIMPLE_SUBSCRIPTION_ENABLED:
        await callback.answer('❌ 暂时无法进行简单订阅购买', show_alert=True)
        return

    if settings.is_multi_tariff_enabled():
        await callback.answer('使用计划选择来管理订阅', show_alert=True)
        return

    # Проверка ограничения на покупку/продление подписки
    if getattr(db_user, 'restriction_subscription', False):
        reason = html.escape(getattr(db_user, 'restriction_reason', None) or 'Действие ограничено администратором')
        support_url = settings.get_support_contact_url()
        keyboard = []
        if support_url:
            keyboard.append([types.InlineKeyboardButton(text='🆘 申诉', url=support_url)])
        keyboard.append([types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription')])

        await callback.message.edit_text(
            f'🚫 <b>订阅购买有限</b>\n\n{reason}\n\n如果您认为这是一个错误，您可以对该决定提出申诉。',
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
        await callback.answer()
        return

    # Проверяем, есть ли у пользователя подписка
    from app.database.crud.subscription import get_subscription_by_user_id

    current_subscription = await get_subscription_by_user_id(db, db_user.id)

    device_limit = resolve_simple_subscription_device_limit()

    # При продлении учитываем количество устройств из текущей подписки
    # (независимо от того, включён ли выбор устройств)
    if current_subscription:
        current_device_limit = current_subscription.device_limit or device_limit
        # Используем максимум из текущего и дефолтного
        device_limit = max(device_limit, current_device_limit)

    # Подготовим параметры простой подписки
    subscription_params = {
        'period_days': settings.SIMPLE_SUBSCRIPTION_PERIOD_DAYS,
        'device_limit': device_limit,
        'traffic_limit_gb': settings.SIMPLE_SUBSCRIPTION_TRAFFIC_GB,
        'squad_uuid': settings.SIMPLE_SUBSCRIPTION_SQUAD_UUID,
    }

    # Сохраняем параметры в состояние
    await state.update_data(subscription_params=subscription_params)

    data = await state.get_data()
    resolved_squad_uuid = await _ensure_simple_subscription_squad_uuid(
        db,
        state,
        subscription_params,
        user_id=db_user.id,
        state_data=data,
    )

    price_kopeks, price_breakdown = await _calculate_simple_subscription_price(
        db,
        subscription_params,
        user=db_user,
        resolved_squad_uuid=resolved_squad_uuid,
    )

    period_days = subscription_params['period_days']
    user_balance_kopeks = getattr(db_user, 'balance_kopeks', 0)

    logger.warning(
        'SIMPLE_SUBSCRIPTION_DEBUG_START | user=%s | period=%s | base=%s | traffic=%s | devices=%s | servers=%s | discount=%s | total=%s | squads=%s',
        db_user.id,
        period_days,
        price_breakdown.get('base_price', 0),
        price_breakdown.get('traffic_price', 0),
        price_breakdown.get('devices_price', 0),
        price_breakdown.get('servers_price', 0),
        price_breakdown.get('total_discount', 0),
        price_kopeks,
        ','.join(price_breakdown.get('resolved_squad_uuids', []))
        if price_breakdown.get('resolved_squad_uuids')
        else 'none',
    )

    can_pay_from_balance = user_balance_kopeks >= price_kopeks
    logger.warning(
        'SIMPLE_SUBSCRIPTION_DEBUG_START_BALANCE | user= | balance= | min_required= | can_pay',
        db_user_id=db_user.id,
        user_balance_kopeks=user_balance_kopeks,
        price_kopeks=price_kopeks,
        can_pay_from_balance=can_pay_from_balance,
    )

    # Проверяем, является ли у пользователя текущая подписка активной платной подпиской
    has_active_paid_subscription = False
    trial_notice = ''
    if current_subscription:
        if not getattr(current_subscription, 'is_trial', False) and current_subscription.is_active:
            # Это платная активная подписка - требуем подтверждение
            has_active_paid_subscription = True
        elif getattr(current_subscription, 'is_trial', False):
            # Это тестовая подписка
            try:
                days_left = max(0, (current_subscription.end_date - datetime.now(UTC)).days)
            except Exception:
                days_left = 0
            key = (
                'SIMPLE_SUBSCRIPTION_TRIAL_NOTICE_ACTIVE'
                if current_subscription.is_active
                else 'SIMPLE_SUBSCRIPTION_TRIAL_NOTICE_TRIAL'
            )
            trial_notice = texts.t(
                key,
                'ℹ️ 您已经有试用订阅。它将在 {days} 天后过期。',
            ).format(days=days_left)

    server_label = _get_simple_subscription_server_label(
        texts,
        subscription_params,
        resolved_squad_uuid,
    )
    show_devices = settings.is_devices_selection_enabled()

    message_lines = [
        '⚡ <b>Простая покупка подписки</b>',
        '',
        f'📅 Период: {subscription_params["period_days"]} дней',
    ]

    if show_devices:
        message_lines.append(f'📱 Устройства: {subscription_params["device_limit"]}')

    traffic_limit_gb = subscription_params['traffic_limit_gb']
    traffic_label = 'Безлимит' if traffic_limit_gb == 0 else f'{traffic_limit_gb} ГБ'

    message_lines.extend(
        [
            f'📊 Трафик: {traffic_label}',
            f'🌍 Сервер: {server_label}',
            '',
            f'💰 Стоимость: {settings.format_price(price_kopeks)}',
            f'💳 Ваш баланс: {settings.format_price(user_balance_kopeks)}',
            '',
        ]
    )

    # Если у пользователя уже есть активная платная подписка, требуем подтверждение
    if has_active_paid_subscription:
        # У пользователя уже есть активная платная подписка
        message_lines.append(
            '⚠️ У вас уже есть активная платная подписка. '
            'Покупка простой подписки изменит параметры вашей текущей подписки. '
            'Требуется подтверждение.'
        )
        message_text = '\n'.join(message_lines)

        # Клавиатура с подтверждением
        keyboard_rows = [
            [
                types.InlineKeyboardButton(
                    text='✅ 确认购买', callback_data='simple_subscription_confirm_purchase'
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')],
        ]
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    else:
        # У пользователя нет активной платной подписки (или есть только пробная)
        # Показываем стандартный выбор метода оплаты
        if can_pay_from_balance:
            message_lines.append('Вы можете оплатить подписку с баланса или выбрать другой способ оплаты.')
        else:
            message_lines.append('Баланс пока недостаточный для мгновенной оплаты. Выберите подходящий способ оплаты:')

        message_text = '\n'.join(message_lines)

        if trial_notice:
            message_text = f'{trial_notice}\n\n{message_text}'

        methods_keyboard = _get_simple_subscription_payment_keyboard(db_user.language)
        keyboard_rows = []

        if can_pay_from_balance:
            keyboard_rows.append(
                [
                    types.InlineKeyboardButton(
                        text='✅ 从余额中支付',
                        callback_data='simple_subscription_pay_with_balance',
                    )
                ]
            )

        keyboard_rows.extend(methods_keyboard.inline_keyboard)

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    await callback.message.edit_text(message_text, reply_markup=keyboard, parse_mode='HTML')

    # Устанавливаем соответствующее состояние
    if has_active_paid_subscription:
        await state.set_state(SubscriptionStates.waiting_for_simple_subscription_confirmation)
    else:
        await state.set_state(SubscriptionStates.waiting_for_simple_subscription_payment_method)
    await callback.answer()


async def _calculate_simple_subscription_price(
    db: AsyncSession,
    params: dict,
    *,
    user: User | None = None,
    resolved_squad_uuid: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Рассчитывает цену простой подписки."""

    resolved_uuids = [resolved_squad_uuid] if resolved_squad_uuid else None
    return await compute_simple_subscription_price(
        db,
        params,
        user=user,
        resolved_squad_uuids=resolved_uuids,
    )


def _get_simple_subscription_payment_keyboard(language: str) -> types.InlineKeyboardMarkup:
    """Создает клавиатуру с методами оплаты для простой подписки."""
    texts = get_texts(language)
    keyboard = []

    # Добавляем доступные методы оплаты
    if settings.TELEGRAM_STARS_ENABLED:
        keyboard.append(
            [types.InlineKeyboardButton(text='⭐ Telegram Stars', callback_data='simple_subscription_stars')]
        )

    if settings.is_yookassa_enabled():
        yookassa_methods = []
        if settings.YOOKASSA_SBP_ENABLED:
            yookassa_methods.append(
                types.InlineKeyboardButton(text='🏦 YooKassa (SBP)', callback_data='simple_subscription_yookassa_sbp')
            )
        yookassa_methods.append(
            types.InlineKeyboardButton(text='💳 YooKassa（地图）', callback_data='simple_subscription_yookassa')
        )
        if yookassa_methods:
            keyboard.append(yookassa_methods)

    if settings.is_cryptobot_enabled():
        keyboard.append(
            [types.InlineKeyboardButton(text='🪙 CryptoBot', callback_data='simple_subscription_cryptobot')]
        )

    if settings.is_heleket_enabled():
        keyboard.append([types.InlineKeyboardButton(text='🪙 Heleket', callback_data='simple_subscription_heleket')])

    if settings.is_mulenpay_enabled():
        mulenpay_name = settings.get_mulenpay_display_name()
        keyboard.append(
            [types.InlineKeyboardButton(text=f'💳 {mulenpay_name}', callback_data='simple_subscription_mulenpay')]
        )

    if settings.is_pal24_enabled():
        keyboard.append([types.InlineKeyboardButton(text='💳 PayPalych', callback_data='simple_subscription_pal24')])

    if settings.is_wata_enabled():
        keyboard.append([types.InlineKeyboardButton(text='💳 WATA', callback_data='simple_subscription_wata')])

    # Кнопка назад
    keyboard.append([types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')])

    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def _get_simple_subscription_server_label(
    texts,
    subscription_params: dict[str, Any],
    resolved_squad_uuid: str | None = None,
) -> str:
    """Возвращает локализованное описание выбранного сервера."""

    if subscription_params.get('squad_uuid'):
        return texts.t('SIMPLE_SUBSCRIPTION_SERVER_SELECTED', '已选择')

    if resolved_squad_uuid:
        return texts.t(
            'SIMPLE_SUBSCRIPTION_SERVER_ASSIGNED',
            '自动分配',
        )

    return texts.t('SIMPLE_SUBSCRIPTION_SERVER_ANY', '任何可用')


async def _ensure_simple_subscription_squad_uuid(
    db: AsyncSession,
    state: FSMContext,
    subscription_params: dict[str, Any],
    *,
    user_id: int | None = None,
    state_data: dict[str, Any] | None = None,
) -> str | None:
    """Определяет UUID сквада для простой подписки."""

    explicit_uuid = subscription_params.get('squad_uuid')
    if explicit_uuid:
        return explicit_uuid

    if state_data is None:
        state_data = await state.get_data()

    resolved_uuid = state_data.get('resolved_squad_uuid')
    if resolved_uuid:
        return resolved_uuid

    try:
        from app.database.crud.server_squad import get_random_active_squad_uuid

        resolved_uuid = await get_random_active_squad_uuid(db)
    except Exception as error:  # pragma: no cover - defensive logging
        logger.error('SIMPLE_SUBSCRIPTION_RANDOM_SQUAD_ERROR | user= | error', user_id=user_id, error=error)
        return None

    if resolved_uuid:
        await state.update_data(resolved_squad_uuid=resolved_uuid)
        logger.info(
            'SIMPLE_SUBSCRIPTION_RANDOM_SQUAD_ASSIGNED | user= | squad', user_id=user_id, resolved_uuid=resolved_uuid
        )

    return resolved_uuid


@error_handler
async def handle_simple_subscription_pay_with_balance(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Обрабатывает оплату простой подписки с баланса."""
    texts = get_texts(db_user.language)

    data = await state.get_data()
    subscription_params = data.get('subscription_params', {})

    if not subscription_params:
        await callback.answer('❌ 订阅数据已过时。请重新开始。', show_alert=True)
        return

    # Проверяем, имеет ли пользователь активную платную подписку
    from app.database.crud.subscription import get_subscription_by_user_id

    current_subscription = await get_subscription_by_user_id(db, db_user.id)

    if current_subscription and not getattr(current_subscription, 'is_trial', False) and current_subscription.is_active:
        # У пользователя есть активная платная подписка - требуем подтверждение
        await callback.answer(
            '⚠️ 您已经有有效的付费订阅。请确认您的购买。', show_alert=True
        )
        return

    resolved_squad_uuid = await _ensure_simple_subscription_squad_uuid(
        db,
        state,
        subscription_params,
        user_id=db_user.id,
        state_data=data,
    )

    # Lock user BEFORE pricing to prevent TOCTOU
    from app.database.crud.user import lock_user_for_pricing, subtract_user_balance

    db_user = await lock_user_for_pricing(db, db_user.id)

    # Рассчитываем цену подписки (group discounts per-category)
    price_kopeks, price_breakdown = await _calculate_simple_subscription_price(
        db,
        subscription_params,
        user=db_user,
        resolved_squad_uuid=resolved_squad_uuid,
    )

    # PricingEngine already applies promo-offer discount inside calculate_classic_new_subscription_price.
    # Only determine whether to consume the offer (zero it out after use).
    from app.utils.promo_offer import get_user_active_promo_discount_percent

    consume_promo = get_user_active_promo_discount_percent(db_user) > 0

    total_required = price_kopeks
    logger.warning(
        'SIMPLE_SUBSCRIPTION_DEBUG_PAY_BALANCE | user= | period= | base= | traffic= | devices= | servers= | discount= | total_required= | balance',
        db_user_id=db_user.id,
        subscription_params=subscription_params['period_days'],
        price_breakdown=price_breakdown.get('base_price', 0),
        price_breakdown_2=price_breakdown.get('traffic_price', 0),
        price_breakdown_3=price_breakdown.get('devices_price', 0),
        price_breakdown_4=price_breakdown.get('servers_price', 0),
        price_breakdown_5=price_breakdown.get('total_discount', 0),
        total_required=total_required,
        getattr=getattr(db_user, 'balance_kopeks', 0),
    )

    # Проверяем баланс пользователя
    user_balance_kopeks = getattr(db_user, 'balance_kopeks', 0)

    if total_required > 0 and user_balance_kopeks < total_required:
        await callback.answer('❌ 您的余额中没有足够的资金来支付订阅费用', show_alert=True)
        return

    try:
        # Списываем средства с баланса пользователя
        purchase_description = f'Оплата подписки на {subscription_params["period_days"]} дней'
        success = await subtract_user_balance(
            db,
            db_user,
            price_kopeks,
            purchase_description,
            consume_promo_offer=consume_promo,
            mark_as_paid_subscription=True,
        )

        if not success:
            await callback.answer('❌ 从余额中扣除资金时出错', show_alert=True)
            return

        # Создаём транзакцию для учёта списания
        from app.database.crud.transaction import create_transaction
        from app.database.models import PaymentMethod, TransactionType

        transaction = await create_transaction(
            db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=price_kopeks,
            description=purchase_description,
            payment_method=PaymentMethod.BALANCE,
        )

        # Проверяем, есть ли у пользователя уже подписка
        from app.database.crud.subscription import extend_subscription, get_subscription_by_user_id

        existing_subscription = await get_subscription_by_user_id(db, db_user.id)

        if existing_subscription:
            # Если подписка уже существует (платная или тестовая), продлеваем её
            # Сохраняем информацию о текущей подписке, особенно является ли она пробной
            was_trial = getattr(existing_subscription, 'is_trial', False)

            subscription = await extend_subscription(
                db=db,
                subscription=existing_subscription,
                days=subscription_params['period_days'],
                traffic_limit_gb=subscription_params['traffic_limit_gb'],
                device_limit=subscription_params['device_limit'],
                connected_squads=[resolved_squad_uuid] if resolved_squad_uuid else None,
            )

            # Если текущая подписка была пробной, и мы обновляем её
            # нужно изменить статус подписки
            if was_trial:
                from app.database.models import SubscriptionStatus

                # Переводим подписку из пробной в активную платную
                subscription.status = SubscriptionStatus.ACTIVE.value
                subscription.is_trial = False

            await db.commit()
            await db.refresh(subscription)
        else:
            # Если подписки нет, создаём новую
            from app.database.crud.subscription import create_paid_subscription

            subscription = await create_paid_subscription(
                db=db,
                user_id=db_user.id,
                duration_days=subscription_params['period_days'],
                traffic_limit_gb=subscription_params['traffic_limit_gb'],
                device_limit=subscription_params['device_limit'],
                connected_squads=[resolved_squad_uuid] if resolved_squad_uuid else [],
                update_server_counters=True,
            )

        if not subscription:
            # Возвращаем средства на баланс в случае ошибки
            from app.services.payment_service import add_user_balance

            await add_user_balance(
                db,
                db_user.id,
                price_kopeks,
                f'Возврат средств за неудавшуюся подписку на {subscription_params["period_days"]} дней',
            )
            await callback.answer('❌ 创建订阅时出错。资金已退回余额。', show_alert=True)
            return

        # Обновляем баланс пользователя
        await db.refresh(db_user)

        # Обновляем или создаём ссылку подписки в RemnaWave
        try:
            from app.services.subscription_service import SubscriptionService

            subscription_service = SubscriptionService()
            remnawave_user = await subscription_service.create_remnawave_user(db, subscription)
            if remnawave_user:
                await db.refresh(subscription)
        except Exception as sync_error:
            logger.error(
                'Ошибка синхронизации подписки с RemnaWave для пользователя',
                db_user_id=db_user.id,
                sync_error=sync_error,
                exc_info=True,
            )

        # Отправляем уведомление об успешной покупке
        server_label = _get_simple_subscription_server_label(
            texts,
            subscription_params,
            resolved_squad_uuid,
        )
        show_devices = settings.is_devices_selection_enabled()

        success_lines = [
            '✅ <b>Подписка успешно активирована!</b>',
            '',
            f'📅 Период: {subscription_params["period_days"]} дней',
        ]

        if show_devices:
            success_lines.append(f'📱 Устройства: {subscription_params["device_limit"]}')

        success_traffic_gb = subscription_params['traffic_limit_gb']
        success_traffic_label = 'Безлимит' if success_traffic_gb == 0 else f'{success_traffic_gb} ГБ'

        success_lines.extend(
            [
                f'📊 Трафик: {success_traffic_label}',
                f'🌍 Сервер: {server_label}',
                '',
                f'💰 Списано с баланса: {settings.format_price(price_kopeks)}',
                f'💳 Ваш баланс: {settings.format_price(db_user.balance_kopeks)}',
                '',
                "🔗 Для подключения перейдите в раздел 'Подключиться'",
            ]
        )

        success_message = '\n'.join(success_lines)

        connect_mode = settings.CONNECT_BUTTON_MODE
        subscription_link = get_display_subscription_link(subscription)
        connect_button_text = texts.t('CONNECT_BUTTON', '🔗连接')

        def _fallback_connect_button() -> types.InlineKeyboardButton:
            return types.InlineKeyboardButton(
                text=connect_button_text,
                callback_data='subscription_connect',
            )

        if connect_mode == 'miniapp_subscription':
            if subscription_link:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        web_app=types.WebAppInfo(url=subscription_link),
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        elif connect_mode == 'miniapp_custom':
            custom_url = settings.MINIAPP_CUSTOM_URL
            if custom_url:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        web_app=types.WebAppInfo(url=custom_url),
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        elif connect_mode == 'link':
            if subscription_link:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        url=subscription_link,
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        elif connect_mode == 'happ_cryptolink':
            if subscription_link:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        callback_data='open_subscription_link',
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        else:
            connect_row = [_fallback_connect_button()]

        keyboard_rows = [connect_row]

        happ_row = get_happ_download_button_row(texts)
        if happ_row:
            keyboard_rows.append(happ_row)

        keyboard_rows.append([types.InlineKeyboardButton(text='🏠 主页', callback_data='back_to_menu')])

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        await callback.message.edit_text(success_message, reply_markup=keyboard, parse_mode='HTML')

        # Отправляем уведомление админам
        try:
            from app.services.admin_notification_service import AdminNotificationService

            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_purchase_notification(
                db,
                db_user,
                subscription,
                transaction,
                subscription_params['period_days'],
                False,  # was_trial_conversion
                amount_kopeks=price_kopeks,
                purchase_type='renewal' if existing_subscription else 'first_purchase',
            )
        except Exception as e:
            logger.error('Ошибка отправки уведомления админам о покупке', error=e)

        await state.clear()
        await callback.answer()

        logger.info(
            'Пользователь успешно купил подписку с баланса на ₽',
            telegram_id=db_user.telegram_id,
            price_kopeks=price_kopeks / 100,
        )

    except Exception as error:
        logger.error(
            'Ошибка оплаты простой подписки с баланса для пользователя',
            db_user_id=db_user.id,
            error=error,
            exc_info=True,
        )
        await callback.answer(
            '❌ 订阅付款错误。请稍后重试或联系支持人员。',
            show_alert=True,
        )
        await state.clear()


@error_handler
async def handle_simple_subscription_pay_with_balance_disabled(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Показывает уведомление, если баланса недостаточно для прямой оплаты."""
    await callback.answer(
        '❌ 余额资金不足。充值您的余额或选择其他付款方式。',
        show_alert=True,
    )


@error_handler
async def handle_simple_subscription_other_payment_methods(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Обрабатывает выбор других способов оплаты."""
    texts = get_texts(db_user.language)

    data = await state.get_data()
    subscription_params = data.get('subscription_params', {})

    if not subscription_params:
        await callback.answer('❌ 订阅数据已过时。请重新开始。', show_alert=True)
        return

    resolved_squad_uuid = await _ensure_simple_subscription_squad_uuid(
        db,
        state,
        subscription_params,
        user_id=db_user.id,
        state_data=data,
    )

    # Рассчитываем цену подписки
    price_kopeks, price_breakdown = await _calculate_simple_subscription_price(
        db,
        subscription_params,
        user=db_user,
        resolved_squad_uuid=resolved_squad_uuid,
    )

    user_balance_kopeks = getattr(db_user, 'balance_kopeks', 0)
    can_pay_from_balance = user_balance_kopeks >= price_kopeks
    logger.warning(
        'SIMPLE_SUBSCRIPTION_DEBUG_METHODS | user= | balance= | base= | traffic= | devices= | servers= | discount= | total_required= | can_pay',
        db_user_id=db_user.id,
        user_balance_kopeks=user_balance_kopeks,
        price_breakdown=price_breakdown.get('base_price', 0),
        price_breakdown_2=price_breakdown.get('traffic_price', 0),
        price_breakdown_3=price_breakdown.get('devices_price', 0),
        price_breakdown_4=price_breakdown.get('servers_price', 0),
        price_breakdown_5=price_breakdown.get('total_discount', 0),
        price_kopeks=price_kopeks,
        can_pay_from_balance=can_pay_from_balance,
    )

    # Отображаем доступные методы оплаты
    server_label = _get_simple_subscription_server_label(
        texts,
        subscription_params,
        resolved_squad_uuid,
    )
    show_devices = settings.is_devices_selection_enabled()

    message_lines = [
        '💳 <b>Оплата подписки</b>',
        '',
        f'📅 Период: {subscription_params["period_days"]} дней',
    ]

    if show_devices:
        message_lines.append(f'📱 Устройства: {subscription_params["device_limit"]}')

    payment_traffic_gb = subscription_params['traffic_limit_gb']
    payment_traffic_label = 'Безлимит' if payment_traffic_gb == 0 else f'{payment_traffic_gb} ГБ'

    message_lines.extend(
        [
            f'📊 Трафик: {payment_traffic_label}',
            f'🌍 Сервер: {server_label}',
            '',
            f'💰 Стоимость: {settings.format_price(price_kopeks)}',
            '',
            (
                'Вы можете оплатить подписку с баланса или выбрать другой способ оплаты:'
                if can_pay_from_balance
                else 'Выберите подходящий способ оплаты:'
            ),
        ]
    )

    message_text = '\n'.join(message_lines)

    base_keyboard = _get_simple_subscription_payment_keyboard(db_user.language)
    keyboard_rows = []

    if can_pay_from_balance:
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text='✅ 从余额中支付', callback_data='simple_subscription_pay_with_balance'
                )
            ]
        )

    keyboard_rows.extend(base_keyboard.inline_keyboard)
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    await callback.message.edit_text(message_text, reply_markup=keyboard, parse_mode='HTML')

    await callback.answer()


@error_handler
async def handle_simple_subscription_payment_method(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Обрабатывает выбор метода оплаты для простой подписки."""
    texts = get_texts(db_user.language)

    data = await state.get_data()
    subscription_params = data.get('subscription_params', {})

    if not subscription_params:
        await callback.answer('❌ 订阅数据已过时。请重新开始。', show_alert=True)
        return

    # Проверяем, имеет ли пользователь активную платную подписку
    from app.database.crud.subscription import get_subscription_by_user_id

    current_subscription = await get_subscription_by_user_id(db, db_user.id)

    if current_subscription and not getattr(current_subscription, 'is_trial', False) and current_subscription.is_active:
        # У пользователя есть активная платная подписка - показываем сообщение
        await callback.answer(
            '⚠️ 您已经有有效的付费订阅。请通过主菜单确认您的购买。',
            show_alert=True,
        )
        return

    payment_method = callback.data.replace('simple_subscription_', '')

    try:
        payment_service = PaymentService(callback.bot)
        purchase_service = SubscriptionPurchaseService()

        resolved_squad_uuid = await _ensure_simple_subscription_squad_uuid(
            db,
            state,
            subscription_params,
            user_id=db_user.id,
            state_data=data,
        )

        # Рассчитываем цену подписки (group discounts per-category)
        price_kopeks, _ = await _calculate_simple_subscription_price(
            db,
            subscription_params,
            user=db_user,
            resolved_squad_uuid=resolved_squad_uuid,
        )

        # Apply promo-offer discount on top of group discounts (consistent with balance-pay path)
        from app.services.pricing_engine import PricingEngine
        from app.utils.promo_offer import get_user_active_promo_discount_percent

        offer_pct = get_user_active_promo_discount_percent(db_user)
        if offer_pct > 0:
            price_kopeks = PricingEngine.apply_discount(price_kopeks, offer_pct)

        if payment_method == 'stars':
            # Оплата через Telegram Stars
            order = await purchase_service.create_subscription_order(
                db=db,
                user_id=db_user.id,
                period_days=subscription_params['period_days'],
                device_limit=subscription_params['device_limit'],
                traffic_limit_gb=subscription_params['traffic_limit_gb'],
                squad_uuid=resolved_squad_uuid,
                payment_method='telegram_stars',
                total_price_kopeks=price_kopeks,
            )

            if not order:
                await callback.answer('❌ 无法准备订单。请稍后重试。', show_alert=True)
                return

            stars_count = settings.rubles_to_stars(settings.kopeks_to_rubles(price_kopeks))

            stars_traffic_gb = subscription_params['traffic_limit_gb']
            stars_traffic_label = 'Безлимит' if stars_traffic_gb == 0 else f'{stars_traffic_gb} ГБ'

            await callback.bot.send_invoice(
                chat_id=callback.from_user.id,
                title=f'Подписка на {subscription_params["period_days"]} дней',
                description=(
                    f'Простая покупка подписки\n'
                    f'Период: {subscription_params["period_days"]} дней\n'
                    f'Устройства: {subscription_params["device_limit"]}\n'
                    f'Трафик: {stars_traffic_label}'
                ),
                payload=(f'simple_sub_{db_user.id}_{order.id}_{subscription_params["period_days"]}'),
                provider_token='',  # Пустой токен для Telegram Stars
                currency='XTR',  # Telegram Stars
                prices=[types.LabeledPrice(label='Подписка', amount=stars_count)],
            )

            await state.clear()
            await callback.answer()

        elif payment_method in ['yookassa', 'yookassa_sbp']:
            # Оплата через YooKassa
            if not settings.is_yookassa_enabled():
                await callback.answer('❌暂时无法通过YooKassa付款', show_alert=True)
                return

            if payment_method == 'yookassa_sbp' and not settings.YOOKASSA_SBP_ENABLED:
                await callback.answer('❌暂时无法使用SBP付款', show_alert=True)
                return

            # Создаем заказ на подписку
            order = await purchase_service.create_subscription_order(
                db=db,
                user_id=db_user.id,
                period_days=subscription_params['period_days'],
                device_limit=subscription_params['device_limit'],
                traffic_limit_gb=subscription_params['traffic_limit_gb'],
                squad_uuid=resolved_squad_uuid,
                payment_method='yookassa_sbp' if payment_method == 'yookassa_sbp' else 'yookassa',
                total_price_kopeks=price_kopeks,
            )

            if not order:
                await callback.answer('❌ 创建订单时出错', show_alert=True)
                return

            # Создаем платеж через YooKassa
            if payment_method == 'yookassa_sbp':
                payment_result = await payment_service.create_yookassa_sbp_payment(
                    db=db,
                    user_id=db_user.id,
                    amount_kopeks=price_kopeks,
                    description=f'Оплата подписки на {subscription_params["period_days"]} дней',
                    receipt_email=db_user.email if hasattr(db_user, 'email') and db_user.email else None,
                    receipt_phone=db_user.phone if hasattr(db_user, 'phone') and db_user.phone else None,
                    metadata={
                        'user_telegram_id': str(db_user.telegram_id),
                        'user_username': db_user.username or '',
                        'order_id': str(order.id),
                        'subscription_id': str(order.id),
                        'subscription_period': str(subscription_params['period_days']),
                        'payment_purpose': 'simple_subscription_purchase',
                    },
                )
            else:
                payment_result = await payment_service.create_yookassa_payment(
                    db=db,
                    user_id=db_user.id,
                    amount_kopeks=price_kopeks,
                    description=f'Оплата подписки на {subscription_params["period_days"]} дней',
                    receipt_email=db_user.email if hasattr(db_user, 'email') and db_user.email else None,
                    receipt_phone=db_user.phone if hasattr(db_user, 'phone') and db_user.phone else None,
                    metadata={
                        'user_telegram_id': str(db_user.telegram_id),
                        'user_username': db_user.username or '',
                        'order_id': str(order.id),
                        'subscription_id': str(order.id),
                        'subscription_period': str(subscription_params['period_days']),
                        'payment_purpose': 'simple_subscription_purchase',
                    },
                )

            if not payment_result:
                await callback.answer('❌ 创建付款时出错', show_alert=True)
                return

            # Отправляем QR-код и/или ссылку для оплаты
            confirmation_url = payment_result.get('confirmation_url')
            qr_confirmation_data = payment_result.get('qr_confirmation_data')

            if not confirmation_url and not qr_confirmation_data:
                await callback.answer('❌ 接收付款数据错误', show_alert=True)
                return

            # Подготовим QR-код для вставки в основное сообщение
            qr_photo = None
            if qr_confirmation_data or confirmation_url:
                try:
                    # Импортируем необходимые модули для генерации QR-кода
                    from io import BytesIO

                    import qrcode
                    from aiogram.types import BufferedInputFile

                    # Используем qr_confirmation_data если доступно, иначе confirmation_url
                    qr_data = qr_confirmation_data or confirmation_url

                    # Создаем QR-код из полученных данных
                    qr = qrcode.QRCode(version=1, box_size=10, border=5)
                    qr.add_data(qr_data)
                    qr.make(fit=True)

                    img = qr.make_image(fill_color='black', back_color='white')

                    # Сохраняем изображение в байты
                    img_bytes = BytesIO()
                    img.save(img_bytes, format='PNG')
                    img_bytes.seek(0)

                    qr_photo = BufferedInputFile(img_bytes.getvalue(), filename='qrcode.png')
                except ImportError:
                    logger.warning('qrcode библиотека не установлена, QR-код не будет сгенерирован')
                except Exception as e:
                    logger.error('Ошибка генерации QR-кода', error=e)

            # Создаем клавиатуру с кнопками для оплаты по ссылке и проверки статуса
            keyboard_buttons = []

            # Добавляем кнопку оплаты, если доступна ссылка
            if confirmation_url:
                keyboard_buttons.append([types.InlineKeyboardButton(text='🔗 继续付款', url=confirmation_url)])
            else:
                # Если ссылка недоступна, предлагаем оплатить через ID платежа в приложении банка
                keyboard_buttons.append(
                    [types.InlineKeyboardButton(text='📱在银行应用程序中付款', callback_data='temp_disabled')]
                )

            # Добавляем общие кнопки
            keyboard_buttons.append(
                [
                    types.InlineKeyboardButton(
                        text='📊 检查状态', callback_data=f'check_yookassa_{payment_result["local_payment_id"]}'
                    )
                ]
            )
            keyboard_buttons.append(
                [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')]
            )

            keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

            # Подготавливаем текст сообщения
            show_devices = settings.is_devices_selection_enabled()

            message_lines = [
                '💳 <b>Оплата подписки через YooKassa</b>',
                '',
                f'📅 Период: {subscription_params["period_days"]} дней',
            ]

            if show_devices:
                message_lines.append(f'📱 Устройства: {subscription_params["device_limit"]}')

            yookassa_traffic_gb = subscription_params['traffic_limit_gb']
            yookassa_traffic_label = 'Безлимит' if yookassa_traffic_gb == 0 else f'{yookassa_traffic_gb} ГБ'

            message_lines.extend(
                [
                    f'📊 Трафик: {yookassa_traffic_label}',
                    f'💰 Сумма: {settings.format_price(price_kopeks)}',
                    f'🆔 ID платежа: {payment_result["yookassa_payment_id"][:8]}...',
                    '',
                ]
            )

            message_text = '\n'.join(message_lines)

            # Добавляем инструкции в зависимости от доступных способов оплаты
            if not confirmation_url:
                message_text += (
                    f"📱 <b>付款说明：</b>\n1. 打开银行应用程序\n2.通过详情查找支付功能或通过SBP转账\n3. 输入付款方式ID：<code>{payment_result['yookassa_payment_id']}</code>\n4. 在银行申请中确认付款\n5. 款项将自动存入您的余额"
                )

            message_text += (
                f'🔒 付款通过安全系统进行 YooKassa\n✅ 我们接受信用卡：Visa、MasterCard、MIR\n\n❓ 有任何问题请联系{settings.get_support_contact_display_html()}'
            )

            # Отправляем сообщение с инструкциями и клавиатурой
            # Если есть QR-код, отправляем его как медиа-сообщение
            if qr_photo:
                # Используем метод отправки фото с описанием
                await callback.message.edit_media(
                    media=types.InputMediaPhoto(media=qr_photo, caption=message_text, parse_mode='HTML'),
                    reply_markup=keyboard,
                )
            else:
                # Если QR-код недоступен, отправляем обычное текстовое сообщение
                await callback.message.edit_text(message_text, reply_markup=keyboard, parse_mode='HTML')

            await state.clear()
            await callback.answer()

        elif payment_method == 'cryptobot':
            # Оплата через CryptoBot
            if not settings.is_cryptobot_enabled():
                await callback.answer('❌暂时无法通过CryptoBot付款', show_alert=True)
                return

            amount_rubles = price_kopeks / 100
            if amount_rubles < 100 or amount_rubles > 100000:
                await callback.answer(
                    '❌ 金额必须在100到100,000₽之间通过CryptoBot支付',
                    show_alert=True,
                )
                return

            try:
                from app.utils.currency_converter import currency_converter

                usd_rate = await currency_converter.get_usd_to_rub_rate()
            except Exception as rate_error:
                logger.warning('Не удалось получить курс USD', rate_error=rate_error)
                usd_rate = 95.0

            amount_usd = round(amount_rubles / usd_rate, 2)
            if amount_usd < 1:
                await callback.answer(
                    '❌通过CryptoBot支付的最低金额约为1美元',
                    show_alert=True,
                )
                return
            if amount_usd > 1000:
                await callback.answer(
                    '❌ 通过CryptoBot支付的最高金额为1000美元',
                    show_alert=True,
                )
                return

            payment_service = PaymentService(callback.bot)
            crypto_result = await payment_service.create_cryptobot_payment(
                db=db,
                user_id=db_user.id,
                amount_usd=amount_usd,
                asset=settings.CRYPTOBOT_DEFAULT_ASSET,
                description=settings.get_subscription_payment_description(
                    subscription_params['period_days'],
                    price_kopeks,
                ),
                payload=f'simple_subscription_{db_user.id}_{price_kopeks}',
            )

            if not crypto_result:
                await callback.answer(
                    '❌ 通过 CryptoBot 创建付款时出错。请稍后重试或联系支持人员。',
                    show_alert=True,
                )
                return

            payment_url = (
                crypto_result.get('mini_app_invoice_url')
                or crypto_result.get('bot_invoice_url')
                or crypto_result.get('web_app_invoice_url')
            )

            if not payment_url:
                await callback.answer(
                    '❌ 无法接收付款链接。联系支持人员。',
                    show_alert=True,
                )
                return

            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text='🪙通过CryptoBot支付',
                            url=payment_url,
                        )
                    ],
                    [
                        types.InlineKeyboardButton(
                            text=texts.t('CHECK_STATUS_BUTTON', '📊检查状态'),
                            callback_data=f'check_simple_cryptobot_{crypto_result["local_payment_id"]}',
                        )
                    ],
                    [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')],
                ]
            )

            message_text = (
                f"🪙<b>通过CryptoBot</b>付款\n\n💰 支付金额：{amount_rubles:.0f} ₽\n💵 美元：{amount_usd:.2f} USD\n🪙资产：{crypto_result['asset']}\n💱 汇率：1 美元 ≈ {usd_rate:.2f} ₽\n🆔 ID 付款方式：{crypto_result['invoice_id'][:8]}...\n\n📱<b>使用说明：</b>\n1. 点击“通过CryptoBot支付”按钮\n2. 选择资产并按照提示操作\n3.确认转账\n4. 资金自动到账\n\n❓ 有任何问题请联系{settings.get_support_contact_display_html()}"
            )

            await callback.message.edit_text(
                message_text,
                reply_markup=keyboard,
                parse_mode='HTML',
            )

            await state.clear()
            await callback.answer()
            return

        elif payment_method == 'heleket':
            if not settings.is_heleket_enabled():
                await callback.answer('❌暂时无法使用Heleket付款', show_alert=True)
                return

            amount_rubles = price_kopeks / 100
            if amount_rubles < 100 or amount_rubles > 100000:
                await callback.answer(
                    '❌ 金额必须在100到100,000₽之间通过Heleket支付',
                    show_alert=True,
                )
                return

            heleket_result = await payment_service.create_heleket_payment(
                db=db,
                user_id=db_user.id,
                amount_kopeks=price_kopeks,
                description=settings.get_subscription_payment_description(
                    subscription_params['period_days'],
                    price_kopeks,
                ),
                language=db_user.language,
            )

            if not heleket_result:
                await callback.answer(
                    '❌ 创建付款时出错 Heleket。请稍后重试或联系支持人员。',
                    show_alert=True,
                )
                return

            payment_url = heleket_result.get('payment_url')
            if not payment_url:
                await callback.answer(
                    '❌ 接收付款链接Heleket失败。联系支持人员。',
                    show_alert=True,
                )
                return

            local_payment_id = heleket_result.get('local_payment_id')
            payer_amount = heleket_result.get('payer_amount')
            payer_currency = heleket_result.get('payer_currency')
            discount_percent = heleket_result.get('discount_percent')

            markup_percent = None
            if discount_percent is not None:
                try:
                    markup_percent = -int(discount_percent)
                except (TypeError, ValueError):
                    markup_percent = None

            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text='🪙通过Heleket支付',
                            url=payment_url,
                        )
                    ],
                    [
                        types.InlineKeyboardButton(
                            text=texts.t('CHECK_STATUS_BUTTON', '📊检查状态'),
                            callback_data=f'check_simple_heleket_{local_payment_id}',
                        )
                    ],
                    [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')],
                ]
            )

            message_lines = [
                '🪙 <b>Оплата через Heleket</b>',
                '',
                f'💰 Сумма: {settings.format_price(price_kopeks)}',
            ]

            if payer_amount and payer_currency:
                message_lines.append(f'🪙 К оплате: {payer_amount} {payer_currency}')
                try:
                    payer_amount_float = float(payer_amount)
                    if payer_amount_float > 0:
                        rub_per_currency = amount_rubles / payer_amount_float
                        message_lines.append(f'💱 Курс: 1 {payer_currency} ≈ {rub_per_currency:.2f} ₽')
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

            if markup_percent:
                sign = '+' if markup_percent > 0 else ''
                message_lines.append(f'📈 Наценка: {sign}{markup_percent}%')

            message_lines.extend(
                [
                    '',
                    '📱 <b>Инструкция:</b>',
                    "1. Нажмите кнопку 'Оплатить через Heleket'",
                    '2. Следуйте подсказкам на странице оплаты',
                    '3. Подтвердите перевод',
                    '4. Средства зачислятся автоматически',
                    '',
                    f'❓ Если возникнут проблемы, обратитесь в {settings.get_support_contact_display_html()}',
                ]
            )

            await callback.message.edit_text(
                '\n'.join(message_lines),
                reply_markup=keyboard,
                parse_mode='HTML',
            )

            await state.clear()
            await callback.answer()
            return

        elif payment_method == 'mulenpay':
            # Оплата через MulenPay
            mulenpay_name = settings.get_mulenpay_display_name()
            if not settings.is_mulenpay_enabled():
                await callback.answer(
                    f'❌暂时无法使用{mulenpay_name}付款',
                    show_alert=True,
                )
                return

            if price_kopeks < settings.MULENPAY_MIN_AMOUNT_KOPEKS or price_kopeks > settings.MULENPAY_MAX_AMOUNT_KOPEKS:
                await callback.answer(
                    f'❌ Mulen Pay 的金额必须在 {settings.format_price(settings.MULENPAY_MIN_AMOUNT_KOPEKS)} 和 {settings.format_price(settings.MULENPAY_MAX_AMOUNT_KOPEKS)} 之间',
                    show_alert=True,
                )
                return

            payment_service = PaymentService(callback.bot)
            mulen_result = await payment_service.create_mulenpay_payment(
                db=db,
                user_id=db_user.id,
                amount_kopeks=price_kopeks,
                description=settings.get_subscription_payment_description(
                    subscription_params['period_days'],
                    price_kopeks,
                ),
                language=db_user.language,
            )

            if not mulen_result or not mulen_result.get('payment_url'):
                await callback.answer(
                    texts.t(
                        'MULENPAY_PAYMENT_ERROR',
                        '❌创建{mulenpay_name}付款失败。请稍后再试或联系支持。',
                    ),
                    show_alert=True,
                )
                return

            payment_url = mulen_result['payment_url']
            local_payment_id = mulen_result.get('local_payment_id')
            payment_id_display = mulen_result.get('mulen_payment_id') or local_payment_id

            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text=texts.t('MULENPAY_PAY_BUTTON', '💳通过{mulenpay_name}付款'),
                            url=payment_url,
                        )
                    ],
                    [
                        types.InlineKeyboardButton(
                            text=texts.t('CHECK_STATUS_BUTTON', '📊检查状态'),
                            callback_data=f'check_simple_mulenpay_{local_payment_id}',
                        )
                    ],
                    [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')],
                ]
            )

            message_template = texts.t(
                'MULENPAY_PAYMENT_INSTRUCTIONS',
                (
                    '💳<b>通过{mulenpay_name_html}付款</b>\n\n💰金额：{amount}\n🆔付款ID：{payment_id}\n\n📱<b>说明：</b>\n1.点击“通过{mulenpay_name}付款”按钮\n2.按照支付系统提示操作\n3.确认转账\n4.资金将自动到账\n\n❓如果遇到问题，请联系{support}'
                ),
            )

            await callback.message.edit_text(
                message_template.format(
                    mulenpay_name=mulenpay_name,
                    mulenpay_name_html=settings.get_mulenpay_display_name_html(),
                    amount=settings.format_price(price_kopeks),
                    payment_id=payment_id_display,
                    support=settings.get_support_contact_display_html(),
                ),
                reply_markup=keyboard,
                parse_mode='HTML',
            )

            await state.clear()
            await callback.answer()
            return

        elif payment_method == 'pal24':
            # Оплата через PayPalych
            if not settings.is_pal24_enabled():
                await callback.answer('❌暂时无法通过PayPalych付款', show_alert=True)
                return

            payment_service = PaymentService(callback.bot)
            pal24_result = await payment_service.create_pal24_payment(
                db=db,
                user_id=db_user.id,
                amount_kopeks=price_kopeks,
                description=settings.get_subscription_payment_description(
                    subscription_params['period_days'],
                    price_kopeks,
                ),
                language=db_user.language,
            )

            if not pal24_result:
                await callback.answer(
                    texts.t(
                        'PAL24_PAYMENT_ERROR',
                        '❌创建PayPalych付款失败。请稍后再试或联系支持。',
                    ),
                    show_alert=True,
                )
                return

            sbp_url = pal24_result.get('sbp_url') or pal24_result.get('transfer_url')
            card_url = pal24_result.get('card_url')
            fallback_url = pal24_result.get('link_page_url') or pal24_result.get('link_url')

            if not (sbp_url or card_url or fallback_url):
                await callback.answer(
                    texts.t(
                        'PAL24_PAYMENT_ERROR',
                        '❌创建PayPalych付款失败。请稍后再试或联系支持。',
                    ),
                    show_alert=True,
                )
                return

            if not sbp_url:
                sbp_url = fallback_url

            bill_id = pal24_result.get('bill_id')
            local_payment_id = pal24_result.get('local_payment_id')

            pay_buttons: list[list[types.InlineKeyboardButton]] = []
            steps: list[str] = []
            step_counter = 1

            default_sbp_text = texts.t(
                'PAL24_SBP_PAY_BUTTON',
                '🏦通过PayPalych(快捷支付)付款',
            )
            sbp_button_text = settings.get_pal24_sbp_button_text(default_sbp_text)

            if sbp_url and settings.is_pal24_sbp_button_visible():
                pay_buttons.append(
                    [
                        types.InlineKeyboardButton(
                            text=sbp_button_text,
                            url=sbp_url,
                        )
                    ]
                )
                steps.append(
                    texts.t(
                        'PAL24_INSTRUCTION_BUTTON',
                        '{step}.点击“{button}”按钮',
                    ).format(step=step_counter, button=html.escape(sbp_button_text))
                )
                step_counter += 1

            default_card_text = texts.t(
                'PAL24_CARD_PAY_BUTTON',
                '💳使用银行卡支付(PayPalych)',
            )
            card_button_text = settings.get_pal24_card_button_text(default_card_text)

            if card_url and card_url != sbp_url and settings.is_pal24_card_button_visible():
                pay_buttons.append(
                    [
                        types.InlineKeyboardButton(
                            text=card_button_text,
                            url=card_url,
                        )
                    ]
                )
                steps.append(
                    texts.t(
                        'PAL24_INSTRUCTION_BUTTON',
                        '{step}.点击“{button}”按钮',
                    ).format(step=step_counter, button=html.escape(card_button_text))
                )
                step_counter += 1

            if not pay_buttons and fallback_url and settings.is_pal24_sbp_button_visible():
                pay_buttons.append(
                    [
                        types.InlineKeyboardButton(
                            text=sbp_button_text,
                            url=fallback_url,
                        )
                    ]
                )
                steps.append(
                    texts.t(
                        'PAL24_INSTRUCTION_BUTTON',
                        '{step}.点击“{button}”按钮',
                    ).format(step=step_counter, button=html.escape(sbp_button_text))
                )
                step_counter += 1

            follow_template = texts.t(
                'PAL24_INSTRUCTION_FOLLOW',
                '{step}.按照支付系统提示操作',
            )
            steps.append(follow_template.format(step=step_counter))
            step_counter += 1

            confirm_template = texts.t(
                'PAL24_INSTRUCTION_CONFIRM',
                '{step}.确认转账',
            )
            steps.append(confirm_template.format(step=step_counter))
            step_counter += 1

            success_template = texts.t(
                'PAL24_INSTRUCTION_COMPLETE',
                '{step}.资金将自动到账',
            )
            steps.append(success_template.format(step=step_counter))

            message_template = texts.t(
                'PAL24_PAYMENT_INSTRUCTIONS',
                (
                    '🏦<b>通过PayPalych(快捷支付)付款</b>\n\n💰金额：{amount}\n🆔账单ID：{bill_id}\n\n📱<b>说明：</b>\n1.点击“通过PayPalych(快捷支付)付款”按钮\n2.按照支付系统提示操作\n3.确认转账\n4.资金将自动到账\n\n❓如果遇到问题，请联系{support}'
                ),
            )

            keyboard_rows = pay_buttons + [
                [
                    types.InlineKeyboardButton(
                        text=texts.t('CHECK_STATUS_BUTTON', '📊检查状态'),
                        callback_data=f'check_simple_pal24_{local_payment_id}',
                    )
                ],
                [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')],
            ]

            keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

            message_text = message_template.format(
                amount=settings.format_price(price_kopeks),
                bill_id=bill_id,
                steps='\n'.join(steps),
                support=settings.get_support_contact_display_html(),
            )

            await callback.message.edit_text(
                message_text,
                reply_markup=keyboard,
                parse_mode='HTML',
            )

            await state.clear()
            await callback.answer()
            return

        elif payment_method == 'wata':
            # Оплата через WATA
            if not settings.is_wata_enabled():
                await callback.answer('❌暂时无法通过WATA付款', show_alert=True)
                return
            if price_kopeks < settings.WATA_MIN_AMOUNT_KOPEKS or price_kopeks > settings.WATA_MAX_AMOUNT_KOPEKS:
                await callback.answer(
                    f'❌ WATA 的金额必须在 {settings.format_price(settings.WATA_MIN_AMOUNT_KOPEKS)} 和 {settings.format_price(settings.WATA_MAX_AMOUNT_KOPEKS)} 之间。',
                    show_alert=True,
                )
                return

            payment_service = PaymentService(callback.bot)
            try:
                wata_result = await payment_service.create_wata_payment(
                    db=db,
                    user_id=db_user.id,
                    amount_kopeks=price_kopeks,
                    description=settings.get_subscription_payment_description(
                        subscription_params['period_days'],
                        price_kopeks,
                    ),
                    language=db_user.language,
                )
            except Exception as error:
                logger.error('Ошибка создания WATA платежа', error=error)
                wata_result = None

            if not wata_result or not wata_result.get('payment_url'):
                await callback.answer(
                    texts.t(
                        'WATA_PAYMENT_ERROR',
                        '❌创建WATA付款失败。请稍后再试或联系支持。',
                    ),
                    show_alert=True,
                )
                return

            payment_url = wata_result['payment_url']
            payment_link_id = wata_result.get('payment_link_id')
            local_payment_id = wata_result.get('local_payment_id')

            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text=texts.t('WATA_PAY_BUTTON', '💳通过WATA付款'),
                            url=payment_url,
                        )
                    ],
                    [
                        types.InlineKeyboardButton(
                            text=texts.t('CHECK_STATUS_BUTTON', '📊检查状态'),
                            callback_data=f'check_simple_wata_{local_payment_id}',
                        )
                    ],
                    [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')],
                ]
            )

            message_template = texts.t(
                'WATA_PAYMENT_INSTRUCTIONS',
                (
                    '💳<b>通过WATA付款</b>\n\n💰金额：{amount}\n🆔付款ID：{payment_id}\n\n📱<b>说明：</b>\n1.点击“通过WATA付款”按钮\n2.按照支付系统提示操作\n3.确认转账\n4.资金将自动到账\n\n❓如果遇到问题，请联系{support}'
                ),
            )

            await callback.message.edit_text(
                message_template.format(
                    amount=settings.format_price(price_kopeks),
                    payment_id=payment_link_id,
                    support=settings.get_support_contact_display_html(),
                ),
                reply_markup=keyboard,
                parse_mode='HTML',
            )

            await state.clear()
            await callback.answer()
            return

        else:
            await callback.answer('❌ 未知的付款方式', show_alert=True)

    except Exception as e:
        logger.error('Ошибка обработки метода оплаты простой подписки', error=e)
        await callback.answer(
            '❌ 处理请求时出错。请稍后重试或联系支持人员。', show_alert=True
        )
        await state.clear()


@error_handler
async def check_simple_pal24_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.rsplit('_', 1)[-1])
        payment_service = PaymentService(callback.bot)
        status_info = await payment_service.get_pal24_payment_status(db, local_payment_id)

        if not status_info:
            await callback.answer('❌ 未找到付款', show_alert=True)
            return

        payment = status_info['payment']

        status_labels = {
            'NEW': ('⏳', 'Ожидает оплаты'),
            'PROCESS': ('⌛', 'Обрабатывается'),
            'SUCCESS': ('✅', 'Оплачен'),
            'FAIL': ('❌', 'Отменен'),
            'UNDERPAID': ('⚠️', 'Недоплата'),
            'OVERPAID': ('⚠️', 'Переплата'),
        }

        emoji, status_text = status_labels.get(payment.status, ('❓', 'Неизвестно'))

        metadata = payment.metadata_json or {}
        links_meta = metadata.get('links') if isinstance(metadata, dict) else {}
        if not isinstance(links_meta, dict):
            links_meta = {}

        links_info = status_info.get('links') or {}
        sbp_link = links_info.get('sbp') or links_meta.get('sbp') or status_info.get('sbp_url') or payment.link_url
        card_link = links_info.get('card') or links_meta.get('card') or status_info.get('card_url')
        if not card_link and payment.link_page_url and payment.link_page_url != sbp_link:
            card_link = payment.link_page_url

        db_user = getattr(callback, 'db_user', None)
        texts = get_texts(db_user.language if db_user else settings.DEFAULT_LANGUAGE)

        message_lines = [
            '🏦 Статус платежа PayPalych:',
            '',
            f'🆔 ID счета: {payment.bill_id}',
            f'💰 Сумма: {settings.format_price(payment.amount_kopeks)}',
            f'📊 Статус: {emoji} {status_text}',
            f'📅 Создан: {payment.created_at.strftime("%d.%m.%Y %H:%M")}',
        ]

        if payment.is_paid:
            message_lines += ['', '✅ Платеж успешно завершен! Средства уже зачислены.']
        elif payment.status in {'NEW', 'PROCESS'}:
            message_lines += [
                '',
                '⏳ Платеж еще не завершен. Оплатите счет и проверьте статус позже.',
            ]
            if sbp_link:
                message_lines += ['', f'🏦 СБП: {sbp_link}']
            if card_link and card_link != sbp_link:
                message_lines.append(f'💳 Карта: {card_link}')
        elif payment.status in {'FAIL', 'UNDERPAID', 'OVERPAID'}:
            message_lines += [
                '',
                f'❌ Платеж не завершен корректно. Обратитесь в {settings.get_support_contact_display()}',
            ]

        pay_rows: list[list[types.InlineKeyboardButton]] = []

        if not payment.is_paid and payment.status in {'NEW', 'PROCESS'}:
            default_sbp_text = texts.t(
                'PAL24_SBP_PAY_BUTTON',
                '🏦通过PayPalych(快捷支付)付款',
            )
            sbp_button_text = settings.get_pal24_sbp_button_text(default_sbp_text)

            if sbp_link and settings.is_pal24_sbp_button_visible():
                pay_rows.append(
                    [
                        types.InlineKeyboardButton(
                            text=sbp_button_text,
                            url=sbp_link,
                        )
                    ]
                )

            default_card_text = texts.t(
                'PAL24_CARD_PAY_BUTTON',
                '💳使用银行卡支付(PayPalych)',
            )
            card_button_text = settings.get_pal24_card_button_text(default_card_text)

            if card_link and settings.is_pal24_card_button_visible():
                if not pay_rows or pay_rows[-1][0].url != card_link:
                    pay_rows.append(
                        [
                            types.InlineKeyboardButton(
                                text=card_button_text,
                                url=card_link,
                            )
                        ]
                    )

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=pay_rows
            + [
                [
                    types.InlineKeyboardButton(
                        text=texts.t('CHECK_STATUS_BUTTON', '📊检查状态'),
                        callback_data=f'check_simple_pal24_{local_payment_id}',
                    )
                ],
                [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')],
            ]
        )

        await callback.answer()
        try:
            await callback.message.edit_text(
                '\n'.join(message_lines),
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except TelegramBadRequest as error:
            if 'message is not modified' in str(error).lower():
                await callback.answer(texts.t('CHECK_STATUS_NO_CHANGES', '状态未更改'))
            else:
                raise

    except Exception as error:
        logger.error('Ошибка проверки статуса PayPalych для простой подписки', error=error)
        await callback.answer('❌ 状态检查错误', show_alert=True)


@error_handler
async def check_simple_mulenpay_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.rsplit('_', 1)[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 付款 ID 错误', show_alert=True)
        return

    payment_service = PaymentService(callback.bot)
    status_info = await payment_service.get_mulenpay_payment_status(db, local_payment_id)

    if not status_info:
        await callback.answer('❌ 未找到付款', show_alert=True)
        return

    payment = status_info['payment']

    user_language = settings.DEFAULT_LANGUAGE
    try:
        from app.services.payment_service import get_user_by_id as fetch_user_by_id

        user = await fetch_user_by_id(db, payment.user_id)
        if user and getattr(user, 'language', None):
            user_language = user.language
    except Exception as error:
        logger.debug('Не удалось получить пользователя для MulenPay статуса', error=error)

    texts = get_texts(user_language)
    status_labels = {
        'created': ('⏳', 'Ожидает оплаты'),
        'processing': ('⌛', 'Обрабатывается'),
        'success': ('✅', 'Оплачен'),
        'canceled': ('❌', 'Отменен'),
        'error': ('⚠️', 'Ошибка'),
        'hold': ('🔒', 'Холд'),
        'unknown': ('❓', 'Неизвестно'),
    }

    emoji, status_text = status_labels.get(payment.status, ('❓', 'Неизвестно'))

    message_lines = [
        '💳 Статус платежа Mulen Pay:',
        '',
        f'🆔 ID: {payment.mulen_payment_id or payment.id}',
        f'💰 Сумма: {settings.format_price(payment.amount_kopeks)}',
        f'📊 Статус: {emoji} {status_text}',
        f'📅 Создан: {payment.created_at.strftime("%d.%m.%Y %H:%M") if payment.created_at else "—"}',
    ]

    if payment.is_paid:
        message_lines.append('\n✅ Платеж успешно завершен! Средства уже зачислены.')
    elif payment.status in {'created', 'processing'}:
        message_lines.append('\n⏳ Платеж еще не завершен. Завершите оплату и проверьте статус позже.')

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t('CHECK_STATUS_BUTTON', '📊检查状态'),
                    callback_data=f'check_simple_mulenpay_{local_payment_id}',
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')],
        ]
    )

    await callback.answer()
    await callback.message.edit_text(
        '\n'.join(message_lines),
        reply_markup=keyboard,
        parse_mode='HTML',
    )


@error_handler
async def check_simple_cryptobot_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.rsplit('_', 1)[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 付款 ID 错误', show_alert=True)
        return

    from app.database.crud.cryptobot import get_cryptobot_payment_by_id

    payment = await get_cryptobot_payment_by_id(db, local_payment_id)
    if not payment:
        await callback.answer('❌ 未找到付款', show_alert=True)
        return

    status_labels = {
        'active': ('⏳', 'Ожидает оплаты'),
        'paid': ('✅', 'Оплачен'),
        'expired': ('❌', 'Истек'),
    }
    emoji, status_text = status_labels.get(payment.status, ('❓', 'Неизвестно'))

    language = settings.DEFAULT_LANGUAGE
    try:
        from app.services.payment_service import get_user_by_id as fetch_user_by_id

        user = await fetch_user_by_id(db, payment.user_id)
        if user and getattr(user, 'language', None):
            language = user.language
    except Exception as error:
        logger.debug('Не удалось получить пользователя для CryptoBot статуса', error=error)

    texts = get_texts(language)
    message_lines = [
        '🪙 <b>Статус платежа CryptoBot</b>',
        '',
        f'🆔 ID: {payment.invoice_id}',
        f'💰 Сумма: {payment.amount} {payment.asset}',
        f'📊 Статус: {emoji} {status_text}',
        f'📅 Создан: {payment.created_at.strftime("%d.%m.%Y %H:%M") if payment.created_at else "—"}',
    ]

    if payment.status == 'paid':
        message_lines.append('\n✅ Платеж подтвержден. Средства уже зачислены.')
    elif payment.status == 'active':
        message_lines.append('\n⏳ Платеж еще ожидает подтверждения. Оплатите счет и проверьте статус позже.')

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t('CHECK_STATUS_BUTTON', '📊检查状态'),
                    callback_data=f'check_simple_cryptobot_{local_payment_id}',
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')],
        ]
    )

    await callback.answer()
    await callback.message.edit_text(
        '\n'.join(message_lines),
        reply_markup=keyboard,
        parse_mode='HTML',
    )


@error_handler
async def check_simple_heleket_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.rsplit('_', 1)[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 付款 ID 错误', show_alert=True)
        return

    from app.database.crud.heleket import get_heleket_payment_by_id

    payment = await get_heleket_payment_by_id(db, local_payment_id)
    if not payment:
        await callback.answer('❌ 未找到付款', show_alert=True)
        return

    status_labels = {
        'check': ('⏳', 'Ожидает оплаты'),
        'paid': ('✅', 'Оплачен'),
        'paid_over': ('✅', 'Оплачен (переплата)'),
        'wrong_amount': ('⚠️', 'Неверная сумма'),
        'cancel': ('❌', 'Отменен'),
        'fail': ('❌', 'Ошибка'),
        'process': ('⌛', 'Обрабатывается'),
        'confirm_check': ('⌛', 'Ожидает подтверждения'),
    }

    emoji, status_text = status_labels.get(payment.status, ('❓', 'Неизвестно'))

    language = settings.DEFAULT_LANGUAGE
    try:
        from app.services.payment_service import get_user_by_id as fetch_user_by_id

        user = await fetch_user_by_id(db, payment.user_id)
        if user and getattr(user, 'language', None):
            language = user.language
    except Exception as error:
        logger.debug('Не удалось получить пользователя для Heleket статуса', error=error)

    texts = get_texts(language)

    message_lines = [
        '🪙 Статус платежа Heleket:',
        '',
        f'🆔 UUID: {payment.uuid[:8]}...',
        f'💰 Сумма: {settings.format_price(payment.amount_kopeks)}',
        f'📊 Статус: {emoji} {status_text}',
        f'📅 Создан: {payment.created_at.strftime("%d.%m.%Y %H:%M") if payment.created_at else "—"}',
    ]

    if payment.payer_amount and payment.payer_currency:
        message_lines.append(f'🪙 Оплата: {payment.payer_amount} {payment.payer_currency}')

    if payment.is_paid:
        message_lines.append('\n✅ Платеж успешно завершен! Средства уже зачислены.')
    elif payment.status in {'check', 'process', 'confirm_check'}:
        message_lines.append('\n⏳ Платеж еще обрабатывается. Завершите оплату и проверьте статус позже.')
        if payment.payment_url:
            message_lines.append(f'\n🔗 Ссылка на оплату: {payment.payment_url}')
    elif payment.status in {'fail', 'cancel', 'wrong_amount'}:
        message_lines.append(
            f'\n❌ Платеж не завершен корректно. Обратитесь в {settings.get_support_contact_display()}'
        )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t('CHECK_STATUS_BUTTON', '📊检查状态'),
                    callback_data=f'check_simple_heleket_{local_payment_id}',
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')],
        ]
    )

    await callback.answer()
    await callback.message.edit_text(
        '\n'.join(message_lines),
        reply_markup=keyboard,
        parse_mode='HTML',
    )


@error_handler
async def check_simple_wata_payment_status(
    callback: types.CallbackQuery,
    db: AsyncSession,
):
    try:
        local_payment_id = int(callback.data.rsplit('_', 1)[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 付款 ID 错误', show_alert=True)
        return

    payment_service = PaymentService(callback.bot)
    status_info = await payment_service.get_wata_payment_status(db, local_payment_id)

    if not status_info:
        await callback.answer('❌ 未找到付款', show_alert=True)
        return

    payment = status_info['payment']
    texts = get_texts(settings.DEFAULT_LANGUAGE)

    status_labels = {
        'Opened': ('⏳', texts.t('WATA_STATUS_OPENED', '等待付款')),
        'Closed': ('⌛', texts.t('WATA_STATUS_CLOSED', '处理中')),
        'Paid': ('✅', texts.t('WATA_STATUS_PAID', '已支付')),
        'Declined': ('❌', texts.t('WATA_STATUS_DECLINED', '已拒绝')),
    }
    emoji, status_text = status_labels.get(payment.status, ('❓', texts.t('WATA_STATUS_UNKNOWN', '未知')))

    message_lines = [
        texts.t('WATA_STATUS_TITLE', '💳<b>WATA付款状态</b>'),
        '',
        f'🆔 ID: {payment.payment_link_id}',
        f'💰 Сумма: {settings.format_price(payment.amount_kopeks)}',
        f'📊 Статус: {emoji} {status_text}',
        f'📅 Создан: {payment.created_at.strftime("%d.%m.%Y %H:%M") if payment.created_at else "—"}',
    ]

    if payment.is_paid:
        message_lines.append('\n✅ Платеж успешно завершен! Средства уже зачислены.')
    elif payment.status in {'Opened', 'Closed'}:
        message_lines.append('\n⏳ Платеж еще не завершен. Завершите оплату и проверьте статус позже.')

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t('CHECK_STATUS_BUTTON', '📊检查状态'),
                    callback_data=f'check_simple_wata_{local_payment_id}',
                )
            ],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data='subscription_purchase')],
        ]
    )

    await callback.answer()
    await callback.message.edit_text(
        '\n'.join(message_lines),
        reply_markup=keyboard,
        parse_mode='HTML',
    )


@error_handler
async def confirm_simple_subscription_purchase(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Обрабатывает подтверждение простой покупки подписки при наличии активной платной подписки."""
    texts = get_texts(db_user.language)

    data = await state.get_data()
    subscription_params = data.get('subscription_params', {})

    if not subscription_params:
        await callback.answer('❌ 订阅数据已过时。请重新开始。', show_alert=True)
        return

    resolved_squad_uuid = await _ensure_simple_subscription_squad_uuid(
        db,
        state,
        subscription_params,
        user_id=db_user.id,
        state_data=data,
    )

    # Lock user BEFORE pricing to prevent TOCTOU
    from app.database.crud.user import lock_user_for_pricing, subtract_user_balance

    db_user = await lock_user_for_pricing(db, db_user.id)

    # Рассчитываем цену подписки (group discounts per-category)
    price_kopeks, price_breakdown = await _calculate_simple_subscription_price(
        db,
        subscription_params,
        user=db_user,
        resolved_squad_uuid=resolved_squad_uuid,
    )

    # PricingEngine already applies promo-offer discount inside calculate_classic_new_subscription_price.
    # Only determine whether to consume the offer (zero it out after use).
    from app.utils.promo_offer import get_user_active_promo_discount_percent

    consume_promo = get_user_active_promo_discount_percent(db_user) > 0

    total_required = price_kopeks
    logger.warning(
        'SIMPLE_SUBSCRIPTION_DEBUG_CONFIRM | user= | period= | base= | traffic= | devices= | servers= | discount= | total_required= | balance',
        db_user_id=db_user.id,
        subscription_params=subscription_params['period_days'],
        price_breakdown=price_breakdown.get('base_price', 0),
        price_breakdown_2=price_breakdown.get('traffic_price', 0),
        price_breakdown_3=price_breakdown.get('devices_price', 0),
        price_breakdown_4=price_breakdown.get('servers_price', 0),
        price_breakdown_5=price_breakdown.get('total_discount', 0),
        total_required=total_required,
        getattr=getattr(db_user, 'balance_kopeks', 0),
    )

    # Проверяем баланс пользователя
    user_balance_kopeks = getattr(db_user, 'balance_kopeks', 0)

    if total_required > 0 and user_balance_kopeks < total_required:
        await callback.answer('❌ 您的余额中没有足够的资金来支付订阅费用', show_alert=True)
        return

    try:
        # Списываем средства с баланса пользователя
        purchase_description = f'Оплата подписки на {subscription_params["period_days"]} дней'
        success = await subtract_user_balance(
            db,
            db_user,
            price_kopeks,
            purchase_description,
            consume_promo_offer=consume_promo,
            mark_as_paid_subscription=True,
        )

        if not success:
            await callback.answer('❌ 从余额中扣除资金时出错', show_alert=True)
            return

        # Создаём транзакцию для учёта списания
        from app.database.crud.transaction import create_transaction
        from app.database.models import PaymentMethod, TransactionType

        transaction = await create_transaction(
            db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=price_kopeks,
            description=purchase_description,
            payment_method=PaymentMethod.BALANCE,
        )

        # Проверяем, есть ли у пользователя уже подписка
        from app.database.crud.subscription import extend_subscription, get_subscription_by_user_id

        existing_subscription = await get_subscription_by_user_id(db, db_user.id)

        if existing_subscription:
            # Если подписка уже существует, продлеваем её
            # Сохраняем информацию о текущей подписке, особенно является ли она пробной
            was_trial = getattr(existing_subscription, 'is_trial', False)

            subscription = await extend_subscription(
                db=db,
                subscription=existing_subscription,
                days=subscription_params['period_days'],
                traffic_limit_gb=subscription_params['traffic_limit_gb'],
                device_limit=subscription_params['device_limit'],
                connected_squads=[resolved_squad_uuid] if resolved_squad_uuid else None,
            )

            # Если текущая подписка была пробной, и мы обновляем её
            # нужно изменить статус подписки
            if was_trial:
                from app.database.models import SubscriptionStatus

                # Переводим подписку из пробной в активную платную
                subscription.status = SubscriptionStatus.ACTIVE.value
                subscription.is_trial = False

            await db.commit()
            await db.refresh(subscription)
        else:
            # Если подписки нет, создаём новую
            from app.database.crud.subscription import create_paid_subscription

            subscription = await create_paid_subscription(
                db=db,
                user_id=db_user.id,
                duration_days=subscription_params['period_days'],
                traffic_limit_gb=subscription_params['traffic_limit_gb'],
                device_limit=subscription_params['device_limit'],
                connected_squads=[resolved_squad_uuid] if resolved_squad_uuid else [],
                update_server_counters=True,
            )

        if not subscription:
            # Возвращаем средства на баланс в случае ошибки
            from app.services.payment_service import add_user_balance

            await add_user_balance(
                db,
                db_user.id,
                price_kopeks,
                f'Возврат средств за неудавшуюся подписку на {subscription_params["period_days"]} дней',
            )
            await callback.answer('❌ 创建订阅时出错。资金已退回余额。', show_alert=True)
            return

        # Обновляем баланс пользователя
        await db.refresh(db_user)

        # Обновляем или создаём ссылку подписки в RemnaWave
        try:
            from app.services.subscription_service import SubscriptionService

            subscription_service = SubscriptionService()
            remnawave_user = await subscription_service.create_remnawave_user(db, subscription)
            if remnawave_user:
                await db.refresh(subscription)
        except Exception as sync_error:
            logger.error(
                'Ошибка синхронизации подписки с RemnaWave для пользователя',
                db_user_id=db_user.id,
                sync_error=sync_error,
                exc_info=True,
            )

        # Отправляем уведомление об успешной покупке
        server_label = _get_simple_subscription_server_label(
            texts,
            subscription_params,
            resolved_squad_uuid,
        )
        show_devices = settings.is_devices_selection_enabled()

        success_lines = [
            '✅ <b>Подписка успешно активирована!</b>',
            '',
            f'📅 Период: {subscription_params["period_days"]} дней',
        ]

        if show_devices:
            success_lines.append(f'📱 Устройства: {subscription_params["device_limit"]}')

        success_traffic_gb = subscription_params['traffic_limit_gb']
        success_traffic_label = 'Безлимит' if success_traffic_gb == 0 else f'{success_traffic_gb} ГБ'

        success_lines.extend(
            [
                f'📊 Трафик: {success_traffic_label}',
                f'🌍 Сервер: {server_label}',
                '',
                f'💰 Списано с баланса: {settings.format_price(price_kopeks)}',
                f'💳 Ваш баланс: {settings.format_price(db_user.balance_kopeks)}',
                '',
                "🔗 Для подключения перейдите в раздел 'Подключиться'",
            ]
        )

        success_message = '\n'.join(success_lines)

        connect_mode = settings.CONNECT_BUTTON_MODE
        subscription_link = get_display_subscription_link(subscription)
        connect_button_text = texts.t('CONNECT_BUTTON', '🔗连接')

        def _fallback_connect_button() -> types.InlineKeyboardButton:
            return types.InlineKeyboardButton(
                text=connect_button_text,
                callback_data='subscription_connect',
            )

        if connect_mode == 'miniapp_subscription':
            if subscription_link:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        web_app=types.WebAppInfo(url=subscription_link),
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        elif connect_mode == 'miniapp_custom':
            custom_url = settings.MINIAPP_CUSTOM_URL
            if custom_url:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        web_app=types.WebAppInfo(url=custom_url),
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        elif connect_mode == 'link':
            if subscription_link:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        url=subscription_link,
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        elif connect_mode == 'happ_cryptolink':
            if subscription_link:
                connect_row = [
                    types.InlineKeyboardButton(
                        text=connect_button_text,
                        callback_data='open_subscription_link',
                    )
                ]
            else:
                connect_row = [_fallback_connect_button()]
        else:
            connect_row = [_fallback_connect_button()]

        keyboard_rows = [connect_row]

        happ_row = get_happ_download_button_row(texts)
        if happ_row:
            keyboard_rows.append(happ_row)

        keyboard_rows.append([types.InlineKeyboardButton(text='🏠 主页', callback_data='back_to_menu')])

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        await callback.message.edit_text(success_message, reply_markup=keyboard, parse_mode='HTML')

        # Отправляем уведомление админам
        try:
            from app.services.admin_notification_service import AdminNotificationService

            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_purchase_notification(
                db,
                db_user,
                subscription,
                transaction,
                subscription_params['period_days'],
                False,  # was_trial_conversion
                amount_kopeks=price_kopeks,
                purchase_type='renewal' if existing_subscription else 'first_purchase',
            )
        except Exception as e:
            logger.error('Ошибка отправки уведомления админам о покупке', error=e)

        await state.clear()
        await callback.answer()

        logger.info(
            'Пользователь успешно купил подписку с баланса на ₽',
            telegram_id=db_user.telegram_id,
            price_kopeks=price_kopeks / 100,
        )

    except Exception as error:
        logger.error(
            'Ошибка подтверждения простой подписки с баланса для пользователя',
            db_user_id=db_user.id,
            error=error,
            exc_info=True,
        )
        await callback.answer(
            '❌ 订阅付款错误。请稍后重试或联系支持人员。',
            show_alert=True,
        )
        await state.clear()


def register_simple_subscription_handlers(dp):
    """Регистрирует обработчики простой покупки подписки."""

    dp.callback_query.register(start_simple_subscription_purchase, F.data == 'simple_subscription_purchase')

    dp.callback_query.register(confirm_simple_subscription_purchase, F.data == 'simple_subscription_confirm_purchase')

    dp.callback_query.register(
        handle_simple_subscription_pay_with_balance, F.data == 'simple_subscription_pay_with_balance'
    )

    dp.callback_query.register(
        handle_simple_subscription_pay_with_balance_disabled, F.data == 'simple_subscription_pay_with_balance_disabled'
    )

    dp.callback_query.register(
        handle_simple_subscription_other_payment_methods, F.data == 'simple_subscription_other_payment_methods'
    )

    dp.callback_query.register(handle_simple_subscription_payment_method, F.data.startswith('simple_subscription_'))

    dp.callback_query.register(check_simple_pal24_payment_status, F.data.startswith('check_simple_pal24_'))

    dp.callback_query.register(check_simple_mulenpay_payment_status, F.data.startswith('check_simple_mulenpay_'))

    dp.callback_query.register(check_simple_cryptobot_payment_status, F.data.startswith('check_simple_cryptobot_'))

    dp.callback_query.register(check_simple_heleket_payment_status, F.data.startswith('check_simple_heleket_'))

    dp.callback_query.register(check_simple_wata_payment_status, F.data.startswith('check_simple_wata_'))

    dp.callback_query.register(check_simple_pal24_payment_status, F.data.startswith('check_simple_pal24_'))
