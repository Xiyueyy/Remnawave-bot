"""
Хендлеры админ-панели для управления заблокированными пользователями.

Позволяет сканировать пользователей, выявлять тех, кто заблокировал бота,
и выполнять очистку БД и панели Remnawave.
"""

import html
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.blocked_users_service import (
    BlockCheckResult,
    BlockedUserAction,
    BlockedUsersService,
)
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


# =============================================================================
# Enums для текстов и callback_data
# =============================================================================


class BlockedUsersText(Enum):
    """Тексты для сообщений модуля заблокированных пользователей."""

    MENU_TITLE = '🔒 <b>检查已拉黑机器人的用户</b>'
    MENU_DESCRIPTION = (
        '\n\n你可以在这里检查哪些用户拉黑了机器人，'
        '并将他们从数据库和 Remnawave 面板中清理掉。\n\n'
        '<b>工作方式：</b>\n'
        '1. 扫描会向每位用户发送一次测试请求\n'
        '2. 如果用户拉黑了机器人，就会收到错误\n'
        '3. 你可以将这些用户从数据库和/或 Remnawave 中删除'
    )

    SCAN_STARTED = '🔄 <b>扫描已开始...</b>\n\n这可能需要几分钟。'
    SCAN_PROGRESS = '🔄 <b>扫描进度：</b> {checked}/{total} ({percent}%)'
    SCAN_COMPLETE = (
        '✅ <b>扫描完成</b>\n\n'
        '📊 <b>结果：</b>\n'
        '• 已检查：{total_checked}\n'
        '• 已拉黑机器人：{blocked_count}\n'
        '• 正常用户：{active_users}\n'
        '• 错误：{errors}\n'
        '• 无 Telegram ID：{skipped}\n\n'
        '⏱ 扫描耗时：{duration:.1f}秒'
    )
    SCAN_NO_BLOCKED = '✅ <b>很好！</b>\n\n未发现拉黑机器人的用户。'

    BLOCKED_LIST_TITLE = '🔒 <b>已拉黑用户</b> ({count})\n\n'
    BLOCKED_USER_ROW = '• {name} (ID: <code>{telegram_id}</code>)\n'

    CLEANUP_CONFIRM_TITLE = '⚠️ <b>确认操作</b>\n\n'
    CLEANUP_CONFIRM_DELETE_DB = (
        '你将要从 <b>数据库中删除</b> {count} 个用户。\n'
        '此操作无法撤销！\n\n'
        '将删除以下内容：\n'
        '• 用户资料\n'
        '• 订阅\n'
        '• 交易记录\n'
        '• 推荐数据'
    )
    CLEANUP_CONFIRM_DELETE_REMNAWAVE = (
        '你将要从 <b>Remnawave 中删除</b> {count} 个用户。\n他们的 VPN 访问将被完全禁用。'
    )
    CLEANUP_CONFIRM_DELETE_BOTH = (
        '你将要 <b>彻底删除</b> {count} 个用户：\n'
        '• 从机器人数据库\n'
        '• 从 Remnawave 面板\n\n'
        '此操作无法撤销！'
    )
    CLEANUP_CONFIRM_MARK = (
        '你将要把 {count} 个用户 <b>标记为已拉黑</b>。\n'
        '他们会保留在数据库中，但状态会被标记为 “blocked”。'
    )

    CLEANUP_PROGRESS = '🗑 <b>清理中：</b> {processed}/{total}'
    CLEANUP_COMPLETE = (
        '✅ <b>清理完成</b>\n\n'
        '📊 <b>结果：</b>\n'
        '• 已从数据库删除：{deleted_db}\n'
        '• 已从 Remnawave 删除：{deleted_remnawave}\n'
        '• 已标记为拉黑：{marked}\n'
        '• 错误：{errors}'
    )

    BUTTON_START_SCAN = '🔍 开始扫描'
    BUTTON_VIEW_BLOCKED = '👥 已封禁用户列表（{count}）'
    BUTTON_DELETE_DB = '🗑 从数据库删除'
    BUTTON_DELETE_REMNAWAVE = '🌐 从 Remnawave 删除'
    BUTTON_DELETE_BOTH = '💀 全部删除'
    BUTTON_MARK_BLOCKED = '🚫 标记为已封禁'
    BUTTON_CONFIRM = '✅ 确认'
    BUTTON_CANCEL = '❌ 取消'
    BUTTON_BACK = '⬅️ 返回'
    BUTTON_BACK_TO_USERS = '⬅️ 返回用户列表'


