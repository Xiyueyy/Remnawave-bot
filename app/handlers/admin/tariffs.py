"""Управление тарифами в админ-панели."""

import html

import structlog
from aiogram import Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.promo_group import get_promo_groups_with_counts
from app.database.crud.server_squad import get_all_server_squads
from app.database.crud.tariff import (
    create_tariff,
    delete_tariff,
    get_active_subscriptions_count_by_tariff_id,
    get_tariff_by_id,
    get_tariff_subscriptions_count,
    get_tariffs_with_subscriptions_count,
    update_tariff,
)
from app.database.models import Tariff, User
from app.localization.texts import get_texts
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler
from app.utils.formatting import format_period, format_price_kopeks, format_traffic


logger = structlog.get_logger(__name__)

ITEMS_PER_PAGE = 10


def _parse_period_prices(text: str) -> dict[str, int]:
    """
    Парсит строку с ценами периодов.
    Формат: "30:9900, 90:24900, 180:44900" или "30=9900; 90=24900"
    """
    prices = {}
    text = text.replace(';', ',').replace('=', ':')

    for part in text.split(','):
        part = part.strip()
        if not part:
            continue

        if ':' not in part:
            continue

        period_str, price_str = part.split(':', 1)
        try:
            period = int(period_str.strip())
            price = int(price_str.strip())
            if period > 0 and price >= 0:
                prices[str(period)] = price
        except ValueError:
            continue

    return prices


def _format_period_prices_display(prices: dict[str, int]) -> str:
    """Форматирует цены периодов для отображения."""
    if not prices:
        return '未指定'

    lines = []
    for period_str in sorted(prices.keys(), key=int):
        period = int(period_str)
        price = prices[period_str]
        lines.append(f'  • {format_period(period)}: {format_price_kopeks(price)}')

    return '\n'.join(lines)


def _format_period_prices_for_edit(prices: dict[str, int]) -> str:
    """Форматирует цены периодов для редактирования."""
    if not prices:
        return '30:9900, 90:24900, 180:44900'

    parts = []
    for period_str in sorted(prices.keys(), key=int):
        parts.append(f'{period_str}:{prices[period_str]}')

    return ', '.join(parts)


def get_tariffs_list_keyboard(
    tariffs: list[tuple[Tariff, int]],
    language: str,
    page: int = 0,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру списка тарифов."""
    texts = get_texts(language)
    buttons = []

    for tariff, subs_count in tariffs:
        status = '✅' if tariff.is_active else '❌'
        button_text = f'{status} {tariff.name} ({subs_count})'
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=f'admin_tariff_view:{tariff.id}')])

    # Пагинация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text='◀️', callback_data=f'admin_tariffs_page:{page - 1}'))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text='▶️', callback_data=f'admin_tariffs_page:{page + 1}'))
    if nav_buttons:
        buttons.append(nav_buttons)

    # Кнопка создания
    buttons.append([InlineKeyboardButton(text='➕ 创建套餐', callback_data='admin_tariff_create')])

    # Кнопка назад
    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariff_view_keyboard(
    tariff: Tariff,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру просмотра тарифа."""
    texts = get_texts(language)
    buttons = []

    # Редактирование полей
    buttons.append(
        [
            InlineKeyboardButton(text='✏️标题', callback_data=f'admin_tariff_edit_name:{tariff.id}'),
            InlineKeyboardButton(text='📝 描述', callback_data=f'admin_tariff_edit_desc:{tariff.id}'),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(text='📊 流量', callback_data=f'admin_tariff_edit_traffic:{tariff.id}'),
            InlineKeyboardButton(text='📱 设备', callback_data=f'admin_tariff_edit_devices:{tariff.id}'),
        ]
    )
    # Цены за периоды только для обычных тарифов (не суточных)
    is_daily = getattr(tariff, 'is_daily', False)
    if not is_daily:
        buttons.append(
            [
                InlineKeyboardButton(text='💰 价格', callback_data=f'admin_tariff_edit_prices:{tariff.id}'),
                InlineKeyboardButton(text='🎚️等级', callback_data=f'admin_tariff_edit_tier:{tariff.id}'),
            ]
        )
    else:
        buttons.append(
            [
                InlineKeyboardButton(text='🎚️等级', callback_data=f'admin_tariff_edit_tier:{tariff.id}'),
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                text='📱💰 每台设备的价格', callback_data=f'admin_tariff_edit_device_price:{tariff.id}'
            ),
            InlineKeyboardButton(
                text='📱🔒最大。设备', callback_data=f'admin_tariff_edit_max_devices:{tariff.id}'
            ),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(text='⏰ 试用日', callback_data=f'admin_tariff_edit_trial_days:{tariff.id}'),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text='📈 额外购买流量', callback_data=f'admin_tariff_edit_traffic_topup:{tariff.id}'
            ),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(text='🔄 流量重置', callback_data=f'admin_tariff_edit_reset_mode:{tariff.id}'),
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(text='🌐 服务器', callback_data=f'admin_tariff_edit_squads:{tariff.id}'),
            InlineKeyboardButton(text='👥 促销组', callback_data=f'admin_tariff_edit_promo:{tariff.id}'),
        ]
    )

    # Суточный режим - только для уже суточных тарифов показываем настройки
    # Новые тарифы делаются суточными только при создании
    if is_daily:
        buttons.append(
            [
                InlineKeyboardButton(
                    text='💰每日价格', callback_data=f'admin_tariff_edit_daily_price:{tariff.id}'
                ),
            ]
        )
        # Примечание: отключение суточного режима убрано - это необратимое решение при создании

    # Переключение триала
    if tariff.is_trial_available:
        buttons.append(
            [InlineKeyboardButton(text='🎁 ❌ 删除试用版', callback_data=f'admin_tariff_toggle_trial:{tariff.id}')]
        )
    else:
        buttons.append(
            [InlineKeyboardButton(text='🎁 试用一下', callback_data=f'admin_tariff_toggle_trial:{tariff.id}')]
        )

    # Переключение активности
    if tariff.is_active:
        buttons.append(
            [InlineKeyboardButton(text='❌ 停用', callback_data=f'admin_tariff_toggle:{tariff.id}')]
        )
    else:
        buttons.append([InlineKeyboardButton(text='✅ 激活', callback_data=f'admin_tariff_toggle:{tariff.id}')])

    # Удаление
    buttons.append([InlineKeyboardButton(text='🗑️ 删除', callback_data=f'admin_tariff_delete:{tariff.id}')])

    # Назад к списку
    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data='admin_tariffs')])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _format_traffic_reset_mode(mode: str | None) -> str:
    """Форматирует режим сброса трафика для отображения."""
    mode_labels = {
        'DAY': '📅 Ежедневно',
        'WEEK': '📆 Еженедельно',
        'MONTH': '🗓️ Ежемесячно',
        'MONTH_ROLLING': '🔄 Скользящий месяц',
        'NO_RESET': '🚫 Никогда',
    }
    if mode is None:
        return f'🌐 全局设置 ({settings.DEFAULT_TRAFFIC_RESET_STRATEGY})'
    return mode_labels.get(mode, f'⚠️ Неизвестно ({mode})')


def _format_traffic_topup_packages(tariff: Tariff) -> str:
    """Форматирует пакеты докупки трафика для отображения."""
    if not getattr(tariff, 'traffic_topup_enabled', False):
        return '❌ 残疾人'

    packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
    if not packages:
        return '✅ 已启用，但未配置包'

    lines = ['✅ 包含在内']
    for gb in sorted(packages.keys()):
        price = packages[gb]
        lines.append(f'  • {gb} ГБ: {format_price_kopeks(price)}')

    return '\n'.join(lines)


