import hashlib
import json
from html import escape as html_escape
from pathlib import Path

import qrcode
import structlog
from aiogram import Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_referral_keyboard
from app.localization.texts import get_texts
from app.services.admin_notification_service import AdminNotificationService, NotificationCategory
from app.services.referral_withdrawal_service import referral_withdrawal_service
from app.states import ReferralWithdrawalStates
from app.utils.photo_message import edit_or_answer_photo
from app.utils.user_utils import (
    get_detailed_referral_list,
    get_effective_referral_commission_percent,
    get_referral_analytics,
    get_user_referral_summary,
)


logger = structlog.get_logger(__name__)


async def show_referral_info(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    # Проверяем, включена ли реферальная программа
    if not settings.is_referral_program_enabled():
        texts = get_texts(db_user.language)
        await callback.answer(texts.t('REFERRAL_PROGRAM_DISABLED', '推荐计划已禁用'), show_alert=True)
        return

    texts = get_texts(db_user.language)

    if not db_user.referral_code:
        await callback.answer(texts.t('REFERRAL_CODE_NOT_ASSIGNED', '未分配推荐代码'), show_alert=True)
        return

    summary = await get_user_referral_summary(db, db_user.id)

    bot_username = (await callback.bot.get_me()).username
    bot_referral_link = settings.get_bot_referral_link(db_user.referral_code, bot_username)
    cabinet_referral_link = settings.get_cabinet_referral_link(db_user.referral_code)

    referral_text = (
        texts.t('REFERRAL_PROGRAM_TITLE', '👥<b>推荐计划</b>')
        + '\n\n'
        + texts.t('REFERRAL_STATS_HEADER', '📊<b>您的统计数据：</b>')
        + '\n'
        + texts.t(
            'REFERRAL_STATS_INVITED',
            '•已邀请用户：<b>{count}</b>',
        ).format(count=summary['invited_count'])
        + '\n'
        + texts.t(
            'REFERRAL_STATS_FIRST_TOPUPS',
            '•已完成首次充值：<b>{count}</b>',
        ).format(count=summary['paid_referrals_count'])
        + '\n'
        + texts.t(
            'REFERRAL_STATS_ACTIVE',
            '•活跃推荐：<b>{count}</b>',
        ).format(count=summary['active_referrals_count'])
        + '\n'
        + texts.t(
            'REFERRAL_STATS_CONVERSION',
            '•转化率：<b>{rate}%</b>',
        ).format(rate=summary['conversion_rate'])
        + '\n'
        + texts.t(
            'REFERRAL_STATS_TOTAL_EARNED',
            '•总共赚取：<b>{amount}</b>',
        ).format(amount=texts.format_price(summary['total_earned_kopeks']))
        + '\n'
        + texts.t(
            'REFERRAL_STATS_MONTH_EARNED',
            '•上个月赚取：<b>{amount}</b>',
        ).format(amount=texts.format_price(summary['month_earned_kopeks']))
        + '\n\n'
        + texts.t('REFERRAL_REWARDS_HEADER', '🎁<b>奖励如何运作：</b>')
    )

    if settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS > 0:
        referral_text += '\n' + texts.t(
            'REFERRAL_REWARD_NEW_USER',
            '•新用户首次充值<b>{minimum}</b>起将获得：<b>{bonus}</b>',
        ).format(
            bonus=texts.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS),
            minimum=texts.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS),
        )

    if settings.REFERRAL_INVITER_BONUS_KOPEKS > 0:
        referral_text += '\n' + texts.t(
            'REFERRAL_REWARD_INVITER',
            '•推荐首次充值时您将获得：<b>{bonus}</b>',
        ).format(bonus=texts.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS))

    if settings.REFERRAL_MAX_COMMISSION_PAYMENTS > 0:
        commission_line = texts.t(
            'REFERRAL_REWARD_COMMISSION_LIMITED',
            '•前{max_payments}次推荐充值的佣金：<b>{percent}%</b>',
        ).format(
            percent=get_effective_referral_commission_percent(db_user),
            max_payments=settings.REFERRAL_MAX_COMMISSION_PAYMENTS,
        )
    else:
        commission_line = texts.t(
            'REFERRAL_REWARD_COMMISSION',
            '•每次推荐充值的佣金：<b>{percent}%</b>',
        ).format(percent=get_effective_referral_commission_percent(db_user))

    referral_text += '\n' + commission_line + '\n\n'

    # Show bot link
    referral_text += (
        texts.t('REFERRAL_BOT_LINK_TITLE', '🤖<b>机器人链接：</b>')
        + f'\n<code>{html_escape(bot_referral_link)}</code>\n'
    )

    # Show cabinet link if configured
    if cabinet_referral_link:
        referral_text += (
            '\n'
            + texts.t('REFERRAL_CABINET_LINK_TITLE', '🌐<b>控制面板链接：</b>')
            + f'\n<code>{html_escape(cabinet_referral_link)}</code>\n'
        )

    referral_text += (
        '\n'
        + texts.t('REFERRAL_CODE_TITLE', '🆔<b>您的代码：</b><code>{code}</code>').format(
            code=html_escape(str(db_user.referral_code or ''))
        )
        + '\n\n'
    )

    if summary['recent_earnings']:
        meaningful_earnings = [earning for earning in summary['recent_earnings'][:5] if earning['amount_kopeks'] > 0]

        if meaningful_earnings:
            referral_text += (
                texts.t(
                    'REFERRAL_RECENT_EARNINGS_HEADER',
                    '💰<b>最近收入：</b>',
                )
                + '\n'
            )
            for earning in meaningful_earnings[:3]:
                reason_text = {
                    'referral_first_topup': texts.t(
                        'REFERRAL_EARNING_REASON_FIRST_TOPUP',
                        '🎉首次充值',
                    ),
                    'referral_commission_topup': texts.t(
                        'REFERRAL_EARNING_REASON_COMMISSION_TOPUP',
                        '💰充值佣金',
                    ),
                    'referral_commission': texts.t(
                        'REFERRAL_EARNING_REASON_COMMISSION_PURCHASE',
                        '💰购买佣金',
                    ),
                }.get(earning['reason'], earning['reason'])

                referral_text += (
                    texts.t(
                        'REFERRAL_RECENT_EARNINGS_ITEM',
                        '•{reason}:<b>{amount}</b>(来自{referral_name})',
                    ).format(
                        reason=reason_text,
                        amount=texts.format_price(earning['amount_kopeks']),
                        referral_name=html_escape(str(earning['referral_name'] or '')),
                    )
                    + '\n'
                )
            referral_text += '\n'

    if summary['earnings_by_type']:
        referral_text += (
            texts.t(
                'REFERRAL_EARNINGS_BY_TYPE_HEADER',
                '📈<b>按类型收入：</b>',
            )
            + '\n'
        )

        if 'referral_first_topup' in summary['earnings_by_type']:
            data = summary['earnings_by_type']['referral_first_topup']
            if data['total_amount_kopeks'] > 0:
                referral_text += (
                    texts.t(
                        'REFERRAL_EARNINGS_FIRST_TOPUPS',
                        '•首次充值奖励：<b>{count}</b>({amount})',
                    ).format(
                        count=data['count'],
                        amount=texts.format_price(data['total_amount_kopeks']),
                    )
                    + '\n'
                )

        if 'referral_commission_topup' in summary['earnings_by_type']:
            data = summary['earnings_by_type']['referral_commission_topup']
            if data['total_amount_kopeks'] > 0:
                referral_text += (
                    texts.t(
                        'REFERRAL_EARNINGS_TOPUPS',
                        '•充值佣金：<b>{count}</b>({amount})',
                    ).format(
                        count=data['count'],
                        amount=texts.format_price(data['total_amount_kopeks']),
                    )
                    + '\n'
                )

        if 'referral_commission' in summary['earnings_by_type']:
            data = summary['earnings_by_type']['referral_commission']
            if data['total_amount_kopeks'] > 0:
                referral_text += (
                    texts.t(
                        'REFERRAL_EARNINGS_PURCHASES',
                        '•购买佣金：<b>{count}</b>({amount})',
                    ).format(
                        count=data['count'],
                        amount=texts.format_price(data['total_amount_kopeks']),
                    )
                    + '\n'
                )

        referral_text += '\n'

    referral_text += texts.t(
        'REFERRAL_INVITE_FOOTER',
        '📢邀请朋友并赚钱！',
    )

    await edit_or_answer_photo(
        callback,
        referral_text,
        get_referral_keyboard(db_user.language),
    )
    await callback.answer()


