import html as html_mod
from datetime import UTC, datetime

from aiogram import types
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.transaction import create_transaction
from app.database.crud.user import lock_user_for_pricing, subtract_user_balance
from app.database.models import Subscription, TransactionType, User
from app.keyboards.inline import (
    get_app_selection_keyboard,
    get_back_keyboard,
    get_change_devices_keyboard,
    get_confirm_change_devices_keyboard,
    get_connection_guide_keyboard,
    get_device_management_help_keyboard,
    get_devices_management_keyboard,
    get_insufficient_balance_keyboard,
    get_specific_app_keyboard,
)
from app.localization.texts import get_texts
from app.services.pricing_engine import PricingEngine
from app.services.remnawave_service import RemnaWaveService
from app.services.subscription_service import SubscriptionService
from app.services.user_cart_service import user_cart_service
from app.utils.pagination import paginate_list
from app.utils.pricing_utils import (
    apply_percentage_discount,
)
from app.utils.subscription_utils import (
    get_display_subscription_link,
)

from .common import (
    _get_period_hint_from_subscription,
    get_apps_for_platform_async,
    get_device_name,
    logger,
    render_guide_blocks,
)
from .countries import _get_available_countries


async def _resolve_subscription(callback, db_user, db, state=None):
    """Resolve subscription — delegates to shared resolve_subscription_from_context."""
    from .common import resolve_subscription_from_context

    return await resolve_subscription_from_context(callback, db_user, db, state)


def _get_remnawave_uuid(subscription, db_user):
    """Get remnawave_uuid from subscription (multi-tariff) or user (legacy)."""
    return getattr(subscription, 'remnawave_uuid', None) or db_user.remnawave_uuid


async def get_current_devices_detailed(db_user: User, subscription=None) -> dict:
    try:
        uuid = _get_remnawave_uuid(subscription, db_user) if subscription else db_user.remnawave_uuid
        if not uuid:
            return {'count': 0, 'devices': []}

        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{uuid}')

            if response and 'response' in response:
                devices_info = response['response']
                total_devices = devices_info.get('total', 0)
                devices_list = devices_info.get('devices', [])

                return {'count': total_devices, 'devices': devices_list[:5]}
            return {'count': 0, 'devices': []}

    except Exception as e:
        logger.error('Ошибка получения детальной информации об устройствах', error=e)
        return {'count': 0, 'devices': []}


async def get_servers_display_names(squad_uuids: list[str]) -> str:
    if not squad_uuids:
        return '没有服务器'

    try:
        from app.database.crud.server_squad import get_server_squad_by_uuid
        from app.database.database import AsyncSessionLocal

        server_names = []

        async with AsyncSessionLocal() as db:
            for uuid in squad_uuids:
                server = await get_server_squad_by_uuid(db, uuid)
                if server:
                    server_names.append(html_mod.escape(server.display_name))
                    logger.debug('Найден сервер в БД', uuid=uuid, display_name=server.display_name)
                else:
                    logger.warning('Сервер с UUID не найден в БД', uuid=uuid)

        if not server_names:
            countries = await _get_available_countries()
            for uuid in squad_uuids:
                for country in countries:
                    if country['uuid'] == uuid:
                        server_names.append(html_mod.escape(country['name']))
                        logger.debug('Найден сервер в кэше', uuid=uuid, country=country['name'])
                        break

        if not server_names:
            if len(squad_uuids) == 1:
                return '🎯 测试服务器'
            return f'{len(squad_uuids)} 国家'

        if len(server_names) > 6:
            displayed = ', '.join(server_names[:6])
            remaining = len(server_names) - 6
            return f'{displayed} 以及 {remaining}'
        return ', '.join(server_names)

    except Exception as e:
        logger.error('Ошибка получения названий серверов', error=e)
        if len(squad_uuids) == 1:
            return '🎯 测试服务器'
        return f'{len(squad_uuids)} 国家'


async def get_current_devices_count(db_user: User, subscription=None) -> str:
    try:
        uuid = _get_remnawave_uuid(subscription, db_user) if subscription else db_user.remnawave_uuid
        if not uuid:
            return '—'

        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{uuid}')

            if response and 'response' in response:
                total_devices = response['response'].get('total', 0)
                return str(total_devices)
            return '—'

    except Exception as e:
        logger.error('Ошибка получения количества устройств', error=e)
        return '—'


async def handle_change_devices(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t('PAID_FEATURE_ONLY', '⚠此功能仅适用于付费订阅'),
            show_alert=True,
        )
        return

    # Проверяем тариф подписки
    tariff = None
    if subscription.tariff_id:
        from app.database.crud.tariff import get_tariff_by_id

        tariff = await get_tariff_by_id(db, subscription.tariff_id)

    # Для тарифов - проверяем разрешено ли изменение устройств
    tariff_device_price = getattr(tariff, 'device_price_kopeks', None) if tariff else None
    if tariff:
        if tariff_device_price is None or tariff_device_price <= 0:
            await callback.answer(
                texts.t('TARIFF_DEVICES_DISABLED', '⚠️ 您的套餐不支持更换设备'),
                show_alert=True,
            )
            return
    # Для обычных подписок проверяем глобальную настройку
    elif not settings.is_devices_selection_enabled():
        await callback.answer(
            texts.t('DEVICES_SELECTION_DISABLED', '⚠️设备数量选择不可用'),
            show_alert=True,
        )
        return

    current_devices = subscription.device_limit

    period_hint_days = _get_period_hint_from_subscription(subscription)
    devices_discount_percent = PricingEngine.get_addon_discount_percent(
        db_user,
        'devices',
        period_hint_days,
    )

    # Для тарифов показываем цену из тарифа
    if tariff:
        price_per_device = tariff_device_price
        price_text = texts.format_price(price_per_device)
        prompt_text = texts.t(
            'CHANGE_DEVICES_PROMPT_TARIFF',
            (
                '📱<b>更改设备数量</b>\n\n当前限制：{current_devices}台设备\n每台额外设备价格：{price}/月\n请选择新的设备数量：\n\n💡<b>重要提示：</b>\n•增加-按剩余时间比例补差价\n•减少-不退款'
            ),
        ).format(current_devices=current_devices, price=price_text)
    else:
        prompt_text = texts.t(
            'CHANGE_DEVICES_PROMPT',
            (
                '📱<b>更改设备数量</b>\n\n当前限制：{current_devices}台设备\n请选择新的设备数量：\n\n💡<b>重要提示：</b>\n•增加-按剩余时间比例补差价\n•减少-不退款'
            ),
        ).format(current_devices=current_devices)

    # В мульти-тарифе кнопка "назад" ведёт к детальному виду подписки
    back_cb = f'sm:{sub_id}' if settings.is_multi_tariff_enabled() and sub_id else 'subscription_settings'

    await callback.message.edit_text(
        prompt_text,
        reply_markup=get_change_devices_keyboard(
            current_devices,
            db_user.language,
            subscription.end_date,
            devices_discount_percent,
            tariff=tariff,
            back_callback=back_cb,
        ),
    )

    await callback.answer()


