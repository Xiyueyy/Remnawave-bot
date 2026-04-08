"""
Обработчики команд для массовой блокировки пользователей
"""

import structlog
from aiogram import types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.bulk_ban_service import bulk_ban_service
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


@admin_required
@error_handler
async def start_bulk_ban_process(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    """
    Начало процесса массовой блокировки пользователей
    """
    await callback.message.edit_text(
        '🛑 <b>大量封杀用户</b>\n\n输入电报列表 ID 进行阻止。\n\n<b>输入格式：</b>\n• 每条线一个 ID\n• 用逗号分隔\n• 穿过空间\n\n示例：\n<code>123456789\n987654321\n111222333</code>\n\n或者：\n<code>123456789, 987654321, 111222333</code>\n\n要取消使用命令 /cancel',
        parse_mode='HTML',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_users')]]
        ),
    )

    await state.set_state(AdminStates.waiting_for_bulk_ban_list)
    await callback.answer()


@admin_required
@error_handler
async def process_bulk_ban_list(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    """
    Обработка списка Telegram ID и выполнение массовой блокировки
    """
    if not message.text:
        await message.answer(
            '❌ 使用 Telegram 列表发送短信 ID',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='🔙 返回', callback_data='admin_users')]]
            ),
        )
        return

    input_text = message.text.strip()

    if not input_text:
        await message.answer(
            '❌ 输入正确的电报列表 ID',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='🔙 返回', callback_data='admin_users')]]
            ),
        )
        return

    # Парсим ID из текста
    try:
        telegram_ids = await bulk_ban_service.parse_telegram_ids_from_text(input_text)
    except Exception as e:
        logger.error('电报解析错误 ID', error=e)
        await message.answer(
            '❌ 处理列表 ID 时出错。检查输入格式。',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='🔙 返回', callback_data='admin_users')]]
            ),
        )
        return

    if not telegram_ids:
        await message.answer(
            '❌ 列表中未找到有效的电报 ID',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='🔙 返回', callback_data='admin_users')]]
            ),
        )
        return

    if len(telegram_ids) > 1000:  # Ограничение на количество ID за раз
        await message.answer(
            f'❌ 列表中的 ID 数量过多（{len(telegram_ids)}）。最大：1000',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='🔙 返回', callback_data='admin_users')]]
            ),
        )
        return

    # Выполняем массовую блокировку
    try:
        successfully_banned, not_found, error_ids = await bulk_ban_service.ban_users_by_telegram_ids(
            db=db,
            admin_user_id=db_user.id,
            telegram_ids=telegram_ids,
            reason='管理员批量屏蔽',
            bot=message.bot,
            notify_admin=True,
            admin_name=db_user.full_name,
        )

        # Подготавливаем сообщение с результатами
        result_text = '✅ <b>Массовая блокировка завершена</b>\n\n'
        result_text += '📊 <b>Результаты:</b>\n'
        result_text += f'✅ Успешно заблокировано: {successfully_banned}\n'
        result_text += f'❌ Не найдено: {not_found}\n'
        result_text += f'💥 Ошибок: {len(error_ids)}\n\n'
        result_text += f'📈 Всего обработано: {len(telegram_ids)}'

        if successfully_banned > 0:
            result_text += f'\n🎯 Процент успеха: {round((successfully_banned / len(telegram_ids)) * 100, 1)}%'

        # Добавляем информацию об ошибках, если есть
        if error_ids:
            result_text += '\n\n⚠️ <b>Telegram ID с ошибками:</b>\n'
            result_text += f'<code>{", ".join(map(str, error_ids[:10]))}</code>'  # Показываем первые 10
            if len(error_ids) > 10:
                result_text += f' и еще {len(error_ids) - 10}...'

        await message.answer(
            result_text,
            parse_mode='HTML',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='👥致用户', callback_data='admin_users')]]
            ),
        )

    except Exception as e:
        logger.error('执行质量锁定时出错', error=e)
        await message.answer(
            '❌ 执行批量阻止时发生错误',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='🔙 返回', callback_data='admin_users')]]
            ),
        )

    await state.clear()


def register_bulk_ban_handlers(dp):
    """
    Регистрация обработчиков команд для массовой блокировки
    """
    # Обработчик команды начала массовой блокировки
    dp.callback_query.register(start_bulk_ban_process, lambda c: c.data == 'admin_bulk_ban_start')

    # Обработчик текстового сообщения с ID для блокировки
    dp.message.register(process_bulk_ban_list, AdminStates.waiting_for_bulk_ban_list)