async def show_referral_qr(
    callback: types.CallbackQuery,
    db_user: User,
):
    texts = get_texts(db_user.language)

    if not db_user.referral_code:
        await callback.answer(texts.t('REFERRAL_CODE_NOT_ASSIGNED', '未分配推荐代码'), show_alert=True)
        return

    await callback.answer()

    bot_username = (await callback.bot.get_me()).username
    bot_referral_link = settings.get_bot_referral_link(db_user.referral_code, bot_username)

    qr_dir = Path('data') / 'referral_qr'
    qr_dir.mkdir(parents=True, exist_ok=True)

    link_hash = hashlib.md5(bot_referral_link.encode()).hexdigest()[:8]
    file_path = qr_dir / f'{db_user.id}_{link_hash}.png'
    if not file_path.exists():
        img = qrcode.make(bot_referral_link)
        img.save(file_path)

    photo = FSInputFile(file_path)
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data='menu_referrals')]]
    )

    caption = texts.t(
        'REFERRAL_QR_BOT_LINK',
        '🤖机器人链接：\n{link}',
    ).format(link=bot_referral_link)

    cabinet_referral_link = settings.get_cabinet_referral_link(db_user.referral_code)
    if cabinet_referral_link:
        caption += '\n\n' + texts.t(
            'REFERRAL_QR_CABINET_LINK',
            '🌐控制面板链接：\n{link}',
        ).format(link=cabinet_referral_link)

    try:
        await callback.message.edit_media(
            types.InputMediaPhoto(media=photo, caption=caption),
            reply_markup=keyboard,
        )
    except TelegramBadRequest:
        await callback.message.delete()
        await callback.message.answer_photo(
            photo,
            caption=caption,
            reply_markup=keyboard,
        )


