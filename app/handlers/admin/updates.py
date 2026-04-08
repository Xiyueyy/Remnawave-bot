import structlog
from aiogram import Dispatcher, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.version_service import version_service
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


def get_updates_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text='🔄 检查更新', callback_data='admin_updates_check')],
        [InlineKeyboardButton(text='📋版本信息', callback_data='admin_updates_info')],
        [
            InlineKeyboardButton(
                text='🔗 打开存储库', url=f'https://github.com/{version_service.repo}/releases'
            )
        ],
        [InlineKeyboardButton(text='◀️ 返回', callback_data='admin_panel')],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_version_info_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text='🔄 刷新', callback_data='admin_updates_info')],
        [InlineKeyboardButton(text='◀️ 更新', callback_data='admin_updates')],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@admin_required
@error_handler
async def show_updates_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        version_info = await version_service.get_version_info()

        current_version = version_info['current_version']
        has_updates = version_info['has_updates']
        total_newer = version_info['total_newer']
        last_check = version_info['last_check']

        status_icon = '🆕' if has_updates else '✅'
        status_text = f'Доступно {total_newer} обновлений' if has_updates else 'Актуальная версия'

        last_check_text = ''
        if last_check:
            last_check_text = f'\n🕐 Последняя проверка: {last_check.strftime("%d.%m.%Y %H:%M")}'

        message = f'🔄 <b>更新系统</b>\n\n📦 <b>当前版本：</b> <code>{current_version}</code>\n{status_icon} <b>状态：</b> {status_text}\n\n🔗 <b>存储库：</b> {version_service.repo}{last_check_text}\n\nℹ️系统每小时自动检查更新并发送有关新版本的通知。'

        await callback.message.edit_text(
            message, reply_markup=get_updates_keyboard(db_user.language), parse_mode='HTML'
        )
        await callback.answer()

    except Exception as e:
        if 'message is not modified' in str(e).lower():
            logger.debug('📝 Сообщение не изменено в show_updates_menu')
            await callback.answer()
            return
        logger.error('Ошибка показа меню обновлений', error=e)
        await callback.answer('❌ 加载更新菜单时出错', show_alert=True)


@admin_required
@error_handler
async def check_updates(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.answer('🔄 正在检查更新...')

    try:
        has_updates, newer_releases = await version_service.check_for_updates(force=True)

        if not has_updates:
            message = f'✅ <b>未找到更新</b>\n\n📦 <b>当前版本：</b> <code>{version_service.current_version}</code>\n🎯 <b>状态：</b> 您已安装最新版本\n\n🔗 <b>存储库：</b> {version_service.repo}'

        else:
            updates_list = []
            for i, release in enumerate(newer_releases[:5]):
                icon = version_service.format_version_display(release).split()[0]
                updates_list.append(f'{i + 1}. {icon} <code>{release.tag_name}</code> • {release.formatted_date}')

            updates_text = '\n'.join(updates_list)
            more_text = f'\n\n📋 И еще {len(newer_releases) - 5} обновлений...' if len(newer_releases) > 5 else ''

            message = f'🆕 <b>发现更新</b>\n\n📦 <b>当前版本：</b> <code>{version_service.current_version}</code>\n🎯 <b>可用更新：</b> {len(newer_releases)}\n\n📋 <b>最新版本：</b>\n{updates_text}QQPH3QQQ\n\n🔗 <b>存储库：</b> {version_service.repo}'

        keyboard = get_updates_keyboard(db_user.language)

        if has_updates:
            keyboard.inline_keyboard.insert(
                -2, [InlineKeyboardButton(text='📋 有关版本的更多信息', callback_data='admin_updates_info')]
            )

        await callback.message.edit_text(message, reply_markup=keyboard, parse_mode='HTML')

    except Exception as e:
        if 'message is not modified' in str(e).lower():
            logger.debug('📝 Сообщение не изменено в check_updates')
            return
        logger.error('Ошибка проверки обновлений', error=e)
        await callback.message.edit_text(
            f'❌ <b>更新检查错误</b>\n\n无法联系服务器 GitHub。\n请稍后重试。\n\n📦 <b>当前版本：</b> <code>{version_service.current_version}</code>',
            reply_markup=get_updates_keyboard(db_user.language),
            parse_mode='HTML',
        )


@admin_required
@error_handler
async def show_version_info(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    await callback.answer('📋 正在加载有关版本的信息...')

    try:
        version_info = await version_service.get_version_info()

        current_version = version_info['current_version']
        current_release = version_info['current_release']
        newer_releases = version_info['newer_releases']
        has_updates = version_info['has_updates']
        last_check = version_info['last_check']

        current_info = '📦 <b>当前版本</b>'

        if current_release:
            current_info += f'🏷️ <b>版本：</b> <代码>{current_release.tag_name}</code>'
            current_info += f'📅 <b>发布日期：</b> {current_release.formatted_date}'
            if current_release.short_description:
                current_info += f'📝 <b>描述：</b>\n{current_release.short_description}'
        else:
            current_info += f'🏷️ <b>版本：</b> <代码>{current_version}</code>'
            current_info += 'ℹ️ <b>状态：</b> 发布信息不可用'

        message_parts = [current_info]

        if has_updates and newer_releases:
            updates_info = '🆕<b>已更新</b>'

            for i, release in enumerate(newer_releases):
                icon = '🔥' if i == 0 else '📦'
                if release.prerelease:
                    icon = '🧪'
                elif release.is_dev:
                    icon = '🔧'

                updates_info += f'{icon} <b>{release.tag_name}</b>\n'
                updates_info += f'   📅 {release.formatted_date}\n'
                if release.short_description:
                    updates_info += f'   📝 {release.short_description}\n'
                updates_info += '\n'

            message_parts.append(updates_info.rstrip())

        system_info = '🔧 <b>更新系统</b>'
        system_info += f'🔗 <b>存储库：</b> {version_service.repo}'
        system_info += f"⚡ <b>自动检查：</b> {('Включена' if version_service.enabled else 'Отключена')}"
        system_info += '🕐 <b>间隔：</b> 每小时'

        if last_check:
            system_info += f"🕐 <b>最后一次检查：</b> {last_check.strftime('%d.%m.%Y %H:%M')}"

        message_parts.append(system_info.rstrip())

        final_message = '\n'.join(message_parts)

        if len(final_message) > 4000:
            final_message = final_message[:3900] + '\n\n... (информация обрезана)'

        await callback.message.edit_text(
            final_message,
            reply_markup=get_version_info_keyboard(db_user.language),
            parse_mode='HTML',
            disable_web_page_preview=True,
        )

    except Exception as e:
        if 'message is not modified' in str(e).lower():
            logger.debug('📝 Сообщение не изменено в show_version_info')
            return
        logger.error('Ошибка получения информации о версиях', error=e)
        await callback.message.edit_text(
            f'❌ <b>加载错误</b>\n\n获取版本信息失败。\n\n📦 <b>当前版本：</b> <code>{version_service.current_version}</code>',
            reply_markup=get_version_info_keyboard(db_user.language),
            parse_mode='HTML',
        )


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_updates_menu, F.data == 'admin_updates')

    dp.callback_query.register(check_updates, F.data == 'admin_updates_check')

    dp.callback_query.register(show_version_info, F.data == 'admin_updates_info')
