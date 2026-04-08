import json
from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.contest import (
    clear_attempts,
    create_round,
    get_template_by_id,
    list_templates,
    update_template_fields,
)
from app.database.models import ContestTemplate
from app.keyboards.admin import (
    get_daily_contest_manage_keyboard,
)
from app.localization.texts import get_texts
from app.services.contest_rotation_service import contest_rotation_service
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)

EDITABLE_FIELDS: dict[str, dict] = {
    'prize_type': {'type': str, 'label': '奖品类型（天/balance/custom）'},
    'prize_value': {'type': str, 'label': '奖金价值'},
    'max_winners': {'type': int, 'min': 1, 'label': '最大限度。获奖者'},
    'attempts_per_user': {'type': int, 'min': 1, 'label': '每个用户的尝试次数'},
    'times_per_day': {'type': int, 'min': 1, 'label': '每天轮数'},
    'schedule_times': {'type': str, 'label': '时间表 HH:MM 以逗号分隔'},
    'cooldown_hours': {'type': int, 'min': 1, 'label': '回合持续时间（小时）'},
}


async def _get_template(db: AsyncSession, template_id: int) -> ContestTemplate | None:
    return await get_template_by_id(db, template_id)


@admin_required
@error_handler
async def show_daily_contests(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    templates = await list_templates(db, enabled_only=False)

    lines = [texts.t('ADMIN_DAILY_CONTESTS_TITLE', '📆 每日竞赛')]
    if not templates:
        lines.append(texts.t('ADMIN_CONTESTS_EMPTY', '暂时还没有已创建的竞赛。'))
    else:
        for tpl in templates:
            status = '🟢' if tpl.is_enabled else '⚪️'
            prize_info = f'{tpl.prize_value} ({tpl.prize_type})' if tpl.prize_type else tpl.prize_value
            lines.append(f'{status} <b>{tpl.name}</b>（小号：{tpl.slug}）-奖品{prize_info}，最大{tpl.max_winners}')

    keyboard_rows = []
    if templates:
        keyboard_rows.append(
            [types.InlineKeyboardButton(text='❌ 关闭所有活跃回合', callback_data='admin_daily_close_all')]
        )
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text='� 重置所有活跃回合中的尝试', callback_data='admin_daily_reset_all_attempts'
                )
            ]
        )
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text='� 开展所有活跃的比赛', callback_data='admin_daily_start_all'
                )
            ]
        )
    for tpl in templates:
        keyboard_rows.append(
            [
                types.InlineKeyboardButton(
                    text=f'⚙️ {tpl.name}',
                    callback_data=f'admin_daily_contest_{tpl.id}',
                )
            ]
        )
    keyboard_rows.append([types.InlineKeyboardButton(text=texts.BACK, callback_data='admin_contests')])

    await callback.message.edit_text(
        '\n'.join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )
    await callback.answer()