async def show_detailed_referral_list(callback: types.CallbackQuery, db_user: User, db: AsyncSession, page: int = 1):
    texts = get_texts(db_user.language)

    referrals_data = await get_detailed_referral_list(db, db_user.id, limit=10, offset=(page - 1) * 10)

    if not referrals_data['referrals']:
        await edit_or_answer_photo(
            callback,
            texts.t(
                'REFERRAL_LIST_EMPTY',
                '📋您目前没有推荐。\n\n分享您的推荐链接开始赚钱吧！',
            ),
            types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data='menu_referrals')]]
            ),
            parse_mode=None,
        )
        await callback.answer()
        return

    text = (
        texts.t(
            'REFERRAL_LIST_HEADER',
            '👥<b>您的推荐</b>(第{current}/{total}页)',
        ).format(
            current=referrals_data['current_page'],
            total=referrals_data['total_pages'],
        )
        + '\n\n'
    )

    for i, referral in enumerate(referrals_data['referrals'], 1):
        status_emoji = '🟢' if referral['status'] == 'active' else '🔴'

        topup_emoji = '💰' if referral['has_made_first_topup'] else '⏳'

        text += (
            texts.t(
                'REFERRAL_LIST_ITEM_HEADER',
                '{index}.{status}<b>{name}</b>',
            ).format(index=i, status=status_emoji, name=html_escape(str(referral['full_name'] or '')))
            + '\n'
        )
        text += (
            texts.t(
                'REFERRAL_LIST_ITEM_TOPUPS',
                '{emoji}充值：{count}',
            ).format(emoji=topup_emoji, count=referral['topups_count'])
            + '\n'
        )
        text += (
            texts.t(
                'REFERRAL_LIST_ITEM_EARNED',
                '💎从中赚取：{amount}',
            ).format(amount=texts.format_price(referral['total_earned_kopeks']))
            + '\n'
        )
        text += (
            texts.t(
                'REFERRAL_LIST_ITEM_REGISTERED',
                '📅注册：{days}天前',
            ).format(days=referral['days_since_registration'])
            + '\n'
        )

        if referral['days_since_activity'] is not None:
            text += (
                texts.t(
                    'REFERRAL_LIST_ITEM_ACTIVITY',
                    '🕐活跃：{days}天前',
                ).format(days=referral['days_since_activity'])
                + '\n'
            )
        else:
            text += (
                texts.t(
                    'REFERRAL_LIST_ITEM_ACTIVITY_LONG_AGO',
                    '🕐活跃：很久以前',
                )
                + '\n'
            )

        text += '\n'

    keyboard = []
    nav_buttons = []

    if referrals_data['has_prev']:
        nav_buttons.append(
            types.InlineKeyboardButton(
                text=texts.t('REFERRAL_LIST_PREV_PAGE', '⬅️上一页'), callback_data=f'referral_list_page_{page - 1}'
            )
        )

    if referrals_data['has_next']:
        nav_buttons.append(
            types.InlineKeyboardButton(
                text=texts.t('REFERRAL_LIST_NEXT_PAGE', '下一页➡️'), callback_data=f'referral_list_page_{page + 1}'
            )
        )

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([types.InlineKeyboardButton(text=texts.BACK, callback_data='menu_referrals')])

    await edit_or_answer_photo(
        callback,
        text,
        types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


async def show_referral_analytics(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)

    analytics = await get_referral_analytics(db, db_user.id)

    text = texts.t('REFERRAL_ANALYTICS_TITLE', '📊<b>推荐分析</b>') + '\n\n'

    text += (
        texts.t(
            'REFERRAL_ANALYTICS_EARNINGS_HEADER',
            '💰<b>按周期收入：</b>',
        )
        + '\n'
    )
    text += (
        texts.t(
            'REFERRAL_ANALYTICS_EARNINGS_TODAY',
            '•今天：{amount}',
        ).format(amount=texts.format_price(analytics['earnings_by_period']['today']))
        + '\n'
    )
    text += (
        texts.t(
            'REFERRAL_ANALYTICS_EARNINGS_WEEK',
            '•本周：{amount}',
        ).format(amount=texts.format_price(analytics['earnings_by_period']['week']))
        + '\n'
    )
    text += (
        texts.t(
            'REFERRAL_ANALYTICS_EARNINGS_MONTH',
            '•本月：{amount}',
        ).format(amount=texts.format_price(analytics['earnings_by_period']['month']))
        + '\n'
    )
    text += (
        texts.t(
            'REFERRAL_ANALYTICS_EARNINGS_QUARTER',
            '•本季度：{amount}',
        ).format(amount=texts.format_price(analytics['earnings_by_period']['quarter']))
        + '\n\n'
    )

    if analytics['top_referrals']:
        text += (
            texts.t(
                'REFERRAL_ANALYTICS_TOP_TITLE',
                '🏆<b>前{count}名推荐：</b>',
            ).format(count=len(analytics['top_referrals']))
            + '\n'
        )
        for i, ref in enumerate(analytics['top_referrals'], 1):
            text += (
                texts.t(
                    'REFERRAL_ANALYTICS_TOP_ITEM',
                    '{index}.{name}:{amount}({count}次收入)',
                ).format(
                    index=i,
                    name=html_escape(str(ref['referral_name'] or '')),
                    amount=texts.format_price(ref['total_earned_kopeks']),
                    count=ref['earnings_count'],
                )
                + '\n'
            )
        text += '\n'

    text += texts.t(
        'REFERRAL_ANALYTICS_FOOTER',
        '📈继续发展您的推荐网络！',
    )

    await edit_or_answer_photo(
        callback,
        text,
        types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data='menu_referrals')]]
        ),
    )
    await callback.answer()


