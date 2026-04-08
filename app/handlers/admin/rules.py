import re

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.rules import clear_all_rules, create_or_update_rules, get_current_rules_content
from app.database.models import User
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler
from app.utils.validators import get_html_help_text, validate_html_tags


def _safe_preview(html_text: str, limit: int = 500) -> str:
    """Создаёт превью текста, безопасно обрезая HTML-теги."""
    plain = re.sub(r'<[^>]+>', '', html_text)
    if len(plain) <= limit:
        return plain
    return plain[:limit] + '...'


logger = structlog.get_logger(__name__)


@admin_required
@error_handler
async def show_rules_management(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    text = '📋 <b>管理服务规则</b>\n\n当前规则会在用户注册时和主菜单中显示。\n\n选择动作：'

    keyboard = [
        [types.InlineKeyboardButton(text='📝 编辑规则', callback_data='admin_edit_rules')],
        [types.InlineKeyboardButton(text='👀 查看规则', callback_data='admin_view_rules')],
        [types.InlineKeyboardButton(text='🗑️明确的规则', callback_data='admin_clear_rules')],
        [types.InlineKeyboardButton(text='ℹ️ HTML 帮助', callback_data='admin_rules_help')],
        [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_submenu_settings')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def view_current_rules(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        current_rules = await get_current_rules_content(db, db_user.language)

        is_valid, error_msg = validate_html_tags(current_rules)
        warning = ''
        if not is_valid:
            warning = f'\n\n⚠️ <b>Внимание:</b> В правилах найдена ошибка HTML: {error_msg}'

        await callback.message.edit_text(
            f'📋 <b>当前服务规则</b>\n\n{current_rules}{warning}',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='✏️编辑', callback_data='admin_edit_rules')],
                    [types.InlineKeyboardButton(text='🗑️清除', callback_data='admin_clear_rules')],
                    [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_rules')],
                ]
            ),
        )
        await callback.answer()
    except Exception as e:
        logger.error('Ошибка при показе правил', error=e)
        await callback.message.edit_text(
            '❌ 加载规则时出错。也许文本包含不正确的 HTML 标签。',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🗑️明确的规则', callback_data='admin_clear_rules')],
                    [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_rules')],
                ]
            ),
        )
        await callback.answer()


@admin_required
@error_handler
async def start_edit_rules(callback: types.CallbackQuery, db_user: User, state: FSMContext, db: AsyncSession):
    try:
        current_rules = await get_current_rules_content(db, db_user.language)

        preview = _safe_preview(current_rules, 500)

        text = (
            f'✏️ <b>编辑规则</b>\n\n<b>当前规则：</b>\n<代码>{preview}</code>\n\n发送服务规则的新文本。\n\n<i>支持 HTML 标记。保存前将检查所有标签。</i>\n\n💡 <b>提示：</b> 点击 /html_help 查看支持的标签'
        )

        await callback.message.edit_text(
            text,
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='ℹ️HTML帮助', callback_data='admin_rules_help')],
                    [types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_rules')],
                ]
            ),
        )

        await state.set_state(AdminStates.editing_rules_page)
        await callback.answer()

    except Exception as e:
        logger.error('Ошибка при начале редактирования правил', error=e)
        await callback.answer('❌ 加载编辑规则时出错', show_alert=True)


@admin_required
@error_handler
async def process_rules_edit(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    new_rules = message.text

    if len(new_rules) > 4000:
        await message.answer('❌ 规则文本太长（最多 4000 个字符）')
        return

    is_valid, error_msg = validate_html_tags(new_rules)
    if not is_valid:
        await message.answer(
            f'❌ <b>HTML 标记中的错误：</b>\n{error_msg}\n\n请更正错误并重新提交文本。\n\n💡 使用/html_help 查看正确语法',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='ℹ️HTML帮助', callback_data='admin_rules_help')],
                    [types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_rules')],
                ]
            ),
        )
        return

    try:
        preview_text = f'📋 <b>Предварительный просмотр новых правил:</b>\n\n{new_rules}\n\n'
        preview_text += '⚠️ <b>Внимание!</b> Новые правила будут показываться всем пользователям.\n\n'
        preview_text += 'Сохранить изменения?'

        if len(preview_text) > 4000:
            preview_text = (
                '📋 <b>Предварительный просмотр новых правил:</b>\n\n'
                f'{_safe_preview(new_rules, 500)}\n\n'
                f'⚠️ <b>Внимание!</b> Новые правила будут показываться всем пользователям.\n\n'
                f'Текст правил: {len(new_rules)} символов\n'
                f'Сохранить изменения?'
            )

        await message.answer(
            preview_text,
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(text='✅ 保存', callback_data='admin_save_rules'),
                        types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_rules'),
                    ]
                ]
            ),
        )

        await state.update_data(new_rules=new_rules)

    except Exception as e:
        logger.error('Ошибка при показе превью правил', error=e)
        await message.answer(
            f'⚠️ <b>确认保存规则</b>\n\n新规则已准备好保存（{len(new_rules)} 字符）。\nHTML 标签已检查且正确。\n\n保存更改吗？',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(text='✅ 保存', callback_data='admin_save_rules'),
                        types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_rules'),
                    ]
                ]
            ),
        )

        await state.update_data(new_rules=new_rules)


