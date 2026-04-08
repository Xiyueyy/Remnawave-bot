import html
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.server_squad import (
    count_active_users_for_squad,
    get_all_server_squads,
    get_server_squad_by_uuid,
)
from app.database.models import User
from app.keyboards.admin import (
    get_admin_remnawave_keyboard,
    get_node_management_keyboard,
    get_squad_edit_keyboard,
    get_squad_management_keyboard,
)
from app.localization.texts import get_texts
from app.services.remnawave_service import RemnaWaveConfigurationError, RemnaWaveService
from app.services.remnawave_sync_service import (
    RemnaWaveAutoSyncStatus,
    remnawave_sync_service,
)
from app.services.system_settings_service import bot_configuration_service
from app.states import (
    RemnaWaveSyncStates,
    SquadCreateStates,
    SquadMigrationStates,
    SquadRenameStates,
)
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_bytes, format_datetime


logger = structlog.get_logger(__name__)

squad_inbound_selections = {}
squad_create_data = {}

MIGRATION_PAGE_SIZE = 8


def _format_duration(seconds: float) -> str:
    if seconds < 1:
        return '小于1秒'

    minutes, sec = divmod(int(seconds), 60)
    if minutes:
        if sec:
            return f'{minutes} 最小 {sec} 秒'
        return f'{minutes} 分钟'
    return f'{sec} 与'


def _format_user_stats(stats: dict[str, Any] | None) -> str:
    if not stats:
        return '—'

    created = stats.get('created', 0)
    updated = stats.get('updated', 0)
    deleted = stats.get('deleted', stats.get('deactivated', 0))
    errors = stats.get('errors', 0)

    return f'• 创建者：{created}\n• 更新：{updated}\n• 停用：{deleted}\n• 错误：{errors}'


def _format_server_stats(stats: dict[str, Any] | None) -> str:
    if not stats:
        return '—'

    created = stats.get('created', 0)
    updated = stats.get('updated', 0)
    removed = stats.get('removed', 0)
    total = stats.get('total', 0)

    return f'• 创建者：{created}\n• 更新：{updated}\n• 删除：{removed}\n• 面板总计：{total}'


def _build_auto_sync_view(status: RemnaWaveAutoSyncStatus) -> tuple[str, types.InlineKeyboardMarkup]:
    times_text = ', '.join(t.strftime('%H:%M') for t in status.times) if status.times else '—'
    next_run_text = format_datetime(status.next_run) if status.next_run else '—'

    if status.last_run_finished_at:
        finished_text = format_datetime(status.last_run_finished_at)
        started_text = format_datetime(status.last_run_started_at) if status.last_run_started_at else '—'
        duration = status.last_run_finished_at - status.last_run_started_at if status.last_run_started_at else None
        duration_text = f' ({_format_duration(duration.total_seconds())})' if duration else ''
        reason_map = {
            'manual': 'вручную',
            'auto': 'по расписанию',
            'immediate': 'при включении',
        }
        reason_text = reason_map.get(status.last_run_reason or '', '—')
        result_icon = '✅' if status.last_run_success else '❌'
        result_label = 'успешно' if status.last_run_success else 'с ошибками'
        error_block = f'\n⚠️ Ошибка: {status.last_run_error}' if status.last_run_error else ''
        last_run_text = (
            f'{result_icon} {result_label}\n'
            f'• Старт: {started_text}\n'
            f'• Завершено: {finished_text}{duration_text}\n'
            f'• Причина запуска: {reason_text}{error_block}'
        )
    elif status.last_run_started_at:
        last_run_text = (
            '⏳ Синхронизация началась, но еще не завершилась'
            if status.is_running
            else f'ℹ️ Последний запуск: {format_datetime(status.last_run_started_at)}'
        )
    else:
        last_run_text = '—'

    running_text = '⏳ Выполняется сейчас' if status.is_running else 'Ожидание'
    toggle_text = '❌ Отключить' if status.enabled else '✅ Включить'

    text = f"🔄 <b>自动同步RemnaWave</b>\n\n⚙️ <b>状态：</b> {('✅ Включена' if status.enabled else '❌ Отключена')}\n🕒 <b>时间表：</b> {times_text}\n📅 <b>下次推出：</b> {(next_run_text if status.enabled else '—')}\n⏱️ <b>条件：</b> {running_text}\n\n📊 <b>上次发布：</b>\n{last_run_text}\n\n👥 <b>用户：</b>\n{_format_user_stats(status.last_user_stats)}\n\n🌐 <b>服务器：</b>\n{_format_server_stats(status.last_server_stats)}"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text='🔁 立即启动',
                    callback_data='remnawave_auto_sync_run',
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=toggle_text,
                    callback_data='remnawave_auto_sync_toggle',
                )
            ],
            [
                types.InlineKeyboardButton(
                    text='🕒 更改日程',
                    callback_data='remnawave_auto_sync_times',
                )
            ],
            [
                types.InlineKeyboardButton(
                    text='⬅️ 返回',
                    callback_data='admin_rw_sync',
                )
            ],
        ]
    )

    return text, keyboard


def _format_migration_server_label(texts, server) -> str:
    status = (
        texts.t('ADMIN_SQUAD_MIGRATION_STATUS_AVAILABLE', '✅可用')
        if getattr(server, 'is_available', True)
        else texts.t('ADMIN_SQUAD_MIGRATION_STATUS_UNAVAILABLE', '🚫不可用')
    )
    return texts.t(
        'ADMIN_SQUAD_MIGRATION_SERVER_LABEL',
        '{name}—👥{users}({status})',
    ).format(name=html.escape(server.display_name), users=server.current_users, status=status)