async def create_invite_message(callback: types.CallbackQuery, db_user: User):
    texts = get_texts(db_user.language)

    if not db_user.referral_code:
        await callback.answer(texts.t('REFERRAL_CODE_NOT_ASSIGNED', '未分配推荐代码'), show_alert=True)
        return

    bot_username = (await callback.bot.get_me()).username
    bot_referral_link = settings.get_bot_referral_link(db_user.referral_code, bot_username)
    cabinet_referral_link = settings.get_cabinet_referral_link(db_user.referral_code)

    bonus_block = ''
    if settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS > 0:
        bonus_block = '\n\n' + texts.t(
            'REFERRAL_INVITE_BONUS',
            '💎首次充值{minimum}起，您将获得{bonus}余额奖励！',
        ).format(
            minimum=texts.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS),
            bonus=texts.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS),
        )

    cabinet_block = ''
    if cabinet_referral_link:
        cabinet_block = f'\n\n🌐 {cabinet_referral_link}'

    invite_text = texts.t(
        'REFERRAL_INVITE_TEXT',
        '🎉加入网络代理服务！{bonus_block}\n\n🚀快速连接\n🌍全球服务器\n🔒可靠保护\n\n👇点击链接：\n{link}{cabinet_block}',
    ).format(
        bonus_block=bonus_block,
        link=bot_referral_link,
        cabinet_block=cabinet_block,
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=texts.BACK, callback_data='menu_referrals')],
        ]
    )

    await edit_or_answer_photo(
        callback,
        (
            texts.t('REFERRAL_INVITE_CREATED_TITLE', '📝<b>邀请已创建！</b>')
            + '\n\n'
            + texts.t(
                'REFERRAL_INVITE_CREATED_INSTRUCTION',
                '点击下方文本即可复制：',
            )
            + '\n\n'
            f'<blockquote><code>{html_escape(invite_text)}</code></blockquote>'
        ),
        keyboard,
    )
    await callback.answer()


