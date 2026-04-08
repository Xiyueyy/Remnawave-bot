import html

import structlog
from aiogram import Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.rules import clear_all_rules, get_rules_statistics
from app.database.crud.ticket import TicketCRUD
from app.database.models import User
from app.handlers.admin import support_settings as support_settings_handlers
from app.keyboards.admin import (
    get_admin_communications_submenu_keyboard,
    get_admin_main_keyboard,
    get_admin_promo_submenu_keyboard,
    get_admin_settings_submenu_keyboard,
    get_admin_support_submenu_keyboard,
    get_admin_system_submenu_keyboard,
    get_admin_users_submenu_keyboard,
)
from app.localization.texts import clear_rules_cache, get_texts
from app.services.support_settings_service import SupportSettingsService
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


@admin_required
@error_handler
async def show_admin_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)

    admin_text = texts.ADMIN_PANEL
    try:
        from app.services.remnawave_service import RemnaWaveService

        remnawave_service = RemnaWaveService()
        stats = await remnawave_service.get_system_statistics()
        system_stats = stats.get('system', {})
        users_online = system_stats.get('users_online', 0)
        users_today = system_stats.get('users_last_day', 0)
        users_week = system_stats.get('users_last_week', 0)
        admin_text = admin_text.replace(
            '\n\nВыберите раздел для управления:',
            (
                f'\n\n- 🟢 Онлайн сейчас: {users_online}'
                f'\n- 📅 Онлайн сегодня: {users_today}'
                f'\n- 🗓️ На этой неделе: {users_week}'
                '\n\nВыберите раздел для управления:'
            ),
        )
    except Exception as e:
        logger.error('无法获取管理面板的 Remnawave 统计信息', error=e)

    await callback.message.edit_text(admin_text, reply_markup=get_admin_main_keyboard(db_user.language))
    await callback.answer()


@admin_required
@error_handler
async def show_users_submenu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        texts.t('ADMIN_USERS_SUBMENU_TITLE', '👥**用户和订阅管理**\n\n')
        + texts.t('ADMIN_SUBMENU_SELECT_SECTION', '请选择所需部分：'),
        reply_markup=get_admin_users_submenu_keyboard(db_user.language),
        parse_mode='Markdown',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_promo_submenu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        texts.t('ADMIN_PROMO_SUBMENU_TITLE', '💰**优惠码与统计**\n\n')
        + texts.t('ADMIN_SUBMENU_SELECT_SECTION', '请选择所需部分：'),
        reply_markup=get_admin_promo_submenu_keyboard(db_user.language),
        parse_mode='Markdown',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_communications_submenu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        texts.t('ADMIN_COMMUNICATIONS_SUBMENU_TITLE', '📨**通讯**\n\n')
        + texts.t('ADMIN_COMMUNICATIONS_SUBMENU_DESCRIPTION', '管理广播和界面文本：'),
        reply_markup=get_admin_communications_submenu_keyboard(db_user.language),
        parse_mode='Markdown',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_support_submenu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    # Moderators have access only to tickets and not to settings
    is_moderator_only = not settings.is_admin(callback.from_user.id) and SupportSettingsService.is_moderator(
        callback.from_user.id
    )

    kb = get_admin_support_submenu_keyboard(db_user.language)
    if is_moderator_only:
        # Rebuild keyboard to include only tickets and back to main menu
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.t('ADMIN_SUPPORT_TICKETS', '🎫支持工单'), callback_data='admin_tickets'
                    )
                ],
                [InlineKeyboardButton(text=texts.BACK, callback_data='back_to_menu')],
            ]
        )
    await callback.message.edit_text(
        texts.t('ADMIN_SUPPORT_SUBMENU_TITLE', '🛟**支持**\n\n')
        + (
            texts.t('ADMIN_SUPPORT_SUBMENU_DESCRIPTION_MODERATOR', '访问工单。')
            if is_moderator_only
            else texts.t('ADMIN_SUPPORT_SUBMENU_DESCRIPTION', '管理工单和支持设置：')
        ),
        reply_markup=kb,
        parse_mode='Markdown',
    )
    await callback.answer()