async def confirm_change_devices(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    texts = get_texts(db_user.language)
    try:
        new_devices_count = int(callback.data.split('_')[2])
    except (ValueError, IndexError):
        await callback.answer(texts.t('INVALID_REQUEST', 'Invalid request'), show_alert=True)
        return
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return

    # Проверяем тариф подписки
    tariff = None
    if subscription.tariff_id:
        from app.database.crud.tariff import get_tariff_by_id

        tariff = await get_tariff_by_id(db, subscription.tariff_id)

    # Для тарифов - проверяем разрешено ли изменение устройств
    tariff_device_price = getattr(tariff, 'device_price_kopeks', None) if tariff else None
    if tariff:
        if tariff_device_price is None or tariff_device_price <= 0:
            await callback.answer(
                texts.t('TARIFF_DEVICES_DISABLED', '⚠️ 您的套餐不支持更换设备'),
                show_alert=True,
            )
            return
        price_per_device = tariff_device_price
    else:
        if not settings.is_devices_selection_enabled():
            await callback.answer(
                texts.t('DEVICES_SELECTION_DISABLED', '⚠️设备数量选择不可用'),
                show_alert=True,
            )
            return
        price_per_device = settings.PRICE_PER_DEVICE

    current_devices = subscription.device_limit

    if new_devices_count == current_devices:
        await callback.answer(
            texts.t('DEVICES_NO_CHANGE', 'ℹ️设备数量未更改'),
            show_alert=True,
        )
        return

    # Используем max_device_limit из тарифа если есть, иначе глобальную настройку
    tariff_max_devices = getattr(tariff, 'max_device_limit', None) if tariff else None
    effective_max = (tariff_max_devices if tariff_max_devices is not None and tariff_max_devices > 0 else None) or (
        settings.MAX_DEVICES_LIMIT if settings.MAX_DEVICES_LIMIT > 0 else None
    )
    if effective_max and new_devices_count > effective_max:
        await callback.answer(
            texts.t(
                'DEVICES_LIMIT_EXCEEDED',
                '⚠️超过最大设备限制({limit})',
            ).format(limit=effective_max),
            show_alert=True,
        )
        return

    # Минимум при уменьшении всегда 1 (device_limit тарифа — это "включено при покупке", а не нижняя граница)
    if new_devices_count < 1:
        await callback.answer(
            texts.t(
                'DEVICES_MIN_LIMIT_REACHED',
                '⚠️ 最小设备数量：{limit}',
            ).format(limit=1),
            show_alert=True,
        )
        return

    devices_difference = new_devices_count - current_devices

    if devices_difference > 0:
        additional_devices = devices_difference

        # Для тарифов - все устройства платные (нет бесплатного лимита)
        if tariff:
            chargeable_devices = additional_devices
        elif current_devices < settings.DEFAULT_DEVICE_LIMIT:
            free_devices = settings.DEFAULT_DEVICE_LIMIT - current_devices
            chargeable_devices = max(0, additional_devices - free_devices)
        else:
            chargeable_devices = additional_devices

        devices_price_per_month = chargeable_devices * price_per_device

        # Считаем стоимость по оставшимся дням подписки
        now = datetime.now(UTC)
        days_left = max(1, (subscription.end_date - now).days)
        period_hint_days = days_left

        devices_discount_percent = PricingEngine.get_addon_discount_percent(
            db_user,
            'devices',
            period_hint_days,
        )
        discounted_per_month, discount_per_month = apply_percentage_discount(
            devices_price_per_month,
            devices_discount_percent,
        )
        # Цена = месячная_цена * days_left / 30
        price = int(discounted_per_month * days_left / 30)
        price = max(100, price)  # Минимум 1 рубль
        total_discount = int(discount_per_month * days_left / 30)
        period_label = f'{days_left} дн.' if days_left > 1 else '1 день'

        if price > 0 and db_user.balance_kopeks < price:
            missing_kopeks = price - db_user.balance_kopeks
            required_text = f'{texts.format_price(price)} (за {period_label})'
            message_text = texts.t(
                'ADDON_INSUFFICIENT_FUNDS_MESSAGE',
                (
                    '⚠️<b>资金不足</b>\n\n服务费用：{required}\n当前余额：{balance}\n缺少：{missing}\n\n请选择充值方式。金额将自动填入。'
                ),
            ).format(
                required=required_text,
                balance=texts.format_price(db_user.balance_kopeks),
                missing=texts.format_price(missing_kopeks),
            )

            # Сохраняем корзину для автопокупки после пополнения баланса
            await user_cart_service.save_user_cart(
                user_id=db_user.id,
                cart_data={
                    'cart_mode': 'add_devices',
                    'devices_to_add': devices_difference,
                    'price_kopeks': price,
                },
            )
            logger.info(
                'Сохранена корзина add_devices для пользователя : + устройств, цена коп.',
                telegram_id=db_user.telegram_id,
                devices_difference=devices_difference,
                price=price,
            )

            await callback.message.answer(
                message_text,
                reply_markup=get_insufficient_balance_keyboard(
                    db_user.language,
                    amount_kopeks=missing_kopeks,
                    has_saved_cart=True,
                ),
                parse_mode='HTML',
            )
            await callback.answer()
            return

        action_text = texts.t(
            'DEVICE_CHANGE_ACTION_INCREASE',
            '增加到{count}',
        ).format(count=new_devices_count)
        if price > 0:
            cost_text = texts.t(
                'DEVICE_CHANGE_EXTRA_COST',
                '补差价：{amount}({period})',
            ).format(
                amount=texts.format_price(price),
                period=period_label,
                months=period_label,
            )
            if total_discount > 0:
                cost_text += texts.t(
                    'DEVICE_CHANGE_DISCOUNT_INFO',
                    '(折扣{percent}%:-{amount})',
                ).format(
                    percent=devices_discount_percent,
                    amount=texts.format_price(total_discount),
                )
        else:
            cost_text = texts.t('DEVICE_CHANGE_FREE', '免费')

    else:
        price = 0
        action_text = texts.t(
            'DEVICE_CHANGE_ACTION_DECREASE',
            '减少到{count}',
        ).format(count=new_devices_count)
        cost_text = texts.t('DEVICE_CHANGE_NO_REFUND', '不退款')

    # Проверяем количество подключённых устройств для предупреждения
    devices_warning = ''
    remnawave_uuid = _get_remnawave_uuid(subscription, db_user)
    if new_devices_count < current_devices and remnawave_uuid:
        try:
            service = RemnaWaveService()
            async with service.get_api_client() as api:
                response = await api._make_request('GET', f'/api/hwid/devices/{remnawave_uuid}')
                if response and 'response' in response:
                    connected_count = response['response'].get('total', 0)
                    if connected_count > new_devices_count:
                        devices_warning = texts.t(
                            'DEVICE_CHANGE_RESET_WARNING',
                            (
                                '⚠️<b>注意！</b>\n您已连接 {connected} 设备。\n当限制减少到{new}时，所有设备将被重置。\n您将需要重新连接所需的设备。'
                            ),
                        ).format(connected=connected_count, new=new_devices_count)
        except Exception as e:
            logger.error('Ошибка проверки устройств', error=e)

    confirm_text = texts.t(
        'DEVICE_CHANGE_CONFIRMATION',
        (
            '📱<b>确认更改</b>\n\n当前数量：{current}台设备\n新数量：{new}台设备\n\n操作：{action}\n💰{cost}\n\n确认更改吗？'
        ),
    ).format(
        current=current_devices,
        new=new_devices_count,
        action=action_text,
        cost=cost_text,
    )

    if devices_warning:
        confirm_text += devices_warning

    await callback.message.edit_text(
        confirm_text,
        reply_markup=get_confirm_change_devices_keyboard(
            new_devices_count,
            price,
            db_user.language,
            back_callback=f'sm:{sub_id}' if settings.is_multi_tariff_enabled() and sub_id else 'subscription_settings',
        ),
    )

    await callback.answer()


async def execute_change_devices(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    callback_parts = callback.data.split('_')
    texts = get_texts(db_user.language)
    try:
        new_devices_count = int(callback_parts[3])
    except (ValueError, IndexError):
        await callback.answer(texts.t('INVALID_REQUEST', 'Invalid request'), show_alert=True)
        return

    db_user = await lock_user_for_pricing(db, db_user.id)
    # Re-resolve after lock since db_user was refreshed
    subscription, _ = await _resolve_subscription(callback, db_user, db, state)
    if not subscription:
        await callback.answer(
            texts.t('NO_ACTIVE_SUBSCRIPTION', '⚠️您没有有效的订阅'),
            show_alert=True,
        )
        return
    current_devices = subscription.device_limit

    # Проверяем тариф подписки
    tariff = None
    if subscription.tariff_id:
        from app.database.crud.tariff import get_tariff_by_id

        tariff = await get_tariff_by_id(db, subscription.tariff_id)

    # Для тарифов - проверяем разрешено ли изменение устройств
    if tariff:
        tariff_device_price = getattr(tariff, 'device_price_kopeks', None)
        if tariff_device_price is None or tariff_device_price <= 0:
            await callback.answer(
                texts.t('TARIFF_DEVICES_DISABLED', '⚠️ 您的套餐不支持更换设备'),
                show_alert=True,
            )
            return
        price_per_device = tariff_device_price
    elif not settings.is_devices_selection_enabled():
        await callback.answer(
            texts.t('DEVICES_SELECTION_DISABLED', '⚠️设备数量选择不可用'),
            show_alert=True,
        )
        return
    else:
        price_per_device = settings.PRICE_PER_DEVICE

    # Минимум при уменьшении всегда 1 (device_limit тарифа — это "включено при покупке", а не нижняя граница)
    if new_devices_count < 1:
        await callback.answer(
            texts.t(
                'DEVICES_MIN_LIMIT_REACHED',
                '⚠️ 最小设备数量：{limit}',
            ).format(limit=1),
            show_alert=True,
        )
        return

    # Recompute price under lock (callback-baked value may be stale)
    devices_difference = new_devices_count - current_devices
    if devices_difference > 0:
        if tariff:
            chargeable_devices = devices_difference
        elif current_devices < settings.DEFAULT_DEVICE_LIMIT:
            free_devices = settings.DEFAULT_DEVICE_LIMIT - current_devices
            chargeable_devices = max(0, devices_difference - free_devices)
        else:
            chargeable_devices = devices_difference

        devices_price_per_month = chargeable_devices * price_per_device
        days_left = max(1, (subscription.end_date - datetime.now(UTC)).days)
        devices_discount_percent = PricingEngine.get_addon_discount_percent(
            db_user,
            'devices',
            days_left,
        )
        discounted_per_month, _ = apply_percentage_discount(
            devices_price_per_month,
            devices_discount_percent,
        )
        price = int(discounted_per_month * days_left / 30)
        price = max(100, price)
    else:
        price = 0

    try:
        if price > 0:
            success = await subtract_user_balance(
                db, db_user, price, f'Изменение количества устройств с {current_devices} до {new_devices_count}'
            )

            if not success:
                await callback.answer(
                    texts.t('PAYMENT_CHARGE_ERROR', '⚠️扣款失败'),
                    show_alert=True,
                )
                return

            charged_days = max(1, (subscription.end_date - datetime.now(UTC)).days)
            await create_transaction(
                db=db,
                user_id=db_user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=price,
                description=f'Изменение устройств с {current_devices} до {new_devices_count} за {charged_days} дн.',
            )

        # Re-lock subscription after subtract_user_balance committed (released all locks)
        relock_result = await db.execute(
            select(Subscription)
            .where(Subscription.id == subscription.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        subscription = relock_result.scalar_one()

        # Re-validate: prevent double-charge and max-limit violation
        if new_devices_count > current_devices:
            tariff_max_recheck = getattr(tariff, 'max_device_limit', None) if tariff else None
            max_devices = (
                tariff_max_recheck if tariff_max_recheck is not None and tariff_max_recheck > 0 else None
            ) or (settings.MAX_DEVICES_LIMIT if settings.MAX_DEVICES_LIMIT > 0 else None)
            if max_devices and new_devices_count > max_devices:
                if price > 0:
                    user_refund = await db.execute(
                        select(User)
                        .where(User.id == db_user.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    refund_user = user_refund.scalar_one()
                    refund_user.balance_kopeks += price
                    await db.commit()
                await callback.answer(
                    f'⚠️ 已超出设备限制 ({max_devices})。余额已退回。',
                    show_alert=True,
                )
                return
            # Check if concurrent request already applied the same change
            if price > 0 and subscription.device_limit >= new_devices_count:
                user_refund = await db.execute(
                    select(User)
                    .where(User.id == db_user.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                refund_user = user_refund.scalar_one()
                refund_user.balance_kopeks += price
                await db.commit()
                await callback.answer(
                    '⚠️ 更改已应用。余额已退回。',
                    show_alert=True,
                )
                return

        subscription.device_limit = new_devices_count
        subscription.updated_at = datetime.now(UTC)

        await db.commit()

        # Реактивируем подписку если она была DISABLED/EXPIRED (например, после LIMITED/EXPIRED в RemnaWave)
        from app.database.crud.subscription import reactivate_subscription

        await reactivate_subscription(db, subscription)

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        # Явно включаем пользователя на панели (PATCH может не снять LIMITED-статус)
        remnawave_uuid = _get_remnawave_uuid(subscription, db_user)
        if remnawave_uuid and subscription.status == 'active':
            await subscription_service.enable_remnawave_user(remnawave_uuid)

        # При уменьшении лимита - удалить лишние устройства (последние подключённые)
        devices_reset_count = 0
        if new_devices_count < current_devices and remnawave_uuid:
            try:
                service = RemnaWaveService()
                async with service.get_api_client() as api:
                    response = await api._make_request('GET', f'/api/hwid/devices/{remnawave_uuid}')
                    if response and 'response' in response:
                        devices_list = response['response'].get('devices', [])
                        connected_count = len(devices_list)

                        # Если подключённых устройств больше чем новый лимит - удалить лишние
                        if connected_count > new_devices_count:
                            devices_to_remove = connected_count - new_devices_count
                            logger.info(
                                '🔧 Удаление лишних устройств при уменьшении лимита: подключено новый лимит удаляем',
                                connected_count=connected_count,
                                new_devices_count=new_devices_count,
                                devices_to_remove=devices_to_remove,
                            )

                            # Сортируем по дате (последние в конце) и удаляем последние
                            sorted_devices = sorted(
                                devices_list,
                                key=lambda d: d.get('updatedAt') or d.get('createdAt') or '',
                            )
                            devices_to_delete = sorted_devices[-devices_to_remove:]

                            for device in devices_to_delete:
                                device_hwid = device.get('hwid')
                                if device_hwid:
                                    try:
                                        delete_data = {'userUuid': remnawave_uuid, 'hwid': device_hwid}
                                        await api._make_request('POST', '/api/hwid/devices/delete', data=delete_data)
                                        devices_reset_count += 1
                                        logger.info('✅ Удалено устройство', device_hwid=device_hwid)
                                    except Exception as del_error:
                                        logger.error(
                                            'Ошибка удаления устройства', device_hwid=device_hwid, del_error=del_error
                                        )
            except Exception as reset_error:
                logger.error('Ошибка удаления устройств при уменьшении лимита', reset_error=reset_error)

        await db.refresh(db_user)
        await db.refresh(subscription)

        try:
            from app.services.admin_notification_service import AdminNotificationService

            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_update_notification(
                db, db_user, subscription, 'devices', current_devices, new_devices_count, price
            )
        except Exception as e:
            logger.error('Ошибка отправки уведомления об изменении устройств', error=e)

        if new_devices_count > current_devices:
            success_text = texts.t(
                'DEVICE_CHANGE_INCREASE_SUCCESS',
                '✅设备数量已增加！\n\n',
            )
            success_text += texts.t(
                'DEVICE_CHANGE_RESULT_LINE',
                '📱之前：{old}→现在：{new}\n',
            ).format(old=current_devices, new=new_devices_count)
            if price > 0:
                success_text += texts.t(
                    'DEVICE_CHANGE_CHARGED',
                    '💰已扣除：{amount}',
                ).format(amount=texts.format_price(price))
        else:
            success_text = texts.t(
                'DEVICE_CHANGE_DECREASE_SUCCESS',
                '✅设备数量已减少！\n\n',
            )
            success_text += texts.t(
                'DEVICE_CHANGE_RESULT_LINE',
                '📱之前：{old}→现在：{new}\n',
            ).format(old=current_devices, new=new_devices_count)
            if devices_reset_count > 0:
                success_text += texts.t(
                    'DEVICE_CHANGE_DEVICES_REMOVED',
                    '🗑已删除的设备：{count}',
                ).format(count=devices_reset_count)
            success_text += texts.t(
                'DEVICE_CHANGE_NO_REFUND_INFO',
                'ℹ️不退款',
            )

        await callback.message.edit_text(success_text, reply_markup=get_back_keyboard(db_user.language))

        logger.info(
            '✅ Пользователь изменил количество устройств с на доплата: ₽',
            telegram_id=db_user.telegram_id,
            current_devices=current_devices,
            new_devices_count=new_devices_count,
            price=price / 100,
        )

    except Exception as e:
        logger.error('Ошибка изменения количества устройств', error=e)
        await callback.message.edit_text(texts.ERROR, reply_markup=get_back_keyboard(db_user.language))

    await callback.answer()


async def handle_device_management(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t('PAID_FEATURE_ONLY', '⚠此功能仅适用于付费订阅'),
            show_alert=True,
        )
        return

    remnawave_uuid = _get_remnawave_uuid(subscription, db_user)
    if not remnawave_uuid:
        await callback.answer(
            texts.t('DEVICE_UUID_NOT_FOUND', '❌未找到用户UUID'),
            show_alert=True,
        )
        return

    try:
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{remnawave_uuid}')

            if response and 'response' in response:
                devices_info = response['response']
                total_devices = devices_info.get('total', 0)
                devices_list = devices_info.get('devices', [])

                if total_devices == 0:
                    await callback.message.edit_text(
                        texts.t('DEVICE_NONE_CONNECTED', 'ℹ️您没有已连接的设备'),
                        reply_markup=get_back_keyboard(db_user.language),
                    )
                    await callback.answer()
                    return

                await show_devices_page(callback, db_user, devices_list, page=1, sub_id=sub_id)
            else:
                await callback.answer(
                    texts.t(
                        'DEVICE_FETCH_INFO_ERROR',
                        '❌获取设备信息失败',
                    ),
                    show_alert=True,
                )

    except Exception as e:
        logger.error('Ошибка получения списка устройств', error=e)
        await callback.answer(
            texts.t(
                'DEVICE_FETCH_INFO_ERROR',
                '❌获取设备信息失败',
            ),
            show_alert=True,
        )

    await callback.answer()


async def show_devices_page(
    callback: types.CallbackQuery, db_user: User, devices_list: list[dict], page: int = 1, sub_id: int | None = None
):
    texts = get_texts(db_user.language)
    devices_per_page = 5

    pagination = paginate_list(devices_list, page=page, per_page=devices_per_page)

    devices_text = texts.t(
        'DEVICE_MANAGEMENT_OVERVIEW',
        (
            '🔄<b>设备管理</b>\n\n📊总共连接：{total}台设备\n📄第{page}页，共{pages}页\n\n'
        ),
    ).format(total=len(devices_list), page=pagination.page, pages=pagination.total_pages)

    if pagination.items:
        devices_text += texts.t(
            'DEVICE_MANAGEMENT_CONNECTED_HEADER',
            '<b>已连接设备：</b>\n',
        )
        for i, device in enumerate(pagination.items, 1):
            platform = device.get('platform', 'Unknown')
            device_model = device.get('deviceModel', 'Unknown')
            device_info = f'{platform} - {device_model}'

            if len(device_info) > 35:
                device_info = device_info[:32] + '...'

            devices_text += texts.t(
                'DEVICE_MANAGEMENT_LIST_ITEM',
                '•{device}\n',
            ).format(device=device_info)

    devices_text += texts.t(
        'DEVICE_MANAGEMENT_ACTIONS',
        ('\n💡<b>操作：</b>\n•选择要重置的设备\n•或者立即重置所有设备'),
    )

    await callback.message.edit_text(
        devices_text,
        reply_markup=get_devices_management_keyboard(
            pagination.items,
            pagination,
            db_user.language,
            back_callback=f'sm:{sub_id}' if settings.is_multi_tariff_enabled() and sub_id else 'subscription_settings',
        ),
    )


async def handle_devices_page(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None):
    page = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    remnawave_uuid = _get_remnawave_uuid(subscription, db_user) if subscription else db_user.remnawave_uuid

    try:
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{remnawave_uuid}')

            if response and 'response' in response:
                devices_list = response['response'].get('devices', [])
                await show_devices_page(callback, db_user, devices_list, page=page, sub_id=sub_id)
            else:
                await callback.answer(
                    texts.t('DEVICE_FETCH_ERROR', '❌获取设备失败'),
                    show_alert=True,
                )

    except Exception as e:
        logger.error('Ошибка перехода на страницу устройств', error=e)
        await callback.answer(
            texts.t('DEVICE_PAGE_LOAD_ERROR', '❌加载页面失败'),
            show_alert=True,
        )


async def handle_single_device_reset(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    remnawave_uuid = _get_remnawave_uuid(subscription, db_user) if subscription else db_user.remnawave_uuid
    try:
        callback_parts = callback.data.split('_')
        if len(callback_parts) < 4:
            logger.error('Некорректный формат callback_data', callback_data=callback.data)
            await callback.answer(
                texts.t('DEVICE_RESET_INVALID_REQUEST', '❌错误：请求无效'),
                show_alert=True,
            )
            return

        device_index = int(callback_parts[2])
        page = int(callback_parts[3])

        logger.info('🔧 Сброс устройства: index=, page', device_index=device_index, page=page)

    except (ValueError, IndexError) as e:
        logger.error('❌ Ошибка парсинга callback_data', callback_data=callback.data, error=e)
        await callback.answer(
            texts.t('DEVICE_RESET_PARSE_ERROR', '❌处理请求失败'),
            show_alert=True,
        )
        return

    try:
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{remnawave_uuid}')

            if response and 'response' in response:
                devices_list = response['response'].get('devices', [])

                devices_per_page = 5
                pagination = paginate_list(devices_list, page=page, per_page=devices_per_page)

                if device_index < len(pagination.items):
                    device = pagination.items[device_index]
                    device_hwid = device.get('hwid')

                    if device_hwid:
                        delete_data = {'userUuid': remnawave_uuid, 'hwid': device_hwid}

                        await api._make_request('POST', '/api/hwid/devices/delete', data=delete_data)

                        platform = device.get('platform', 'Unknown')
                        device_model = device.get('deviceModel', 'Unknown')
                        device_info = f'{platform} - {device_model}'

                        await callback.answer(
                            texts.t(
                                'DEVICE_RESET_SUCCESS',
                                '✅设备{device}已成功重置！',
                            ).format(device=device_info),
                            show_alert=True,
                        )

                        updated_response = await api._make_request('GET', f'/api/hwid/devices/{remnawave_uuid}')
                        if updated_response and 'response' in updated_response:
                            updated_devices = updated_response['response'].get('devices', [])

                            if updated_devices:
                                updated_pagination = paginate_list(
                                    updated_devices, page=page, per_page=devices_per_page
                                )
                                if not updated_pagination.items and page > 1:
                                    page = page - 1

                                await show_devices_page(callback, db_user, updated_devices, page=page, sub_id=sub_id)
                            else:
                                await callback.message.edit_text(
                                    texts.t(
                                        'DEVICE_RESET_ALL_DONE',
                                        'ℹ️所有设备已重置',
                                    ),
                                    reply_markup=get_back_keyboard(db_user.language),
                                )

                        logger.info(
                            '✅ Пользователь сбросил устройство',
                            telegram_id=db_user.telegram_id,
                            device_info=device_info,
                        )
                    else:
                        await callback.answer(
                            texts.t(
                                'DEVICE_RESET_ID_FAILED',
                                '❌获取设备ID失败',
                            ),
                            show_alert=True,
                        )
                else:
                    await callback.answer(
                        texts.t('DEVICE_RESET_NOT_FOUND', '❌未找到设备'),
                        show_alert=True,
                    )
            else:
                await callback.answer(
                    texts.t('DEVICE_FETCH_ERROR', '❌获取设备失败'),
                    show_alert=True,
                )

    except Exception as e:
        logger.error('Ошибка сброса устройства', error=e)
        await callback.answer(
            texts.t('DEVICE_RESET_ERROR', '❌重置设备失败'),
            show_alert=True,
        )


async def handle_all_devices_reset_from_management(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    remnawave_uuid = _get_remnawave_uuid(subscription, db_user) if subscription else db_user.remnawave_uuid

    if not remnawave_uuid:
        await callback.answer(
            texts.t('DEVICE_UUID_NOT_FOUND', '❌未找到用户UUID'),
            show_alert=True,
        )
        return

    try:
        from app.services.remnawave_service import RemnaWaveService

        service = RemnaWaveService()

        async with service.get_api_client() as api:
            devices_response = await api._make_request('GET', f'/api/hwid/devices/{remnawave_uuid}')

            if not devices_response or 'response' not in devices_response:
                await callback.answer(
                    texts.t(
                        'DEVICE_LIST_FETCH_ERROR',
                        '❌获取设备列表失败',
                    ),
                    show_alert=True,
                )
                return

            devices_list = devices_response['response'].get('devices', [])

            if not devices_list:
                await callback.answer(
                    texts.t('DEVICE_NONE_CONNECTED', 'ℹ️您没有已连接的设备'),
                    show_alert=True,
                )
                return

            logger.info('🔧 Найдено устройств для сброса', devices_list_count=len(devices_list))

            success_count = 0
            failed_count = 0

            for device in devices_list:
                device_hwid = device.get('hwid')
                if device_hwid:
                    try:
                        delete_data = {'userUuid': remnawave_uuid, 'hwid': device_hwid}

                        await api._make_request('POST', '/api/hwid/devices/delete', data=delete_data)
                        success_count += 1
                        logger.info('✅ Устройство удалено', device_hwid=device_hwid)

                    except Exception as device_error:
                        failed_count += 1
                        logger.error(
                            '❌ Ошибка удаления устройства', device_hwid=device_hwid, device_error=device_error
                        )
                else:
                    failed_count += 1
                    logger.warning('⚠️ У устройства нет HWID', device=device)

            if success_count > 0:
                if failed_count == 0:
                    await callback.message.edit_text(
                        texts.t(
                            'DEVICE_RESET_ALL_SUCCESS_MESSAGE',
                            (
                                '✅<b>所有设备已成功重置！</b>\n\n🔄已重置：{count}台设备\n📱您现在可以重新连接您的设备\n\n💡使用“我的订阅”部分中的链接重新连接'
                            ),
                        ).format(count=success_count),
                        reply_markup=get_back_keyboard(db_user.language),
                        parse_mode='HTML',
                    )
                    logger.info(
                        '✅ Пользователь успешно сбросил устройств',
                        telegram_id=db_user.telegram_id,
                        success_count=success_count,
                    )
                else:
                    await callback.message.edit_text(
                        texts.t(
                            'DEVICE_RESET_PARTIAL_MESSAGE',
                            (
                                '⚠️<b>部分设备重置</b>\n\n✅已删除：{success}台设备\n❌删除失败：{failed}台设备\n\n请重试或联系支持。'
                            ),
                        ).format(success=success_count, failed=failed_count),
                        reply_markup=get_back_keyboard(db_user.language),
                        parse_mode='HTML',
                    )
                    logger.warning(
                        '⚠️ Частичный сброс у пользователя /',
                        telegram_id=db_user.telegram_id,
                        success_count=success_count,
                        devices_list_count=len(devices_list),
                    )
            else:
                await callback.message.edit_text(
                    texts.t(
                        'DEVICE_RESET_ALL_FAILED_MESSAGE',
                        (
                            '❌<b>重置设备失败</b>\n\n请稍后再试或联系技术支持。\n\n总设备数：{total}'
                        ),
                    ).format(total=len(devices_list)),
                    reply_markup=get_back_keyboard(db_user.language),
                    parse_mode='HTML',
                )
                logger.error(
                    '❌ Не удалось сбросить ни одного устройства у пользователя', telegram_id=db_user.telegram_id
                )

    except Exception as e:
        logger.error('Ошибка сброса всех устройств', error=e)
        await callback.message.edit_text(texts.ERROR, reply_markup=get_back_keyboard(db_user.language))

    await callback.answer()


async def confirm_add_devices(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None):
    devices_count = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return

    # Проверяем тариф подписки
    tariff = None
    if subscription.tariff_id:
        from app.database.crud.tariff import get_tariff_by_id

        tariff = await get_tariff_by_id(db, subscription.tariff_id)

    # Для тарифов - проверяем разрешено ли добавление устройств
    tariff_device_price = getattr(tariff, 'device_price_kopeks', None) if tariff else None
    if tariff:
        if tariff_device_price is None or tariff_device_price <= 0:
            await callback.answer(
                texts.t('TARIFF_DEVICES_DISABLED', '⚠️ 添加设备不适用于您的套餐'),
                show_alert=True,
            )
            return
        price_per_device = tariff_device_price
    else:
        if not settings.is_devices_selection_enabled():
            await callback.answer(
                texts.t('DEVICES_SELECTION_DISABLED', '⚠️设备数量选择不可用'),
                show_alert=True,
            )
            return
        price_per_device = settings.PRICE_PER_DEVICE

    resume_callback = None

    new_total_devices = subscription.device_limit + devices_count

    # Используем max_device_limit из тарифа если есть, иначе глобальную настройку
    tariff_max_devices = getattr(tariff, 'max_device_limit', None) if tariff else None
    effective_max = tariff_max_devices or (settings.MAX_DEVICES_LIMIT if settings.MAX_DEVICES_LIMIT > 0 else None)
    if effective_max and new_total_devices > effective_max:
        await callback.answer(
            texts.t(
                'DEVICES_LIMIT_EXCEEDED_DETAIL',
                '⚠️ 已超出最大设备限制 ({limit})。您有：{current}，添加：{adding}',
            ).format(limit=effective_max, current=subscription.device_limit, adding=devices_count),
            show_alert=True,
        )
        return

    devices_price_per_month = devices_count * price_per_device

    # TOCTOU: lock user row before reading promo/discount state
    db_user = await lock_user_for_pricing(db, db_user.id)

    # Проверяем является ли тариф суточным
    is_daily_tariff = tariff and getattr(tariff, 'is_daily', False)

    if is_daily_tariff:
        # Для суточных тарифов считаем по дням (как в кабинете)
        now = datetime.now(UTC)
        days_left = max(1, (subscription.end_date - now).days)
        period_hint_days = days_left

        devices_discount_percent = PricingEngine.get_addon_discount_percent(
            db_user,
            'devices',
            period_hint_days,
        )
        discounted_per_month, discount_per_month = apply_percentage_discount(
            devices_price_per_month,
            devices_discount_percent,
        )
        # Цена = месячная_цена * days_left / 30
        price = int(discounted_per_month * days_left / 30)
        price = max(100, price)  # Минимум 1 рубль
        total_discount = int(discount_per_month * days_left / 30)
        period_label = f'{days_left} дн.' if days_left > 1 else '1 день'
    else:
        # Для обычных тарифов - по дням (как в кабинете)
        now = datetime.now(UTC)
        days_left = max(1, (subscription.end_date - now).days)
        period_hint_days = days_left

        devices_discount_percent = PricingEngine.get_addon_discount_percent(
            db_user,
            'devices',
            period_hint_days,
        )
        discounted_per_month, discount_per_month = apply_percentage_discount(
            devices_price_per_month,
            devices_discount_percent,
        )
        # Цена = месячная_цена * days_left / 30
        price = int(discounted_per_month * days_left / 30)
        price = max(100, price)  # Минимум 1 рубль
        total_discount = int(discount_per_month * days_left / 30)
        period_label = f'{days_left} дн.' if days_left > 1 else '1 день'

    logger.info(
        'Добавление устройств: ₽/мес × = ₽ (скидка ₽)',
        devices_count=devices_count,
        discounted_per_month=discounted_per_month / 100,
        period_label=period_label,
        price=price / 100,
        total_discount=total_discount / 100,
    )

    if price > 0 and db_user.balance_kopeks < price:
        missing_kopeks = price - db_user.balance_kopeks
        required_text = f'{texts.format_price(price)} (за {period_label})'
        message_text = texts.t(
            'ADDON_INSUFFICIENT_FUNDS_MESSAGE',
            (
                '⚠️<b>资金不足</b>\n\n服务费用：{required}\n当前余额：{balance}\n缺少：{missing}\n\n请选择充值方式。金额将自动填入。'
            ),
        ).format(
            required=required_text,
            balance=texts.format_price(db_user.balance_kopeks),
            missing=texts.format_price(missing_kopeks),
        )

        # Сохраняем корзину для автопокупки после пополнения баланса
        await user_cart_service.save_user_cart(
            user_id=db_user.id,
            cart_data={
                'cart_mode': 'add_devices',
                'devices_to_add': devices_count,
                'price_kopeks': price,
            },
        )
        logger.info(
            'Сохранена корзина add_devices для пользователя : + устройств, цена коп.',
            telegram_id=db_user.telegram_id,
            devices_count=devices_count,
            price=price,
        )

        await callback.message.edit_text(
            message_text,
            reply_markup=get_insufficient_balance_keyboard(
                db_user.language,
                resume_callback=resume_callback,
                amount_kopeks=missing_kopeks,
                has_saved_cart=True,
            ),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    try:
        success = await subtract_user_balance(
            db, db_user, price, f'Добавление {devices_count} устройств на {period_label}'
        )

        if not success:
            await callback.answer('⚠️ 借方错误', show_alert=True)
            return

        # Re-lock subscription after subtract_user_balance committed (released all locks)
        relock_result = await db.execute(
            select(Subscription)
            .where(Subscription.id == subscription.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        subscription = relock_result.scalar_one()

        # Re-validate max device limit after re-lock
        actual_current = subscription.device_limit or 1
        actual_new = actual_current + devices_count
        tariff_max_recheck = getattr(tariff, 'max_device_limit', None) if tariff else None
        max_devices = tariff_max_recheck or (settings.MAX_DEVICES_LIMIT if settings.MAX_DEVICES_LIMIT > 0 else None)
        if max_devices and actual_new > max_devices:
            # Concurrent purchase exceeded limit — refund
            user_refund = await db.execute(
                select(User).where(User.id == db_user.id).with_for_update().execution_options(populate_existing=True)
            )
            refund_user = user_refund.scalar_one()
            refund_user.balance_kopeks += price
            await db.commit()
            await callback.answer(
                f'⚠️ 已超出设备限制 ({max_devices})。余额已退回。',
                show_alert=True,
            )
            return

        subscription.device_limit = actual_new
        subscription.updated_at = datetime.now(UTC)
        await db.commit()

        # Реактивируем подписку если она была DISABLED/EXPIRED (например, после LIMITED/EXPIRED в RemnaWave)
        from app.database.crud.subscription import reactivate_subscription

        await reactivate_subscription(db, subscription)

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        # Явно включаем пользователя на панели (PATCH может не снять LIMITED-статус)
        remnawave_uuid = _get_remnawave_uuid(subscription, db_user)
        if remnawave_uuid and subscription.status == 'active':
            await subscription_service.enable_remnawave_user(remnawave_uuid)

        await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=price,
            description=f'Добавление {devices_count} устройств на {period_label}',
        )

        await db.refresh(db_user)
        await db.refresh(subscription)

        # Отправляем уведомление админам о докупке устройств
        try:
            from app.services.admin_notification_service import AdminNotificationService

            notification_service = AdminNotificationService(callback.bot)
            old_device_limit = subscription.device_limit - devices_count
            await notification_service.send_subscription_update_notification(
                db, db_user, subscription, 'devices', old_device_limit, subscription.device_limit, price
            )
        except Exception as e:
            logger.error('Ошибка отправки уведомления о докупке устройств', error=e)

        success_text = (
            f'✅ 设备添加成功！\n\n📱 新增：{devices_count} 设备\n新限制：{subscription.device_limit} 设备'
        )
        success_text += f'💰 注销：{texts.format_price(price)}（对于 {period_label}）'
        if total_discount > 0:
            success_text += f'（折扣{devices_discount_percent}%：-{texts.format_price(total_discount)}）'

        await callback.message.edit_text(success_text, reply_markup=get_back_keyboard(db_user.language))

        logger.info(
            '✅ Пользователь добавил устройств за ₽',
            telegram_id=db_user.telegram_id,
            devices_count=devices_count,
            price=price / 100,
        )

    except Exception as e:
        logger.error('Ошибка добавления устройств', error=e)
        await callback.message.edit_text(texts.ERROR, reply_markup=get_back_keyboard(db_user.language))

    await callback.answer()


async def handle_reset_devices(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    await handle_device_management(callback, db_user, db, state)


async def confirm_reset_devices(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    await handle_device_management(callback, db_user, db, state)


async def handle_device_guide(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None):
    device_type = callback.data.split('_')[2]
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return
    subscription_link = get_display_subscription_link(subscription)

    if not subscription_link:
        await callback.answer(
            texts.t('SUBSCRIPTION_LINK_UNAVAILABLE', '❌订阅链接不可用'),
            show_alert=True,
        )
        return

    apps = await get_apps_for_platform_async(device_type, db_user.language)

    hide_subscription_link = settings.should_hide_subscription_link()

    if not apps:
        await callback.answer(
            texts.t('SUBSCRIPTION_DEVICE_APPS_NOT_FOUND', '❌未找到此设备的应用程序'),
            show_alert=True,
        )
        return

    featured_app = next((app for app in apps if app.get('isFeatured', False)), apps[0])
    featured_app_id = featured_app.get('id')
    other_apps = [app for app in apps if isinstance(app, dict) and app.get('id') and app.get('id') != featured_app_id]

    other_app_names = ', '.join(
        html_mod.escape(str(app.get('name')).strip())
        for app in other_apps
        if isinstance(app.get('name'), str) and app.get('name').strip()
    )

    if hide_subscription_link:
        link_section = (
            texts.t('SUBSCRIPTION_DEVICE_LINK_TITLE', '🔗<b>订阅链接：</b>')
            + '\n'
            + texts.t(
                'SUBSCRIPTION_LINK_HIDDEN_NOTICE',
                'ℹ️订阅链接在下方按钮中或“我的订阅”部分可用。',
            )
            + '\n\n'
        )
    else:
        link_section = (
            texts.t('SUBSCRIPTION_DEVICE_LINK_TITLE', '🔗<b>订阅链接：</b>')
            + f'\n<code>{html_mod.escape(subscription_link)}</code>\n\n'
        )

    guide_text = (
        texts.t(
            'SUBSCRIPTION_DEVICE_GUIDE_TITLE',
            '📱<b>{device_name}设置</b>',
        ).format(device_name=html_mod.escape(get_device_name(device_type, db_user.language)))
        + '\n\n'
        + link_section
        + texts.t(
            'SUBSCRIPTION_DEVICE_FEATURED_APP',
            '📋<b>推荐应用：</b>{app_name}',
        ).format(app_name=html_mod.escape(featured_app.get('name', '')))
    )

    if other_app_names:
        guide_text += '\n\n' + texts.t(
            'SUBSCRIPTION_DEVICE_OTHER_APPS',
            '📦<b>其他应用：</b>{app_list}',
        ).format(app_list=other_app_names)
        guide_text += '\n' + texts.t(
            'SUBSCRIPTION_DEVICE_OTHER_APPS_HINT',
            '点击下方的“其他应用”按钮选择应用。',
        )

    blocks_text = render_guide_blocks(featured_app.get('blocks', []), db_user.language)
    if blocks_text:
        guide_text += '\n\n' + blocks_text

    guide_text += '\n\n' + texts.t('SUBSCRIPTION_DEVICE_HOW_TO_TITLE', '💡<b>如何连接：</b>')
    guide_text += '\n' + '\n'.join(
        [
            texts.t(
                'SUBSCRIPTION_DEVICE_HOW_TO_STEP1',
                '1.通过上方链接安装应用',
            ),
            texts.t(
                'SUBSCRIPTION_DEVICE_HOW_TO_STEP2',
                '2.点击下方的“连接”按钮',
            ),
            texts.t(
                'SUBSCRIPTION_DEVICE_HOW_TO_STEP3',
                '3.打开应用并粘贴链接',
            ),
            texts.t(
                'SUBSCRIPTION_DEVICE_HOW_TO_STEP4',
                '4.连接到服务器',
            ),
        ]
    )

    await callback.message.edit_text(
        guide_text,
        reply_markup=get_connection_guide_keyboard(
            subscription_link,
            featured_app,
            device_type,
            db_user.language,
            has_other_apps=bool(other_apps),
            sub_id=sub_id,
        ),
        parse_mode='HTML',
    )
    await callback.answer()


async def handle_app_selection(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    device_type = callback.data.split('_')[2]
    texts = get_texts(db_user.language)

    apps = await get_apps_for_platform_async(device_type, db_user.language)

    if not apps:
        await callback.answer(
            texts.t('SUBSCRIPTION_DEVICE_APPS_NOT_FOUND', '❌未找到此设备的应用程序'),
            show_alert=True,
        )
        return

    app_text = (
        texts.t(
            'SUBSCRIPTION_APPS_TITLE',
            '📱<b>适用于{device_name}的应用程序</b>',
        ).format(device_name=html_mod.escape(get_device_name(device_type, db_user.language)))
        + '\n\n'
        + texts.t('SUBSCRIPTION_APPS_PROMPT', '请选择要连接的应用程序：')
    )

    await callback.message.edit_text(
        app_text, reply_markup=get_app_selection_keyboard(device_type, apps, db_user.language), parse_mode='HTML'
    )
    await callback.answer()


async def handle_specific_app_guide(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    parts = callback.data.split('_', 2)
    if len(parts) < 3:
        await callback.answer('Invalid callback data', show_alert=True)
        return
    _, device_type, app_id = parts
    texts = get_texts(db_user.language)
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return

    subscription_link = get_display_subscription_link(subscription)

    if not subscription_link:
        await callback.answer(
            texts.t('SUBSCRIPTION_LINK_UNAVAILABLE', '❌订阅链接不可用'),
            show_alert=True,
        )
        return

    apps = await get_apps_for_platform_async(device_type, db_user.language)
    app = next((a for a in apps if a.get('id') == app_id), None) if apps else None

    if not app:
        await callback.answer(
            texts.t('SUBSCRIPTION_APP_NOT_FOUND', '❌未找到应用程序'),
            show_alert=True,
        )
        return

    hide_subscription_link = settings.should_hide_subscription_link()

    if hide_subscription_link:
        link_section = (
            texts.t('SUBSCRIPTION_DEVICE_LINK_TITLE', '🔗<b>订阅链接：</b>')
            + '\n'
            + texts.t(
                'SUBSCRIPTION_LINK_HIDDEN_NOTICE',
                'ℹ️订阅链接在下方按钮中或“我的订阅”部分可用。',
            )
            + '\n\n'
        )
    else:
        link_section = (
            texts.t('SUBSCRIPTION_DEVICE_LINK_TITLE', '🔗<b>订阅链接：</b>')
            + f'\n<code>{html_mod.escape(subscription_link)}</code>\n\n'
        )

    guide_text = (
        texts.t(
            'SUBSCRIPTION_SPECIFIC_APP_TITLE',
            '📱<b>{app_name}-{device_name}</b>',
        ).format(
            app_name=html_mod.escape(app.get('name', '')),
            device_name=html_mod.escape(get_device_name(device_type, db_user.language)),
        )
        + '\n\n'
        + link_section
    )

    blocks_text = render_guide_blocks(app.get('blocks', []), db_user.language)
    if blocks_text:
        guide_text += blocks_text + '\n\n'

    await callback.message.edit_text(
        guide_text,
        reply_markup=get_specific_app_keyboard(
            subscription_link,
            app,
            device_type,
            db_user.language,
            sub_id=sub_id,
        ),
        parse_mode='HTML',
    )
    await callback.answer()


async def show_device_connection_help(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext = None
):
    subscription, sub_id = await _resolve_subscription(callback, db_user, db, state)
    if subscription is None:
        return
    subscription_link = get_display_subscription_link(subscription)

    if not subscription_link:
        await callback.answer('❌ 订阅链接不可用', show_alert=True)
        return

    help_text = f'📱 <b>如何重新连接设备</b>\n\n重置设备后，您需要：\n\n<b>1.获取订阅链接：</b>\n📋复制下面的链接或在“我的订阅”部分找到它\n\n<b>2.设置 VPN 应用程序：</b>\n• 打开您的VPN 应用程序\n• 找到“添加订阅”或“导入”功能\n• 粘贴复制的链接\n\n<b>3.连接：</b>\n• 选择服务器\n• 单击“连接”\n\n<b>🔗您的订阅链接：</b>\n<代码>{html_mod.escape(subscription_link)}</code>\n\n💡 <b>提示：</b> 保存此链接 - 您将需要它来连接新设备'

    await callback.message.edit_text(
        help_text, reply_markup=get_device_management_help_keyboard(db_user.language), parse_mode='HTML'
    )
    await callback.answer()
