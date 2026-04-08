import re

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.welcome_text import (
    get_available_placeholders,
    get_current_welcome_text_or_default,
    get_current_welcome_text_settings,
    set_welcome_text,
    toggle_welcome_text_status,
)
from app.database.models import User
from app.keyboards.admin import get_welcome_text_keyboard
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


def validate_html_tags(text: str) -> tuple[bool, str]:
    """
    Проверяет HTML-теги в тексте на соответствие требованиям Telegram API.

    Args:
        text: Текст для проверки

    Returns:
        Кортеж из (валидно ли, сообщение об ошибке или None)
    """
    # Поддерживаемые теги в parse_mode="HTML" для Telegram API
    allowed_tags = {
        'b',
        'strong',  # жирный
        'i',
        'em',  # курсив
        'u',
        'ins',  # подчеркнуто
        's',
        'strike',
        'del',  # зачеркнуто
        'code',  # моноширинный для коротких фрагментов
        'pre',  # моноширинный блок кода
        'a',  # ссылки
    }

    # Убираем плейсхолдеры из строки перед проверкой тегов
    # Плейсхолдеры имеют формат {ключ}, и не являются тегами
    placeholder_pattern = r'\{[^{}]+\}'
    clean_text = re.sub(placeholder_pattern, '', text)

    # Находим все открывающие и закрывающие теги
    tag_pattern = r'<(/?)([a-zA-Z]+)(\s[^>]*)?>'
    tags_with_pos = [
        (m.group(1), m.group(2), m.group(3), m.start(), m.end()) for m in re.finditer(tag_pattern, clean_text)
    ]

    for closing, tag, attrs, start_pos, end_pos in tags_with_pos:
        tag_lower = tag.lower()

        # Проверяем, является ли тег поддерживаемым
        if tag_lower not in allowed_tags:
            return (
                False,
                f"不支持的 HTML 标签：<{tag}>。仅使用标签：{', '.join(sorted(allowed_tags))}",
            )

        # Проверяем атрибуты для тега <a>
        if tag_lower == 'a':
            if closing:
                continue  # Для закрывающего тега не нужно проверять атрибуты
            if not attrs:
                return False, "<a> 标记必须包含 href 属性，例如：<a href='URL'>link</a>"

            # Проверяем, что есть атрибут href
            if 'href=' not in attrs.lower():
                return False, "<a> 标记必须包含 href 属性，例如：<a href='URL'>link</a>"

            # Проверяем формат URL
            href_match = re.search(r'href\s*=\s*[\'"]([^\'"]+)[\'"]', attrs, re.IGNORECASE)
            if href_match:
                url = href_match.group(1)
                # Проверяем, что URL начинается с поддерживаемой схемы
                if not re.match(r'^https?://|^tg://', url, re.IGNORECASE):
                    return False, f'<a> 标记中的 URL 必须以 http://, https:// 或 tg:// 开头。找到：{url}'
            else:
                return False, '无法从 <a> 标记的 href 属性中提取 URL'

    # Проверяем парность тегов с использованием стека
    stack = []
    for closing, tag, attrs, start_pos, end_pos in tags_with_pos:
        tag_lower = tag.lower()

        if tag_lower not in allowed_tags:
            continue

        if closing:
            # Это закрывающий тег
            if not stack:
                return False, f'额外结束标签：</QQQPH0QQQ>'

            last_opening_tag = stack.pop()
            if last_opening_tag.lower() != tag_lower:
                return False, f'</QQQPH0QQQ> 标记与开始标记 <{last_opening_tag}> 不匹配'
        else:
            # Это открывающий тег
            stack.append(tag)

    # Если остались незакрытые теги
    if stack:
        unclosed_tags = ', '.join([f'<{tag}>' for tag in stack])
        return False, f'未关闭的标签： {unclosed_tags}'

    return True, None


def get_telegram_formatting_info() -> str:
    return '📝 <b>支持的格式标签：</b>\n\n• <code><b>粗体文本</b></code> → <b>粗体文本</b>\n• <代码><i>斜体</i></code> → <i>斜体</i>\n• <code><u>下划线</u></code> → <u>下划线</u>\n• <code><s>删除线</s></code> → <s>删除线</s>\n• <code><code>等宽空格</code></code> → <code>等宽空格</code>\n• <code><pre>代码块</pre></code> → 多行代码\n• <code><a href="URL">链接</a></code> → 链接\n\n⚠️ <b>注意：</b> 仅使用以上标签！\n不支持任何其他 HTML 标签，并将显示为纯文本。\n\n❌ <b>请勿使用：</b> <div>、<span>、<p>、<br>、<h1>-<h6>、<img>和其他 HTML 标签。'