@admin_required
@error_handler
async def show_daily_contest(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    try:
        template_id = int(callback.data.split('_')[-1])
    except Exception:
        await callback.answer('身份证号不正确', show_alert=True)
        return

    tpl = await _get_template(db, template_id)
    if not tpl:
        await callback.answer(texts.t('ADMIN_CONTEST_NOT_FOUND', '未找到竞赛。'), show_alert=True)
        return

    lines = [
        f'🏷 <b>{tpl.name}</b> (slug: {tpl.slug})',
        f'{texts.t("ADMIN_CONTEST_STATUS_ACTIVE", '🟢 已启用') if tpl.is_enabled else texts.t("ADMIN_CONTEST_STATUS_INACTIVE", '⚪️ 已停用')}',
        f"奖品类型：{tpl.prize_type or 'days'} |值：{tpl.prize_value or '1'}",
        f'最大获奖者：{tpl.max_winners}',
        f'尝试/用户：{tpl.attempts_per_user}',
        f'每天轮数：{tpl.times_per_day}',
        f"时间表：{tpl.schedule_times or '-'}",
        f'回合持续时间：{tpl.cooldown_hours} 小时。',
    ]
    await callback.message.edit_text(
        '\n'.join(lines),
        reply_markup=get_daily_contest_manage_keyboard(tpl.id, tpl.is_enabled, db_user.language),
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_daily_contest(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    template_id = int(callback.data.split('_')[-1])
    tpl = await _get_template(db, template_id)
    if not tpl:
        await callback.answer(texts.t('ADMIN_CONTEST_NOT_FOUND', '未找到竞赛。'), show_alert=True)
        return
    tpl.is_enabled = not tpl.is_enabled
    await db.commit()
    await callback.answer(texts.t('ADMIN_UPDATED', '已更新'))
    await show_daily_contest(callback, db_user, db)


@admin_required
@error_handler
async def start_round_now(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    template_id = int(callback.data.split('_')[-1])
    tpl = await _get_template(db, template_id)
    if not tpl:
        await callback.answer(texts.t('ADMIN_CONTEST_NOT_FOUND', '未找到竞赛。'), show_alert=True)
        return

    if not tpl.is_enabled:
        tpl.is_enabled = True
        await db.commit()
        await db.refresh(tpl)

    payload = contest_rotation_service._build_payload_for_template(tpl)  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    ends = now + timedelta(hours=tpl.cooldown_hours)
    await create_round(
        db,
        template=tpl,
        starts_at=now,
        ends_at=ends,
        payload=payload,
    )
    await contest_rotation_service._announce_round_start(  # type: ignore[attr-defined]
        tpl,
        now,
        ends,
    )
    await callback.answer(texts.t('ADMIN_ROUND_STARTED', '回合已启动'), show_alert=True)
    await show_daily_contest(callback, db_user, db)


@admin_required
@error_handler
async def manual_start_round(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    template_id = int(callback.data.split('_')[-1])
    tpl = await _get_template(db, template_id)
    if not tpl:
        await callback.answer(texts.t('ADMIN_CONTEST_NOT_FOUND', '未找到竞赛。'), show_alert=True)
        return

    # Проверяем, есть ли уже активный раунд для этого шаблона
    from app.database.crud.contest import get_active_round_by_template

    exists = await get_active_round_by_template(db, tpl.id)
    if exists:
        await callback.answer(texts.t('ADMIN_ROUND_ALREADY_ACTIVE', '该回合已经开始。'), show_alert=True)
        await show_daily_contest(callback, db_user, db)
        return

    # Для ручного старта не включаем конкурс, если он выключен
    payload = contest_rotation_service._build_payload_for_template(tpl)  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    ends = now + timedelta(hours=tpl.cooldown_hours)
    await create_round(
        db,
        template=tpl,
        starts_at=now,
        ends_at=ends,
        payload=payload,
    )

    # Анонсируем всем пользователям (как тест)
    await contest_rotation_service._announce_round_start(  # type: ignore[attr-defined]
        tpl,
        now,
        ends,
    )
    await callback.answer(texts.t('ADMIN_ROUND_STARTED', '回合已启动'), show_alert=True)
    await show_daily_contest(callback, db_user, db)


@admin_required
@error_handler
async def prompt_edit_field(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    parts = callback.data.split('_')
    template_id = int(parts[3])
    field = '_'.join(parts[4:])  # поле может содержать подчеркивания

    tpl = await _get_template(db, template_id)
    if not tpl or field not in EDITABLE_FIELDS:
        await callback.answer(texts.t('ADMIN_CONTEST_NOT_FOUND', '未找到竞赛。'), show_alert=True)
        return

    meta = EDITABLE_FIELDS[field]
    await state.set_state(AdminStates.editing_daily_contest_field)
    await state.update_data(template_id=template_id, field=field)
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.BACK,
                    callback_data=f'admin_daily_contest_{template_id}',
                )
            ]
        ]
    )
    await callback.message.edit_text(
        texts.t(
            'ADMIN_CONTEST_FIELD_PROMPT',
            '请输入 {label} 的新值：',
        ).format(label=meta.get('label', field)),
        reply_markup=kb,
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_field(
    message: types.Message,
    state: FSMContext,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    data = await state.get_data()
    template_id = data.get('template_id')
    field = data.get('field')
    if not template_id or not field or field not in EDITABLE_FIELDS:
        await message.answer(texts.ERROR)
        await state.clear()
        return

    tpl = await _get_template(db, template_id)
    if not tpl:
        await message.answer(texts.t('ADMIN_CONTEST_NOT_FOUND', '未找到竞赛。'))
        await state.clear()
        return

    meta = EDITABLE_FIELDS[field]
    raw = message.text or ''
    try:
        if meta['type'] is int:
            value = int(raw)
            if meta.get('min') is not None and value < meta['min']:
                raise ValueError('min')
        else:
            value = raw.strip()
    except Exception:
        await message.answer(texts.t('ADMIN_INVALID_NUMBER', '数字格式无效'))
        await state.clear()
        return

    await update_template_fields(db, tpl, **{field: value})
    back_kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.BACK,
                    callback_data=f'admin_daily_contest_{template_id}',
                )
            ]
        ]
    )
    await message.answer(texts.t('ADMIN_UPDATED', '已更新'), reply_markup=back_kb)
    await state.clear()


