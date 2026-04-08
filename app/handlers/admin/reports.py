import structlog
from aiogram import Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.keyboards.admin import (
    get_admin_report_result_keyboard,
    get_admin_reports_keyboard,
)
from app.localization.texts import get_texts
from app.services.reporting_service import (
    ReportingServiceError,
    ReportPeriod,
    reporting_service,
)
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


@admin_required
@error_handler
async def show_reports_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await callback.message.edit_text(
        '📊 <b>报告</b>\n\n选择将报告发送到管理主题的时间段。',
        reply_markup=get_admin_reports_keyboard(db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def send_daily_report(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await _send_report(callback, ReportPeriod.DAILY, db_user.language)


@admin_required
@error_handler
async def send_weekly_report(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await _send_report(callback, ReportPeriod.WEEKLY, db_user.language)


@admin_required
@error_handler
async def send_monthly_report(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await _send_report(callback, ReportPeriod.MONTHLY, db_user.language)


async def _send_report(
    callback: types.CallbackQuery,
    period: ReportPeriod,
    language: str,
) -> None:
    try:
        report_text = await reporting_service.send_report(period, send_to_topic=True)
    except ReportingServiceError as exc:
        logger.warning('发送报告失败', exc=exc)
        await callback.answer(str(exc), show_alert=True)
        return
    except Exception as exc:
        logger.error('发送报告时出现意外错误', exc=exc)
        await callback.answer('发送报告失败。请稍后重试。', show_alert=True)
        return

    await callback.message.answer(
        report_text,
        reply_markup=get_admin_report_result_keyboard(language),
    )
    await callback.answer('报告已发送至主题')


@admin_required
@error_handler
async def close_report_message(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    texts = get_texts(db_user.language)

    try:
        await callback.message.delete()
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.warning('无法关闭报告消息', exc=exc)
        await callback.answer(texts.t('REPORT_CLOSE_ERROR', '❌关闭报告失败。'), show_alert=True)
        return

    await callback.answer(texts.t('REPORT_CLOSED', '✅报告已关闭。'))


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(show_reports_menu, F.data == 'admin_reports')
    dp.callback_query.register(send_daily_report, F.data == 'admin_reports_daily')
    dp.callback_query.register(send_weekly_report, F.data == 'admin_reports_weekly')
    dp.callback_query.register(send_monthly_report, F.data == 'admin_reports_monthly')
    dp.callback_query.register(close_report_message, F.data == 'admin_close_report')
