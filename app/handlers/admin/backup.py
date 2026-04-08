import html
from datetime import datetime

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.backup_service import backup_service
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


class BackupStates(StatesGroup):
    waiting_backup_file = State()
    waiting_settings_update = State()


def get_backup_main_keyboard(language: str = 'ru'):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text='🚀 创建备份', callback_data='backup_create'),
                InlineKeyboardButton(text='📥 恢复', callback_data='backup_restore'),
            ],
            [
                InlineKeyboardButton(text='📋 备份列表', callback_data='backup_list'),
                InlineKeyboardButton(text='⚙️设置', callback_data='backup_settings'),
            ],
            [InlineKeyboardButton(text='◀️ 返回', callback_data='admin_panel')],
        ]
    )


def get_backup_list_keyboard(backups: list, page: int = 1, per_page: int = 5):
    keyboard = []

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_backups = backups[start_idx:end_idx]

    for backup in page_backups:
        try:
            if backup.get('timestamp'):
                dt = datetime.fromisoformat(backup['timestamp'].replace('Z', '+00:00'))
                date_str = dt.strftime('%d.%m %H:%M')
            else:
                date_str = '?'
        except:
            date_str = '?'

        size_str = f'{backup.get("file_size_mb", 0):.1f}MB'
        records_str = backup.get('total_records', '?')

        button_text = f'📦 {date_str} • {size_str} • {records_str} 记录'
        callback_data = f'backup_manage_{backup["filename"]}'

        keyboard.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])

    if len(backups) > per_page:
        total_pages = (len(backups) + per_page - 1) // per_page
        nav_row = []

        if page > 1:
            nav_row.append(InlineKeyboardButton(text='⬅️', callback_data=f'backup_list_page_{page - 1}'))

        nav_row.append(InlineKeyboardButton(text=f'{page}/{total_pages}', callback_data='noop'))

        if page < total_pages:
            nav_row.append(InlineKeyboardButton(text='➡️', callback_data=f'backup_list_page_{page + 1}'))

        keyboard.append(nav_row)

    keyboard.extend([[InlineKeyboardButton(text='◀️ 返回', callback_data='backup_panel')]])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_backup_manage_keyboard(backup_filename: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='📥 恢复', callback_data=f'backup_restore_file_{backup_filename}')],
            [InlineKeyboardButton(text='🗑️ 删除', callback_data=f'backup_delete_{backup_filename}')],
            [InlineKeyboardButton(text='◀️ 前往列表', callback_data='backup_list')],
        ]
    )


def get_backup_settings_keyboard(settings_obj):
    auto_status = '✅ 已启用' if settings_obj.auto_backup_enabled else '❌ 已禁用'
    compression_status = '✅ 已启用' if settings_obj.compression_enabled else '❌ 已禁用'
    logs_status = '✅ 已启用' if settings_obj.include_logs else '❌ 已禁用'

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f'🔄 自动备份：{auto_status}', callback_data='backup_toggle_auto')],
            [InlineKeyboardButton(text=f'🗜️压缩：{compression_status}', callback_data='backup_toggle_compression')],
            [InlineKeyboardButton(text=f'📋 登录备份：{logs_status}', callback_data='backup_toggle_logs')],
            [InlineKeyboardButton(text='◀️ 返回', callback_data='backup_panel')],
        ]
    )


@admin_required
@error_handler
async def show_backup_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    settings_obj = await backup_service.get_backup_settings()

    status_auto = '✅ 已启用' if settings_obj.auto_backup_enabled else '❌ 已关闭'
    compression_status = '是' if settings_obj.compression_enabled else '否'

    text = f"🗄️ <b>备份系统</b>\n\n📊 <b>状态：</b>\n• 自动备份：{status_auto}\n• 间隔：{settings_obj.backup_interval_hours} 小时\n• 存储：{settings_obj.max_backups_keep} 个文件\n• 压缩：{compression_status}\n\n📁 <b>位置：</b> <code>/app/data/backups</code>\n\n⚡ <b>可操作：</b>\n• 创建全部数据的完整备份\n• 从备份文件恢复\n• 管理自动备份"

    await callback.message.edit_text(text, parse_mode='HTML', reply_markup=get_backup_main_keyboard(db_user.language))
    await callback.answer()


