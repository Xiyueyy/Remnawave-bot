from __future__ import annotations

import html
import math
from datetime import UTC, datetime

import structlog
from aiogram import Dispatcher, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import PaymentMethod, User
from app.localization.texts import get_texts
from app.services.payment_service import PaymentService
from app.services.payment_verification_service import (
    SUPPORTED_MANUAL_CHECK_METHODS,
    PendingPayment,
    get_payment_record,
    list_recent_pending_payments,
    run_manual_check,
)
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_datetime, format_time_ago, format_username


logger = structlog.get_logger(__name__)

PAGE_SIZE = 6


def _method_display(method: PaymentMethod) -> str:
    if method == PaymentMethod.MULENPAY:
        return settings.get_mulenpay_display_name()
    if method == PaymentMethod.PAL24:
        return 'PayPalych'
    if method == PaymentMethod.WATA:
        return 'WATA'
    if method == PaymentMethod.HELEKET:
        return 'Heleket'
    if method == PaymentMethod.YOOKASSA:
        return 'YooKassa'
    if method == PaymentMethod.PLATEGA:
        return settings.get_platega_display_name()
    if method == PaymentMethod.CRYPTOBOT:
        return 'CryptoBot'
    if method == PaymentMethod.TELEGRAM_STARS:
        return 'Telegram Stars'
    if method == PaymentMethod.KASSA_AI:
        return settings.get_kassa_ai_display_name()
    if method == PaymentMethod.RIOPAY:
        return settings.get_riopay_display_name()
    if method == PaymentMethod.FREEKASSA:
        return settings.get_freekassa_display_name()
    return method.value


