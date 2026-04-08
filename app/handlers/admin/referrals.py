import asyncio
import html
import json
from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.referral import (
    get_referral_statistics,
    get_top_referrers_by_period,
)
from app.database.crud.user import get_user_by_id, get_user_by_telegram_id
from app.database.models import ReferralEarning, User, WithdrawalRequest, WithdrawalRequestStatus
from app.localization.texts import get_texts
from app.services.referral_withdrawal_service import referral_withdrawal_service
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


@admin_required
@error_handler
async def show_referral_statistics(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        stats = await get_referral_statistics(db)

        avg_per_referrer = 0
        if stats.get('active_referrers', 0) > 0:
            avg_per_referrer = stats.get('total_paid_kopeks', 0) / stats['active_referrers']

        current_time = datetime.now(UTC).strftime('%H:%M:%S')

        text = f"🤝 <b>推荐统计</b>\n\n<b>一般指标：</b>\n- 有推荐的用户：{stats.get('users_with_referrals', 0)}\n- 活跃推荐人：{stats.get('active_referrers', 0)}\n- 支付总额：{settings.format_price(stats.get('total_paid_kopeks', 0))}\n\n<b>期间：</b>\n- 今天：{settings.format_price(stats.get('today_earnings_kopeks', 0))}\n- 一周：{settings.format_price(stats.get('week_earnings_kopeks', 0))}\n- 一个月：{settings.format_price(stats.get('month_earnings_kopeks', 0))}\n\n<b>平均指标：</b>\n- 每个推荐人：{settings.format_price(int(avg_per_referrer))}\n\n<b>前 5 名推荐人：</b>"

        top_referrers = stats.get('top_referrers', [])
        if top_referrers:
            for i, referrer in enumerate(top_referrers[:5], 1):
                earned = referrer.get('total_earned_kopeks', 0)
                count = referrer.get('referrals_count', 0)
                user_id = referrer.get('user_id', 'N/A')

                if count > 0:
                    text += f'{i}。 ID {user_id}：{settings.format_price(earned)}（{count} 参考）'
                else:
                    logger.warning('推荐人有推荐，但位于顶部', user_id=user_id, count=count)
        else:
            text += '无数据'

        text += f"<b>推荐系统设置：</b>\n- 最低存款：{settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}\n- 首次存款奖金：{settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}\n- 邀请者奖励：{settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}\n- 购买佣金：{settings.REFERRAL_COMMISSION_PERCENT}%\n- 通知：{('✅ 已启用' if settings.REFERRAL_NOTIFICATIONS_ENABLED else '❌ 已禁用')}\n\n<i>🕐更新：{current_time}</i>"

        keyboard_rows = [
            [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_referrals')],
            [types.InlineKeyboardButton(text='👥 热门推荐者', callback_data='admin_referrals_top')],
            [types.InlineKeyboardButton(text='🔍 日志诊断', callback_data='admin_referral_diagnostics')],
        ]

        # Кнопка заявок на вывод (если функция 已启用)
        if settings.is_referral_withdrawal_enabled():
            keyboard_rows.append(
                [types.InlineKeyboardButton(text='💸 提款申请', callback_data='admin_withdrawal_requests')]
            )

        keyboard_rows.extend(
            [
                [types.InlineKeyboardButton(text='⚙️设置', callback_data='admin_referrals_settings')],
                [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_panel')],
            ]
        )

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
            await callback.answer('已更新')
        except Exception as edit_error:
            if 'message is not modified' in str(edit_error):
                await callback.answer('数据是最新的')
            else:
                logger.error('编辑消息时出错', edit_error=edit_error)
                await callback.answer('更新错误')

    except Exception as e:
        logger.error('show_referral_statistics 错误', error=e, exc_info=True)

        current_time = datetime.now(UTC).strftime('%H:%M:%S')
        text = f'🤝 <b>推荐统计</b>\n\n❌ <b>数据加载错误</b>\n\n<b>当前设置：</b>\n- 最低存款：{settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}\n- 首次存款奖金：{settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}\n- 邀请者奖励：{settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}\n- 购买佣金：{settings.REFERRAL_COMMISSION_PERCENT}%\n\n<i>🕐 时间：{current_time}</i>'

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='🔄 重试', callback_data='admin_referrals')],
                [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_panel')],
            ]
        )

        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except:
            pass
        await callback.answer('加载统计信息时发生错误')


def _get_top_keyboard(period: str, sort_by: str) -> types.InlineKeyboardMarkup:
    """Создаёт клавиатуру для выбора периода и сортировки."""
    period_week = '✅ 近 7 天' if period == 'week' else '近 7 天'
    period_month = '✅ 近 30 天' if period == 'month' else '近 30 天'
    sort_earnings = '✅ 按收益' if sort_by == 'earnings' else '按收益'
    sort_invited = '✅ 按邀请数' if sort_by == 'invited' else '按邀请数'

    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text=period_week, callback_data=f'admin_top_ref:week:{sort_by}'),
                types.InlineKeyboardButton(text=period_month, callback_data=f'admin_top_ref:month:{sort_by}'),
            ],
            [
                types.InlineKeyboardButton(text=sort_earnings, callback_data=f'admin_top_ref:{period}:earnings'),
                types.InlineKeyboardButton(text=sort_invited, callback_data=f'admin_top_ref:{period}:invited'),
            ],
            [types.InlineKeyboardButton(text='🔄 刷新', callback_data=f'admin_top_ref:{period}:{sort_by}')],
            [types.InlineKeyboardButton(text='⬅️ 统计', callback_data='admin_referrals')],
        ]
    )