def format_tariff_info(tariff: Tariff, language: str, subs_count: int = 0) -> str:
    """Форматирует информацию о тарифе."""
    get_texts(language)

    status = '✅ Активен' if tariff.is_active else '❌ Неактивен'
    traffic = format_traffic(tariff.traffic_limit_gb)
    prices_display = _format_period_prices_display(tariff.period_prices or {})

    # Форматируем список серверов
    squads_list = tariff.allowed_squads or []
    squads_display = f'{len(squads_list)} серверов' if squads_list else 'Все серверы'

    # Форматируем промогруппы
    promo_groups = tariff.allowed_promo_groups or []
    if promo_groups:
        promo_display = ', '.join(pg.name for pg in promo_groups)
    else:
        promo_display = 'Доступен всем'

    trial_status = '✅ Да' if tariff.is_trial_available else '❌ Нет'

    # Форматируем дни триала
    trial_days = getattr(tariff, 'trial_duration_days', None)
    if trial_days:
        trial_days_display = f'{trial_days} дней'
    else:
        trial_days_display = f'По умолчанию ({settings.TRIAL_DURATION_DAYS} дней)'

    # Форматируем цену за устройство
    device_price = getattr(tariff, 'device_price_kopeks', None)
    if device_price is not None and device_price > 0:
        device_price_display = format_price_kopeks(device_price) + '/мес'
    else:
        device_price_display = 'Недоступно'

    # Форматируем макс. устройств
    max_devices = getattr(tariff, 'max_device_limit', None)
    if max_devices is not None and max_devices > 0:
        max_devices_display = str(max_devices)
    else:
        max_devices_display = '∞ (без лимита)'

    # Форматируем докупку трафика
    traffic_topup_display = _format_traffic_topup_packages(tariff)

    # Форматируем режим сброса трафика
    traffic_reset_mode = getattr(tariff, 'traffic_reset_mode', None)
    traffic_reset_display = _format_traffic_reset_mode(traffic_reset_mode)

    # Форматируем суточный тариф
    is_daily = getattr(tariff, 'is_daily', False)
    daily_price_kopeks = getattr(tariff, 'daily_price_kopeks', 0)

    # Формируем блок цен в зависимости от типа тарифа
    if is_daily:
        price_block = f'<b>💰 Суточная цена:</b> {format_price_kopeks(daily_price_kopeks)}/день'
        tariff_type = '🔄 Суточный'
    else:
        price_block = f'<b>Цены:</b>\n{prices_display}'
        tariff_type = '📅 Периодный'

    return f"📦 <b>套餐：{html.escape(tariff.name)}</b>\n\n{status} | {tariff_type}\n🎚️等级：{tariff.tier_level}\n📊 订单：{tariff.display_order}\n\n<b>参数：</b>\n• 流量：{traffic}\n• 设备：{tariff.device_limit}\n• 最大。设备：{max_devices_display}\n• 每次添加的价格。设备：{device_price_display}\n• 试用版：{trial_status}\n• 试用天数：{trial_days_display}\n\n<b>额外流量购买：</b>\n{traffic_topup_display}\n\n<b>流量重置：</b> {traffic_reset_display}\n\n{price_block}\n\n<b>服务器：</b> {squads_display}\n<b>促销组：</b> {promo_display}\n\n📊 订阅套餐：{subs_count}\n\n{(f'📝 {html.escape(tariff.description)}' if tariff.description else '')}"