@admin_required
@error_handler
async def edit_payload(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
    state: FSMContext,
):
    texts = get_texts(db_user.language)
    template_id = int(callback.data.split('_')[-1])
    tpl = await _get_template(db, template_id)
    if not tpl:
        await callback.answer(texts.t('ADMIN_CONTEST_NOT_FOUND', '未找到竞赛。'), show_alert=True)
        return

    await state.set_state(AdminStates.editing_daily_contest_value)
    await state.update_data(template_id=template_id, field='payload')
    payload_json = json.dumps(tpl.payload or {}, ensure_ascii=False, indent=2)
    kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.BACK,
                    callback_data=f'admin_daily_contest_{template_id}',
                )
            ]
        ]
    )
    await callback.message.edit_text(
        texts.t('ADMIN_CONTEST_PAYLOAD_PROMPT', '发送游戏的 JSON 负载（设置字典）：')
        + f'<code>{payload_json}</code>',
        reply_markup=kb,
    )
    await callback.answer()


@admin_required
@error_handler
async def process_payload(
    message: types.Message,
    state: FSMContext,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    data = await state.get_data()
    template_id = data.get('template_id')
    if not template_id:
        await message.answer(texts.ERROR)
        await state.clear()
        return

    try:
        payload = json.loads(message.text or '{}')
        if not isinstance(payload, dict):
            raise ValueError
    except Exception:
        await message.answer(texts.t('ADMIN_INVALID_JSON', '数据格式无效'))
        await state.clear()
        return

    tpl = await _get_template(db, template_id)
    if not tpl:
        await message.answer(texts.t('ADMIN_CONTEST_NOT_FOUND', '未找到竞赛。'))
        await state.clear()
        return

    await update_template_fields(db, tpl, payload=payload)
    back_kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.BACK,
                    callback_data=f'admin_daily_contest_{template_id}',
                )
            ]
        ]
    )
    await message.answer(texts.t('ADMIN_UPDATED', '已更新'), reply_markup=back_kb)
    await state.clear()


