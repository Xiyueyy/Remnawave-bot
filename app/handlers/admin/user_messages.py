import structlog
from aiogram import Dispatcher, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.user_message import (
    create_user_message,
    delete_user_message,
    get_all_user_messages,
    get_user_message_by_id,
    get_user_messages_stats,
    toggle_user_message_status,
    update_user_message,
)
from app.database.models import User
from app.localization.texts import get_texts
from app.utils.decorators import admin_required, error_handler
from app.utils.validators import (
    get_html_help_text,
    sanitize_html,
    validate_html_tags,
)


logger = structlog.get_logger(__name__)


class UserMessageStates(StatesGroup):
    waiting_for_message_text = State()
    waiting_for_edit_text = State()


def get_user_messages_keyboard(language: str = 'ru'):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='📝 添加留言', callback_data='add_user_message')],
            [InlineKeyboardButton(text='📋 消息列表', callback_data='list_user_messages:0')],
            [InlineKeyboardButton(text='📊 统计', callback_data='user_messages_stats')],
            [InlineKeyboardButton(text='🔙返回管理面板', callback_data='admin_panel')],
        ]
    )


def get_message_actions_keyboard(message_id: int, is_active: bool, language: str = 'ru'):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    status_text = '🔴 Деактивировать' if is_active else '🟢 Активировать'

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='✏️编辑', callback_data=f'edit_user_message:{message_id}')],
            [InlineKeyboardButton(text=status_text, callback_data=f'toggle_user_message:{message_id}')],
            [InlineKeyboardButton(text='🗑️ 删除', callback_data=f'delete_user_message:{message_id}')],
            [InlineKeyboardButton(text='🔙 到列表', callback_data='list_user_messages:0')],
        ]
    )


@admin_required
@error_handler
async def show_user_messages_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    get_texts(db_user.language)

    text = (
        '📢 <b>在主菜单中管理消息</b>\n\n您可以在此处添加将在订阅信息和操作按钮之间的主菜单中向用户显示的消息。\n\n• 消息支持HTML 标签\n• 您可以创建多条消息\n• 活动消息随机显示\n• 不显示非活动消息'
    )

    await callback.message.edit_text(text, reply_markup=get_user_messages_keyboard(db_user.language), parse_mode='HTML')
    await callback.answer()