@admin_required
@error_handler
async def show_tariffs_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает список тарифов."""
    await state.clear()
    texts = get_texts(db_user.language)

    # Проверяем режим продаж
    if not settings.is_tariffs_mode():
        await callback.message.edit_text(
            '⚠️ <b>套餐模式已禁用</b>\n\n要使用套餐，请设置：\n<code>SALES_MODE=套餐</code>\n\n当前模式：<code>经典</code>',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')]]
            ),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    tariffs_data = await get_tariffs_with_subscriptions_count(db, include_inactive=True)

    if not tariffs_data:
        await callback.message.edit_text(
            '📦 <b>套餐</b>\n\n套餐尚未制定。\n创建您的第一个套餐即可开始。',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text='➕ 创建套餐', callback_data='admin_tariff_create')],
                    [InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')],
                ]
            ),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    total_pages = (len(tariffs_data) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page_data = tariffs_data[:ITEMS_PER_PAGE]

    total_subs = sum(count for _, count in tariffs_data)
    active_count = sum(1 for t, _ in tariffs_data if t.is_active)

    await callback.message.edit_text(
        f'📦 <b>套餐</b>\n\n总计：{len(tariffs_data)}（活跃：{active_count}）\n套餐订阅：{total_subs}\n\n选择要查看和编辑的套餐：',
        reply_markup=get_tariffs_list_keyboard(page_data, db_user.language, 0, total_pages),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def show_tariffs_page(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Показывает страницу списка тарифов."""
    get_texts(db_user.language)
    page = int(callback.data.split(':')[1])

    tariffs_data = await get_tariffs_with_subscriptions_count(db, include_inactive=True)
    total_pages = (len(tariffs_data) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_data = tariffs_data[start_idx:end_idx]

    total_subs = sum(count for _, count in tariffs_data)
    active_count = sum(1 for t, _ in tariffs_data if t.is_active)

    await callback.message.edit_text(
        f'📦<b>套餐</b>（页面{page + 1}/QQQPH1QQQ）\n\n总计：{len(tariffs_data)}（活跃：{active_count}）\n套餐订阅：{total_subs}\n\n选择要查看和编辑的套餐：',
        reply_markup=get_tariffs_list_keyboard(page_data, db_user.language, page, total_pages),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def view_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Просмотр тарифа."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await callback.message.edit_text(
        format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает активность тарифа."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    tariff = await update_tariff(db, tariff, is_active=not tariff.is_active)
    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    status = 'активирован' if tariff.is_active else 'деактивирован'
    await callback.answer(f'套餐{status}', show_alert=True)

    await callback.message.edit_text(
        format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def toggle_trial_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает тариф как триальный."""
    from app.database.crud.tariff import clear_trial_tariff, set_trial_tariff

    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    if tariff.is_trial_available:
        # Снимаем флаг триала
        await clear_trial_tariff(db)
        await callback.answer('试验从套餐中删除', show_alert=True)
    else:
        # Устанавливаем этот тариф как триальный (снимает флаг с других)
        await set_trial_tariff(db, tariff_id)
        await callback.answer(f'套餐“{tariff.name}”设定为试行', show_alert=True)

    # Перезагружаем тариф
    tariff = await get_tariff_by_id(db, tariff_id)
    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await callback.message.edit_text(
        format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def toggle_daily_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает суточный режим тарифа."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    is_daily = getattr(tariff, 'is_daily', False)

    if is_daily:
        # Отключаем суточный режим
        tariff = await update_tariff(db, tariff, is_daily=False, daily_price_kopeks=0)
        await callback.answer('日常模式已禁用', show_alert=True)
    else:
        # Включаем суточный режим (с ценой по умолчанию)
        tariff = await update_tariff(db, tariff, is_daily=True, daily_price_kopeks=5000)  # 50 руб по умолчанию
        await callback.answer(
            '每日模式已启用。价格：50₽/天\n使用“💰每日价格”按钮设置价格', show_alert=True
        )

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await callback.message.edit_text(
        format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_daily_price(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование суточной цены."""
    texts = get_texts(db_user.language)

    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    current_price = getattr(tariff, 'daily_price_kopeks', 0)
    current_price / 100 if current_price else 0

    await state.set_state(AdminStates.editing_tariff_daily_price)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    await callback.message.edit_text(
        f'💰 <b>编辑每日价格</b>\n\n套餐：{html.escape(tariff.name)}\n目前价格：{format_price_kopeks(current_price)}/天\n\n输入每天的新价格（以卢布为单位）。\n示例：<代码>50</code> 或 <代码>99.90</code>',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_view:{tariff_id}')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_daily_price_input(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает ввод суточной цены (создание и редактирование)."""
    get_texts(db_user.language)
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    # Парсим цену
    try:
        price_rubles = float(message.text.strip().replace(',', '.'))
        if price_rubles <= 0:
            raise ValueError('Цена должна быть положительной')

        price_kopeks = int(price_rubles * 100)
    except ValueError:
        await message.answer(
            '❌ 价格不正确。输入一个正数。\n示例：<代码>50</code> 或 <代码>99.90</code>',
            parse_mode='HTML',
        )
        return

    # Проверяем - это создание или редактирование
    is_creating = data.get('tariff_is_daily') and not tariff_id

    if is_creating:
        # Создаем новый суточный тариф
        tariff = await create_tariff(
            db,
            name=data['tariff_name'],
            traffic_limit_gb=data['tariff_traffic'],
            device_limit=data['tariff_devices'],
            tier_level=data['tariff_tier'],
            period_prices={},
            is_active=True,
            is_daily=True,
            daily_price_kopeks=price_kopeks,
        )
        await state.clear()

        await message.answer(
            '✅ <b>Суточный тариф создан!</b>\n\n' + format_tariff_info(tariff, db_user.language, 0),
            reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
            parse_mode='HTML',
        )
    else:
        # Редактируем существующий тариф
        if not tariff_id:
            await state.clear()
            return

        tariff = await get_tariff_by_id(db, tariff_id)
        if not tariff:
            await message.answer('未找到套餐')
            await state.clear()
            return

        tariff = await update_tariff(db, tariff, daily_price_kopeks=price_kopeks)
        await state.clear()

        subs_count = await get_tariff_subscriptions_count(db, tariff_id)

        await message.answer(
            f'✅ Суточная цена установлена: {format_price_kopeks(price_kopeks)}/день\n\n'
            + format_tariff_info(tariff, db_user.language, subs_count),
            reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
            parse_mode='HTML',
        )


# ============ СОЗДАНИЕ ТАРИФА ============


@admin_required
@error_handler
async def start_create_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает создание тарифа."""
    texts = get_texts(db_user.language)

    await state.set_state(AdminStates.creating_tariff_name)
    await state.update_data(language=db_user.language)

    await callback.message.edit_text(
        '📦 <b>创建套餐</b>\n\n步骤1/6：输入套餐名称\n\n示例：<i>基本版</i>、<i>高级版</i>、<i>商务版</i>',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data='admin_tariffs')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_tariff_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает название тарифа."""
    texts = get_texts(db_user.language)
    name = message.text.strip()

    if len(name) < 2:
        await message.answer('名称必须至少有 2 个字符')
        return

    if len(name) > 50:
        await message.answer('标题不得超过 50 个字符')
        return

    await state.update_data(tariff_name=name)
    await state.set_state(AdminStates.creating_tariff_traffic)

    await message.answer(
        f'📦 <b>创建套餐</b>\n\n名称：<b>{name}</b>\n\n步骤2/6：在GB中输入流量限制\n\n输入 <code>0</code> 流量无限制\n示例：<i>100</i>、<i>500</i>、<i>0</i>',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data='admin_tariffs')]]
        ),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def process_tariff_traffic(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает лимит трафика."""
    texts = get_texts(db_user.language)

    try:
        traffic = int(message.text.strip())
        if traffic < 0:
            raise ValueError
    except ValueError:
        await message.answer('请输入有效数字（0个或更多）')
        return

    data = await state.get_data()
    await state.update_data(tariff_traffic=traffic)
    await state.set_state(AdminStates.creating_tariff_devices)

    traffic_display = format_traffic(traffic)

    await message.answer(
        f"📦 <b>创建套餐</b>\n\n名称：<b>{data['tariff_name']}</b>\n流量：<b>{traffic_display}</b>\n\n步骤 3/6：输入设备限制\n\n示例：<i>1</i>、<i>3</i>、<i>5</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data='admin_tariffs')]]
        ),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def process_tariff_devices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает лимит устройств."""
    texts = get_texts(db_user.language)

    try:
        devices = int(message.text.strip())
        if devices < 1:
            raise ValueError
    except ValueError:
        await message.answer('请输入有效数字（1个或更多）')
        return

    data = await state.get_data()
    await state.update_data(tariff_devices=devices)
    await state.set_state(AdminStates.creating_tariff_tier)

    traffic_display = format_traffic(data['tariff_traffic'])

    await message.answer(
        f"📦 <b>创建套餐</b>\n\n名称：<b>{data['tariff_name']}</b>\n流量：<b>{traffic_display}</b>\n设备：<b>{devices}</b>\n\n步骤 4/6：输入套餐级别 (1-10)\n\n水平仪用于视觉显示\n1 - 基本，10 - 最大\n示例：<i>1</i>、<i>2</i>、<i>3</i>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data='admin_tariffs')]]
        ),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def process_tariff_tier(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает уровень тарифа."""
    texts = get_texts(db_user.language)

    try:
        tier = int(message.text.strip())
        if tier < 1 or tier > 10:
            raise ValueError
    except ValueError:
        await message.answer('输入 1 到 10 之间的数字')
        return

    data = await state.get_data()
    await state.update_data(tariff_tier=tier)

    traffic_display = format_traffic(data['tariff_traffic'])

    # Шаг 5/6: Выбор типа тарифа
    await message.answer(
        f"📦 <b>创建套餐</b>\n\n名称：<b>{data['tariff_name']}</b>\n流量：<b>{traffic_display}</b>\n设备：<b>{data['tariff_devices']}</b>\n级别：<b>{tier}</b>\n\n步骤 5/6：选择套餐类型",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='📅 定期（月）', callback_data='tariff_type_periodic')],
                [InlineKeyboardButton(text='🔄 每日津贴（按天支付）', callback_data='tariff_type_daily')],
                [InlineKeyboardButton(text=texts.CANCEL, callback_data='admin_tariffs')],
            ]
        ),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def select_tariff_type_periodic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Выбирает периодный тип тарифа."""
    texts = get_texts(db_user.language)
    data = await state.get_data()

    await state.update_data(tariff_is_daily=False)
    await state.set_state(AdminStates.creating_tariff_prices)

    traffic_display = format_traffic(data['tariff_traffic'])

    await callback.message.edit_text(
        f"📦 <b>创建套餐</b>\n\n名称：<b>{data['tariff_name']}</b>\n流量：<b>{traffic_display}</b>\n设备：<b>{data['tariff_devices']}</b>\n级别：<b>{data['tariff_tier']}</b>\n类型：<b>📅定期</b>\n\n步骤 6/6：输入期间的价格\n\n格式：<code>天:price_in_kopecks</code>\n多个句点用逗号分隔\n\n示例：\n<代码>30:9900、90:24900、180:44900、360:79900</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data='admin_tariffs')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def select_tariff_type_daily(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Выбирает суточный тип тарифа."""
    from app.states import AdminStates

    texts = get_texts(db_user.language)
    data = await state.get_data()

    await state.update_data(tariff_is_daily=True)
    await state.set_state(AdminStates.editing_tariff_daily_price)

    traffic_display = format_traffic(data['tariff_traffic'])

    await callback.message.edit_text(
        f"📦 <b>创建每日套餐</b>\n\n名称：<b>{data['tariff_name']}</b>\n流量：<b>{traffic_display}</b>\n设备：<b>{data['tariff_devices']}</b>\n级别：<b>{data['tariff_tier']}</b>\n类型：<b>🔄每日津贴</b>\n\n步骤 6/6：输入卢布每日价格\n\n示例：<i>50</i>（50 ₽/天）、<i>99.90</i>（99.90 ₽/天）",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data='admin_tariffs')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_tariff_prices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает цены тарифа."""
    get_texts(db_user.language)

    prices = _parse_period_prices(message.text.strip())

    if not prices:
        await message.answer(
            '无法识别价格。\n\n格式：<code>天:price_in_kopecks</code>\n示例：<代码>30:9900, 90:24900</code>',
            parse_mode='HTML',
        )
        return

    data = await state.get_data()
    await state.update_data(tariff_prices=prices)

    format_traffic(data['tariff_traffic'])
    _format_period_prices_display(prices)

    # Создаем тариф
    tariff = await create_tariff(
        db,
        name=data['tariff_name'],
        traffic_limit_gb=data['tariff_traffic'],
        device_limit=data['tariff_devices'],
        tier_level=data['tariff_tier'],
        period_prices=prices,
        is_active=True,
    )

    await state.clear()

    subs_count = 0

    await message.answer(
        '✅ <b>Тариф создан!</b>\n\n' + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


# ============ РЕДАКТИРОВАНИЕ ТАРИФА ============


@admin_required
@error_handler
async def start_edit_tariff_name(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование названия тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_name)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    await callback.message.edit_text(
        f'✏️ <b>编辑标题</b>\n\n当前名称：<b>{html.escape(tariff.name)}</b>\n\n输入新名称：',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_view:{tariff_id}')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое название тарифа."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer('未找到套餐')
        await state.clear()
        return

    name = message.text.strip()
    if len(name) < 2 or len(name) > 50:
        await message.answer('名称必须为 2 到 50 个字符')
        return

    tariff = await update_tariff(db, tariff, name=name)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        '✅ Название изменено!\n\n' + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_tariff_description(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование описания тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_description)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    current_desc = tariff.description or 'Не задано'

    await callback.message.edit_text(
        f'📝 <b>编辑说明</b>\n\n当前描述：\n{current_desc}\n\n输入新描述（或删除 <code>-</code>）：',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_view:{tariff_id}')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_description(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое описание тарифа."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer('未找到套餐')
        await state.clear()
        return

    description = message.text.strip()
    if description == '-':
        description = None

    tariff = await update_tariff(db, tariff, description=description)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        '✅ Описание изменено!\n\n' + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_tariff_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование трафика тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_traffic)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    current_traffic = format_traffic(tariff.traffic_limit_gb)

    await callback.message.edit_text(
        f'📊 <b>编辑流量</b>\n\n当前限制：<b>{current_traffic}</b>\n\n在 GB 中输入新限制（0 = 无限制）：',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_view:{tariff_id}')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_traffic(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новый лимит трафика."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer('未找到套餐')
        await state.clear()
        return

    try:
        traffic = int(message.text.strip())
        if traffic < 0:
            raise ValueError
    except ValueError:
        await message.answer('请输入有效数字（0个或更多）')
        return

    tariff = await update_tariff(db, tariff, traffic_limit_gb=traffic)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        '✅ Трафик изменен!\n\n' + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_tariff_devices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование лимита устройств."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_devices)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    await callback.message.edit_text(
        f'📱 <b>编辑设备</b>\n\n当前限制：<b>{tariff.device_limit}</b>\n\n输入新的设备限制：',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_view:{tariff_id}')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_devices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новый лимит устройств."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer('未找到套餐')
        await state.clear()
        return

    try:
        devices = int(message.text.strip())
        if devices < 1:
            raise ValueError
    except ValueError:
        await message.answer('请输入有效数字（1个或更多）')
        return

    tariff = await update_tariff(db, tariff, device_limit=devices)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        '✅ Лимит устройств изменен!\n\n' + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_tariff_tier(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование уровня тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_tier)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    await callback.message.edit_text(
        f'🎚️ <b>编辑级别</b>\n\n当前等级：<b>{tariff.tier_level}</b>\n\n进入新级别（1-10）：',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_view:{tariff_id}')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_tier(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новый уровень тарифа."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer('未找到套餐')
        await state.clear()
        return

    try:
        tier = int(message.text.strip())
        if tier < 1 or tier > 10:
            raise ValueError
    except ValueError:
        await message.answer('输入 1 到 10 之间的数字')
        return

    tariff = await update_tariff(db, tariff, tier_level=tier)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        '✅ Уровень изменен!\n\n' + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


@admin_required
@error_handler
async def start_edit_tariff_prices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование цен тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_prices)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    current_prices = _format_period_prices_for_edit(tariff.period_prices or {})
    prices_display = _format_period_prices_display(tariff.period_prices or {})

    await callback.message.edit_text(
        f'💰 <b>编辑价格</b>\n\n目前价格：\n{prices_display}\n\n按以下格式输入新价格：\n<代码>{current_prices}</code>\n\n（天数：price_in_kopecks，以逗号分隔）',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_view:{tariff_id}')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_prices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новые цены тарифа."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer('未找到套餐')
        await state.clear()
        return

    prices = _parse_period_prices(message.text.strip())
    if not prices:
        await message.answer(
            '无法识别价格。\n格式：<code>天:价格</code>\n示例：<代码>30:9900, 90:24900</code>',
            parse_mode='HTML',
        )
        return

    tariff = await update_tariff(db, tariff, period_prices=prices)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        '✅ Цены изменены!\n\n' + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


# ============ РЕДАКТИРОВАНИЕ ЦЕНЫ ЗА УСТРОЙСТВО ============


@admin_required
@error_handler
async def start_edit_tariff_device_price(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование цены за устройство."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_device_price)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    device_price = getattr(tariff, 'device_price_kopeks', None)
    if device_price is not None and device_price > 0:
        current_price = format_price_kopeks(device_price) + '/мес'
    else:
        current_price = 'Недоступно (докупка устройств запрещена)'

    await callback.message.edit_text(
        f'📱💰 <b>编辑每台设备的价格</b>\n\n当前价格：<b>{current_price}</b>\n\n输入每月一台设备的价格（以戈比为单位）。\n\n• <code>0</code> 或 <code>-</code> - 无法额外购买设备\n• 例如：<code>5000</code> = 每台设备 50₽/月',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_view:{tariff_id}')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_device_price(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новую цену за устройство."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer('未找到套餐')
        await state.clear()
        return

    text = message.text.strip()

    if text == '-' or text == '0':
        device_price = None
    else:
        try:
            device_price = int(text)
            if device_price < 0:
                raise ValueError
        except ValueError:
            await message.answer(
                '请输入有效数字（0 或更多）。\n要禁用额外购买，请输入 <code>0</code> 或 <code>-</code>',
                parse_mode='HTML',
            )
            return

    tariff = await update_tariff(db, tariff, device_price_kopeks=device_price)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        '✅ Цена за устройство изменена!\n\n' + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


# ============ РЕДАКТИРОВАНИЕ МАКС. УСТРОЙСТВ ============


@admin_required
@error_handler
async def start_edit_tariff_max_devices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование макс. устройств."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_max_devices)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    max_devices = getattr(tariff, 'max_device_limit', None)
    if max_devices is not None and max_devices > 0:
        current_max = str(max_devices)
    else:
        current_max = '∞ (без лимита)'

    await callback.message.edit_text(
        f'📱🔒<b>编辑最大。设备</b>\n\n当前值：<b>{current_max}</b>\n基本设备数量：<b>{tariff.device_limit}</b>\n\n输入用户可以购买的最大设备数量。\n\n• <代码>0</code> 或 <代码>-</code> - 无限制\n• 例如：<code>5</code> = 每个套餐最多 5 台设备',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_view:{tariff_id}')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_max_devices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое макс. кол-во устройств."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer('未找到套餐')
        await state.clear()
        return

    text = message.text.strip()

    if text == '-' or text == '0':
        max_devices = None
    else:
        try:
            max_devices = int(text)
            if max_devices < 1:
                raise ValueError
        except ValueError:
            await message.answer(
                '请输入有效的数字（1 个或更多）。\n要取消限制，请输入 <code>0</code> 或 <code>-</code>',
                parse_mode='HTML',
            )
            return

    tariff = await update_tariff(db, tariff, max_device_limit=max_devices)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        '✅ Макс. устройств изменено!\n\n' + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


# ============ РЕДАКТИРОВАНИЕ ДНЕЙ ТРИАЛА ============


@admin_required
@error_handler
async def start_edit_tariff_trial_days(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование дней триала."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_trial_days)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    trial_days = getattr(tariff, 'trial_duration_days', None)
    if trial_days:
        current_days = f'{trial_days} дней'
    else:
        current_days = f'По умолчанию ({settings.TRIAL_DURATION_DAYS} дней)'

    await callback.message.edit_text(
        f'⏰ <b>编辑试用日</b>\n\n当前值：<b>{current_days}</b>\n\n输入试用天数。\n\n• <code>0</code> 或 <code>-</code> - 使用默认设置（{settings.TRIAL_DURATION_DAYS} 天）\n• 例如：<code>7</code> = 7 天试用期',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_view:{tariff_id}')]]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_trial_days(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое количество дней триала."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer('未找到套餐')
        await state.clear()
        return

    text = message.text.strip()

    if text == '-' or text == '0':
        trial_days = None
    else:
        try:
            trial_days = int(text)
            if trial_days < 1:
                raise ValueError
        except ValueError:
            await message.answer(
                '请输入有效的天数（1 或更多）。\n要使用默认设置，请输入 <code>0</code> 或 <code>-</code>',
                parse_mode='HTML',
            )
            return

    tariff = await update_tariff(db, tariff, trial_duration_days=trial_days)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        '✅ Дни триала изменены!\n\n' + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode='HTML',
    )


# ============ РЕДАКТИРОВАНИЕ ДОКУПКИ ТРАФИКА ============


def _parse_traffic_topup_packages(text: str) -> dict[int, int]:
    """
    Парсит строку с пакетами докупки трафика.
    Формат: "5:5000, 10:9000, 20:15000" (ГБ:цена_в_копейках)
    """
    packages = {}
    text = text.replace(';', ',').replace('=', ':')

    for part in text.split(','):
        part = part.strip()
        if not part:
            continue

        if ':' not in part:
            continue

        gb_str, price_str = part.split(':', 1)
        try:
            gb = int(gb_str.strip())
            price = int(price_str.strip())
            if gb > 0 and price > 0:
                packages[gb] = price
        except ValueError:
            continue

    return packages


def _format_traffic_topup_packages_for_edit(packages: dict[int, int]) -> str:
    """Форматирует пакеты докупки для редактирования."""
    if not packages:
        return '5:5000, 10:9000, 20:15000'

    parts = []
    for gb in sorted(packages.keys()):
        parts.append(f'{gb}:{packages[gb]}')

    return ', '.join(parts)


@admin_required
@error_handler
async def start_edit_tariff_traffic_topup(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает меню настройки докупки трафика."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    # Проверяем, безлимитный ли тариф
    if tariff.is_unlimited_traffic:
        await callback.answer('无限套餐不可回购', show_alert=True)
        return

    is_enabled = getattr(tariff, 'traffic_topup_enabled', False)
    packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
    max_topup_traffic = getattr(tariff, 'max_topup_traffic_gb', 0) or 0

    # Форматируем текущие настройки
    if is_enabled:
        status = '✅ Включено'
        if packages:
            packages_display = '\n'.join(
                f'  • {gb} ГБ: {format_price_kopeks(price)}' for gb, price in sorted(packages.items())
            )
        else:
            packages_display = '  Пакеты не настроены'
    else:
        status = '❌ Отключено'
        packages_display = '  -'

    # Форматируем лимит
    if max_topup_traffic > 0:
        max_limit_display = f'{max_topup_traffic} ГБ'
    else:
        max_limit_display = 'Без ограничений'

    buttons = []

    # Переключение вкл/выкл
    if is_enabled:
        buttons.append(
            [InlineKeyboardButton(text='❌ 禁用', callback_data=f'admin_tariff_toggle_traffic_topup:{tariff_id}')]
        )
    else:
        buttons.append(
            [InlineKeyboardButton(text='✅ 启用', callback_data=f'admin_tariff_toggle_traffic_topup:{tariff_id}')]
        )

    # Редактирование пакетов и лимита (только если включено)
    if is_enabled:
        buttons.append(
            [
                InlineKeyboardButton(
                    text='📦 定制套餐', callback_data=f'admin_tariff_edit_topup_packages:{tariff_id}'
                )
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text='📊 最大。流量限制', callback_data=f'admin_tariff_edit_max_topup:{tariff_id}'
                )
            ]
        )

    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')])

    await callback.message.edit_text(
        f'📈 <b>额外购买“{html.escape(tariff.name)}”</b>的流量\n\n状态：{status}\n\n<b>套餐：</b>\n{packages_display}\n\n<b>最大。限制：</b> {max_limit_display}\n\n用户将能够以指定价格购买额外流量。',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_tariff_traffic_topup(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает включение/выключение докупки трафика."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    is_enabled = getattr(tariff, 'traffic_topup_enabled', False)
    new_value = not is_enabled

    tariff = await update_tariff(db, tariff, traffic_topup_enabled=new_value)

    status_text = 'включена' if new_value else 'отключена'
    await callback.answer(f'加购流量{status_text}')

    # Перерисовываем меню
    texts = get_texts(db_user.language)
    packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
    max_topup_traffic = getattr(tariff, 'max_topup_traffic_gb', 0) or 0

    if new_value:
        status = '✅ Включено'
        if packages:
            packages_display = '\n'.join(
                f'  • {gb} ГБ: {format_price_kopeks(price)}' for gb, price in sorted(packages.items())
            )
        else:
            packages_display = '  Пакеты не настроены'
    else:
        status = '❌ Отключено'
        packages_display = '  -'

    # Форматируем лимит
    if max_topup_traffic > 0:
        max_limit_display = f'{max_topup_traffic} ГБ'
    else:
        max_limit_display = 'Без ограничений'

    buttons = []

    if new_value:
        buttons.append(
            [InlineKeyboardButton(text='❌ 禁用', callback_data=f'admin_tariff_toggle_traffic_topup:{tariff_id}')]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text='📦 定制套餐', callback_data=f'admin_tariff_edit_topup_packages:{tariff_id}'
                )
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text='📊 最大。流量限制', callback_data=f'admin_tariff_edit_max_topup:{tariff_id}'
                )
            ]
        )
    else:
        buttons.append(
            [InlineKeyboardButton(text='✅ 启用', callback_data=f'admin_tariff_toggle_traffic_topup:{tariff_id}')]
        )

    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            f'📈 <b>额外购买“{html.escape(tariff.name)}”</b>的流量\n\n状态：{status}\n\n<b>套餐：</b>\n{packages_display}\n\n<b>最大。限制：</b> {max_limit_display}\n\n用户将能够以指定价格购买额外流量。',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass


@admin_required
@error_handler
async def start_edit_traffic_topup_packages(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование пакетов докупки трафика."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_traffic_topup_packages)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
    current_packages = _format_traffic_topup_packages_for_edit(packages)

    if packages:
        packages_display = '\n'.join(
            f'  • {gb} ГБ: {format_price_kopeks(price)}' for gb, price in sorted(packages.items())
        )
    else:
        packages_display = '  Не настроены'

    await callback.message.edit_text(
        f'📦 <b>配置额外流量购买套餐</b>\n\n套餐：<b>{html.escape(tariff.name)}</b>\n\n<b>当前软件包：</b>\n{packages_display}\n\n按以下格式输入包：\n<代码>{current_packages}</code>\n\n（GB：price_in_kopecks，以逗号分隔）\n例如：<code>5:5000, 10:9000</code> = 5GB 为 50₽，10GB 为 90₽',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_edit_traffic_topup:{tariff_id}')]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_traffic_topup_packages(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новые пакеты докупки трафика."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer('未找到套餐')
        await state.clear()
        return

    if not message.text:
        await message.answer(
            '请发短信。\n\n格式：<code>GB:price_in_kopecks</code>\n示例：<代码>5:5000、10:9000、20:15000</code>',
            parse_mode='HTML',
        )
        return

    packages = _parse_traffic_topup_packages(message.text.strip())

    if not packages:
        await message.answer(
            '无法识别包。\n\n格式：<code>GB:price_in_kopecks</code>\n示例：<代码>5:5000、10:9000、20:15000</code>',
            parse_mode='HTML',
        )
        return

    # Преобразуем в формат для JSON (строковые ключи)
    packages_json = {str(gb): price for gb, price in packages.items()}

    tariff = await update_tariff(db, tariff, traffic_topup_packages=packages_json)
    await state.clear()

    # Показываем обновленное меню
    texts = get_texts(db_user.language)
    packages_display = '\n'.join(f'  • {gb} ГБ: {format_price_kopeks(price)}' for gb, price in sorted(packages.items()))
    max_topup_traffic = getattr(tariff, 'max_topup_traffic_gb', 0) or 0
    max_limit_display = f'{max_topup_traffic} ГБ' if max_topup_traffic > 0 else 'Без ограничений'

    buttons = [
        [InlineKeyboardButton(text='❌ 禁用', callback_data=f'admin_tariff_toggle_traffic_topup:{tariff_id}')],
        [
            InlineKeyboardButton(
                text='📦 定制套餐', callback_data=f'admin_tariff_edit_topup_packages:{tariff_id}'
            )
        ],
        [InlineKeyboardButton(text='📊 最大。流量限制', callback_data=f'admin_tariff_edit_max_topup:{tariff_id}')],
        [InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')],
    ]

    await message.answer(
        f'✅ <b>套餐已更新！</b>\n\n📈 <b>额外购买“{html.escape(tariff.name)}”</b>的流量\n\n状态： ✅ 已启用\n\n<b>套餐：</b>\n{packages_display}\n\n<b>最大。限制：</b> {max_limit_display}\n\n用户将能够以指定价格购买额外流量。',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )


# ============ МАКСИМАЛЬНЫЙ ЛИМИТ ДОКУПКИ ТРАФИКА ============


@admin_required
@error_handler
async def start_edit_max_topup_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование максимального лимита докупки трафика."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_max_topup_traffic)
    await state.update_data(tariff_id=tariff_id)

    current_limit = getattr(tariff, 'max_topup_traffic_gb', 0) or 0
    if current_limit > 0:
        current_display = f'{current_limit} ГБ'
    else:
        current_display = 'Без ограничений'

    await callback.message.edit_text(
        f'📊 <b>最大流量限制</b>\n\n套餐：<b>{html.escape(tariff.name)}</b>\n当前限制：<b>{current_display}</b>\n\n输入所有额外购买后订阅中可以使用的最大总流量（以 GB 表示）。\n\n• 例如，如果套餐给出 100 个 GB，且限额为 200 个 GB，则用户将可以再购买 100 个 GB\n• 输入<code>0</code> 以取消限制',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=texts.CANCEL, callback_data=f'admin_tariff_edit_traffic_topup:{tariff_id}')]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_max_topup_traffic(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое значение максимального лимита докупки трафика."""
    texts = get_texts(db_user.language)
    state_data = await state.get_data()
    tariff_id = state_data.get('tariff_id')

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer('未找到套餐')
        await state.clear()
        return

    # Парсим значение
    text = message.text.strip()
    try:
        new_limit = int(text)
        if new_limit < 0:
            raise ValueError('Negative value')
    except ValueError:
        await message.answer(
            '输入一个整数（0 或更大）。\n\n• <code>0</code> - 无限制\n• <code>200</code> - 每个订阅最多 200 个 GB',
            parse_mode='HTML',
        )
        return

    tariff = await update_tariff(db, tariff, max_topup_traffic_gb=new_limit)
    await state.clear()

    # Показываем обновленное меню
    packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
    if packages:
        packages_display = '\n'.join(
            f'  • {gb} ГБ: {format_price_kopeks(price)}' for gb, price in sorted(packages.items())
        )
    else:
        packages_display = '  Пакеты не настроены'

    max_limit_display = f'{new_limit} ГБ' if new_limit > 0 else 'Без ограничений'

    buttons = [
        [InlineKeyboardButton(text='❌ 禁用', callback_data=f'admin_tariff_toggle_traffic_topup:{tariff_id}')],
        [
            InlineKeyboardButton(
                text='📦 定制套餐', callback_data=f'admin_tariff_edit_topup_packages:{tariff_id}'
            )
        ],
        [InlineKeyboardButton(text='📊 最大。流量限制', callback_data=f'admin_tariff_edit_max_topup:{tariff_id}')],
        [InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')],
    ]

    await message.answer(
        f'✅ <b>限制已更新！</b>\n\n📈 <b>额外购买“{html.escape(tariff.name)}”</b>的流量\n\n状态： ✅ 已启用\n\n<b>套餐：</b>\n{packages_display}\n\n<b>最大。限制：</b> {max_limit_display}\n\n用户将能够以指定价格购买额外流量。',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )


# ============ УДАЛЕНИЕ ТАРИФА ============


@admin_required
@error_handler
async def confirm_delete_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Запрашивает подтверждение удаления тарифа."""
    get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    active_count = await get_active_subscriptions_count_by_tariff_id(db, tariff_id)

    if active_count > 0:
        total_count = await get_tariff_subscriptions_count(db, tariff_id)
        await callback.message.edit_text(
            f'🗑️ <b>取消套餐</b>\n\n无法删除套餐<b>{html.escape(tariff.name)}</b>。\n\n⚠️ <b>活跃订阅数：</b> {active_count}（总计：{total_count}）\n首先，停用套餐并等待所有有效订阅到期，或将订阅转移到另一个套餐。',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text='◀️ 返回套餐', callback_data=f'admin_tariff_view:{tariff_id}')],
                ]
            ),
            parse_mode='HTML',
        )
        await callback.answer()
        return

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    warning = ''
    if subs_count > 0:
        warning = (
            f'\n\n⚠️ <b>Внимание!</b> На этом тарифе {subs_count} неактивных подписок.\nОни потеряют привязку к тарифу.'
        )

    await callback.message.edit_text(
        f'🗑️ <b>取消套餐</b>\n\n您确定要取消套餐<b>{html.escape(tariff.name)}</b>吗？{warning}',
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text='✅ 是，删除', callback_data=f'admin_tariff_delete_confirm:{tariff_id}'
                    ),
                    InlineKeyboardButton(text='❌ 取消', callback_data=f'admin_tariff_view:{tariff_id}'),
                ]
            ]
        ),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_tariff_confirmed(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Удаляет тариф после подтверждения."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    # Защита от удаления тарифа с активными подписками (FK RESTRICT)
    active_count = await get_active_subscriptions_count_by_tariff_id(db, tariff.id)
    if active_count > 0:
        await callback.answer(
            f'无法删除套餐：{active_count} 活跃订阅。首先，取消套餐。',
            show_alert=True,
        )
        return

    tariff_name = tariff.name
    await delete_tariff(db, tariff)

    await callback.answer(f'套餐“{tariff_name}”已被删除', show_alert=True)

    # Возвращаемся к списку
    tariffs_data = await get_tariffs_with_subscriptions_count(db, include_inactive=True)

    if not tariffs_data:
        await callback.message.edit_text(
            '📦 <b>套餐</b>\n\n套餐尚未制定。',
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text='➕ 创建套餐', callback_data='admin_tariff_create')],
                    [InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')],
                ]
            ),
            parse_mode='HTML',
        )
        return

    total_pages = (len(tariffs_data) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page_data = tariffs_data[:ITEMS_PER_PAGE]

    await callback.message.edit_text(
        f'📦 <b>套餐</b>\n\n✅ 套餐“{tariff_name}”已被删除\n\n总计：{len(tariffs_data)}',
        reply_markup=get_tariffs_list_keyboard(page_data, db_user.language, 0, total_pages),
        parse_mode='HTML',
    )


# ============ РЕДАКТИРОВАНИЕ СЕРВЕРОВ ============


@admin_required
@error_handler
async def start_edit_tariff_squads(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает меню выбора серверов для тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    squads, _ = await get_all_server_squads(db, limit=10000)

    if not squads:
        await callback.answer('没有可用的服务器', show_alert=True)
        return

    current_squads = set(tariff.allowed_squads or [])

    buttons = []
    for squad in squads:
        is_selected = squad.squad_uuid in current_squads
        prefix = '✅' if is_selected else '⬜'
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f'{prefix} {squad.display_name}',
                    callback_data=f'trf_sq:{tariff_id}:{squad.squad_uuid}',
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(text='🔄清除一切', callback_data=f'admin_tariff_clear_squads:{tariff_id}'),
            InlineKeyboardButton(text='✅ 选择全部', callback_data=f'admin_tariff_select_all_squads:{tariff_id}'),
        ]
    )
    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')])

    selected_count = len(current_squads)

    await callback.message.edit_text(
        f'🌐 <b>套餐“{html.escape(tariff.name)}”的服务器</b>\n\n已选择： {selected_count} 来自 {len(squads)}\n\n如果未选择服务器，则所有服务器均可用。\n单击服务器以选择/取消选择：',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_tariff_squad(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает выбор сервера для тарифа."""
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    squad_uuid = parts[2]

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    current_squads = set(tariff.allowed_squads or [])

    if squad_uuid in current_squads:
        current_squads.remove(squad_uuid)
    else:
        current_squads.add(squad_uuid)

    tariff = await update_tariff(db, tariff, allowed_squads=list(current_squads))

    # Перерисовываем меню
    squads, _ = await get_all_server_squads(db, limit=10000)
    texts = get_texts(db_user.language)

    buttons = []
    for squad in squads:
        is_selected = squad.squad_uuid in current_squads
        prefix = '✅' if is_selected else '⬜'
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f'{prefix} {squad.display_name}',
                    callback_data=f'trf_sq:{tariff_id}:{squad.squad_uuid}',
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(text='🔄清除一切', callback_data=f'admin_tariff_clear_squads:{tariff_id}'),
            InlineKeyboardButton(text='✅ 选择全部', callback_data=f'admin_tariff_select_all_squads:{tariff_id}'),
        ]
    )
    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            f'🌐 <b>套餐“{html.escape(tariff.name)}”的服务器</b>\n\n已选择： {len(current_squads)} 来自 {len(squads)}\n\n如果未选择服务器，则所有服务器均可用。\n单击服务器以选择/取消选择：',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass

    await callback.answer()

    # Применяем изменения серверов к существующим подпискам
    from app.services.subscription_service import SubscriptionService

    propagate_result = await SubscriptionService().propagate_tariff_squads(db, tariff.id, list(current_squads))
    if propagate_result.failed_ids:
        await callback.message.answer(
            f'⚠️ 来自 {propagate_result.total} 的 {len(propagate_result.failed_ids)} 订阅与 RemnaWave 不同步',
        )


@admin_required
@error_handler
async def clear_tariff_squads(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Очищает список серверов тарифа."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    tariff = await update_tariff(db, tariff, allowed_squads=[])
    await callback.answer('所有服务器已清空')

    # Перерисовываем меню
    squads, _ = await get_all_server_squads(db, limit=10000)
    texts = get_texts(db_user.language)

    buttons = []
    for squad in squads:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f'⬜ {squad.display_name}',
                    callback_data=f'trf_sq:{tariff_id}:{squad.squad_uuid}',
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(text='🔄清除一切', callback_data=f'admin_tariff_clear_squads:{tariff_id}'),
            InlineKeyboardButton(text='✅ 选择全部', callback_data=f'admin_tariff_select_all_squads:{tariff_id}'),
        ]
    )
    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            f'🌐 <b>套餐“{html.escape(tariff.name)}”的服务器</b>\n\n已选择： 0 来自 {len(squads)}\n\n如果未选择服务器，则所有服务器均可用。\n单击服务器以选择/取消选择：',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass

    # Применяем изменения серверов к существующим подпискам (пустой список = все серверы)
    from app.services.subscription_service import SubscriptionService

    propagate_result = await SubscriptionService().propagate_tariff_squads(db, tariff.id, [])
    if propagate_result.failed_ids:
        await callback.message.answer(
            f'⚠️ 来自 {propagate_result.total} 的 {len(propagate_result.failed_ids)} 订阅与 RemnaWave 不同步',
        )


@admin_required
@error_handler
async def select_all_tariff_squads(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Выбирает все серверы для тарифа."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    squads, _ = await get_all_server_squads(db, limit=10000)
    all_uuids = [s.squad_uuid for s in squads if s.squad_uuid]

    tariff = await update_tariff(db, tariff, allowed_squads=all_uuids)
    await callback.answer('所有服务器均已选择')

    texts = get_texts(db_user.language)

    buttons = []
    for squad in squads:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f'✅ {squad.display_name}',
                    callback_data=f'trf_sq:{tariff_id}:{squad.squad_uuid}',
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(text='🔄清除一切', callback_data=f'admin_tariff_clear_squads:{tariff_id}'),
            InlineKeyboardButton(text='✅ 选择全部', callback_data=f'admin_tariff_select_all_squads:{tariff_id}'),
        ]
    )
    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            f'🌐 <b>套餐“{html.escape(tariff.name)}”的服务器</b>\n\n已选择： {len(squads)} 来自 {len(squads)}\n\n如果未选择服务器，则所有服务器均可用。\n单击服务器以选择/取消选择：',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass

    # Применяем изменения серверов к существующим подпискам
    from app.services.subscription_service import SubscriptionService

    propagate_result = await SubscriptionService().propagate_tariff_squads(db, tariff.id, all_uuids)
    if propagate_result.failed_ids:
        await callback.message.answer(
            f'⚠️ 来自 {propagate_result.total} 的 {len(propagate_result.failed_ids)} 订阅与 RemnaWave 不同步',
        )


# ============ РЕДАКТИРОВАНИЕ ПРОМОГРУПП ============


@admin_required
@error_handler
async def start_edit_tariff_promo_groups(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Показывает меню выбора промогрупп для тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    promo_groups_data = await get_promo_groups_with_counts(db)

    if not promo_groups_data:
        await callback.answer('没有促销组', show_alert=True)
        return

    current_groups = {pg.id for pg in (tariff.allowed_promo_groups or [])}

    buttons = []
    for promo_group, _ in promo_groups_data:
        is_selected = promo_group.id in current_groups
        prefix = '✅' if is_selected else '⬜'
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f'{prefix} {promo_group.name}',
                    callback_data=f'admin_tariff_toggle_promo:{tariff_id}:{promo_group.id}',
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(text='🔄清除一切', callback_data=f'admin_tariff_clear_promo:{tariff_id}'),
        ]
    )
    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')])

    selected_count = len(current_groups)

    await callback.message.edit_text(
        f'👥 <b>套餐“{html.escape(tariff.name)}”的促销组</b>\n\n已选择: {selected_count}\n\n如果未选择任何团体，则该套餐适用于所有人。\n选择适用此套餐的组：',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_tariff_promo_group(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает выбор промогруппы для тарифа."""
    from app.database.crud.tariff import add_promo_group_to_tariff, remove_promo_group_from_tariff

    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    promo_group_id = int(parts[2])

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    current_groups = {pg.id for pg in (tariff.allowed_promo_groups or [])}

    if promo_group_id in current_groups:
        await remove_promo_group_from_tariff(db, tariff, promo_group_id)
        current_groups.remove(promo_group_id)
    else:
        await add_promo_group_to_tariff(db, tariff, promo_group_id)
        current_groups.add(promo_group_id)

    # Обновляем тариф из БД
    tariff = await get_tariff_by_id(db, tariff_id)
    current_groups = {pg.id for pg in (tariff.allowed_promo_groups or [])}

    # Перерисовываем меню
    promo_groups_data = await get_promo_groups_with_counts(db)
    texts = get_texts(db_user.language)

    buttons = []
    for promo_group, _ in promo_groups_data:
        is_selected = promo_group.id in current_groups
        prefix = '✅' if is_selected else '⬜'
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f'{prefix} {promo_group.name}',
                    callback_data=f'admin_tariff_toggle_promo:{tariff_id}:{promo_group.id}',
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(text='🔄清除一切', callback_data=f'admin_tariff_clear_promo:{tariff_id}'),
        ]
    )
    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            f'👥 <b>套餐“{html.escape(tariff.name)}”的促销组</b>\n\n已选择: {len(current_groups)}\n\n如果未选择任何团体，则该套餐适用于所有人。\n选择适用此套餐的组：',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass

    await callback.answer()


@admin_required
@error_handler
async def clear_tariff_promo_groups(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Очищает список промогрупп тарифа."""
    from app.database.crud.tariff import set_tariff_promo_groups

    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    await set_tariff_promo_groups(db, tariff, [])
    await callback.answer('所有促销组已被清除')

    # Перерисовываем меню
    promo_groups_data = await get_promo_groups_with_counts(db)
    texts = get_texts(db_user.language)

    buttons = []
    for promo_group, _ in promo_groups_data:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f'⬜ {promo_group.name}',
                    callback_data=f'admin_tariff_toggle_promo:{tariff_id}:{promo_group.id}',
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(text='🔄清除一切', callback_data=f'admin_tariff_clear_promo:{tariff_id}'),
        ]
    )
    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')])

    try:
        await callback.message.edit_text(
            f'👥 <b>套餐“{html.escape(tariff.name)}”的促销组</b>\n\n已选择: 0\n\n如果未选择任何团体，则该套餐适用于所有人。\n选择适用此套餐的组：',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode='HTML',
        )
    except TelegramBadRequest:
        pass


