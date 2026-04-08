import asyncio
import html
from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import InterfaceError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.subscription import get_expiring_subscriptions
from app.database.crud.tariff import get_all_tariffs
from app.database.crud.user import get_users_list
from app.database.database import AsyncSessionLocal
from app.database.models import (
    BroadcastHistory,
    Subscription,
    SubscriptionStatus,
    User,
    UserStatus,
)
from app.keyboards.admin import (
    BROADCAST_BUTTON_ROWS,
    DEFAULT_BROADCAST_BUTTONS,
    get_admin_messages_keyboard,
    get_broadcast_button_config,
    get_broadcast_button_labels,
    get_broadcast_history_keyboard,
    get_broadcast_media_keyboard,
    get_broadcast_target_keyboard,
    get_custom_criteria_keyboard,
    get_media_confirm_keyboard,
    get_pinned_message_keyboard,
    get_updated_message_buttons_selector_keyboard_with_media,
)
from app.localization.texts import get_texts
from app.services.pinned_message_service import (
    broadcast_pinned_message,
    get_active_pinned_message,
    set_active_pinned_message,
    unpin_active_pinned_message,
)
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler
from app.utils.display_names import escape_display_name
from app.utils.miniapp_buttons import BUTTON_KEY_TO_CABINET_PATH, build_miniapp_or_callback_button


logger = structlog.get_logger(__name__)


async def safe_edit_or_send_text(callback: types.CallbackQuery, text: str, reply_markup=None, parse_mode: str = 'HTML'):
    """
    Безопасно редактирует сообщение или удаляет и отправляет новое.
    Нужно для случаев, когда текущее сообщение - медиа (фото/видео),
    которое нельзя отредактировать через edit_text.
    """
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if 'there is no text in the message to edit' in str(e):
            # Сообщение - медиа без текста, удаляем и отправляем новое
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.bot.send_message(
                chat_id=callback.message.chat.id, text=text, reply_markup=reply_markup, parse_mode=parse_mode
            )
        else:
            raise


BUTTON_ROWS = BROADCAST_BUTTON_ROWS
DEFAULT_SELECTED_BUTTONS = DEFAULT_BROADCAST_BUTTONS

CABINET_MINIAPP_BUTTON_KEYS = {
    'balance',
    'referrals',
    'promocode',
    'connect',
    'subscription',
    'support',
}


def get_message_buttons_selector_keyboard(language: str = 'ru') -> types.InlineKeyboardMarkup:
    return get_updated_message_buttons_selector_keyboard(list(DEFAULT_SELECTED_BUTTONS), language)


def get_updated_message_buttons_selector_keyboard(
    selected_buttons: list, language: str = 'ru'
) -> types.InlineKeyboardMarkup:
    return get_updated_message_buttons_selector_keyboard_with_media(selected_buttons, False, language)


def create_broadcast_keyboard(
    selected_buttons: list,
    language: str = 'ru',
    custom_buttons: list[dict] | None = None,
) -> types.InlineKeyboardMarkup | None:
    selected_buttons = selected_buttons or []
    keyboard: list[list[types.InlineKeyboardButton]] = []
    button_config_map = get_broadcast_button_config(language)

    for row in BUTTON_ROWS:
        row_buttons: list[types.InlineKeyboardButton] = []
        for button_key in row:
            if button_key not in selected_buttons:
                continue
            button_config = button_config_map[button_key]
            if settings.is_cabinet_mode() and button_key in CABINET_MINIAPP_BUTTON_KEYS:
                row_buttons.append(
                    build_miniapp_or_callback_button(
                        text=button_config['text'],
                        callback_data=button_config['callback'],
                        cabinet_path=BUTTON_KEY_TO_CABINET_PATH.get(button_key, ''),
                    )
                )
            else:
                row_buttons.append(
                    types.InlineKeyboardButton(text=button_config['text'], callback_data=button_config['callback'])
                )
        if row_buttons:
            keyboard.append(row_buttons)

    # Append custom buttons (each on its own row)
    if custom_buttons:
        for btn in custom_buttons:
            label = btn.get('label', '')
            action_type = btn.get('action_type', 'callback')
            action_value = btn.get('action_value', '')
            if not label or not action_value:
                continue
            if action_type == 'url':
                keyboard.append([types.InlineKeyboardButton(text=label, url=action_value)])
            else:
                # callback type
                keyboard.append([types.InlineKeyboardButton(text=label, callback_data=action_value)])

    if not keyboard:
        return None

    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _persist_broadcast_result(
    broadcast_id: int,
    sent_count: int,
    failed_count: int,
    status: str,
    blocked_count: int = 0,
) -> None:
    """
    Сохраняет результаты рассылки в НОВОЙ сессии.

    ВАЖНО: Используем свежую сессию вместо переданной, потому что за время
    долгой рассылки (минуты/часы) оригинальное соединение гарантированно
    закроется по таймауту PostgreSQL (idle_in_transaction_session_timeout).

    Args:
        broadcast_id: ID записи BroadcastHistory (не ORM-объект!)
        sent_count: Количество успешно отправленных сообщений
        failed_count: Количество неудачных отправок
        status: Финальный статус рассылки ('completed', 'partial', 'failed')
        blocked_count: Количество пользователей, заблокировавших бота
    """
    completed_at = datetime.now(UTC)
    max_retries = 3
    retry_delay = 1.0

    for attempt in range(1, max_retries + 1):
        try:
            async with AsyncSessionLocal() as session:
                broadcast_history = await session.get(BroadcastHistory, broadcast_id)
                if not broadcast_history:
                    logger.critical(
                        '找不到 BroadcastHistory # 条目来记录结果', broadcast_id=broadcast_id
                    )
                    return

                broadcast_history.sent_count = sent_count
                broadcast_history.failed_count = failed_count
                broadcast_history.blocked_count = blocked_count
                broadcast_history.status = status
                broadcast_history.completed_at = completed_at
                await session.commit()

                logger.info(
                    '邮件结果已保存（id发送失败阻止状态=）',
                    broadcast_id=broadcast_id,
                    sent_count=sent_count,
                    failed_count=failed_count,
                    blocked_count=blocked_count,
                    status=status,
                )
                return

        except InterfaceError as error:
            logger.warning(
                '保存邮件结果时连接错误（尝试/）',
                attempt=attempt,
                max_retries=max_retries,
                error=error,
            )
            if attempt < max_retries:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                logger.critical(
                    '尝试保存邮件结果失败 (id=)',
                    max_retries=max_retries,
                    broadcast_id=broadcast_id,
                )

        except Exception as error:
            logger.critical(
                '保存邮件结果时出现意外错误 (id=)',
                broadcast_id=broadcast_id,
                exc_info=error,
            )
            return


@admin_required
@error_handler
async def show_messages_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    text = '📨 <b>Mail邮件管理</b>\n\n选择邮寄类型：\n\n- <b>所有用户</b> - 邮寄给所有活跃用户\n- <b>按订阅</b> - 按订阅类型过滤\n- <b>按标准</b> - 自定义过滤器\n- <b>H历史</b> - 查看以前的邮件\n\n⚠️群发邮件要小心！'

    await safe_edit_or_send_text(
        callback, text, reply_markup=get_admin_messages_keyboard(db_user.language), parse_mode='HTML'
    )
    await callback.answer()


