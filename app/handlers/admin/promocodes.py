import html
from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.promo_group import get_promo_group_by_id, get_promo_groups_with_counts
from app.database.crud.promocode import (
    create_promocode,
    delete_promocode,
    get_promocode_by_code,
    get_promocode_by_id,
    get_promocode_statistics,
    get_promocodes_count,
    get_promocodes_list,
    update_promocode,
)
from app.database.models import PromoCodeType, User
from app.keyboards.admin import (
    get_admin_pagination_keyboard,
    get_admin_promocodes_keyboard,
    get_promocode_type_keyboard,
)
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler
from app.utils.formatters import format_datetime


logger = structlog.get_logger(__name__)


@admin_required
@error_handler
async def show_promocodes_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    total_codes = await get_promocodes_count(db)
    active_codes = await get_promocodes_count(db, is_active=True)

    text = f'🎫 <b>促销代码管理</b>\n\n📊 <b>统计：</b>\n- 促销代码总数：{total_codes}\n- 主动：{active_codes}\n- 无效：{total_codes - active_codes}\n\n选择动作：'

    await callback.message.edit_text(text, reply_markup=get_admin_promocodes_keyboard(db_user.language))
    await callback.answer()


@admin_required
@error_handler
async def show_promocodes_list(callback: types.CallbackQuery, db_user: User, db: AsyncSession, page: int = 1):
    limit = 10
    offset = (page - 1) * limit

    promocodes = await get_promocodes_list(db, offset=offset, limit=limit)
    total_count = await get_promocodes_count(db)
    total_pages = (total_count + limit - 1) // limit

    if not promocodes:
        await callback.message.edit_text(
            '🎫 未找到促销代码',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_promocodes')]]
            ),
        )
        await callback.answer()
        return

    text = f'🎫 <b>优惠码列表</b>（第 {page}/{total_pages} 页）'
    keyboard = []

    for promo in promocodes:
        status_emoji = '✅' if promo.is_active else '❌'
        type_emoji = {
            'balance': '💰',
            'subscription_days': '📅',
            'trial_subscription': '🎁',
            'promo_group': '🏷️',
            'discount': '💸',
        }.get(promo.type, '🎫')

        text += f'{status_emoji} {type_emoji} <code>{promo.code}</code>\n'
        text += f'📊 用途：{promo.current_uses}/{promo.max_uses}'

        if promo.type == PromoCodeType.BALANCE.value:
            text += f'💰 奖金：{settings.format_price(promo.balance_bonus_kopeks)}'
        elif promo.type == PromoCodeType.SUBSCRIPTION_DAYS.value:
            text += f'📅 天：{promo.subscription_days}'
        elif promo.type == PromoCodeType.PROMO_GROUP.value:
            if promo.promo_group:
                text += f'🏷️促销群：{html.escape(promo.promo_group.name)}'
        elif promo.type == PromoCodeType.DISCOUNT.value:
            discount_hours = promo.subscription_days
            if discount_hours > 0:
                text += f'💸 折扣：{promo.balance_bonus_kopeks}%（{discount_hours} h.）'
            else:
                text += f'💸 折扣：{promo.balance_bonus_kopeks}%（购买前）'

        if promo.valid_until:
            text += f'⏰ 之前：{format_datetime(promo.valid_until)}'

        keyboard.append([types.InlineKeyboardButton(text=f'🎫 {promo.code}', callback_data=f'promo_manage_{promo.id}')])

        text += '\n'

    if total_pages > 1:
        pagination_row = get_admin_pagination_keyboard(
            page, total_pages, 'admin_promo_list', 'admin_promocodes', db_user.language
        ).inline_keyboard[0]
        keyboard.append(pagination_row)

    keyboard.extend(
        [
            [types.InlineKeyboardButton(text='➕ 创建', callback_data='admin_promo_create')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_promocodes')],
        ]
    )

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_promocodes_list_page(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Обработчик пагинации списка промокодов."""
    try:
        page = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        page = 1
    await show_promocodes_list(callback, db_user, db, page=page)


@admin_required
@error_handler
async def show_promocode_management(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    promo_id = int(callback.data.split('_')[-1])

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer('❌ 未找到促销代码', show_alert=True)
        return

    status_emoji = '✅' if promo.is_active else '❌'
    type_emoji = {
        'balance': '💰',
        'subscription_days': '📅',
        'trial_subscription': '🎁',
        'promo_group': '🏷️',
        'discount': '💸',
    }.get(promo.type, '🎫')

    text = f"🎫 <b>优惠码管理</b>\n\n{type_emoji} <b>代码：</b> <code>{promo.code}</code>\n{status_emoji} <b>状态：</b> {'启用' if promo.is_active else '停用'}\n📊 <b>用途：</b> {promo.current_uses}/{promo.max_uses}"

    if promo.type == PromoCodeType.BALANCE.value:
        text += f'💰 <b>奖励：</b> {settings.format_price(promo.balance_bonus_kopeks)}'
    elif promo.type == PromoCodeType.SUBSCRIPTION_DAYS.value:
        text += f'📅 <b>天数：</b> {promo.subscription_days}'
    elif promo.type == PromoCodeType.PROMO_GROUP.value:
        if promo.promo_group:
            text += f'🏷️<b>促销群：</b> {html.escape(promo.promo_group.name)}（优先：{promo.promo_group.priority}）'
        elif promo.promo_group_id:
            text += f'🏷️ <b>促销组ID：</b> {promo.promo_group_id}（未找到）'
    elif promo.type == PromoCodeType.DISCOUNT.value:
        discount_hours = promo.subscription_days
        if discount_hours > 0:
            text += f'💸 <b>折扣：</b> {promo.balance_bonus_kopeks}%（期限：{discount_hours} h.）'
        else:
            text += f'💸 <b>折扣：</b> {promo.balance_bonus_kopeks}%（首次购买前）'

    if promo.valid_until:
        text += f'⏰ <b> 有效期至：</b> {format_datetime(promo.valid_until)}'

    first_purchase_only = getattr(promo, 'first_purchase_only', False)
    first_purchase_emoji = '✅' if first_purchase_only else '❌'
    text += f'🆕 <b> 仅限首次购买：</b> {first_purchase_emoji}'

    text += f'📅 <b>创建者：</b> {format_datetime(promo.created_at)}'

    first_purchase_btn_text = '🆕 Первая покупка: ✅' if first_purchase_only else '🆕 Первая покупка: ❌'

    keyboard = [
        [
            types.InlineKeyboardButton(text='✏️编辑', callback_data=f'promo_edit_{promo.id}'),
            types.InlineKeyboardButton(text='🔄 切换状态', callback_data=f'promo_toggle_{promo.id}'),
        ],
        [types.InlineKeyboardButton(text=first_purchase_btn_text, callback_data=f'promo_toggle_first_{promo.id}')],
        [
            types.InlineKeyboardButton(text='📊 统计', callback_data=f'promo_stats_{promo.id}'),
            types.InlineKeyboardButton(text='🗑️ 删除', callback_data=f'promo_delete_{promo.id}'),
        ],
        [types.InlineKeyboardButton(text='⬅️ 前往列表', callback_data='admin_promo_list')],
    ]

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def show_promocode_edit_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 接收 ID 促销代码时出错', show_alert=True)
        return

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer('❌ 未找到促销代码', show_alert=True)
        return

    text = f'✏️ <b>编辑促销码</b> <code>{promo.code}</code>\n\n💰 <b>当前参数：</b>'

    if promo.type == PromoCodeType.BALANCE.value:
        text += f'• 奖金：{settings.format_price(promo.balance_bonus_kopeks)}'
    elif promo.type in [PromoCodeType.SUBSCRIPTION_DAYS.value, PromoCodeType.TRIAL_SUBSCRIPTION.value]:
        text += f'• 天数：{promo.subscription_days}'

    text += f'• 用途：{promo.current_uses}/{promo.max_uses}'

    if promo.valid_until:
        text += f'• 之前：{format_datetime(promo.valid_until)}'
    else:
        text += '• 持续时间：无限制'

    text += '选择要更改的选项：'

    keyboard = [
        [types.InlineKeyboardButton(text='📅 结束日期', callback_data=f'promo_edit_date_{promo.id}')],
        [types.InlineKeyboardButton(text='📊 使用次数', callback_data=f'promo_edit_uses_{promo.id}')],
    ]

    if promo.type == PromoCodeType.BALANCE.value:
        keyboard.insert(
            1, [types.InlineKeyboardButton(text='💰 奖金金额', callback_data=f'promo_edit_amount_{promo.id}')]
        )
    elif promo.type in [PromoCodeType.SUBSCRIPTION_DAYS.value, PromoCodeType.TRIAL_SUBSCRIPTION.value]:
        keyboard.insert(
            1, [types.InlineKeyboardButton(text='📅 天数', callback_data=f'promo_edit_days_{promo.id}')]
        )

    keyboard.extend([[types.InlineKeyboardButton(text='⬅️ 返回', callback_data=f'promo_manage_{promo.id}')]])

    await callback.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
    await callback.answer()


@admin_required
@error_handler
async def start_edit_promocode_date(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 接收 ID 促销代码时出错', show_alert=True)
        return

    await state.update_data(editing_promo_id=promo_id, edit_action='date')

    text = f'📅 <b>更改促销代码的结束日期</b>\n\n输入距离结束的天数（从当前时刻开始）：\n• 输入 <b>0</b> 获取终身促销代码\n• 输入正数以设置截止日期\n\n<i>例如：30（促销码有效期为30天）</i>\n\nID 促销代码：{promo_id}'

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data=f'promo_edit_{promo_id}')]]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await state.set_state(AdminStates.setting_promocode_expiry)
    await callback.answer()


@admin_required
@error_handler
async def start_edit_promocode_amount(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 接收 ID 促销代码时出错', show_alert=True)
        return

    await state.update_data(editing_promo_id=promo_id, edit_action='amount')

    text = f'💰 <b>更改促销代码的奖金金额</b>\n\n输入新的卢布金额：\n<i>例如：500</i>\n\nID 促销代码：{promo_id}'

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data=f'promo_edit_{promo_id}')]]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await state.set_state(AdminStates.setting_promocode_value)
    await callback.answer()


@admin_required
@error_handler
async def start_edit_promocode_days(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    # ИСПРАВЛЕНИЕ: берем последний элемент как ID
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 接收 ID 促销代码时出错', show_alert=True)
        return

    await state.update_data(editing_promo_id=promo_id, edit_action='days')

    text = f'📅 <b>更改订阅天数</b>\n\n输入新的天数：\n<i>例如：30</i>\n\nID 促销代码：{promo_id}'

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data=f'promo_edit_{promo_id}')]]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await state.set_state(AdminStates.setting_promocode_value)
    await callback.answer()


@admin_required
@error_handler
async def start_edit_promocode_uses(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 接收 ID 促销代码时出错', show_alert=True)
        return

    await state.update_data(editing_promo_id=promo_id, edit_action='uses')

    text = f'📊 <b>更改最大使用次数</b>\n\n输入新的使用次数：\n• 输入<b>0</b> 无限使用\n• 输入一个正数作为限制。\n\n<i>例如：100</i>\n\nID 促销代码：{promo_id}'

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data=f'promo_edit_{promo_id}')]]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await state.set_state(AdminStates.setting_promocode_uses)
    await callback.answer()


@admin_required
@error_handler
async def start_promocode_creation(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    await callback.message.edit_text(
        '🎫 <b>创建促销代码</b>\n\n选择促销代码类型：',
        reply_markup=get_promocode_type_keyboard(db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def select_promocode_type(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    promo_type = callback.data.split('_')[-1]

    type_names = {
        'balance': '💰 Пополнение баланса',
        'days': '📅 Дни подписки',
        'trial': '🎁 Тестовая подписка',
        'group': '🏷️ Промогруппа',
        'discount': '💸 Одноразовая скидка',
    }

    await state.update_data(promocode_type=promo_type)

    await callback.message.edit_text(
        f'🎫 <b>创建促销代码</b>\n\n型号：{type_names.get(promo_type, promo_type)}\n\n输入促销代码（仅限拉丁字母和数字）：',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_promocodes')]]
        ),
    )

    await state.set_state(AdminStates.creating_promocode)
    await callback.answer()


@admin_required
@error_handler
async def process_promocode_code(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    code = message.text.strip().upper()

    if not code.isalnum() or len(code) < 3 or len(code) > 20:
        await message.answer('❌ 代码只能包含拉丁字母和数字（3-20 个字符）')
        return

    existing = await get_promocode_by_code(db, code)
    if existing:
        await message.answer('❌ 使用此代码的促销代码已存在')
        return

    await state.update_data(promocode_code=code)

    data = await state.get_data()
    promo_type = data.get('promocode_type')

    if promo_type == 'balance':
        await message.answer(f'💰 <b>促销代码：</b> <code>{code}</code>\n\n输入充值余额的金额（卢布）：')
        await state.set_state(AdminStates.setting_promocode_value)
    elif promo_type == 'days':
        await message.answer(f'📅 <b>促销代码：</b> <code>{code}</code>\n\n输入订阅天数：')
        await state.set_state(AdminStates.setting_promocode_value)
    elif promo_type == 'trial':
        await message.answer(f'🎁 <b>促销代码：</b> <code>{code}</code>\n\n输入试用订阅的天数：')
        await state.set_state(AdminStates.setting_promocode_value)
    elif promo_type == 'discount':
        await message.answer(f'💸 <b>促销代码：</b> <code>{code}</code>\n\n输入折扣百分比 (1-100)：')
        await state.set_state(AdminStates.setting_promocode_value)
    elif promo_type == 'group':
        # Show promo group selection
        groups_with_counts = await get_promo_groups_with_counts(db, limit=50)

        if not groups_with_counts:
            await message.answer(
                '❌ 未找到促销组。创建至少一个促销组。',
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_promocodes')]]
                ),
            )
            await state.clear()
            return

        keyboard = []
        text = f'🏷️ <b>促销代码：</b> <code>{code}</code>\n\n选择要分配的促销组：'

        for promo_group, user_count in groups_with_counts:
            text += (
                f'• {html.escape(promo_group.name)}（优先级：{promo_group.priority}，用户：{user_count}）'
            )
            keyboard.append(
                [
                    types.InlineKeyboardButton(
                        text=f'{promo_group.name} (↑{promo_group.priority})',
                        callback_data=f'promo_select_group_{promo_group.id}',
                    )
                ]
            )

        keyboard.append([types.InlineKeyboardButton(text='❌ 取消', callback_data='admin_promocodes')])

        await message.answer(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard))
        await state.set_state(AdminStates.selecting_promo_group)


@admin_required
@error_handler
async def process_promo_group_selection(
    callback: types.CallbackQuery, db_user: User, state: FSMContext, db: AsyncSession
):
    """Handle promo group selection for promocode"""
    try:
        promo_group_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 接收 ID 促销组时出错', show_alert=True)
        return

    promo_group = await get_promo_group_by_id(db, promo_group_id)
    if not promo_group:
        await callback.answer('❌ 找不到促销组', show_alert=True)
        return

    await state.update_data(promo_group_id=promo_group_id, promo_group_name=promo_group.name)

    await callback.message.edit_text(
        f'🏷️ <b> 促销组的促销代码</b>\n\n促销组：{html.escape(promo_group.name)}\n优先级：{promo_group.priority}\n\n📊 输入促销代码可以使用的次数（或 0 表示无限制）：'
    )

    await state.set_state(AdminStates.setting_promocode_uses)
    await callback.answer()


@admin_required
@error_handler
async def process_promocode_value(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()

    if data.get('editing_promo_id'):
        await handle_edit_value(message, db_user, state, db)
        return

    try:
        value = int(message.text.strip())

        promo_type = data.get('promocode_type')

        if promo_type == 'balance' and (value < 1 or value > 10000):
            await message.answer('❌ 金额必须在 1 至 10,000 卢布之间')
            return
        if promo_type in ['days', 'trial'] and (value < 1 or value > 3650):
            await message.answer('❌ 天数必须在 1 到 3650 之间')
            return
        if promo_type == 'discount' and (value < 1 or value > 100):
            await message.answer('❌ 折扣百分比必须在 1 到 100 之间')
            return

        await state.update_data(promocode_value=value)

        await message.answer('📊 输入促销代码可以使用的次数（或 0 表示无限制）：')
        await state.set_state(AdminStates.setting_promocode_uses)

    except ValueError:
        await message.answer('❌ 输入有效号码')


async def handle_edit_value(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()
    promo_id = data.get('editing_promo_id')
    edit_action = data.get('edit_action')

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await message.answer('❌ 未找到促销代码')
        await state.clear()
        return

    try:
        value = int(message.text.strip())

        if edit_action == 'amount':
            if value < 1 or value > 10000:
                await message.answer('❌ 金额必须在 1 至 10,000 卢布之间')
                return

            await update_promocode(db, promo, balance_bonus_kopeks=value * 100)
            await message.answer(
                f'✅ 奖金金额改为{value}₽',
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [types.InlineKeyboardButton(text='🎫 获取促销代码', callback_data=f'promo_manage_{promo_id}')]
                    ]
                ),
            )

        elif edit_action == 'days':
            if value < 1 or value > 3650:
                await message.answer('❌ 天数必须在 1 到 3650 之间')
                return

            await update_promocode(db, promo, subscription_days=value)
            await message.answer(
                f'✅ 天数改为{value}',
                reply_markup=types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [types.InlineKeyboardButton(text='🎫 获取促销代码', callback_data=f'promo_manage_{promo_id}')]
                    ]
                ),
            )

        await state.clear()
        logger.info(
            '促销代码已由管理员编辑',
            code=promo.code,
            telegram_id=db_user.telegram_id,
            edit_action=edit_action,
            value=value,
        )

    except ValueError:
        await message.answer('❌ 输入有效号码')


@admin_required
@error_handler
async def process_promocode_uses(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()

    if data.get('editing_promo_id'):
        await handle_edit_uses(message, db_user, state, db)
        return

    try:
        max_uses = int(message.text.strip())

        if max_uses < 0 or max_uses > 100000:
            await message.answer('❌ 使用次数必须在 0 到 100,000 之间')
            return

        if max_uses == 0:
            max_uses = 999999

        await state.update_data(promocode_max_uses=max_uses)

        await message.answer('⏰ 输入促销代码的有效期（以天为单位）（或 0 表示无限制）：')
        await state.set_state(AdminStates.setting_promocode_expiry)

    except ValueError:
        await message.answer('❌ 输入有效号码')


async def handle_edit_uses(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()
    promo_id = data.get('editing_promo_id')

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await message.answer('❌ 未找到促销代码')
        await state.clear()
        return

    try:
        max_uses = int(message.text.strip())

        if max_uses < 0 or max_uses > 100000:
            await message.answer('❌ 使用次数必须在 0 到 100,000 之间')
            return

        if max_uses == 0:
            max_uses = 999999

        if max_uses < promo.current_uses:
            await message.answer(
                f'❌ 新限制（{max_uses}）不能小于当前使用量（{promo.current_uses}）'
            )
            return

        await update_promocode(db, promo, max_uses=max_uses)

        uses_text = 'безлимитное' if max_uses == 999999 else str(max_uses)
        await message.answer(
            f'✅ 最大使用次数改为{uses_text}',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🎫 获取促销代码', callback_data=f'promo_manage_{promo_id}')]
                ]
            ),
        )

        await state.clear()
        logger.info(
            '由管理员编辑的促销代码 max_uses',
            code=promo.code,
            telegram_id=db_user.telegram_id,
            max_uses=max_uses,
        )

    except ValueError:
        await message.answer('❌ 输入有效号码')


@admin_required
@error_handler
async def process_promocode_expiry(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()

    if data.get('editing_promo_id'):
        await handle_edit_expiry(message, db_user, state, db)
        return

    try:
        expiry_days = int(message.text.strip())

        if expiry_days < 0 or expiry_days > 3650:
            await message.answer('❌ 有效期必须为0至3650天')
            return

        code = data.get('promocode_code')
        promo_type = data.get('promocode_type')
        value = data.get('promocode_value', 0)
        max_uses = data.get('promocode_max_uses', 1)
        promo_group_id = data.get('promo_group_id')
        promo_group_name = data.get('promo_group_name')

        # Для DISCOUNT типа нужно дополнительно спросить срок действия скидки в часах
        if promo_type == 'discount':
            await state.update_data(promocode_expiry_days=expiry_days)
            await message.answer(
                f'⏰ <b>促销代码：</b> <code>{code}</code>\n\n输入折扣有效期（以小时为单位）（0-8760）：\n0 = 首次购买前无限制'
            )
            await state.set_state(AdminStates.setting_discount_hours)
            return

        valid_until = None
        if expiry_days > 0:
            valid_until = datetime.now(UTC) + timedelta(days=expiry_days)

        type_map = {
            'balance': PromoCodeType.BALANCE,
            'days': PromoCodeType.SUBSCRIPTION_DAYS,
            'trial': PromoCodeType.TRIAL_SUBSCRIPTION,
            'group': PromoCodeType.PROMO_GROUP,
        }

        promocode = await create_promocode(
            db=db,
            code=code,
            type=type_map[promo_type],
            balance_bonus_kopeks=value * 100 if promo_type == 'balance' else 0,
            subscription_days=value if promo_type in ['days', 'trial'] else 0,
            max_uses=max_uses,
            valid_until=valid_until,
            created_by=db_user.id,
            promo_group_id=promo_group_id if promo_type == 'group' else None,
        )

        type_names = {
            'balance': 'Пополнение баланса',
            'days': 'Дни подписки',
            'trial': 'Тестовая подписка',
            'group': 'Промогруппа',
        }

        summary_text = f"""
✅ <b>Промокод создан!</b>

🎫 <b>Код:</b> <code>{promocode.code}</code>
📝 <b>类型：</b> {type_names.get(promo_type)}
"""

        if promo_type == 'balance':
            summary_text += f'💰 <b>Сумма:</b> {settings.format_price(promocode.balance_bonus_kopeks)}\n'
        elif promo_type in ['days', 'trial']:
            summary_text += f'📅 <b>Дней:</b> {promocode.subscription_days}\n'
        elif promo_type == 'group' and promo_group_name:
            summary_text += f'🏷️ <b>Промогруппа:</b> {promo_group_name}\n'

        summary_text += f'📊 <b>Использований:</b> {promocode.max_uses}\n'

        if promocode.valid_until:
            summary_text += f'⏰ <b>有效期至：</b> {format_datetime(promocode.valid_until)}\n'

        await message.answer(
            summary_text,
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='🎫 获取促销代码', callback_data='admin_promocodes')]]
            ),
        )

        await state.clear()
        logger.info('促销代码由管理员创建', code=code, telegram_id=db_user.telegram_id)

    except ValueError:
        await message.answer('❌ 输入正确的天数')


@admin_required
@error_handler
async def process_discount_hours(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    """Обработчик ввода срока действия скидки в часах для DISCOUNT промокода."""
    data = await state.get_data()

    try:
        discount_hours = int(message.text.strip())

        if discount_hours < 0 or discount_hours > 8760:
            await message.answer('❌ 折扣期必须为0至8760小时')
            return

        code = data.get('promocode_code')
        value = data.get('promocode_value', 0)  # Процент скидки
        max_uses = data.get('promocode_max_uses', 1)
        expiry_days = data.get('promocode_expiry_days', 0)

        valid_until = None
        if expiry_days > 0:
            valid_until = datetime.now(UTC) + timedelta(days=expiry_days)

        # Создаем DISCOUNT промокод
        # balance_bonus_kopeks = процент скидки (НЕ копейки!)
        # subscription_days = срок действия скидки в часах (НЕ дни!)
        promocode = await create_promocode(
            db=db,
            code=code,
            type=PromoCodeType.DISCOUNT,
            balance_bonus_kopeks=value,  # Процент (1-100)
            subscription_days=discount_hours,  # Часы (0-8760)
            max_uses=max_uses,
            valid_until=valid_until,
            created_by=db_user.id,
            promo_group_id=None,
        )

        summary_text = f"""
✅ <b>Промокод создан!</b>

🎫 <b>Код:</b> <code>{promocode.code}</code>
📝 <b>类型：</b> Одноразовая скидка
💸 <b>折扣：</b> {promocode.balance_bonus_kopeks}%
"""

        if discount_hours > 0:
            summary_text += f'⏰ <b>Срок скидки:</b> {discount_hours} ч.\n'
        else:
            summary_text += '⏰ <b>Срок скидки:</b> 到 первой покупки\n'

        summary_text += f'📊 <b>Использований:</b> {promocode.max_uses}\n'

        if promocode.valid_until:
            summary_text += f'⏳ <b>Промокод действует до:</b> {format_datetime(promocode.valid_until)}\n'

        await message.answer(
            summary_text,
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='🎫 获取促销代码', callback_data='admin_promocodes')]]
            ),
        )

        await state.clear()
        logger.info(
            '管理员已创建 DISCOUNT 促销代码 (%, h)',
            code=code,
            value=value,
            discount_hours=discount_hours,
            telegram_id=db_user.telegram_id,
        )

    except ValueError:
        await message.answer('❌ 输入正确的小时数')


async def handle_edit_expiry(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    data = await state.get_data()
    promo_id = data.get('editing_promo_id')

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await message.answer('❌ 未找到促销代码')
        await state.clear()
        return

    try:
        expiry_days = int(message.text.strip())

        if expiry_days < 0 or expiry_days > 3650:
            await message.answer('❌ 有效期必须为0至3650天')
            return

        valid_until = None
        if expiry_days > 0:
            valid_until = datetime.now(UTC) + timedelta(days=expiry_days)

        await update_promocode(db, promo, valid_until=valid_until)

        if valid_until:
            expiry_text = f'до {format_datetime(valid_until)}'
        else:
            expiry_text = 'бессрочно'

        await message.answer(
            f'✅ 促销代码的有效期已更改：{expiry_text}',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[
                    [types.InlineKeyboardButton(text='🎫 获取促销代码', callback_data=f'promo_manage_{promo_id}')]
                ]
            ),
        )

        await state.clear()
        logger.info(
            '促销代码由到期日管理员编辑',
            code=promo.code,
            telegram_id=db_user.telegram_id,
            expiry_days=expiry_days,
        )

    except ValueError:
        await message.answer('❌ 输入正确的天数')


@admin_required
@error_handler
async def toggle_promocode_status(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    promo_id = int(callback.data.split('_')[-1])

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer('❌ 未找到促销代码', show_alert=True)
        return

    new_status = not promo.is_active
    await update_promocode(db, promo, is_active=new_status)

    status_text = 'активирован' if new_status else 'деактивирован'
    await callback.answer(f'✅ 促销代码 {status_text}', show_alert=True)

    await show_promocode_management(callback, db_user, db)


@admin_required
@error_handler
async def toggle_promocode_first_purchase(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    """Переключает режим 'только для первой покупки'."""
    promo_id = int(callback.data.split('_')[-1])

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer('❌ 未找到促销代码', show_alert=True)
        return

    new_status = not getattr(promo, 'first_purchase_only', False)
    await update_promocode(db, promo, first_purchase_only=new_status)

    status_text = 'включён' if new_status else 'выключен'
    await callback.answer(f'✅ “首次购买”模式 {status_text}', show_alert=True)

    await show_promocode_management(callback, db_user, db)


@admin_required
@error_handler
async def confirm_delete_promocode(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 接收 ID 促销代码时出错', show_alert=True)
        return

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer('❌ 未找到促销代码', show_alert=True)
        return

    text = f"⚠️ <b>删除确认</b>\n\n您确定要删除优惠码 <code>{promo.code}</code> 吗？\n\n📊 <b>优惠码信息：</b>\n• 用途：{promo.current_uses}/{promo.max_uses}\n• 状态：{'启用' if promo.is_active else '停用'}\n\n<b>⚠️ 注意：</b> 此操作无法撤销！\n\nID: {promo_id}"

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(text='✅ 是，删除', callback_data=f'promo_delete_confirm_{promo.id}'),
                types.InlineKeyboardButton(text='❌ 取消', callback_data=f'promo_manage_{promo.id}'),
            ]
        ]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def delete_promocode_confirmed(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    try:
        promo_id = int(callback.data.split('_')[-1])
    except (ValueError, IndexError):
        await callback.answer('❌ 接收 ID 促销代码时出错', show_alert=True)
        return

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer('❌ 未找到促销代码', show_alert=True)
        return

    code = promo.code
    success = await delete_promocode(db, promo)

    if success:
        await callback.answer(f'✅ 促销代码 {code} 已删除', show_alert=True)
        await show_promocodes_list(callback, db_user, db)
    else:
        await callback.answer('❌ 删除促销代码时出错', show_alert=True)


@admin_required
@error_handler
async def show_promocode_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    promo_id = int(callback.data.split('_')[-1])

    promo = await get_promocode_by_id(db, promo_id)
    if not promo:
        await callback.answer('❌ 未找到促销代码', show_alert=True)
        return

    stats = await get_promocode_statistics(db, promo_id)

    text = f"📊 <b>促销代码统计</b> <code>{promo.code}</code>\n\n📈 <b>综合统计：</b>\n- 总使用量：{stats['total_uses']}\n- 今天使用：{stats['today_uses']}\n- 剩余用途：{promo.max_uses - promo.current_uses}\n\n📅 <b>最后使用：</b>"

    if stats['recent_uses']:
        for use in stats['recent_uses'][:5]:
            use_date = format_datetime(use.used_at)

            if hasattr(use, 'user_username') and use.user_username:
                user_display = f'@{html.escape(use.user_username)}'
            elif hasattr(use, 'user_full_name') and use.user_full_name:
                user_display = html.escape(use.user_full_name)
            elif hasattr(use, 'user_telegram_id'):
                user_display = f'ID{use.user_telegram_id}'
            else:
                user_display = f'ID{use.user_id}'

            text += f'- {use_date} | {user_display}\n'
    else:
        text += '- 尚未使用'

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text='⬅️ 返回', callback_data=f'promo_manage_{promo.id}')]]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def show_general_promocode_stats(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    total_codes = await get_promocodes_count(db)
    active_codes = await get_promocodes_count(db, is_active=True)

    text = f'📊 <b>促销代码总体统计</b>\n\n📈 <b> 主要指标：</b>\n- 促销代码总数：{total_codes}\n- 主动：{active_codes}\n- 无效：{total_codes - active_codes}\n\n如需详细统计信息，请从列表中选择特定的促销代码。'

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text='🎫 获取促销代码', callback_data='admin_promo_list')],
            [types.InlineKeyboardButton(text='⬅️ 返回', callback_data='admin_promocodes')],
        ]
    )

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_promocodes_menu, F.data == 'admin_promocodes')
    dp.callback_query.register(show_promocodes_list, F.data == 'admin_promo_list')
    dp.callback_query.register(show_promocodes_list_page, F.data.startswith('admin_promo_list_page_'))
    dp.callback_query.register(start_promocode_creation, F.data == 'admin_promo_create')
    dp.callback_query.register(select_promocode_type, F.data.startswith('promo_type_'))
    dp.callback_query.register(process_promo_group_selection, F.data.startswith('promo_select_group_'))

    dp.callback_query.register(show_promocode_management, F.data.startswith('promo_manage_'))
    dp.callback_query.register(toggle_promocode_first_purchase, F.data.startswith('promo_toggle_first_'))
    dp.callback_query.register(toggle_promocode_status, F.data.startswith('promo_toggle_'))
    dp.callback_query.register(show_promocode_stats, F.data.startswith('promo_stats_'))

    dp.callback_query.register(start_edit_promocode_date, F.data.startswith('promo_edit_date_'))
    dp.callback_query.register(start_edit_promocode_amount, F.data.startswith('promo_edit_amount_'))
    dp.callback_query.register(start_edit_promocode_days, F.data.startswith('promo_edit_days_'))
    dp.callback_query.register(start_edit_promocode_uses, F.data.startswith('promo_edit_uses_'))
    dp.callback_query.register(show_general_promocode_stats, F.data == 'admin_promo_general_stats')

    dp.callback_query.register(show_promocode_edit_menu, F.data.regexp(r'^promo_edit_\d+$'))

    dp.callback_query.register(delete_promocode_confirmed, F.data.startswith('promo_delete_confirm_'))
    dp.callback_query.register(confirm_delete_promocode, F.data.startswith('promo_delete_'))

    dp.message.register(process_promocode_code, AdminStates.creating_promocode)
    dp.message.register(process_promocode_value, AdminStates.setting_promocode_value)
    dp.message.register(process_promocode_uses, AdminStates.setting_promocode_uses)
    dp.message.register(process_promocode_expiry, AdminStates.setting_promocode_expiry)
    dp.message.register(process_discount_hours, AdminStates.setting_discount_hours)