@admin_required
@error_handler
async def start_all_contests(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    templates = await list_templates(db, enabled_only=True)
    if not templates:
        await callback.answer(texts.t('ADMIN_CONTESTS_EMPTY', '暂时还没有已创建的竞赛。'), show_alert=True)
        return

    started_count = 0
    for tpl in templates:
        from app.database.crud.contest import get_active_round_by_template

        exists = await get_active_round_by_template(db, tpl.id)
        if exists:
            continue  # уже запущен

        payload = contest_rotation_service._build_payload_for_template(tpl)  # type: ignore[attr-defined]
        now = datetime.now(UTC)
        ends = now + timedelta(hours=tpl.cooldown_hours)
        await create_round(
            db,
            template=tpl,
            starts_at=now,
            ends_at=ends,
            payload=payload,
        )
        await contest_rotation_service._announce_round_start(  # type: ignore[attr-defined]
            tpl,
            now,
            ends,
        )
        started_count += 1

    message = f'比赛启动：{started_count}'
    await callback.answer(message, show_alert=True)
    await show_daily_contests(callback, db_user, db)


@admin_required
@error_handler
async def close_all_rounds(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    get_texts(db_user.language)
    from app.database.crud.contest import get_active_rounds

    active_rounds = await get_active_rounds(db)
    if not active_rounds:
        await callback.answer('没有活跃回合', show_alert=True)
        return

    for rnd in active_rounds:
        rnd.status = 'finished'
    await db.commit()

    await callback.answer(f'封闭回合：{len(active_rounds)}', show_alert=True)
    await show_daily_contests(callback, db_user, db)


@admin_required
@error_handler
async def reset_all_attempts(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    get_texts(db_user.language)
    from app.database.crud.contest import get_active_rounds

    active_rounds = await get_active_rounds(db)
    if not active_rounds:
        await callback.answer('没有活跃回合', show_alert=True)
        return

    total_deleted = 0
    for rnd in active_rounds:
        deleted = await clear_attempts(db, rnd.id)
        total_deleted += deleted

    await callback.answer(f'尝试重置：{total_deleted}', show_alert=True)
    await show_daily_contests(callback, db_user, db)


@admin_required
@error_handler
async def reset_attempts(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    template_id = int(callback.data.split('_')[-1])
    tpl = await _get_template(db, template_id)
    if not tpl:
        await callback.answer(texts.t('ADMIN_CONTEST_NOT_FOUND', '未找到竞赛。'), show_alert=True)
        return

    from app.database.crud.contest import clear_attempts, get_active_round_by_template

    round_obj = await get_active_round_by_template(db, tpl.id)
    if not round_obj:
        await callback.answer('没有活跃回合', show_alert=True)
        return

    deleted_count = await clear_attempts(db, round_obj.id)
    await callback.answer(f'尝试重置：{deleted_count}', show_alert=True)
    await show_daily_contest(callback, db_user, db)


@admin_required
@error_handler
async def close_round(
    callback: types.CallbackQuery,
    db_user,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)
    template_id = int(callback.data.split('_')[-1])
    tpl = await _get_template(db, template_id)
    if not tpl:
        await callback.answer(texts.t('ADMIN_CONTEST_NOT_FOUND', '未找到竞赛。'), show_alert=True)
        return

    from app.database.crud.contest import get_active_round_by_template

    round_obj = await get_active_round_by_template(db, tpl.id)
    if not round_obj:
        await callback.answer('没有活跃回合', show_alert=True)
        return

    round_obj.status = 'finished'
    await db.commit()
    await db.refresh(round_obj)

    await callback.answer('本轮比赛已结束', show_alert=True)
    await show_daily_contest(callback, db_user, db)


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_daily_contests, F.data == 'admin_contests_daily')
    dp.callback_query.register(show_daily_contest, F.data.startswith('admin_daily_contest_'))
    dp.callback_query.register(toggle_daily_contest, F.data.startswith('admin_daily_toggle_'))
    dp.callback_query.register(start_all_contests, F.data == 'admin_daily_start_all')
    dp.callback_query.register(start_round_now, F.data.startswith('admin_daily_start_'))
    dp.callback_query.register(manual_start_round, F.data.startswith('admin_daily_manual_'))
    dp.callback_query.register(close_all_rounds, F.data == 'admin_daily_close_all')
    dp.callback_query.register(reset_all_attempts, F.data == 'admin_daily_reset_all_attempts')
    dp.callback_query.register(reset_attempts, F.data.startswith('admin_daily_reset_attempts_'))
    dp.callback_query.register(close_round, F.data.startswith('admin_daily_close_'))
    dp.callback_query.register(prompt_edit_field, F.data.startswith('admin_daily_edit_'))
    dp.callback_query.register(edit_payload, F.data.startswith('admin_daily_payload_'))

    dp.message.register(process_edit_field, AdminStates.editing_daily_contest_field)
    dp.message.register(process_payload, AdminStates.editing_daily_contest_value)
