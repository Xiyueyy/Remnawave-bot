import html

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.promo_group import get_promo_groups_with_counts
from app.database.crud.server_squad import (
    delete_server_squad,
    get_all_server_squads,
    get_available_server_squads,
    get_server_connected_users,
    get_server_squad_by_id,
    get_server_statistics,
    sync_with_remnawave,
    update_server_squad,
    update_server_squad_promo_groups,
)
from app.database.models import User
from app.services.remnawave_service import RemnaWaveService
from app.states import AdminStates
from app.utils.cache import cache
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


def _build_server_edit_view(server):
    status_emoji = '✅ Доступен' if server.is_available else '❌ Недоступен'
    price_text = f'{int(server.price_rubles)} ₽' if server.price_kopeks > 0 else 'Бесплатно'
    promo_groups_text = (
        ', '.join(sorted(pg.name for pg in server.allowed_promo_groups))
        if server.allowed_promo_groups
        else 'Не выбраны'
    )

    trial_status = '✅ Да' if server.is_trial_eligible else '⚪️ Нет'

    text = f"🌐 <b>编辑服务器</b>\n\n<b>信息：</b>\n• ID：{server.id}\n• UUID：<代码>{server.squad_uuid}</code>\n• 名称：{html.escape(server.display_name)}\n• 原件：{(html.escape(server.original_name) if server.original_name else 'Не указано')}\n• 状态：{status_emoji}\n\n<b>设置：</b>\n• 价格：{price_text}\n• 国家代码：{server.country_code or 'Не указан'}\n• 用户限制：{server.max_users or 'Без лимита'}\n• 当前用户：{server.current_users}\n• 促销组：{promo_groups_text}\n• 试发行：{trial_status}\n\n<b>描述：</b>\n{server.description or 'Не указано'}\n\n选择要更改的内容："

    keyboard = [
        [
            types.InlineKeyboardButton(text='✏️标题', callback_data=f'admin_server_edit_name_{server.id}'),
            types.InlineKeyboardButton(text='💰 价格', callback_data=f'admin_server_edit_price_{server.id}'),
        ],
        [
            types.InlineKeyboardButton(text='🌍国家', callback_data=f'admin_server_edit_country_{server.id}'),
            types.InlineKeyboardButton(text='👥 限制', callback_data=f'admin_server_edit_limit_{server.id}'),
        ],
        [
            types.InlineKeyboardButton(text='👥 用户', callback_data=f'admin_server_users_{server.id}'),
        ],
        [
            types.InlineKeyboardButton(
                text='🎁 Выдавать сквад' if not server.is_trial_eligible else '🚫 Не выдавать сквад',
                callback_data=f'admin_server_trial_{server.id}',
            ),
        ],
        [
            types.InlineKeyboardButton(text='🎯 促销组', callback_data=f'admin_server_edit_promo_{server.id}'),
            types.InlineKeyboardButton(text='📝 描述', callback_data=f'admin_server_edit_desc_{server.id}'),
        ],
        [
            types.InlineKeyboardButton(
                text='❌ Отключить' if server.is_available else '✅ Включить',
                callback_data=f'admin_server_toggle_{server.id}',
            )
        ],
        [
            types.InlineKeyboardButton(text='🗑️ 删除', callback_data=f'admin_server_delete_{server.id}'),
            types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_servers_list'),
        ],
    ]

    return text, types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def _build_server_promo_groups_keyboard(server_id: int, promo_groups, selected_ids):
    keyboard = []
    for group in promo_groups:
        emoji = '✅' if group['id'] in selected_ids else '⚪'
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=f'{emoji} {group["name"]}',
                    callback_data=f'admin_server_promo_toggle_{server_id}_{group["id"]}',
                )
            ]
        )

    keyboard.append(
        [types.InlineKeyboardButton(text='💾 保存', callback_data=f'admin_server_promo_save_{server_id}')]
    )
    keyboard.append([types.InlineKeyboardButton(text='⬅️ 返回', callback_data=f'admin_server_edit_{server_id}')])

    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


