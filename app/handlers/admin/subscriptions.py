import structlog
from aiogram import Dispatcher, F, types
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.subscription import (
    get_all_subscriptions,
    get_expired_subscriptions,
    get_expiring_subscriptions,
    get_subscriptions_statistics,
)
from app.database.models import User
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_datetime


def get_country_flag(country_name: str) -> str:
    flags = {
        'USA': '🇺🇸',
        'United States': '🇺🇸',
        'US': '🇺🇸',
        'Germany': '🇩🇪',
        'DE': '🇩🇪',
        'Deutschland': '🇩🇪',
        'Netherlands': '🇳🇱',
        'NL': '🇳🇱',
        'Holland': '🇳🇱',
        'United Kingdom': '🇬🇧',
        'UK': '🇬🇧',
        'GB': '🇬🇧',
        'Japan': '🇯🇵',
        'JP': '🇯🇵',
        'France': '🇫🇷',
        'FR': '🇫🇷',
        'Canada': '🇨🇦',
        'CA': '🇨🇦',
        'Russia': '🇷🇺',
        'RU': '🇷🇺',
        'Singapore': '🇸🇬',
        'SG': '🇸🇬',
    }
    return flags.get(country_name, '🌍')


async def get_users_by_countries(db: AsyncSession) -> dict:
    try:
        result = await db.execute(
            select(User.preferred_location, func.count(User.id))
            .where(User.preferred_location.isnot(None))
            .group_by(User.preferred_location)
        )

        stats = {}
        for location, count in result.fetchall():
            if location:
                stats[location] = count

        return stats
    except Exception as e:
        logger.error('Ошибка получения статистики по странам', error=e)
        return {}


logger = structlog.get_logger(__name__)


