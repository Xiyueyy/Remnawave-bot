import html
import re

import structlog
from aiogram import Bot, Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.campaign import (
    create_campaign,
    delete_campaign,
    get_campaign_by_id,
    get_campaign_by_start_parameter,
    get_campaign_statistics,
    get_campaigns_count,
    get_campaigns_list,
    get_campaigns_overview,
    update_campaign,
)
from app.database.crud.server_squad import get_all_server_squads, get_server_squad_by_id
from app.database.crud.tariff import get_all_tariffs, get_tariff_by_id
from app.database.models import User
from app.keyboards.admin import (
    get_admin_campaigns_keyboard,
    get_admin_pagination_keyboard,
    get_campaign_bonus_type_keyboard,
    get_campaign_edit_keyboard,
    get_campaign_management_keyboard,
    get_confirmation_keyboard,
)
from app.localization.texts import get_texts
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler
from app.utils.display_names import escape_display_name


logger = structlog.get_logger(__name__)

_CAMPAIGN_PARAM_REGEX = re.compile(r'^[A-Za-z0-9_-]{3,32}$')
_CAMPAIGNS_PAGE_SIZE = 5


def _format_campaign_summary(campaign, texts) -> str:
    status = '🟢 Активна' if campaign.is_active else '⚪️ Выключена'

    if campaign.is_balance_bonus:
        bonus_text = texts.format_price(campaign.balance_bonus_kopeks)
        bonus_info = f'💰 Бонус на баланс: <b>{bonus_text}</b>'
    elif campaign.is_subscription_bonus:
        traffic_text = texts.format_traffic(campaign.subscription_traffic_gb or 0)
        device_limit = campaign.subscription_device_limit
        if device_limit is None:
            device_limit = settings.DEFAULT_DEVICE_LIMIT
        bonus_info = (
            f'📱 Пробная подписка: <b>{campaign.subscription_duration_days or 0} д.</b>\n'
            f'🌐 流量： <b>{traffic_text}</b>\n'
            f'📱 设备: <b>{device_limit}</b>'
        )
    elif campaign.is_tariff_bonus:
        tariff_name = 'Не выбран'
        if hasattr(campaign, 'tariff') and campaign.tariff:
            tariff_name = escape_display_name(campaign.tariff.name)
        else:
            tariff_name = html.escape(tariff_name)
        bonus_info = f'🎁 套餐：<b>{tariff_name}</b>\n📅 时长：<b>{campaign.tariff_duration_days or 0} 天</b>'
    elif campaign.is_none_bonus:
        bonus_info = '🔗 Только ссылка (без награды)'
    else:
        bonus_info = '❓ Неизвестный тип бонуса'

    return (
        f'<b>{html.escape(campaign.name)}</b>\n启动参数：<code>{html.escape(campaign.start_parameter)}</code>\n状态：{status}\n{bonus_info}'
    )


async def _get_bot_deep_link(callback: types.CallbackQuery, start_parameter: str) -> str:
    bot = await callback.bot.get_me()
    return f'https://t.me/{bot.username}?start={start_parameter}'


async def _get_bot_deep_link_from_message(message: types.Message, start_parameter: str) -> str:
    bot = await message.bot.get_me()
    return f'https://t.me/{bot.username}?start={start_parameter}'


