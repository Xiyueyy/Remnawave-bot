from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Dispatcher, F, types
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.referral import get_referral_statistics
from app.database.crud.subscription import get_subscriptions_statistics
from app.database.crud.transaction import get_revenue_by_period, get_transactions_statistics
from app.database.models import User
from app.keyboards.admin import get_admin_statistics_keyboard
from app.services.user_service import UserService
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_datetime, format_percentage


logger = structlog.get_logger(__name__)


@admin_required
@error_handler
async def show_statistics_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    text = '📊 <b>系统统计</b>\n\n选择一个部分来查看统计信息：'

    await callback.message.edit_text(text, reply_markup=get_admin_statistics_keyboard(db_user.language))
    await callback.answer()


@admin_required
@error_handler
async def show_users_statistics(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    user_service = UserService()
    stats = await user_service.get_user_statistics(db)

    total_users = stats['total_users']
    active_rate = format_percentage(stats['active_users'] / total_users * 100 if total_users > 0 else 0)

    current_time = format_datetime(datetime.now(UTC))

    text = f"👥 <b>用户统计</b>\n\n<b>一般指标：</b>\n- 注册总数：{stats['total_users']}\n- 活跃：{stats['active_users']} ({active_rate})\n- 已阻止：{stats['blocked_users']}\n\n<b>新注册：</b>\n- 今天：{stats['new_today']}\n- 一周：{stats['new_week']}\n- 一个月：{stats['new_month']}\n\n<b>活动：</b>\n- 活动系数：{active_rate}\n- 每月增长：+{stats['new_month']} ({format_percentage(stats['new_month'] / total_users * 100 if total_users > 0 else 0)})\n\n<b>更新：</b> {current_time}"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_stats_users')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_statistics')],
        ]
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception as e:
        if 'message is not modified' in str(e):
            await callback.answer('📊 数据是最新的', show_alert=False)
        else:
            logger.error('Ошибка обновления статистики пользователей', error=e)
            await callback.answer('❌ 数据更新错误', show_alert=True)
            return

    await callback.answer('✅ 统计数据已更新')


@admin_required
@error_handler
async def show_subscriptions_statistics(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    stats = await get_subscriptions_statistics(db)

    total_subs = stats['total_subscriptions']
    conversion_rate = format_percentage(stats['paid_subscriptions'] / total_subs * 100 if total_subs > 0 else 0)
    current_time = format_datetime(datetime.now(UTC))

    text = f"📱 <b>订阅统计</b>\n\n<b>一般指标：</b>\n- 总订阅数：{stats['total_subscriptions']}\n- 活跃：{stats['active_subscriptions']}\n- 付费：{stats['paid_subscriptions']}\n- 试用版：{stats['trial_subscriptions']}\n\n<b>转换：</b>\n- 从试用到付费：{conversion_rate}\n- 有效付费：{stats['paid_subscriptions']}\n\n<b>销售：</b>\n- 今天：{stats['purchased_today']}\n- 一周：{stats['purchased_week']}\n- 一个月：{stats['purchased_month']}\n\n<b>更新：</b> {current_time}"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_stats_subs')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_statistics')],
        ]
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer('✅ 统计数据已更新')
    except Exception as e:
        if 'message is not modified' in str(e):
            await callback.answer('📊 数据是最新的', show_alert=False)
        else:
            logger.error('Ошибка обновления статистики подписок', error=e)
            await callback.answer('❌ 数据更新错误', show_alert=True)


@admin_required
@error_handler
async def show_revenue_statistics(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_stats = await get_transactions_statistics(db, month_start, now)
    all_time_stats = await get_transactions_statistics(db, start_date=datetime(2020, 1, 1, tzinfo=UTC), end_date=now)
    current_time = format_datetime(datetime.now(UTC))

    text = f"💰<b>收入统计</b>\n\n<b>当月：</b>\n- 收入：{settings.format_price(month_stats['totals']['income_kopeks'])}\n- 费用：{settings.format_price(month_stats['totals']['expenses_kopeks'])}\n- 利润：{settings.format_price(month_stats['totals']['profit_kopeks'])}\n- 来自订阅：{settings.format_price(abs(month_stats['totals']['subscription_income_kopeks']))}\n\n<b>今天：</b>\n- 交易：{month_stats['today']['transactions_count']}\n- 收入：{settings.format_price(month_stats['today']['income_kopeks'])}\n\n<b>所有时间：</b>\n- 总收入：{settings.format_price(all_time_stats['totals']['income_kopeks'])}\n- 利润总额：{settings.format_price(all_time_stats['totals']['profit_kopeks'])}\n\n<b>付款方式：</b>"

    for method, data in month_stats['by_payment_method'].items():
        if method and data['count'] > 0:
            text += f'• {method}: {data["count"]} ({settings.format_price(data["amount"])})\n'

    text += f'<b>更新：</b> {current_time}'

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            # [types.InlineKeyboardButton(text="📈 Период", callback_data="admin_revenue_period")],
            [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_stats_revenue')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_statistics')],
        ]
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer('✅ 统计数据已更新')
    except Exception as e:
        if 'message is not modified' in str(e):
            await callback.answer('📊 数据是最新的', show_alert=False)
        else:
            logger.error('Ошибка обновления статистики доходов', error=e)
            await callback.answer('❌ 数据更新错误', show_alert=True)