def _build_migration_keyboard(
    texts,
    squads,
    page: int,
    total_pages: int,
    stage: str,
    *,
    exclude_uuid: str = None,
):
    prefix = 'admin_migration_source' if stage == 'source' else 'admin_migration_target'
    rows = []
    has_items = False

    button_template = texts.t(
        'ADMIN_SQUAD_MIGRATION_SQUAD_BUTTON',
        '🌍{name}—👥{users}({status})',
    )

    for squad in squads:
        if exclude_uuid and squad.squad_uuid == exclude_uuid:
            continue

        has_items = True
        status = (
            texts.t('ADMIN_SQUAD_MIGRATION_STATUS_AVAILABLE_SHORT', '✅')
            if getattr(squad, 'is_available', True)
            else texts.t('ADMIN_SQUAD_MIGRATION_STATUS_UNAVAILABLE_SHORT', '🚫')
        )
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=button_template.format(
                        name=squad.display_name,
                        users=squad.current_users,
                        status=status,
                    ),
                    callback_data=f'{prefix}_{squad.squad_uuid}',
                )
            ]
        )

    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                types.InlineKeyboardButton(
                    text='⬅️',
                    callback_data=f'{prefix}_page_{page - 1}',
                )
            )
        nav_buttons.append(
            types.InlineKeyboardButton(
                text=texts.t(
                    'ADMIN_SQUAD_MIGRATION_PAGE',
                    '第{page}/{pages}页',
                ).format(page=page, pages=total_pages),
                callback_data='admin_migration_page_info',
            )
        )
        if page < total_pages:
            nav_buttons.append(
                types.InlineKeyboardButton(
                    text='➡️',
                    callback_data=f'{prefix}_page_{page + 1}',
                )
            )
        rows.append(nav_buttons)

    rows.append(
        [
            types.InlineKeyboardButton(
                text=texts.CANCEL,
                callback_data='admin_migration_cancel',
            )
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows), has_items


async def _fetch_migration_page(
    db: AsyncSession,
    page: int,
):
    squads, total = await get_all_server_squads(
        db,
        page=max(1, page),
        limit=MIGRATION_PAGE_SIZE,
    )
    total_pages = max(1, math.ceil(total / MIGRATION_PAGE_SIZE))

    page = max(page, 1)
    if page > total_pages:
        page = total_pages
        squads, total = await get_all_server_squads(
            db,
            page=page,
            limit=MIGRATION_PAGE_SIZE,
        )
        total_pages = max(1, math.ceil(total / MIGRATION_PAGE_SIZE))

    return squads, page, total_pages


@admin_required
@error_handler
async def show_squad_migration_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)

    await state.clear()

    squads, page, total_pages = await _fetch_migration_page(db, page=1)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        'source',
    )

    message = (
        texts.t('ADMIN_SQUAD_MIGRATION_TITLE', '🚚<b>节点组迁移</b>')
        + '\n\n'
        + texts.t(
            'ADMIN_SQUAD_MIGRATION_SELECT_SOURCE',
            '请选择要迁出的squad：',
        )
    )

    if not has_items:
        message += '\n\n' + texts.t(
            'ADMIN_SQUAD_MIGRATION_NO_OPTIONS',
            '没有可用的squads。请添加新的或取消操作。',
        )

    await state.set_state(SquadMigrationStates.selecting_source)

    await callback.message.edit_text(
        message,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def paginate_migration_source(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    if await state.get_state() != SquadMigrationStates.selecting_source:
        await callback.answer()
        return

    try:
        page = int(callback.data.split('_page_')[-1])
    except (ValueError, IndexError):
        await callback.answer()
        return

    squads, page, total_pages = await _fetch_migration_page(db, page=page)
    texts = get_texts(db_user.language)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        'source',
    )

    message = (
        texts.t('ADMIN_SQUAD_MIGRATION_TITLE', '🚚<b>节点组迁移</b>')
        + '\n\n'
        + texts.t(
            'ADMIN_SQUAD_MIGRATION_SELECT_SOURCE',
            '请选择要迁出的squad：',
        )
    )

    if not has_items:
        message += '\n\n' + texts.t(
            'ADMIN_SQUAD_MIGRATION_NO_OPTIONS',
            '没有可用的squads。请添加新的或取消操作。',
        )

    await callback.message.edit_text(
        message,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_migration_source_selection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    if await state.get_state() != SquadMigrationStates.selecting_source:
        await callback.answer()
        return

    if '_page_' in callback.data:
        await callback.answer()
        return

    source_uuid = callback.data.replace('admin_migration_source_', '', 1)

    texts = get_texts(db_user.language)
    server = await get_server_squad_by_uuid(db, source_uuid)

    if not server:
        await callback.answer(
            texts.t(
                'ADMIN_SQUAD_MIGRATION_SQUAD_NOT_FOUND',
                '节点组未找到或不可用。',
            ),
            show_alert=True,
        )
        return

    await state.update_data(
        source_uuid=server.squad_uuid,
        source_display=_format_migration_server_label(texts, server),
    )

    squads, page, total_pages = await _fetch_migration_page(db, page=1)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        'target',
        exclude_uuid=server.squad_uuid,
    )

    message = (
        texts.t('ADMIN_SQUAD_MIGRATION_TITLE', '🚚<b>节点组迁移</b>')
        + '\n\n'
        + texts.t(
            'ADMIN_SQUAD_MIGRATION_SELECTED_SOURCE',
            '来源：{source}',
        ).format(source=_format_migration_server_label(texts, server))
        + '\n\n'
        + texts.t(
            'ADMIN_SQUAD_MIGRATION_SELECT_TARGET',
            '请选择要迁入的squad：',
        )
    )

    if not has_items:
        message += '\n\n' + texts.t(
            'ADMIN_SQUAD_MIGRATION_TARGET_EMPTY',
            '没有其他可用于迁移的squads。请取消操作或创建新的squads。',
        )

    await state.set_state(SquadMigrationStates.selecting_target)

    await callback.message.edit_text(
        message,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def paginate_migration_target(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    if await state.get_state() != SquadMigrationStates.selecting_target:
        await callback.answer()
        return

    try:
        page = int(callback.data.split('_page_')[-1])
    except (ValueError, IndexError):
        await callback.answer()
        return

    data = await state.get_data()
    source_uuid = data.get('source_uuid')
    if not source_uuid:
        await callback.answer()
        return

    texts = get_texts(db_user.language)

    squads, page, total_pages = await _fetch_migration_page(db, page=page)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        'target',
        exclude_uuid=source_uuid,
    )

    source_display = data.get('source_display') or source_uuid

    message = (
        texts.t('ADMIN_SQUAD_MIGRATION_TITLE', '🚚<b>节点组迁移</b>')
        + '\n\n'
        + texts.t(
            'ADMIN_SQUAD_MIGRATION_SELECTED_SOURCE',
            '来源：{source}',
        ).format(source=source_display)
        + '\n\n'
        + texts.t(
            'ADMIN_SQUAD_MIGRATION_SELECT_TARGET',
            '请选择要迁入的squad：',
        )
    )

    if not has_items:
        message += '\n\n' + texts.t(
            'ADMIN_SQUAD_MIGRATION_TARGET_EMPTY',
            '没有其他可用于迁移的squads。请取消操作或创建新的squads。',
        )

    await callback.message.edit_text(
        message,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def handle_migration_target_selection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    current_state = await state.get_state()
    if current_state != SquadMigrationStates.selecting_target:
        await callback.answer()
        return

    if '_page_' in callback.data:
        await callback.answer()
        return

    data = await state.get_data()
    source_uuid = data.get('source_uuid')

    if not source_uuid:
        await callback.answer()
        return

    target_uuid = callback.data.replace('admin_migration_target_', '', 1)

    texts = get_texts(db_user.language)

    if target_uuid == source_uuid:
        await callback.answer(
            texts.t(
                'ADMIN_SQUAD_MIGRATION_SAME_SQUAD',
                '不能选择相同的squad。',
            ),
            show_alert=True,
        )
        return

    target_server = await get_server_squad_by_uuid(db, target_uuid)
    if not target_server:
        await callback.answer(
            texts.t(
                'ADMIN_SQUAD_MIGRATION_SQUAD_NOT_FOUND',
                '节点组未找到或不可用。',
            ),
            show_alert=True,
        )
        return

    source_display = data.get('source_display') or source_uuid

    users_to_move = await count_active_users_for_squad(db, source_uuid)

    await state.update_data(
        target_uuid=target_server.squad_uuid,
        target_display=_format_migration_server_label(texts, target_server),
        migration_count=users_to_move,
    )

    await state.set_state(SquadMigrationStates.confirming)

    message_lines = [
        texts.t('ADMIN_SQUAD_MIGRATION_TITLE', '🚚<b>节点组迁移</b>'),
        '',
        texts.t(
            'ADMIN_SQUAD_MIGRATION_CONFIRM_DETAILS',
            '请检查迁移参数：',
        ),
        texts.t(
            'ADMIN_SQUAD_MIGRATION_CONFIRM_SOURCE',
            '•来源：{source}',
        ).format(source=source_display),
        texts.t(
            'ADMIN_SQUAD_MIGRATION_CONFIRM_TARGET',
            '•目标：{target}',
        ).format(target=_format_migration_server_label(texts, target_server)),
        texts.t(
            'ADMIN_SQUAD_MIGRATION_CONFIRM_COUNT',
            '•要迁移的用户数：{count}',
        ).format(count=users_to_move),
        '',
        texts.t(
            'ADMIN_SQUAD_MIGRATION_CONFIRM_PROMPT',
            '请确认执行操作。',
        ),
    ]

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        'ADMIN_SQUAD_MIGRATION_CONFIRM_BUTTON',
                        '✅确认',
                    ),
                    callback_data='admin_migration_confirm',
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        'ADMIN_SQUAD_MIGRATION_CHANGE_TARGET',
                        '🔄更改目标服务器',
                    ),
                    callback_data='admin_migration_change_target',
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.CANCEL,
                    callback_data='admin_migration_cancel',
                )
            ],
        ]
    )

    await callback.message.edit_text(
        '\n'.join(message_lines),
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def change_migration_target(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()
    source_uuid = data.get('source_uuid')

    if not source_uuid:
        await callback.answer()
        return

    await state.set_state(SquadMigrationStates.selecting_target)

    texts = get_texts(db_user.language)
    squads, page, total_pages = await _fetch_migration_page(db, page=1)
    keyboard, has_items = _build_migration_keyboard(
        texts,
        squads,
        page,
        total_pages,
        'target',
        exclude_uuid=source_uuid,
    )

    source_display = data.get('source_display') or source_uuid

    message = (
        texts.t('ADMIN_SQUAD_MIGRATION_TITLE', '🚚<b>节点组迁移</b>')
        + '\n\n'
        + texts.t(
            'ADMIN_SQUAD_MIGRATION_SELECTED_SOURCE',
            '来源：{source}',
        ).format(source=source_display)
        + '\n\n'
        + texts.t(
            'ADMIN_SQUAD_MIGRATION_SELECT_TARGET',
            '请选择要迁入的squad：',
        )
    )

    if not has_items:
        message += '\n\n' + texts.t(
            'ADMIN_SQUAD_MIGRATION_TARGET_EMPTY',
            '没有其他可用于迁移的squads。请取消操作或创建新的squads。',
        )

    await callback.message.edit_text(
        message,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_squad_migration(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    current_state = await state.get_state()
    if current_state != SquadMigrationStates.confirming:
        await callback.answer()
        return

    data = await state.get_data()
    source_uuid = data.get('source_uuid')
    target_uuid = data.get('target_uuid')

    if not source_uuid or not target_uuid:
        await callback.answer()
        return

    texts = get_texts(db_user.language)
    remnawave_service = RemnaWaveService()

    await callback.answer(texts.t('ADMIN_SQUAD_MIGRATION_IN_PROGRESS', '正在开始迁移...'))

    try:
        result = await remnawave_service.migrate_squad_users(
            db,
            source_uuid=source_uuid,
            target_uuid=target_uuid,
        )
    except RemnaWaveConfigurationError as error:
        message = texts.t(
            'ADMIN_SQUAD_MIGRATION_API_ERROR',
            '❌Remnawave接口未配置：{error}',
        ).format(error=str(error))
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t(
                            'ADMIN_SQUAD_MIGRATION_BACK_BUTTON',
                            '⬅️返回Remnawave',
                        ),
                        callback_data='admin_remnawave',
                    )
                ]
            ]
        )
        await callback.message.edit_text(message, reply_markup=reply_markup)
        await state.clear()
        return

    source_display = data.get('source_display') or source_uuid
    target_display = data.get('target_display') or target_uuid

    if not result.get('success'):
        error_message = result.get('message') or ''
        error_code = result.get('error') or 'unexpected'
        message = texts.t(
            'ADMIN_SQUAD_MIGRATION_ERROR',
            '❌迁移失败（代码：{code}）。{details}',
        ).format(code=error_code, details=error_message)
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t(
                            'ADMIN_SQUAD_MIGRATION_BACK_BUTTON',
                            '⬅️返回Remnawave',
                        ),
                        callback_data='admin_remnawave',
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        text=texts.t(
                            'ADMIN_SQUAD_MIGRATION_NEW_BUTTON',
                            '🔁新迁移',
                        ),
                        callback_data='admin_rw_migration',
                    )
                ],
            ]
        )
        await callback.message.edit_text(message, reply_markup=reply_markup)
        await state.clear()
        return

    message_lines = [
        texts.t('ADMIN_SQUAD_MIGRATION_SUCCESS_TITLE', '✅迁移完成'),
        '',
        texts.t('ADMIN_SQUAD_MIGRATION_CONFIRM_SOURCE', '•来源：{source}').format(source=source_display),
        texts.t('ADMIN_SQUAD_MIGRATION_CONFIRM_TARGET', '•目标：{target}').format(target=target_display),
        '',
        texts.t(
            'ADMIN_SQUAD_MIGRATION_RESULT_TOTAL',
            '找到的订阅数：{count}',
        ).format(count=result.get('total', 0)),
        texts.t(
            'ADMIN_SQUAD_MIGRATION_RESULT_UPDATED',
            '已迁移：{count}',
        ).format(count=result.get('updated', 0)),
    ]

    panel_updated = result.get('panel_updated', 0)
    panel_failed = result.get('panel_failed', 0)

    if panel_updated:
        message_lines.append(
            texts.t(
                'ADMIN_SQUAD_MIGRATION_RESULT_PANEL_UPDATED',
                '在面板中已更新：{count}',
            ).format(count=panel_updated)
        )
    if panel_failed:
        message_lines.append(
            texts.t(
                'ADMIN_SQUAD_MIGRATION_RESULT_PANEL_FAILED',
                '在面板中更新失败：{count}',
            ).format(count=panel_failed)
        )

    reply_markup = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        'ADMIN_SQUAD_MIGRATION_NEW_BUTTON',
                        '🔁新迁移',
                    ),
                    callback_data='admin_rw_migration',
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        'ADMIN_SQUAD_MIGRATION_BACK_BUTTON',
                        '⬅️返回Remnawave',
                    ),
                    callback_data='admin_remnawave',
                )
            ],
        ]
    )

    await callback.message.edit_text(
        '\n'.join(message_lines),
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    await state.clear()


@admin_required
@error_handler
async def cancel_squad_migration(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await state.clear()

    message = texts.t(
        'ADMIN_SQUAD_MIGRATION_CANCELLED',
        '❌迁移已取消。',
    )

    reply_markup = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        'ADMIN_SQUAD_MIGRATION_BACK_BUTTON',
                        '⬅️返回Remnawave',
                    ),
                    callback_data='admin_remnawave',
                )
            ]
        ]
    )

    await callback.message.edit_text(message, reply_markup=reply_markup)
    await callback.answer()