@admin_required
@error_handler
async def show_welcome_text_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    welcome_settings = await get_current_welcome_text_settings(db)
    status_emoji = '🟢' if welcome_settings['is_enabled'] else '🔴'
    status_text = 'включено' if welcome_settings['is_enabled'] else 'отключено'

    await callback.message.edit_text(
        f'👋欢迎文字管理\n\n{status_emoji} <b>状态：</b> {status_text}\n\n您可以在此处控制注册后向新用户显示的文本。\n\n💡 用于自动更正的可用占位符：',
        reply_markup=get_welcome_text_keyboard(db_user.language, welcome_settings['is_enabled']),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_welcome_text(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    new_status = await toggle_welcome_text_status(db, db_user.id)

    status_emoji = '🟢' if new_status else '🔴'
    status_text = 'включено' if new_status else 'отключено'
    action_text = 'включены' if new_status else 'отключены'

    await callback.message.edit_text(
        f'👋欢迎文字管理\n\n{status_emoji} <b>状态：</b> {status_text}\n\n✅ 欢迎留言{action_text}！\n\n您可以在此处控制注册后向新用户显示的文本。\n\n💡 用于自动更正的可用占位符：',
        reply_markup=get_welcome_text_keyboard(db_user.language, new_status),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_current_welcome_text(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    welcome_settings = await get_current_welcome_text_settings(db)
    current_text = welcome_settings['text']
    is_enabled = welcome_settings['is_enabled']

    if not welcome_settings['id']:
        status = '📝 Используется стандартный текст:'
    else:
        status = '📝 Текущий приветственный текст:'

    status_emoji = '🟢' if is_enabled else '🔴'
    status_text = 'включено' if is_enabled else 'отключено'

    placeholders = get_available_placeholders()
    placeholders_text = '\n'.join([f'• <code>{key}</code> - {desc}' for key, desc in placeholders.items()])

    await callback.message.edit_text(
        f'{status_emoji} <b>状态：</b> {status_text}\n\n{status}\n\n<代码>{current_text}</code>\n\n💡可用占位符：\n{placeholders_text}',
        reply_markup=get_welcome_text_keyboard(db_user.language, is_enabled),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_placeholders_help(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    welcome_settings = await get_current_welcome_text_settings(db)
    placeholders = get_available_placeholders()
    placeholders_text = '\n'.join([f'• <code>{key}</code>\n  {desc}' for key, desc in placeholders.items()])

    help_text = (
        f'💡 用于自动更正的可用占位符：\n\n{placeholders_text}\n\n📌 使用示例：\n• <code>你好，{{user_name}}！欢迎光临！</code>\n• <code>你好，{{first_name}}！很高兴见到你！</code>\n• <code>你好，{{username}}！感谢您注册！</code>\n\n如果没有用户数据，则使用“朋友”一词。'
    )

    await callback.message.edit_text(
        help_text,
        reply_markup=get_welcome_text_keyboard(db_user.language, welcome_settings['is_enabled']),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_formatting_help(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    welcome_settings = await get_current_welcome_text_settings(db)
    formatting_info = get_telegram_formatting_info()

    await callback.message.edit_text(
        formatting_info,
        reply_markup=get_welcome_text_keyboard(db_user.language, welcome_settings['is_enabled']),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def start_edit_welcome_text(callback: types.CallbackQuery, state: FSMContext, db_user: User, db: AsyncSession):
    welcome_settings = await get_current_welcome_text_settings(db)
    current_text = welcome_settings['text']

    placeholders = get_available_placeholders()
    placeholders_text = '\n'.join([f'• <code>{key}</code> - {desc}' for key, desc in placeholders.items()])

    await callback.message.edit_text(
        f'📝 编辑欢迎文字\n\n当前文本：\n<代码>{current_text}</code>\n\n💡可用占位符：\n{placeholders_text}\n\n发送新文本：',
        parse_mode='HTML',
    )

    await state.set_state(AdminStates.editing_welcome_text)
    await callback.answer()


@admin_required
@error_handler
async def process_welcome_text_edit(message: types.Message, state: FSMContext, db_user: User, db: AsyncSession):
    new_text = message.text.strip()

    if len(new_text) < 10:
        await message.answer('❌ 文字太短了！最少 10 个字符。')
        return

    if len(new_text) > 4000:
        await message.answer('❌ 文字太长了！最多 4000 个字符。')
        return

    # Проверяем HTML-теги на валидность
    is_valid, error_msg = validate_html_tags(new_text)
    if not is_valid:
        await message.answer(f'❌ HTML 标记错误：\n\n{error_msg}')
        return

    success = await set_welcome_text(db, new_text, db_user.id)

    if success:
        welcome_settings = await get_current_welcome_text_settings(db)
        status_emoji = '🟢' if welcome_settings['is_enabled'] else '🔴'
        status_text = 'включено' if welcome_settings['is_enabled'] else 'отключено'

        placeholders = get_available_placeholders()
        placeholders_text = '\n'.join([f'• <code>{key}</code>' for key in placeholders.keys()])

        await message.answer(
            f'✅ 欢迎文字已成功更新！\n\n{status_emoji} <b>状态：</b> {status_text}\n\n新文本：\n<代码>{new_text}</code>\n\n💡 占位符将被替换：{placeholders_text}',
            reply_markup=get_welcome_text_keyboard(db_user.language, welcome_settings['is_enabled']),
            parse_mode='HTML',
        )
    else:
        welcome_settings = await get_current_welcome_text_settings(db)
        await message.answer(
            '❌ 保存文本时出错。再试一次。',
            reply_markup=get_welcome_text_keyboard(db_user.language, welcome_settings['is_enabled']),
        )

    await state.clear()


@admin_required
@error_handler
async def reset_welcome_text(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    default_text = await get_current_welcome_text_or_default()
    success = await set_welcome_text(db, default_text, db_user.id)

    if success:
        welcome_settings = await get_current_welcome_text_settings(db)
        status_emoji = '🟢' if welcome_settings['is_enabled'] else '🔴'
        status_text = 'включено' if welcome_settings['is_enabled'] else 'отключено'

        await callback.message.edit_text(
            f'✅ 欢迎文字已重置为标准！\n\n{status_emoji} <b>状态：</b> {status_text}\n\n标准文本：\n<代码>{default_text}</code>\n\n💡 占位符 <code>{{user_name}}</code> 将替换为用户名',
            reply_markup=get_welcome_text_keyboard(db_user.language, welcome_settings['is_enabled']),
            parse_mode='HTML',
        )
    else:
        welcome_settings = await get_current_welcome_text_settings(db)
        await callback.message.edit_text(
            '❌ 重置文本时出错。再试一次。',
            reply_markup=get_welcome_text_keyboard(db_user.language, welcome_settings['is_enabled']),
        )

    await callback.answer()


@admin_required
@error_handler
async def show_preview_welcome_text(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    from app.database.crud.welcome_text import get_welcome_text_for_user

    class TestUser:
        def __init__(self):
            self.first_name = 'Иван'
            self.username = 'test_user'

    test_user = TestUser()
    preview_text = await get_welcome_text_for_user(db, test_user)

    welcome_settings = await get_current_welcome_text_settings(db)

    if preview_text:
        await callback.message.edit_text(
            f'👁️预览\n\n用户“Ivan”(@test_user) 的文本是什么样子的：\n\n<代码>{preview_text}</code>',
            reply_markup=get_welcome_text_keyboard(db_user.language, welcome_settings['is_enabled']),
            parse_mode='HTML',
        )
    else:
        await callback.message.edit_text(
            '👁️预览\n\n🔴 欢迎消息被禁用。\n新用户注册后不会收到欢迎短信。',
            reply_markup=get_welcome_text_keyboard(db_user.language, welcome_settings['is_enabled']),
            parse_mode='HTML',
        )

    await callback.answer()


def register_welcome_text_handlers(dp: Dispatcher):
    dp.callback_query.register(show_welcome_text_panel, F.data == 'welcome_text_panel')

    dp.callback_query.register(toggle_welcome_text, F.data == 'toggle_welcome_text')

    dp.callback_query.register(show_current_welcome_text, F.data == 'show_welcome_text')

    dp.callback_query.register(show_placeholders_help, F.data == 'show_placeholders_help')

    dp.callback_query.register(show_formatting_help, F.data == 'show_formatting_help')

    dp.callback_query.register(show_preview_welcome_text, F.data == 'preview_welcome_text')

    dp.callback_query.register(start_edit_welcome_text, F.data == 'edit_welcome_text')

    dp.callback_query.register(reset_welcome_text, F.data == 'reset_welcome_text')

    dp.message.register(process_welcome_text_edit, AdminStates.editing_welcome_text)