@admin_required
@error_handler
async def show_servers_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    stats = await get_server_statistics(db)

    text = f"🌐 <b>服务器管理</b>\n\n📊<b>统计：</b>\n• 服务器总数：{stats['total_servers']}\n• 可用：{stats['available_servers']}\n• 不可用：{stats['unavailable_servers']}\n• 有连接：{stats['servers_with_connections']}\n\n💰 <b>服务器收入：</b>\n• 一般：{int(stats['total_revenue_rubles'])} ₽\n\n选择动作："

    keyboard = [
        [
            types.InlineKeyboardButton(text='📋 服务器列表', callback_data='admin_servers_list'),
            types.InlineKeyboardButton(text='🔄 同步', callback_data='admin_servers_sync'),
        ],
        [
            types.InlineKeyboardButton(text='📊 同步计数器', callback_data='admin_servers_sync_counts'),
            types.InlineKeyboardButton(text='📈详细统计', callback_data='admin_servers_stats'),
        ],
        [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_panel')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_servers_list(callback: types.CallbackQuery, db_user: User, db: AsyncSession, page: int = 1):
    servers, total_count = await get_all_server_squads(db, page=page, limit=10)
    total_pages = (total_count + 9) // 10

    if not servers:
        text = '🌐 <b>服务器列表</b>\n\n❌ 未找到服务器。'
    else:
        text = '🌐 <b>服务器列表</b>'
        text += f'📊 总计：{total_count} |页码：{page}/QQQPH2QQQ'

        for i, server in enumerate(servers, 1 + (page - 1) * 10):
            status_emoji = '✅' if server.is_available else '❌'
            price_text = f'{int(server.price_rubles)} ₽' if server.price_kopeks > 0 else 'Бесплатно'

            text += f'{i}. {status_emoji} {html.escape(server.display_name)}\n'
            text += f'💰价格：{price_text}'

            if server.max_users:
                text += f' | 👥 {server.current_users}/{server.max_users}'

            text += f'\n   UUID: <code>{server.squad_uuid}</code>\n\n'

    keyboard = []

    for i, server in enumerate(servers):
        row_num = i // 2
        if len(keyboard) <= row_num:
            keyboard.append([])

        status_emoji = '✅' if server.is_available else '❌'
        keyboard[row_num].append(
            types.InlineKeyboardButton(
                text=f'{status_emoji} {server.display_name[:15]}...', callback_data=f'admin_server_edit_{server.id}'
            )
        )

    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(types.InlineKeyboardButton(text='⬅️', callback_data=f'admin_servers_list_page_{page - 1}'))

        nav_row.append(types.InlineKeyboardButton(text=f'{page}/{total_pages}', callback_data='current_page'))

        if page < total_pages:
            nav_row.append(types.InlineKeyboardButton(text='➡️', callback_data=f'admin_servers_list_page_{page + 1}'))

        keyboard.append(nav_row)

    keyboard.extend([[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_servers')]])

    await callback.message.edit_text(
        text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode='HTML'
    )
    await callback.answer()


@admin_required
@error_handler
async def sync_servers_with_remnawave(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.message.edit_text(
        '🔄 与 Remnawave 同步...\n\n请稍候，这可能需要一些时间。', reply_markup=None
    )

    try:
        remnawave_service = RemnaWaveService()
        squads = await remnawave_service.get_all_squads()

        if not squads:
            await callback.message.edit_text(
                '❌ 从 Remnawave 获取小队数据失败。\n\n检查 API 的设置。',
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_servers')]]
                ),
            )
            return

        created, updated, removed = await sync_with_remnawave(db, squads)

        await cache.delete_pattern('available_countries*')

        text = f'✅ <b>同步完成</b>\n\n📊 <b>结果：</b>\n• 创建新服务器：{created}\n• 更新了现有的：{updated}\n• 删除缺失项目：{removed}\n• 处理总量：{len(squads)}\n\nℹ️ 新服务器被创建为不可用。\n在服务器列表中配置它们。'

        keyboard = [
            [
                types.InlineKeyboardButton(text='📋 服务器列表', callback_data='admin_servers_list'),
                types.InlineKeyboardButton(text='🔄 重试', callback_data='admin_servers_sync'),
            ],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_servers')],
        ]

        await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))

    except Exception as e:
        logger.error('Ошибка синхронизации серверов', error=e)
        await callback.message.edit_text(
            f'❌ 同步错误：{e!s}',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_servers')]]
            ),
        )

    await callback.answer()


@admin_required
@error_handler
async def show_server_edit_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    text, keyboard = _build_server_edit_view(server)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()


@admin_required
@error_handler
async def show_server_users(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    payload = callback.data.split('admin_server_users_', 1)[-1]
    payload_parts = payload.split('_')

    server_id = int(payload_parts[0])
    page = int(payload_parts[1]) if len(payload_parts) > 1 else 1
    page = max(page, 1)
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    users = await get_server_connected_users(db, server_id)
    total_users = len(users)

    page_size = 10
    total_pages = max((total_users + page_size - 1) // page_size, 1)

    page = min(page, total_pages)

    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    page_users = users[start_index:end_index]

    safe_name = html.escape(server.display_name or '—')
    safe_uuid = html.escape(server.squad_uuid or '—')

    header = [
        '🌐 <b>Пользователи сервера</b>',
        '',
        f'• Сервер: {safe_name}',
        f'• UUID: <code>{safe_uuid}</code>',
        f'• Подключений: {total_users}',
    ]

    if total_pages > 1:
        header.append(f'• Страница: {page}/{total_pages}')

    header.append('')

    text = '\n'.join(header)

    def _get_status_icon(status_text: str) -> str:
        if not status_text:
            return ''

        parts = status_text.split(' ', 1)
        return parts[0] if parts else status_text

    if users:
        lines = []
        for index, user in enumerate(page_users, start=start_index + 1):
            safe_user_name = html.escape(user.full_name)
            if user.telegram_id:
                user_link = f'<a href="tg://user?id={user.telegram_id}">{safe_user_name}</a>'
            else:
                user_link = f'<b>{safe_user_name}</b>'
            lines.append(f'{index}. {user_link}')

        text += '\n' + '\n'.join(lines)
    else:
        text += '没有找到用户。'

    keyboard: list[list[types.InlineKeyboardButton]] = []

    for user in page_users:
        display_name = user.full_name
        if len(display_name) > 30:
            display_name = display_name[:27] + '...'

        if settings.is_multi_tariff_enabled() and hasattr(user, 'subscriptions') and user.subscriptions:
            status_parts = []
            for sub in user.subscriptions:
                emoji = '🟢' if sub.is_active else '🔴'
                name = sub.tariff.name if sub.tariff else f'#{sub.id}'
                status_parts.append(f'{emoji}{name}')
            subscription_status = ', '.join(status_parts)
        elif user.subscription:
            subscription_status = user.subscription.status_display
        else:
            subscription_status = '❌ Нет подписки'
        status_icon = _get_status_icon(subscription_status)

        if status_icon:
            button_text = f'{status_icon} {display_name}'
        else:
            button_text = display_name

        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=button_text,
                    callback_data=f'admin_user_manage_{user.id}',
                )
            ]
        )

    if total_pages > 1:
        navigation_buttons: list[types.InlineKeyboardButton] = []

        if page > 1:
            navigation_buttons.append(
                types.InlineKeyboardButton(
                    text='⬅️ 上一页',
                    callback_data=f'admin_server_users_{server_id}_{page - 1}',
                )
            )

        navigation_buttons.append(
            types.InlineKeyboardButton(
                text=f'页码 {page}QQPH2QQQ1QQQ',
                callback_data=f'admin_server_users_{server_id}_{page}',
            )
        )

        if page < total_pages:
            navigation_buttons.append(
                types.InlineKeyboardButton(
                    text='下一个➡️',
                    callback_data=f'admin_server_users_{server_id}_{page + 1}',
                )
            )

        keyboard.append(navigation_buttons)

    keyboard.append([types.InlineKeyboardButton(text='⬅️ 到服务器', callback_data=f'admin_server_edit_{server_id}')])

    keyboard.append([types.InlineKeyboardButton(text='⬅️ 前往列表', callback_data='admin_servers_list')])

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode='HTML',
    )

    await callback.answer()


