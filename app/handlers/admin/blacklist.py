"""
Обработчики админ-панели для управления черным списком
"""

import html

import structlog
from aiogram import types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from app.database.models import User
from app.services.blacklist_service import blacklist_service
from app.states import BlacklistStates
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


@admin_required
@error_handler
async def show_blacklist_settings(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    """
    Показывает настройки черного списка
    """
    logger.info('调用用户的 show_blacklist_settings 处理程序', from_user_id=callback.from_user.id)

    is_enabled = blacklist_service.is_blacklist_check_enabled()
    github_url = blacklist_service.get_blacklist_github_url()
    blacklist_count = len(await blacklist_service.get_all_blacklisted_users())

    status_text = '✅ 已启用' if is_enabled else '❌ 已禁用'
    url_text = github_url or '未设置'

    text = f'🔐 <b>黑名单设置</b>\n\n状态：{status_text}\n黑名单网址：<code>{url_text}</code>\n条目数：{blacklist_count}\n\n行动：'

    keyboard = [
        [
            types.InlineKeyboardButton(
                text='🔄 更新列表' if is_enabled else '🔄 更新（已禁用）',
                callback_data='admin_blacklist_update',
            )
        ],
        [
            types.InlineKeyboardButton(
                text='📋 查看列表' if is_enabled else '📋 查看（已禁用）',
                callback_data='admin_blacklist_view',
            )
        ],
        [
            types.InlineKeyboardButton(
                text='✏️ GitHub URL' if not github_url else '✏️ 修改 URL', callback_data='admin_blacklist_set_url'
            )
        ],
        [
            types.InlineKeyboardButton(
                text='✅ 启用' if not is_enabled else '❌ 禁用', callback_data='admin_blacklist_toggle'
            )
        ],
        [types.InlineKeyboardButton(text='⬅️返回用户', callback_data='admin_users')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def toggle_blacklist(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    """
    Переключает статус проверки черного списка
    """
    # Текущая реализация использует настройки из .env
    # Для полной реализации нужно будет создать сервис настроек
    is_enabled = blacklist_service.is_blacklist_check_enabled()

    # В реальной реализации нужно будет изменить настройку в базе данных
    # или в системе настроек, но сейчас просто покажем статус
    new_status = not is_enabled
    status_text = '已启用' if new_status else '已禁用'

    await callback.message.edit_text(
        f'黑名单检查状态：{status_text}\n\n要更改黑名单检查状态，请更改值\n文件 <code>.env</code> 中的 <code>BLACKLIST_CHECK_ENABLED</code>',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='🔄 更新状态', callback_data='admin_blacklist_settings')],
                [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_blacklist_settings')],
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def update_blacklist(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    """
    Обновляет черный список из GitHub
    """
    success, message = await blacklist_service.force_update_blacklist()

    if success:
        await callback.message.edit_text(
            f'✅ {message}',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='📋 查看列表', callback_data='admin_blacklist_view')],
                    [types.InlineKeyboardButton(text='🔄 手动更新', callback_data='admin_blacklist_update')],
                    [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_blacklist_settings')],
                ]
            ),
        )
    else:
        await callback.message.edit_text(
            f'❌更新错误：{message}',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🔄 重试', callback_data='admin_blacklist_update')],
                    [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_blacklist_settings')],
                ]
            ),
        )
    await callback.answer()


@admin_required
@error_handler
async def show_blacklist_users(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    """
    Показывает список пользователей в черном списке
    """
    blacklist_users = await blacklist_service.get_all_blacklisted_users()

    if not blacklist_users:
        text = '黑名单为空'
    else:
        text = f'🔐 <b>黑名单（{len(blacklist_users)}条目）</b>'

        # Показываем первые 20 записей
        for i, (tg_id, username, reason) in enumerate(blacklist_users[:20], 1):
            text += f'{i}. <code>{tg_id}</code> {html.escape(username or "")} — {html.escape(reason or "")}\n'

        if len(blacklist_users) > 20:
            text += f'...以及更多 {len(blacklist_users) - 20} 条目'

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='🔄 刷新', callback_data='admin_blacklist_view')],
                [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_blacklist_settings')],
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def start_set_blacklist_url(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    """
    Начинает процесс установки URL к черному списку
    """
    current_url = blacklist_service.get_blacklist_github_url() or '未设置'

    await callback.message.edit_text(
        f'在GitHub上输入黑名单文件的新URL\n\n当前网址：{current_url}\n\n示例：https://raw.githubusercontent.com/username/repository/main/blacklist.txt\n\n要取消，请使用命令 /cancel',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_blacklist_settings')]]
        ),
    )

    await state.set_state(BlacklistStates.waiting_for_blacklist_url)
    await callback.answer()


@admin_required
@error_handler
async def process_blacklist_url(message: types.Message, db_user: User, state: FSMContext):
    """
    Обрабатывает введенный URL к черному списку
    """
    # Обрабатываем сообщение только если б起 ожидает ввод URL
    if await state.get_state() != BlacklistStates.waiting_for_blacklist_url.state:
        return

    url = message.text.strip()

    # В реальной реализации нужно сохранить URL в систему настроек
    # В текущей реализации просто выводим сообщение
    if url.lower() in ['/cancel', 'отмена', 'cancel']:
        await message.answer(
            'URL 设置已取消',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text='🔐黑名单设置', callback_data='admin_blacklist_settings'
                        )
                    ]
                ]
            ),
        )
        await state.clear()
        return

    # Проверяем, что URL выглядит корректно
    if not url.startswith(('http://', 'https://')):
        await message.answer(
            '❌ 网址错误。 URL 必须以 http:// 或 https:// 开头',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        types.InlineKeyboardButton(
                            text='🔐黑名单设置', callback_data='admin_blacklist_settings'
                        )
                    ]
                ]
            ),
        )
        return

    # В реальной системе здесь нужно сохранить URL в базу данных настроек
    # или в систему конфигурации

    await message.answer(
        f'✅ 设置黑名单 URL：\n<code>{url}</code>\n\n要应用更改，请重新启动机器人或更改值\n文件 <code>.env</code> 中的 <code>BLACKLIST_GITHUB_URL</code>',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='🔄 刷新列表', callback_data='admin_blacklist_update')],
                [
                    types.InlineKeyboardButton(
                        text='🔐黑名单设置', callback_data='admin_blacklist_settings'
                    )
                ],
            ]
        ),
    )
    await state.clear()


def register_blacklist_handlers(dp):
    """
    Регистрация обработчиков черного списка
    """
    # Обработчик показа настроек черного списка
    # Эт起 обработчик нужно будет вызывать из меню пользователей или отдельно
    dp.callback_query.register(show_blacklist_settings, lambda c: c.data == 'admin_blacklist_settings')

    # Обработчики для взаимодействия с черным списком
    dp.callback_query.register(toggle_blacklist, lambda c: c.data == 'admin_blacklist_toggle')

    dp.callback_query.register(update_blacklist, lambda c: c.data == 'admin_blacklist_update')

    dp.callback_query.register(show_blacklist_users, lambda c: c.data == 'admin_blacklist_view')

    dp.callback_query.register(start_set_blacklist_url, lambda c: c.data == 'admin_blacklist_set_url')

    # Обработчик сообщений для установки URL (работает только в нужном состоянии)
    dp.message.register(process_blacklist_url, StateFilter(BlacklistStates.waiting_for_blacklist_url))