def _status_info(
    record: PendingPayment,
    *,
    texts,
) -> tuple[str, str]:
    status = (record.status or '').lower()

    if record.is_paid:
        return '✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')

    if record.method == PaymentMethod.PAL24:
        mapping = {
            'new': ('⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')),
            'process': ('⌛', texts.t('ADMIN_PAYMENT_STATUS_PROCESSING', '处理中')),
            'success': ('✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')),
            'fail': ('❌', texts.t('ADMIN_PAYMENT_STATUS_FAILED', '失败')),
            'canceled': ('❌', texts.t('ADMIN_PAYMENT_STATUS_CANCELED', '已取消')),
            'cancel': ('❌', texts.t('ADMIN_PAYMENT_STATUS_CANCELED', '已取消')),
        }
        return mapping.get(status, ('❓', texts.t('ADMIN_PAYMENT_STATUS_UNKNOWN', '未知状态')))

    if record.method == PaymentMethod.MULENPAY:
        mapping = {
            'created': ('⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')),
            'processing': ('⌛', texts.t('ADMIN_PAYMENT_STATUS_PROCESSING', '处理中')),
            'hold': ('🔒', texts.t('ADMIN_PAYMENT_STATUS_ON_HOLD', '已保留')),
            'success': ('✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')),
            'canceled': ('❌', texts.t('ADMIN_PAYMENT_STATUS_CANCELED', '已取消')),
            'cancel': ('❌', texts.t('ADMIN_PAYMENT_STATUS_CANCELED', '已取消')),
            'error': ('⚠️', texts.t('ADMIN_PAYMENT_STATUS_FAILED', '失败')),
        }
        return mapping.get(status, ('❓', texts.t('ADMIN_PAYMENT_STATUS_UNKNOWN', '未知状态')))

    if record.method == PaymentMethod.WATA:
        mapping = {
            'opened': ('⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')),
            'pending': ('⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')),
            'processing': ('⌛', texts.t('ADMIN_PAYMENT_STATUS_PROCESSING', '处理中')),
            'paid': ('✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')),
            'closed': ('✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')),
            'declined': ('❌', texts.t('ADMIN_PAYMENT_STATUS_FAILED', '失败')),
            'canceled': ('❌', texts.t('ADMIN_PAYMENT_STATUS_CANCELED', '已取消')),
            'expired': ('⌛', texts.t('ADMIN_PAYMENT_STATUS_EXPIRED', '已过期')),
        }
        return mapping.get(status, ('❓', texts.t('ADMIN_PAYMENT_STATUS_UNKNOWN', '未知状态')))

    if record.method == PaymentMethod.PLATEGA:
        mapping = {
            'pending': ('⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')),
            'inprogress': ('⌛', texts.t('ADMIN_PAYMENT_STATUS_PROCESSING', '处理中')),
            'confirmed': ('✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')),
            'failed': ('❌', texts.t('ADMIN_PAYMENT_STATUS_FAILED', '失败')),
            'canceled': ('❌', texts.t('ADMIN_PAYMENT_STATUS_CANCELED', '已取消')),
            'cancelled': ('❌', texts.t('ADMIN_PAYMENT_STATUS_CANCELED', '已取消')),
            'expired': ('⌛', texts.t('ADMIN_PAYMENT_STATUS_EXPIRED', '已过期')),
        }
        return mapping.get(status, ('❓', texts.t('ADMIN_PAYMENT_STATUS_UNKNOWN', '未知状态')))

    if record.method == PaymentMethod.HELEKET:
        if status in {'pending', 'created', 'waiting', 'check', 'processing'}:
            return '⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')
        if status in {'paid', 'paid_over'}:
            return '✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')
        if status in {'cancel', 'canceled', 'fail', 'failed', 'expired'}:
            return '❌', texts.t('ADMIN_PAYMENT_STATUS_CANCELED', '已取消')
        return '❓', texts.t('ADMIN_PAYMENT_STATUS_UNKNOWN', '未知状态')

    if record.method == PaymentMethod.YOOKASSA:
        mapping = {
            'pending': ('⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')),
            'waiting_for_capture': ('⌛', texts.t('ADMIN_PAYMENT_STATUS_PROCESSING', '处理中')),
            'succeeded': ('✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')),
            'canceled': ('❌', texts.t('ADMIN_PAYMENT_STATUS_CANCELED', '已取消')),
        }
        return mapping.get(status, ('❓', texts.t('ADMIN_PAYMENT_STATUS_UNKNOWN', '未知状态')))

    if record.method == PaymentMethod.CRYPTOBOT:
        mapping = {
            'active': ('⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')),
            'paid': ('✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')),
            'expired': ('⌛', texts.t('ADMIN_PAYMENT_STATUS_EXPIRED', '已过期')),
        }
        return mapping.get(status, ('❓', texts.t('ADMIN_PAYMENT_STATUS_UNKNOWN', '未知状态')))

    if record.method == PaymentMethod.TELEGRAM_STARS:
        if record.is_paid:
            return '✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')
        return '⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')

    if record.method == PaymentMethod.FREEKASSA:
        mapping = {
            'pending': ('⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')),
            'success': ('✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')),
            'paid': ('✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')),
            'canceled': ('❌', texts.t('ADMIN_PAYMENT_STATUS_CANCELED', '已取消')),
            'error': ('❌', texts.t('ADMIN_PAYMENT_STATUS_FAILED', '失败')),
        }
        return mapping.get(status, ('❓', texts.t('ADMIN_PAYMENT_STATUS_UNKNOWN', '未知状态')))

    if record.method == PaymentMethod.KASSA_AI:
        mapping = {
            'pending': ('⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')),
            'created': ('⏳', texts.t('ADMIN_PAYMENT_STATUS_PENDING', '等待付款')),
            'processing': ('⌛', texts.t('ADMIN_PAYMENT_STATUS_PROCESSING', '处理中')),
            'success': ('✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')),
            'paid': ('✅', texts.t('ADMIN_PAYMENT_STATUS_PAID', '已支付')),
            'canceled': ('❌', texts.t('ADMIN_PAYMENT_STATUS_CANCELED', '已取消')),
            'error': ('❌', texts.t('ADMIN_PAYMENT_STATUS_FAILED', '失败')),
        }
        return mapping.get(status, ('❓', texts.t('ADMIN_PAYMENT_STATUS_UNKNOWN', '未知状态')))

    return '❓', texts.t('ADMIN_PAYMENT_STATUS_UNKNOWN', '未知状态')


