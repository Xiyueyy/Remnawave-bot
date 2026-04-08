import html

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.keyboards.admin import get_admin_main_keyboard, get_maintenance_keyboard
from app.localization.texts import get_texts
from app.services.maintenance_service import maintenance_service
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


class MaintenanceStates(StatesGroup):
    waiting_for_reason = State()
    waiting_for_notification_message = State()


@admin_required
@error_handler
async def show_maintenance_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    get_texts(db_user.language)

    status_info = maintenance_service.get_status_info()

    try:
        from app.services.remnawave_service import RemnaWaveService

        rw_service = RemnaWaveService()
        panel_status = await rw_service.get_panel_status_summary()
    except Exception as e:
        logger.error('获取面板状态时出错', error=e)
        panel_status = {'description': '❓ 检查失败', 'has_issues': True}

    status_emoji = '🔧' if status_info['is_active'] else '✅'
    status_text = '已开启' if status_info['is_active'] else '已关闭'

    api_emoji = '✅' if status_info['api_status'] else '❌'
    api_text = '可用' if status_info['api_status'] else '不可用'

    monitoring_emoji = '🔄' if status_info['monitoring_active'] else '⏹️'
    monitoring_text = '运行中' if status_info['monitoring_active'] else '已停止'

    enabled_info = ''
    if status_info['is_active'] and status_info['enabled_at']:
        enabled_time = status_info['enabled_at'].strftime('%d.%m.%Y %H:%M:%S')
        enabled_info = f'\n📅 <b>开启时间：</b> {enabled_time}'
        if status_info['reason']:
            enabled_info += f'\n📝 <b>原因：</b> {status_info["reason"]}'

    last_check_info = ''
    if status_info['last_check']:
        last_check_time = status_info['last_check'].strftime('%H:%M:%S')
        last_check_info = f'\n🕐 <b>上次检查：</b> {last_check_time}'

    failures_info = ''
    if status_info['consecutive_failures'] > 0:
        failures_info = f'\n⚠️ <b>连续检查失败次数：</b> {status_info["consecutive_failures"]}'

    panel_info = f'\n🌐 <b>Remnawave 面板：</b> {panel_status["description"]}'
    if panel_status.get('response_time'):
        panel_info += f'\n⚡ <b>响应时间：</b> {panel_status["response_time"]}秒'

    message_text = (
        f'🔧 <b>技术工作管理</b>\n\n'
        f'{status_emoji} <b>维护模式：</b> {status_text}\n'
        f'{api_emoji} <b>API Remnawave：</b> {api_text}\n'
        f'{monitoring_emoji} <b>监控：</b> {monitoring_text}\n'
        f'🛠️ <b>监控自动启动：</b> {status_info["auto_start_monitoring"]}\n'
        f'⏱️ <b>检查间隔：</b> {status_info["monitoring_check_interval"]}秒\n'
        f'🤖 <b>自动开启：</b> {status_info["auto_enable"]}\n'
        f'{enabled_info}\n'
        f'{last_check_info}\n'
        f'{failures_info}\n'
        f'{panel_info}\n\n'
        f'ℹ️ <i>在维护模式下，普通用户无法使用机器人。管理员拥有完全访问权限。</i>'
    )

    await callback.message.edit_text(
        message_text,
        reply_markup=get_maintenance_keyboard(
            db_user.language,
            status_info['is_active'],
            status_info['monitoring_active'],
            panel_status.get('has_issues', False),
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_maintenance_mode(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    is_active = maintenance_service.is_maintenance_active()

    if is_active:
        success = await maintenance_service.disable_maintenance()
        if success:
            await callback.answer('维护模式已禁用', show_alert=True)
        else:
            await callback.answer('关闭维护模式时出错', show_alert=True)
    else:
        await state.set_state(MaintenanceStates.waiting_for_reason)
        await callback.message.edit_text(
            '🔧 <b>启用维护模式</b>\n\n输入包含技术工作的原因或发送 /skip 跳过：',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data='maintenance_panel')]]
            ),
        )

    await callback.answer()


@admin_required
@error_handler
async def process_maintenance_reason(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    current_state = await state.get_state()

    if current_state != MaintenanceStates.waiting_for_reason:
        return

    reason = None
    if message.text and message.text != '/skip':
        reason = message.text[:200]

    success = await maintenance_service.enable_maintenance(reason=reason, auto=False)

    if success:
        response_text = '维护模式已启用'
        if reason:
            response_text += f'\n原因：{html.escape(reason)}'
    else:
        response_text = '启用维护模式失败'

    await message.answer(response_text)
    await state.clear()

    maintenance_service.get_status_info()
    await message.answer(
        '返回维护控制面板：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='🔧 维护面板', callback_data='maintenance_panel')]]
        ),
    )