@admin_required
@error_handler
async def show_top_referrers(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Показывает топ рефереров (по умолчанию: неделя, по заработку)."""
    await _show_top_referrers_filtered(callback, db, period='week', sort_by='earnings')


@admin_required
@error_handler
async def show_top_referrers_filtered(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Обрабатывает выбор периода и сортировки."""
    # Парсим callback_data: admin_top_ref:period:sort_by
    parts = callback.data.split(':')
    if len(parts) != 3:
        await callback.answer('参数错误')
        return

    period = parts[1]  # week или month
    sort_by = parts[2]  # earnings или invited

    if period not in ('week', 'month'):
        period = 'week'
    if sort_by not in ('earnings', 'invited'):
        sort_by = 'earnings'

    await _show_top_referrers_filtered(callback, db, period, sort_by)


async def _show_top_referrers_filtered(callback: types.CallbackQuery, db: AsyncSession, period: str, sort_by: str):
    """Внутренняя функция отображения топа с фильтрами."""
    try:
        top_referrers = await get_top_referrers_by_period(db, period=period, sort_by=sort_by)

        period_text = '近 7 天' if period == 'week' else '近 30 天'
        sort_text = '按收益' if sort_by == 'earnings' else '按邀请数'

        text = f'🏆 <b>顶级推荐人 {period_text}</b>'
        text += f'\n<i>排序：{sort_text}</i>\n\n'

        if top_referrers:
            for i, referrer in enumerate(top_referrers[:20], 1):
                earned = referrer.get('earnings_kopeks', 0)
                count = referrer.get('invited_count', 0)
                display_name = referrer.get('display_name', 'N/A')
                username = referrer.get('username', '')
                telegram_id = referrer.get('telegram_id')
                user_email = referrer.get('email', '')
                user_id = referrer.get('user_id', '')
                id_display = telegram_id or user_email or f'#{user_id}' if user_id else 'N/A'

                if username:
                    display_text = f'@{html.escape(username)} (ID{id_display})'
                elif display_name and display_name != f'ID{id_display}':
                    display_text = f'{html.escape(display_name)} (ID{id_display})'
                else:
                    display_text = f'ID{id_display}'

                emoji = ''
                if i == 1:
                    emoji = '🥇 '
                elif i == 2:
                    emoji = '🥈 '
                elif i == 3:
                    emoji = '🥉 '

                # Выделяем основную метрику в зависимости 起 сортировки
                if sort_by == 'invited':
                    text += f'{emoji}{i}. {display_text}\n'
                    text += f'👥 <b>{count} 邀请</b> | 💰{settings.format_price(earned)}'
                else:
                    text += f'{emoji}{i}. {display_text}\n'
                    text += f'💰 <b>{settings.format_price(earned)}</b> | 👥 {count} 受邀'
        else:
            text += '所选期间没有数据'

        keyboard = _get_top_keyboard(period, sort_by)

        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
            await callback.answer()
        except Exception as edit_error:
            if 'message is not modified' in str(edit_error):
                await callback.answer('数据是最新的')
            else:
                raise

    except Exception as e:
        logger.error('show_top_referrers_filtered 中的错误', error=e, exc_info=True)
        await callback.answer('加载热门引荐来源网址时出错')


@admin_required
@error_handler
async def show_referral_settings(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    text = f"⚙️ <b>推荐系统设置</b>\n\n<b>奖金和奖励：</b>\n• 参与最低存款金额：{settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}\n• 推荐首次存款奖金：{settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}\n• 邀请人首次存款奖金：{settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}\n\n<b> 佣金：</b>\n• 每次推荐购买的百分比：{settings.REFERRAL_COMMISSION_PERCENT}%\n\n<b>通知：</b>\n• 状态：{('✅ 已启用' if settings.REFERRAL_NOTIFICATIONS_ENABLED else '❌ 已禁用')}\n• 发送尝试：{getattr(settings, 'REFERRAL_NOTIFICATION_RETRY_ATTEMPTS', 3)}\n\n<i>💡 要更改设置，请编辑 .env 文件并重新启动机器人</i>"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 统计', callback_data='admin_referrals')]]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def show_pending_withdrawal_requests(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Показывает список ожидающих заявок на вывод."""
    requests = await referral_withdrawal_service.get_pending_requests(db)

    if not requests:
        text = '📋 <b> 提现请求</b>\n\n没有待处理的申请。'

        keyboard_rows = []
        # Кнопка тестового начисления (только в тестовом режиме)
        if settings.REFERRAL_WITHDRAWAL_TEST_MODE:
            keyboard_rows.append(
                [types.InlineKeyboardButton(text='🧪 测试累积', callback_data='admin_test_referral_earning')]
            )
        keyboard_rows.append([types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_referrals')])

        await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows))
        await callback.answer()
        return

    text = f'📋 <b> 请求提现 ({len(requests)})</b>'

    for req in requests[:10]:
        user = await get_user_by_id(db, req.user_id)
        user_name = html.escape(user.full_name) if user and user.full_name else '未知'
        user_tg_id = user.telegram_id if user else 'N/A'

        risk_emoji = (
            '🟢' if req.risk_score < 30 else '🟡' if req.risk_score < 50 else '🟠' if req.risk_score < 70 else '🔴'
        )

        text += f'<b>#{req.id}</b> — {user_name} (ID{user_tg_id})\n'
        text += f'💰 {req.amount_kopeks / 100:.0f}₽ | {risk_emoji} 风险：{req.risk_score}/100'
        text += f'📅 {req.created_at.strftime("%d.%m.%Y %H:%M")}\n\n'

    keyboard_rows = []
    for req in requests[:5]:
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=f'#{req.id} — {req.amount_kopeks / 100:.0f}₽', callback_data=f'admin_withdrawal_view_{req.id}'
                )
            ]
        )

    # Кнопка тестового начисления (только в тестовом режиме)
    if settings.REFERRAL_WITHDRAWAL_TEST_MODE:
        keyboard_rows.append(
            [types.InlineKeyboardButton(text='🧪 测试累积', callback_data='admin_test_referral_earning')]
        )

    keyboard_rows.append([types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_referrals')])

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows))
    await callback.answer()