def _is_checkable(record: PendingPayment) -> bool:
    if record.method not in SUPPORTED_MANUAL_CHECK_METHODS:
        return False
    if not record.is_recent():
        return False
    status = (record.status or '').lower()
    if record.method == PaymentMethod.PAL24:
        return status in {'new', 'process'}
    if record.method == PaymentMethod.MULENPAY:
        return status in {'created', 'processing', 'hold'}
    if record.method == PaymentMethod.WATA:
        return status in {'opened', 'pending', 'processing', 'inprogress', 'in_progress'}
    if record.method == PaymentMethod.PLATEGA:
        return status in {'pending', 'inprogress', 'in_progress'}
    if record.method == PaymentMethod.HELEKET:
        return status not in {'paid', 'paid_over', 'cancel', 'canceled', 'fail', 'failed', 'expired'}
    if record.method == PaymentMethod.YOOKASSA:
        return status in {'pending', 'waiting_for_capture'}
    if record.method == PaymentMethod.CRYPTOBOT:
        return status == 'active'
    if record.method == PaymentMethod.FREEKASSA:
        return status in {'pending', 'created', ''}
    if record.method == PaymentMethod.KASSA_AI:
        return status in {'pending', 'created', 'processing', ''}
    return False


def _record_display_number(record: PendingPayment) -> str:
    if record.identifier:
        return str(record.identifier)
    return str(record.local_id)