@admin_required
@error_handler
async def handle_migration_page_info(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    await callback.answer(
        texts.t('ADMIN_SQUAD_MIGRATION_PAGE_HINT', '这是当前页。'),
        show_alert=False,
    )


@admin_required
@error_handler
async def show_remnawave_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    remnawave_service = RemnaWaveService()
    connection_test = await remnawave_service.test_api_connection()

    status = connection_test.get('status')
    if status == 'connected':
        status_emoji = '✅'
    elif status == 'not_configured':
        status_emoji = 'ℹ️'
    else:
        status_emoji = '❌'

    api_url_display = settings.REMNAWAVE_API_URL or '—'

    text = f"🖥️ <b>管理 Remnawave</b>\n\n📡 <b>连接：</b> {status_emoji} {connection_test.get('message', 'Нет данных')}\n🌐 <b>URL：</b> <code>{api_url_display}</code>\n\n选择动作："

    await callback.message.edit_text(text, reply_markup=get_admin_remnawave_keyboard(db_user.language))
    await callback.answer()


@admin_required
@error_handler
async def show_system_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.get_system_statistics()

    if 'error' in stats:
        await callback.message.edit_text(
            f"❌ 获取统计信息时出错：{stats['error']}",
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_remnawave')]]
            ),
        )
        await callback.answer()
        return

    system = stats.get('system', {})
    users_by_status = stats.get('users_by_status', {})
    server_info = stats.get('server_info', {})
    bandwidth = stats.get('bandwidth', {})
    traffic_periods = stats.get('traffic_periods', {})
    nodes_realtime = stats.get('nodes_realtime', [])
    nodes_weekly = stats.get('nodes_weekly', [])

    memory_total = server_info.get('memory_total', 1)
    memory_used_percent = (server_info.get('memory_used', 0) / memory_total * 100) if memory_total > 0 else 0

    uptime_seconds = server_info.get('uptime_seconds', 0)
    uptime_days = int(uptime_seconds // 86400)
    uptime_hours = int((uptime_seconds % 86400) // 3600)
    uptime_str = f'{uptime_days}д {uptime_hours}ч'

    users_status_text = ''
    for status, count in users_by_status.items():
        status_emoji = {'ACTIVE': '✅', 'DISABLED': '❌', 'LIMITED': '⚠️', 'EXPIRED': '⏰'}.get(status, '❓')
        users_status_text += f'  {status_emoji} {status}: {count}\n'

    top_nodes_text = ''
    for i, node in enumerate(nodes_weekly[:3], 1):
        top_nodes_text += f'  {i}. {node["name"]}: {format_bytes(node["total_bytes"])}\n'

    realtime_nodes_text = ''
    for node in nodes_realtime[:3]:
        node_total = node.get('downloadBytes', 0) + node.get('uploadBytes', 0)
        if node_total > 0:
            realtime_nodes_text += f'  📡 {node.get("nodeName", "Unknown")}: {format_bytes(node_total)}\n'

    def format_traffic_change(difference_str):
        if not difference_str or difference_str == '0':
            return ''
        if difference_str.startswith('-'):
            return f' (🔻 {difference_str[1:]})'
        return f' (🔺 {difference_str})'

    text = f"📊 <b>详细统计Remnawave</b>\n\n🖥️<b>服务器：</b>\n- CPU：{server_info.get('cpu_cores', 0)} 核心\n- 内存：{format_bytes(server_info.get('memory_used', 0))} / {format_bytes(memory_total)} ({memory_used_percent:.1f}%)\n- 可用：{format_bytes(server_info.get('memory_free', 0))}\n- 正常运行时间：{uptime_str}\n\n👥 <b>用户（共{system.get('total_users', 0)}）：</b>\n- 🟢 现已上线：{system.get('users_online', 0)}\n- 📅每天：{system.get('users_last_day', 0)}\n- 📊 一周：{system.get('users_last_week', 0)}\n- 💤 从未访问过：{system.get('users_never_online', 0)}\n\n<b>用户状态：</b>\n{users_status_text}\n\n🌐 <b>节点（{system.get('nodes_online', 0)}上线）：</b>"

    if realtime_nodes_text:
        text += f'<b>实时活动：</b>\n{realtime_nodes_text}'

    if top_nodes_text:
        text += f'<b> 本周热门节点：</b>\n{top_nodes_text}'

    text += f"📈 <b>总用户流量：</b> {format_bytes(system.get('total_user_traffic', 0))}\n\n📊 <b>各时段流量：</b>\n- 2 天：{format_bytes(traffic_periods.get('last_2_days', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('last_2_days', {}).get('difference', ''))}\n- 7 天：{format_bytes(traffic_periods.get('last_7_days', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('last_7_days', {}).get('difference', ''))}\n- 30 天：{format_bytes(traffic_periods.get('last_30_days', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('last_30_days', {}).get('difference', ''))}\n- 月份：{format_bytes(traffic_periods.get('current_month', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('current_month', {}).get('difference', ''))}\n- 年份：{format_bytes(traffic_periods.get('current_year', {}).get('current', 0))}{format_traffic_change(traffic_periods.get('current_year', {}).get('difference', ''))}"

    if bandwidth.get('realtime_total', 0) > 0:
        text += f"⚡ <b>实时路况：</b>\n- 下载：{format_bytes(bandwidth.get('realtime_download', 0))}\n- 加载：{format_bytes(bandwidth.get('realtime_upload', 0))}\n- 总计：{format_bytes(bandwidth.get('realtime_total', 0))}"

    text += f"🕒 <b>更新：</b> {format_datetime(stats.get('last_updated', datetime.now(UTC)))}"

    keyboard = [
        [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_rw_system')],
        [
            types.InlineKeyboardButton(text='📈 节点', callback_data='admin_rw_nodes'),
            types.InlineKeyboardButton(text='👥 同步', callback_data='admin_rw_sync'),
        ],
        [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_remnawave')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_traffic_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    remnawave_service = RemnaWaveService()

    try:
        async with remnawave_service.get_api_client() as api:
            bandwidth_stats = await api.get_bandwidth_stats()

            realtime_usage = await api.get_nodes_realtime_usage()

            nodes_stats = await api.get_nodes_statistics()

    except Exception as e:
        await callback.message.edit_text(
            f'❌ 获取流量统计错误：{e!s}',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_remnawave')]]
            ),
        )
        await callback.answer()
        return

    def parse_bandwidth(bandwidth_str):
        return remnawave_service._parse_bandwidth_string(bandwidth_str)

    total_realtime_download = sum(node.get('downloadBytes', 0) for node in realtime_usage)
    total_realtime_upload = sum(node.get('uploadBytes', 0) for node in realtime_usage)
    total_realtime = total_realtime_download + total_realtime_upload

    total_users_online = sum(node.get('usersOnline', 0) for node in realtime_usage)

    periods = {
        'last_2_days': bandwidth_stats.get('bandwidthLastTwoDays', {}),
        'last_7_days': bandwidth_stats.get('bandwidthLastSevenDays', {}),
        'last_30_days': bandwidth_stats.get('bandwidthLast30Days', {}),
        'current_month': bandwidth_stats.get('bandwidthCalendarMonth', {}),
        'current_year': bandwidth_stats.get('bandwidthCurrentYear', {}),
    }

    def format_change(diff_str):
        if not diff_str or diff_str == '0':
            return ''
        if diff_str.startswith('-'):
            return f' 🔻 {diff_str[1:]}'
        return f' 🔺 {diff_str}'

    text = f"📊 <b>流量统计Remnawave</b>\n\n⚡ <b>入站流量：</b>\n- 下载：{format_bytes(total_realtime_download)}\n- 加载：{format_bytes(total_realtime_upload)}\n- 总流量：{format_bytes(total_realtime)}\n- 在线用户：{total_users_online}\n\n📈 <b> 按期间统计：</b>\n\n<b>2天内：</b>\n- 当前：{format_bytes(parse_bandwidth(periods['last_2_days'].get('current', '0')))}\n- 上一页：{format_bytes(parse_bandwidth(periods['last_2_days'].get('previous', '0')))}\n- 更改：{format_change(periods['last_2_days'].get('difference', ''))}\n\n<b>7天内：</b>\n- 当前：{format_bytes(parse_bandwidth(periods['last_7_days'].get('current', '0')))}\n- 上一页：{format_bytes(parse_bandwidth(periods['last_7_days'].get('previous', '0')))}\n- 更改：{format_change(periods['last_7_days'].get('difference', ''))}\n\n<b>30天内：</b>\n- 当前：{format_bytes(parse_bandwidth(periods['last_30_days'].get('current', '0')))}\n- 上一页：{format_bytes(parse_bandwidth(periods['last_30_days'].get('previous', '0')))}\n- 更改：{format_change(periods['last_30_days'].get('difference', ''))}\n\n<b>当前月份：</b>\n- 当前：{format_bytes(parse_bandwidth(periods['current_month'].get('current', '0')))}\n- 上一页：{format_bytes(parse_bandwidth(periods['current_month'].get('previous', '0')))}\n- 更改：{format_change(periods['current_month'].get('difference', ''))}\n\n<b>当前年份：</b>\n- 当前：{format_bytes(parse_bandwidth(periods['current_year'].get('current', '0')))}\n- 上一篇：{format_bytes(parse_bandwidth(periods['current_year'].get('previous', '0')))}\n- 更改：{format_change(periods['current_year'].get('difference', ''))}"

    if realtime_usage:
        text += '🌐 <b> 节点流量（实时）：</b>'
        for node in sorted(realtime_usage, key=lambda x: x.get('totalBytes', 0), reverse=True):
            node_total = node.get('totalBytes', 0)
            if node_total > 0:
                text += f'- {node.get("nodeName", "Unknown")}: {format_bytes(node_total)}\n'

    if nodes_stats.get('lastSevenDays'):
        text += '📊 <b> 7 天顶级节点：</b>'

        nodes_weekly = {}
        for day_data in nodes_stats['lastSevenDays']:
            node_name = day_data['nodeName']
            if node_name not in nodes_weekly:
                nodes_weekly[node_name] = 0
            nodes_weekly[node_name] += int(day_data['totalBytes'])

        sorted_nodes = sorted(nodes_weekly.items(), key=lambda x: x[1], reverse=True)
        for i, (node_name, total_bytes) in enumerate(sorted_nodes[:5], 1):
            text += f'{i}. {node_name}: {format_bytes(total_bytes)}\n'

    text += f'🕒 <b>更新：</b> {format_datetime(datetime.now(UTC))}'

    keyboard = [
        [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_rw_traffic')],
        [
            types.InlineKeyboardButton(text='📈 节点', callback_data='admin_rw_nodes'),
            types.InlineKeyboardButton(text='📊 系统', callback_data='admin_rw_system'),
        ],
        [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_remnawave')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_nodes_management(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    remnawave_service = RemnaWaveService()
    nodes = await remnawave_service.get_all_nodes()

    if not nodes:
        await callback.message.edit_text(
            '🖥️ 找不到节点或连接错误',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_remnawave')]]
            ),
        )
        await callback.answer()
        return

    text = '🖥️ <b>节点管理</b>'
    keyboard = []

    for node in nodes:
        status_emoji = '🟢' if node['is_node_online'] else '🔴'
        connection_emoji = '📡' if node['is_connected'] else '📵'

        text += f'{status_emoji} {connection_emoji} <b>{node["name"]}</b>\n'
        text += f'🌍 {node["country_code"]} • {node["address"]}\n'
        text += f"👥 在线：{node['users_online'] or 0}"

        keyboard.append(
            [types.InlineKeyboardButton(text=f'⚙️ {node["name"]}', callback_data=f'admin_node_manage_{node["uuid"]}')]
        )

    keyboard.extend(
        [
            [types.InlineKeyboardButton(text='🔄 重新加载所有内容', callback_data='admin_restart_all_nodes')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_remnawave')],
        ]
    )

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_node_details(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    node_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    node = await remnawave_service.get_node_details(node_uuid)

    if not node:
        await callback.answer('❌ 未找到节点', show_alert=True)
        return

    status_emoji = '🟢' if node['is_node_online'] else '🔴'
    xray_emoji = '✅' if node['is_xray_running'] else '❌'

    status_change = format_datetime(node['last_status_change']) if node.get('last_status_change') else '—'
    created_at = format_datetime(node['created_at']) if node.get('created_at') else '—'
    updated_at = format_datetime(node['updated_at']) if node.get('updated_at') else '—'
    notify_percent = f'{node["notify_percent"]}%' if node.get('notify_percent') is not None else '—'
    sys_info = (node.get('system') or {}).get('info', {})
    cpu_model = html.escape(str(sys_info.get('cpuModel') or '—'))
    cpu_count = sys_info.get('cpus', 0)
    cpu_info = f'{cpu_count}x {cpu_model}' if cpu_count else cpu_model
    memory_total = sys_info.get('memoryTotal', 0)
    total_ram = format_bytes(memory_total) if memory_total else '—'
    versions = node.get('versions') or {}
    xray_ver = html.escape(str(versions.get('xray') or '—'))
    node_ver = html.escape(str(versions.get('node') or '—'))
    xray_uptime_sec = node.get('xray_uptime', 0)
    if xray_uptime_sec:
        days, rem = divmod(int(xray_uptime_sec), 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        xray_uptime_str = f'{days}d {hours}h {mins}m' if days else (f'{hours}h {mins}m' if hours else f'{mins}m')
    else:
        xray_uptime_str = '—'

    text = f"🖥️ <b>节点：{html.escape(node['name'])}</b>\n\n<b>状态：</b>\n- 在线：{status_emoji} {('Да' if node['is_node_online'] else 'Нет')}\n- X射线：{xray_emoji} {('Запущен' if node['is_xray_running'] else 'Остановлен')}\n- 已连接：{('📡 Да' if node['is_connected'] else '📵 Нет')}\n- 禁用：{('❌ Да' if node['is_disabled'] else '✅ Нет')}\n- 状态变更：{status_change}\n- 消息：{html.escape(str(node.get('last_status_message') or '—'))}\n- 正常运行时间 X 射线：{xray_uptime_str}\n\n<b>版本：</b>\n- X射线：{xray_ver}\n- 节点：{node_ver}\n\n<b>信息：</b>\n- 地址：{html.escape(node['address'])}\n- 国家：{html.escape(node['country_code'])}\n- 在线用户：{node['users_online']}\n- CPU：{cpu_info}\n- 内存：{total_ram}\n- 提供商：{html.escape(str(node.get('provider_uuid') or '—'))}\n\n<b>流量：</b>\n- 二手：{format_bytes(node['traffic_used_bytes'])}\n- 限额：{(format_bytes(node['traffic_limit_bytes']) if node['traffic_limit_bytes'] else 'Без лимита')}\n- 追踪：{('✅ Активен' if node.get('is_traffic_tracking_active') else '❌ Отключен')}\n- 重置日：{node.get('traffic_reset_day') or '—'}\n- 通知：{notify_percent}\n- 乘数：{node.get('consumption_multiplier') or 1}\n\n<b>元数据：</b>\n- 创建者：{created_at}\n- 更新：{updated_at}"

    await callback.message.edit_text(text, reply_markup=get_node_management_keyboard(node_uuid, db_user.language))
    await callback.answer()


@admin_required
@error_handler
async def manage_node(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    action, node_uuid = callback.data.split('_')[1], callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    success = await remnawave_service.manage_node(node_uuid, action)

    if success:
        action_text = {'enable': 'включена', 'disable': 'отключена', 'restart': 'перезагружена'}
        await callback.answer(f"✅ 节点 {action_text.get(action, 'обработана')}")
    else:
        await callback.answer('❌ 动作执行错误', show_alert=True)

    await show_node_details(callback, db_user, db)


@admin_required
@error_handler
async def show_node_statistics(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    node_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()

    node = await remnawave_service.get_node_details(node_uuid)

    if not node:
        await callback.answer('❌ 未找到节点', show_alert=True)
        return

    status_emoji = '🟢' if node['is_node_online'] else '🔴'
    xray_emoji = '✅' if node['is_xray_running'] else '❌'
    xray_uptime_sec = node.get('xray_uptime', 0)
    if xray_uptime_sec:
        days, rem = divmod(int(xray_uptime_sec), 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        xray_uptime_str = f'{days}d {hours}h {mins}m' if days else (f'{hours}h {mins}m' if hours else f'{mins}m')
    else:
        xray_uptime_str = '—'

    try:
        end_date = datetime.now(UTC)
        start_date = end_date - timedelta(days=7)

        node_usage = await remnawave_service.get_node_user_usage_by_range(node_uuid, start_date, end_date)

        realtime_stats = await remnawave_service.get_nodes_realtime_usage()

        node_realtime = None
        for stats in realtime_stats:
            if stats.get('nodeUuid') == node_uuid:
                node_realtime = stats
                break

        status_change = format_datetime(node['last_status_change']) if node.get('last_status_change') else '—'
        created_at = format_datetime(node['created_at']) if node.get('created_at') else '—'
        updated_at = format_datetime(node['updated_at']) if node.get('updated_at') else '—'
        notify_percent = f'{node["notify_percent"]}%' if node.get('notify_percent') is not None else '—'
        sys_info = (node.get('system') or {}).get('info', {})
        cpu_model = html.escape(str(sys_info.get('cpuModel') or '—'))
        cpu_count = sys_info.get('cpus', 0)
        cpu_info = f'{cpu_count}x {cpu_model}' if cpu_count else cpu_model
        memory_total = sys_info.get('memoryTotal', 0)
        total_ram = format_bytes(memory_total) if memory_total else '—'
        sys_stats = (node.get('system') or {}).get('stats', {})
        load_avg = sys_stats.get('loadAvg', [])
        load_str = ' / '.join(f'{v:.2f}' for v in load_avg[:3]) if load_avg else '—'
        versions = node.get('versions') or {}
        xray_ver = html.escape(str(versions.get('xray') or '—'))
        node_ver = html.escape(str(versions.get('node') or '—'))

        text = f"📊 <b>节点统计：{html.escape(node['name'])}</b>\n\n<b>状态：</b>\n- 在线：{status_emoji} {('Да' if node['is_node_online'] else 'Нет')}\n- X射线：{xray_emoji} {('Запущен' if node['is_xray_running'] else 'Остановлен')} (v{xray_ver})\n- 节点：v{node_ver}\n- 在线用户：{node['users_online']}\n- 状态变更：{status_change}\n- 消息：{html.escape(str(node.get('last_status_message') or '—'))}\n- 正常运行时间 X 射线：{xray_uptime_str}\n\n<b>资源：</b>\n- CPU：{cpu_info}\n- 内存：{total_ram}\n- 负载：{load_str}\n- 提供商：{html.escape(str(node.get('provider_uuid') or '—'))}\n\n<b>流量：</b>\n- 二手：{format_bytes(node['traffic_used_bytes'] or 0)}\n- 极限：{(format_bytes(node['traffic_limit_bytes']) if node['traffic_limit_bytes'] else 'Без лимита')}\n- 追踪：{('✅ Активен' if node.get('is_traffic_tracking_active') else '❌ Отключен')}\n- 重置日：{node.get('traffic_reset_day') or '—'}\n- 通知：{notify_percent}\n- 乘数：{node.get('consumption_multiplier') or 1}\n\n<b>元数据：</b>\n- 创建者：{created_at}\n- 更新：{updated_at}"

        if node_realtime:
            text += f"<b>入站流量：</b>\n- 下载者：{format_bytes(node_realtime.get('downloadBytes', 0))}\n- 上传者: {format_bytes(node_realtime.get('uploadBytes', 0))}\n- 总流量：{format_bytes(node_realtime.get('totalBytes', 0))}\n- 在线：{node_realtime.get('usersOnline', 0)}"

        if node_usage:
            text += '<b> 7天统计：</b>'
            total_usage = 0
            for usage in node_usage[-5:]:
                daily_usage = usage.get('total', 0)
                total_usage += daily_usage
                text += f'- {usage.get("date", "N/A")}: {format_bytes(daily_usage)}\n'

            text += f'<b> 7天总流量：</b> {format_bytes(total_usage)}'
        else:
            text += '<b> 7天统计：</b> 数据不可用'

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='🔄 刷新', callback_data=f'node_stats_{node_uuid}')],
                [types.InlineKeyboardButton(text='⬅️ 返回', callback_data=f'admin_node_manage_{node_uuid}')],
            ]
        )

        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()

    except Exception as e:
        logger.error('获取节点统计信息时出错', node_uuid=node_uuid, error=e)

        text = f"📊 <b>节点统计：{html.escape(node['name'])}</b>\n\n<b>状态：</b>\n- 在线：{status_emoji} {('Да' if node['is_node_online'] else 'Нет')}\n- X射线：{xray_emoji} {('Запущен' if node['is_xray_running'] else 'Остановлен')}\n- 在线用户：{node['users_online']}\n- 状态变更：{(format_datetime(node.get('last_status_change')) if node.get('last_status_change') else '—')}\n- 消息：{html.escape(str(node.get('last_status_message') or '—'))}\n- 正常运行时间 X 射线：{xray_uptime_str}\n\n<b>流量：</b>\n- 二手：{format_bytes(node['traffic_used_bytes'] or 0)}\n- 极限：{(format_bytes(node['traffic_limit_bytes']) if node['traffic_limit_bytes'] else 'Без лимита')}\n- 追踪：{('✅ Активен' if node.get('is_traffic_tracking_active') else '❌ Отключен')}\n- 重置日：{node.get('traffic_reset_day') or '—'}\n- 通知：{node.get('notify_percent') or '—'}\n- 乘数：{node.get('consumption_multiplier') or 1}\n\n⚠️<b>详细统计暂时无法获取</b>\n可能的原因：\n• 连接到 API 时出现问题\n• 最近添加的节点\n• 没有足够的数据来显示\n\n<b>更新：</b> {format_datetime('now')}"

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='🔄再试一次', callback_data=f'node_stats_{node_uuid}')],
                [types.InlineKeyboardButton(text='⬅️ 返回', callback_data=f'admin_node_manage_{node_uuid}')],
            ]
        )

        await callback.message.edit_text(text, reply_markup=keyboard)
        await callback.answer()


@admin_required
@error_handler
async def show_squad_details(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)

    if not squad:
        await callback.answer('❌ 找不到小队', show_alert=True)
        return

    text = f"🌐 <b> 小队：{squad['name']}</b>\n\n<b>信息：</b>\n- UUID：<code>{squad['uuid']}</code>\n- 参与者：{squad['members_count']}\n- 界外球：{squad['inbounds_count']}\n\n<b>出界：</b>"

    if squad.get('inbounds'):
        for inbound in squad['inbounds']:
            text += f'- {inbound["tag"]} ({inbound["type"]})\n'
    else:
        text += '无有效入站'

    await callback.message.edit_text(text, reply_markup=get_squad_management_keyboard(squad_uuid, db_user.language))
    await callback.answer()


@admin_required
@error_handler
async def manage_squad_action(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    parts = callback.data.split('_')
    action = parts[1]
    squad_uuid = parts[-1]

    remnawave_service = RemnaWaveService()

    if action == 'add_users':
        success = await remnawave_service.add_all_users_to_squad(squad_uuid)
        if success:
            await callback.answer('✅ 将用户添加到队列的任务')
        else:
            await callback.answer('❌ 添加用户时出错', show_alert=True)

    elif action == 'remove_users':
        success = await remnawave_service.remove_all_users_from_squad(squad_uuid)
        if success:
            await callback.answer('✅ 将用户从队列中移除的任务')
        else:
            await callback.answer('❌ 删除用户时出错', show_alert=True)

    elif action == 'delete':
        success = await remnawave_service.delete_squad(squad_uuid)
        if success:
            await callback.message.edit_text(
                '✅ 队伍已成功删除',
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 致各小队', callback_data='admin_rw_squads')]]
                ),
            )
        else:
            await callback.answer('❌ 删除小队时出错', show_alert=True)
        return

    refreshed_callback = callback.model_copy(update={'data': f'admin_squad_manage_{squad_uuid}'}).as_(callback.bot)

    await show_squad_details(refreshed_callback, db_user, db)


@admin_required
@error_handler
async def show_squad_edit_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)

    if not squad:
        await callback.answer('❌ 找不到小队', show_alert=True)
        return

    text = f"✏️ <b>编辑小队：{squad['name']}</b>\n\n<b>当前入站：</b>"

    if squad.get('inbounds'):
        for inbound in squad['inbounds']:
            text += f'✅ {inbound["tag"]} ({inbound["type"]})\n'
    else:
        text += '无有效入站'

    text += '<b>可用操作：</b>'

    await callback.message.edit_text(text, reply_markup=get_squad_edit_keyboard(squad_uuid, db_user.language))
    await callback.answer()


@admin_required
@error_handler
async def show_squad_inbounds_selection(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()

    squad = await remnawave_service.get_squad_details(squad_uuid)
    all_inbounds = await remnawave_service.get_all_inbounds()

    if not squad:
        await callback.answer('❌ 找不到小队', show_alert=True)
        return

    if not all_inbounds:
        await callback.answer('❌ 没有可用的入站', show_alert=True)
        return

    if squad_uuid not in squad_inbound_selections:
        squad_inbound_selections[squad_uuid] = {inbound['uuid'] for inbound in squad.get('inbounds', [])}

    text = f"🔧 <b>改变界内</b>\n\n<b>小队：</b> {squad['name']}\n<b>当前入站：</b> {len(squad_inbound_selections[squad_uuid])}\n\n<b>Available inbounds:</b>"

    keyboard = []

    for i, inbound in enumerate(all_inbounds[:15]):
        is_selected = inbound['uuid'] in squad_inbound_selections[squad_uuid]
        emoji = '✅' if is_selected else '☐'

        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=f'{emoji} {inbound["tag"]} ({inbound["type"]})', callback_data=f'sqd_tgl_{i}_{squad_uuid[:8]}'
                )
            ]
        )

    if len(all_inbounds) > 15:
        text += f'⚠️ 显示 {len(all_inbounds)} 的前 15 个入界'

    keyboard.extend(
        [
            [types.InlineKeyboardButton(text='💾 保存更改', callback_data=f'sqd_save_{squad_uuid[:8]}')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data=f'sqd_edit_{squad_uuid[:8]}')],
        ]
    )

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_squad_rename_form(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    squad = await remnawave_service.get_squad_details(squad_uuid)

    if not squad:
        await callback.answer('❌ 找不到小队', show_alert=True)
        return

    await state.update_data(squad_uuid=squad_uuid, squad_name=squad['name'])
    await state.set_state(SquadRenameStates.waiting_for_new_name)

    text = f"✏️ <b>重命名小队</b>\n\n<b>现名：</b> {squad['name']}\n\n📝 <b>输入新的小队名称：</b>\n\n<i>姓名要求：</i>\n• 2 到20 个字符\n• 仅限字母、数字、连字符和下划线\n• 没有空格或特殊字符\n\n使用新名称发送消息或单击“取消”退出。"

    keyboard = [[types.InlineKeyboardButton(text='❌ 取消', callback_data=f'cancel_rename_{squad_uuid}')]]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def cancel_squad_rename(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    squad_uuid = callback.data.split('_')[-1]

    await state.clear()

    refreshed_callback = callback.model_copy(update={'data': f'squad_edit_{squad_uuid}'}).as_(callback.bot)

    await show_squad_edit_menu(refreshed_callback, db_user, db)


@admin_required
@error_handler
async def process_squad_new_name(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    data = await state.get_data()
    squad_uuid = data.get('squad_uuid')
    old_name = data.get('squad_name')

    if not squad_uuid:
        await message.answer('❌ 错误：找不到小队')
        await state.clear()
        return

    new_name = message.text.strip()

    if not new_name:
        await message.answer('❌ 标题不能为空。再试一次：')
        return

    if len(new_name) < 2 or len(new_name) > 20:
        await message.answer('❌ 名称必须为 2 至 20 个字符。再试一次：')
        return

    import re

    if not re.match(r'^[A-Za-z0-9_-]+$', new_name):
        await message.answer(
            '❌ 名称只能包含字母、数字、连字符和下划线。再试一次：'
        )
        return

    if new_name == old_name:
        await message.answer('❌ 新名称与当前名称一致。输入另一个名字：')
        return

    remnawave_service = RemnaWaveService()
    success = await remnawave_service.rename_squad(squad_uuid, new_name)

    if success:
        await message.answer(
            f'✅ <b>小队成功更名！</b>\n\n<b>旧名称：</b> {old_name}\n<b>新名称：</b> {new_name}',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text='📋 球队详情', callback_data=f'admin_squad_manage_{squad_uuid}'
                        )
                    ],
                    [types.InlineKeyboardButton(text='⬅️ 致各小队', callback_data='admin_rw_squads')],
                ]
            ),
        )
        await state.clear()
    else:
        await message.answer(
            '❌ <b> 球队更名错误</b>\n\n可能的原因：\n• 已存在同名小队\n• 连接到 API 时出现问题\n• 权利不足\n\n尝试使用不同的名称：',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='❌ 取消', callback_data=f'cancel_rename_{squad_uuid}')]
                ]
            ),
        )