class BlockedUsersCallback(Enum):
    """Callback data для кнопок модуля."""

    MENU = 'admin_blocked_users'
    START_SCAN = 'admin_blocked_scan'
    VIEW_LIST = 'admin_blocked_list'
    VIEW_LIST_PAGE = 'admin_blocked_list_page_'
    ACTION_DELETE_DB = 'admin_blocked_action_db'
    ACTION_DELETE_REMNAWAVE = 'admin_blocked_action_rw'
    ACTION_DELETE_BOTH = 'admin_blocked_action_both'
    ACTION_MARK = 'admin_blocked_action_mark'
    CONFIRM_PREFIX = 'admin_blocked_confirm_'
    CANCEL = 'admin_blocked_cancel'


# =============================================================================
# FSM States
# =============================================================================


class BlockedUsersStates(StatesGroup):
    """Состояния FSM для модуля заблокированных пользователей."""

    scanning = State()
    viewing_results = State()
    confirming_action = State()
    processing_cleanup = State()


# =============================================================================
# Keyboards
# =============================================================================


def get_blocked_users_menu_keyboard(
    scan_result: dict[str, Any] | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура главного меню модуля."""
    buttons = [
        [
            InlineKeyboardButton(
                text=BlockedUsersText.BUTTON_START_SCAN.value,
                callback_data=BlockedUsersCallback.START_SCAN.value,
            )
        ]
    ]

    blocked_count = scan_result.get('blocked_count', 0) if scan_result else 0
    if blocked_count > 0:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=BlockedUsersText.BUTTON_VIEW_BLOCKED.value.format(count=blocked_count),
                    callback_data=BlockedUsersCallback.VIEW_LIST.value,
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text=BlockedUsersText.BUTTON_BACK_TO_USERS.value,
                callback_data='admin_users',
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_blocked_list_keyboard(
    page: int = 1,
    total_pages: int = 1,
    has_blocked: bool = True,
) -> InlineKeyboardMarkup:
    """Клавиатура списка заблокированных пользователей."""
    buttons = []

    # Пагинация
    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text='⬅️',
                    callback_data=f'{BlockedUsersCallback.VIEW_LIST_PAGE.value}{page - 1}',
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                text=f'{page}/{total_pages}',
                callback_data='noop',
            )
        )
        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text='➡️',
                    callback_data=f'{BlockedUsersCallback.VIEW_LIST_PAGE.value}{page + 1}',
                )
            )
        buttons.append(nav_row)

    # Действия
    if has_blocked:
        buttons.extend(
            [
                [
                    InlineKeyboardButton(
                        text=BlockedUsersText.BUTTON_DELETE_DB.value,
                        callback_data=BlockedUsersCallback.ACTION_DELETE_DB.value,
                    ),
                    InlineKeyboardButton(
                        text=BlockedUsersText.BUTTON_DELETE_REMNAWAVE.value,
                        callback_data=BlockedUsersCallback.ACTION_DELETE_REMNAWAVE.value,
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=BlockedUsersText.BUTTON_DELETE_BOTH.value,
                        callback_data=BlockedUsersCallback.ACTION_DELETE_BOTH.value,
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=BlockedUsersText.BUTTON_MARK_BLOCKED.value,
                        callback_data=BlockedUsersCallback.ACTION_MARK.value,
                    ),
                ],
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text=BlockedUsersText.BUTTON_BACK.value,
                callback_data=BlockedUsersCallback.MENU.value,
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_confirm_keyboard(action: BlockedUserAction) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения действия."""
    action_map = {
        BlockedUserAction.DELETE_FROM_DB: 'db',
        BlockedUserAction.DELETE_FROM_REMNAWAVE: 'rw',
        BlockedUserAction.DELETE_BOTH: 'both',
        BlockedUserAction.MARK_AS_BLOCKED: 'mark',
    }

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=BlockedUsersText.BUTTON_CONFIRM.value,
                    callback_data=f'{BlockedUsersCallback.CONFIRM_PREFIX.value}{action_map[action]}',
                ),
                InlineKeyboardButton(
                    text=BlockedUsersText.BUTTON_CANCEL.value,
                    callback_data=BlockedUsersCallback.CANCEL.value,
                ),
            ]
        ]
    )