async def show_withdrawal_info(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    """Показывает информацию о выводе реферального баланса."""
    texts = get_texts(db_user.language)

    if not settings.is_referral_withdrawal_enabled():
        await callback.answer(texts.t('REFERRAL_WITHDRAWAL_DISABLED', '输出功能禁用'), show_alert=True)
        return

    # Получаем детальную статистику баланса
    stats = await referral_withdrawal_service.get_referral_balance_stats(db, db_user.id)
    min_amount = settings.REFERRAL_WITHDRAWAL_MIN_AMOUNT_KOPEKS
    cooldown_days = settings.REFERRAL_WITHDRAWAL_COOLDOWN_DAYS

    # Проверяем возможность вывода
    can_request, reason, _stats = await referral_withdrawal_service.can_request_withdrawal(db, db_user.id)

    text = texts.t('REFERRAL_WITHDRAWAL_TITLE', '💸 <b>提取推荐余额</b>') + '\n\n'

    # Показываем детальную статистику
    text += referral_withdrawal_service.format_balance_stats_for_user(stats, texts)
    text += '\n'

    text += (
        texts.t('REFERRAL_WITHDRAWAL_MIN_AMOUNT', '📊 最低金额：<b>{amount}</b>').format(
            amount=texts.format_price(min_amount)
        )
        + '\n'
    )
    text += (
        texts.t('REFERRAL_WITHDRAWAL_COOLDOWN', '⏱ 输出频率：每<b>{days}</b>天一次').format(days=cooldown_days)
        + '\n\n'
    )

    keyboard = []

    if can_request:
        text += texts.t('REFERRAL_WITHDRAWAL_READY', '✅ 您可以申请提款') + '\n'
        keyboard.append(
            [
                types.InlineKeyboardButton(
                    text=texts.t('REFERRAL_WITHDRAWAL_REQUEST_BUTTON', '📝 提交申请'),
                    callback_data='referral_withdrawal_start',
                )
            ]
        )
    else:
        text += f'❌ {html_escape(str(reason))}\n'

    keyboard.append([types.InlineKeyboardButton(text=texts.BACK, callback_data='menu_referrals')])

    await edit_or_answer_photo(callback, text, types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


async def start_withdrawal_request(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    """Начинает процесс оформления заявки на вывод."""
    texts = get_texts(db_user.language)

    # Повторная проверка
    can_request, reason, wd_stats = await referral_withdrawal_service.can_request_withdrawal(db, db_user.id)
    if not can_request:
        await callback.answer(reason, show_alert=True)
        return

    available = wd_stats.get('available_total', 0) if wd_stats else 0

    # Сохраняем доступный баланс в состоянии
    await state.update_data(available_balance=available)
    await state.set_state(ReferralWithdrawalStates.waiting_for_amount)

    text = texts.t(
        'REFERRAL_WITHDRAWAL_ENTER_AMOUNT', '💸 输入以卢布为单位的提款金额\n\n可用：<b>{amount}</b>'
    ).format(amount=texts.format_price(available))

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t('REFERRAL_WITHDRAWAL_ALL', f'撤回一切（{available / 100:.0f}₽）'),
                    callback_data=f'referral_withdrawal_amount_{available}',
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t('CANCEL', '❌取消'), callback_data='referral_withdrawal_cancel'
                )
            ],
        ]
    )

    await edit_or_answer_photo(callback, text, keyboard)
    await callback.answer()


