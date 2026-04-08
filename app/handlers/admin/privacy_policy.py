import html
from datetime import datetime

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.privacy_policy_service import PrivacyPolicyService
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler
from app.utils.validators import get_html_help_text, validate_html_tags


logger = structlog.get_logger(__name__)


def _format_timestamp(value: datetime | None) -> str:
    if not value:
        return ''
    try:
        return value.strftime('%d.%m.%Y %H:%M')
    except Exception:
        return ''


async def _build_overview(
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    policy = await PrivacyPolicyService.get_policy(
        db,
        db_user.language,
        fallback=False,
    )

    normalized_language = PrivacyPolicyService.normalize_language(db_user.language)
    has_content = bool(policy and policy.content and policy.content.strip())

    description = texts.t(
        'ADMIN_PRIVACY_POLICY_DESCRIPTION',
        '隐私政策显示在“信息”部分。',
    )

    status_text = texts.t(
        'ADMIN_PRIVACY_POLICY_STATUS_DISABLED',
        '⚠️政策显示已禁用或文本缺失。',
    )
    if policy and policy.is_enabled and has_content:
        status_text = texts.t(
            'ADMIN_PRIVACY_POLICY_STATUS_ENABLED',
            '✅政策已激活并向用户显示。',
        )
    elif policy and policy.is_enabled:
        status_text = texts.t(
            'ADMIN_PRIVACY_POLICY_STATUS_ENABLED_EMPTY',
            '⚠️政策已启用，但文本为空—用户将看不到。',
        )

    updated_at = _format_timestamp(getattr(policy, 'updated_at', None))
    updated_block = ''
    if updated_at:
        updated_block = texts.t(
            'ADMIN_PRIVACY_POLICY_UPDATED_AT',
            '最后更新：{timestamp}',
        ).format(timestamp=updated_at)

    preview_block = texts.t(
        'ADMIN_PRIVACY_POLICY_PREVIEW_EMPTY',
        '尚未设置文本。',
    )
    if has_content:
        preview_title = texts.t(
            'ADMIN_PRIVACY_POLICY_PREVIEW_TITLE',
            '<b>文本预览：</b>',
        )
        preview_raw = policy.content.strip()
        preview_trimmed = preview_raw[:400]
        if len(preview_raw) > 400:
            preview_trimmed += '...'
        preview_block = f'{preview_title}\n<code>{html.escape(preview_trimmed)}</code>'

    language_block = texts.t(
        'ADMIN_PRIVACY_POLICY_LANGUAGE',
        '语言：<code>{lang}</code>',
    ).format(lang=normalized_language)

    header = texts.t(
        'ADMIN_PRIVACY_POLICY_HEADER',
        '🛡️<b>隐私政策</b>',
    )
    actions_prompt = texts.t(
        'ADMIN_PRIVACY_POLICY_ACTION_PROMPT',
        '请选择操作：',
    )

    message_parts = [
        header,
        description,
        language_block,
        status_text,
    ]

    if updated_block:
        message_parts.append(updated_block)

    message_parts.append(preview_block)
    message_parts.append(actions_prompt)

    overview_text = '\n\n'.join(part for part in message_parts if part)

    buttons: list[list[types.InlineKeyboardButton]] = []

    buttons.append(
        [
            types.InlineKeyboardButton(
                text=texts.t(
                    'ADMIN_PRIVACY_POLICY_EDIT_BUTTON',
                    '✏️编辑文本',
                ),
                callback_data='admin_privacy_policy_edit',
            )
        ]
    )

    if has_content:
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        'ADMIN_PRIVACY_POLICY_VIEW_BUTTON',
                        '👀查看当前文本',
                    ),
                    callback_data='admin_privacy_policy_view',
                )
            ]
        )

    toggle_text = texts.t(
        'ADMIN_PRIVACY_POLICY_ENABLE_BUTTON',
        '✅启用显示',
    )
    if policy and policy.is_enabled:
        toggle_text = texts.t(
            'ADMIN_PRIVACY_POLICY_DISABLE_BUTTON',
            '🚫禁用显示',
        )

    buttons.append(
        [
            types.InlineKeyboardButton(
                text=toggle_text,
                callback_data='admin_privacy_policy_toggle',
            )
        ]
    )

    buttons.append(
        [
            types.InlineKeyboardButton(
                text=texts.t(
                    'ADMIN_PRIVACY_POLICY_HTML_HELP',
                    'ℹ️标记帮助',
                ),
                callback_data='admin_privacy_policy_help',
            )
        ]
    )

    buttons.append(
        [
            types.InlineKeyboardButton(
                text=texts.BACK,
                callback_data='admin_submenu_settings',
            )
        ]
    )

    return overview_text, types.InlineKeyboardMarkup(inline_keyboard=buttons), policy