@admin_required
@error_handler
async def toggle_server_availability(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    new_status = not server.is_available
    await update_server_squad(db, server_id, is_available=new_status)

    await cache.delete_pattern('available_countries*')

    status_text = 'включен' if new_status else 'отключен'
    await callback.answer(f'✅ 服务器{status_text}！')

    server = await get_server_squad_by_id(db, server_id)

    text, keyboard = _build_server_edit_view(server)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')


@admin_required
@error_handler
async def toggle_server_trial_assignment(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    new_status = not server.is_trial_eligible
    await update_server_squad(db, server_id, is_trial_eligible=new_status)

    status_text = 'будет выдаваться' if new_status else 'перестанет выдаваться'
    await callback.answer(f'✅ 小队{status_text} 试用中')

    server = await get_server_squad_by_id(db, server_id)

    text, keyboard = _build_server_edit_view(server)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')


@admin_required
@error_handler
async def start_server_edit_price(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_price)

    current_price = f'{int(server.price_rubles)} ₽' if server.price_kopeks > 0 else 'Бесплатно'

    await callback.message.edit_text(
        f'💰 <b>编辑价格</b>\n\n当前价格：<b>{current_price}</b>\n\n发送新的卢布价格（例如：15.50）或 0 以免费访问：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='❌ 取消', callback_data=f'admin_server_edit_{server_id}')]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_price_edit(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    data = await state.get_data()
    server_id = data.get('server_id')

    try:
        price_rubles = float(message.text.replace(',', '.'))

        if price_rubles < 0:
            await message.answer('❌ 价格不能为负数')
            return

        if price_rubles > 10000:
            await message.answer('❌价格太高（最高10,000₽）')
            return

        price_kopeks = int(price_rubles * 100)

        server = await update_server_squad(db, server_id, price_kopeks=price_kopeks)

        if server:
            await state.clear()

            await cache.delete_pattern('available_countries*')

            price_text = f'{int(price_rubles)} ₽' if price_kopeks > 0 else 'Бесплатно'
            await message.answer(
                f'✅ 服务器价格更改为：<b>{price_text}</b>',
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(
                                text='🔙 到服务器', callback_data=f'admin_server_edit_{server_id}'
                            )
                        ]
                    ]
                ),
                parse_mode='HTML',
            )
        else:
            await message.answer('❌ 更新服务器时出错')

    except ValueError:
        await message.answer('❌ 价格格式无效。使用数字（例如：15.50）')