async def process_withdrawal_amount(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    """Обрабатывает ввод суммы для вывода."""
    texts = get_texts(db_user.language)
    data = await state.get_data()
    available = data.get('available_balance', 0)

    try:
        # Парсим сумму (в рублях)
        amount_text = message.text.strip().replace(',', '.').replace('₽', '').replace(' ', '')
        amount_rubles = float(amount_text)
        amount_kopeks = int(amount_rubles * 100)

        if amount_kopeks <= 0:
            await message.answer(texts.t('REFERRAL_WITHDRAWAL_INVALID_AMOUNT', '❌ 输入正数'))
            return

        min_amount = settings.REFERRAL_WITHDRAWAL_MIN_AMOUNT_KOPEKS
        if amount_kopeks < min_amount:
            await message.answer(
                texts.t('REFERRAL_WITHDRAWAL_MIN_ERROR', '❌ 最低金额：{amount}').format(
                    amount=texts.format_price(min_amount)
                )
            )
            return

        if amount_kopeks > available:
            await message.answer(
                texts.t('REFERRAL_WITHDRAWAL_INSUFFICIENT', '❌ 资金不足。可用：{amount}').format(
                    amount=texts.format_price(available)
                )
            )
            return

        # Сохраняем сумму и переходим к вводу реквизитов
        await state.update_data(withdrawal_amount=amount_kopeks)
        await state.set_state(ReferralWithdrawalStates.waiting_for_payment_details)

        text = texts.t(
            'REFERRAL_WITHDRAWAL_ENTER_DETAILS',
            '💳 输入转账详细信息：\n\n例如：\n• SBP：+7 999 123-45-67（储蓄银行）',
        )

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.t('CANCEL', '❌取消'), callback_data='referral_withdrawal_cancel'
                    )
                ]
            ]
        )

        await message.answer(text, reply_markup=keyboard)

    except ValueError:
        await message.answer(texts.t('REFERRAL_WITHDRAWAL_INVALID_AMOUNT', '❌ 输入正确的金额'))