@admin_required
@error_handler
async def create_backup_handler(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.answer('🔄备份创建开始...')

    progress_msg = await callback.message.edit_text(
        '🔄 <b> 正在创建备份...</b>\n\n⏳ 从数据库导出数据...\n这可能需要几分钟。',
        parse_mode='HTML',
    )

    # Создаем бекап
    created_by_id = db_user.telegram_id or db_user.email or f'#{db_user.id}'
    success, message, file_path = await backup_service.create_backup(created_by=created_by_id, compress=True)

    if success:
        await progress_msg.edit_text(
            f'✅ <b>备份创建成功！</b>\n\n{message}',
            parse_mode='HTML',
            reply_markup=get_backup_main_keyboard(db_user.language),
        )
    else:
        await progress_msg.edit_text(
            f'❌ <b>创建备份时出错</b>\n\n{html.escape(message)}',
            parse_mode='HTML',
            reply_markup=get_backup_main_keyboard(db_user.language),
        )


@admin_required
@error_handler
async def show_backup_list(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    page = 1
    if callback.data.startswith('backup_list_page_'):
        try:
            page = int(callback.data.split('_')[-1])
        except:
            page = 1

    backups = await backup_service.get_backup_list()

    if not backups:
        text = '📦 <b> 备份列表为空</b>\n\n尚未创建备份。'
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='🚀 创建第一个备份', callback_data='backup_create')],
                [InlineKeyboardButton(text='◀️ 返回', callback_data='backup_panel')],
            ]
        )
    else:
        text = f'📦 <b> 备份列表</b>（总计：{len(backups)}）'
        text += '选择要管理的备份：'
        keyboard = get_backup_list_keyboard(backups, page)

    await callback.message.edit_text(text, parse_mode='HTML', reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def manage_backup_file(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    filename = callback.data.replace('backup_manage_', '')

    backups = await backup_service.get_backup_list()
    backup_info = None

    for backup in backups:
        if backup['filename'] == filename:
            backup_info = backup
            break

    if not backup_info:
        await callback.answer('❌ 找不到备份文件', show_alert=True)
        return

    try:
        if backup_info.get('timestamp'):
            dt = datetime.fromisoformat(backup_info['timestamp'].replace('Z', '+00:00'))
            date_str = dt.strftime('%d.%m.%Y %H:%M:%S')
        else:
            date_str = '未知'
    except:
        date_str = '日期格式错误'

    text = f"📦 <b>备份信息</b>\n\n📄 <b>文件：</b> <code>{filename}</code>\n📅 <b>创建时间：</b> {date_str}\n💾 <b>大小：</b> {backup_info.get('file_size_mb', 0):.2f} MB\n📊 <b>数据表：</b> {backup_info.get('tables_count', '?')}\n📈 <b>记录数：</b> {backup_info.get('total_records', '?'):,}\n🗜️ <b>压缩：</b> {('是' if backup_info.get('compressed') else '否')}\n🗄️ <b>数据库：</b> {backup_info.get('database_type', 'unknown')}"

    if backup_info.get('error'):
        text += f"⚠️ <b>错误：</b> {backup_info['error']}"

    await callback.message.edit_text(text, parse_mode='HTML', reply_markup=get_backup_manage_keyboard(filename))
    await callback.answer()


@admin_required
@error_handler
async def delete_backup_confirm(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    filename = callback.data.replace('backup_delete_', '')

    text = '🗑️ <b>删除备份</b>'
    text += '您确定要删除备份吗？'
    text += f'📄 <code>{filename}</code>\n\n'
    text += '⚠️ <b>此操作无法撤消！</b>'

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text='✅ 是，删除', callback_data=f'backup_delete_confirm_{filename}'),
                InlineKeyboardButton(text='❌ 取消', callback_data=f'backup_manage_{filename}'),
            ]
        ]
    )

    await callback.message.edit_text(text, parse_mode='HTML', reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def delete_backup_execute(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    filename = callback.data.replace('backup_delete_confirm_', '')

    success, message = await backup_service.delete_backup(filename)

    if success:
        await callback.message.edit_text(
            f'✅ <b>备份已删除</b>\n\n{message}',
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text='📋 到备份列表', callback_data='backup_list')]]
            ),
        )
    else:
        await callback.message.edit_text(
            f'❌ <b>删除错误</b>\n\n{message}',
            parse_mode='HTML',
            reply_markup=get_backup_manage_keyboard(filename),
        )

    await callback.answer()