def _build_list_keyboard(
    records: list[PendingPayment],
    *,
    page: int,
    total_pages: int,
    language: str,
    has_checkable: bool = False,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    texts = get_texts(language)

    for record in records:
        number = _record_display_number(record)
        details_template = texts.t('ADMIN_PAYMENTS_ITEM_DETAILS', '📄№{number}')
        try:
            button_text = details_template.format(number=number)
        except Exception:  # pragma: no cover - fallback for broken localization
            button_text = f'📄 {number}'
        buttons.append(
            [
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f'admin_payment_{record.method.value}_{record.local_id}',
                )
            ]
        )

    # Кнопка "Проверить все" если есть что проверять
    if has_checkable:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=texts.t('ADMIN_PAYMENTS_CHECK_ALL', '🔄 检查一切'),
                    callback_data='admin_payments_check_all',
                )
            ]
        )

    # Кнопка экспорта если есть платежи
    if records:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=texts.t('ADMIN_PAYMENTS_EXPORT', '📥 上传至文件'),
                    callback_data='admin_payments_export',
                )
            ]
        )

    if total_pages > 1:
        navigation_row: list[InlineKeyboardButton] = []
        if page > 1:
            navigation_row.append(
                InlineKeyboardButton(
                    text='⬅️',
                    callback_data=f'admin_payments_page_{page - 1}',
                )
            )

        navigation_row.append(
            InlineKeyboardButton(
                text=f'{page}/{total_pages}',
                callback_data='admin_payments_page_current',
            )
        )

        if page < total_pages:
            navigation_row.append(
                InlineKeyboardButton(
                    text='➡️',
                    callback_data=f'admin_payments_page_{page + 1}',
                )
            )

        buttons.append(navigation_row)

    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_detail_keyboard(
    record: PendingPayment,
    *,
    language: str,
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    rows: list[list[InlineKeyboardButton]] = []

    payment = record.payment
    payment_url = getattr(payment, 'payment_url', None)
    if record.method == PaymentMethod.PAL24:
        payment_url = payment.link_url or payment.link_page_url or payment_url
    elif record.method == PaymentMethod.WATA:
        payment_url = payment.url or payment_url
    elif record.method == PaymentMethod.YOOKASSA:
        payment_url = getattr(payment, 'confirmation_url', None) or payment_url
    elif record.method == PaymentMethod.CRYPTOBOT:
        payment_url = (
            payment.bot_invoice_url or payment.mini_app_invoice_url or payment.web_app_invoice_url or payment_url
        )

    if payment_url:
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.t('ADMIN_PAYMENT_OPEN_LINK', '🔗打开链接'),
                    url=payment_url,
                )
            ]
        )

    if _is_checkable(record):
        rows.append(
            [
                InlineKeyboardButton(
                    text=texts.t('ADMIN_PAYMENT_CHECK_BUTTON', '🔁检查状态'),
                    callback_data=f'admin_payment_check_{record.method.value}_{record.local_id}',
                )
            ]
        )

    rows.append([InlineKeyboardButton(text=texts.BACK, callback_data='admin_payments')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_user_line(user: User) -> str:
    username = format_username(user.username, user.telegram_id, user.full_name)
    user_id_display = user.telegram_id or user.email or f'#{user.id}'
    return f'👤 {html.escape(username)} (<code>{user_id_display}</code>)'


def _build_record_lines(
    record: PendingPayment,
    *,
    index: int,
    texts,
    language: str,
) -> list[str]:
    amount = settings.format_price(record.amount_kopeks)
    if record.method == PaymentMethod.CRYPTOBOT:
        crypto_amount = getattr(record.payment, 'amount', None)
        crypto_asset = getattr(record.payment, 'asset', None)
        if crypto_amount and crypto_asset:
            amount = f'{crypto_amount} {crypto_asset}'
    method_name = _method_display(record.method)
    emoji, status_text = _status_info(record, texts=texts)
    created = format_datetime(record.created_at)
    age = format_time_ago(record.created_at, language)
    identifier = html.escape(str(record.identifier)) if record.identifier else ''
    display_number = html.escape(_record_display_number(record))

    lines = [
        f'{index}. <b>{html.escape(method_name)}</b> — {amount}',
        f'   {emoji} {status_text}',
        f'   🕒 {created} ({age})',
        _format_user_line(record.user),
    ]

    if identifier:
        lines.append(f'   🆔 ID: <code>{identifier}</code>')
    else:
        lines.append(f'   🆔 ID: <code>{display_number}</code>')

    return lines


def _build_payment_details_text(record: PendingPayment, *, texts, language: str) -> str:
    method_name = _method_display(record.method)
    emoji, status_text = _status_info(record, texts=texts)
    amount = settings.format_price(record.amount_kopeks)
    if record.method == PaymentMethod.CRYPTOBOT:
        crypto_amount = getattr(record.payment, 'amount', None)
        crypto_asset = getattr(record.payment, 'asset', None)
        if crypto_amount and crypto_asset:
            amount = f'{crypto_amount} {crypto_asset}'
    created = format_datetime(record.created_at)
    age = format_time_ago(record.created_at, language)
    raw_identifier = record.identifier or record.local_id
    identifier = html.escape(str(raw_identifier)) if raw_identifier is not None else '—'
    lines = [
        texts.t('ADMIN_PAYMENT_DETAILS_TITLE', '💳<b>付款详情</b>'),
        '',
        f'<b>{html.escape(method_name)}</b>',
        f'{emoji} {status_text}',
        '',
        f'💰 {texts.t("ADMIN_PAYMENT_AMOUNT", '金额')}: {amount}',
        f'🕒 {texts.t("ADMIN_PAYMENT_CREATED", '创建时间')}: {created} ({age})',
        f'🆔 ID: <code>{identifier}</code>',
        _format_user_line(record.user),
    ]

    if record.expires_at:
        expires_at = format_datetime(record.expires_at)
        lines.append(f'⏳ {texts.t("ADMIN_PAYMENT_EXPIRES", '过期时间')}: {expires_at}')

    payment = record.payment

    if record.method == PaymentMethod.PAL24:
        if getattr(payment, 'payment_status', None):
            lines.append(
                f'💳 {texts.t("ADMIN_PAYMENT_GATEWAY_STATUS", '支付网关状态')}: '
                f'{html.escape(str(payment.payment_status))}'
            )
        if getattr(payment, 'payment_method', None):
            lines.append(
                f'🏦 {texts.t("ADMIN_PAYMENT_GATEWAY_METHOD", '支付方式')}: {html.escape(str(payment.payment_method))}'
            )
        if getattr(payment, 'balance_amount', None):
            lines.append(
                f'💱 {texts.t("ADMIN_PAYMENT_GATEWAY_AMOUNT", '支付网关金额')}: '
                f'{html.escape(str(payment.balance_amount))}'
            )
        if getattr(payment, 'payer_account', None):
            lines.append(
                f'👛 {texts.t("ADMIN_PAYMENT_GATEWAY_ACCOUNT", '付款人账户')}: '
                f'{html.escape(str(payment.payer_account))}'
            )

    if record.method == PaymentMethod.MULENPAY:
        if getattr(payment, 'mulen_payment_id', None):
            lines.append(
                f'🧾 {texts.t("ADMIN_PAYMENT_GATEWAY_ID", '支付网关ID')}: {html.escape(str(payment.mulen_payment_id))}'
            )

    if record.method == PaymentMethod.WATA:
        if getattr(payment, 'order_id', None):
            lines.append(
                f'🧾 {texts.t("ADMIN_PAYMENT_GATEWAY_ID", '支付网关ID')}: {html.escape(str(payment.order_id))}'
            )
        if getattr(payment, 'terminal_public_id', None):
            lines.append(f'🏦 Terminal: {html.escape(str(payment.terminal_public_id))}')

    if record.method == PaymentMethod.HELEKET:
        if getattr(payment, 'order_id', None):
            lines.append(
                f'🧾 {texts.t("ADMIN_PAYMENT_GATEWAY_ID", '支付网关ID')}: {html.escape(str(payment.order_id))}'
            )
        if getattr(payment, 'payer_amount', None) and getattr(payment, 'payer_currency', None):
            lines.append(
                f'🪙 {texts.t("ADMIN_PAYMENT_PAYER_AMOUNT", '已支付')}: '
                f'{html.escape(str(payment.payer_amount))} {html.escape(str(payment.payer_currency))}'
            )

    if record.method == PaymentMethod.YOOKASSA:
        if getattr(payment, 'payment_method_type', None):
            lines.append(
                f'💳 {texts.t("ADMIN_PAYMENT_GATEWAY_METHOD", '支付方式')}: '
                f'{html.escape(str(payment.payment_method_type))}'
            )
        if getattr(payment, 'confirmation_url', None):
            lines.append(texts.t('ADMIN_PAYMENT_HAS_LINK', '🔗付款链接在上方按钮中。'))

    if record.method == PaymentMethod.CRYPTOBOT:
        if getattr(payment, 'amount', None) and getattr(payment, 'asset', None):
            lines.append(
                f'🪙 {texts.t("ADMIN_PAYMENT_CRYPTO_AMOUNT", '加密货币金额')}: '
                f'{html.escape(str(payment.amount))} {html.escape(str(payment.asset))}'
            )
        if getattr(payment, 'bot_invoice_url', None) or getattr(payment, 'mini_app_invoice_url', None):
            lines.append(texts.t('ADMIN_PAYMENT_HAS_LINK', '🔗付款链接在上方按钮中。'))
        if getattr(payment, 'status', None):
            lines.append(
                f'📊 {texts.t("ADMIN_PAYMENT_GATEWAY_STATUS", '支付网关状态')}: {html.escape(str(payment.status))}'
            )

    if record.method == PaymentMethod.TELEGRAM_STARS:
        description = getattr(payment, 'description', '') or ''
        if description:
            lines.append(f'📝 {html.escape(description)}')
        if getattr(payment, 'external_id', None):
            lines.append(
                f'🧾 {texts.t("ADMIN_PAYMENT_GATEWAY_ID", '支付网关ID')}: {html.escape(str(payment.external_id))}'
            )

    if _is_checkable(record):
        lines.append('')
        lines.append(texts.t('ADMIN_PAYMENT_CHECK_HINT', 'ℹ️可以启动手动状态检查。'))

    return '\n'.join(lines)


def _parse_method_and_id(payload: str, *, prefix: str) -> tuple[PaymentMethod, int] | None:
    suffix = payload[len(prefix) :]
    try:
        method_str, identifier = suffix.rsplit('_', 1)
        method = PaymentMethod(method_str)
        payment_id = int(identifier)
        return method, payment_id
    except (ValueError, KeyError):
        return None


@admin_required
@error_handler
async def show_payments_overview(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    texts = get_texts(db_user.language)

    page = 1
    if callback.data.startswith('admin_payments_page_'):
        try:
            page = int(callback.data.split('_')[-1])
        except ValueError:
            page = 1

    records = await list_recent_pending_payments(db)
    total = len(records)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(page, 1)
    page = min(page, total_pages)

    start_index = (page - 1) * PAGE_SIZE
    page_records = records[start_index : start_index + PAGE_SIZE]

    header = texts.t('ADMIN_PAYMENTS_TITLE', '💳<b>充值检查</b>')
    description = texts.t(
        'ADMIN_PAYMENTS_DESCRIPTION',
        '过去24小时内创建并等待付款的充值账单列表。',
    )
    notice = texts.t(
        'ADMIN_PAYMENTS_NOTICE',
        '只能检查24小时内且状态为等待中的账单。',
    )

    lines = [header, '', description]

    # Проверяем есть ли платежи для массовой проверки
    checkable_records = [r for r in records if _is_checkable(r) and not r.is_paid]
    has_checkable = len(checkable_records) > 0

    if page_records:
        for idx, record in enumerate(page_records, start=start_index + 1):
            lines.extend(_build_record_lines(record, index=idx, texts=texts, language=db_user.language))
            lines.append('')
        lines.append(notice)
        if has_checkable:
            lines.append('')
            lines.append(
                texts.t('ADMIN_PAYMENTS_CHECKABLE_COUNT', '🔄 可检查：{count}').format(
                    count=len(checkable_records)
                )
            )
    else:
        empty_text = texts.t('ADMIN_PAYMENTS_EMPTY', '过去24小时内未找到等待中的充值账单。')
        lines.append('')
        lines.append(empty_text)

    keyboard = _build_list_keyboard(
        page_records,
        page=page,
        total_pages=total_pages,
        language=db_user.language,
        has_checkable=has_checkable,
    )

    await callback.message.edit_text(
        '\n'.join(line for line in lines if line is not None),
        parse_mode='HTML',
        reply_markup=keyboard,
    )
    await callback.answer()


async def _render_payment_details(
    callback: types.CallbackQuery,
    db_user: User,
    record: PendingPayment,
) -> None:
    texts = get_texts(db_user.language)
    text = _build_payment_details_text(record, texts=texts, language=db_user.language)
    keyboard = _build_detail_keyboard(record, language=db_user.language)
    await callback.message.edit_text(text, parse_mode='HTML', reply_markup=keyboard)


@admin_required
@error_handler
async def show_payment_details(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    parsed = _parse_method_and_id(callback.data, prefix='admin_payment_')
    if not parsed:
        await callback.answer('❌ Invalid payment reference', show_alert=True)
        return

    method, payment_id = parsed
    record = await get_payment_record(db, method, payment_id)
    if not record:
        await callback.answer('❌ 未找到支付记录', show_alert=True)
        return

    await _render_payment_details(callback, db_user, record)
    await callback.answer()


@admin_required
@error_handler
async def manual_check_payment(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    logger.info('manual_check_payment called', callback_data=callback.data)

    parsed = _parse_method_and_id(callback.data, prefix='admin_payment_check_')
    if not parsed:
        logger.warning('Failed to parse', callback_data=callback.data)
        await callback.answer('❌ Invalid payment reference', show_alert=True)
        return

    method, payment_id = parsed
    logger.info('Checking payment: method id', method=method, payment_id=payment_id)

    record = await get_payment_record(db, method, payment_id)
    texts = get_texts(db_user.language)

    if not record:
        logger.warning('Payment not found: method id', method=method, payment_id=payment_id)
        await callback.answer(texts.t('ADMIN_PAYMENT_NOT_FOUND', '未找到付款。'), show_alert=True)
        return

    logger.info('Record found: status is_paid', record_status=record.status, is_paid=record.is_paid)

    if not _is_checkable(record):
        logger.info('Payment not checkable: method status', method=method, record_status=record.status)
        await callback.answer(
            texts.t('ADMIN_PAYMENT_CHECK_NOT_AVAILABLE', '此账单不支持手动检查。'),
            show_alert=True,
        )
        return

    logger.info('Running manual check...')
    payment_service = PaymentService(callback.bot)
    updated = await run_manual_check(db, method, payment_id, payment_service)
    logger.info('Check result', updated=updated is not None)

    if not updated:
        await callback.answer(
            texts.t('ADMIN_PAYMENT_CHECK_FAILED', '未能更新付款状态。'),
            show_alert=True,
        )
        return

    await _render_payment_details(callback, db_user, updated)

    if updated.status != record.status or updated.is_paid != record.is_paid:
        emoji, status_text = _status_info(updated, texts=texts)
        message = texts.t(
            'ADMIN_PAYMENT_CHECK_SUCCESS',
            '状态已更新：{status}',
        ).format(status=f'{emoji} {status_text}')
    else:
        message = texts.t(
            'ADMIN_PAYMENT_CHECK_NO_CHANGES',
            '检查后状态未改变。',
        )

    await callback.answer(message, show_alert=True)


@admin_required
@error_handler
async def check_all_payments(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    """Массовая проверка всех ожидающих платежей."""
    logger.info('check_all_payments called')

    texts = get_texts(db_user.language)

    # Получаем все ожидающие платежи
    records = await list_recent_pending_payments(db)
    logger.info('Found total records', records_count=len(records))

    checkable_records = [r for r in records if _is_checkable(r) and not r.is_paid]
    logger.info('Found checkable records', checkable_records_count=len(checkable_records))

    if not checkable_records:
        await callback.answer(
            texts.t('ADMIN_PAYMENTS_NO_CHECKABLE', '无需检查付款'),
            show_alert=True,
        )
        return

    await callback.answer(
        texts.t('ADMIN_PAYMENTS_CHECKING_ALL', '🔄 正在检查 {count} 笔付款...').format(count=len(checkable_records)),
    )

    payment_service = PaymentService(callback.bot)
    checked = 0
    confirmed = 0
    failed = 0

    for record in checkable_records:
        try:
            logger.info('Checking payment', method=record.method.value, local_id=record.local_id)
            updated = await run_manual_check(db, record.method, record.local_id, payment_service)
            checked += 1
            logger.info('Check result: is_paid', is_paid=updated.is_paid if updated else None)
            if updated and updated.is_paid and not record.is_paid:
                confirmed += 1
        except Exception as e:
            logger.error('Check failed', method=record.method.value, local_id=record.local_id, error=e, exc_info=True)
            failed += 1

    logger.info('Check complete: checked confirmed failed', checked=checked, confirmed=confirmed, failed=failed)

    # Показываем результат
    result_lines = [
        texts.t('ADMIN_PAYMENTS_CHECK_ALL_RESULT', '🔄 <b>查看结果</b>'),
        '',
        texts.t('ADMIN_PAYMENTS_CHECK_ALL_CHECKED', '✅ 已检查：{count}').format(count=checked),
        texts.t('ADMIN_PAYMENTS_CHECK_ALL_CONFIRMED', '💰 已确认：{count}').format(count=confirmed),
    ]
    if failed:
        result_lines.append(texts.t('ADMIN_PAYMENTS_CHECK_ALL_FAILED', '❌ 错误：{count}').format(count=failed))

    # Перезагружаем список платежей
    records = await list_recent_pending_payments(db)
    total = len(records)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page_records = records[:PAGE_SIZE]
    checkable_records = [r for r in records if _is_checkable(r) and not r.is_paid]

    result_lines.append('')
    result_lines.append(texts.t('ADMIN_PAYMENTS_TITLE', '💳<b>充值检查</b>'))

    if page_records:
        result_lines.append('')
        for idx, record in enumerate(page_records, start=1):
            result_lines.extend(_build_record_lines(record, index=idx, texts=texts, language=db_user.language))
            result_lines.append('')

    keyboard = _build_list_keyboard(
        page_records,
        page=1,
        total_pages=total_pages,
        language=db_user.language,
        has_checkable=len(checkable_records) > 0,
    )

    logger.info('Updating message with results...')
    try:
        await callback.message.edit_text(
            '\n'.join(result_lines),
            parse_mode='HTML',
            reply_markup=keyboard,
        )
        logger.info('Message updated successfully')
    except Exception as e:
        logger.error('Failed to update message', e=e, exc_info=True)


@admin_required
@error_handler
async def export_payments(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    """Экспорт данных платежей в JSON файл."""
    import json

    from aiogram.types import BufferedInputFile

    texts = get_texts(db_user.language)

    records = await list_recent_pending_payments(db)

    if not records:
        await callback.answer(
            texts.t('ADMIN_PAYMENTS_EXPORT_EMPTY', '出口无需付款'),
            show_alert=True,
        )
        return

    # Формируем данные для экспорта
    export_data = []
    for record in records:
        payment = record.payment
        user = record.user

        payment_data = {
            'id': record.local_id,
            'method': record.method.value,
            'method_display': _method_display(record.method),
            'identifier': record.identifier,
            'amount_kopeks': record.amount_kopeks,
            'amount_rubles': record.amount_kopeks / 100,
            'status': record.status,
            'is_paid': record.is_paid,
            'created_at': record.created_at.isoformat() if record.created_at else None,
            'expires_at': record.expires_at.isoformat() if record.expires_at else None,
            'user': {
                'id': user.id,
                'telegram_id': user.telegram_id,
                'username': user.username,
                'full_name': user.full_name,
            },
        }

        # Добавляем специфичные поля в зависимости 起 метода
        if hasattr(payment, 'order_id'):
            payment_data['order_id'] = payment.order_id
        if hasattr(payment, 'payment_url'):
            payment_data['payment_url'] = payment.payment_url
        if hasattr(payment, 'callback_payload'):
            payment_data['callback_payload'] = payment.callback_payload

        export_data.append(payment_data)

    # Создаём JSON файл
    json_content = json.dumps(export_data, ensure_ascii=False, indent=2, default=str)
    file_bytes = json_content.encode('utf-8')

    # Отправляем файл
    filename = f'payments_export_{datetime.now(UTC).strftime("%Y%m%d_%H%M%S")}.json'

    await callback.message.answer_document(
        document=BufferedInputFile(file_bytes, filename=filename),
        caption=texts.t(
            'ADMIN_PAYMENTS_EXPORT_CAPTION',
            '📥 导出付款\n\n📊 总条目：{count}\n💰 已付款：{paid}\n⏳ 待处理：{pending}',
        ).format(
            count=len(export_data),
            paid=sum(1 for r in export_data if r['is_paid']),
            pending=sum(1 for r in export_data if not r['is_paid']),
        ),
    )

    await callback.answer(texts.t('ADMIN_PAYMENTS_EXPORT_SUCCESS', '✅ 文件已发送'))


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(check_all_payments, F.data == 'admin_payments_check_all')
    dp.callback_query.register(export_payments, F.data == 'admin_payments_export')
    dp.callback_query.register(manual_check_payment, F.data.startswith('admin_payment_check_'))
    dp.callback_query.register(
        show_payment_details,
        F.data.startswith('admin_payment_') & ~F.data.startswith('admin_payment_check_'),
    )
    dp.callback_query.register(show_payments_overview, F.data.startswith('admin_payments_page_'))
    dp.callback_query.register(show_payments_overview, F.data == 'admin_payments')