async def process_withdrawal_amount_callback(
    callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext
):
    """Обрабатывает выбор суммы для вывода через кнопку."""
    texts = get_texts(db_user.language)

    # Получаем сумму из callback_data
    amount_kopeks = int(callback.data.split('_')[-1])

    # Сохраняем сумму и переходим к вводу реквизитов
    await state.update_data(withdrawal_amount=amount_kopeks)
    await state.set_state(ReferralWithdrawalStates.waiting_for_payment_details)

    text = texts.t(
        'REFERRAL_WITHDRAWAL_ENTER_DETAILS',
        '💳 输入转账详细信息：\n\n例如：\n• SBP：+7 999 123-45-67（储蓄银行）',
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t('CANCEL', '❌取消'), callback_data='referral_withdrawal_cancel'
                )
            ]
        ]
    )

    await edit_or_answer_photo(callback, text, keyboard)
    await callback.answer()


async def process_payment_details(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    """Обрабатывает ввод реквизитов и показывает подтверждение."""
    texts = get_texts(db_user.language)
    data = await state.get_data()
    amount_kopeks = data.get('withdrawal_amount', 0)
    payment_details = message.text.strip()

    if len(payment_details) < 10:
        await message.answer(texts.t('REFERRAL_WITHDRAWAL_DETAILS_TOO_SHORT', '❌ 细节太短'))
        return

    # Сохраняем реквизиты
    await state.update_data(payment_details=payment_details)
    await state.set_state(ReferralWithdrawalStates.confirming)

    text = texts.t('REFERRAL_WITHDRAWAL_CONFIRM_TITLE', '📋<b>确认申请</b>') + '\n\n'
    text += (
        texts.t('REFERRAL_WITHDRAWAL_CONFIRM_AMOUNT', '💰 金额：<b>{amount}</b>').format(
            amount=texts.format_price(amount_kopeks)
        )
        + '\n\n'
    )
    text += (
        texts.t('REFERRAL_WITHDRAWAL_CONFIRM_DETAILS', '💳详情：\n<代码>{details}</code>').format(
            details=html_escape(payment_details)
        )
        + '\n\n'
    )
    text += texts.t('REFERRAL_WITHDRAWAL_CONFIRM_WARNING', '⚠️ 发送申请后将由管理部门审核')

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t('REFERRAL_WITHDRAWAL_CONFIRM_BUTTON', '✅ 确认'),
                    callback_data='referral_withdrawal_confirm',
                )
            ],
            [
                types.InlineKeyboardButton(
                    text=texts.t('CANCEL', '❌取消'), callback_data='referral_withdrawal_cancel'
                )
            ],
        ]
    )

    await message.answer(text, reply_markup=keyboard)