@admin_required
@error_handler
async def start_server_edit_name(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_name)

    await callback.message.edit_text(
        f'✏️ <b>编辑标题</b>\n\n当前名称：<b>{html.escape(server.display_name)}</b>\n\n提交服务器的新名称：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='❌ 取消', callback_data=f'admin_server_edit_{server_id}')]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_name_edit(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    data = await state.get_data()
    server_id = data.get('server_id')

    new_name = message.text.strip()

    if len(new_name) > 255:
        await message.answer('❌ 标题太长（最多 255 个字符）')
        return

    if len(new_name) < 3:
        await message.answer('❌ 名称太短（最少 3 个字符）')
        return

    server = await update_server_squad(db, server_id, display_name=new_name)

    if server:
        await state.clear()

        await cache.delete_pattern('available_countries*')

        await message.answer(
            f'✅ 服务器名称更改为：<b>{new_name}</b>',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🔙 到服务器', callback_data=f'admin_server_edit_{server_id}')]
                ]
            ),
            parse_mode='HTML',
        )
    else:
        await message.answer('❌ 更新服务器时出错')


@admin_required
@error_handler
async def delete_server_confirm(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    text = f'🗑️ <b>删除服务器</b>\n\n您确实要删除服务器吗：\n<b>{html.escape(server.display_name)}</b>\n\n⚠️<b>注意！</b>\n仅当服务器没有活动连接时才能删除。\n\n此操作无法撤消！'

    keyboard = [
        [
            types.InlineKeyboardButton(text='🗑️是的，删除', callback_data=f'admin_server_delete_confirm_{server_id}'),
            types.InlineKeyboardButton(text='❌ 取消', callback_data=f'admin_server_edit_{server_id}'),
        ]
    ]

    await callback.message.edit_text(
        text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode='HTML'
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_server_execute(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    success = await delete_server_squad(db, server_id)

    if success:
        await cache.delete_pattern('available_countries*')

        await callback.message.edit_text(
            f'✅ 服务器<b>{html.escape(server.display_name)}</b>已成功删除！',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='📋 前往服务器列表', callback_data='admin_servers_list')]
                ]
            ),
            parse_mode='HTML',
        )
    else:
        await callback.message.edit_text(
            f'❌ 删除服务器<b>{html.escape(server.display_name)}</b>失败\n\n可能存在与其之间的活动连接。',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🔙 到服务器', callback_data=f'admin_server_edit_{server_id}')]
                ]
            ),
            parse_mode='HTML',
        )

    await callback.answer()


