"""Admin handler for managing required channel subscriptions."""

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.database.crud.required_channel import (
    add_channel,
    delete_channel,
    get_all_channels,
    get_channel_by_id,
    toggle_channel,
    validate_channel_id,
)
from app.database.database import AsyncSessionLocal
from app.services.channel_subscription_service import channel_subscription_service
from app.utils.decorators import admin_required


logger = structlog.get_logger(__name__)

router = Router(name='admin_required_channels')


class AddChannelStates(StatesGroup):
    waiting_channel_id = State()
    waiting_channel_link = State()
    waiting_channel_title = State()


# -- List channels ----------------------------------------------------------------


def _channels_keyboard(channels: list) -> InlineKeyboardMarkup:
    buttons = []
    for ch in channels:
        status = '✅' if ch.is_active else '❌'
        title = ch.title or ch.channel_id
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f'{status} {title}',
                    callback_data=f'reqch:view:{ch.id}',
                )
            ]
        )
    buttons.append([InlineKeyboardButton(text='➕ 添加频道', callback_data='reqch:add')])
    buttons.append([InlineKeyboardButton(text='◀️ 返回', callback_data='admin_submenu_settings')])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _channel_detail_keyboard(channel_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = '❌ 禁用' if is_active else '✅ 启用'
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=f'reqch:toggle:{channel_id}')],
            [InlineKeyboardButton(text='🗑 删除', callback_data=f'reqch:delete:{channel_id}')],
            [InlineKeyboardButton(text='◀️ 前往列表', callback_data='reqch:list')],
        ]
    )


@router.callback_query(F.data == 'reqch:list')
@admin_required
async def show_channels_list(callback: CallbackQuery, **kwargs) -> None:
    async with AsyncSessionLocal() as db:
        channels = await get_all_channels(db)

    if not channels:
        text = '<b>📢 所需频道</b>\n\n通道未配置。点击“添加”即可创建。'
    else:
        lines = ['<b>📢 所需频道</b>']
        for ch in channels:
            status = '✅' if ch.is_active else '❌'
            title = ch.title or ch.channel_id
            lines.append(f'{status} <code>{ch.channel_id}</code> — {title}')
        text = '\n'.join(lines)

    await callback.message.edit_text(text, reply_markup=_channels_keyboard(channels))
    await callback.answer()