async def confirm_withdrawal_request(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    """Подтверждает и создаёт заявку на вывод."""
    texts = get_texts(db_user.language)
    data = await state.get_data()
    amount_kopeks = data.get('withdrawal_amount', 0)
    payment_details = data.get('payment_details', '')

    await state.clear()

    # Создаём заявку
    request, error = await referral_withdrawal_service.create_withdrawal_request(
        db, db_user.id, amount_kopeks, payment_details
    )

    if error:
        await callback.answer(f'❌ {error}', show_alert=True)
        return

    # Отправляем уведомление админам
    analysis = json.loads(request.risk_analysis) if request.risk_analysis else {}

    user_id_display = html_escape(str(db_user.telegram_id or db_user.email or f'#{db_user.id}'))
    safe_name = html_escape(db_user.full_name or 'Без имени')
    safe_details = html_escape(payment_details)
    admin_text = f"""
🔔 <b>Новая заявка на вывод #{request.id}</b>

👤 Пользователь: {safe_name}
🆔 ID: <code>{user_id_display}</code>
💰 Сумма: <b>{amount_kopeks / 100:.0f}₽</b>

💳 Реквизиты:
<code>{safe_details}</code>

{referral_withdrawal_service.format_analysis_for_admin(analysis)}
"""

    # Формируем клавиатуру - кнопка профиля только для Telegram-пользователей
    keyboard_rows = [
        [
            types.InlineKeyboardButton(text='✅ 批准', callback_data=f'admin_withdrawal_approve_{request.id}'),
            types.InlineKeyboardButton(text='❌ 拒绝', callback_data=f'admin_withdrawal_reject_{request.id}'),
        ]
    ]
    if db_user.telegram_id:
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text='👤 用户资料', callback_data=f'admin_user_{db_user.telegram_id}'
                )
            ]
        )
    admin_keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    try:
        notification_service = AdminNotificationService(callback.bot)
        await notification_service.send_admin_notification(
            admin_text, reply_markup=admin_keyboard, category=NotificationCategory.PARTNERS
        )
    except Exception as e:
        logger.error('Ошибка отправки уведомления админам о заявке на вывод', error=e)

    # Уведомление в топик, если настроено
    topic_id = settings.REFERRAL_WITHDRAWAL_NOTIFICATIONS_TOPIC_ID
    if topic_id and settings.ADMIN_NOTIFICATIONS_CHAT_ID:
        try:
            await callback.bot.send_message(
                chat_id=settings.ADMIN_NOTIFICATIONS_CHAT_ID,
                message_thread_id=topic_id,
                text=admin_text,
                reply_markup=admin_keyboard,
                parse_mode='HTML',
            )
        except Exception as e:
            logger.error('Ошибка отправки уведомления в топик о заявке на вывод', error=e)

    # Отвечаем пользователю
    text = texts.t(
        'REFERRAL_WITHDRAWAL_SUCCESS',
        '✅ <b>请求#{id} 已创建！</b>\n\n金额：<b>{amount}</b>\n\n您的申请将由管理部门审核。我们将通知您结果。',
    ).format(id=request.id, amount=texts.format_price(amount_kopeks))

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data='menu_referrals')]]
    )

    await edit_or_answer_photo(callback, text, keyboard)
    await callback.answer()


async def cancel_withdrawal_request(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    """Отменяет процесс создания заявки на вывод."""
    await state.clear()
    texts = get_texts(db_user.language)
    await callback.answer(texts.t('CANCELLED', '取消'))

    # Возвращаем в меню партнёрки
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data='menu_referrals')]]
    )
    await edit_or_answer_photo(callback, texts.t('REFERRAL_WITHDRAWAL_CANCELLED', '❌ 申请已取消'), keyboard)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_referral_info, F.data == 'menu_referrals')

    dp.callback_query.register(create_invite_message, F.data == 'referral_create_invite')

    dp.callback_query.register(show_referral_qr, F.data == 'referral_show_qr')

    dp.callback_query.register(show_detailed_referral_list, F.data == 'referral_list')

    dp.callback_query.register(show_referral_analytics, F.data == 'referral_analytics')

    async def handle_referral_list_page(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
        page = int(callback.data.split('_')[-1])
        await show_detailed_referral_list(callback, db_user, db, page)

    dp.callback_query.register(handle_referral_list_page, F.data.startswith('referral_list_page_'))

    # Хендлеры вывода реферального баланса
    dp.callback_query.register(show_withdrawal_info, F.data == 'referral_withdrawal')

    dp.callback_query.register(start_withdrawal_request, F.data == 'referral_withdrawal_start')

    dp.callback_query.register(process_withdrawal_amount_callback, F.data.startswith('referral_withdrawal_amount_'))

    dp.callback_query.register(confirm_withdrawal_request, F.data == 'referral_withdrawal_confirm')

    dp.callback_query.register(cancel_withdrawal_request, F.data == 'referral_withdrawal_cancel')

    # Обработка текстового ввода суммы
    dp.message.register(process_withdrawal_amount, ReferralWithdrawalStates.waiting_for_amount)

    # Обработка текстового ввода реквизитов
    dp.message.register(process_payment_details, ReferralWithdrawalStates.waiting_for_payment_details)