@admin_required
@error_handler
async def show_server_detailed_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    stats = await get_server_statistics(db)
    available_servers = await get_available_server_squads(db)

    text = f"📊 <b>详细的服务器统计</b>\n\n<b>🌐一般信息：</b>\n• 服务器总数：{stats['total_servers']}\n• 可用：{stats['available_servers']}\n• 不可用：{stats['unavailable_servers']}\n• 具有活动连接：{stats['servers_with_connections']}\n\n<b>💰财务统计：</b>\n• 总收入：{int(stats['total_revenue_rubles'])} ₽\n• 每台服务器的平均价格：{int(stats['total_revenue_rubles'] / max(stats['servers_with_connections'], 1))} ₽\n\n<b>🔥 按价格排名的顶级服务器：</b>"

    sorted_servers = sorted(available_servers, key=lambda x: x.price_kopeks, reverse=True)

    for i, server in enumerate(sorted_servers[:5], 1):
        price_text = f'{int(server.price_rubles)} ₽' if server.price_kopeks > 0 else 'Бесплатно'
        text += f'{i}. {html.escape(server.display_name)} - {price_text}\n'

    if not sorted_servers:
        text += '没有可用的服务器'

    keyboard = [
        [
            types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_servers_stats'),
            types.InlineKeyboardButton(text='📋 列表', callback_data='admin_servers_list'),
        ],
        [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_servers')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def start_server_edit_country(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_country)

    current_country = server.country_code or 'Не указан'

    await callback.message.edit_text(
        f'🌍 <b>编辑国家代码</b>\n\n当前国家/地区代码：<b>{current_country}</b>\n\n提交新的国家/地区代码（例如：RU、US、DE）或“-”以删除：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='❌ 取消', callback_data=f'admin_server_edit_{server_id}')]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_country_edit(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    data = await state.get_data()
    server_id = data.get('server_id')

    new_country = message.text.strip().upper()

    if new_country == '-':
        new_country = None
    elif len(new_country) > 5:
        await message.answer('❌ 国家/地区代码太长（最多 5 个字符）')
        return

    server = await update_server_squad(db, server_id, country_code=new_country)

    if server:
        await state.clear()

        await cache.delete_pattern('available_countries*')

        country_text = new_country or 'Удален'
        await message.answer(
            f'✅ 国家代码更改为：<b>{country_text}</b>',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🔙 到服务器', callback_data=f'admin_server_edit_{server_id}')]
                ]
            ),
            parse_mode='HTML',
        )
    else:
        await message.answer('❌ 更新服务器时出错')


@admin_required
@error_handler
async def start_server_edit_limit(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_limit)

    current_limit = server.max_users or 'Без лимита'

    await callback.message.edit_text(
        f'👥 <b>编辑用户限制</b>\n\n当前限制：<b>{current_limit}</b>\n\n提交新的用户限制（数量）或 0 以进行无限制访问：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='❌ 取消', callback_data=f'admin_server_edit_{server_id}')]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_limit_edit(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    data = await state.get_data()
    server_id = data.get('server_id')

    try:
        limit = int(message.text.strip())

        if limit < 0:
            await message.answer('❌ 限制不能为负数')
            return

        if limit > 10000:
            await message.answer('❌ 限制太高（最多 10,000）')
            return

        max_users = limit if limit > 0 else None

        server = await update_server_squad(db, server_id, max_users=max_users)

        if server:
            await state.clear()

            limit_text = f'{limit} пользователей' if limit > 0 else 'Без лимита'
            await message.answer(
                f'✅ 用户限制更改为：<b>{limit_text}</b>',
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(
                                text='🔙 到服务器', callback_data=f'admin_server_edit_{server_id}'
                            )
                        ]
                    ]
                ),
                parse_mode='HTML',
            )
        else:
            await message.answer('❌ 更新服务器时出错')

    except ValueError:
        await message.answer('❌ 数字格式无效。输入一个整数。')