@admin_required
@error_handler
async def show_subscriptions_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    stats = await get_subscriptions_statistics(db)

    text = f"📱 <b>订阅管理</b>\n\n📊<b>统计：</b>\n- 总计：{stats['total_subscriptions']}\n- 活跃：{stats['active_subscriptions']}\n- 付费：{stats['paid_subscriptions']}\n- 试用版：{stats['trial_subscriptions']}\n\n📈 <b>销售：</b>\n- 今天：{stats['purchased_today']}\n- 一周：{stats['purchased_week']}\n- 一个月：{stats['purchased_month']}\n\n选择动作："

    keyboard = [
        [
            types.InlineKeyboardButton(text='📋 订阅列表', callback_data='admin_subs_list'),
            types.InlineKeyboardButton(text='⏰ 即将过期', callback_data='admin_subs_expiring'),
        ],
        [
            types.InlineKeyboardButton(text='📊 统计', callback_data='admin_subs_stats'),
            types.InlineKeyboardButton(text='🌍地理', callback_data='admin_subs_countries'),
        ],
        [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_panel')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_subscriptions_list(callback: types.CallbackQuery, db_user: User, db: AsyncSession, page: int = 1):
    subscriptions, total_count = await get_all_subscriptions(db, page=page, limit=10)
    total_pages = (total_count + 9) // 10

    if not subscriptions:
        text = '📱 <b>订阅列表</b>\n\n❌ 未找到订阅。'
    else:
        text = '📱 <b>订阅列表</b>'
        text += f'📊 总计：{total_count} |页码：{page}/QQQPH2QQQ'

        for i, sub in enumerate(subscriptions, 1 + (page - 1) * 10):
            user_info = (
                (f'ID{sub.user.telegram_id}' if sub.user.telegram_id else sub.user.email or f'#{sub.user.id}')
                if sub.user
                else 'Неизвестно'
            )
            sub_type = '🎁' if sub.is_trial else '💎'
            status = '✅ Активна' if sub.is_active else '❌ Неактивна'

            text += f'{i}. {sub_type} {user_info}\n'
            text += f'{status} |之前：{format_datetime(sub.end_date)}'
            if sub.device_limit > 0:
                text += f'📱 设备：{sub.device_limit}'
            text += '\n'

    keyboard = []

    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(types.InlineKeyboardButton(text='⬅️', callback_data=f'admin_subs_list_page_{page - 1}'))

        nav_row.append(types.InlineKeyboardButton(text=f'{page}/{total_pages}', callback_data='current_page'))

        if page < total_pages:
            nav_row.append(types.InlineKeyboardButton(text='➡️', callback_data=f'admin_subs_list_page_{page + 1}'))

        keyboard.append(nav_row)

    keyboard.extend(
        [
            [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_subs_list')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_subscriptions')],
        ]
    )

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_expiring_subscriptions(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    expiring_3d = await get_expiring_subscriptions(db, 3)
    expiring_1d = await get_expiring_subscriptions(db, 1)
    expired = await get_expired_subscriptions(db)

    text = f'⏰ <b>订阅即将到期</b>\n\n📊<b>统计：</b>\n- 3 天后过期：{len(expiring_3d)}\n- 明天到期：{len(expiring_1d)}\n- 已过期：{len(expired)}\n\n<b>3 天后过期：</b>'

    for sub in expiring_3d[:5]:
        user_info = (
            (f'ID{sub.user.telegram_id}' if sub.user.telegram_id else sub.user.email or f'#{sub.user.id}')
            if sub.user
            else 'Неизвестно'
        )
        sub_type = '🎁' if sub.is_trial else '💎'
        text += f'{sub_type} {user_info} - {format_datetime(sub.end_date)}\n'

    if len(expiring_3d) > 5:
        text += f'...还有 {len(expiring_3d) - 5}'

    text += '<b>明天到期：</b>'
    for sub in expiring_1d[:5]:
        user_info = (
            (f'ID{sub.user.telegram_id}' if sub.user.telegram_id else sub.user.email or f'#{sub.user.id}')
            if sub.user
            else 'Неизвестно'
        )
        sub_type = '🎁' if sub.is_trial else '💎'
        text += f'{sub_type} {user_info} - {format_datetime(sub.end_date)}\n'

    if len(expiring_1d) > 5:
        text += f'...还有 {len(expiring_1d) - 5}'

    keyboard = [
        [types.InlineKeyboardButton(text='📨 发送提醒', callback_data='admin_send_expiry_reminders')],
        [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_subs_expiring')],
        [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_subscriptions')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_subscriptions_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    stats = await get_subscriptions_statistics(db)

    expiring_3d = await get_expiring_subscriptions(db, 3)
    expiring_7d = await get_expiring_subscriptions(db, 7)
    expired = await get_expired_subscriptions(db)

    text = f"📊 <b>订阅详细统计</b>\n\n<b>📱一般信息：</b>\n• 总订阅数：{stats['total_subscriptions']}\n• 活跃：{stats['active_subscriptions']}\n• 不活动：{stats['total_subscriptions'] - stats['active_subscriptions']}\n\n<b>💎 按类型：</b>\n• 付费：{stats['paid_subscriptions']}\n• 试用版：{stats['trial_subscriptions']}\n\n<b>📈销售：</b>\n• 今天：{stats['purchased_today']}\n• 一周：{stats['purchased_week']}\n• 当月：{stats['purchased_month']}\n\n<b>⏰ 过期时间：</b>\n• 3 天后过期：{len(expiring_3d)}\n• 7 天后过期：{len(expiring_7d)}\n• 已过期：{len(expired)}\n\n<b>💰转换：</b>\n• 从试用到付费：{stats.get('trial_to_paid_conversion', 0)}%\n• 续订：{stats.get('renewals_count', 0)}"

    keyboard = [
        # [
        #     types.InlineKeyboardButton(text="📊 Экспорт данных", callback_data="admin_subs_export"),
        #     types.InlineKeyboardButton(text="📈 Графики", callback_data="admin_subs_charts")
        # ],
        # [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_subs_stats")],
        [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_subscriptions')]
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_countries_management(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        from app.services.remnawave_service import RemnaWaveService

        remnawave_service = RemnaWaveService()

        nodes_data = await remnawave_service.get_all_nodes()
        squads_data = await remnawave_service.get_all_squads()

        lines = ['🌍 <b>国家管理</b>', '']

        if nodes_data:
            lines.append('<b>可用服务器：</b>')
            countries = {}

            for node in nodes_data:
                country_code = node.get('country_code', 'XX')
                country_name = country_code

                if country_name not in countries:
                    countries[country_name] = []
                countries[country_name].append(node)

            for country in sorted(countries):
                nodes = countries[country]
                active_nodes = len([n for n in nodes if n.get('is_connected') and n.get('is_node_online')])
                total_nodes = len(nodes)

                country_flag = get_country_flag(country)
                total_users_online = sum(n.get('users_online', 0) or 0 for n in nodes)
                line = (
                    f'• {country_flag} {country}：'
                    f'{active_nodes}/{total_nodes} 台服务器'
                    f'，在线用户：{total_users_online}'
                )
                lines.append(line)
        else:
            lines.append('❌ 加载服务器数据失败')

        if squads_data:
            total_members = sum(squad.get('members_count', 0) for squad in squads_data)
            lines.extend(
                [
                    '',
                    f'<b>小队总数：</b> {len(squads_data)}',
                    f'<b>小队成员总数：</b> {total_members}',
                    '',
                    '<b>小队列表：</b>',
                ]
            )
            for squad in squads_data[:5]:
                name = squad.get('name', '未知')
                members = squad.get('members_count', 0)
                inbounds = squad.get('inbounds_count', 0)
                lines.append(f'• {name}：{members} 名成员，{inbounds} 个入站')

            if len(squads_data) > 5:
                lines.append(f'…以及另外 {len(squads_data) - 5} 个小队')

        user_stats = await get_users_by_countries(db)
        if user_stats:
            lines.extend(['', '<b>按地区划分的用户：</b>'])
            for country in sorted(user_stats):
                count = user_stats[country]
                country_flag = get_country_flag(country)
                lines.append(f'• {country_flag} {country}：{count} 用户')

        text = '\n'.join(lines)

    except Exception as e:
        logger.error('Ошибка получения данных о странах', error=e)
        text = f'🌍<b>国家管理</b>\n\n❌ <b>加载数据时出错</b>\n获取服务器信息失败。\n\n检查与 RemnaWave API 的连接。\n\n<b>错误详细信息：</b> {e!s}'

    keyboard = [
        [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_subs_countries')],
        [
            types.InlineKeyboardButton(text='📊 节点统计', callback_data='admin_rw_nodes'),
            types.InlineKeyboardButton(text='🔧 小队', callback_data='admin_rw_squads'),
        ],
        [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_subscriptions')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def send_expiry_reminders(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.message.edit_text(
        '📨 发送提醒...\n\n请稍候，这可能需要一些时间。', reply_markup=None
    )

    expiring_subs = await get_expiring_subscriptions(db, 1)
    sent_count = 0

    for subscription in expiring_subs:
        if subscription.user:
            try:
                user = subscription.user
                # Skip email-only users (no telegram_id)
                if not user.telegram_id:
                    logger.debug('Пропуск email-пользователя при отправке напоминания', user_id=user.id)
                    continue

                days_left = max(1, subscription.days_left)

                tariff_label = ''
                if settings.is_multi_tariff_enabled() and hasattr(subscription, 'tariff') and subscription.tariff:
                    tariff_label = f' «{subscription.tariff.name}»'
                reminder_text = f"""
⚠️ <b>Подписка{tariff_label} истекает!</b>

Ваша подписка истекает через {days_left} день(а).

Не забудьте продлить подписку, чтобы не потерять доступ к серверам.

💎 Продлить подписку можно в главном меню.
"""

                await callback.bot.send_message(chat_id=user.telegram_id, text=reminder_text)
                sent_count += 1

            except Exception as e:
                logger.error('Ошибка отправки напоминания пользователю', user_id=subscription.user_id, error=e)

    await callback.message.edit_text(
        f'✅ 已发送提醒：{sent_count} 来自 {len(expiring_subs)}',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_subs_expiring')]]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_subscriptions_pagination(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    page = int(callback.data.split('_')[-1])
    await show_subscriptions_list(callback, db_user, db, page)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_subscriptions_menu, F.data == 'admin_subscriptions')
    dp.callback_query.register(show_subscriptions_list, F.data == 'admin_subs_list')
    dp.callback_query.register(show_expiring_subscriptions, F.data == 'admin_subs_expiring')
    dp.callback_query.register(show_subscriptions_stats, F.data == 'admin_subs_stats')
    dp.callback_query.register(show_countries_management, F.data == 'admin_subs_countries')
    dp.callback_query.register(send_expiry_reminders, F.data == 'admin_send_expiry_reminders')

    dp.callback_query.register(handle_subscriptions_pagination, F.data.startswith('admin_subs_list_page_'))