# Moderator panel entry (from main menu quick button)
@admin_required
@error_handler
async def show_moderator_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.t('ADMIN_SUPPORT_TICKETS', '🎫支持工单'), callback_data='admin_tickets'
                )
            ],
            [
                InlineKeyboardButton(
                    text=texts.t('BACK_TO_MAIN_MENU_BUTTON', '⬅️返回主菜单'), callback_data='back_to_menu'
                )
            ],
        ]
    )
    await callback.message.edit_text(
        texts.t('ADMIN_SUPPORT_MODERATION_TITLE', '🧑\u200d⚖️<b>支持管理</b>')
        + '\n\n'
        + texts.t('ADMIN_SUPPORT_MODERATION_DESCRIPTION', '访问支持工单。'),
        parse_mode='HTML',
        reply_markup=kb,
    )
    await callback.answer()


@admin_required
@error_handler
async def show_support_audit(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)
    # pagination
    page = 1
    if callback.data.startswith('admin_support_audit_page_'):
        try:
            page = int(callback.data.split('_')[-1])
        except Exception:
            page = 1
    per_page = 10
    total = await TicketCRUD.count_support_audit(db)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(page, 1)
    page = min(page, total_pages)
    offset = (page - 1) * per_page
    logs = await TicketCRUD.list_support_audit(db, limit=per_page, offset=offset)

    lines = [texts.t('ADMIN_SUPPORT_AUDIT_TITLE', '🧾<b>版主审计</b>'), '']
    if not logs:
        lines.append(texts.t('ADMIN_SUPPORT_AUDIT_EMPTY', '暂无内容'))
    else:
        for log in logs:
            role = (
                texts.t('ADMIN_SUPPORT_AUDIT_ROLE_MODERATOR', '版主')
                if getattr(log, 'is_moderator', False)
                else texts.t('ADMIN_SUPPORT_AUDIT_ROLE_ADMIN', '管理员')
            )
            ts = log.created_at.strftime('%d.%m.%Y %H:%M') if getattr(log, 'created_at', None) else ''
            action_map = {
                'close_ticket': texts.t('ADMIN_SUPPORT_AUDIT_ACTION_CLOSE_TICKET', '关闭工单'),
                'block_user_timed': texts.t('ADMIN_SUPPORT_AUDIT_ACTION_BLOCK_TIMED', '临时封锁'),
                'block_user_perm': texts.t('ADMIN_SUPPORT_AUDIT_ACTION_BLOCK_PERM', '永久封锁'),
                'close_all_tickets': texts.t(
                    'ADMIN_SUPPORT_AUDIT_ACTION_CLOSE_ALL_TICKETS', '批量关闭工单'
                ),
                'unblock_user': texts.t('ADMIN_SUPPORT_AUDIT_ACTION_UNBLOCK', '解除封锁'),
            }
            action_text = action_map.get(log.action, log.action)
            ticket_part = f' тикет #{log.ticket_id}' if log.ticket_id else ''
            details = log.details or {}
            extra = ''
            if log.action == 'block_user_timed' and 'minutes' in details:
                extra = f' ({details["minutes"]} мин)'
            elif log.action == 'close_all_tickets' and 'count' in details:
                extra = f' ({details["count"]})'
            actor_id_display = log.actor_telegram_id or f'user#{log.actor_user_id}' if log.actor_user_id else 'unknown'
            lines.append(f'{ts} • {role} <code>{actor_id_display}</code> — {action_text}{ticket_part}{extra}')

    # keyboard with pagination
    nav_row = []
    if total_pages > 1:
        if page > 1:
            nav_row.append(InlineKeyboardButton(text='⬅️', callback_data=f'admin_support_audit_page_{page - 1}'))
        nav_row.append(InlineKeyboardButton(text=f'{page}/{total_pages}', callback_data='current_page'))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton(text='➡️', callback_data=f'admin_support_audit_page_{page + 1}'))

    kb_rows = []
    if nav_row:
        kb_rows.append(nav_row)
    kb_rows.append([InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_support')])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    await callback.message.edit_text('\n'.join(lines), parse_mode='HTML', reply_markup=kb)
    await callback.answer()


@admin_required
@error_handler
async def show_settings_submenu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        texts.t('ADMIN_SETTINGS_SUBMENU_TITLE', '⚙️**系统设置**\n\n')
        + texts.t('ADMIN_SETTINGS_SUBMENU_DESCRIPTION', '管理Remnawave、监控和其他设置：'),
        reply_markup=get_admin_settings_submenu_keyboard(db_user.language),
        parse_mode='Markdown',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_system_submenu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)

    await callback.message.edit_text(
        texts.t('ADMIN_SYSTEM_SUBMENU_TITLE', '🛠️**系统功能**\n\n')
        + texts.t(
            'ADMIN_SYSTEM_SUBMENU_DESCRIPTION', '报告、更新、日志、备份和系统操作：'
        ),
        reply_markup=get_admin_system_submenu_keyboard(db_user.language),
        parse_mode='Markdown',
    )
    await callback.answer()


@admin_required
@error_handler
async def clear_rules_command(message: types.Message, db_user: User, db: AsyncSession):
    try:
        stats = await get_rules_statistics(db)

        if stats['total_active'] == 0:
            await message.reply(
                'ℹ️ <b>规则已清除</b>\n\n系统中没有有效的规则。使用标准默认规则。'
            )
            return

        success = await clear_all_rules(db, db_user.language)

        if success:
            clear_rules_cache()

            await message.reply(
                f"✅ <b>规则成功通关！</b>\n\n📊 <b>统计：</b>\n• 清除规则：{stats['total_active']}\n• 语言：{db_user.language}\n• 完成者：{html.escape(db_user.full_name or '')}\n\n现在使用标准默认规则。"
            )

            logger.info(
                '管理团队清除的规则', telegram_id=db_user.telegram_id, full_name=db_user.full_name
            )
        else:
            await message.reply('⚠️<b>无清洁规则</b>\n\n未找到有效规则。')

    except Exception as e:
        logger.error('使用命令清除规则时出错', error=e)
        await message.reply(
            f'❌ <b>清除规则时出错</b>\n\n发生错误：{e!s}\n尝试通过管理面板或稍后重试。'
        )


@admin_required
@error_handler
async def rules_stats_command(message: types.Message, db_user: User, db: AsyncSession):
    try:
        stats = await get_rules_statistics(db)

        if 'error' in stats:
            await message.reply(f"❌ 获取统计信息时出错：{stats['error']}")
            return

        text = '📊 <b>服务规则统计</b>'
        text += '📋 <b>一般信息：</b>'
        text += f"• 活动规则：{stats['total_active']}"
        text += f"• 总历史记录：{stats['total_all_time']}"
        text += f"• 支持的语言：{stats['total_languages']}"

        if stats['languages']:
            text += '🌐 <b>按语言：</b>'
            for lang, lang_stats in stats['languages'].items():
                text += f"• <code>{lang}</code>：{lang_stats['active_count']} 规则，"
                text += f"{lang_stats['content_length']} 人物"
                if lang_stats['last_updated']:
                    text += f"更新：{lang_stats['last_updated'].strftime('%d.%m.%Y %H:%M')}"
        else:
            text += 'ℹ️ 没有活动规则 - 使用默认规则'

        await message.reply(text)

    except Exception as e:
        logger.error('获取规则统计信息时出错', error=e)
        await message.reply(f'❌ <b>接收错误统计</b>\n\n发生错误：{e!s}')


@admin_required
@error_handler
async def admin_commands_help(message: types.Message, db_user: User, db: AsyncSession):
    help_text = '🔧 <b>可用的管理命令：</b>\n\n<b>📋规则管理：</b>\n• <code>/clear_rules</code> - 清除所有规则\n• <code>/rules_stats</code> - 规则统计\n\n<b>ℹ️帮助：</b>\n• <code>/admin_help</code> - 这是消息\n\n<b>📱控制面板：</b>\n使用主菜单中的“管理面板”按钮可以完全访问所有功能。\n\n<b>⚠️重要：</b>\n所有命令都会被记录并需要管理员权限。'

    await message.reply(help_text)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_admin_panel, F.data == 'admin_panel')

    dp.callback_query.register(show_users_submenu, F.data == 'admin_submenu_users')

    dp.callback_query.register(show_promo_submenu, F.data == 'admin_submenu_promo')

    dp.callback_query.register(show_communications_submenu, F.data == 'admin_submenu_communications')

    dp.callback_query.register(show_support_submenu, F.data == 'admin_submenu_support')
    dp.callback_query.register(
        show_support_audit, F.data.in_(['admin_support_audit']) | F.data.startswith('admin_support_audit_page_')
    )

    dp.callback_query.register(show_settings_submenu, F.data == 'admin_submenu_settings')

    dp.callback_query.register(show_system_submenu, F.data == 'admin_submenu_system')
    dp.callback_query.register(show_moderator_panel, F.data == 'moderator_panel')
    # Support settings module
    support_settings_handlers.register_handlers(dp)

    dp.message.register(clear_rules_command, Command('clear_rules'))

    dp.message.register(rules_stats_command, Command('rules_stats'))

    dp.message.register(admin_commands_help, Command('admin_help'))