@admin_required
@error_handler
async def add_user_message_start(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    await callback.message.edit_text(
        f'📝 <b>添加新消息</b>\n\n输入将在主菜单中显示的消息文本。\n\n{get_html_help_text()}\n\n发短信 /cancel 取消。',
        parse_mode='HTML',
    )

    await state.set_state(UserMessageStates.waiting_for_message_text)
    await callback.answer()


@admin_required
@error_handler
async def process_new_message_text(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    if message.text == '/cancel':
        await state.clear()
        await message.answer(
            '❌ 添加消息已被取消。', reply_markup=get_user_messages_keyboard(db_user.language)
        )
        return

    message_text = message.text.strip()

    if len(message_text) > 4000:
        await message.answer(
            '❌ 消息太长。最多 4000 个字符。\n请重试或发送 /cancel 取消。'
        )
        return

    is_valid, error_msg = validate_html_tags(message_text)
    if not is_valid:
        await message.answer(
            f'❌ HTML 标记错误：{error_msg}\n\n更正错误并重试，或发送 /cancel 取消。',
            parse_mode=None,
        )
        return

    try:
        new_message = await create_user_message(db=db, message_text=message_text, created_by=db_user.id, is_active=True)

        await state.clear()

        await message.answer(
            f"✅ <b>消息已添加！</b>\n\n<b>ID:</b> {new_message.id}\n<b>状态：</b> {('🟢 Активно' if new_message.is_active else '🔴 Неактивно')}\n<b>创建者：</b> {new_message.created_at.strftime('%d.%m.%Y %H:%M')}\n\n<b>预览：</b>\n<块引用>{message_text}</blockquote>",
            reply_markup=get_user_messages_keyboard(db_user.language),
            parse_mode='HTML',
        )

    except Exception as e:
        logger.error('Ошибка создания сообщения', error=e)
        await state.clear()
        await message.answer(
            '❌ 创建消息时发生错误。再试一次。',
            reply_markup=get_user_messages_keyboard(db_user.language),
        )


@admin_required
@error_handler
async def list_user_messages(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    page = 0
    if ':' in callback.data:
        try:
            page = int(callback.data.split(':')[1])
        except (ValueError, IndexError):
            page = 0

    limit = 5
    offset = page * limit

    messages = await get_all_user_messages(db, offset=offset, limit=limit)

    if not messages:
        await callback.message.edit_text(
            '📋 <b>留言列表</b>\n\n还没有消息。添加您的第一条消息！',
            reply_markup=get_user_messages_keyboard(db_user.language),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    text = '📋 <b>留言列表</b>'

    for msg in messages:
        status_emoji = '🟢' if msg.is_active else '🔴'
        preview = msg.message_text[:100] + '...' if len(msg.message_text) > 100 else msg.message_text
        preview = preview.replace('<', '&lt;').replace('>', '&gt;')

        text += f'{status_emoji} <b>ID {msg.id}</b>\n{preview}\n📅 {msg.created_at.strftime("%d.%m.%Y %H:%M")}\n\n'

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = []

    for msg in messages:
        status_emoji = '🟢' if msg.is_active else '🔴'
        keyboard.append(
            [InlineKeyboardButton(text=f'{status_emoji} ID {msg.id}', callback_data=f'view_user_message:{msg.id}')]
        )

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text='⬅️ 返回', callback_data=f'list_user_messages:{page - 1}'))

    nav_buttons.append(InlineKeyboardButton(text='➕添加', callback_data='add_user_message'))

    if len(messages) == limit:
        nav_buttons.append(InlineKeyboardButton(text='转发➡️', callback_data=f'list_user_messages:{page + 1}'))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton(text='🔙 返回', callback_data='user_messages_panel')])

    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode='HTML'
    )
    await callback.answer()