# =============================================================================
# Handlers
# =============================================================================


@admin_required
@error_handler
async def show_blocked_users_menu(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
) -> None:
    """Показывает главное меню модуля заблокированных пользователей."""
    data = await state.get_data()
    scan_result = data.get('blocked_users_scan_result')

    text = BlockedUsersText.MENU_TITLE.value + BlockedUsersText.MENU_DESCRIPTION.value

    if scan_result:
        text += (
            f"📊 <b>最后扫描：</b>\n• 已阻止：{scan_result.get('blocked_count', 0)}\n• 主动：{scan_result.get('active_users', 0)}"
        )

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_blocked_users_menu_keyboard(scan_result),
    )
    await callback.answer()


@admin_required
@error_handler
async def start_scan(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Запускает сканирование пользователей."""
    await state.set_state(BlockedUsersStates.scanning)

    # Отправляем начальное сообщение
    await callback.message.edit_text(
        BlockedUsersText.SCAN_STARTED.value,
        parse_mode=ParseMode.HTML,
    )

    service = BlockedUsersService(bot)
    last_update_time = datetime.now(tz=UTC)

    async def progress_callback(checked: int, total: int) -> None:
        nonlocal last_update_time
        now = datetime.now(tz=UTC)
        # Обновляем сообщение не чаще раза в 3 секунды
        if (now - last_update_time).total_seconds() >= 3:
            last_update_time = now
            percent = int(checked / total * 100) if total > 0 else 0
            try:
                await callback.message.edit_text(
                    BlockedUsersText.SCAN_PROGRESS.value.format(
                        checked=checked,
                        total=total,
                        percent=percent,
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass  # Игнорируем ошибки обновления сообщения

    # Выполняем сканирование
    result = await service.scan_all_users(
        db,
        only_active=True,
        progress_callback=progress_callback,
    )

    # Сериализуем результат в dict для Redis и keyboard
    scan_result_dict = {
        'total_checked': result.total_checked,
        'blocked_count': result.blocked_count,
        'active_users': result.active_users,
        'errors': result.errors,
        'skipped_no_telegram': result.skipped_no_telegram,
        'scan_duration_seconds': result.scan_duration_seconds,
    }

    # Сохраняем результат в state
    await state.update_data(
        blocked_users_scan_result=scan_result_dict,
        blocked_users_list=[
            {
                'user_id': u.user_id,
                'telegram_id': u.telegram_id,
                'username': u.username,
                'full_name': u.full_name,
                'remnawave_uuid': u.remnawave_uuid,
            }
            for u in result.blocked_users
        ],
    )

    await state.set_state(BlockedUsersStates.viewing_results)

    # Формируем итоговое сообщение
    if result.blocked_count == 0:
        text = BlockedUsersText.SCAN_NO_BLOCKED.value
    else:
        text = BlockedUsersText.SCAN_COMPLETE.value.format(
            total_checked=result.total_checked,
            blocked_count=result.blocked_count,
            active_users=result.active_users,
            errors=result.errors,
            skipped=result.skipped_no_telegram,
            duration=result.scan_duration_seconds,
        )

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_blocked_users_menu_keyboard(scan_result_dict),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_blocked_list(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    page: int = 1,
) -> None:
    """Показывает список заблокированных пользователей."""
    data = await state.get_data()
    blocked_list: list[dict[str, Any]] = data.get('blocked_users_list', [])

    if not blocked_list:
        await callback.answer('没有被阻止的用户', show_alert=True)
        return

    # Пагинация
    per_page = 15
    total_pages = (len(blocked_list) + per_page - 1) // per_page
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_users = blocked_list[start_idx:end_idx]

    text = BlockedUsersText.BLOCKED_LIST_TITLE.value.format(count=len(blocked_list))

    for user_data in page_users:
        name = user_data.get('full_name') or user_data.get('username') or '未命名'
        telegram_id = user_data.get('telegram_id', '?')
        text += BlockedUsersText.BLOCKED_USER_ROW.value.format(
            name=html.escape(name),
            telegram_id=telegram_id,
        )

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_blocked_list_keyboard(page, total_pages, bool(blocked_list)),
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_blocked_list_pagination(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
) -> None:
    """Обрабатывает пагинацию списка заблокированных."""
    try:
        page = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        page = 1

    await show_blocked_list(callback, db_user, state, page)


@admin_required
@error_handler
async def show_action_confirm(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    action: BlockedUserAction,
) -> None:
    """Показывает подтверждение действия."""
    data = await state.get_data()
    blocked_list = data.get('blocked_users_list', [])
    count = len(blocked_list)

    if count == 0:
        await callback.answer('没有要处理的用户', show_alert=True)
        return

    await state.set_state(BlockedUsersStates.confirming_action)
    await state.update_data(pending_action=action.value)

    text = BlockedUsersText.CLEANUP_CONFIRM_TITLE.value

    if action == BlockedUserAction.DELETE_FROM_DB:
        text += BlockedUsersText.CLEANUP_CONFIRM_DELETE_DB.value.format(count=count)
    elif action == BlockedUserAction.DELETE_FROM_REMNAWAVE:
        text += BlockedUsersText.CLEANUP_CONFIRM_DELETE_REMNAWAVE.value.format(count=count)
    elif action == BlockedUserAction.DELETE_BOTH:
        text += BlockedUsersText.CLEANUP_CONFIRM_DELETE_BOTH.value.format(count=count)
    elif action == BlockedUserAction.MARK_AS_BLOCKED:
        text += BlockedUsersText.CLEANUP_CONFIRM_MARK.value.format(count=count)

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_confirm_keyboard(action),
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_action_delete_db(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
) -> None:
    """Обрабатывает выбор удаления из БД."""
    await show_action_confirm(callback, db_user, state, BlockedUserAction.DELETE_FROM_DB)


@admin_required
@error_handler
async def handle_action_delete_remnawave(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
) -> None:
    """Обрабатывает выбор удаления из Remnawave."""
    await show_action_confirm(callback, db_user, state, BlockedUserAction.DELETE_FROM_REMNAWAVE)


@admin_required
@error_handler
async def handle_action_delete_both(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
) -> None:
    """Обрабатывает выбор полного удаления."""
    await show_action_confirm(callback, db_user, state, BlockedUserAction.DELETE_BOTH)


@admin_required
@error_handler
async def handle_action_mark(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
) -> None:
    """Обрабатывает выбор пометки как заблокированных."""
    await show_action_confirm(callback, db_user, state, BlockedUserAction.MARK_AS_BLOCKED)


@admin_required
@error_handler
async def handle_confirm_action(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
    bot: Bot,
) -> None:
    """Выполняет подтвержденное действие."""
    data = await state.get_data()
    blocked_list = data.get('blocked_users_list', [])

    # Определяем действие из callback_data
    action_code = callback.data.replace(BlockedUsersCallback.CONFIRM_PREFIX.value, '')
    action_map = {
        'db': BlockedUserAction.DELETE_FROM_DB,
        'rw': BlockedUserAction.DELETE_FROM_REMNAWAVE,
        'both': BlockedUserAction.DELETE_BOTH,
        'mark': BlockedUserAction.MARK_AS_BLOCKED,
    }
    action = action_map.get(action_code)

    if not action:
        await callback.answer('未知行动', show_alert=True)
        return

    if not blocked_list:
        await callback.answer('没有要处理的用户', show_alert=True)
        return

    await state.set_state(BlockedUsersStates.processing_cleanup)

    # Преобразуем обратно в BlockCheckResult
    blocked_results = [
        BlockCheckResult(
            user_id=u['user_id'],
            telegram_id=u['telegram_id'],
            username=u['username'],
            full_name=u['full_name'],
            status=None,  # type: ignore
            remnawave_uuid=u['remnawave_uuid'],
        )
        for u in blocked_list
    ]

    service = BlockedUsersService(bot)
    last_update_time = datetime.now(tz=UTC)

    async def progress_callback(processed: int, total_count: int) -> None:
        nonlocal last_update_time
        now = datetime.now(tz=UTC)
        if (now - last_update_time).total_seconds() >= 2:
            last_update_time = now
            try:
                await callback.message.edit_text(
                    BlockedUsersText.CLEANUP_PROGRESS.value.format(
                        processed=processed,
                        total=total_count,
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    # Выполняем очистку
    result = await service.cleanup_blocked_users(
        db,
        blocked_results,
        action,
        progress_callback=progress_callback,
    )

    # Очищаем сохраненные данные
    await state.update_data(
        blocked_users_scan_result=None,
        blocked_users_list=[],
        pending_action=None,
    )
    await state.set_state(None)

    # Показываем результат
    text = BlockedUsersText.CLEANUP_COMPLETE.value.format(
        deleted_db=result.deleted_from_db,
        deleted_remnawave=result.deleted_from_remnawave,
        marked=result.marked_as_blocked,
        errors=len(result.errors),
    )

    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_blocked_users_menu_keyboard(),
    )

    logger.info(
        '清除被阻止的用户已完成：DB=、RW=、marked=、错误',
        deleted_from_db=result.deleted_from_db,
        deleted_from_remnawave=result.deleted_from_remnawave,
        marked_as_blocked=result.marked_as_blocked,
        errors_count=len(result.errors),
    )

    await callback.answer()


@admin_required
@error_handler
async def handle_cancel(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
) -> None:
    """Отменяет текущее действие и возвращает в меню."""
    await state.update_data(pending_action=None)
    await state.set_state(BlockedUsersStates.viewing_results)
    await show_blocked_users_menu(callback, db_user, state)


# =============================================================================
# Registration
# =============================================================================


def register_handlers(dp: Dispatcher) -> None:
    """Регистрирует хендлеры модуля заблокированных пользователей."""

    # Главное меню
    dp.callback_query.register(
        show_blocked_users_menu,
        F.data == BlockedUsersCallback.MENU.value,
    )

    # Сканирование
    dp.callback_query.register(
        start_scan,
        F.data == BlockedUsersCallback.START_SCAN.value,
    )

    # Список заблокированных
    dp.callback_query.register(
        show_blocked_list,
        F.data == BlockedUsersCallback.VIEW_LIST.value,
    )

    # Пагинация списка
    dp.callback_query.register(
        handle_blocked_list_pagination,
        F.data.startswith(BlockedUsersCallback.VIEW_LIST_PAGE.value),
    )

    # Выбор действий
    dp.callback_query.register(
        handle_action_delete_db,
        F.data == BlockedUsersCallback.ACTION_DELETE_DB.value,
    )
    dp.callback_query.register(
        handle_action_delete_remnawave,
        F.data == BlockedUsersCallback.ACTION_DELETE_REMNAWAVE.value,
    )
    dp.callback_query.register(
        handle_action_delete_both,
        F.data == BlockedUsersCallback.ACTION_DELETE_BOTH.value,
    )
    dp.callback_query.register(
        handle_action_mark,
        F.data == BlockedUsersCallback.ACTION_MARK.value,
    )

    # Подтверждение действий
    dp.callback_query.register(
        handle_confirm_action,
        F.data.startswith(BlockedUsersCallback.CONFIRM_PREFIX.value),
    )

    # Отмена
    dp.callback_query.register(
        handle_cancel,
        F.data == BlockedUsersCallback.CANCEL.value,
    )