@admin_required
@error_handler
async def show_pinned_message_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    await state.clear()
    pinned_message = await get_active_pinned_message(db)

    if pinned_message:
        content_preview = html.escape(pinned_message.content or '')
        last_updated = pinned_message.updated_at or pinned_message.created_at
        timestamp_text = last_updated.strftime('%d.%m.%Y %H:%M') if last_updated else '—'
        media_line = ''
        if pinned_message.media_type:
            media_label = '图片' if pinned_message.media_type == 'photo' else '视频'
            media_line = f'📎 媒体：{media_label}\n'
        position_line = '⬆️ 在菜单前发送' if pinned_message.send_before_menu else '⬇️ 在菜单后发送'
        start_mode_line = (
            '🔁 每次 /start 都发送' if pinned_message.send_on_every_start else '🚫 仅首次和更新时发送'
        )
        body = (
            '📌 <b>置顶消息</b>\n\n'
            '📝 当前文本：\n'
            f'<code>{content_preview}</code>\n\n'
            f'{media_line}'
            f'{position_line}\n'
            f'{start_mode_line}\n'
            f'🕒 更新时间：{timestamp_text}'
        )
    else:
        body = (
            '📌 <b>置顶消息</b>\n\n'
            '尚未设置消息。发送新文本后即可群发并置顶给用户。'
        )

    await callback.message.edit_text(
        body,
        reply_markup=get_pinned_message_keyboard(
            db_user.language,
            send_before_menu=getattr(pinned_message, 'send_before_menu', True),
            send_on_every_start=getattr(pinned_message, 'send_on_every_start', True),
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def prompt_pinned_message_update(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
):
    await state.set_state(AdminStates.editing_pinned_message)
    await callback.message.edit_text(
        '✏️ <b>新置顶消息</b>\n\n发送您想要固定的文本、照片或视频。\n该机器人会将其发送给所有活跃用户，取消固定旧用户并固定新用户，而无需通知。',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_pinned_message')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_pinned_message_position(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    pinned_message = await get_active_pinned_message(db)
    if not pinned_message:
        await callback.answer('首先设置固定消息', show_alert=True)
        return

    pinned_message.send_before_menu = not pinned_message.send_before_menu
    pinned_message.updated_at = datetime.now(UTC)
    await db.commit()

    await show_pinned_message_menu(callback, db_user, db, state)


@admin_required
@error_handler
async def toggle_pinned_message_start_mode(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    pinned_message = await get_active_pinned_message(db)
    if not pinned_message:
        await callback.answer('首先设置固定消息', show_alert=True)
        return

    pinned_message.send_on_every_start = not pinned_message.send_on_every_start
    pinned_message.updated_at = datetime.now(UTC)
    await db.commit()

    await show_pinned_message_menu(callback, db_user, db, state)


@admin_required
@error_handler
async def delete_pinned_message(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    pinned_message = await get_active_pinned_message(db)
    if not pinned_message:
        await callback.answer('固定的消息不再存在', show_alert=True)
        return

    await callback.message.edit_text(
        '🗑️ <b>删除固定消息</b>\n\n等待机器人取消固定来自用户的消息...',
        parse_mode='HTML',
    )

    unpinned_count, failed_count, deleted = await unpin_active_pinned_message(
        callback.bot,
        db,
    )

    if not deleted:
        await callback.message.edit_text(
            '❌ 找不到要删除的活动固定消息',
            reply_markup=get_admin_messages_keyboard(db_user.language),
            parse_mode='HTML',
        )
        await state.clear()
        return

    total = unpinned_count + failed_count
    await callback.message.edit_text(
        f'✅ <b> 已删除置顶消息</b>\n\n👥 已处理的聊天记录：{total}\n✅ 取消固定：{unpinned_count}\n⚠️错误：{failed_count}\n\n可以使用“更新”按钮设置新消息。',
        reply_markup=get_admin_messages_keyboard(db_user.language),
        parse_mode='HTML',
    )
    await state.clear()


@admin_required
@error_handler
async def process_pinned_message_update(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    media_type: str | None = None
    media_file_id: str | None = None

    if message.photo:
        media_type = 'photo'
        media_file_id = message.photo[-1].file_id
    elif message.video:
        media_type = 'video'
        media_file_id = message.video.file_id

    pinned_text = message.html_text or message.caption_html or message.text or message.caption or ''

    if not pinned_text and not media_file_id:
        await message.answer(
            texts.t('ADMIN_PINNED_NO_CONTENT', '❌ 无法读取消息中的文本或媒体，请重试。')
        )
        return

    try:
        pinned_message = await set_active_pinned_message(
            db,
            pinned_text,
            db_user.id,
            media_type=media_type,
            media_file_id=media_file_id,
        )
    except ValueError as validation_error:
        await message.answer(f'❌ {validation_error}')
        return

    # Сообщение сохранено, спрашиваем о рассылке
    from app.keyboards.admin import get_pinned_broadcast_confirm_keyboard
    from app.states import AdminStates

    await message.answer(
        texts.t(
            'ADMIN_PINNED_SAVED_ASK_BROADCAST',
            '📌 <b>消息已保存！</b>\n\n选择如何向用户发送消息：\n\n• <b>立即广播</b> — 将发送并置顶给所有活跃用户\n• <b>仅在 启动命令 时</b> — 用户将在下次启动机器人时看到',
        ),
        reply_markup=get_pinned_broadcast_confirm_keyboard(db_user.language, pinned_message.id),
        parse_mode='HTML',
    )
    await state.set_state(AdminStates.confirming_pinned_broadcast)


@admin_required
@error_handler
async def handle_pinned_broadcast_now(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Разослать закреплённое сообщение сейчас всем пользователям."""
    texts = get_texts(db_user.language)

    # Получаем ID сообщения из callback_data
    pinned_message_id = int(callback.data.split(':')[1])

    # Получаем сообщение из БД
    from sqlalchemy import select

    from app.database.models import PinnedMessage

    result = await db.execute(select(PinnedMessage).where(PinnedMessage.id == pinned_message_id))
    pinned_message = result.scalar_one_or_none()

    if not pinned_message:
        await callback.answer('❌ 未找到消息', show_alert=True)
        await state.clear()
        return

    await callback.message.edit_text(
        texts.t('ADMIN_PINNED_SAVING', '📌 消息已保存。开始向用户发送并置顶...'),
        parse_mode='HTML',
    )

    sent_count, failed_count = await broadcast_pinned_message(
        callback.bot,
        db,
        pinned_message,
    )

    total = sent_count + failed_count
    await callback.message.edit_text(
        texts.t(
            'ADMIN_PINNED_UPDATED',
            '✅ <b>置顶消息已更新</b>\n\n👥 收件人: {total}\n✅ 已发送: {sent}\n⚠️ 错误: {failed}',
        ).format(total=total, sent=sent_count, failed=failed_count),
        reply_markup=get_admin_messages_keyboard(db_user.language),
        parse_mode='HTML',
    )
    await state.clear()


@admin_required
@error_handler
async def handle_pinned_broadcast_skip(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Пропустить рассылку — пользователи увидят при /start."""
    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        texts.t(
            'ADMIN_PINNED_SAVED_NO_BROADCAST',
            '✅ <b>置顶消息已保存</b>\n\n未执行广播。用户将在下次输入 启动命令 时看到消息。',
        ),
        reply_markup=get_admin_messages_keyboard(db_user.language),
        parse_mode='HTML',
    )
    await state.clear()


@admin_required
@error_handler
async def show_broadcast_targets(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    await callback.message.edit_text(
        '🎯 <b>目标受众选择</b>\n\n选择邮件的用户类别：',
        reply_markup=get_broadcast_target_keyboard(db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_tariff_filter(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Показывает список тарифов для фильтрации рассылки."""
    tariffs = await get_all_tariffs(db, include_inactive=False)

    if not tariffs:
        await callback.message.edit_text(
            '❌ <b>无可用套餐</b>\n\n在套餐管理部分创建套餐。',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_msg_by_sub')]]
            ),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    # Получаем количество подписчиков на каждом тарифе
    tariff_counts = {}
    for tariff in tariffs:
        count_query = select(func.count(Subscription.id)).where(
            Subscription.tariff_id == tariff.id,
            Subscription.status == SubscriptionStatus.ACTIVE.value,
        )
        result = await db.execute(count_query)
        tariff_counts[tariff.id] = result.scalar() or 0

    buttons = []
    for tariff in tariffs:
        count = tariff_counts.get(tariff.id, 0)
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text=f'{tariff.name}（{count}人）', callback_data=f'broadcast_tariff_{tariff.id}'
                )
            ]
        )

    buttons.append([types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_msg_by_sub')])

    await callback.message.edit_text(
        '📦 <b> 邮寄率 </b>\n\n选择要发送给有效订阅此套餐的用户的套餐：',
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_messages_history(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    page = 1
    if '_page_' in callback.data:
        page = int(callback.data.split('_page_')[1])

    limit = 10
    offset = (page - 1) * limit

    stmt = select(BroadcastHistory).order_by(BroadcastHistory.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    broadcasts = result.scalars().all()

    count_stmt = select(func.count(BroadcastHistory.id))
    count_result = await db.execute(count_stmt)
    total_count = count_result.scalar() or 0
    total_pages = (total_count + limit - 1) // limit

    if not broadcasts:
        text = '📋 <b> 邮寄历史 </b>\n\n❌ 邮件历史记录为空。\n提交您的第一份新闻通讯以在此处查看。'
        keyboard = [[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_messages')]]
    else:
        text = f'📋 <b>邮件历史</b>（第 {page}/{total_pages} 页）'

        for broadcast in broadcasts:
            status_emoji = '✅' if broadcast.status == 'completed' else '❌' if broadcast.status == 'failed' else '⏳'
            success_rate = (
                round((broadcast.sent_count / broadcast.total_count * 100), 1) if broadcast.total_count > 0 else 0
            )

            message_preview = (
                broadcast.message_text[:100] + '...'
                if broadcast.message_text and len(broadcast.message_text) > 100
                else (broadcast.message_text or '📊 投票')
            )

            import html

            message_preview = html.escape(message_preview)

            text += f"{status_emoji} <b>{broadcast.created_at.strftime('%d.%m.%Y %H:%M')}</b>\n📊 已发送：{broadcast.sent_count}/{broadcast.total_count} ({success_rate}%)\n🎯 受众：{get_target_name(broadcast.target_type)}\n👤 管理员：{html.escape(broadcast.admin_name or '')}\n📝 消息：{message_preview}\n──────────────────────────────────────────────────────────────────────────────────────────────"

        keyboard = get_broadcast_history_keyboard(page, total_pages, db_user.language).inline_keyboard

    await callback.message.edit_text(
        text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode='HTML'
    )
    await callback.answer()


@admin_required
@error_handler
async def show_custom_broadcast(callback: types.CallbackQuery, db_user: User, state: FSMContext, db: AsyncSession):
    stats = await get_users_statistics(db)

    text = f"📝 <b>按照标准分配</b>\n\n📊 <b>可用过滤器：</b>\n\n👥 <b> 报名方式：</b>\n• 今天：{stats['today']} 人。\n• 一周：{stats['week']} 人。\n• 一个月：{stats['month']} 人。\n\n💼 <b>按活动：</b>\n• 今天活跃：{stats['active_today']} 人。\n• 不活跃7 天以上：{stats['inactive_week']} 人。\n• 不活跃30 天以上：{stats['inactive_month']} 人。\n\n🔗 <b>来源：</b>\n• 通过推荐：{stats['referrals']} 人。\n•直接报名：{stats['direct']}人。\n\n选择过滤条件："

    await callback.message.edit_text(
        text, reply_markup=get_custom_criteria_keyboard(db_user.language), parse_mode='HTML'
    )
    await callback.answer()


@admin_required
@error_handler
async def select_custom_criteria(callback: types.CallbackQuery, db_user: User, state: FSMContext, db: AsyncSession):
    criteria = callback.data.replace('criteria_', '')

    criteria_names = {
        'today': '今日注册',
        'week': '近 7 天注册',
        'month': '近 30 天注册',
        'active_today': '今日活跃',
        'inactive_week': '7+ 天未活跃',
        'inactive_month': '30+ 天未活跃',
        'referrals': '通过推荐注册',
        'direct': '直接注册',
    }

    user_count = await get_custom_users_count(db, criteria)

    await state.update_data(broadcast_target=f'custom_{criteria}')

    await callback.message.edit_text(
        f'📨 <b>创建新闻通讯</b>\n\n🎯 <b>条件：</b> {criteria_names.get(criteria, criteria)}\n👥 <b> 收件人：</b> {user_count}\n\n输入要发送的消息文本：\n\n<i>支持HTML标记</i>',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_messages')]]
        ),
        parse_mode='HTML',
    )

    await state.set_state(AdminStates.waiting_for_broadcast_message)
    await callback.answer()


@admin_required
@error_handler
async def select_broadcast_target(callback: types.CallbackQuery, db_user: User, state: FSMContext, db: AsyncSession):
    raw_target = callback.data[len('broadcast_') :]
    target_aliases = {
        'no_sub': 'no',
    }
    target = target_aliases.get(raw_target, raw_target)

    target_names = {
        'all': '全部用户',
        'active': '有有效订阅的用户',
        'trial': '有试用订阅的用户',
        'no': '无订阅用户',
        'expiring': '即将到期的订阅用户',
        'expired': '已过期订阅用户',
        'active_zero': '有效订阅且流量为 0 GB',
        'trial_zero': '试用订阅且流量为 0 GB',
    }

    # Обработка фильтра по тарифу
    target_name = target_names.get(target, target)
    if target.startswith('tariff_'):
        tariff_id = int(target.split('_')[1])
        from app.database.crud.tariff import get_tariff_by_id

        tariff = await get_tariff_by_id(db, tariff_id)
        if tariff:
            target_name = f'套餐「{escape_display_name(tariff.name)}」'
        else:
            target_name = f'套餐 #{tariff_id}'

    user_count = await get_target_users_count(db, target)

    await state.update_data(broadcast_target=target)

    await callback.message.edit_text(
        f'📨 <b>创建新闻通讯</b>\n\n🎯 <b>观众：</b> {target_name}\n👥 <b> 收件人：</b> {user_count}\n\n输入要发送的消息文本：\n\n<i>支持HTML标记</i>',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_messages')]]
        ),
        parse_mode='HTML',
    )

    await state.set_state(AdminStates.waiting_for_broadcast_message)
    await callback.answer()


@admin_required
@error_handler
async def process_broadcast_message(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    broadcast_text = message.text

    if len(broadcast_text) > 4000:
        await message.answer('❌ 讯息太长（最多 4000 个字符）')
        return

    await state.update_data(broadcast_message=broadcast_text)

    await message.answer(
        '🖼️ <b>添加媒体文件</b>\n\n您可以将照片、视频或文档添加到消息中。\n或者跳过这一步。\n\n选择媒体类型：',
        reply_markup=get_broadcast_media_keyboard(db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def handle_media_selection(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    if callback.data == 'skip_media':
        await state.update_data(has_media=False)
        await show_button_selector_callback(callback, db_user, state)
        return

    media_type = callback.data.replace('add_media_', '')

    media_instructions = {
        'photo': '📷 请发送要群发的图片：',
        'video': '🎥 请发送要群发的视频：',
        'document': '📄 请发送要群发的文件：',
    }

    await state.update_data(media_type=media_type, waiting_for_media=True)

    instruction_text = (
        f'{media_instructions.get(media_type, "请发送媒体文件：")}\n\n<i>文件大小不能超过 50 MB</i>'
    )
    instruction_keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_messages')]]
    )

    # Проверяем, является ли текущее сообщение медиа-сообщением
    is_media_message = (
        callback.message.photo
        or callback.message.video
        or callback.message.document
        or callback.message.animation
        or callback.message.audio
        or callback.message.voice
    )

    if is_media_message:
        # Удаляем медиа-сообщение и отправляем новое текстовое
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(instruction_text, reply_markup=instruction_keyboard, parse_mode='HTML')
    else:
        await callback.message.edit_text(instruction_text, reply_markup=instruction_keyboard, parse_mode='HTML')

    await state.set_state(AdminStates.waiting_for_broadcast_media)
    await callback.answer()


@admin_required
@error_handler
async def process_broadcast_media(message: types.Message, db_user: User, state: FSMContext):
    data = await state.get_data()
    expected_type = data.get('media_type')

    media_file_id = None
    media_type = None

    if message.photo and expected_type == 'photo':
        media_file_id = message.photo[-1].file_id
        media_type = 'photo'
    elif message.video and expected_type == 'video':
        media_file_id = message.video.file_id
        media_type = 'video'
    elif message.document and expected_type == 'document':
        media_file_id = message.document.file_id
        media_type = 'document'
    else:
        await message.answer(f'❌ 请按照说明发送{expected_type}。')
        return

    await state.update_data(
        has_media=True, media_file_id=media_file_id, media_type=media_type, media_caption=message.caption
    )

    await show_media_preview(message, db_user, state)


async def show_media_preview(message: types.Message, db_user: User, state: FSMContext):
    data = await state.get_data()
    media_type = data.get('media_type')
    media_file_id = data.get('media_file_id')

    preview_text = (
        f'🖼️ <b>媒体文件已添加</b>\n\n'
        f'📎 <b>类型：</b> {media_type}\n'
        f'✅ 文件已保存并可发送\n\n'
        f'接下来要做什么？'
    )

    # Для предпросмотра рассылки используем оригинальный метод без патчинга логотипа
    # чтобы показать именно загруженное фото
    from app.utils.message_patch import _original_answer

    if media_type == 'photo' and media_file_id:
        # Показываем предпросмотр с загруженным фото
        await message.bot.send_photo(
            chat_id=message.chat.id,
            photo=media_file_id,
            caption=preview_text,
            reply_markup=get_media_confirm_keyboard(db_user.language),
            parse_mode='HTML',
        )
    else:
        # Для других типов медиа или если нет фото, используем обычное сообщение
        await _original_answer(
            message, preview_text, reply_markup=get_media_confirm_keyboard(db_user.language), parse_mode='HTML'
        )


@admin_required
@error_handler
async def handle_media_confirmation(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    action = callback.data

    if action == 'confirm_media':
        await show_button_selector_callback(callback, db_user, state)
    elif action == 'replace_media':
        data = await state.get_data()
        data.get('media_type', 'photo')
        await handle_media_selection(callback, db_user, state)
    elif action == 'skip_media':
        await state.update_data(has_media=False, media_file_id=None, media_type=None, media_caption=None)
        await show_button_selector_callback(callback, db_user, state)


@admin_required
@error_handler
async def handle_change_media(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    await safe_edit_or_send_text(
        callback,
        '🖼️ <b>更改媒体文件</b>\n\n选择新媒体类型：',
        reply_markup=get_broadcast_media_keyboard(db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_button_selector_callback(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    data = await state.get_data()
    has_media = data.get('has_media', False)
    selected_buttons = data.get('selected_buttons')

    if selected_buttons is None:
        selected_buttons = list(DEFAULT_SELECTED_BUTTONS)
        await state.update_data(selected_buttons=selected_buttons)

    media_info = ''
    if has_media:
        media_type = data.get('media_type', '文件')
        media_info = f'\n🖼️ <b>媒体文件：</b> 已添加 {media_type}'

    text = f'📘 <b>附加按钮选择</b>\n\n选择将添加到群发消息中的按钮：\n\n💰 <b>余额充值</b> - 打开充值方式\n🤝 <b>推广计划</b> - 打开推荐计划页面\n🎫 <b>促销代码</b> - 打开促销代码输入页面\n🔗 <b>连接帮助</b> - 打开连接说明\n📱 <b>我的订阅</b> - 显示订阅状态\n🛠️ <b>技术支持</b> - 联系客服\n\n🏠 <b>“首页”按钮</b> 默认启用，如有需要你也可以关闭它。{media_info}\n\n选择需要的按钮后点击“继续”：'

    keyboard = get_updated_message_buttons_selector_keyboard_with_media(selected_buttons, has_media, db_user.language)

    # Проверяем, является ли текущее сообщение медиа-сообщением
    # (фото, видео, документ и т.д.) - для них нельзя использовать edit_text
    is_media_message = (
        callback.message.photo
        or callback.message.video
        or callback.message.document
        or callback.message.animation
        or callback.message.audio
        or callback.message.voice
    )

    if is_media_message:
        # Удаляем медиа-сообщение и отправляем новое текстовое
        try:
            await callback.message.delete()
        except Exception:
            pass  # Игнорируем ошибки удаления
        await callback.message.answer(text, reply_markup=keyboard, parse_mode='HTML')
    else:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer()


@admin_required
@error_handler
async def show_button_selector(message: types.Message, db_user: User, state: FSMContext):
    data = await state.get_data()
    selected_buttons = data.get('selected_buttons')
    if selected_buttons is None:
        selected_buttons = list(DEFAULT_SELECTED_BUTTONS)
        await state.update_data(selected_buttons=selected_buttons)

    has_media = data.get('has_media', False)

    text = '📘 <b>附加按钮选择</b>\n\n选择将添加到群发消息中的按钮：\n\n💰 <b>余额充值</b> - 打开充值方式\n🤝 <b>推广计划</b> - 打开推荐计划页面\n🎫 <b>促销代码</b> - 打开促销代码输入页面\n🔗 <b>连接帮助</b> - 打开连接说明\n📱 <b>我的订阅</b> - 显示订阅状态\n🛠️ <b>技术支持</b> - 联系客服\n\n🏠 <b>“首页”按钮</b> 默认启用，如有需要你也可以关闭它。\n\n选择需要的按钮后点击“继续”：'

    keyboard = get_updated_message_buttons_selector_keyboard_with_media(selected_buttons, has_media, db_user.language)

    await message.answer(text, reply_markup=keyboard, parse_mode='HTML')


@admin_required
@error_handler
async def toggle_button_selection(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    button_type = callback.data.replace('btn_', '')
    data = await state.get_data()
    selected_buttons = data.get('selected_buttons')
    if selected_buttons is None:
        selected_buttons = list(DEFAULT_SELECTED_BUTTONS)
    else:
        selected_buttons = list(selected_buttons)

    if button_type in selected_buttons:
        selected_buttons.remove(button_type)
    else:
        selected_buttons.append(button_type)

    await state.update_data(selected_buttons=selected_buttons)

    has_media = data.get('has_media', False)
    keyboard = get_updated_message_buttons_selector_keyboard_with_media(selected_buttons, has_media, db_user.language)

    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def confirm_button_selection(callback: types.CallbackQuery, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()
    target = data.get('broadcast_target')
    message_text = data.get('broadcast_message')
    selected_buttons = data.get('selected_buttons')
    if selected_buttons is None:
        selected_buttons = list(DEFAULT_SELECTED_BUTTONS)
        await state.update_data(selected_buttons=selected_buttons)
    has_media = data.get('has_media', False)
    media_type = data.get('media_type')

    user_count = (
        await get_target_users_count(db, target)
        if not target.startswith('custom_')
        else await get_custom_users_count(db, target.replace('custom_', ''))
    )
    target_display = get_target_display_name(target)

    media_info = ''
    if has_media:
        media_type_names = {'photo': '图片', 'video': '视频', 'document': '文件'}
        media_info = f'\n🖼️ <b>媒体文件：</b> {media_type_names.get(media_type, media_type)}'

    ordered_keys = [button_key for row in BUTTON_ROWS for button_key in row]
    button_labels = get_broadcast_button_labels(db_user.language)
    selected_names = [button_labels[key] for key in ordered_keys if key in selected_buttons]
    if selected_names:
        buttons_info = f'\n📘 <b>按钮：</b> {", ".join(selected_names)}'
    else:
        buttons_info = '\n📘 <b>按钮：</b> 无'

    preview_text = f"""
📨 <b>群发预览</b>

🎯 <b>受众：</b> {target_display}
👥 <b>接收人数：</b> {user_count}

📝 <b>消息内容：</b>
{message_text}{media_info}

{buttons_info}

确认发送吗？
"""

    keyboard = [
        [
            types.InlineKeyboardButton(text='✅ 发送', callback_data='admin_confirm_broadcast'),
            types.InlineKeyboardButton(text='📘 更改按钮', callback_data='edit_buttons'),
        ]
    ]

    if has_media:
        keyboard.append([types.InlineKeyboardButton(text='🖼️更换媒体', callback_data='change_media')])

    keyboard.append([types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_messages')])

    # Если есть медиа, показываем его с загруженным фото, иначе обычное текстовое сообщение
    if has_media and media_type == 'photo':
        media_file_id = data.get('media_file_id')
        if media_file_id:
            # Удаляем текущее сообщение и отправляем новое с фото
            try:
                await callback.message.delete()
            except Exception:
                pass
            # Telegram ограничивает caption 到 1024 символов
            if len(preview_text) <= 1024:
                await callback.bot.send_photo(
                    chat_id=callback.message.chat.id,
                    photo=media_file_id,
                    caption=preview_text,
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
                    parse_mode='HTML',
                )
            else:
                # Фото без caption + текст отдельным сообщением
                await callback.bot.send_photo(
                    chat_id=callback.message.chat.id,
                    photo=media_file_id,
                )
                await callback.bot.send_message(
                    chat_id=callback.message.chat.id,
                    text=preview_text,
                    reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
                    parse_mode='HTML',
                )
        else:
            # Если нет file_id, используем safe редактирование
            await safe_edit_or_send_text(
                callback,
                preview_text,
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
                parse_mode='HTML',
            )
    else:
        # Для текстовых сообщений или других типов медиа используем safe редактирование
        await safe_edit_or_send_text(
            callback, preview_text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode='HTML'
        )

    await callback.answer()


@admin_required
@error_handler
async def confirm_broadcast(callback: types.CallbackQuery, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()
    target = data.get('broadcast_target')
    message_text = data.get('broadcast_message')
    selected_buttons = data.get('selected_buttons')
    if selected_buttons is None:
        selected_buttons = list(DEFAULT_SELECTED_BUTTONS)
    has_media = data.get('has_media', False)
    media_type = data.get('media_type')
    media_file_id = data.get('media_file_id')
    media_caption = data.get('media_caption')

    # =========================================================================
    # КРИТИЧНО: Извлекаем ВСЕ скалярные значения из ORM-объектов СЕЙЧАС,
    # пока сессия активна. После начала рассылки соединение с БД может
    # закрыться по таймауту, и любое обращение к атрибутам ORM вызовет:
    # - MissingGreenlet (lazy loading вне async контекста)
    # - InterfaceError (соединение закрыто)
    # =========================================================================
    admin_id: int = db_user.id
    admin_name: str = db_user.full_name  # property, читает first_name/last_name
    admin_telegram_id: int | None = db_user.telegram_id
    admin_language: str = db_user.language

    await safe_edit_or_send_text(
        callback,
        '📨 <b> 邮寄准备...</b>\n\n⏳ 正在加载收件人列表...',
        reply_markup=None,
        parse_mode='HTML',
    )

    # Загружаем пользователей и сразу извлекаем telegram_id в список
    # чтобы не обращаться к ORM-объектам во время долгой рассылки
    if target.startswith('custom_'):
        users_orm = await get_custom_users(db, target.replace('custom_', ''))
    else:
        users_orm = await get_target_users(db, target)

    # Извлекаем только telegram_id - это всё что нужно для отправки
    # Фильтруем None (email-only пользователи)
    recipient_telegram_ids: list[int] = [user.telegram_id for user in users_orm if user.telegram_id is not None]
    total_users_count = len(users_orm)

    # Создаём запись истории рассылки
    broadcast_history = BroadcastHistory(
        target_type=target,
        message_text=message_text,
        has_media=has_media,
        media_type=media_type,
        media_file_id=media_file_id,
        media_caption=media_caption,
        total_count=total_users_count,
        sent_count=0,
        failed_count=0,
        admin_id=admin_id,
        admin_name=admin_name,
        status='in_progress',
    )
    db.add(broadcast_history)
    await db.commit()
    await db.refresh(broadcast_history)

    # Сохраняем ID - это единственное что нам нужно после коммита
    broadcast_id: int = broadcast_history.id

    # =========================================================================
    # С этого момента НЕ используем db сессию и ORM-объекты!
    # Работаем только со скалярными значениями.
    # =========================================================================

    sent_count = 0
    failed_count = 0

    broadcast_keyboard = create_broadcast_keyboard(selected_buttons, admin_language)

    # =========================================================================
    # Rate limiting: Telegram допускает ~30 msg/sec для бота.
    # Используем batch_size=25 + 1 сек задержка между батчами = ~25 msg/sec
    # с запасом, чтобы не получать FloodWait.
    # Semaphore=25 — все сообщения батча отправляются параллельно.
    # =========================================================================
    _BATCH_SIZE = 25
    _BATCH_DELAY = 1.0  # секунда между батчами
    _MAX_SEND_RETRIES = 3
    # Обновляем прогресс каждые N батчей (не каждое сообщение — иначе FloodWait на edit_text)
    _PROGRESS_UPDATE_INTERVAL = max(1, 500 // _BATCH_SIZE)  # ~каждые 500 сообщений
    # Минимальный интервал между обновлениями прогресса (секунды)
    _PROGRESS_MIN_INTERVAL = 5.0

    # Глобальная пауза при FloodWait — тормозим ВСЕ отправки, а не один сл起 семафора
    flood_wait_until: float = 0.0

    async def send_single_broadcast(telegram_id: int) -> str:
        """Отправляет одно сообщение. Возвращает 'sent', 'blocked' или 'failed'."""
        nonlocal flood_wait_until

        for attempt in range(_MAX_SEND_RETRIES):
            # Глобальная пауза при FloodWait
            now = asyncio.get_event_loop().time()
            if flood_wait_until > now:
                await asyncio.sleep(flood_wait_until - now)

            try:
                if has_media and media_file_id:
                    send_method = {
                        'photo': callback.bot.send_photo,
                        'video': callback.bot.send_video,
                        'document': callback.bot.send_document,
                    }.get(media_type)
                    if send_method:
                        media_kwarg = {
                            'photo': 'photo',
                            'video': 'video',
                            'document': 'document',
                        }[media_type]
                        # Telegram ограничивает caption 到 1024 символов
                        if len(message_text) <= 1024:
                            await send_method(
                                chat_id=telegram_id,
                                **{media_kwarg: media_file_id},
                                caption=message_text,
                                parse_mode='HTML',
                                reply_markup=broadcast_keyboard,
                            )
                        else:
                            # Медиа без caption + текст отдельным сообщением
                            await send_method(
                                chat_id=telegram_id,
                                **{media_kwarg: media_file_id},
                            )
                            await callback.bot.send_message(
                                chat_id=telegram_id,
                                text=message_text,
                                parse_mode='HTML',
                                reply_markup=broadcast_keyboard,
                            )
                    else:
                        # Неизвестный media_type — отправляем как текст
                        await callback.bot.send_message(
                            chat_id=telegram_id,
                            text=message_text,
                            parse_mode='HTML',
                            reply_markup=broadcast_keyboard,
                        )
                else:
                    await callback.bot.send_message(
                        chat_id=telegram_id,
                        text=message_text,
                        parse_mode='HTML',
                        reply_markup=broadcast_keyboard,
                    )
                return 'sent'

            except TelegramRetryAfter as e:
                # Глобальная пауза — тормозим все корутины
                wait_seconds = e.retry_after + 1
                flood_wait_until = asyncio.get_event_loop().time() + wait_seconds
                logger.warning(
                    'FloodWait：Telegram 要求等待秒（用户，尝试/）',
                    retry_after=e.retry_after,
                    telegram_id=telegram_id,
                    attempt=attempt + 1,
                    MAX_SEND_RETRIES=_MAX_SEND_RETRIES,
                )
                await asyncio.sleep(wait_seconds)

            except TelegramForbiddenError:
                return 'blocked'

            except TelegramBadRequest as e:
                err = str(e).lower()
                if 'bot was blocked' in err or 'user is deactivated' in err or 'chat not found' in err:
                    return 'blocked'
                logger.debug('发送给用户时的 BadRequest', telegram_id=telegram_id, e=e)
                return 'failed'

            except Exception as e:
                logger.error(
                    '发送给用户时出错（尝试/）',
                    telegram_id=telegram_id,
                    attempt=attempt + 1,
                    MAX_SEND_RETRIES=_MAX_SEND_RETRIES,
                    e=e,
                )
                if attempt < _MAX_SEND_RETRIES - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))

        return 'failed'

    # =========================================================================
    # Прогресс-бар в реальном времени (как в сканере заблокированных)
    # =========================================================================
    total_recipients = len(recipient_telegram_ids)
    last_progress_update: float = 0.0
    # ID сообщения, которое обновляем (может быть заменено при ошибке)
    progress_message = callback.message

    def _build_progress_text(
        current_sent: int,
        current_failed: int,
        total: int,
        phase: str = 'sending',
        current_blocked: int = 0,
    ) -> str:
        processed = current_sent + current_failed + current_blocked
        percent = round(processed / total * 100, 1) if total > 0 else 0
        bar_length = 20
        filled = int(bar_length * processed / total) if total > 0 else 0
        bar = '█' * filled + '░' * (bar_length - filled)

        if phase == 'sending':
            blocked_line = f'• 已屏蔽机器人：{current_blocked}\n' if current_blocked else ''
            return (
                f'📨 <b>群发进行中...</b>\n\n[{bar}] {percent}%\n\n📊 <b>进度：</b>\n• 已发送：{current_sent}\n{blocked_line}• 错误：{current_failed}\n• 已处理：{processed}/{total}\n\n⏳ 请勿关闭对话，群发仍在继续……'
            )
        return ''

    async def _update_progress_message(current_sent: int, current_failed: int, current_blocked: int = 0) -> None:
        """Безопасно обновляет сообщение с прогрессом."""
        nonlocal last_progress_update, progress_message
        now = asyncio.get_event_loop().time()
        if now - last_progress_update < _PROGRESS_MIN_INTERVAL:
            return
        last_progress_update = now

        text = _build_progress_text(current_sent, current_failed, total_recipients, current_blocked=current_blocked)
        try:
            await progress_message.edit_text(text, parse_mode='HTML')
        except TelegramRetryAfter as e:
            # Не паникуем — пропускаем обновление прогресса
            logger.debug('FloodWait 更新进度时，跳过：秒', retry_after=e.retry_after)
        except TelegramBadRequest:
            # Сообщение удалено или контент не изменился — отправляем новое
            try:
                progress_message = await callback.bot.send_message(
                    chat_id=callback.message.chat.id,
                    text=text,
                    parse_mode='HTML',
                )
            except Exception:
                pass
        except Exception:
            pass  # Не ломаем рассылку из-за ошибок обновления прогресса

    # Первое обновление прогресса
    await _update_progress_message(0, 0)

    blocked_count = 0
    blocked_telegram_ids: list[int] = []

    # =========================================================================
    # Основной цикл рассылки — батчами по _BATCH_SIZE
    # =========================================================================
    for batch_idx, i in enumerate(range(0, total_recipients, _BATCH_SIZE)):
        batch = recipient_telegram_ids[i : i + _BATCH_SIZE]

        # Отправляем батч параллельно
        results = await asyncio.gather(
            *[send_single_broadcast(tid) for tid in batch],
            return_exceptions=True,
        )

        for idx, result in enumerate(results):
            if isinstance(result, str):
                if result == 'sent':
                    sent_count += 1
                elif result == 'blocked':
                    blocked_count += 1
                    blocked_telegram_ids.append(batch[idx])
                else:
                    failed_count += 1
            elif isinstance(result, Exception):
                failed_count += 1
                logger.error('邮件列表中未处理的异常', result=result)

        # Обновляем прогресс каждые _PROGRESS_UPDATE_INTERVAL батчей
        if batch_idx % _PROGRESS_UPDATE_INTERVAL == 0:
            await _update_progress_message(sent_count, failed_count, blocked_count)

        # Задержка между батчами для соблюдения rate limits
        await asyncio.sleep(_BATCH_DELAY)

    # Учитываем пропущенных email-only пользователей
    skipped_email_users = total_users_count - total_recipients
    if skipped_email_users > 0:
        logger.info('发送消息时跳过仅使用电子邮件的用户', skipped_email_users=skipped_email_users)

    status = 'completed' if failed_count == 0 and blocked_count == 0 else 'partial'

    # Сохраняем результат в НОВОЙ сессии (старая уже мертва)
    await _persist_broadcast_result(
        broadcast_id=broadcast_id,
        sent_count=sent_count,
        failed_count=failed_count,
        status=status,
        blocked_count=blocked_count,
    )

    success_rate = round(sent_count / total_users_count * 100, 1) if total_users_count else 0
    media_info = f'\n🖼️ <b>媒体文件：</b> {media_type}' if has_media else ''
    blocked_line = f'• 已拉黑机器人：{blocked_count}\n' if blocked_count else ''

    result_text = (
        f'✅ <b>群发完成！</b>\n\n'
        f'📊 <b>结果：</b>\n'
        f'• 已发送：{sent_count}\n'
        f'{blocked_line}'
        f'• 发送失败：{failed_count}\n'
        f'• 总用户数：{total_users_count}\n'
        f'• 成功率：{success_rate}%{media_info}\n\n'
        f'<b>管理员：</b> {html.escape(admin_name)}'
    )

    back_keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text='📨 前往时事通讯', callback_data='admin_messages')]]
    )

    try:
        await progress_message.edit_text(result_text, reply_markup=back_keyboard, parse_mode='HTML')
    except TelegramBadRequest as e:
        error_msg = str(e).lower()
        if (
            'message to edit not found' in error_msg
            or 'there is no text' in error_msg
            or "message can't be edited" in error_msg
        ):
            await callback.bot.send_message(
                chat_id=callback.message.chat.id,
                text=result_text,
                reply_markup=back_keyboard,
                parse_mode='HTML',
            )
        else:
            raise

    await state.clear()
    logger.info(
        '管理员已完成邮件发送：发送失败总数=（媒体：）',
        admin_telegram_id=admin_telegram_id,
        sent_count=sent_count,
        failed_count=failed_count,
        total_users_count=total_users_count,
        has_media=has_media,
    )


async def get_target_users_count(db: AsyncSession, target: str) -> int:
    """Быстрый подсчёт пользователей через SQL COUNT вместо загрузки всех в память."""
    from sqlalchemy import distinct, func as sql_func

    base_filter = User.status == UserStatus.ACTIVE.value

    if target == 'all':
        query = select(sql_func.count(User.id)).where(base_filter)
        result = await db.execute(query)
        return result.scalar() or 0

    if target == 'active':
        # Активные платные подписки (не триал)
        query = (
            select(sql_func.count(distinct(User.id)))
            .join(Subscription, User.id == Subscription.user_id)
            .where(
                base_filter,
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.is_trial == False,
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    if target == 'trial':
        # Триальные подписки (без проверки is_active, как в оригинале)
        query = (
            select(sql_func.count(distinct(User.id)))
            .join(Subscription, User.id == Subscription.user_id)
            .where(
                base_filter,
                Subscription.is_trial == True,
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    if target == 'no':
        # Без активной подписки - используем NOT EXISTS для корректности
        subquery = (
            select(Subscription.id)
            .where(
                Subscription.user_id == User.id,
                Subscription.status == SubscriptionStatus.ACTIVE.value,
            )
            .exists()
        )
        query = select(sql_func.count(User.id)).where(base_filter, ~subquery)
        result = await db.execute(query)
        return result.scalar() or 0

    if target == 'expiring':
        # Истекающие в ближайшие 3 дня
        now = datetime.now(UTC)
        expiry_threshold = now + timedelta(days=3)
        query = (
            select(sql_func.count(distinct(User.id)))
            .join(Subscription, User.id == Subscription.user_id)
            .where(
                base_filter,
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.end_date <= expiry_threshold,
                Subscription.end_date > now,
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    if target == 'expiring_subscribers':
        # Истекающие в ближайшие 7 天
        now = datetime.now(UTC)
        expiry_threshold = now + timedelta(days=7)
        query = (
            select(sql_func.count(distinct(User.id)))
            .join(Subscription, User.id == Subscription.user_id)
            .where(
                base_filter,
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.end_date <= expiry_threshold,
                Subscription.end_date > now,
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    if target == 'expired':
        # Истекшие подписки
        now = datetime.now(UTC)
        expired_statuses = [
            SubscriptionStatus.EXPIRED.value,
            SubscriptionStatus.DISABLED.value,
            SubscriptionStatus.LIMITED.value,
        ]
        query = (
            select(sql_func.count(distinct(User.id)))
            .outerjoin(Subscription, User.id == Subscription.user_id)
            .where(
                base_filter,
                or_(
                    Subscription.status.in_(expired_statuses),
                    and_(Subscription.end_date <= now, Subscription.status != SubscriptionStatus.ACTIVE.value),
                    and_(Subscription.id == None, User.has_had_paid_subscription == True),
                ),
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    if target == 'expired_subscribers':
        # То же что и expired
        now = datetime.now(UTC)
        expired_statuses = [
            SubscriptionStatus.EXPIRED.value,
            SubscriptionStatus.DISABLED.value,
            SubscriptionStatus.LIMITED.value,
        ]
        query = (
            select(sql_func.count(distinct(User.id)))
            .outerjoin(Subscription, User.id == Subscription.user_id)
            .where(
                base_filter,
                or_(
                    Subscription.status.in_(expired_statuses),
                    and_(Subscription.end_date <= now, Subscription.status != SubscriptionStatus.ACTIVE.value),
                    and_(Subscription.id == None, User.has_had_paid_subscription == True),
                ),
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    if target == 'active_zero':
        # Активные платные с нулевым трафиком
        query = (
            select(sql_func.count(distinct(User.id)))
            .join(Subscription, User.id == Subscription.user_id)
            .where(
                base_filter,
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.is_trial == False,
                or_(Subscription.traffic_used_gb == None, Subscription.traffic_used_gb <= 0),
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    if target == 'trial_zero':
        # Триальные с нулевым трафиком
        query = (
            select(sql_func.count(distinct(User.id)))
            .join(Subscription, User.id == Subscription.user_id)
            .where(
                base_filter,
                Subscription.is_trial == True,
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                or_(Subscription.traffic_used_gb == None, Subscription.traffic_used_gb <= 0),
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    if target == 'zero':
        # Все активные с нулевым трафиком
        query = (
            select(sql_func.count(distinct(User.id)))
            .join(Subscription, User.id == Subscription.user_id)
            .where(
                base_filter,
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                or_(Subscription.traffic_used_gb == None, Subscription.traffic_used_gb <= 0),
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    # Фильтр по тарифу
    if target.startswith('tariff_'):
        tariff_id = int(target.split('_')[1])
        query = (
            select(sql_func.count(distinct(User.id)))
            .join(Subscription, User.id == Subscription.user_id)
            .where(
                base_filter,
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.tariff_id == tariff_id,
            )
        )
        result = await db.execute(query)
        return result.scalar() or 0

    # Custom filters — быстрый COUNT вместо загрузки всех пользователей
    if target.startswith('custom_'):
        now = datetime.now(UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        criteria = target[len('custom_') :]

        if criteria == 'today':
            query = select(sql_func.count(User.id)).where(base_filter, User.created_at >= today)
        elif criteria == 'week':
            query = select(sql_func.count(User.id)).where(base_filter, User.created_at >= now - timedelta(days=7))
        elif criteria == 'month':
            query = select(sql_func.count(User.id)).where(base_filter, User.created_at >= now - timedelta(days=30))
        elif criteria == 'active_today':
            query = select(sql_func.count(User.id)).where(base_filter, User.last_activity >= today)
        elif criteria == 'inactive_week':
            query = select(sql_func.count(User.id)).where(base_filter, User.last_activity < now - timedelta(days=7))
        elif criteria == 'inactive_month':
            query = select(sql_func.count(User.id)).where(base_filter, User.last_activity < now - timedelta(days=30))
        elif criteria == 'referrals':
            query = select(sql_func.count(User.id)).where(base_filter, User.referred_by_id.isnot(None))
        elif criteria == 'direct':
            query = select(sql_func.count(User.id)).where(base_filter, User.referred_by_id.is_(None))
        else:
            return 0

        result = await db.execute(query)
        return result.scalar() or 0

    return 0


async def get_target_users(db: AsyncSession, target: str) -> list:
    # Загружаем всех активных пользователей батчами, чтобы не ограничиваться 10к
    users: list[User] = []
    offset = 0
    batch_size = 5000

    while True:
        batch = await get_users_list(
            db,
            offset=offset,
            limit=batch_size,
            status=UserStatus.ACTIVE,
        )

        if not batch:
            break

        users.extend(batch)
        offset += batch_size

    if target == 'all':
        return users

    if target == 'active':
        return [
            user
            for user in users
            if any(s.is_active and not s.is_trial for s in (getattr(user, 'subscriptions', None) or []))
        ]

    if target == 'trial':
        return [user for user in users if any(s.is_trial for s in (getattr(user, 'subscriptions', None) or []))]

    if target == 'no':
        return [user for user in users if not any(s.is_active for s in (getattr(user, 'subscriptions', None) or []))]

    if target == 'expiring':
        expiring_subs = await get_expiring_subscriptions(db, 3)
        return [sub.user for sub in expiring_subs if sub.user]

    if target == 'expired':
        now = datetime.now(UTC)
        expired_statuses = {
            SubscriptionStatus.EXPIRED.value,
            SubscriptionStatus.DISABLED.value,
        }
        expired_users = []
        for user in users:
            subs = getattr(user, 'subscriptions', None) or []
            if subs:
                has_expired = any(s.status in expired_statuses or (s.end_date <= now and not s.is_active) for s in subs)
                if has_expired:
                    expired_users.append(user)
            elif user.has_had_paid_subscription:
                expired_users.append(user)
        return expired_users

    if target == 'active_zero':
        return [
            user
            for user in users
            if any(
                not s.is_trial and s.is_active and (s.traffic_used_gb or 0) <= 0
                for s in (getattr(user, 'subscriptions', None) or [])
            )
        ]

    if target == 'trial_zero':
        return [
            user
            for user in users
            if any(
                s.is_trial and s.is_active and (s.traffic_used_gb or 0) <= 0
                for s in (getattr(user, 'subscriptions', None) or [])
            )
        ]

    if target == 'zero':
        return [
            user
            for user in users
            if any(s.is_active and (s.traffic_used_gb or 0) <= 0 for s in (getattr(user, 'subscriptions', None) or []))
        ]

    if target == 'expiring_subscribers':
        expiring_subs = await get_expiring_subscriptions(db, 7)
        return [sub.user for sub in expiring_subs if sub.user]

    if target == 'expired_subscribers':
        now = datetime.now(UTC)
        expired_statuses = {
            SubscriptionStatus.EXPIRED.value,
            SubscriptionStatus.DISABLED.value,
        }
        expired_users = []
        for user in users:
            subs = getattr(user, 'subscriptions', None) or []
            if subs:
                has_expired = any(s.status in expired_statuses or (s.end_date <= now and not s.is_active) for s in subs)
                if has_expired:
                    expired_users.append(user)
            elif user.has_had_paid_subscription:
                expired_users.append(user)
        return expired_users

    if target == 'canceled_subscribers':
        return [
            user
            for user in users
            if any(s.status == SubscriptionStatus.DISABLED.value for s in (getattr(user, 'subscriptions', None) or []))
        ]

    if target == 'trial_ending':
        now = datetime.now(UTC)
        in_3_days = now + timedelta(days=3)
        return [
            user
            for user in users
            if any(
                s.is_trial and s.is_active and s.end_date <= in_3_days
                for s in (getattr(user, 'subscriptions', None) or [])
            )
        ]

    if target == 'trial_expired':
        now = datetime.now(UTC)
        return [
            user
            for user in users
            if any(s.is_trial and s.end_date <= now for s in (getattr(user, 'subscriptions', None) or []))
        ]

    if target == 'autopay_failed':
        from app.database.models import SubscriptionEvent

        week_ago = datetime.now(UTC) - timedelta(days=7)
        stmt = (
            select(SubscriptionEvent.user_id)
            .where(
                and_(
                    SubscriptionEvent.event_type == 'autopay_failed',
                    SubscriptionEvent.occurred_at >= week_ago,
                )
            )
            .distinct()
        )
        result = await db.execute(stmt)
        failed_user_ids = set(result.scalars().all())
        return [user for user in users if user.id in failed_user_ids]

    if target == 'low_balance':
        threshold_kopeks = 10000  # 100 рублей
        return [
            user for user in users if (user.balance_kopeks or 0) < threshold_kopeks and (user.balance_kopeks or 0) > 0
        ]

    if target == 'inactive_30d':
        threshold = datetime.now(UTC) - timedelta(days=30)
        return [user for user in users if user.last_activity and user.last_activity < threshold]

    if target == 'inactive_60d':
        threshold = datetime.now(UTC) - timedelta(days=60)
        return [user for user in users if user.last_activity and user.last_activity < threshold]

    if target == 'inactive_90d':
        threshold = datetime.now(UTC) - timedelta(days=90)
        return [user for user in users if user.last_activity and user.last_activity < threshold]

    # Фильтр по тарифу
    if target.startswith('tariff_'):
        tariff_id = int(target.split('_')[1])
        return [
            user
            for user in users
            if any(s.is_active and s.tariff_id == tariff_id for s in (getattr(user, 'subscriptions', None) or []))
        ]

    return []


async def get_custom_users_count(db: AsyncSession, criteria: str) -> int:
    users = await get_custom_users(db, criteria)
    return len(users)


async def get_custom_users(db: AsyncSession, criteria: str) -> list:
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    if criteria == 'today':
        stmt = select(User).where(and_(User.status == 'active', User.created_at >= today))
    elif criteria == 'week':
        stmt = select(User).where(and_(User.status == 'active', User.created_at >= week_ago))
    elif criteria == 'month':
        stmt = select(User).where(and_(User.status == 'active', User.created_at >= month_ago))
    elif criteria == 'active_today':
        stmt = select(User).where(and_(User.status == 'active', User.last_activity >= today))
    elif criteria == 'inactive_week':
        stmt = select(User).where(and_(User.status == 'active', User.last_activity < week_ago))
    elif criteria == 'inactive_month':
        stmt = select(User).where(and_(User.status == 'active', User.last_activity < month_ago))
    elif criteria == 'referrals':
        stmt = select(User).where(and_(User.status == 'active', User.referred_by_id.isnot(None)))
    elif criteria == 'direct':
        stmt = select(User).where(and_(User.status == 'active', User.referred_by_id.is_(None)))
    else:
        return []

    result = await db.execute(stmt)
    return result.scalars().all()


async def get_users_statistics(db: AsyncSession) -> dict:
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    stats = {}

    stats['today'] = (
        await db.scalar(select(func.count(User.id)).where(and_(User.status == 'active', User.created_at >= today))) or 0
    )

    stats['week'] = (
        await db.scalar(select(func.count(User.id)).where(and_(User.status == 'active', User.created_at >= week_ago)))
        or 0
    )

    stats['month'] = (
        await db.scalar(select(func.count(User.id)).where(and_(User.status == 'active', User.created_at >= month_ago)))
        or 0
    )

    stats['active_today'] = (
        await db.scalar(select(func.count(User.id)).where(and_(User.status == 'active', User.last_activity >= today)))
        or 0
    )

    stats['inactive_week'] = (
        await db.scalar(select(func.count(User.id)).where(and_(User.status == 'active', User.last_activity < week_ago)))
        or 0
    )

    stats['inactive_month'] = (
        await db.scalar(
            select(func.count(User.id)).where(and_(User.status == 'active', User.last_activity < month_ago))
        )
        or 0
    )

    stats['referrals'] = (
        await db.scalar(
            select(func.count(User.id)).where(and_(User.status == 'active', User.referred_by_id.isnot(None)))
        )
        or 0
    )

    stats['direct'] = (
        await db.scalar(select(func.count(User.id)).where(and_(User.status == 'active', User.referred_by_id.is_(None))))
        or 0
    )

    return stats


def get_target_name(target_type: str) -> str:
    names = {
        'all': '全部用户',
        'active': '有有效订阅的用户',
        'trial': '有试用订阅的用户',
        'no': '无订阅用户',
        'sub': '无订阅用户',
        'expiring': '即将到期的订阅用户',
        'expired': '已过期订阅用户',
        'active_zero': '有效订阅且流量为 0 GB',
        'trial_zero': '试用订阅且流量为 0 GB',
        'zero': '订阅流量为 0 GB',
        'custom_today': '今日注册',
        'custom_week': '近 7 天注册',
        'custom_month': '近 30 天注册',
        'custom_active_today': '今日活跃',
        'custom_inactive_week': '7+ 天未活跃',
        'custom_inactive_month': '30+ 天未活跃',
        'custom_referrals': '通过推荐注册',
        'custom_direct': '直接注册',
    }
    # Обработка фильтра по тарифу
    if target_type.startswith('tariff_'):
        tariff_id = target_type.split('_')[1]
        return f'套餐#{tariff_id}'
    return names.get(target_type, target_type)


def get_target_display_name(target: str) -> str:
    return get_target_name(target)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_messages_menu, F.data == 'admin_messages')
    dp.callback_query.register(show_pinned_message_menu, F.data == 'admin_pinned_message')
    dp.callback_query.register(toggle_pinned_message_position, F.data == 'admin_pinned_message_position')
    dp.callback_query.register(toggle_pinned_message_start_mode, F.data == 'admin_pinned_message_start_mode')
    dp.callback_query.register(delete_pinned_message, F.data == 'admin_pinned_message_delete')
    dp.callback_query.register(prompt_pinned_message_update, F.data == 'admin_pinned_message_edit')
    dp.callback_query.register(handle_pinned_broadcast_now, F.data.startswith('admin_pinned_broadcast_now:'))
    dp.callback_query.register(handle_pinned_broadcast_skip, F.data.startswith('admin_pinned_broadcast_skip:'))
    dp.callback_query.register(show_broadcast_targets, F.data.in_(['admin_msg_all', 'admin_msg_by_sub']))
    dp.callback_query.register(show_tariff_filter, F.data == 'broadcast_by_tariff')
    dp.callback_query.register(select_broadcast_target, F.data.startswith('broadcast_'))
    dp.callback_query.register(confirm_broadcast, F.data == 'admin_confirm_broadcast')

    dp.callback_query.register(show_messages_history, F.data.startswith('admin_msg_history'))
    dp.callback_query.register(show_custom_broadcast, F.data == 'admin_msg_custom')
    dp.callback_query.register(select_custom_criteria, F.data.startswith('criteria_'))

    dp.callback_query.register(toggle_button_selection, F.data.startswith('btn_'))
    dp.callback_query.register(confirm_button_selection, F.data == 'buttons_confirm')
    dp.callback_query.register(show_button_selector_callback, F.data == 'edit_buttons')
    dp.callback_query.register(handle_media_selection, F.data.startswith('add_media_'))
    dp.callback_query.register(handle_media_selection, F.data == 'skip_media')
    dp.callback_query.register(handle_media_confirmation, F.data.in_(['confirm_media', 'replace_media']))
    dp.callback_query.register(handle_change_media, F.data == 'change_media')
    dp.message.register(process_broadcast_message, AdminStates.waiting_for_broadcast_message)
    dp.message.register(process_broadcast_media, AdminStates.waiting_for_broadcast_media)
    dp.message.register(process_pinned_message_update, AdminStates.editing_pinned_message)