@admin_required
@error_handler
async def view_withdrawal_request(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Показывает детали заявки на вывод."""
    request_id = int(callback.data.split('_')[-1])

    result = await db.execute(select(WithdrawalRequest).where(WithdrawalRequest.id == request_id))
    request = result.scalar_one_or_none()

    if not request:
        await callback.answer('未找到应用程序', show_alert=True)
        return

    user = await get_user_by_id(db, request.user_id)
    user_name = html.escape(user.full_name) if user and user.full_name else '未知'
    user_tg_id = (user.telegram_id or user.email or f'#{user.id}') if user else 'N/A'

    analysis = json.loads(request.risk_analysis) if request.risk_analysis else {}

    status_text = {
        WithdrawalRequestStatus.PENDING.value: '⏳ 待处理',
        WithdrawalRequestStatus.APPROVED.value: '✅ 已批准',
        WithdrawalRequestStatus.REJECTED.value: '❌ 已拒绝',
        WithdrawalRequestStatus.COMPLETED.value: '✅ 已完成',
        WithdrawalRequestStatus.CANCELLED.value: '🚫 已取消',
    }.get(request.status, request.status)

    text = f"📋 <b>应用#{request.id}</b>\n\n👤 用户：{user_name}\n🆔 ID：<code>{user_tg_id}</code>\n💰 金额：<b>{request.amount_kopeks / 100:.0f}₽</b>\n📊状态：{status_text}\n\n💳 <b> 详细信息：</b>\n<code>{html.escape(request.payment_details or '')}</code>\n\n📅 创建者：{request.created_at.strftime('%d.%m.%Y %H:%M')}\n\n{referral_withdrawal_service.format_analysis_for_admin(analysis)}"

    keyboard = []

    if request.status == WithdrawalRequestStatus.PENDING.value:
        keyboard.append(
            [
                types.InlineKeyboardButton(text='✅ 批准', callback_data=f'admin_withdrawal_approve_{request.id}'),
                types.InlineKeyboardButton(text='❌ 拒绝', callback_data=f'admin_withdrawal_reject_{request.id}'),
            ]
        )

    if request.status == WithdrawalRequestStatus.APPROVED.value:
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text='✅ 汇款', callback_data=f'admin_withdrawal_complete_{request.id}'
                )
            ]
        )

    if user:
        keyboard.append(
            [types.InlineKeyboardButton(text='👤 用户资料', callback_data=f'admin_user_manage_{user.id}')]
        )
    keyboard.append([types.InlineKeyboardButton(text='⬅️ 前往列表', callback_data='admin_withdrawal_requests')])

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def approve_withdrawal_request(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Одобряет заявку на вывод."""
    request_id = int(callback.data.split('_')[-1])

    result = await db.execute(select(WithdrawalRequest).where(WithdrawalRequest.id == request_id))
    request = result.scalar_one_or_none()

    if not request:
        await callback.answer('未找到应用程序', show_alert=True)
        return

    success, error = await referral_withdrawal_service.approve_request(db, request_id, db_user.id)

    if success:
        # Уведомляем пользователя (только если есть telegram_id)
        user = await get_user_by_id(db, request.user_id)
        if user and user.telegram_id:
            try:
                texts = get_texts(user.language)
                await callback.bot.send_message(
                    user.telegram_id,
                    texts.t(
                        'REFERRAL_WITHDRAWAL_APPROVED',
                        '✅ <b>提款请求#{id}已获批准！</b>\n\n金额：<b>{amount}</b>\n资金已从余额中扣除。\n\n期望转移到指定的详细信息。',
                    ).format(id=request.id, amount=texts.format_price(request.amount_kopeks)),
                )
            except Exception as e:
                logger.error('向用户发送通知时出错', error=e)

        await callback.answer('✅ 申请获批，资金从余额中扣除')

        # Обновляем отображение
        await view_withdrawal_request(callback, db_user, db)
    else:
        await callback.answer(f'❌ {error}', show_alert=True)


@admin_required
@error_handler
async def reject_withdrawal_request(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Отклоняет заявку на вывод."""
    request_id = int(callback.data.split('_')[-1])

    result = await db.execute(select(WithdrawalRequest).where(WithdrawalRequest.id == request_id))
    request = result.scalar_one_or_none()

    if not request:
        await callback.answer('未找到应用程序', show_alert=True)
        return

    success, _error = await referral_withdrawal_service.reject_request(
        db, request_id, db_user.id, '被管理员拒绝'
    )

    if success:
        # Уведомляем пользователя (только если есть telegram_id)
        user = await get_user_by_id(db, request.user_id)
        if user and user.telegram_id:
            try:
                texts = get_texts(user.language)
                await callback.bot.send_message(
                    user.telegram_id,
                    texts.t(
                        'REFERRAL_WITHDRAWAL_REJECTED',
                        '❌ <b>提款请求#{id}被拒绝</b>\n\n金额：<b>{amount}</b>\n\n如果您有疑问，请联系支持人员。',
                    ).format(id=request.id, amount=texts.format_price(request.amount_kopeks)),
                )
            except Exception as e:
                logger.error('向用户发送通知时出错', error=e)

        await callback.answer('❌ 申请被拒绝')

        # Обновляем отображение
        await view_withdrawal_request(callback, db_user, db)
    else:
        await callback.answer('❌ 拒绝错误', show_alert=True)


@admin_required
@error_handler
async def complete_withdrawal_request(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Отмечает заявку как выполненную (деньги переведены)."""
    request_id = int(callback.data.split('_')[-1])

    result = await db.execute(select(WithdrawalRequest).where(WithdrawalRequest.id == request_id))
    request = result.scalar_one_or_none()

    if not request:
        await callback.answer('未找到应用程序', show_alert=True)
        return

    success, _error = await referral_withdrawal_service.complete_request(db, request_id, db_user.id, '翻译完成')

    if success:
        # Уведомляем пользователя (только если есть telegram_id)
        user = await get_user_by_id(db, request.user_id)
        if user and user.telegram_id:
            try:
                texts = get_texts(user.language)
                await callback.bot.send_message(
                    user.telegram_id,
                    texts.t(
                        'REFERRAL_WITHDRAWAL_COMPLETED',
                        '💸 <b> 申请#{id} 付款已完成！</b>\n\n金额：<b>{amount}</b>\n\n款项已发送至指定详细信息。',
                    ).format(id=request.id, amount=texts.format_price(request.amount_kopeks)),
                )
            except Exception as e:
                logger.error('向用户发送通知时出错', error=e)

        await callback.answer('✅ 申请完成')

        # Обновляем отображение
        await view_withdrawal_request(callback, db_user, db)
    else:
        await callback.answer('❌ 运行时错误', show_alert=True)


@admin_required
@error_handler
async def start_test_referral_earning(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext
):
    """Начинает процесс тестового начисления реферального дохода."""
    if not settings.REFERRAL_WITHDRAWAL_TEST_MODE:
        await callback.answer('测试模式已禁用', show_alert=True)
        return

    await state.set_state(AdminStates.test_referral_earning_input)

    text = '🧪 <b>测试推荐收入的累积</b>\n\n按以下格式输入数据：\n<code>telegram_id amount_in_rubles</code>\n\n示例：\n• <code>123456789 500</code> - 将向用户123456789存入500₽\n• <code>987654321 1000</code> - 将向用户987654321存入1000₽\n\n⚠️ 这将创建一个真实的 ReferralEarning 记录，就像用户从推荐中赚取的一样。'

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_withdrawal_requests')]]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def process_test_referral_earning(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    """Обрабатывает ввод тестового начисления."""
    if not settings.REFERRAL_WITHDRAWAL_TEST_MODE:
        await message.answer('❌ 测试模式已禁用')
        await state.clear()
        return

    text_input = message.text.strip()
    parts = text_input.split()

    if len(parts) != 2:
        await message.answer(
            '❌ 格式无效。输入：<code>telegram_id amount</code>\n\n例如：<code>123456789 500</code>'
        )
        return

    try:
        target_telegram_id = int(parts[0])
        amount_rubles = float(parts[1].replace(',', '.'))
        amount_kopeks = int(amount_rubles * 100)

        if amount_kopeks <= 0:
            await message.answer('❌金额必须为正数')
            return

        if amount_kopeks > 10000000:  # Лимит 100 000₽
            await message.answer('❌ 最大测试累积金额：100,000₽')
            return

    except ValueError:
        await message.answer(
            '❌ 数字格式不正确。输入：<code>telegram_id amount</code>\n\n例如：<code>123456789 500</code>'
        )
        return

    # Ищем целевого пользователя
    target_user = await get_user_by_telegram_id(db, target_telegram_id)
    if not target_user:
        await message.answer(f'❌ 数据库中未找到 ID {target_telegram_id} 的用户')
        return

    # Создаём тестовое начисление
    earning = ReferralEarning(
        user_id=target_user.id,
        referral_id=target_user.id,  # Сам на себя (тестовое)
        amount_kopeks=amount_kopeks,
        reason='test_earning',
    )
    db.add(earning)

    # Добавляем на баланс пользователя
    from app.database.crud.user import lock_user_for_update

    target_user = await lock_user_for_update(db, target_user)
    target_user.balance_kopeks += amount_kopeks

    await db.commit()
    await state.clear()

    await message.answer(
        f"✅ <b>测试入账已创建！</b>\n\n👤 用户：{(html.escape(target_user.full_name) if target_user.full_name else '未命名')}\n🆔 ID：<code>{target_telegram_id}</code>\n💰 金额：<b>{amount_rubles:.0f}₽</b>\n💳 新余额：<b>{target_user.balance_kopeks / 100:.0f}₽</b>\n\n该金额将作为推荐收益记入。",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='📋申请', callback_data='admin_withdrawal_requests')],
                [types.InlineKeyboardButton(text='👤 简介', callback_data=f'admin_user_manage_{target_user.id}')],
            ]
        ),
    )

    logger.info(
        '测试应计：管理员将 ₽ 记入用户名下',
        telegram_id=db_user.telegram_id,
        amount_rubles=amount_rubles,
        target_telegram_id=target_telegram_id,
    )