@admin_required
@error_handler
async def show_privacy_policy_management(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    overview_text, markup, _ = await _build_overview(db_user, db)

    await callback.message.edit_text(
        overview_text,
        reply_markup=markup,
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_privacy_policy(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    updated_policy = await PrivacyPolicyService.toggle_enabled(db, db_user.language)
    logger.info(
        '管理员 %s 切换了隐私策略的显示：%s',
        db_user.telegram_id,
        'enabled' if updated_policy.is_enabled else 'disabled',
    )
    status_message = (
        texts.t('ADMIN_PRIVACY_POLICY_ENABLED', '✅政策已启用')
        if updated_policy.is_enabled
        else texts.t('ADMIN_PRIVACY_POLICY_DISABLED', '🚫政策已禁用')
    )

    overview_text, markup, _ = await _build_overview(db_user, db)
    await callback.message.edit_text(
        overview_text,
        reply_markup=markup,
    )
    await callback.answer(status_message)


@admin_required
@error_handler
async def start_edit_privacy_policy(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    policy = await PrivacyPolicyService.get_policy(
        db,
        db_user.language,
        fallback=False,
    )

    current_preview = ''
    if policy and policy.content:
        preview = policy.content.strip()[:400]
        if len(policy.content.strip()) > 400:
            preview += '...'
        current_preview = (
            texts.t(
                'ADMIN_PRIVACY_POLICY_CURRENT_PREVIEW',
                '当前文本（预览）：',
            )
            + f'\n<code>{html.escape(preview)}</code>\n\n'
        )

    prompt = texts.t(
        'ADMIN_PRIVACY_POLICY_EDIT_PROMPT',
        '请发送新的隐私政策文本。允许使用标记标记。',
    )

    hint = texts.t(
        'ADMIN_PRIVACY_POLICY_EDIT_HINT',
        '使用/html_help获取标签帮助。',
    )

    message_text = (
        f'📝 <b>{texts.t("ADMIN_PRIVACY_POLICY_EDIT_TITLE", '编辑政策')}</b>\n\n'
        f'{current_preview}{prompt}\n\n{hint}'
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        'ADMIN_PRIVACY_POLICY_HTML_HELP',
                        'ℹ️标记帮助',
                    ),
                    callback_data='admin_privacy_policy_help',
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t('ADMIN_PRIVACY_POLICY_CANCEL', '❌取消'),
                    callback_data='admin_privacy_policy_cancel',
                )
            ],
        ]
    )

    await callback.message.edit_text(message_text, reply_markup=keyboard)
    await state.set_state(AdminStates.editing_privacy_policy)
    await callback.answer()