@admin_required
@error_handler
async def toggle_squad_inbound(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    parts = callback.data.split('_')
    inbound_index = int(parts[2])
    short_squad_uuid = parts[3]

    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()

    full_squad_uuid = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            break

    if not full_squad_uuid:
        await callback.answer('❌ 找不到小队', show_alert=True)
        return

    all_inbounds = await remnawave_service.get_all_inbounds()
    if inbound_index >= len(all_inbounds):
        await callback.answer('❌ 未找到入站', show_alert=True)
        return

    selected_inbound = all_inbounds[inbound_index]

    if full_squad_uuid not in squad_inbound_selections:
        squad_inbound_selections[full_squad_uuid] = set()

    if selected_inbound['uuid'] in squad_inbound_selections[full_squad_uuid]:
        squad_inbound_selections[full_squad_uuid].remove(selected_inbound['uuid'])
        await callback.answer(f"➖ 删除：{selected_inbound['tag']}")
    else:
        squad_inbound_selections[full_squad_uuid].add(selected_inbound['uuid'])
        await callback.answer(f"➕ 添加：{selected_inbound['tag']}")

    text = f"🔧 <b>改变界内</b>\n\n<b>小队：</b> {(squads[0]['name'] if squads else 'Неизвестно')}\n<b>入界选择：</b> {len(squad_inbound_selections[full_squad_uuid])}\n\n<b>可用入界：</b>"

    keyboard = []
    for i, inbound in enumerate(all_inbounds[:15]):
        is_selected = inbound['uuid'] in squad_inbound_selections[full_squad_uuid]
        emoji = '✅' if is_selected else '☐'

        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=f'{emoji} {inbound["tag"]} ({inbound["type"]})',
                    callback_data=f'sqd_tgl_{i}_{short_squad_uuid}',
                )
            ]
        )

    keyboard.extend(
        [
            [types.InlineKeyboardButton(text='💾 保存更改', callback_data=f'sqd_save_{short_squad_uuid}')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data=f'sqd_edit_{short_squad_uuid}')],
        ]
    )

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))