@admin_required
@error_handler
async def show_referral_statistics(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    stats = await get_referral_statistics(db)
    current_time = format_datetime(datetime.now(UTC))

    avg_per_referrer = 0
    if stats['active_referrers'] > 0:
        avg_per_referrer = stats['total_paid_kopeks'] / stats['active_referrers']

    text = f"🤝 <b>推荐统计</b>\n\n<b>一般指标：</b>\n- 有推荐的用户：{stats['users_with_referrals']}\n- 活跃推荐人：{stats['active_referrers']}\n- 总付费：{settings.format_price(stats['total_paid_kopeks'])}\n\n<b>期间：</b>\n- 今天：{settings.format_price(stats['today_earnings_kopeks'])}\n- 一周：{settings.format_price(stats['week_earnings_kopeks'])}\n- 一个月：{settings.format_price(stats['month_earnings_kopeks'])}\n\n<b>平均指标：</b>\n- 一位推荐人：{settings.format_price(int(avg_per_referrer))}\n\n<b>热门引荐来源：</b>"

    if stats['top_referrers']:
        for i, referrer in enumerate(stats['top_referrers'][:5], 1):
            name = referrer['display_name']
            earned = settings.format_price(referrer['total_earned_kopeks'])
            count = referrer['referrals_count']
            text += f'{i}。 {name}：{earned}（{count} 参考号）'
    else:
        text += '还没有有效的推荐人'

    text += f'<b>更新：</b> {current_time}'

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_stats_referrals')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_statistics')],
        ]
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer('✅ 统计数据已更新')
    except Exception as e:
        if 'message is not modified' in str(e):
            await callback.answer('📊 数据是最新的', show_alert=False)
        else:
            logger.error('Ошибка обновления реферальной статистики', error=e)
            await callback.answer('❌ 数据更新错误', show_alert=True)


@admin_required
@error_handler
async def show_summary_statistics(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    user_service = UserService()
    user_stats = await user_service.get_user_statistics(db)
    sub_stats = await get_subscriptions_statistics(db)

    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    revenue_stats = await get_transactions_statistics(db, month_start, now)
    current_time = format_datetime(datetime.now(UTC))

    conversion_rate = 0
    if user_stats['total_users'] > 0:
        conversion_rate = sub_stats['paid_subscriptions'] / user_stats['total_users'] * 100

    arpu = 0
    if user_stats['active_users'] > 0:
        arpu = revenue_stats['totals']['income_kopeks'] / user_stats['active_users']

    text = f"📊 <b>系统概要</b>\n\n<b>用户：</b>\n- 总计：{user_stats['total_users']}\n- 活跃：{user_stats['active_users']}\n- 本月新内容：{user_stats['new_month']}\n\n<b>订阅：</b>\n- 活跃：{sub_stats['active_subscriptions']}\n- 付费：{sub_stats['paid_subscriptions']}\n- 转换：{format_percentage(conversion_rate)}\n\n<b>财务（月）：</b>\n- 收入：{settings.format_price(revenue_stats['totals']['income_kopeks'])}\n- ARPU：{settings.format_price(int(arpu))}\n- 交易：{sum((data['count'] for data in revenue_stats['by_type'].values()))}\n\n<b>身高：</b>\n- 用户：+{user_stats['new_month']} 每月\n- 销售额：+{sub_stats['purchased_month']} 每月\n\n<b>更新：</b> {current_time}"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_stats_summary')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_statistics')],
        ]
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer('✅ 统计数据已更新')
    except Exception as e:
        if 'message is not modified' in str(e):
            await callback.answer('📊 数据是最新的', show_alert=False)
        else:
            logger.error('Ошибка обновления общей статистики', error=e)
            await callback.answer('❌ 数据更新错误', show_alert=True)


@admin_required
@error_handler
async def show_revenue_by_period(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    period = callback.data.split('_')[-1]

    period_map = {'today': 1, 'yesterday': 1, 'week': 7, 'month': 30, 'all': 365}

    days = period_map.get(period, 30)
    revenue_data = await get_revenue_by_period(db, days)

    if period == 'yesterday':
        yesterday = datetime.now(UTC).date() - timedelta(days=1)
        revenue_data = [r for r in revenue_data if r['date'] == yesterday]
    elif period == 'today':
        today = datetime.now(UTC).date()
        revenue_data = [r for r in revenue_data if r['date'] == today]

    total_revenue = sum(r['amount_kopeks'] for r in revenue_data)
    avg_daily = total_revenue / len(revenue_data) if revenue_data else 0

    text = f'📈 <b>期间收入：{period}</b>\n\n<b>摘要：</b>\n- 总收入：{settings.format_price(total_revenue)}\n- 有数据的天数：{len(revenue_data)}\n- 每日平均收入：{settings.format_price(int(avg_daily))}\n\n<b>按天：</b>'

    for revenue in revenue_data[-10:]:
        text += f'• {revenue["date"].strftime("%d.%m")}: {settings.format_price(revenue["amount_kopeks"])}\n'

    if len(revenue_data) > 10:
        text += f'……还有一个 {len(revenue_data) - 10} 天'

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='📊又一个时期', callback_data='admin_revenue_period')],
                [types.InlineKeyboardButton(text='⬅️ 收入', callback_data='admin_stats_revenue')],
            ]
        ),
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_statistics_menu, F.data == 'admin_statistics')
    dp.callback_query.register(show_users_statistics, F.data == 'admin_stats_users')
    dp.callback_query.register(show_subscriptions_statistics, F.data == 'admin_stats_subs')
    dp.callback_query.register(show_revenue_statistics, F.data == 'admin_stats_revenue')
    dp.callback_query.register(show_referral_statistics, F.data == 'admin_stats_referrals')
    dp.callback_query.register(show_summary_statistics, F.data == 'admin_stats_summary')
    dp.callback_query.register(show_revenue_by_period, F.data.startswith('period_'))

    periods = ['today', 'yesterday', 'week', 'month', 'all']
    for period in periods:
        dp.callback_query.register(show_revenue_by_period, F.data == f'period_{period}')