@admin_required
@error_handler
async def cancel_edit_privacy_policy(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    await state.clear()
    overview_text, markup, _ = await _build_overview(db_user, db)
    await callback.message.edit_text(
        overview_text,
        reply_markup=markup,
    )
    await callback.answer()


@admin_required
@error_handler
async def process_privacy_policy_edit(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    new_text = message.text or ''

    if len(new_text) > 4000:
        await message.answer(
            texts.t(
                'ADMIN_PRIVACY_POLICY_TOO_LONG',
                '❌政策文本太长。最多4000个字符。',
            )
        )
        return

    is_valid, error_message = validate_html_tags(new_text)
    if not is_valid:
        await message.answer(
            texts.t(
                'ADMIN_PRIVACY_POLICY_HTML_ERROR',
                '❌标记错误：{error}',
            ).format(error=error_message)
        )
        return

    await PrivacyPolicyService.save_policy(db, db_user.language, new_text)
    logger.info(
        '管理员已更新隐私政策的文本（符号）',
        telegram_id=db_user.telegram_id,
        new_text_count=len(new_text),
    )
    await state.clear()

    success_text = texts.t(
        'ADMIN_PRIVACY_POLICY_SAVED',
        '✅隐私政策已更新。',
    )

    reply_markup = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        'ADMIN_PRIVACY_POLICY_BACK_BUTTON',
                        '⬅️返回政策设置',
                    ),
                    callback_data='admin_privacy_policy',
                )
            ]
        ]
    )

    await message.answer(success_text, reply_markup=reply_markup)


@admin_required
@error_handler
async def view_privacy_policy(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    policy = await PrivacyPolicyService.get_policy(
        db,
        db_user.language,
        fallback=False,
    )

    if not policy or not policy.content or not policy.content.strip():
        await callback.answer(
            texts.t(
                'ADMIN_PRIVACY_POLICY_PREVIEW_EMPTY_ALERT',
                '尚未设置政策文本。',
            ),
            show_alert=True,
        )
        return

    content = policy.content.strip()
    truncated = False
    max_length = 3800
    if len(content) > max_length:
        content = content[: max_length - 3] + '...'
        truncated = True

    header = texts.t(
        'ADMIN_PRIVACY_POLICY_VIEW_TITLE',
        '👀<b>当前政策文本</b>',
    )

    note = ''
    if truncated:
        note = texts.t(
            'ADMIN_PRIVACY_POLICY_VIEW_TRUNCATED',
            '\n\n⚠️文本已缩短以便显示。用户将在菜单中看到完整版本。',
        )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        'ADMIN_PRIVACY_POLICY_BACK_BUTTON',
                        '⬅️返回政策设置',
                    ),
                    callback_data='admin_privacy_policy',
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        'ADMIN_PRIVACY_POLICY_EDIT_BUTTON',
                        '✏️编辑文本',
                    ),
                    callback_data='admin_privacy_policy_edit',
                )
            ],
        ]
    )

    await callback.message.edit_text(
        f'{header}\n\n{content}{note}',
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def show_privacy_policy_html_help(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    help_text = get_html_help_text()

    current_state = await state.get_state()

    buttons: list[list[types.InlineKeyboardButton]] = []

    if current_state == AdminStates.editing_privacy_policy.state:
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text=texts.t(
                        'ADMIN_PRIVACY_POLICY_RETURN_TO_EDIT',
                        '⬅️返回编辑',
                    ),
                    callback_data='admin_privacy_policy_edit',
                )
            ]
        )

    buttons.append(
        [
            types.InlineKeyboardButton(
                text=texts.t(
                    'ADMIN_PRIVACY_POLICY_BACK_BUTTON',
                    '⬅️返回政策设置',
                ),
                callback_data='admin_privacy_policy',
            )
        ]
    )

    await callback.message.edit_text(
        help_text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        show_privacy_policy_management,
        F.data == 'admin_privacy_policy',
    )
    dp.callback_query.register(
        toggle_privacy_policy,
        F.data == 'admin_privacy_policy_toggle',
    )
    dp.callback_query.register(
        start_edit_privacy_policy,
        F.data == 'admin_privacy_policy_edit',
    )
    dp.callback_query.register(
        cancel_edit_privacy_policy,
        F.data == 'admin_privacy_policy_cancel',
    )
    dp.callback_query.register(
        view_privacy_policy,
        F.data == 'admin_privacy_policy_view',
    )
    dp.callback_query.register(
        show_privacy_policy_html_help,
        F.data == 'admin_privacy_policy_help',
    )

    dp.message.register(
        process_privacy_policy_edit,
        AdminStates.editing_privacy_policy,
    )