@admin_required
@error_handler
async def save_squad_inbounds(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    short_squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()

    full_squad_uuid = None
    squad_name = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            squad_name = squad['name']
            break

    if not full_squad_uuid:
        await callback.answer('❌ 找不到小队', show_alert=True)
        return

    selected_inbounds = squad_inbound_selections.get(full_squad_uuid, set())

    try:
        success = await remnawave_service.update_squad_inbounds(full_squad_uuid, list(selected_inbounds))

        if success:
            squad_inbound_selections.pop(full_squad_uuid, None)

            await callback.message.edit_text(
                f'✅ <b> 球队入界更新</b>\n\n<b>小队：</b> {squad_name}\n<b> 入界次数：</b> {len(selected_inbounds)}',
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [types.InlineKeyboardButton(text='⬅️ 致各小队', callback_data='admin_rw_squads')],
                        [
                            types.InlineKeyboardButton(
                                text='📋 球队详情', callback_data=f'admin_squad_manage_{full_squad_uuid}'
                            )
                        ],
                    ]
                ),
            )
            await callback.answer('✅ 更改已保存！')
        else:
            await callback.answer('❌ 保存更改时出错', show_alert=True)

    except Exception as e:
        logger.error('Error saving squad inbounds', error=e)
        await callback.answer('❌ 保存时出错', show_alert=True)