@admin_required
@error_handler
async def restore_backup_start(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    if callback.data.startswith('backup_restore_file_'):
        # Восстановление из конкретного файла
        filename = callback.data.replace('backup_restore_file_', '')

        text = '📥 <b>从备份恢复</b>'
        text += f'📄 <b>文件：</b> <code>{filename}</code>'
        text += '⚠️ <b>注意！</b>'
        text += '• 该过程可能需要几分钟'
        text += '• 建议在恢复之前创建备份'
        text += '• 现有数据将得到补充'
        text += '继续恢复吗？'

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text='✅ 是，恢复', callback_data=f'backup_restore_execute_{filename}'
                    ),
                    InlineKeyboardButton(
                        text='🗑️清洁和恢复', callback_data=f'backup_restore_clear_{filename}'
                    ),
                ],
                [InlineKeyboardButton(text='❌ 取消', callback_data=f'backup_manage_{filename}')],
            ]
        )
    else:
        text = '📥 <b>从备份恢复</b>\n\n📎 发送备份文件（.json 或 .json.gz）\n\n⚠️<b>重要：</b>\n• 文件必须由该备份系统创建\n• 该过程可能需要几分钟\n• 建议在恢复之前创建备份\n\n💡 或从下面的现有备份中进行选择。'

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='📋 从列表中选择', callback_data='backup_list')],
                [InlineKeyboardButton(text='❌ 取消', callback_data='backup_panel')],
            ]
        )

        await state.set_state(BackupStates.waiting_backup_file)

    await callback.message.edit_text(text, parse_mode='HTML', reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def restore_backup_execute(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    if callback.data.startswith('backup_restore_execute_'):
        filename = callback.data.replace('backup_restore_execute_', '')
        clear_existing = False
    elif callback.data.startswith('backup_restore_clear_'):
        filename = callback.data.replace('backup_restore_clear_', '')
        clear_existing = True
    else:
        await callback.answer('❌ 命令格式无效', show_alert=True)
        return

    await callback.answer('🔄恢复已开始...')

    # Показываем прогресс
    action_text = '清空并恢复' if clear_existing else '恢复'
    progress_msg = await callback.message.edit_text(
        f'📥 <b> 正在从备份恢复...</b>\n\n⏳ 我们使用 {action_text} 数据...\n📄 文件：<code>{filename}</code>\n\n这可能需要几分钟。',
        parse_mode='HTML',
    )

    backup_path = backup_service.backup_dir / filename

    success, message = await backup_service.restore_backup(str(backup_path), clear_existing=clear_existing)

    if success:
        await progress_msg.edit_text(
            f'✅ <b>恢复完成！</b>\n\n{message}',
            parse_mode='HTML',
            reply_markup=get_backup_main_keyboard(db_user.language),
        )
    else:
        await progress_msg.edit_text(
            f'❌ <b>恢复错误</b>\n\n{message}',
            parse_mode='HTML',
            reply_markup=get_backup_manage_keyboard(filename),
        )


@admin_required
@error_handler
async def handle_backup_file_upload(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    if not message.document:
        await message.answer(
            '❌ 请发送备份文件（.json 或 .json.gz）',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text='◀️ 取消', callback_data='backup_panel')]]
            ),
        )
        return

    document = message.document

    if not (document.file_name.endswith('.json') or document.file_name.endswith('.json.gz')):
        await message.answer(
            '❌ 不支持的文件格式。上传 .json 或 .json.gz 文件',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text='◀️ 取消', callback_data='backup_panel')]]
            ),
        )
        return

    if document.file_size > 50 * 1024 * 1024:
        await message.answer(
            '❌ 文件太大（最大50MB）',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text='◀️ 取消', callback_data='backup_panel')]]
            ),
        )
        return

    try:
        file = await message.bot.get_file(document.file_id)

        temp_path = backup_service.backup_dir / f'uploaded_{document.file_name}'

        await message.bot.download_file(file.file_path, temp_path)

        text = f'📥 <b>文件已上传</b>\n\n📄 <b>名称：</b> <code>{document.file_name}</code>\n💾 <b>尺寸：</b> {document.file_size / 1024 / 1024:.2f} MB\n\n⚠️<b>注意！</b>\n恢复过程将更改数据库中的数据。\n建议在恢复之前创建备份。\n\n继续？'

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text='✅ 恢复', callback_data=f'backup_restore_execute_{temp_path.name}'
                    ),
                    InlineKeyboardButton(
                        text='🗑️清洁和恢复',
                        callback_data=f'backup_restore_clear_{temp_path.name}',
                    ),
                ],
                [InlineKeyboardButton(text='❌ 取消', callback_data='backup_panel')],
            ]
        )

        await message.answer(text, parse_mode='HTML', reply_markup=keyboard)
        await state.clear()

    except Exception as e:
        logger.error('加载备份文件时出错', error=e)
        await message.answer(
            f'❌ 文件上传错误：{e!s}',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text='◀️ 取消', callback_data='backup_panel')]]
            ),
        )