def _get_period_dates(period: str) -> tuple[datetime, datetime]:
    """Возвращает начальную и конечную даты для заданного периода."""
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == 'today':
        start_date = today
        end_date = today + timedelta(days=1)
    elif period == 'yesterday':
        start_date = today - timedelta(days=1)
        end_date = today
    elif period == 'week':
        start_date = today - timedelta(days=7)
        end_date = today + timedelta(days=1)
    elif period == 'month':
        start_date = today - timedelta(days=30)
        end_date = today + timedelta(days=1)
    else:
        # По умолчанию — сегодня
        start_date = today
        end_date = today + timedelta(days=1)

    return start_date, end_date


def _get_period_display_name(period: str) -> str:
    """Возвращает человекочитаемое название периода."""
    names = {'today': '今天', 'yesterday': '昨天', 'week': '近 7 天', 'month': '近 30 天'}
    return names.get(period, '今天')


async def _show_diagnostics_for_period(callback: types.CallbackQuery, db: AsyncSession, state: FSMContext, period: str):
    """Внутренняя функция для отображения диагностики за указанный период."""
    try:
        await callback.answer('正在分析日志...')

        from app.services.referral_diagnostics_service import referral_diagnostics_service

        # Сохраняем период в state
        await state.update_data(diagnostics_period=period)
        from app.states import AdminStates

        await state.set_state(AdminStates.referral_diagnostics_period)

        # Получаем даты периода
        start_date, end_date = _get_period_dates(period)

        # Анализируем логи
        report = await referral_diagnostics_service.analyze_period(db, start_date, end_date)

        # Формируем отчёт
        period_display = _get_period_display_name(period)

        text = f'🔍 <b> 推荐诊断 - {period_display}</b>\n\n<b>📊 转化统计：</b>\n• 推荐链接的总点击次数：{report.total_ref_clicks}\n• 唯一用户：{report.unique_users_clicked}\n• 丢失推荐：{len(report.lost_referrals)}'

        if report.lost_referrals:
            text += '<b>❌ 失去推荐：</b>'
            text += '<i>（通过链接获取，但未统计推荐人）</i>'

            for i, lost in enumerate(report.lost_referrals[:15], 1):
                # Статус пользователя
                if not lost.registered:
                    status = '⚠️ 不在数据库中'
                elif not lost.has_referrer:
                    status = '❌ 无推荐人'
                else:
                    status = f'⚡ 其他推荐人（ID{lost.current_referrer_id}）'

                # Имя или ID
                if lost.username:
                    user_name = f'@{html.escape(lost.username)}'
                elif lost.full_name:
                    user_name = html.escape(lost.full_name)
                else:
                    user_name = f'ID{lost.telegram_id}'

                # Ожидаемый реферер
                referrer_info = ''
                if lost.expected_referrer_name:
                    referrer_info = f' → {html.escape(lost.expected_referrer_name)}'
                elif lost.expected_referrer_id:
                    referrer_info = f' → ID{lost.expected_referrer_id}'

                # Время
                time_str = lost.click_time.strftime('%H:%M')

                text += f'{i}. {user_name} — {status}\n'
                text += f'   <code>{html.escape(lost.referral_code)}</code>{referrer_info} ({time_str})\n'

            if len(report.lost_referrals) > 15:
                text += f'<i>...还有 {len(report.lost_referrals) - 15}</i>'
        else:
            text += '✅ <b>所有推荐均已计算！</b>'

        # Информация о логах
        log_path = referral_diagnostics_service.log_path
        log_exists = await asyncio.to_thread(log_path.exists)
        log_size = (await asyncio.to_thread(log_path.stat)).st_size if log_exists else 0

        text += f'\n<i>📂 {log_path.name}'
        if log_exists:
            text += f' ({log_size / 1024:.0f} KB)'
            text += f'|行：{report.lines_in_period}'
        else:
            text += '（未找到！）'
        text += '</i>'

        # Кнопки: только "Сегодня" (текущий лог) и "Загрузить файл" (старые логи)
        keyboard_rows = [
            [
                types.InlineKeyboardButton(text='📅 今天（当前日志）', callback_data='admin_ref_diag:today'),
            ],
            [types.InlineKeyboardButton(text='📤下载日志文件', callback_data='admin_ref_diag_upload')],
            [types.InlineKeyboardButton(text='🔍检查奖金（通过数据库）', callback_data='admin_ref_check_bonuses')],
            [
                types.InlineKeyboardButton(
                    text='🏆 与比赛同步', callback_data='admin_ref_sync_contest'
                )
            ],
        ]

        # Кнопки действий (только если есть потерянные рефералы)
        if report.lost_referrals:
            keyboard_rows.append(
                [types.InlineKeyboardButton(text='📋 预览补丁', callback_data='admin_ref_fix_preview')]
            )

        keyboard_rows.extend(
            [
                [types.InlineKeyboardButton(text='🔄 刷新', callback_data=f'admin_ref_diag:{period}')],
                [types.InlineKeyboardButton(text='⬅️ 统计', callback_data='admin_referrals')],
            ]
        )

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        await callback.message.edit_text(text, reply_markup=keyboard)

    except Exception as e:
        logger.error('_show_diagnostics_for_period 中出现错误', error=e, exc_info=True)
        await callback.answer('分析日志时出错', show_alert=True)