@admin_required
@error_handler
async def show_squad_edit_menu_short(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    short_squad_uuid = callback.data.split('_')[-1]

    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()

    full_squad_uuid = None
    for squad in squads:
        if squad['uuid'].startswith(short_squad_uuid):
            full_squad_uuid = squad['uuid']
            break

    if not full_squad_uuid:
        await callback.answer('❌ 找不到小队', show_alert=True)
        return

    refreshed_callback = callback.model_copy(update={'data': f'squad_edit_{full_squad_uuid}'}).as_(callback.bot)

    await show_squad_edit_menu(refreshed_callback, db_user, db)


@admin_required
@error_handler
async def start_squad_creation(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    await state.set_state(SquadCreateStates.waiting_for_name)

    text = '➕ <b>创建新小队</b>\n\n<b>第 1 步（共 2 步）：小队名称</b>\n\n📝 <b>输入新小队的名称：</b>\n\n<i>姓名要求：</i>\n• 2 到20 个字符\n• 仅限字母、数字、连字符和下划线\n• 没有空格或特殊字符\n\n发送包含该名称的消息或单击“取消”退出。'

    keyboard = [[types.InlineKeyboardButton(text='❌ 取消', callback_data='cancel_squad_create')]]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def process_squad_name(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    squad_name = message.text.strip()

    if not squad_name:
        await message.answer('❌ 标题不能为空。再试一次：')
        return

    if len(squad_name) < 2 or len(squad_name) > 20:
        await message.answer('❌ 名称必须为 2 至 20 个字符。再试一次：')
        return

    import re

    if not re.match(r'^[A-Za-z0-9_-]+$', squad_name):
        await message.answer(
            '❌ 名称只能包含字母、数字、连字符和下划线。再试一次：'
        )
        return

    await state.update_data(squad_name=squad_name)
    await state.set_state(SquadCreateStates.selecting_inbounds)

    user_id = message.from_user.id
    squad_create_data[user_id] = {'name': squad_name, 'selected_inbounds': set()}

    remnawave_service = RemnaWaveService()
    all_inbounds = await remnawave_service.get_all_inbounds()

    if not all_inbounds:
        await message.answer(
            '❌ <b>无可用入界</b>\n\n要创建一支球队，您必须至少有一名入队球员。',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 致各小队', callback_data='admin_rw_squads')]]
            ),
        )
        await state.clear()
        return

    text = f'➕ <b>创建小队：{squad_name}</b>\n\n<b>第 2 步（共 2 步）：选择入站</b>\n\n<b>入界选择：</b> 0\n\n<b>可用入界：</b>'

    keyboard = []

    for i, inbound in enumerate(all_inbounds[:15]):
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=f'☐ {inbound["tag"]} ({inbound["type"]})', callback_data=f'create_tgl_{i}'
                )
            ]
        )

    if len(all_inbounds) > 15:
        text += f'⚠️ 显示 {len(all_inbounds)} 的前 15 个入界'

    keyboard.extend(
        [
            [types.InlineKeyboardButton(text='✅ 创建小队', callback_data='create_squad_finish')],
            [types.InlineKeyboardButton(text='❌ 取消', callback_data='cancel_squad_create')],
        ]
    )

    await message.answer(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))


@admin_required
@error_handler
async def toggle_create_inbound(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    inbound_index = int(callback.data.split('_')[-1])
    user_id = callback.from_user.id

    if user_id not in squad_create_data:
        await callback.answer('❌ 错误：找不到会话数据', show_alert=True)
        await state.clear()
        return

    remnawave_service = RemnaWaveService()
    all_inbounds = await remnawave_service.get_all_inbounds()

    if inbound_index >= len(all_inbounds):
        await callback.answer('❌ 未找到入站', show_alert=True)
        return

    selected_inbound = all_inbounds[inbound_index]
    selected_inbounds = squad_create_data[user_id]['selected_inbounds']

    if selected_inbound['uuid'] in selected_inbounds:
        selected_inbounds.remove(selected_inbound['uuid'])
        await callback.answer(f"➖ 删除：{selected_inbound['tag']}")
    else:
        selected_inbounds.add(selected_inbound['uuid'])
        await callback.answer(f"➕ 添加：{selected_inbound['tag']}")

    squad_name = squad_create_data[user_id]['name']

    text = f'➕ <b>创建小队：{squad_name}</b>\n\n<b>第 2 步（共 2 步）：选择入站</b>\n\n<b>入界选择：</b> {len(selected_inbounds)}\n\n<b>可用入界：</b>'

    keyboard = []

    for i, inbound in enumerate(all_inbounds[:15]):
        is_selected = inbound['uuid'] in selected_inbounds
        emoji = '✅' if is_selected else '☐'

        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=f'{emoji} {inbound["tag"]} ({inbound["type"]})', callback_data=f'create_tgl_{i}'
                )
            ]
        )

    keyboard.extend(
        [
            [types.InlineKeyboardButton(text='✅ 创建小队', callback_data='create_squad_finish')],
            [types.InlineKeyboardButton(text='❌ 取消', callback_data='cancel_squad_create')],
        ]
    )

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))


@admin_required
@error_handler
async def finish_squad_creation(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    user_id = callback.from_user.id

    if user_id not in squad_create_data:
        await callback.answer('❌ 错误：找不到会话数据', show_alert=True)
        await state.clear()
        return

    squad_name = squad_create_data[user_id]['name']
    selected_inbounds = list(squad_create_data[user_id]['selected_inbounds'])

    if not selected_inbounds:
        await callback.answer('❌ 您必须至少选择一项入境', show_alert=True)
        return

    remnawave_service = RemnaWaveService()
    success = await remnawave_service.create_squad(squad_name, selected_inbounds)

    squad_create_data.pop(user_id, None)
    await state.clear()

    if success:
        await callback.message.edit_text(
            f'✅ <b>小队创建成功！</b>\n\n<b>名称：</b> {squad_name}\n<b> 入界次数：</b> {len(selected_inbounds)}\n\n小队已准备好使用！',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='📋 阵容名单', callback_data='admin_rw_squads')],
                    [types.InlineKeyboardButton(text='⬅️ 至面板Remnawave', callback_data='admin_remnawave')],
                ]
            ),
        )
        await callback.answer('✅ 小队已创建！')
    else:
        await callback.message.edit_text(
            f'❌ <b> 创建小队时出错 </b>\n\n<b>名称：</b> {squad_name}\n\n可能的原因：\n• 已存在同名小队\n• 连接到 API 时出现问题\n• 权利不足\n• 错误的入界',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🔄再试一次', callback_data='admin_squad_create')],
                    [types.InlineKeyboardButton(text='⬅️ 致各小队', callback_data='admin_rw_squads')],
                ]
            ),
        )
        await callback.answer('❌ 创建小队时出错', show_alert=True)


@admin_required
@error_handler
async def cancel_squad_creation(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    user_id = callback.from_user.id

    squad_create_data.pop(user_id, None)
    await state.clear()

    await show_squads_management(callback, db_user, db)


@admin_required
@error_handler
async def restart_all_nodes(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    remnawave_service = RemnaWaveService()
    success = await remnawave_service.restart_all_nodes()

    if success:
        await callback.message.edit_text(
            '✅ 重启所有节点的命令已发送',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 到节点', callback_data='admin_rw_nodes')]]
            ),
        )
    else:
        await callback.message.edit_text(
            '❌ 重启节点时出错',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 到节点', callback_data='admin_rw_nodes')]]
            ),
        )

    await callback.answer()