@admin_required
@error_handler
async def view_user_message(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        message_id = int(callback.data.split(':')[1])
    except (ValueError, IndexError):
        await callback.answer('❌ 无效的 ID 消息', show_alert=True)
        return

    message = await get_user_message_by_id(db, message_id)

    if not message:
        await callback.answer('❌ 未找到消息', show_alert=True)
        return

    safe_content = sanitize_html(message.message_text)

    status_text = '🟢 Активно' if message.is_active else '🔴 Неактивно'

    text = (
        f"📋 <b>消息 ID {message.id}</b>\n\n<b>状态：</b> {status_text}\n<b>创建者：</b> {message.created_at.strftime('%d.%m.%Y %H:%M')}\n<b>更新：</b> {message.updated_at.strftime('%d.%m.%Y %H:%M')}\n\n<b>内容：</b>\n<块引用>{safe_content}</blockquote>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_message_actions_keyboard(message_id, message.is_active, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_message_status(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        message_id = int(callback.data.split(':')[1])
    except (ValueError, IndexError):
        await callback.answer('❌ 无效的 ID 消息', show_alert=True)
        return

    message = await toggle_user_message_status(db, message_id)

    if not message:
        await callback.answer('❌ 未找到消息', show_alert=True)
        return

    status_text = 'активировано' if message.is_active else 'деактивировано'
    await callback.answer(f'✅ 留言{status_text}')

    await view_user_message(callback, db_user, db)


@admin_required
@error_handler
async def delete_message_confirm(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Подтвердить удаление сообщения"""
    try:
        message_id = int(callback.data.split(':')[1])
    except (ValueError, IndexError):
        await callback.answer('❌ 无效的 ID 消息', show_alert=True)
        return

    success = await delete_user_message(db, message_id)

    if success:
        await callback.answer('✅ 留言已删除')
        await list_user_messages(
            types.CallbackQuery(
                id=callback.id,
                from_user=callback.from_user,
                chat_instance=callback.chat_instance,
                data='list_user_messages:0',
                message=callback.message,
            ),
            db_user,
            db,
        )
    else:
        await callback.answer('❌ 删除消息时出错', show_alert=True)


@admin_required
@error_handler
async def show_messages_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    stats = await get_user_messages_stats(db)

    text = (
        f"📊 <b>消息统计</b>\n\n📝 消息总数：<b>{stats['total_messages']}</b>\n🟢 活跃：<b>{stats['active_messages']}</b>\n🔴 不活动：<b>{stats['inactive_messages']}</b>\n\n活动消息在订阅信息和操作按钮之间的主菜单中随机向用户显示。"
    )

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='🔙 返回', callback_data='user_messages_panel')]]
    )

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()


@admin_required
@error_handler
async def edit_user_message_start(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    try:
        message_id = int(callback.data.split(':')[1])
    except (ValueError, IndexError):
        await callback.answer('❌ 无效的 ID 消息', show_alert=True)
        return

    message = await get_user_message_by_id(db, message_id)

    if not message:
        await callback.answer('❌ 未找到消息', show_alert=True)
        return

    await callback.message.edit_text(
        f'✏️ <b>编辑消息 ID {message.id}</b>\n\n<b>当前文本：</b>\n<块引用>{sanitize_html(message.message_text)}</blockquote>\n\n输入新消息或发送 /cancel 取消：',
        parse_mode='HTML',
    )

    await state.set_data({'editing_message_id': message_id})
    await state.set_state(UserMessageStates.waiting_for_edit_text)
    await callback.answer()


@admin_required
@error_handler
async def process_edit_message_text(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    if message.text == '/cancel':
        await state.clear()
        await message.answer('❌ 编辑已取消。', reply_markup=get_user_messages_keyboard(db_user.language))
        return

    data = await state.get_data()
    message_id = data.get('editing_message_id')

    if not message_id:
        await state.clear()
        await message.answer('❌ 错误：ID 消息未找到')
        return

    new_text = message.text.strip()

    if len(new_text) > 4000:
        await message.answer(
            '❌ 消息太长。最多 4000 个字符。\n请重试或发送 /cancel 取消。'
        )
        return

    is_valid, error_msg = validate_html_tags(new_text)
    if not is_valid:
        await message.answer(
            f'❌ HTML 标记错误：{error_msg}\n\n更正错误并重试，或发送 /cancel 取消。',
            parse_mode=None,
        )
        return

    try:
        updated_message = await update_user_message(db=db, message_id=message_id, message_text=new_text)

        if updated_message:
            await state.clear()
            await message.answer(
                f"✅ <b>消息已更新！</b>\n\n<b>ID:</b> {updated_message.id}\n<b>更新：</b> {updated_message.updated_at.strftime('%d.%m.%Y %H:%M')}\n\n<b>新文本：</b>\n<块引用>{sanitize_html(new_text)}</blockquote>",
                reply_markup=get_user_messages_keyboard(db_user.language),
                parse_mode='HTML',
            )
        else:
            await state.clear()
            await message.answer(
                '❌ 未找到消息或更新错误。',
                reply_markup=get_user_messages_keyboard(db_user.language),
            )

    except Exception as e:
        logger.error('Ошибка обновления сообщения', error=e)
        await state.clear()
        await message.answer(
            '❌ 更新消息时发生错误。', reply_markup=get_user_messages_keyboard(db_user.language)
        )


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_user_messages_panel, F.data == 'user_messages_panel')

    dp.callback_query.register(add_user_message_start, F.data == 'add_user_message')

    dp.message.register(process_new_message_text, StateFilter(UserMessageStates.waiting_for_message_text))

    dp.callback_query.register(edit_user_message_start, F.data.startswith('edit_user_message:'))

    dp.message.register(process_edit_message_text, StateFilter(UserMessageStates.waiting_for_edit_text))

    dp.callback_query.register(list_user_messages, F.data.startswith('list_user_messages'))

    dp.callback_query.register(view_user_message, F.data.startswith('view_user_message:'))

    dp.callback_query.register(toggle_message_status, F.data.startswith('toggle_user_message:'))

    dp.callback_query.register(delete_message_confirm, F.data.startswith('delete_user_message:'))

    dp.callback_query.register(show_messages_stats, F.data == 'user_messages_stats')