@admin_required
@error_handler
async def save_rules(callback: types.CallbackQuery, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()
    new_rules = data.get('new_rules')

    if not new_rules:
        await callback.answer('❌ 错误：找不到规则文本', show_alert=True)
        return

    is_valid, error_msg = validate_html_tags(new_rules)
    if not is_valid:
        await callback.message.edit_text(
            f'❌ <b>保存时出错：</b>\n{error_msg}\n\n由于 HTML 标记中的错误，规则未保存。',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🔄再试一次', callback_data='admin_edit_rules')],
                    [types.InlineKeyboardButton(text='📋遵守规则', callback_data='admin_rules')],
                ]
            ),
        )
        await state.clear()
        await callback.answer()
        return

    try:
        await create_or_update_rules(db=db, content=new_rules, language=db_user.language)

        from app.localization.texts import clear_rules_cache

        clear_rules_cache()

        from app.localization.texts import refresh_rules_cache

        await refresh_rules_cache(db_user.language)

        await callback.message.edit_text(
            f'✅ <b>服务规则已成功更新！</b>\n\n✓ 新规则保存在数据库中\n✓ HTML 标签已检查且正确\n✓ 规则缓存清除和更新\n✓ 规则将显示给用户\n\n📊 文字大小：{len(new_rules)} 个字符',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='👀 查看', callback_data='admin_view_rules')],
                    [types.InlineKeyboardButton(text='📋遵守规则', callback_data='admin_rules')],
                ]
            ),
        )

        await state.clear()
        logger.info('Правила сервиса обновлены администратором', telegram_id=db_user.telegram_id)
        await callback.answer()

    except Exception as e:
        logger.error('Ошибка сохранения правил', error=e)
        await callback.message.edit_text(
            '❌ <b>保存规则时出错</b>\n\n写入数据库时发生错误。再试一次。',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🔄再试一次', callback_data='admin_save_rules')],
                    [types.InlineKeyboardButton(text='📋遵守规则', callback_data='admin_rules')],
                ]
            ),
        )
        await callback.answer()


@admin_required
@error_handler
async def clear_rules_confirmation(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.message.edit_text(
        '🗑️ <b>清算服务规则</b>\n\n⚠️ <b>注意！</b> 您即将彻底删除所有服务规则。\n\n清除后，用户将看到默认的默认规则。\n\n此操作无法撤消。继续？',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(text='✅ 是的，清楚', callback_data='admin_confirm_clear_rules'),
                    types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_rules'),
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_clear_rules(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        await clear_all_rules(db, db_user.language)

        from app.localization.texts import clear_rules_cache

        clear_rules_cache()

        await callback.message.edit_text(
            '✅ <b>规则清除成功！</b>\n\n✓ 所有自定义规则已被删除\n✓ 现在使用标准规则\n✓ 规则缓存已清除\n\n用户将看到默认规则。',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='📝 创造新的', callback_data='admin_edit_rules')],
                    [types.InlineKeyboardButton(text='👀 查看当前', callback_data='admin_view_rules')],
                    [types.InlineKeyboardButton(text='📋遵守规则', callback_data='admin_rules')],
                ]
            ),
        )

        logger.info('Правила очищены администратором', telegram_id=db_user.telegram_id)
        await callback.answer()

    except Exception as e:
        logger.error('Ошибка при очистке правил', error=e)
        await callback.answer('❌ 清除规则时出错', show_alert=True)


@admin_required
@error_handler
async def show_html_help(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    help_text = get_html_help_text()

    await callback.message.edit_text(
        f'ℹ️ <b>有关 HTML 格式化的帮助</b>\n\n{help_text}',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='📝 编辑规则', callback_data='admin_edit_rules')],
                [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_rules')],
            ]
        ),
    )
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_rules_management, F.data == 'admin_rules')
    dp.callback_query.register(view_current_rules, F.data == 'admin_view_rules')
    dp.callback_query.register(start_edit_rules, F.data == 'admin_edit_rules')
    dp.callback_query.register(save_rules, F.data == 'admin_save_rules')

    dp.callback_query.register(clear_rules_confirmation, F.data == 'admin_clear_rules')
    dp.callback_query.register(confirm_clear_rules, F.data == 'admin_confirm_clear_rules')

    dp.callback_query.register(show_html_help, F.data == 'admin_rules_help')

    dp.message.register(process_rules_edit, AdminStates.editing_rules_page)