@admin_required
@error_handler
async def show_backup_settings(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    settings_obj = await backup_service.get_backup_settings()

    text = f"⚙️ <b>备份系统设置</b>\n\n🔄 <b>自动备份：</b>\n• 状态：{('✅ 已启用' if settings_obj.auto_backup_enabled else '❌ 已禁用')}\n• 间隔：{settings_obj.backup_interval_hours} 小时\n• 开始时间：{settings_obj.backup_time}\n\n📦 <b>存储：</b>\n• 最大文件数：{settings_obj.max_backups_keep}\n• 压缩：{('✅ 已启用' if settings_obj.compression_enabled else '❌ 已禁用')}\n• 包含日志：{('✅ 是' if settings_obj.include_logs else '❌ 否')}\n\n📁 <b>位置：</b> <code>{settings_obj.backup_location}</code>"

    await callback.message.edit_text(text, parse_mode='HTML', reply_markup=get_backup_settings_keyboard(settings_obj))
    await callback.answer()


@admin_required
@error_handler
async def toggle_backup_setting(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    settings_obj = await backup_service.get_backup_settings()

    if callback.data == 'backup_toggle_auto':
        new_value = not settings_obj.auto_backup_enabled
        await backup_service.update_backup_settings(auto_backup_enabled=new_value)
        status = '已启用' if new_value else '已禁用'
        await callback.answer(f'自动备份 {status}')

    elif callback.data == 'backup_toggle_compression':
        new_value = not settings_obj.compression_enabled
        await backup_service.update_backup_settings(compression_enabled=new_value)
        status = '已启用' if new_value else '已禁用'
        await callback.answer(f'压缩 {status}')

    elif callback.data == 'backup_toggle_logs':
        new_value = not settings_obj.include_logs
        await backup_service.update_backup_settings(include_logs=new_value)
        status = '已启用' if new_value else '已禁用'
        await callback.answer(f'登录备份{status}')

    await show_backup_settings(callback, db_user, db)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_backup_panel, F.data == 'backup_panel')

    dp.callback_query.register(create_backup_handler, F.data == 'backup_create')

    dp.callback_query.register(show_backup_list, F.data.startswith('backup_list'))

    dp.callback_query.register(manage_backup_file, F.data.startswith('backup_manage_'))

    dp.callback_query.register(
        delete_backup_confirm, F.data.startswith('backup_delete_') & ~F.data.startswith('backup_delete_confirm_')
    )

    dp.callback_query.register(delete_backup_execute, F.data.startswith('backup_delete_confirm_'))

    dp.callback_query.register(
        restore_backup_start, F.data.in_(['backup_restore']) | F.data.startswith('backup_restore_file_')
    )

    dp.callback_query.register(
        restore_backup_execute,
        F.data.startswith('backup_restore_execute_') | F.data.startswith('backup_restore_clear_'),
    )

    dp.callback_query.register(show_backup_settings, F.data == 'backup_settings')

    dp.callback_query.register(
        toggle_backup_setting, F.data.in_(['backup_toggle_auto', 'backup_toggle_compression', 'backup_toggle_logs'])
    )

    dp.message.register(handle_backup_file_upload, BackupStates.waiting_backup_file)