@admin_required
@error_handler
async def toggle_monitoring(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    status_info = maintenance_service.get_status_info()

    if status_info['monitoring_active']:
        success = await maintenance_service.stop_monitoring()
        message = '监控已停止' if success else '停止监控失败'
    else:
        success = await maintenance_service.start_monitoring()
        message = '监控已启动' if success else '启动监控失败'

    await callback.answer(message, show_alert=True)

    await show_maintenance_panel(callback, db_user, db, None)


@admin_required
@error_handler
async def force_api_check(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.answer('API检查...', show_alert=False)

    check_result = await maintenance_service.force_api_check()

    if check_result['success']:
        status_text = '可用' if check_result['api_available'] else '不可用'
        message = f"API {status_text}\n响应时间：{check_result['response_time']}s"
    else:
        message = f"检查错误：{check_result.get('error', '未知错误')}"

    await callback.message.answer(message)

    await show_maintenance_panel(callback, db_user, db, None)


@admin_required
@error_handler
async def check_panel_status(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.answer('检查面板状态...', show_alert=False)

    try:
        from app.services.remnawave_service import RemnaWaveService

        rw_service = RemnaWaveService()

        status_data = await rw_service.check_panel_health()

        status_text = {
            'online': '🟢 面板运行正常',
            'offline': '🔴 面板不可用',
            'degraded': '🟡 面板运行异常',
        }.get(status_data['status'], '❓ 状态未知')

        message_parts = [
            '🌐 <b>Remnawave 面板状态</b>\n',
            f'{status_text}',
            f'⚡ 响应时间：{status_data.get("response_time", 0)}s',
            f'👥 在线用户：{status_data.get("users_online", 0)}',
            f'🖥️ 在线节点：{status_data.get("nodes_online", 0)}/{status_data.get("total_nodes", 0)}',
        ]

        attempts_used = status_data.get('attempts_used')
        if attempts_used:
            message_parts.append(f'🔁 验证尝试：{attempts_used}')

        if status_data.get('api_error'):
            message_parts.append(f"❌ 错误：{status_data['api_error'][:100]}")

        message = '\n'.join(message_parts)

        await callback.message.answer(message, parse_mode='HTML')

    except Exception as e:
        await callback.message.answer(f'❌ 状态检查错误：{e!s}')


@admin_required
@error_handler
async def send_manual_notification(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    await state.set_state(MaintenanceStates.waiting_for_notification_message)

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text='🟢 在线', callback_data='manual_notify_online'),
                types.InlineKeyboardButton(text='🔴离线', callback_data='manual_notify_offline'),
            ],
            [
                types.InlineKeyboardButton(text='🟡 问题', callback_data='manual_notify_degraded'),
                types.InlineKeyboardButton(text='🔧服务', callback_data='manual_notify_maintenance'),
            ],
            [types.InlineKeyboardButton(text='❌ 取消', callback_data='maintenance_panel')],
        ]
    )

    await callback.message.edit_text(
        '📢 <b>手动发送通知</b>\n\n选择通知状态：', reply_markup=keyboard
    )


@admin_required
@error_handler
async def handle_manual_notification(callback: types.CallbackQuery, db_user: User, db: AsyncSession, state: FSMContext):
    status_map = {
        'manual_notify_online': 'online',
        'manual_notify_offline': 'offline',
        'manual_notify_degraded': 'degraded',
        'manual_notify_maintenance': 'maintenance',
    }

    status = status_map.get(callback.data)
    if not status:
        await callback.answer('状态未知')
        return

    await state.update_data(notification_status=status)

    status_names = {
        'online': '🟢 在线',
        'offline': '🔴 离线',
        'degraded': '🟡 异常',
        'maintenance': '🔧 维护中',
    }

    await callback.message.edit_text(
        f'📢 <b>发送通知：{status_names[status]}</b>\n\n输入通知消息或发送 /skip 发送，无需附加文本：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data='maintenance_panel')]]
        ),
    )


@admin_required
@error_handler
async def process_notification_message(message: types.Message, db_user: User, db: AsyncSession, state: FSMContext):
    current_state = await state.get_state()

    if current_state != MaintenanceStates.waiting_for_notification_message:
        return

    data = await state.get_data()
    status = data.get('notification_status')

    if not status:
        await message.answer('错误：未选择状态')
        await state.clear()
        return

    notification_message = ''
    if message.text and message.text != '/skip':
        notification_message = message.text[:300]

    try:
        from app.services.remnawave_service import RemnaWaveService

        rw_service = RemnaWaveService()

        success = await rw_service.send_manual_status_notification(message.bot, status, notification_message)

        if success:
            await message.answer('✅ 已发送通知')
        else:
            await message.answer('❌ 发送通知时出错')

    except Exception as e:
        logger.error('发送手动通知时出错', error=e)
        await message.answer(f'❌ 错误：{e!s}')

    await state.clear()

    await message.answer(
        '返回技术工作小组：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='🔧 维护面板', callback_data='maintenance_panel')]]
        ),
    )


@admin_required
@error_handler
async def back_to_admin_panel(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    texts = get_texts(db_user.language)

    await callback.message.edit_text(texts.ADMIN_PANEL, reply_markup=get_admin_main_keyboard(db_user.language))
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_maintenance_panel, F.data == 'maintenance_panel')

    dp.callback_query.register(toggle_maintenance_mode, F.data == 'maintenance_toggle')

    dp.callback_query.register(toggle_monitoring, F.data == 'maintenance_monitoring')

    dp.callback_query.register(force_api_check, F.data == 'maintenance_check_api')

    dp.callback_query.register(check_panel_status, F.data == 'maintenance_check_panel')

    dp.callback_query.register(send_manual_notification, F.data == 'maintenance_manual_notify')

    dp.callback_query.register(handle_manual_notification, F.data.startswith('manual_notify_'))

    dp.callback_query.register(back_to_admin_panel, F.data == 'admin_panel')

    dp.message.register(process_maintenance_reason, MaintenanceStates.waiting_for_reason)

    dp.message.register(process_notification_message, MaintenanceStates.waiting_for_notification_message)