@admin_required
@error_handler
async def show_referral_diagnostics(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    """Показывает диагностику реферальной системы по логам."""
    # Определяем период из callback_data или используем "today" по умолчанию
    if ':' in callback.data:
        period = callback.data.split(':')[1]
    else:
        period = 'today'

    await _show_diagnostics_for_period(callback, db, state, period)


@admin_required
@error_handler
async def preview_referral_fixes(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    """Показывает предпросмотр исправлений потерянных рефералов."""
    try:
        await callback.answer('正在分析...')

        # Получаем период из state
        state_data = await state.get_data()
        period = state_data.get('diagnostics_period', 'today')

        from app.services.referral_diagnostics_service import DiagnosticReport, referral_diagnostics_service

        # Проверяем, работаем ли с загруженным файлом
        if period == 'uploaded_file':
            # Используем сохранённый отчёт из загруженного файла (десериализуем)
            report_data = state_data.get('uploaded_file_report')
            if not report_data:
                await callback.answer('未找到上传的文件报告', show_alert=True)
                return
            report = DiagnosticReport.from_dict(report_data)
            period_display = '已上传文件'
        else:
            # Получаем даты периода
            start_date, end_date = _get_period_dates(period)

            # Анализируем логи
            report = await referral_diagnostics_service.analyze_period(db, start_date, end_date)
            period_display = _get_period_display_name(period)

        if not report.lost_referrals:
            await callback.answer('无需修复丢失的推荐', show_alert=True)
            return

        # Запускаем предпросмотр исправлений
        fix_report = await referral_diagnostics_service.fix_lost_referrals(db, report.lost_referrals, apply=False)

        # Формируем отчёт
        text = f'📋 <b> 补丁预览 - {period_display}</b>\n\n<b>📊 将做什么：</b>\n• 固定推荐：{fix_report.users_fixed}\n• 推荐奖金：{settings.format_price(fix_report.bonuses_to_referrals)}\n• 推荐奖金：{settings.format_price(fix_report.bonuses_to_referrers)}\n• 错误：{fix_report.errors}\n\n<b>🔍详情：</b>'

        # Показываем первые 10 деталей
        for i, detail in enumerate(fix_report.details[:10], 1):
            if detail.username:
                user_name = f'@{html.escape(detail.username)}'
            elif detail.full_name:
                user_name = html.escape(detail.full_name)
            else:
                user_name = f'ID{detail.telegram_id}'

            if detail.error:
                text += f'{i}. {user_name} — ❌ {html.escape(str(detail.error))}\n'
            else:
                text += f'{i}. {user_name}\n'
                if detail.referred_by_set:
                    referrer_display = (
                        html.escape(detail.referrer_name) if detail.referrer_name else f'ID{detail.referrer_id}'
                    )
                    text += f'• 推荐人：{referrer_display}'
                if detail.had_first_topup:
                    text += f'• 第一次补货：{settings.format_price(detail.topup_amount_kopeks)}'
                if detail.bonus_to_referral_kopeks > 0:
                    text += f'• 推荐奖金：{settings.format_price(detail.bonus_to_referral_kopeks)}'
                if detail.bonus_to_referrer_kopeks > 0:
                    text += f'• 推荐人奖金：{settings.format_price(detail.bonus_to_referrer_kopeks)}'

        if len(fix_report.details) > 10:
            text += f'<i>...还有 {len(fix_report.details) - 10}</i>'

        text += '⚠️ <b>注意！</b> 这只是预览。单击“应用”进行更正。'

        # Кнопка назад зависит 起 источника
        back_button_text = '⬅️ 返回诊断'
        back_button_callback = f'admin_ref_diag:{period}' if period != 'uploaded_file' else 'admin_referral_diagnostics'

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='✅ 应用更正', callback_data='admin_ref_fix_apply')],
                [types.InlineKeyboardButton(text=back_button_text, callback_data=back_button_callback)],
            ]
        )

        await callback.message.edit_text(text, reply_markup=keyboard)

    except Exception as e:
        logger.error('Preview_referral_fixes 中的错误', error=e, exc_info=True)
        await callback.answer('创建预览时出错', show_alert=True)