@admin_required
@error_handler
async def start_server_edit_description(
    callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession
):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_description)

    current_desc = server.description or 'Не указано'

    await callback.message.edit_text(
        f'📝 <b>编辑说明</b>\n\n当前描述：\n<i>{current_desc}</i>\n\n提交新的服务器描述或“-”进行删除：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='❌ 取消', callback_data=f'admin_server_edit_{server_id}')]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_description_edit(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    data = await state.get_data()
    server_id = data.get('server_id')

    new_description = message.text.strip()

    if new_description == '-':
        new_description = None
    elif len(new_description) > 1000:
        await message.answer('❌ 描述太长（最多 1000 个字符）')
        return

    server = await update_server_squad(db, server_id, description=new_description)

    if server:
        await state.clear()

        desc_text = new_description or 'Удалено'
        await cache.delete_pattern('available_countries*')
        await message.answer(
            f'✅ 服务器描述已更改：\n\n<i>{desc_text}</i>',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🔙 到服务器', callback_data=f'admin_server_edit_{server_id}')]
                ]
            ),
            parse_mode='HTML',
        )
    else:
        await message.answer('❌ 更新服务器时出错')


@admin_required
@error_handler
async def start_server_edit_promo_groups(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)

    if not server:
        await callback.answer('❌ 找不到服务器！', show_alert=True)
        return

    promo_groups_data = await get_promo_groups_with_counts(db)
    promo_groups = [
        {'id': group.id, 'name': group.name, 'is_default': group.is_default} for group, _ in promo_groups_data
    ]

    if not promo_groups:
        await callback.answer('❌ 未找到促销组', show_alert=True)
        return

    selected_ids = {pg.id for pg in server.allowed_promo_groups}
    if not selected_ids:
        default_group = next((pg for pg in promo_groups if pg['is_default']), None)
        if default_group:
            selected_ids.add(default_group['id'])

    await state.set_state(AdminStates.editing_server_promo_groups)
    await state.set_data(
        {
            'server_id': server_id,
            'promo_groups': promo_groups,
            'selected_promo_groups': list(selected_ids),
            'server_name': server.display_name,
        }
    )

    text = (
        f'🎯 <b>建立促销群组</b>\n\n服务器：<b>{html.escape(server.display_name)}</b>\n\n选择有权访问此服务器的促销组。\n必须至少选择一个促销组。'
    )

    await callback.message.edit_text(
        text,
        reply_markup=_build_server_promo_groups_keyboard(server_id, promo_groups, selected_ids),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_server_promo_group(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split('_')
    server_id = int(parts[4])
    group_id = int(parts[5])

    data = await state.get_data()
    if not data or data.get('server_id') != server_id:
        await callback.answer('⚠️编辑会话已过时', show_alert=True)
        return

    selected = {int(pg_id) for pg_id in data.get('selected_promo_groups', [])}
    promo_groups = data.get('promo_groups', [])

    if group_id in selected:
        if len(selected) == 1:
            await callback.answer('⚠️ 您无法禁用最后一个促销组', show_alert=True)
            return
        selected.remove(group_id)
        message = '促销组已禁用'
    else:
        selected.add(group_id)
        message = '添加促销组'

    await state.update_data(selected_promo_groups=list(selected))

    await callback.message.edit_reply_markup(
        reply_markup=_build_server_promo_groups_keyboard(server_id, promo_groups, selected)
    )
    await callback.answer(message)


@admin_required
@error_handler
async def save_server_promo_groups(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession,
):
    data = await state.get_data()
    if not data:
        await callback.answer('⚠️没有数据可保存', show_alert=True)
        return

    server_id = data.get('server_id')
    selected = data.get('selected_promo_groups', [])

    if not selected:
        await callback.answer('❌ 至少选择一个促销组', show_alert=True)
        return

    try:
        server = await update_server_squad_promo_groups(db, server_id, selected)
    except ValueError as exc:
        await callback.answer(f'❌ {exc}', show_alert=True)
        return

    if not server:
        await callback.answer('❌ 找不到服务器', show_alert=True)
        return

    await cache.delete_pattern('available_countries*')
    await state.clear()

    text, keyboard = _build_server_edit_view(server)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer('✅ 促销组已更新！')


@admin_required
@error_handler
async def sync_server_user_counts_handler(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.message.edit_text('🔄 用户计数器同步...', reply_markup=None)

    try:
        from app.database.crud.server_squad import sync_server_user_counts

        updated_count = await sync_server_user_counts(db)

        text = f'✅ <b>同步完成</b>\n\n📊 <b>结果：</b>\n• 更新的服务器：{updated_count}\n\n用户计数器与真实数据同步。'

        keyboard = [
            [
                types.InlineKeyboardButton(text='📋 服务器列表', callback_data='admin_servers_list'),
                types.InlineKeyboardButton(text='🔄 重试', callback_data='admin_servers_sync_counts'),
            ],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_servers')],
        ]

        await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))

    except Exception as e:
        logger.error('Ошибка синхронизации счетчиков', error=e)
        await callback.message.edit_text(
            f'❌ 同步错误：{e!s}',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_servers')]]
            ),
        )

    await callback.answer()