def _build_campaign_servers_keyboard(
    servers,
    selected_uuids: list[str],
    *,
    toggle_prefix: str = 'campaign_toggle_server_',
    save_callback: str = 'campaign_servers_save',
    back_callback: str = 'admin_campaigns',
) -> types.InlineKeyboardMarkup:
    keyboard: list[list[types.InlineKeyboardButton]] = []

    for server in servers[:20]:
        is_selected = server.squad_uuid in selected_uuids
        emoji = '✅' if is_selected else ('⚪' if server.is_available else '🔒')
        text = f'{emoji} {server.display_name}'
        keyboard.append([types.InlineKeyboardButton(text=text, callback_data=f'{toggle_prefix}{server.id}')])

    keyboard.append(
        [
            types.InlineKeyboardButton(text='✅ 保存', callback_data=save_callback),
            types.InlineKeyboardButton(text='⬅️ 返回', callback_data=back_callback),
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _render_campaign_edit_menu(
    bot: Bot,
    chat_id: int,
    message_id: int,
    campaign,
    language: str,
    *,
    use_caption: bool = False,
):
    texts = get_texts(language)
    text = f'✏️<b>编辑活动</b>\n\n{_format_campaign_summary(campaign, texts)}\n选择要更改的内容：'

    edit_kwargs = dict(
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=get_campaign_edit_keyboard(
            campaign.id,
            bonus_type=campaign.bonus_type,
            language=language,
        ),
        parse_mode='HTML',
    )

    if use_caption:
        await bot.edit_message_caption(
            caption=text,
            **edit_kwargs,
        )
    else:
        await bot.edit_message_text(
            text=text,
            **edit_kwargs,
        )


@admin_required
@error_handler
async def show_campaigns_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    overview = await get_campaigns_overview(db)

    text = (
        f"📣 <b>广告活动</b>\n\n广告活动总数：<b>{overview['total']}</b>\n主动：<b>{overview['active']}</b> |关闭：<b>{overview['inactive']}</b>\n注册：<b>{overview['registrations']}</b>\n已发行余额：<b>{texts.format_price(overview['balance_total'])}</b>\n已发行订阅：<b>{overview['subscription_total']}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_admin_campaigns_keyboard(db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_campaigns_overall_stats(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    overview = await get_campaigns_overview(db)

    text = ['📊 <b>一般活动统计</b>']
    text.append(f"广告活动总数：<b>{overview['total']}</b>")
    text.append(f"激活：<b>{overview['active']}</b>，禁用：<b>{overview['inactive']}</b>")
    text.append(f"注册总数：<b>{overview['registrations']}</b>")
    text.append(f"已发行总余额：<b>{texts.format_price(overview['balance_total'])}</b>")
    text.append(f"已发行订阅：<b>{overview['subscription_total']}</b>")

    await callback.message.edit_text(
        '\n'.join(text),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_campaigns')]]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_campaigns_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    page = 1
    if callback.data.startswith('admin_campaigns_list_page_'):
        try:
            page = int(callback.data.split('_')[-1])
        except ValueError:
            page = 1

    offset = (page - 1) * _CAMPAIGNS_PAGE_SIZE
    campaigns = await get_campaigns_list(
        db,
        offset=offset,
        limit=_CAMPAIGNS_PAGE_SIZE,
    )
    total = await get_campaigns_count(db)
    total_pages = max(1, (total + _CAMPAIGNS_PAGE_SIZE - 1) // _CAMPAIGNS_PAGE_SIZE)

    if not campaigns:
        await callback.message.edit_text(
            '❌ 未发现任何广告活动。',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='➕ 创建', callback_data='admin_campaigns_create')],
                    [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_campaigns')],
                ]
            ),
        )
        await callback.answer()
        return

    text_lines = ['📋 <b>Список кампаний</b>\n']

    for campaign in campaigns:
        # Access from instance dict to avoid MissingGreenlet on lazy load
        regs = sa_inspect(campaign).dict.get('registrations', []) or []
        registrations = len(regs)
        total_balance = sum(r.balance_bonus_kopeks or 0 for r in regs)
        status = '🟢' if campaign.is_active else '⚪'
        line = (
            f'{status} <b>{html.escape(campaign.name)}</b> — <code>{html.escape(campaign.start_parameter)}</code>\n'
            f'   Регистраций: {registrations}, баланс: {texts.format_price(total_balance)}'
        )
        if campaign.is_subscription_bonus:
            line += f', подписка: {campaign.subscription_duration_days or 0} д.'
        else:
            line += ', бонус: баланс'
        text_lines.append(line)

    keyboard_rows = [
        [
            types.InlineKeyboardButton(
                text=f'🔍 {campaign.name}',
                callback_data=f'admin_campaign_manage_{campaign.id}',
            )
        ]
        for campaign in campaigns
    ]

    pagination = get_admin_pagination_keyboard(
        current_page=page,
        total_pages=total_pages,
        callback_prefix='admin_campaigns_list',
        back_callback='admin_campaigns',
        language=db_user.language,
    )

    keyboard_rows.extend(pagination.inline_keyboard)

    await callback.message.edit_text(
        '\n'.join(text_lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_campaign_detail(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)

    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    texts = get_texts(db_user.language)
    stats = await get_campaign_statistics(db, campaign_id)
    deep_link = await _get_bot_deep_link(callback, campaign.start_parameter)

    text = ['📣 <b>活动管理</b>']
    text.append(_format_campaign_summary(campaign, texts))
    text.append(f'🔗 链接：<code>{deep_link}</code>')
    text.append('📊 <b>统计</b>')
    text.append(f"• 注册：<b>{stats['registrations']}</b>")
    text.append(f"• 已发行余额：<b>{texts.format_price(stats['balance_issued'])}</b>")
    text.append(f"• 已发行订阅：<b>{stats['subscription_issued']}</b>")
    text.append(f"• 收入：<b>{texts.format_price(stats['total_revenue_kopeks'])}</b>")
    text.append(f"• 收到试用版：<b>{stats['trial_users_count']}</b>（有效：{stats['active_trials_count']}）")
    text.append(
        f"• 付款转化：<b>{stats['conversion_count']}</b> / 付款用户：{stats['paid_users_count']}"
    )
    text.append(f"• 转换为付款：<b>{stats['conversion_rate']:.1f}%</b>")
    text.append(f"• 尝试转换：<b>{stats['trial_conversion_rate']:.1f}%</b>")
    text.append(f"• 每个用户的平均收入：<b>{texts.format_price(stats['avg_revenue_per_user_kopeks'])}</b>")
    text.append(f"• 平均首次付款：<b>{texts.format_price(stats['avg_first_payment_kopeks'])}</b>")
    if stats['last_registration']:
        text.append(f"• 最新：{stats['last_registration'].strftime('%d.%m.%Y %H:%M')}")

    await callback.message.edit_text(
        '\n'.join(text),
        reply_markup=get_campaign_management_keyboard(campaign.id, campaign.is_active, db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_campaign_edit_menu(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)

    if not campaign:
        await state.clear()
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    await state.clear()

    use_caption = bool(callback.message.caption) and not bool(callback.message.text)

    await _render_campaign_edit_menu(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        campaign,
        db_user.language,
        use_caption=use_caption,
    )
    await callback.answer()


@admin_required
@error_handler
async def start_edit_campaign_name(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminStates.editing_campaign_name)
    is_caption = bool(callback.message.caption) and not bool(callback.message.text)
    await state.update_data(
        editing_campaign_id=campaign_id,
        campaign_edit_message_id=callback.message.message_id,
        campaign_edit_message_is_caption=is_caption,
    )

    await callback.message.edit_text(
        (
            f'✏️ <b>更改活动名称</b>\n\n当前名称：<b>{html.escape(campaign.name)}</b>\n输入新名称（3-100 个字符）：'
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text='❌ 取消',
                        callback_data=f'admin_campaign_edit_{campaign_id}',
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_campaign_name(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get('editing_campaign_id')
    if not campaign_id:
        await message.answer('❌ 编辑会话已过时。再试一次。')
        await state.clear()
        return

    new_name = message.text.strip()
    if len(new_name) < 3 or len(new_name) > 100:
        await message.answer('❌ 名称必须包含 3 到 100 个字符。再试一次。')
        return

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await message.answer('❌ 找不到活动')
        await state.clear()
        return

    await update_campaign(db, campaign, name=new_name)
    await state.clear()

    await message.answer('✅ 标题已更新。')

    edit_message_id = data.get('campaign_edit_message_id')
    edit_message_is_caption = data.get('campaign_edit_message_is_caption', False)
    if edit_message_id:
        await _render_campaign_edit_menu(
            message.bot,
            message.chat.id,
            edit_message_id,
            campaign,
            db_user.language,
            use_caption=edit_message_is_caption,
        )


@admin_required
@error_handler
async def start_edit_campaign_start_parameter(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminStates.editing_campaign_start)
    is_caption = bool(callback.message.caption) and not bool(callback.message.text)
    await state.update_data(
        editing_campaign_id=campaign_id,
        campaign_edit_message_id=callback.message.message_id,
        campaign_edit_message_is_caption=is_caption,
    )

    await callback.message.edit_text(
        (
            f'🔗 <b>更改起始参数</b>\n\n当前参数：<code>{campaign.start_parameter}</code>\n输入新参数（拉丁字母、数字、- 或 _，3-32 个字符）：'
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text='❌ 取消',
                        callback_data=f'admin_campaign_edit_{campaign_id}',
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_campaign_start_parameter(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get('editing_campaign_id')
    if not campaign_id:
        await message.answer('❌ 编辑会话已过时。再试一次。')
        await state.clear()
        return

    new_param = message.text.strip()
    if not _CAMPAIGN_PARAM_REGEX.match(new_param):
        await message.answer('❌ 只允许使用拉丁字母、数字、符号 - 和 _。长度 3-32 个字符。')
        return

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await message.answer('❌ 找不到活动')
        await state.clear()
        return

    existing = await get_campaign_by_start_parameter(db, new_param)
    if existing and existing.id != campaign_id:
        await message.answer('❌ 该参数已被使用。输入另一个选项。')
        return

    await update_campaign(db, campaign, start_parameter=new_param)
    await state.clear()

    await message.answer('✅ 启动参数已更新。')

    edit_message_id = data.get('campaign_edit_message_id')
    edit_message_is_caption = data.get('campaign_edit_message_is_caption', False)
    if edit_message_id:
        await _render_campaign_edit_menu(
            message.bot,
            message.chat.id,
            edit_message_id,
            campaign,
            db_user.language,
            use_caption=edit_message_is_caption,
        )


@admin_required
@error_handler
async def start_edit_campaign_balance_bonus(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    if not campaign.is_balance_bonus:
        await callback.answer('❌ 该活动有不同类型的奖金', show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminStates.editing_campaign_balance)
    is_caption = bool(callback.message.caption) and not bool(callback.message.text)
    await state.update_data(
        editing_campaign_id=campaign_id,
        campaign_edit_message_id=callback.message.message_id,
        campaign_edit_message_is_caption=is_caption,
    )

    await callback.message.edit_text(
        (
            f'💰 <b>更改奖金以余额</b>\n\n当前奖金：<b>{get_texts(db_user.language).format_price(campaign.balance_bonus_kopeks)}</b>\n输入新的卢布金额（例如 100 或 99.5）：'
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text='❌ 取消',
                        callback_data=f'admin_campaign_edit_{campaign_id}',
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_campaign_balance_bonus(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get('editing_campaign_id')
    if not campaign_id:
        await message.answer('❌ 编辑会话已过时。再试一次。')
        await state.clear()
        return

    try:
        amount_rubles = float(message.text.replace(',', '.'))
    except ValueError:
        await message.answer('❌ 输入正确的金额（例如 100 或 99.5）')
        return

    if amount_rubles <= 0:
        await message.answer('❌ 金额必须大于零')
        return

    amount_kopeks = int(round(amount_rubles * 100))

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await message.answer('❌ 找不到活动')
        await state.clear()
        return

    if not campaign.is_balance_bonus:
        await message.answer('❌ 该活动有不同类型的奖金')
        await state.clear()
        return

    await update_campaign(db, campaign, balance_bonus_kopeks=amount_kopeks)
    await state.clear()

    await message.answer('✅ 奖金已更新。')

    edit_message_id = data.get('campaign_edit_message_id')
    edit_message_is_caption = data.get('campaign_edit_message_is_caption', False)
    if edit_message_id:
        await _render_campaign_edit_menu(
            message.bot,
            message.chat.id,
            edit_message_id,
            campaign,
            db_user.language,
            use_caption=edit_message_is_caption,
        )


async def _ensure_subscription_campaign(message_or_callback, campaign) -> bool:
    if campaign.is_balance_bonus:
        if isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.answer(
                '❌ 此活动仅提供余额奖金',
                show_alert=True,
            )
        else:
            await message_or_callback.answer('❌ 您无法更改此活动的订阅选项')
        return False
    return True


@admin_required
@error_handler
async def start_edit_campaign_subscription_days(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    if not await _ensure_subscription_campaign(callback, campaign):
        return

    await state.clear()
    await state.set_state(AdminStates.editing_campaign_subscription_days)
    is_caption = bool(callback.message.caption) and not bool(callback.message.text)
    await state.update_data(
        editing_campaign_id=campaign_id,
        campaign_edit_message_id=callback.message.message_id,
        campaign_edit_message_is_caption=is_caption,
    )

    await callback.message.edit_text(
        (
            f'📅 <b>更改订阅期限</b>\n\n当前值：<b>{campaign.subscription_duration_days or 0} d.</b>\n输入新的天数 (1-730)：'
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text='❌ 取消',
                        callback_data=f'admin_campaign_edit_{campaign_id}',
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_campaign_subscription_days(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get('editing_campaign_id')
    if not campaign_id:
        await message.answer('❌ 编辑会话已过时。再试一次。')
        await state.clear()
        return

    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer('❌ 输入天数 (1-730)')
        return

    if days <= 0 or days > 730:
        await message.answer('❌ 持续时间必须为 1 至 730 天')
        return

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await message.answer('❌ 找不到活动')
        await state.clear()
        return

    if not await _ensure_subscription_campaign(message, campaign):
        await state.clear()
        return

    await update_campaign(db, campaign, subscription_duration_days=days)
    await state.clear()

    await message.answer('✅ 订阅期限已更新。')

    edit_message_id = data.get('campaign_edit_message_id')
    edit_message_is_caption = data.get('campaign_edit_message_is_caption', False)
    if edit_message_id:
        await _render_campaign_edit_menu(
            message.bot,
            message.chat.id,
            edit_message_id,
            campaign,
            db_user.language,
            use_caption=edit_message_is_caption,
        )


@admin_required
@error_handler
async def start_edit_campaign_subscription_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    if not await _ensure_subscription_campaign(callback, campaign):
        return

    await state.clear()
    await state.set_state(AdminStates.editing_campaign_subscription_traffic)
    is_caption = bool(callback.message.caption) and not bool(callback.message.text)
    await state.update_data(
        editing_campaign_id=campaign_id,
        campaign_edit_message_id=callback.message.message_id,
        campaign_edit_message_is_caption=is_caption,
    )

    current_traffic = campaign.subscription_traffic_gb or 0
    traffic_text = 'безлимит' if current_traffic == 0 else f'{current_traffic} GB'

    await callback.message.edit_text(
        (
            f'🌐 <b>更改流量限制</b>\n\n当前值：<b>{traffic_text}</b>\n在 GB 中输入新限制（0 = 无限制，最大 10000）：'
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text='❌ 取消',
                        callback_data=f'admin_campaign_edit_{campaign_id}',
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_campaign_subscription_traffic(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get('editing_campaign_id')
    if not campaign_id:
        await message.answer('❌ 编辑会话已过时。再试一次。')
        await state.clear()
        return

    try:
        traffic = int(message.text.strip())
    except ValueError:
        await message.answer('❌ 输入一个整数（0或更大）')
        return

    if traffic < 0 or traffic > 10000:
        await message.answer('❌ 流量限制必须为0到10000 GB')
        return

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await message.answer('❌ 找不到活动')
        await state.clear()
        return

    if not await _ensure_subscription_campaign(message, campaign):
        await state.clear()
        return

    await update_campaign(db, campaign, subscription_traffic_gb=traffic)
    await state.clear()

    await message.answer('✅ 流量限制已更新。')

    edit_message_id = data.get('campaign_edit_message_id')
    edit_message_is_caption = data.get('campaign_edit_message_is_caption', False)
    if edit_message_id:
        await _render_campaign_edit_menu(
            message.bot,
            message.chat.id,
            edit_message_id,
            campaign,
            db_user.language,
            use_caption=edit_message_is_caption,
        )


@admin_required
@error_handler
async def start_edit_campaign_subscription_devices(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    if not await _ensure_subscription_campaign(callback, campaign):
        return

    await state.clear()
    await state.set_state(AdminStates.editing_campaign_subscription_devices)
    is_caption = bool(callback.message.caption) and not bool(callback.message.text)
    await state.update_data(
        editing_campaign_id=campaign_id,
        campaign_edit_message_id=callback.message.message_id,
        campaign_edit_message_is_caption=is_caption,
    )

    current_devices = campaign.subscription_device_limit
    if current_devices is None:
        current_devices = settings.DEFAULT_DEVICE_LIMIT

    await callback.message.edit_text(
        (
            f'📱 <b>更改设备限制</b>\n\n当前值：<b>{current_devices}</b>\n输入新数量 (1-{settings.MAX_DEVICES_LIMIT})：'
        ),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text='❌ 取消',
                        callback_data=f'admin_campaign_edit_{campaign_id}',
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_campaign_subscription_devices(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get('editing_campaign_id')
    if not campaign_id:
        await message.answer('❌ 编辑会话已过时。再试一次。')
        await state.clear()
        return

    try:
        devices = int(message.text.strip())
    except ValueError:
        await message.answer('❌ 输入设备的整数个数')
        return

    if devices < 1 or devices > settings.MAX_DEVICES_LIMIT:
        await message.answer(f'❌ 设备数量必须从 1 到 {settings.MAX_DEVICES_LIMIT}')
        return

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await message.answer('❌ 找不到活动')
        await state.clear()
        return

    if not await _ensure_subscription_campaign(message, campaign):
        await state.clear()
        return

    await update_campaign(db, campaign, subscription_device_limit=devices)
    await state.clear()

    await message.answer('✅ 设备限制已更新。')

    edit_message_id = data.get('campaign_edit_message_id')
    edit_message_is_caption = data.get('campaign_edit_message_is_caption', False)
    if edit_message_id:
        await _render_campaign_edit_menu(
            message.bot,
            message.chat.id,
            edit_message_id,
            campaign,
            db_user.language,
            use_caption=edit_message_is_caption,
        )


@admin_required
@error_handler
async def start_edit_campaign_subscription_servers(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    if not await _ensure_subscription_campaign(callback, campaign):
        return

    servers, _ = await get_all_server_squads(db, available_only=False)
    if not servers:
        await callback.answer(
            '❌ 未找到可用的服务器。更改之前添加服务器。',
            show_alert=True,
        )
        return

    selected = list(campaign.subscription_squads or [])

    await state.clear()
    await state.set_state(AdminStates.editing_campaign_subscription_servers)
    is_caption = bool(callback.message.caption) and not bool(callback.message.text)
    await state.update_data(
        editing_campaign_id=campaign_id,
        campaign_edit_message_id=callback.message.message_id,
        campaign_subscription_squads=selected,
        campaign_edit_message_is_caption=is_caption,
    )

    keyboard = _build_campaign_servers_keyboard(
        servers,
        selected,
        toggle_prefix=f'campaign_edit_toggle_{campaign_id}_',
        save_callback=f'campaign_edit_servers_save_{campaign_id}',
        back_callback=f'admin_campaign_edit_{campaign_id}',
    )

    await callback.message.edit_text(
        (
            '🌍 <b>编辑可用服务器</b>\n\n单击服务器可将其添加到活动中或将其从活动中删除。\n选择后，单击“✅ 保存”。'
        ),
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_edit_campaign_server(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    parts = callback.data.split('_')
    try:
        server_id = int(parts[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 检测服务器失败', show_alert=True)
        return

    data = await state.get_data()
    campaign_id = data.get('editing_campaign_id')
    if not campaign_id:
        await callback.answer('❌ 编辑会话已过时', show_alert=True)
        await state.clear()
        return

    server = await get_server_squad_by_id(db, server_id)
    if not server:
        await callback.answer('❌ 找不到服务器', show_alert=True)
        return

    selected = list(data.get('campaign_subscription_squads', []))

    if server.squad_uuid in selected:
        selected.remove(server.squad_uuid)
    else:
        selected.append(server.squad_uuid)

    await state.update_data(campaign_subscription_squads=selected)

    servers, _ = await get_all_server_squads(db, available_only=False)
    keyboard = _build_campaign_servers_keyboard(
        servers,
        selected,
        toggle_prefix=f'campaign_edit_toggle_{campaign_id}_',
        save_callback=f'campaign_edit_servers_save_{campaign_id}',
        back_callback=f'admin_campaign_edit_{campaign_id}',
    )

    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def save_edit_campaign_subscription_servers(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    campaign_id = data.get('editing_campaign_id')
    if not campaign_id:
        await callback.answer('❌ 编辑会话已过时', show_alert=True)
        await state.clear()
        return

    selected = list(data.get('campaign_subscription_squads', []))
    if not selected:
        await callback.answer('❗ 至少选择一台服务器', show_alert=True)
        return

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await state.clear()
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    if not await _ensure_subscription_campaign(callback, campaign):
        await state.clear()
        return

    await update_campaign(db, campaign, subscription_squads=selected)
    await state.clear()

    use_caption = bool(callback.message.caption) and not bool(callback.message.text)

    await _render_campaign_edit_menu(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        campaign,
        db_user.language,
        use_caption=use_caption,
    )
    await callback.answer('✅ 已保存')


@admin_required
@error_handler
async def toggle_campaign_status(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    new_status = not campaign.is_active
    await update_campaign(db, campaign, is_active=new_status)
    status_text = '已启用' if new_status else 'выключена'
    logger.info('🔄 活动已切换', campaign_id=campaign_id, status_text=status_text)

    await show_campaign_detail(callback, db_user, db)


@admin_required
@error_handler
async def show_campaign_stats(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    texts = get_texts(db_user.language)
    stats = await get_campaign_statistics(db, campaign_id)

    text = ['📊 <b>活动统计</b>']
    text.append(_format_campaign_summary(campaign, texts))
    text.append(f"注册：<b>{stats['registrations']}</b>")
    text.append(f"已发行余额：<b>{texts.format_price(stats['balance_issued'])}</b>")
    text.append(f"已发行订阅：<b>{stats['subscription_issued']}</b>")
    if stats['last_registration']:
        text.append(f"最后注册：{stats['last_registration'].strftime('%d.%m.%Y %H:%M')}")

    await callback.message.edit_text(
        '\n'.join(text),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text='⬅️ 返回',
                        callback_data=f'admin_campaign_manage_{campaign_id}',
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def confirm_delete_campaign(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    text = (
        f'🗑️ <b>删除活动</b>\n\n名称： <b>{html.escape(campaign.name)}</b>\n参数：<code>{html.escape(campaign.start_parameter)}</code>\n\n您确定要删除该营销活动吗？'
    )

    await callback.message.edit_text(
        text,
        reply_markup=get_confirmation_keyboard(
            confirm_action=f'admin_campaign_delete_confirm_{campaign_id}',
            cancel_action=f'admin_campaign_manage_{campaign_id}',
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_campaign_confirmed(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    await delete_campaign(db, campaign)
    await callback.message.edit_text(
        '✅ 该活动已被删除。',
        reply_markup=get_admin_campaigns_keyboard(db_user.language),
    )
    await callback.answer('已删除')


@admin_required
@error_handler
async def start_campaign_creation(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    await state.clear()
    await callback.message.edit_text(
        '🆕 <b>创建广告活动</b>\n\n输入活动名称：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_campaigns')]]
        ),
    )
    await state.set_state(AdminStates.creating_campaign_name)
    await callback.answer()


@admin_required
@error_handler
async def process_campaign_name(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    name = message.text.strip()
    if len(name) < 3 or len(name) > 100:
        await message.answer('❌ 名称必须包含 3 到 100 个字符。再试一次。')
        return

    await state.update_data(campaign_name=name)
    await state.set_state(AdminStates.creating_campaign_start)
    await message.answer(
        '🔗 现在输入开始参数（拉丁字母、数字、- 或 _）：',
    )


@admin_required
@error_handler
async def process_campaign_start_parameter(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    start_param = message.text.strip()
    if not _CAMPAIGN_PARAM_REGEX.match(start_param):
        await message.answer('❌ 只允许使用拉丁字母、数字、符号 - 和 _。长度 3-32 个字符。')
        return

    existing = await get_campaign_by_start_parameter(db, start_param)
    if existing:
        await message.answer('❌ 具有此参数的营销活动已存在。输入另一个参数。')
        return

    await state.update_data(campaign_start_parameter=start_param)
    await state.set_state(AdminStates.creating_campaign_bonus)
    await message.answer(
        '🎯 选择活动奖金类型：',
        reply_markup=get_campaign_bonus_type_keyboard(db_user.language),
    )


@admin_required
@error_handler
async def select_campaign_bonus_type(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    # Определяем тип бонуса из callback_data
    if callback.data.endswith('balance'):
        bonus_type = 'balance'
    elif callback.data.endswith('subscription'):
        bonus_type = 'subscription'
    elif callback.data.endswith('tariff'):
        bonus_type = 'tariff'
    elif callback.data.endswith('none'):
        bonus_type = 'none'
    else:
        bonus_type = 'balance'

    await state.update_data(campaign_bonus_type=bonus_type)

    if bonus_type == 'balance':
        await state.set_state(AdminStates.creating_campaign_balance)
        await callback.message.edit_text(
            '💰 输入您余额上的奖金金额（以卢布为单位）：',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_campaigns')]]
            ),
        )
    elif bonus_type == 'subscription':
        await state.set_state(AdminStates.creating_campaign_subscription_days)
        await callback.message.edit_text(
            '📅 输入试用订阅的持续时间（以天为单位）（1-730）：',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_campaigns')]]
            ),
        )
    elif bonus_type == 'tariff':
        # Показываем выбор тарифа
        tariffs = await get_all_tariffs(db, include_inactive=False)
        if not tariffs:
            await callback.answer(
                '❌ 没有可用的套餐。首先制定套餐。',
                show_alert=True,
            )
            return

        keyboard = []
        for tariff in tariffs[:15]:  # Максимум 15 тарифов
            keyboard.append(
                [
                    types.InlineKeyboardButton(
                        text=f'🎁 {tariff.name}',
                        callback_data=f'campaign_select_tariff_{tariff.id}',
                    )
                ]
            )
        keyboard.append([types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_campaigns')])

        await state.set_state(AdminStates.creating_campaign_tariff_select)
        await callback.message.edit_text(
            '🎁 选择发行套餐：',
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    elif bonus_type == 'none':
        # Сразу создаём кампанию без бонуса
        data = await state.get_data()
        campaign = await create_campaign(
            db,
            name=data['campaign_name'],
            start_parameter=data['campaign_start_parameter'],
            bonus_type='none',
            created_by=db_user.id,
        )
        await state.clear()

        deep_link = await _get_bot_deep_link(callback, campaign.start_parameter)
        texts = get_texts(db_user.language)
        summary = _format_campaign_summary(campaign, texts)
        text = f'✅ <b> 活动已创建！</b>\n\n{summary}\n🔗 链接：<code>{deep_link}</code>'

        await callback.message.edit_text(
            text,
            reply_markup=get_campaign_management_keyboard(campaign.id, campaign.is_active, db_user.language),
        )

    await callback.answer()


@admin_required
@error_handler
async def process_campaign_balance_value(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    try:
        amount_rubles = float(message.text.replace(',', '.'))
    except ValueError:
        await message.answer('❌ 输入正确的金额（例如 100 或 99.5）')
        return

    if amount_rubles <= 0:
        await message.answer('❌ 金额必须大于零')
        return

    amount_kopeks = int(round(amount_rubles * 100))
    data = await state.get_data()

    campaign = await create_campaign(
        db,
        name=data['campaign_name'],
        start_parameter=data['campaign_start_parameter'],
        bonus_type='balance',
        balance_bonus_kopeks=amount_kopeks,
        created_by=db_user.id,
    )

    await state.clear()

    deep_link = await _get_bot_deep_link_from_message(message, campaign.start_parameter)
    texts = get_texts(db_user.language)
    summary = _format_campaign_summary(campaign, texts)
    text = f'✅ <b> 活动已创建！</b>\n\n{summary}\n🔗 链接：<code>{deep_link}</code>'

    await message.answer(
        text,
        reply_markup=get_campaign_management_keyboard(campaign.id, campaign.is_active, db_user.language),
    )


@admin_required
@error_handler
async def process_campaign_subscription_days(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer('❌ 输入天数 (1-730)')
        return

    if days <= 0 or days > 730:
        await message.answer('❌ 持续时间必须为 1 至 730 天')
        return

    await state.update_data(campaign_subscription_days=days)
    await state.set_state(AdminStates.creating_campaign_subscription_traffic)
    await message.answer('🌐 在 GB 中输入流量限制（0=无限制）：')


@admin_required
@error_handler
async def process_campaign_subscription_traffic(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    try:
        traffic = int(message.text.strip())
    except ValueError:
        await message.answer('❌ 输入一个整数（0或更大）')
        return

    if traffic < 0 or traffic > 10000:
        await message.answer('❌ 流量限制必须为0到10000 GB')
        return

    await state.update_data(campaign_subscription_traffic=traffic)
    await state.set_state(AdminStates.creating_campaign_subscription_devices)
    await message.answer(f'📱 输入设备数量（1-{settings.MAX_DEVICES_LIMIT}）：')


@admin_required
@error_handler
async def process_campaign_subscription_devices(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    try:
        devices = int(message.text.strip())
    except ValueError:
        await message.answer('❌ 输入设备的整数个数')
        return

    if devices < 1 or devices > settings.MAX_DEVICES_LIMIT:
        await message.answer(f'❌ 设备数量必须从 1 到 {settings.MAX_DEVICES_LIMIT}')
        return

    await state.update_data(campaign_subscription_devices=devices)
    await state.update_data(campaign_subscription_squads=[])
    await state.set_state(AdminStates.creating_campaign_subscription_servers)

    servers, _ = await get_all_server_squads(db, available_only=False)
    if not servers:
        await message.answer(
            '❌ 未找到可用的服务器。在创建活动之前添加服务器。',
        )
        await state.clear()
        return

    keyboard = _build_campaign_servers_keyboard(servers, [])
    await message.answer(
        '🌍 选择可通过订阅使用的服务器（最多显示 20 个）。',
        reply_markup=keyboard,
    )


@admin_required
@error_handler
async def toggle_campaign_server(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    if not server:
        await callback.answer('❌ 找不到服务器', show_alert=True)
        return

    data = await state.get_data()
    selected = list(data.get('campaign_subscription_squads', []))

    if server.squad_uuid in selected:
        selected.remove(server.squad_uuid)
    else:
        selected.append(server.squad_uuid)

    await state.update_data(campaign_subscription_squads=selected)

    servers, _ = await get_all_server_squads(db, available_only=False)
    keyboard = _build_campaign_servers_keyboard(servers, selected)

    await callback.message.edit_reply_markup(reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def finalize_campaign_subscription(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    data = await state.get_data()
    selected = data.get('campaign_subscription_squads', [])

    if not selected:
        await callback.answer('❗ 至少选择一台服务器', show_alert=True)
        return

    campaign = await create_campaign(
        db,
        name=data['campaign_name'],
        start_parameter=data['campaign_start_parameter'],
        bonus_type='subscription',
        subscription_duration_days=data.get('campaign_subscription_days'),
        subscription_traffic_gb=data.get('campaign_subscription_traffic'),
        subscription_device_limit=data.get('campaign_subscription_devices'),
        subscription_squads=selected,
        created_by=db_user.id,
    )

    await state.clear()

    deep_link = await _get_bot_deep_link(callback, campaign.start_parameter)
    texts = get_texts(db_user.language)
    summary = _format_campaign_summary(campaign, texts)
    text = f'✅ <b> 活动已创建！</b>\n\n{summary}\n🔗 链接：<code>{deep_link}</code>'

    await callback.message.edit_text(
        text,
        reply_markup=get_campaign_management_keyboard(campaign.id, campaign.is_active, db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def select_campaign_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Обработка выбора тарифа для кампании."""
    tariff_id = int(callback.data.split('_')[-1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('❌ 未找到套餐', show_alert=True)
        return

    await state.update_data(campaign_tariff_id=tariff_id, campaign_tariff_name=tariff.name)
    await state.set_state(AdminStates.creating_campaign_tariff_days)
    await callback.message.edit_text(
        f'🎁 选择的套餐：<b>{html.escape(tariff.name)}</b>\n\n📅 输入套餐持续时间（以天为单位）（1-730）：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_campaigns')]]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_campaign_tariff_days(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Обработка ввода длительности тарифа для кампании."""
    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer('❌ 输入天数 (1-730)')
        return

    if days <= 0 or days > 730:
        await message.answer('❌ 持续时间必须为 1 至 730 天')
        return

    data = await state.get_data()
    tariff_id = data.get('campaign_tariff_id')

    if not tariff_id:
        await message.answer('❌ 未选择套餐。重新开始创建您的广告活动。')
        await state.clear()
        return

    campaign = await create_campaign(
        db,
        name=data['campaign_name'],
        start_parameter=data['campaign_start_parameter'],
        bonus_type='tariff',
        tariff_id=tariff_id,
        tariff_duration_days=days,
        created_by=db_user.id,
    )

    # Перезагружаем кампанию с загруженным tariff relationship
    campaign = await get_campaign_by_id(db, campaign.id)

    await state.clear()

    deep_link = await _get_bot_deep_link_from_message(message, campaign.start_parameter)
    texts = get_texts(db_user.language)
    summary = _format_campaign_summary(campaign, texts)
    text = f'✅ <b> 活动已创建！</b>\n\n{summary}\n🔗 链接：<code>{deep_link}</code>'

    await message.answer(
        text,
        reply_markup=get_campaign_management_keyboard(campaign.id, campaign.is_active, db_user.language),
    )


@admin_required
@error_handler
async def start_edit_campaign_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Начало редактирования тарифа кампании."""
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    if not campaign.is_tariff_bonus:
        await callback.answer('❌ 此活动不使用“套餐”类型', show_alert=True)
        return

    tariffs = await get_all_tariffs(db, include_inactive=False)
    if not tariffs:
        await callback.answer('❌ 无可用套餐', show_alert=True)
        return

    keyboard = []
    for tariff in tariffs[:15]:
        is_current = campaign.tariff_id == tariff.id
        emoji = '✅' if is_current else '🎁'
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=f'{emoji} {tariff.name}',
                    callback_data=f'campaign_edit_set_tariff_{campaign_id}_{tariff.id}',
                )
            ]
        )
    keyboard.append([types.InlineKeyboardButton(text='⬅️ 返回', callback_data=f'admin_campaign_edit_{campaign_id}')])

    current_tariff_name = 'Не выбран'
    if campaign.tariff:
        current_tariff_name = campaign.tariff.name

    await callback.message.edit_text(
        f'🎁 <b>更改活动套餐</b>\n\n目前套餐：<b>{current_tariff_name}</b>\n选择新的套餐：',
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


@admin_required
@error_handler
async def set_campaign_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Установка тарифа для кампании."""
    parts = callback.data.split('_')
    campaign_id = int(parts[-2])
    tariff_id = int(parts[-1])

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await callback.answer('❌ 未找到套餐', show_alert=True)
        return

    await update_campaign(db, campaign, tariff_id=tariff_id)
    await callback.answer(f'✅ 套餐更改为“{tariff.name}”')

    await _render_campaign_edit_menu(
        callback.bot,
        callback.message.chat.id,
        callback.message.message_id,
        campaign,
        db_user.language,
    )


@admin_required
@error_handler
async def start_edit_campaign_tariff_days(
    callback: types.CallbackQuery,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Начало редактирования длительности тарифа."""
    campaign_id = int(callback.data.split('_')[-1])
    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await callback.answer('❌ 找不到活动', show_alert=True)
        return

    if not campaign.is_tariff_bonus:
        await callback.answer('❌ 此活动不使用“套餐”类型', show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminStates.editing_campaign_tariff_days)
    await state.update_data(
        editing_campaign_id=campaign_id,
        campaign_edit_message_id=callback.message.message_id,
    )

    await callback.message.edit_text(
        f'📅 <b>更改套餐期限</b>\n\n当前值：<b>{campaign.tariff_duration_days or 0} d.</b>\n输入新的天数 (1-730)：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text='❌ 取消',
                        callback_data=f'admin_campaign_edit_{campaign_id}',
                    )
                ]
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_campaign_tariff_days(
    message: types.Message,
    db_user: User,
    state: FSMContext,
    db: AsyncSession,
):
    """Обработка ввода новой длительности тарифа."""
    data = await state.get_data()
    campaign_id = data.get('editing_campaign_id')
    if not campaign_id:
        await message.answer('❌ 编辑会话已过时。再试一次。')
        await state.clear()
        return

    try:
        days = int(message.text.strip())
    except ValueError:
        await message.answer('❌ 输入天数 (1-730)')
        return

    if days <= 0 or days > 730:
        await message.answer('❌ 持续时间必须为 1 至 730 天')
        return

    campaign = await get_campaign_by_id(db, campaign_id)
    if not campaign:
        await message.answer('❌ 找不到活动')
        await state.clear()
        return

    await update_campaign(db, campaign, tariff_duration_days=days)
    await state.clear()

    await message.answer('✅ 套餐期限已更新。')

    edit_message_id = data.get('campaign_edit_message_id')
    if edit_message_id:
        await _render_campaign_edit_menu(
            message.bot,
            message.chat.id,
            edit_message_id,
            campaign,
            db_user.language,
        )


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_campaigns_menu, F.data == 'admin_campaigns')
    dp.callback_query.register(show_campaigns_overall_stats, F.data == 'admin_campaigns_stats')
    dp.callback_query.register(show_campaigns_list, F.data == 'admin_campaigns_list')
    dp.callback_query.register(show_campaigns_list, F.data.startswith('admin_campaigns_list_page_'))
    dp.callback_query.register(start_campaign_creation, F.data == 'admin_campaigns_create')
    dp.callback_query.register(show_campaign_stats, F.data.startswith('admin_campaign_stats_'))
    dp.callback_query.register(show_campaign_detail, F.data.startswith('admin_campaign_manage_'))
    dp.callback_query.register(start_edit_campaign_name, F.data.startswith('admin_campaign_edit_name_'))
    dp.callback_query.register(
        start_edit_campaign_start_parameter,
        F.data.startswith('admin_campaign_edit_start_'),
    )
    dp.callback_query.register(
        start_edit_campaign_balance_bonus,
        F.data.startswith('admin_campaign_edit_balance_'),
    )
    dp.callback_query.register(
        start_edit_campaign_subscription_days,
        F.data.startswith('admin_campaign_edit_sub_days_'),
    )
    dp.callback_query.register(
        start_edit_campaign_subscription_traffic,
        F.data.startswith('admin_campaign_edit_sub_traffic_'),
    )
    dp.callback_query.register(
        start_edit_campaign_subscription_devices,
        F.data.startswith('admin_campaign_edit_sub_devices_'),
    )
    dp.callback_query.register(
        start_edit_campaign_subscription_servers,
        F.data.startswith('admin_campaign_edit_sub_servers_'),
    )
    dp.callback_query.register(
        save_edit_campaign_subscription_servers,
        F.data.startswith('campaign_edit_servers_save_'),
    )
    dp.callback_query.register(toggle_edit_campaign_server, F.data.startswith('campaign_edit_toggle_'))
    # Tariff handlers ДОЛЖНЫ быть ПЕРЕД общим admin_campaign_edit_
    dp.callback_query.register(start_edit_campaign_tariff_days, F.data.startswith('admin_campaign_edit_tariff_days_'))
    dp.callback_query.register(start_edit_campaign_tariff, F.data.startswith('admin_campaign_edit_tariff_'))
    # Общий паттерн ПОСЛЕДНИМ
    dp.callback_query.register(show_campaign_edit_menu, F.data.startswith('admin_campaign_edit_'))
    dp.callback_query.register(delete_campaign_confirmed, F.data.startswith('admin_campaign_delete_confirm_'))
    dp.callback_query.register(confirm_delete_campaign, F.data.startswith('admin_campaign_delete_'))
    dp.callback_query.register(toggle_campaign_status, F.data.startswith('admin_campaign_toggle_'))
    dp.callback_query.register(finalize_campaign_subscription, F.data == 'campaign_servers_save')
    dp.callback_query.register(toggle_campaign_server, F.data.startswith('campaign_toggle_server_'))
    dp.callback_query.register(select_campaign_bonus_type, F.data.startswith('campaign_bonus_'))
    dp.callback_query.register(select_campaign_tariff, F.data.startswith('campaign_select_tariff_'))
    dp.callback_query.register(set_campaign_tariff, F.data.startswith('campaign_edit_set_tariff_'))

    dp.message.register(process_campaign_name, AdminStates.creating_campaign_name)
    dp.message.register(process_campaign_start_parameter, AdminStates.creating_campaign_start)
    dp.message.register(process_campaign_balance_value, AdminStates.creating_campaign_balance)
    dp.message.register(
        process_campaign_subscription_days,
        AdminStates.creating_campaign_subscription_days,
    )
    dp.message.register(
        process_campaign_subscription_traffic,
        AdminStates.creating_campaign_subscription_traffic,
    )
    dp.message.register(
        process_campaign_subscription_devices,
        AdminStates.creating_campaign_subscription_devices,
    )
    dp.message.register(process_edit_campaign_name, AdminStates.editing_campaign_name)
    dp.message.register(
        process_edit_campaign_start_parameter,
        AdminStates.editing_campaign_start,
    )
    dp.message.register(
        process_edit_campaign_balance_bonus,
        AdminStates.editing_campaign_balance,
    )
    dp.message.register(
        process_edit_campaign_subscription_days,
        AdminStates.editing_campaign_subscription_days,
    )
    dp.message.register(
        process_edit_campaign_subscription_traffic,
        AdminStates.editing_campaign_subscription_traffic,
    )
    dp.message.register(
        process_edit_campaign_subscription_devices,
        AdminStates.editing_campaign_subscription_devices,
    )
    dp.message.register(
        process_campaign_tariff_days,
        AdminStates.creating_campaign_tariff_days,
    )
    dp.message.register(
        process_edit_campaign_tariff_days,
        AdminStates.editing_campaign_tariff_days,
    )