@admin_required
@error_handler
async def apply_referral_fixes(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    """Применяет исправления потерянных рефералов."""
    try:
        await callback.answer('应用修复...')

        # Получаем период из state
        state_data = await state.get_data()
        period = state_data.get('diagnostics_period', 'today')

        from app.services.referral_diagnostics_service import DiagnosticReport, referral_diagnostics_service

        # Проверяем, работаем ли с загруженным файлом
        if period == 'uploaded_file':
            # Используем сохранённый отчёт из загруженного файла (десериализуем)
            report_data = state_data.get('uploaded_file_report')
            if not report_data:
                await callback.answer('未找到上传的文件报告', show_alert=True)
                return
            report = DiagnosticReport.from_dict(report_data)
            period_display = '已上传文件'
        else:
            # Получаем даты периода
            start_date, end_date = _get_period_dates(period)

            # Анализируем логи
            report = await referral_diagnostics_service.analyze_period(db, start_date, end_date)
            period_display = _get_period_display_name(period)

        if not report.lost_referrals:
            await callback.answer('无需修复丢失的推荐', show_alert=True)
            return

        # Применяем исправления
        fix_report = await referral_diagnostics_service.fix_lost_referrals(db, report.lost_referrals, apply=True)

        # Формируем отчёт
        text = f'✅ <b>应用更正 - {period_display}</b>\n\n<b>📊 结果：</b>\n• 固定推荐：{fix_report.users_fixed}\n• 推荐奖金：{settings.format_price(fix_report.bonuses_to_referrals)}\n• 推荐奖金：{settings.format_price(fix_report.bonuses_to_referrers)}\n• 错误：{fix_report.errors}\n\n<b>🔍详情：</b>'

        # Показываем первые 10 успешных деталей
        success_count = 0
        for detail in fix_report.details:
            if not detail.error and success_count < 10:
                success_count += 1
                if detail.username:
                    user_name = f'@{html.escape(detail.username)}'
                elif detail.full_name:
                    user_name = html.escape(detail.full_name)
                else:
                    user_name = f'ID{detail.telegram_id}'

                text += f'{success_count}. {user_name}\n'
                if detail.referred_by_set:
                    referrer_display = (
                        html.escape(detail.referrer_name) if detail.referrer_name else f'ID{detail.referrer_id}'
                    )
                    text += f'• 推荐人：{referrer_display}'
                if detail.bonus_to_referral_kopeks > 0:
                    text += f'• 推荐奖金：{settings.format_price(detail.bonus_to_referral_kopeks)}'
                if detail.bonus_to_referrer_kopeks > 0:
                    text += f'• 推荐人奖金：{settings.format_price(detail.bonus_to_referrer_kopeks)}'

        if fix_report.users_fixed > 10:
            text += f'<i>...以及更多 {fix_report.users_fixed - 10} 更正</i>'

        # Показываем ошибки
        if fix_report.errors > 0:
            text += '<b>❌ 错误：</b>'
            error_count = 0
            for detail in fix_report.details:
                if detail.error and error_count < 5:
                    error_count += 1
                    if detail.username:
                        user_name = f'@{html.escape(detail.username)}'
                    elif detail.full_name:
                        user_name = html.escape(detail.full_name)
                    else:
                        user_name = f'ID{detail.telegram_id}'
                    text += f'• {user_name}: {html.escape(str(detail.error))}\n'
            if fix_report.errors > 5:
                text += f'<i>...以及更多 {fix_report.errors - 5} 错误</i>'

        # Кнопки зависят 起 источника
        keyboard_rows = []
        if period != 'uploaded_file':
            keyboard_rows.append(
                [types.InlineKeyboardButton(text='🔄 更新诊断', callback_data=f'admin_ref_diag:{period}')]
            )
        keyboard_rows.append([types.InlineKeyboardButton(text='⬅️ 统计', callback_data='admin_referrals')])

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        await callback.message.edit_text(text, reply_markup=keyboard)

        # Очищаем сохранённый отчёт из state
        if period == 'uploaded_file':
            await state.update_data(uploaded_file_report=None)

    except Exception as e:
        logger.error('apply_referral_fixes 中的错误', error=e, exc_info=True)
        await callback.answer('应用补丁时出错', show_alert=True)


# =============================================================================
# Проверка бонусов по БД
# =============================================================================


@admin_required
@error_handler
async def check_missing_bonuses(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    """Проверяет по БД — всем ли рефералам начислены бонусы."""
    from app.services.referral_diagnostics_service import (
        referral_diagnostics_service,
    )

    await callback.answer('🔍检查奖金...')

    try:
        report = await referral_diagnostics_service.check_missing_bonuses(db)

        # Сохраняем отчёт в state для последующего применения
        await state.update_data(missing_bonuses_report=report.to_dict())

        text = f'🔍 <b>通过数据库查看奖金</b>\n\n📊 <b>统计：</b>\n• 总推荐数：{report.total_referrals_checked}\n• 补货≥最小值：{report.referrals_with_topup}\n• <b>无奖金：{len(report.missing_bonuses)}</b>'

        if report.missing_bonuses:
            text += f'💰 <b> 所需积分：</b>\n• 推荐人：{report.total_missing_to_referrals / 100:.0f}₽\n• 推荐人：{report.total_missing_to_referrers / 100:.0f}₽\n• <b>总计：{(report.total_missing_to_referrals + report.total_missing_to_referrers) / 100:.0f}₽</b>\n\n👤<b>名单（{len(report.missing_bonuses)}人）：</b>'
            for i, mb in enumerate(report.missing_bonuses[:15], 1):
                referral_name = html.escape(
                    mb.referral_full_name or mb.referral_username or str(mb.referral_telegram_id)
                )
                referrer_name = html.escape(
                    mb.referrer_full_name or mb.referrer_username or str(mb.referrer_telegram_id)
                )
                text += f'\n{i}. <b>{referral_name}</b>'
                text += f'└ 邀请人：{referrer_name}'
                text += f'└ 补货：{mb.first_topup_amount_kopeks / 100:.0f}₽'
                text += f'└ 奖励：{mb.referral_bonus_amount / 100:.0f}₽ + {mb.referrer_bonus_amount / 100:.0f}₽'

            if len(report.missing_bonuses) > 15:
                text += f'<i>...还有{len(report.missing_bonuses) - 15}人</i>'

            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='✅ 累积所有奖金', callback_data='admin_ref_bonus_apply')],
                    [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_ref_check_bonuses')],
                    [types.InlineKeyboardButton(text='⬅️走向诊断', callback_data='admin_referral_diagnostics')],
                ]
            )
        else:
            text += '✅ <b>所有奖金已发放！</b>'
            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_ref_check_bonuses')],
                    [types.InlineKeyboardButton(text='⬅️走向诊断', callback_data='admin_referral_diagnostics')],
                ]
            )

        await callback.message.edit_text(text, reply_markup=keyboard)

    except Exception as e:
        logger.error('check_missing_bonuses 错误', error=e, exc_info=True)
        await callback.answer('检查奖金时出错', show_alert=True)