@admin_required
@error_handler
async def handle_servers_pagination(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    page = int(callback.data.split('_')[-1])
    await show_servers_list(callback, db_user, db, page)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_servers_menu, F.data == 'admin_servers')
    dp.callback_query.register(show_servers_list, F.data == 'admin_servers_list')
    dp.callback_query.register(sync_servers_with_remnawave, F.data == 'admin_servers_sync')
    dp.callback_query.register(sync_server_user_counts_handler, F.data == 'admin_servers_sync_counts')
    dp.callback_query.register(show_server_detailed_stats, F.data == 'admin_servers_stats')

    dp.callback_query.register(
        show_server_edit_menu,
        F.data.startswith('admin_server_edit_')
        & ~F.data.contains('name')
        & ~F.data.contains('price')
        & ~F.data.contains('country')
        & ~F.data.contains('limit')
        & ~F.data.contains('desc')
        & ~F.data.contains('promo'),
    )
    dp.callback_query.register(toggle_server_availability, F.data.startswith('admin_server_toggle_'))
    dp.callback_query.register(toggle_server_trial_assignment, F.data.startswith('admin_server_trial_'))
    dp.callback_query.register(show_server_users, F.data.startswith('admin_server_users_'))

    dp.callback_query.register(start_server_edit_name, F.data.startswith('admin_server_edit_name_'))
    dp.callback_query.register(start_server_edit_price, F.data.startswith('admin_server_edit_price_'))
    dp.callback_query.register(start_server_edit_country, F.data.startswith('admin_server_edit_country_'))
    dp.callback_query.register(start_server_edit_promo_groups, F.data.startswith('admin_server_edit_promo_'))
    dp.callback_query.register(start_server_edit_limit, F.data.startswith('admin_server_edit_limit_'))
    dp.callback_query.register(start_server_edit_description, F.data.startswith('admin_server_edit_desc_'))

    dp.message.register(process_server_name_edit, AdminStates.editing_server_name)
    dp.message.register(process_server_price_edit, AdminStates.editing_server_price)
    dp.message.register(process_server_country_edit, AdminStates.editing_server_country)
    dp.message.register(process_server_limit_edit, AdminStates.editing_server_limit)
    dp.message.register(process_server_description_edit, AdminStates.editing_server_description)
    dp.callback_query.register(toggle_server_promo_group, F.data.startswith('admin_server_promo_toggle_'))
    dp.callback_query.register(save_server_promo_groups, F.data.startswith('admin_server_promo_save_'))

    dp.callback_query.register(
        delete_server_confirm, F.data.startswith('admin_server_delete_') & ~F.data.contains('confirm')
    )
    dp.callback_query.register(delete_server_execute, F.data.startswith('admin_server_delete_confirm_'))

    dp.callback_query.register(handle_servers_pagination, F.data.startswith('admin_servers_list_page_'))
