import html

import structlog
from aiogram import types

from app.config import settings
from app.database.models import User
from app.localization.texts import get_texts
from app.utils.decorators import error_handler


logger = structlog.get_logger(__name__)


@error_handler
async def start_tribute_payment(
    callback: types.CallbackQuery,
    db_user: User,
):
    texts = get_texts(db_user.language)

    # Проверка ограничения на пополнение
    if getattr(db_user, 'restriction_topup', False):
        reason = html.escape(getattr(db_user, 'restriction_reason', None) or 'Действие ограничено администратором')
        support_url = settings.get_support_contact_url()
        keyboard = []
        if support_url:
            keyboard.append([types.InlineKeyboardButton(text='🆘 申诉', url=support_url)])
        keyboard.append([types.InlineKeyboardButton(text=texts.BACK, callback_data='menu_balance')])

        await callback.message.edit_text(
            f'🚫 <b>补货有限</b>\n\n{reason}\n\n如果您认为这是一个错误，您可以对该决定提出申诉。',
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
        await callback.answer()
        return

    if not settings.TRIBUTE_ENABLED:
        await callback.answer('❌暂时无法刷卡支付', show_alert=True)
        return

    try:
        from app.services.tribute_service import TributeService

        tribute_service = TributeService(callback.bot)
        payment_url = await tribute_service.create_payment_link(
            user_id=db_user.telegram_id,
            amount_kopeks=0,
            description='Пополнение баланса VPN',
        )

        if not payment_url:
            await callback.answer('❌ 创建付款时出错', show_alert=True)
            return

        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='💳 前往支付', url=payment_url)],
                [types.InlineKeyboardButton(text=texts.BACK, callback_data='balance_topup')],
            ]
        )

        message_text = (
            '💳<b>银行卡充值</b>\n\n• 输入 100₽ 之间的任意金额\n• 通过Tribute 进行安全支付\n• 即时记入余额\n• 我们接受 Visa、MasterCard、MIR 卡\n\n• 🚨 请勿匿名付款！\n\n点击按钮进行付款：'
        )

        await callback.message.edit_text(
            message_text,
            reply_markup=keyboard,
            parse_mode='HTML',
        )

        TributeService.remember_invoice_message(
            db_user.telegram_id,
            callback.message.chat.id,
            callback.message.message_id,
        )

    except Exception as e:
        logger.error('Ошибка создания Tribute платежа', error=e)
        await callback.answer('❌ 创建付款时出错', show_alert=True)

    await callback.answer()