@admin_required
@error_handler
async def apply_missing_bonuses(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    """Применяет начисление пропущенных бонусов."""
    from app.services.referral_diagnostics_service import (
        MissingBonusReport,
        referral_diagnostics_service,
    )

    await callback.answer('💰我发奖金了...')

    try:
        # Получаем сохранённый отчёт
        data = await state.get_data()
        report_dict = data.get('missing_bonuses_report')

        if not report_dict:
            await callback.answer('❌ 未找到报告。更新支票。', show_alert=True)
            return

        report = MissingBonusReport.from_dict(report_dict)

        if not report.missing_bonuses:
            await callback.answer('✅ 没有奖金', show_alert=True)
            return

        # Применяем исправления
        fix_report = await referral_diagnostics_service.fix_missing_bonuses(db, report.missing_bonuses, apply=True)

        text = f'✅ <b> 获得奖金！</b>\n\n📊 <b> 结果：</b>\n• 处理者：{fix_report.users_fixed} 用户\n• 记入推荐：{fix_report.bonuses_to_referrals / 100:.0f}₽\n• 记入推荐人：{fix_report.bonuses_to_referrers / 100:.0f}₽\n• <b>总计：{(fix_report.bonuses_to_referrals + fix_report.bonuses_to_referrers) / 100:.0f}₽</b>'

        if fix_report.errors > 0:
            text += f'⚠️错误：{fix_report.errors}'

        # Очищаем отчёт из state
        await state.update_data(missing_bonuses_report=None)

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='🔍再次检查', callback_data='admin_ref_check_bonuses')],
                [types.InlineKeyboardButton(text='⬅️走向诊断', callback_data='admin_referral_diagnostics')],
            ]
        )

        await callback.message.edit_text(text, reply_markup=keyboard)

    except Exception as e:
        logger.error('apply_missing_bonuses 错误', error=e, exc_info=True)
        await callback.answer('计算奖金时出错', show_alert=True)


@admin_required
@error_handler
async def sync_referrals_with_contest(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext
):
    """Синхронизирует всех рефералов с активными конкурсами."""
    from app.database.crud.referral_contest import get_contests_for_events
    from app.services.referral_contest_service import referral_contest_service

    await callback.answer('🏆 我与比赛同步...')

    try:
        now_utc = datetime.now(UTC)

        # Получаем активные конкурсы
        paid_contests = await get_contests_for_events(db, now_utc, contest_types=['referral_paid'])
        reg_contests = await get_contests_for_events(db, now_utc, contest_types=['referral_registered'])

        all_contests = list(paid_contests) + list(reg_contests)

        if not all_contests:
            await callback.message.edit_text(
                '❌ <b>没有活跃的推荐竞赛</b>\n\n在“竞赛”部分创建竞赛以进行同步。',
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [types.InlineKeyboardButton(text='⬅️走向诊断', callback_data='admin_referral_diagnostics')]
                    ]
                ),
            )
            return

        # Синхронизируем каждый конкурс
        total_created = 0
        total_updated = 0
        total_skipped = 0
        contest_results = []

        for contest in all_contests:
            stats = await referral_contest_service.sync_contest(db, contest.id)
            if 'error' not in stats:
                total_created += stats.get('created', 0)
                total_updated += stats.get('updated', 0)
                total_skipped += stats.get('skipped', 0)
                contest_results.append(f"• {html.escape(contest.title)}：+{stats.get('created', 0)} 新")
            else:
                contest_results.append(f'• {html.escape(contest.title)}：错误')

        text = f'🏆 <b>与比赛同步完成！</b>\n\n📊 <b> 结果：</b>\n• 已处理的比赛：{len(all_contests)}\n• 添加新事件：{total_created}\n• 更新：{total_updated}\n• 缺失（已可用）：{total_skipped}\n\n📋 <b>按比赛：</b>'
        text += '\n'.join(contest_results)

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='🔄 再次同步', callback_data='admin_ref_sync_contest')],
                [types.InlineKeyboardButton(text='⬅️走向诊断', callback_data='admin_referral_diagnostics')],
            ]
        )

        await callback.message.edit_text(text, reply_markup=keyboard)

    except Exception as e:
        logger.error('sync_referrals_with_contest 中出现错误', error=e, exc_info=True)
        await callback.answer('同步错误', show_alert=True)