# ==================== Режим сброса трафика ====================

TRAFFIC_RESET_MODES = [
    ('DAY', '📅 Ежедневно', 'Трафик сбрасывается каждый день'),
    ('WEEK', '📆 Еженедельно', 'Трафик сбрасывается каждую неделю'),
    ('MONTH', '🗓️ Ежемесячно', 'Трафик сбрасывается каждый месяц'),
    ('MONTH_ROLLING', '🔄 Скользящий месяц', 'Трафик сбрасывается через 30 дней от первого подключения'),
    ('NO_RESET', '🚫 Никогда', 'Трафик не сбрасывается автоматически'),
]


def get_traffic_reset_mode_keyboard(tariff_id: int, current_mode: str | None, language: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру для выбора режима сброса трафика."""
    texts = get_texts(language)
    buttons = []

    # Кнопка "Глобальная настройка"
    global_label = (
        f'{"✅ " if current_mode is None else ""}🌐 Глобальная настройка ({settings.DEFAULT_TRAFFIC_RESET_STRATEGY})'
    )
    buttons.append(
        [InlineKeyboardButton(text=global_label, callback_data=f'admin_tariff_set_reset_mode:{tariff_id}:GLOBAL')]
    )

    # Кнопки для каждого режима
    for mode_value, mode_label, mode_desc in TRAFFIC_RESET_MODES:
        is_selected = current_mode == mode_value
        label = f'{"✅ " if is_selected else ""}{mode_label}'
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=f'admin_tariff_set_reset_mode:{tariff_id}:{mode_value}')]
        )

    # Кнопка назад
    buttons.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_tariff_view:{tariff_id}')])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@admin_required
@error_handler
async def start_edit_traffic_reset_mode(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Начинает редактирование режима сброса трафика."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    current_mode = getattr(tariff, 'traffic_reset_mode', None)

    await callback.message.edit_text(
        f'🔄 <b>套餐“{html.escape(tariff.name)}”</b>的流量重置模式\n\n当前模式：{_format_traffic_reset_mode(current_mode)}\n\n选择何时重置此套餐订阅者的已用流量：\n\n• <b>全局设置</b> - 使用机器人配置中的值\n• <b>每日</b> - 每天重置\n• <b>每周</b> - 每周重置\n• <b>每月</b> - 每月重置\n• <b>从不</b> - 流量在整个订阅期内累积',
        reply_markup=get_traffic_reset_mode_keyboard(tariff_id, current_mode, db_user.language),
        parse_mode='HTML',
    )
    await callback.answer()


@admin_required
@error_handler
async def set_traffic_reset_mode(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Устанавливает режим сброса трафика для тарифа."""
    parts = callback.data.split(':')
    tariff_id = int(parts[1])
    new_mode = parts[2]

    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer('未找到套餐', show_alert=True)
        return

    # Преобразуем GLOBAL в None
    if new_mode == 'GLOBAL':
        new_mode = None

    # Обновляем тариф
    tariff = await update_tariff(db, tariff, traffic_reset_mode=new_mode)

    mode_display = _format_traffic_reset_mode(new_mode)
    await callback.answer(f'重置模式已更改：{mode_display}', show_alert=True)

    # Обновляем клавиатуру
    await callback.message.edit_text(
        f'🔄 <b>套餐“{html.escape(tariff.name)}”</b>的流量重置模式\n\n当前模式：{mode_display}\n\n选择何时重置此套餐订阅者的已用流量：\n\n• <b>全局设置</b> - 使用机器人配置中的值\n• <b>每日</b> - 每天重置\n• <b>每周</b> - 每周重置\n• <b>每月</b> - 每月重置\n• <b>从不</b> - 流量在整个订阅期内累积',
        reply_markup=get_traffic_reset_mode_keyboard(tariff_id, new_mode, db_user.language),
        parse_mode='HTML',
    )


def register_handlers(dp: Dispatcher):
    """Регистрирует обработчики для управления тарифами."""
    # Список тарифов
    dp.callback_query.register(show_tariffs_list, F.data == 'admin_tariffs')
    dp.callback_query.register(show_tariffs_page, F.data.startswith('admin_tariffs_page:'))

    # Просмотр и переключение
    dp.callback_query.register(view_tariff, F.data.startswith('admin_tariff_view:'))
    dp.callback_query.register(
        toggle_tariff,
        F.data.startswith('admin_tariff_toggle:')
        & ~F.data.startswith('admin_tariff_toggle_trial:')
        & ~F.data.startswith('trf_sq:')
        & ~F.data.startswith('admin_tariff_toggle_promo:')
        & ~F.data.startswith('admin_tariff_toggle_traffic_topup:')
        & ~F.data.startswith('admin_tariff_toggle_daily:'),
    )
    dp.callback_query.register(toggle_trial_tariff, F.data.startswith('admin_tariff_toggle_trial:'))

    # Создание тарифа
    dp.callback_query.register(start_create_tariff, F.data == 'admin_tariff_create')
    dp.message.register(process_tariff_name, AdminStates.creating_tariff_name)
    dp.message.register(process_tariff_traffic, AdminStates.creating_tariff_traffic)
    dp.message.register(process_tariff_devices, AdminStates.creating_tariff_devices)
    dp.message.register(process_tariff_tier, AdminStates.creating_tariff_tier)
    dp.callback_query.register(select_tariff_type_periodic, F.data == 'tariff_type_periodic')
    dp.callback_query.register(select_tariff_type_daily, F.data == 'tariff_type_daily')
    dp.message.register(process_tariff_prices, AdminStates.creating_tariff_prices)

    # Редактирование названия
    dp.callback_query.register(start_edit_tariff_name, F.data.startswith('admin_tariff_edit_name:'))
    dp.message.register(process_edit_tariff_name, AdminStates.editing_tariff_name)

    # Редактирование описания
    dp.callback_query.register(start_edit_tariff_description, F.data.startswith('admin_tariff_edit_desc:'))
    dp.message.register(process_edit_tariff_description, AdminStates.editing_tariff_description)

    # Редактирование трафика (traffic_topup BEFORE traffic to avoid prefix conflict)
    dp.callback_query.register(start_edit_tariff_traffic_topup, F.data.startswith('admin_tariff_edit_traffic_topup:'))
    dp.callback_query.register(start_edit_tariff_traffic, F.data.startswith('admin_tariff_edit_traffic:'))
    dp.message.register(process_edit_tariff_traffic, AdminStates.editing_tariff_traffic)

    # Редактирование устройств
    dp.callback_query.register(start_edit_tariff_devices, F.data.startswith('admin_tariff_edit_devices:'))
    dp.message.register(process_edit_tariff_devices, AdminStates.editing_tariff_devices)

    # Редактирование уровня
    dp.callback_query.register(start_edit_tariff_tier, F.data.startswith('admin_tariff_edit_tier:'))
    dp.message.register(process_edit_tariff_tier, AdminStates.editing_tariff_tier)

    # Редактирование цен
    dp.callback_query.register(start_edit_tariff_prices, F.data.startswith('admin_tariff_edit_prices:'))
    dp.message.register(process_edit_tariff_prices, AdminStates.editing_tariff_prices)

    # Редактирование цены за устройство
    dp.callback_query.register(start_edit_tariff_device_price, F.data.startswith('admin_tariff_edit_device_price:'))
    dp.message.register(process_edit_tariff_device_price, AdminStates.editing_tariff_device_price)

    # Редактирование макс. устройств
    dp.callback_query.register(start_edit_tariff_max_devices, F.data.startswith('admin_tariff_edit_max_devices:'))
    dp.message.register(process_edit_tariff_max_devices, AdminStates.editing_tariff_max_devices)

    # Редактирование дней триала
    dp.callback_query.register(start_edit_tariff_trial_days, F.data.startswith('admin_tariff_edit_trial_days:'))
    dp.message.register(process_edit_tariff_trial_days, AdminStates.editing_tariff_trial_days)

    # Редактирование докупки трафика (start_edit_tariff_traffic_topup registered above with traffic)
    dp.callback_query.register(toggle_tariff_traffic_topup, F.data.startswith('admin_tariff_toggle_traffic_topup:'))
    dp.callback_query.register(
        start_edit_traffic_topup_packages, F.data.startswith('admin_tariff_edit_topup_packages:')
    )
    dp.message.register(process_edit_traffic_topup_packages, AdminStates.editing_tariff_traffic_topup_packages)

    # Редактирование макс. лимита докупки трафика
    dp.callback_query.register(start_edit_max_topup_traffic, F.data.startswith('admin_tariff_edit_max_topup:'))
    dp.message.register(process_edit_max_topup_traffic, AdminStates.editing_tariff_max_topup_traffic)

    # Удаление (delete_confirm BEFORE delete to avoid prefix conflict)
    dp.callback_query.register(delete_tariff_confirmed, F.data.startswith('admin_tariff_delete_confirm:'))
    dp.callback_query.register(confirm_delete_tariff, F.data.startswith('admin_tariff_delete:'))

    # Редактирование серверов
    dp.callback_query.register(start_edit_tariff_squads, F.data.startswith('admin_tariff_edit_squads:'))
    dp.callback_query.register(toggle_tariff_squad, F.data.startswith('trf_sq:'))
    dp.callback_query.register(clear_tariff_squads, F.data.startswith('admin_tariff_clear_squads:'))
    dp.callback_query.register(select_all_tariff_squads, F.data.startswith('admin_tariff_select_all_squads:'))

    # Редактирование промогрупп
    dp.callback_query.register(start_edit_tariff_promo_groups, F.data.startswith('admin_tariff_edit_promo:'))
    dp.callback_query.register(toggle_tariff_promo_group, F.data.startswith('admin_tariff_toggle_promo:'))
    dp.callback_query.register(clear_tariff_promo_groups, F.data.startswith('admin_tariff_clear_promo:'))

    # Суточный режим
    dp.callback_query.register(toggle_daily_tariff, F.data.startswith('admin_tariff_toggle_daily:'))
    dp.callback_query.register(start_edit_daily_price, F.data.startswith('admin_tariff_edit_daily_price:'))
    dp.message.register(process_daily_price_input, AdminStates.editing_tariff_daily_price)

    # Режим сброса трафика
    dp.callback_query.register(start_edit_traffic_reset_mode, F.data.startswith('admin_tariff_edit_reset_mode:'))
    dp.callback_query.register(set_traffic_reset_mode, F.data.startswith('admin_tariff_set_reset_mode:'))