@admin_required
@error_handler
async def show_sync_options(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    status = remnawave_sync_service.get_status()
    times_text = ', '.join(t.strftime('%H:%M') for t in status.times) if status.times else '—'
    next_run_text = format_datetime(status.next_run) if status.next_run else '—'
    last_result = '—'

    if status.last_run_finished_at:
        result_icon = '✅' if status.last_run_success else '❌'
        result_label = 'успешно' if status.last_run_success else 'с ошибками'
        finished_text = format_datetime(status.last_run_finished_at)
        last_result = f'{result_icon} {result_label} ({finished_text})'
    elif status.last_run_started_at:
        last_result = f'⏳ Запущено {format_datetime(status.last_run_started_at)}'

    status_lines = [
        f'⚙️ Статус: {"✅ Включена" if status.enabled else "❌ Отключена"}',
        f'🕒 Расписание: {times_text}',
        f'📅 Следующий запуск: {next_run_text if status.enabled else "—"}',
        f'📊 Последний запуск: {last_result}',
    ]

    text = (
        '🔄 <b>Синхронизация с Remnawave</b>\n\n'
        '🔄 <b>Полная синхронизация выполняет:</b>\n'
        '• Создание новых пользователей из панели в боте\n'
        '• Обновление данных существующих пользователей\n'
        '• Деактивация подписок пользователей, отсутствующих в панели\n'
        '• Сохранение балансов пользователей\n'
        '• ⏱️ Время выполнения: 2-5 минут\n\n'
        '⚠️ <b>Важно:</b>\n'
        '• Во время синхронизации не выполняйте другие операции\n'
        '• При полной синхронизации подписки пользователей, отсутствующих в панели, будут деактивированы\n'
        '• Рекомендуется делать полную синхронизацию ежедневно\n'
        '• Баланс пользователей НЕ удаляется\n\n'
        '⬆️ <b>Обратная синхронизация:</b>\n'
        '• Отправляет активных пользователей из бота в панель\n'
        '• Используйте при сбоях панели или для восстановления данных\n\n' + '\n'.join(status_lines)
    )

    keyboard = [
        [
            types.InlineKeyboardButton(
                text='🔄 开始完全同步',
                callback_data='sync_all_users',
            )
        ],
        [
            types.InlineKeyboardButton(
                text='⬆️ 同步到面板',
                callback_data='sync_to_panel',
            )
        ],
        [
            types.InlineKeyboardButton(
                text='⚙️ 自动同步设置',
                callback_data='admin_rw_auto_sync',
            )
        ],
        [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_remnawave')],
    ]

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_auto_sync_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    status = remnawave_sync_service.get_status()
    text, keyboard = _build_auto_sync_view(status)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_auto_sync_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    new_value = not bool(settings.REMNAWAVE_AUTO_SYNC_ENABLED)
    await bot_configuration_service.set_value(
        db,
        'REMNAWAVE_AUTO_SYNC_ENABLED',
        new_value,
    )
    await db.commit()

    status = remnawave_sync_service.get_status()
    text, keyboard = _build_auto_sync_view(status)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer(f"自动同步 {('включена' if new_value else 'отключена')}")


@admin_required
@error_handler
async def prompt_auto_sync_schedule(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    status = remnawave_sync_service.get_status()
    current_schedule = ', '.join(t.strftime('%H:%M') for t in status.times) if status.times else '—'

    instructions = (
        '🕒 <b>Настройка расписания автосинхронизации</b>\n\n'
        'Укажите время запуска через запятую или с новой строки в формате HH:MM.\n'
        f'Текущее расписание: <code>{current_schedule}</code>\n\n'
        'Примеры: <code>03:00, 15:30</code> или <code>00:15\n06:00\n18:45</code>\n\n'
        'Отправьте <b>отмена</b>, чтобы вернуться без изменений.'
    )

    await state.set_state(RemnaWaveSyncStates.waiting_for_schedule)
    await state.update_data(
        auto_sync_message_id=callback.message.message_id,
        auto_sync_message_chat_id=callback.message.chat.id,
    )

    await callback.message.edit_text(
        instructions,
        parse_mode='HTML',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text='❌ 取消',
                        callback_data='remnawave_auto_sync_cancel',
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def cancel_auto_sync_schedule(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    status = remnawave_sync_service.get_status()
    text, keyboard = _build_auto_sync_view(status)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback.answer('时间表更改已取消')


@admin_required
@error_handler
async def run_auto_sync_now(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    if remnawave_sync_service.get_status().is_running:
        await callback.answer('同步已在进行中', show_alert=True)
        return

    await state.clear()
    await callback.message.edit_text(
        '🔄 开始自动同步...\n\n请稍候，这可能需要几分钟。',
        parse_mode='HTML',
    )
    await callback.answer('自动同步已开始')

    result = await remnawave_sync_service.run_sync_now(reason='manual')
    status = remnawave_sync_service.get_status()
    base_text, keyboard = _build_auto_sync_view(status)

    if not result.get('started'):
        await callback.message.edit_text(
            '⚠️ <b>Синхронизация уже выполняется</b>\n\n' + base_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
        return

    if result.get('success'):
        user_stats = result.get('user_stats') or {}
        server_stats = result.get('server_stats') or {}
        summary = (
            '✅ <b>Синхронизация завершена</b>\n'
            f'👥 Пользователи: создано {user_stats.get("created", 0)}, обновлено {user_stats.get("updated", 0)}, '
            f'деактивировано {user_stats.get("deleted", user_stats.get("deactivated", 0))}, ошибок {user_stats.get("errors", 0)}\n'
            f'🌐 Серверы: создано {server_stats.get("created", 0)}, обновлено {server_stats.get("updated", 0)}, удалено {server_stats.get("removed", 0)}\n\n'
        )
        final_text = summary + base_text
        await callback.message.edit_text(
            final_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
    else:
        error_text = result.get('error') or 'Неизвестная ошибка'
        summary = f'❌ <b>Синхронизация завершилась с ошибкой</b>\nПричина: {error_text}\n\n'
        await callback.message.edit_text(
            summary + base_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def save_auto_sync_schedule(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    text = (message.text or '').strip()
    data = await state.get_data()

    if text.lower() in {'отмена', 'cancel'}:
        await state.clear()
        status = remnawave_sync_service.get_status()
        view_text, keyboard = _build_auto_sync_view(status)
        message_id = data.get('auto_sync_message_id')
        chat_id = data.get('auto_sync_message_chat_id', message.chat.id)
        if message_id:
            await message.bot.edit_message_text(
                view_text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboard,
                parse_mode='HTML',
            )
        else:
            await message.answer(
                view_text,
                reply_markup=keyboard,
                parse_mode='HTML',
            )
        await message.answer('时间表设置已取消')
        return

    parsed_times = settings.parse_daily_time_list(text)

    if not parsed_times:
        await message.answer(
            '❌ 无法识别时间。使用 HH:MM 格式，例如 03:00 或 18:45。',
        )
        return

    normalized_value = ', '.join(t.strftime('%H:%M') for t in parsed_times)
    await bot_configuration_service.set_value(
        db,
        'REMNAWAVE_AUTO_SYNC_TIMES',
        normalized_value,
    )
    await db.commit()

    status = remnawave_sync_service.get_status()
    view_text, keyboard = _build_auto_sync_view(status)
    message_id = data.get('auto_sync_message_id')
    chat_id = data.get('auto_sync_message_chat_id', message.chat.id)

    if message_id:
        await message.bot.edit_message_text(
            view_text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=keyboard,
            parse_mode='HTML',
        )
    else:
        await message.answer(
            view_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )

    await state.clear()
    await message.answer('✅ 自动同步时间表已更新')


@admin_required
@error_handler
async def sync_all_users(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Выполняет полную синхронизацию всех пользователей"""

    progress_text = """
🔄 <b>Выполняется полная синхронизация...</b>

📋 Этапы:
• Загрузка ВСЕХ пользователей из панели Remnawave
• Создание новых пользователей в боте
• Обновление существующих пользователей
• Деактивация подписок отсутствующих пользователей
• Сохранение балансов

⏳ Пожалуйста, подождите...
"""

    await callback.message.edit_text(progress_text, reply_markup=None)

    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.sync_users_from_panel(db, 'all')

    total_operations = stats['created'] + stats['updated'] + stats.get('deleted', 0)

    if stats['errors'] == 0:
        status_emoji = '✅'
        status_text = '成功完成'
    elif stats['errors'] < total_operations:
        status_emoji = '⚠️'
        status_text = '已完成但有警告'
    else:
        status_emoji = '❌'
        status_text = '完成但有错误'

    text = f"{status_emoji} <b>全同步 {status_text}</b>\n\n📊 <b> 结果：</b>\n• 🆕 创建者：{stats['created']}\n• 🔄 更新：{stats['updated']}\n• 🗑️ 已停用：{stats.get('deleted', 0)}\n• ❌ 错误：{stats['errors']}"

    if stats.get('deleted', 0) > 0:
        text += '🗑️ <b>已停用订阅：</b>\n用户的订阅量\nRemnawave 面板中缺失。\n💰 用户余额被保存。'

    if stats['errors'] > 0:
        text += '⚠️<b>注意：</b>\n某些操作完成时出现错误。\n检查日志以获取详细信息。'

    text += '💡 <b>推荐：</b>\n• 完全同步完成\n• 建议每天运行一次\n• 面板中的所有用户均已同步'

    keyboard = []

    if stats['errors'] > 0:
        keyboard.append([types.InlineKeyboardButton(text='🔄 重新同步', callback_data='sync_all_users')])

    keyboard.extend(
        [
            [
                types.InlineKeyboardButton(text='📊 系统统计', callback_data='admin_rw_system'),
                types.InlineKeyboardButton(text='🌐 节点', callback_data='admin_rw_nodes'),
            ],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_remnawave')],
        ]
    )

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def sync_users_to_panel(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    await callback.message.edit_text(
        '⬆️ 机器人数据正在同步到 Remnawave 面板中...\n\n这可能需要几分钟。',
        reply_markup=None,
    )

    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.sync_users_to_panel(db)

    if stats['errors'] == 0:
        status_emoji = '✅'
        status_text = '成功完成'
    else:
        status_emoji = '⚠️' if (stats['created'] + stats['updated']) > 0 else '❌'
        status_text = 'завершена с предупреждениями' if status_emoji == '⚠️' else 'завершена с ошибками'

    text = (
        f"{status_emoji} <b>同步至面板 {status_text}</b>\n\n📊 <b>结果：</b>\n• 🆕 创建者：{stats['created']}\n• 🔄 更新：{stats['updated']}\n• ❌ 错误：{stats['errors']}"
    )

    keyboard = [
        [types.InlineKeyboardButton(text='🔄 重试', callback_data='sync_to_panel')],
        [types.InlineKeyboardButton(text='🔄 完全同步', callback_data='sync_all_users')],
        [types.InlineKeyboardButton(text='⬅️ 同步', callback_data='admin_rw_sync')],
    ]

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_sync_recommendations(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.message.edit_text('🔍 正在分析同步状态...', reply_markup=None)

    remnawave_service = RemnaWaveService()
    recommendations = await remnawave_service.get_sync_recommendations(db)

    priority_emoji = {'low': '🟢', 'medium': '🟡', 'high': '🔴'}

    text = f"💡 <b>同步建议</b>\n\n{priority_emoji.get(recommendations['priority'], '🟢')} <b>优先级：</b> {recommendations['priority'].upper()}\n⏱️ <b> 运行时间：</b> {recommendations['estimated_time']}\n\n<b> 推荐操作：</b>"

    if recommendations['sync_type'] == 'all':
        text += '🔄 完全同步'
    elif recommendations['sync_type'] == 'update_only':
        text += '📈 数据更新'
    elif recommendations['sync_type'] == 'new_only':
        text += '🆕 新内容同步'
    else:
        text += '✅ 无需同步'

    text += '<b> 原因：</b>'
    for reason in recommendations['reasons']:
        text += f'• {reason}\n'

    keyboard = []

    if recommendations['should_sync'] and recommendations['sync_type'] != 'none':
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text='✅ 遵循建议',
                    callback_data=f'sync_{recommendations["sync_type"]}_users'
                    if recommendations['sync_type'] != 'update_only'
                    else 'sync_update_data',
                )
            ]
        )

    keyboard.extend(
        [
            [types.InlineKeyboardButton(text='🔄 其他选择', callback_data='admin_rw_sync')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_remnawave')],
        ]
    )

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def validate_subscriptions(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.message.edit_text(
        '🔍 订阅正在验证中...\n\n我们检查数据，可能需要几分钟。', reply_markup=None
    )

    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.validate_and_fix_subscriptions(db)

    if stats['errors'] == 0:
        status_emoji = '✅'
        status_text = '成功完成'
    else:
        status_emoji = '⚠️'
        status_text = '完成但有错误'

    text = f"{status_emoji} <b>验证 {status_text}</b>\n\n📊 <b>结果：</b>\n• 🔍 已验证订阅：{stats['checked']}\n• 🔧 固定订阅：{stats['fixed']}\n•⚠️发现问题：{stats['issues_found']}\n• ❌ 错误：{stats['errors']}"

    if stats['fixed'] > 0:
        text += '✅ <b> 已修复问题：</b>'
        text += '• 过期订阅的状态'
        text += '• 缺失数据Remnawave'
        text += '• 不正确的流量限制'
        text += '• 设备设置'

    if stats['errors'] > 0:
        text += '⚠️ 处理过程中检测到错误。\n检查日志以获取详细信息。'

    keyboard = [
        [types.InlineKeyboardButton(text='🔄重复验证', callback_data='sync_validate')],
        [types.InlineKeyboardButton(text='🔄 完全同步', callback_data='sync_all_users')],
        [types.InlineKeyboardButton(text='⬅️ 同步', callback_data='admin_rw_sync')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def cleanup_subscriptions(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.message.edit_text(
        '🧹 不相关订阅正在清除中...\n\n我们删除不在面板中的用户的订阅。',
        reply_markup=None,
    )

    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.cleanup_orphaned_subscriptions(db)

    if stats['errors'] == 0:
        status_emoji = '✅'
        status_text = '成功完成'
    else:
        status_emoji = '⚠️'
        status_text = '完成但有错误'

    text = f"{status_emoji} <b>清洁 {status_text}</b>\n\n📊 <b>结果：</b>\n• 🔍 已验证订阅：{stats['checked']}\n• 🗑️ 已停用：{stats['deactivated']}\n• ❌ 错误：{stats['errors']}"

    if stats['deactivated'] > 0:
        text += '🗑️ <b>已停用订阅：</b>'
        text += '已禁用以下用户的订阅'
        text += '面板 Remnawave 缺失。'
    else:
        text += '✅ 所有订阅都是最新的！\n未找到过时的订阅。'

    if stats['errors'] > 0:
        text += '⚠️ 处理过程中检测到错误。\n检查日志以获取详细信息。'

    keyboard = [
        [types.InlineKeyboardButton(text='🔄重复清洁', callback_data='sync_cleanup')],
        [types.InlineKeyboardButton(text='🔍 验证', callback_data='sync_validate')],
        [types.InlineKeyboardButton(text='⬅️ 同步', callback_data='admin_rw_sync')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def force_cleanup_all_orphaned_users(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.message.edit_text(
        '🗑️强制清除所有不在面板中的用户...\n\n⚠️警告：这将完全删除所有用户数据！\n📊 包括：交易、推荐收入、促销代码、服务器、余额\n\n⏳请稍候...',
        reply_markup=None,
    )

    remnawave_service = RemnaWaveService()
    stats = await remnawave_service.cleanup_orphaned_subscriptions(db)

    if stats['errors'] == 0:
        status_emoji = '✅'
        status_text = '成功完成'
    else:
        status_emoji = '⚠️'
        status_text = '完成但有错误'

    text = f"{status_emoji} <b>强制清洗 {status_text}</b>\n\n📊 <b>结果：</b>\n• 🔍 已验证订阅：{stats['checked']}\n• 🗑️完全通关：{stats['deactivated']}\n• ❌ 错误：{stats['errors']}"

    if stats['deactivated'] > 0:
        text += '🗑️<b>完全清除数据：</b>\n• 订阅已重置为初始状态\n• 所有用户交易均被删除\n• 所有推荐收入均已删除\n• 删除了促销代码的使用\n• 余额重置为零\n• 连接的服务器已删除\n• 将 HWID 设备重置为 Remnawave\n• 已清洁 Remnawave UUID'
    else:
        text += '✅ 未发现过时的订阅！\n所有用户都与面板同步。'

    if stats['errors'] > 0:
        text += '⚠️ 处理过程中检测到错误。\n检查日志以获取详细信息。'

    keyboard = [
        [types.InlineKeyboardButton(text='🔄重复清洁', callback_data='force_cleanup_orphaned')],
        [types.InlineKeyboardButton(text='🔄 完全同步', callback_data='sync_all_users')],
        [types.InlineKeyboardButton(text='⬅️ 同步', callback_data='admin_rw_sync')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def confirm_force_cleanup(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    text = '⚠️<b>注意！危险操作！</b>\n\n🗑️<b>强制清洗将彻底去除：</b>\n• 不在面板中的用户的所有交易\n• 所有推荐收入和关系\n• 所有促销代码的使用\n• 所有连接的订阅服务器\n• 所有余额（重置为零）\n• Remnawave 中的所有 HWID 设备\n• 所有 Remnawave UUID 和链接\n\n⚡ <b>这个动作是不可逆转的！</b>\n\n仅在以下情况下使用：\n• 定期同步没有帮助\n• 有必要彻底清除“垃圾”数据\n• 从面板中大量删除用户后\n\n❓ <b>您确定要继续吗？</b>'

    keyboard = [
        [types.InlineKeyboardButton(text='🗑️ 是的，清除一切', callback_data='force_cleanup_orphaned')],
        [types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_rw_sync')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def sync_users(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    sync_type = callback.data.split('_')[-2] + '_' + callback.data.split('_')[-1]

    progress_text = '🔄 Выполняется синхронизация...\n\n'

    if sync_type == 'all_users':
        progress_text += '📋 Тип: Полная синхронизация\n'
        progress_text += '• Создание новых пользователей\n'
        progress_text += '• Обновление существующих\n'
        progress_text += '• Удаление неактуальных подписок\n'
    elif sync_type == 'new_users':
        progress_text += '📋 Тип: Только новые пользователи\n'
        progress_text += '• Создание пользователей из панели\n'
    elif sync_type == 'update_data':
        progress_text += '📋 Тип: Обновление данных\n'
        progress_text += '• Обновление информации о трафике\n'
        progress_text += '• Синхронизация подписок\n'

    progress_text += '\n⏳ Пожалуйста, подождите...'

    await callback.message.edit_text(progress_text, reply_markup=None)

    remnawave_service = RemnaWaveService()

    sync_map = {'all_users': 'all', 'new_users': 'new_only', 'update_data': 'update_only'}

    stats = await remnawave_service.sync_users_from_panel(db, sync_map.get(sync_type, 'all'))

    total_operations = stats['created'] + stats['updated'] + stats.get('deleted', 0)
    stats['created'] + stats['updated'] + stats.get('deleted', 0)

    if stats['errors'] == 0:
        status_emoji = '✅'
        status_text = '成功完成'
    elif stats['errors'] < total_operations:
        status_emoji = '⚠️'
        status_text = '已完成但有警告'
    else:
        status_emoji = '❌'
        status_text = '完成但有错误'

    text = f'{status_emoji} <b>同步 {status_text}</b>\n\n📊 <b> 结果：</b>'

    if sync_type == 'all_users':
        text += f"• 🆕 创建者：{stats['created']}"
        text += f"• 🔄 更新：{stats['updated']}"
        if 'deleted' in stats:
            text += f"• 🗑️ 已删除：{stats['deleted']}"
        text += f"• ❌ 错误：{stats['errors']}"
    elif sync_type == 'new_users':
        text += f"• 🆕 创建者：{stats['created']}"
        text += f"• ❌ 错误：{stats['errors']}"
        if stats['created'] == 0 and stats['errors'] == 0:
            text += '💡 没有发现新用户'
    elif sync_type == 'update_data':
        text += f"• 🔄 更新：{stats['updated']}"
        text += f"• ❌ 错误：{stats['errors']}"
        if stats['updated'] == 0 and stats['errors'] == 0:
            text += '💡所有数据均为最新数据'

    if stats['errors'] > 0:
        text += '⚠️<b>注意：</b>'
        text += '某些操作完成时出现错误。'
        text += '检查日志以获取详细信息。'

    if sync_type == 'all_users' and 'deleted' in stats and stats['deleted'] > 0:
        text += '🗑️ <b>远程订阅：</b>'
        text += '用户订阅已停用'
        text += 'Remnawave 面板中缺少这些。'

    text += '💡 <b>推荐：</b>'
    if sync_type == 'all_users':
        text += '• 完全同步完成'
        text += '• 建议每天运行一次'
    elif sync_type == 'new_users':
        text += '• 新用户同步'
        text += '• 批量添加时使用'
    elif sync_type == 'update_data':
        text += '• 流量数据更新'
        text += '• 运行以更新统计数据'

    keyboard = []

    if stats['errors'] > 0:
        keyboard.append([types.InlineKeyboardButton(text='🔄 重新同步', callback_data=callback.data)])

    if sync_type != 'all_users':
        keyboard.append([types.InlineKeyboardButton(text='🔄 完全同步', callback_data='sync_all_users')])

    keyboard.extend(
        [
            [
                types.InlineKeyboardButton(text='📊 系统统计', callback_data='admin_rw_system'),
                types.InlineKeyboardButton(text='🌐 节点', callback_data='admin_rw_nodes'),
            ],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_remnawave')],
        ]
    )

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_squads_management(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    remnawave_service = RemnaWaveService()
    squads = await remnawave_service.get_all_squads()

    text = '🌍<b>球队管理</b>'
    keyboard = []

    if squads:
        for squad in squads:
            text += f'🔹 <b>{squad["name"]}</b>\n'
            text += f"👥 参加者：{squad['members_count']}"
            text += f"📡 界外球：{squad['inbounds_count']}"

            keyboard.append(
                [
                    types.InlineKeyboardButton(
                        text=f'⚙️ {squad["name"]}', callback_data=f'admin_squad_manage_{squad["uuid"]}'
                    )
                ]
            )
    else:
        text += '没有找到队伍'

    keyboard.extend(
        [
            [types.InlineKeyboardButton(text='➕ 创建小队', callback_data='admin_squad_create')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_remnawave')],
        ]
    )

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_remnawave_menu, F.data == 'admin_remnawave')
    dp.callback_query.register(show_system_stats, F.data == 'admin_rw_system')
    dp.callback_query.register(show_traffic_stats, F.data == 'admin_rw_traffic')
    dp.callback_query.register(show_nodes_management, F.data == 'admin_rw_nodes')
    dp.callback_query.register(show_node_details, F.data.startswith('admin_node_manage_'))
    dp.callback_query.register(show_node_statistics, F.data.startswith('node_stats_'))
    dp.callback_query.register(manage_node, F.data.startswith('node_enable_'))
    dp.callback_query.register(manage_node, F.data.startswith('node_disable_'))
    dp.callback_query.register(manage_node, F.data.startswith('node_restart_'))
    dp.callback_query.register(restart_all_nodes, F.data == 'admin_restart_all_nodes')
    dp.callback_query.register(show_sync_options, F.data == 'admin_rw_sync')
    dp.callback_query.register(show_auto_sync_settings, F.data == 'admin_rw_auto_sync')
    dp.callback_query.register(toggle_auto_sync_setting, F.data == 'remnawave_auto_sync_toggle')
    dp.callback_query.register(prompt_auto_sync_schedule, F.data == 'remnawave_auto_sync_times')
    dp.callback_query.register(cancel_auto_sync_schedule, F.data == 'remnawave_auto_sync_cancel')
    dp.callback_query.register(run_auto_sync_now, F.data == 'remnawave_auto_sync_run')
    dp.callback_query.register(sync_all_users, F.data == 'sync_all_users')
    dp.callback_query.register(sync_users_to_panel, F.data == 'sync_to_panel')
    dp.callback_query.register(show_squad_migration_menu, F.data == 'admin_rw_migration')
    dp.callback_query.register(paginate_migration_source, F.data.startswith('admin_migration_source_page_'))
    dp.callback_query.register(handle_migration_source_selection, F.data.startswith('admin_migration_source_'))
    dp.callback_query.register(paginate_migration_target, F.data.startswith('admin_migration_target_page_'))
    dp.callback_query.register(handle_migration_target_selection, F.data.startswith('admin_migration_target_'))
    dp.callback_query.register(change_migration_target, F.data == 'admin_migration_change_target')
    dp.callback_query.register(confirm_squad_migration, F.data == 'admin_migration_confirm')
    dp.callback_query.register(cancel_squad_migration, F.data == 'admin_migration_cancel')
    dp.callback_query.register(handle_migration_page_info, F.data == 'admin_migration_page_info')
    dp.callback_query.register(show_squads_management, F.data == 'admin_rw_squads')
    dp.callback_query.register(show_squad_details, F.data.startswith('admin_squad_manage_'))
    dp.callback_query.register(manage_squad_action, F.data.startswith('squad_add_users_'))
    dp.callback_query.register(manage_squad_action, F.data.startswith('squad_remove_users_'))
    dp.callback_query.register(manage_squad_action, F.data.startswith('squad_delete_'))
    dp.callback_query.register(
        show_squad_edit_menu, F.data.startswith('squad_edit_') & ~F.data.startswith('squad_edit_inbounds_')
    )
    dp.callback_query.register(show_squad_inbounds_selection, F.data.startswith('squad_edit_inbounds_'))
    dp.callback_query.register(show_squad_rename_form, F.data.startswith('squad_rename_'))
    dp.callback_query.register(cancel_squad_rename, F.data.startswith('cancel_rename_'))
    dp.callback_query.register(toggle_squad_inbound, F.data.startswith('sqd_tgl_'))
    dp.callback_query.register(save_squad_inbounds, F.data.startswith('sqd_save_'))
    dp.callback_query.register(show_squad_edit_menu_short, F.data.startswith('sqd_edit_'))
    dp.callback_query.register(start_squad_creation, F.data == 'admin_squad_create')
    dp.callback_query.register(cancel_squad_creation, F.data == 'cancel_squad_create')
    dp.callback_query.register(toggle_create_inbound, F.data.startswith('create_tgl_'))
    dp.callback_query.register(finish_squad_creation, F.data == 'create_squad_finish')

    dp.message.register(process_squad_new_name, SquadRenameStates.waiting_for_new_name, F.text)

    dp.message.register(process_squad_name, SquadCreateStates.waiting_for_name, F.text)

    dp.message.register(
        save_auto_sync_schedule,
        RemnaWaveSyncStates.waiting_for_schedule,
        F.text,
    )