@admin_required
@error_handler
async def request_log_file_upload(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    """Запрашивает загрузку лог-файла для анализа."""
    await state.set_state(AdminStates.waiting_for_log_file)

    text = '📤 <b>加载日志文件进行分析</b>\n\n提交您的日志文件（扩展名 .log 或 .txt）。\n\n将分析该文件以查找日志中记录的所有时间丢失的引用。\n\n⚠️<b>重要：</b>\n• 文件必须是文本（.log、.txt）\n• 最大尺寸：50 MB\n• 分析完成后，文件将被自动删除\n\n如果日志轮换已删除旧数据，请下载备份副本。'

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_referral_diagnostics')]]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def receive_log_file(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    """Получает и анализирует загруженный лог-файл."""
    import tempfile
    from pathlib import Path

    if not message.document:
        await message.answer(
            '❌ 请将文件作为文档发送。',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_referral_diagnostics')]
                ]
            ),
        )
        return

    # Проверяем расширение файла
    file_name = message.document.file_name or 'unknown'
    file_ext = Path(file_name).suffix.lower()

    if file_ext not in ['.log', '.txt']:
        await message.answer(
            f'❌ 无效文件格式：{html.escape(file_ext)}\n\n仅支持文本文件（.log、.txt）',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_referral_diagnostics')]
                ]
            ),
        )
        return

    # Проверяем размер файла
    max_size = 50 * 1024 * 1024  # 50 MB
    if message.document.file_size > max_size:
        await message.answer(
            f'❌ 文件太大：{message.document.file_size / 1024 / 1024:.1f} MB\n\n最大尺寸：50 MB',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_referral_diagnostics')]
                ]
            ),
        )
        return

    # Информируем о начале загрузки
    status_message = await message.answer(
        f'📥 正在上传文件 {html.escape(file_name)} ({message.document.file_size / 1024 / 1024:.1f} MB)...'
    )

    temp_file_path = None

    try:
        # Скачиваем файл во временную директорию
        temp_dir = tempfile.gettempdir()
        temp_file_path = str(Path(temp_dir) / f'ref_diagnostics_{message.from_user.id}_{file_name}')

        # Скачиваем файл
        file = await message.bot.get_file(message.document.file_id)
        await message.bot.download_file(file.file_path, temp_file_path)

        logger.info('📥 上传的文件：（字节）', temp_file_path=temp_file_path, file_size=message.document.file_size)

        # Обновляем статус
        await status_message.edit_text(
            f'🔍 正在分析文件 {html.escape(file_name)}...\n\n这可能需要一些时间。'
        )

        # Анализируем файл
        from app.services.referral_diagnostics_service import referral_diagnostics_service

        report = await referral_diagnostics_service.analyze_file(db, temp_file_path)

        # Формируем отчёт
        text = f'🔍 <b> 日志文件分析：{html.escape(file_name)}</b>\n\n<b>📊 转化统计：</b>\n• 推荐链接的总点击次数：{report.total_ref_clicks}\n• 唯一用户：{report.unique_users_clicked}\n• 丢失推荐：{len(report.lost_referrals)}\n• 文件中的行：{report.lines_in_period}'

        if report.lost_referrals:
            text += '<b>❌ 失去推荐：</b>'
            text += '<i>（通过链接获取，但未统计推荐人）</i>'

            for i, lost in enumerate(report.lost_referrals[:15], 1):
                # Статус пользователя
                if not lost.registered:
                    status = '⚠️ 不在数据库中'
                elif not lost.has_referrer:
                    status = '❌ 无推荐人'
                else:
                    status = f'⚡ 其他推荐人（ID{lost.current_referrer_id}）'

                # Имя или ID
                if lost.username:
                    user_name = f'@{html.escape(lost.username)}'
                elif lost.full_name:
                    user_name = html.escape(lost.full_name)
                else:
                    user_name = f'ID{lost.telegram_id}'

                # Ожидаемый реферер
                referrer_info = ''
                if lost.expected_referrer_name:
                    referrer_info = f' → {html.escape(lost.expected_referrer_name)}'
                elif lost.expected_referrer_id:
                    referrer_info = f' → ID{lost.expected_referrer_id}'

                # Время
                time_str = lost.click_time.strftime('%d.%m.%Y %H:%M')

                text += f'{i}. {user_name} — {status}\n'
                text += f'   <code>{html.escape(lost.referral_code)}</code>{referrer_info} ({time_str})\n'

            if len(report.lost_referrals) > 15:
                text += f'<i>...还有 {len(report.lost_referrals) - 15}</i>'
        else:
            text += '✅ <b>所有推荐均已计算！</b>'

        # Сохраняем отчёт в state для дальнейшего использования (сериализуем в dict)
        await state.update_data(
            diagnostics_period='uploaded_file',
            uploaded_file_report=report.to_dict(),
        )

        # Кнопки действий
        keyboard_rows = []

        if report.lost_referrals:
            keyboard_rows.append(
                [types.InlineKeyboardButton(text='📋 预览补丁', callback_data='admin_ref_fix_preview')]
            )

        keyboard_rows.extend(
            [
                [types.InlineKeyboardButton(text='⬅️走向诊断', callback_data='admin_referral_diagnostics')],
                [types.InlineKeyboardButton(text='⬅️ 统计', callback_data='admin_referrals')],
            ]
        )

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        # Удаляем статусное сообщение
        await status_message.delete()

        # Отправляем результат
        await message.answer(text, reply_markup=keyboard)

        # Очищаем состояние
        await state.set_state(AdminStates.referral_diagnostics_period)

    except Exception as e:
        logger.error('❌ 处理文件时出错', error=e, exc_info=True)

        try:
            await status_message.edit_text(
                f'❌ <b> 解析文件时出错</b>\n\n文件：{html.escape(file_name)}\n错误：{html.escape(str(e))}\n\n检查该文件是否是机器人的文本日志。',
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(
                                text='🔄再试一次', callback_data='admin_ref_diag_upload'
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text='⬅️走向诊断', callback_data='admin_referral_diagnostics'
                            )
                        ],
                    ]
                ),
            )
        except:
            await message.answer(
                f'❌ 解析文件时出错：{html.escape(str(e))}',
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_referral_diagnostics')]
                    ]
                ),
            )

    finally:
        # Удаляем временный файл
        if temp_file_path and await asyncio.to_thread(Path(temp_file_path).exists):
            try:
                await asyncio.to_thread(Path(temp_file_path).unlink)
                logger.info('🗑️ 临时文件已删除', temp_file_path=temp_file_path)
            except Exception as e:
                logger.error('删除临时文件时出错', error=e)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_referral_statistics, F.data == 'admin_referrals')
    dp.callback_query.register(show_top_referrers, F.data == 'admin_referrals_top')
    dp.callback_query.register(show_top_referrers_filtered, F.data.startswith('admin_top_ref:'))
    dp.callback_query.register(show_referral_settings, F.data == 'admin_referrals_settings')
    dp.callback_query.register(show_referral_diagnostics, F.data == 'admin_referral_diagnostics')
    dp.callback_query.register(show_referral_diagnostics, F.data.startswith('admin_ref_diag:'))
    dp.callback_query.register(preview_referral_fixes, F.data == 'admin_ref_fix_preview')
    dp.callback_query.register(apply_referral_fixes, F.data == 'admin_ref_fix_apply')

    # Загрузка лог-файла
    dp.callback_query.register(request_log_file_upload, F.data == 'admin_ref_diag_upload')
    dp.message.register(receive_log_file, AdminStates.waiting_for_log_file)

    # Проверка бонусов по БД
    dp.callback_query.register(check_missing_bonuses, F.data == 'admin_ref_check_bonuses')
    dp.callback_query.register(apply_missing_bonuses, F.data == 'admin_ref_bonus_apply')
    dp.callback_query.register(sync_referrals_with_contest, F.data == 'admin_ref_sync_contest')

    # Хендлеры заявок на вывод
    dp.callback_query.register(show_pending_withdrawal_requests, F.data == 'admin_withdrawal_requests')
    dp.callback_query.register(view_withdrawal_request, F.data.startswith('admin_withdrawal_view_'))
    dp.callback_query.register(approve_withdrawal_request, F.data.startswith('admin_withdrawal_approve_'))
    dp.callback_query.register(reject_withdrawal_request, F.data.startswith('admin_withdrawal_reject_'))
    dp.callback_query.register(complete_withdrawal_request, F.data.startswith('admin_withdrawal_complete_'))

    # Тестовое начисление
    dp.callback_query.register(start_test_referral_earning, F.data == 'admin_test_referral_earning')
    dp.message.register(process_test_referral_earning, AdminStates.test_referral_earning_input)