@router.callback_query(F.data.startswith('reqch:view:'))
@admin_required
async def view_channel(callback: CallbackQuery, **kwargs) -> None:
    try:
        channel_db_id = int(callback.data.split(':')[2])
    except (ValueError, IndexError):
        await callback.answer('无效的 ID 频道', show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        ch = await get_channel_by_id(db, channel_db_id)

    if not ch:
        await callback.answer('找不到频道', show_alert=True)
        return

    status = '✅ 已启用' if ch.is_active else '❌ 已禁用'
    title = ch.title or '未命名频道'
    link = ch.channel_link or '—'
    text = (
        f'<b>{title}</b>\n\n'
        f'<b>ID：</b> <code>{ch.channel_id}</code>\n'
        f'<b>链接：</b> {link}\n'
        f'<b>状态：</b> {status}\n'
        f'<b>顺序：</b> {ch.sort_order}'
    )

    await callback.message.edit_text(text, reply_markup=_channel_detail_keyboard(ch.id, ch.is_active))
    await callback.answer()


# -- Toggle / Delete ---------------------------------------------------------------


@router.callback_query(F.data.startswith('reqch:toggle:'))
@admin_required
async def toggle_channel_handler(callback: CallbackQuery, **kwargs) -> None:
    try:
        channel_db_id = int(callback.data.split(':')[2])
    except (ValueError, IndexError):
        await callback.answer('无效的 ID 频道', show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        ch = await toggle_channel(db, channel_db_id)

    if ch:
        await channel_subscription_service.invalidate_channels_cache()
        status = '已启用' if ch.is_active else '已禁用'
        await callback.answer(f'频道已{status}', show_alert=True)

    # Refresh list
    async with AsyncSessionLocal() as db:
        channels = await get_all_channels(db)
    await callback.message.edit_text(
        '<b>📢 所需频道</b>',
        reply_markup=_channels_keyboard(channels),
    )


@router.callback_query(F.data.startswith('reqch:delete:'))
@admin_required
async def delete_channel_handler(callback: CallbackQuery, **kwargs) -> None:
    try:
        channel_db_id = int(callback.data.split(':')[2])
    except (ValueError, IndexError):
        await callback.answer('无效的 ID 频道', show_alert=True)
        return
    async with AsyncSessionLocal() as db:
        ok = await delete_channel(db, channel_db_id)

    if ok:
        await channel_subscription_service.invalidate_channels_cache()
        await callback.answer('频道已删除', show_alert=True)
    else:
        await callback.answer('卸载错误', show_alert=True)

    async with AsyncSessionLocal() as db:
        channels = await get_all_channels(db)
    await callback.message.edit_text(
        '<b>📢 所需频道</b>',
        reply_markup=_channels_keyboard(channels),
    )


# -- Add channel flow --------------------------------------------------------------


@router.callback_query(F.data == 'reqch:add')
@admin_required
async def start_add_channel(callback: CallbackQuery, state: FSMContext, **kwargs) -> None:
    await state.set_state(AddChannelStates.waiting_channel_id)
    await callback.message.edit_text(
        '<b>➕添加频道</b>\n\n发送频道数字 ID（例如 <code>1234567890</code>）。\n前缀 <code>-100</code> 会自动添加。'
    )
    await callback.answer()


@router.message(AddChannelStates.waiting_channel_id)
@admin_required
async def process_channel_id(message: Message, state: FSMContext, **kwargs) -> None:
    if not message.text:
        await message.answer('发送短信。')
        return
    channel_id = message.text.strip()

    # Validate and normalize channel_id (auto-prefixes -100 for bare digits)
    try:
        channel_id = validate_channel_id(channel_id)
    except ValueError as e:
        await message.answer(f'格式无效。{e}\n\n请重试：')
        return

    await state.update_data(channel_id=channel_id)
    await state.set_state(AddChannelStates.waiting_channel_link)
    await message.answer(
        f'频道：<code>{channel_id}</code>\n\n'
        '现在发送频道链接（例如 <code>https://t.me/mychannel</code>）\n'
        '或者发送 <code>-</code> 跳过：'
    )


@router.message(AddChannelStates.waiting_channel_link)
@admin_required
async def process_channel_link(message: Message, state: FSMContext, **kwargs) -> None:
    if not message.text:
        await message.answer('发送短信。')
        return
    link = message.text.strip()
    if link == '-':
        link = None

    if link is not None:
        # Validate and normalize channel link
        if not link.startswith(('https://t.me/', 'http://t.me/', '@')):
            await message.answer('该链接应为 URL，如 t.me 或 @username。再试一次：')
            return
        if link.startswith('@'):
            link = f'https://t.me/{link[1:]}'
        if link.startswith('http://'):
            link = link.replace('http://', 'https://', 1)

    await state.update_data(channel_link=link)
    await state.set_state(AddChannelStates.waiting_channel_title)
    await message.answer(
        '发送频道名称（例如<code>项目新闻</code>）\n或者发送 <code>-</code> 跳过：'
    )


@router.message(AddChannelStates.waiting_channel_title)
@admin_required
async def process_channel_title(message: Message, state: FSMContext, **kwargs) -> None:
    if not message.text:
        await message.answer('发送短信。')
        return
    title = message.text.strip()
    if title == '-':
        title = None

    data = await state.get_data()
    await state.clear()

    async with AsyncSessionLocal() as db:
        try:
            ch = await add_channel(
                db,
                channel_id=data['channel_id'],
                channel_link=data.get('channel_link'),
                title=title,
            )
            await channel_subscription_service.invalidate_channels_cache()

            text = (
                f'✅ 频道已添加！\n\n'
                f'<b>ID：</b> <code>{ch.channel_id}</code>\n'
                f'<b>链接：</b> {ch.channel_link or "—"}\n'
                f'<b>名称：</b> {ch.title or "未填写"}'
            )
        except Exception as e:
            text = '❌ 添加频道时出错。再试一次。'
            logger.error('Error adding channel', error=e)

    async with AsyncSessionLocal() as db:
        channels = await get_all_channels(db)

    await message.answer(text, reply_markup=_channels_keyboard(channels))


def register_handlers(dp_router: Router) -> None:
    dp_router.include_router(router)

