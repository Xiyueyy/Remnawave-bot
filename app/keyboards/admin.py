from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.localization.texts import get_texts


def _t(texts, key: str, default: str) -> str:
    """Helper for localized button labels with fallbacks."""
    return texts.t(key, default)


def get_admin_main_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAIN_USERS_SUBSCRIPTIONS', '👥用户/订阅'),
                    callback_data='admin_submenu_users',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAIN_SERVERS', '🌐服务器'),
                    callback_data='admin_servers',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAIN_TARIFFS', '📦套餐'),
                    callback_data='admin_tariffs',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAIN_PRICING', '💰价格'),
                    callback_data='admin_pricing',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAIN_PROMO_STATS', '💰优惠码/统计'),
                    callback_data='admin_submenu_promo',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAIN_SUPPORT', '🛟支持'),
                    callback_data='admin_submenu_support',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAIN_MESSAGES', '📨消息'),
                    callback_data='admin_submenu_communications',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAIN_SETTINGS', '⚙️设置'),
                    callback_data='admin_submenu_settings',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAIN_SYSTEM', '🛠️系统'),
                    callback_data='admin_submenu_system',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAIN_TRIALS', '🧪试用'),
                    callback_data='admin_trials',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAIN_PAYMENTS', '💳充值'),
                    callback_data='admin_payments',
                ),
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='back_to_menu')],
        ]
    )


def get_admin_users_submenu_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.ADMIN_USERS, callback_data='admin_users'),
                InlineKeyboardButton(text=texts.ADMIN_REFERRALS, callback_data='admin_referrals'),
            ],
            [InlineKeyboardButton(text=texts.ADMIN_SUBSCRIPTIONS, callback_data='admin_subscriptions')],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')],
        ]
    )


def get_admin_promo_submenu_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.ADMIN_PROMOCODES, callback_data='admin_promocodes'),
                InlineKeyboardButton(text=texts.ADMIN_STATISTICS, callback_data='admin_statistics'),
            ],
            [InlineKeyboardButton(text=texts.ADMIN_CAMPAIGNS, callback_data='admin_campaigns')],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CONTESTS', '🏆 竞赛'),
                    callback_data='admin_contests',
                )
            ],
            [InlineKeyboardButton(text=texts.ADMIN_PROMO_GROUPS, callback_data='admin_promo_groups')],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')],
        ]
    )


def get_admin_communications_submenu_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.ADMIN_MESSAGES, callback_data='admin_messages')],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_COMMUNICATIONS_POLLS', '🗳️民意调查'),
                    callback_data='admin_polls',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_COMMUNICATIONS_PROMO_OFFERS', '🎯促销优惠'),
                    callback_data='admin_promo_offers',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_COMMUNICATIONS_WELCOME_TEXT', '👋欢迎文本'),
                    callback_data='welcome_text_panel',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_COMMUNICATIONS_MENU_MESSAGES', '📢菜单消息'),
                    callback_data='user_messages_panel',
                ),
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')],
        ]
    )


def get_admin_support_submenu_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SUPPORT_TICKETS', '🎫支持工单'), callback_data='admin_tickets'
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SUPPORT_AUDIT', '🧾版主审计'), callback_data='admin_support_audit'
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SUPPORT_SETTINGS', '🛟支持设置'),
                    callback_data='admin_support_settings',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')],
        ]
    )


def get_admin_settings_submenu_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.ADMIN_REMNAWAVE, callback_data='admin_remnawave'),
                InlineKeyboardButton(text=texts.ADMIN_MONITORING, callback_data='admin_monitoring'),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SETTINGS_BOT_CONFIG', '🧩机器人配置'),
                    callback_data='admin_bot_config',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=texts.t('ADMIN_MONITORING_SETTINGS', '⚙️监控设置'),
                    callback_data='admin_mon_settings',
                )
            ],
            [
                InlineKeyboardButton(text=texts.ADMIN_RULES, callback_data='admin_rules'),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SETTINGS_MAINTENANCE', '🔧维护'), callback_data='maintenance_panel'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SETTINGS_PRIVACY_POLICY', '🛡️隐私政策'),
                    callback_data='admin_privacy_policy',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SETTINGS_PUBLIC_OFFER', '📄公开服务条款'),
                    callback_data='admin_public_offer',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SETTINGS_FAQ', '❓常见问题'),
                    callback_data='admin_faq',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SETTINGS_REQUIRED_CHANNELS', '📢必订频道'),
                    callback_data='reqch:list',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SETTINGS_APP_CONFIG', '📱 应用配置'),
                    callback_data='admin_remna_config',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')],
        ]
    )


def get_admin_system_submenu_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SYSTEM_UPDATES', '📄更新'), callback_data='admin_updates'
                ),
                InlineKeyboardButton(text=_t(texts, 'ADMIN_SYSTEM_BACKUPS', '🗄️备份'), callback_data='backup_panel'),
            ],
            [InlineKeyboardButton(text=_t(texts, 'ADMIN_SYSTEM_LOGS', '🧾日志'), callback_data='admin_system_logs')],
            [InlineKeyboardButton(text=texts.t('ADMIN_REPORTS', '📊报告'), callback_data='admin_reports')],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')],
        ]
    )


def get_admin_trials_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_TRIALS_RESET_BUTTON', '♻️ 重置所有试用'),
                    callback_data='admin_trials_reset',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')],
        ]
    )


def get_admin_reports_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_REPORTS_PREVIOUS_DAY', '📆昨天'), callback_data='admin_reports_daily'
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_REPORTS_LAST_WEEK', '🗓️本周'), callback_data='admin_reports_weekly'
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_REPORTS_LAST_MONTH', '📅本月'), callback_data='admin_reports_monthly'
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_panel')],
        ]
    )


def get_admin_report_result_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=texts.t('REPORT_CLOSE', '❌关闭'), callback_data='admin_close_report')]
        ]
    )


def get_admin_users_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USERS_ALL', '👥所有用户'), callback_data='admin_users_list'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USERS_SEARCH', '🔍搜索'), callback_data='admin_users_search'
                ),
            ],
            [
                InlineKeyboardButton(text=texts.ADMIN_STATISTICS, callback_data='admin_users_stats'),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USERS_INACTIVE', '🗑️不活跃'), callback_data='admin_users_inactive'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USERS_FILTERS', '⚙️筛选器'), callback_data='admin_users_filters'
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USERS_BLACKLIST', '🔐 黑名单'),
                    callback_data='admin_blacklist_settings',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USERS_BULK_BAN', '🛑 批量封禁'), callback_data='admin_bulk_ban_start'
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USERS_BLOCKED_CHECK', '🔒 已屏蔽机器人的用户'),
                    callback_data='admin_blocked_users',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_users')],
        ]
    )


def get_admin_users_filters_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USERS_FILTER_BALANCE', '💰按余额'),
                    callback_data='admin_users_balance_filter',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USERS_FILTER_RENEW_READY', '♻️准备续费'),
                    callback_data='admin_users_ready_to_renew_filter',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USERS_FILTER_POTENTIAL_CUSTOMERS', '💰 潜在客户'),
                    callback_data='admin_users_potential_customers_filter',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USERS_FILTER_CAMPAIGN', '📢按活动'),
                    callback_data='admin_users_campaign_filter',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_users')],
        ]
    )


def get_admin_subscriptions_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SUBSCRIPTIONS_ALL', '📱所有订阅'), callback_data='admin_subs_list'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SUBSCRIPTIONS_EXPIRING', '⏰即将过期'), callback_data='admin_subs_expiring'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SUBSCRIPTIONS_COUNTRIES', '🌍国家管理'),
                    callback_data='admin_subs_countries',
                )
            ],
            [InlineKeyboardButton(text=texts.ADMIN_STATISTICS, callback_data='admin_subs_stats')],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_users')],
        ]
    )


def get_admin_promocodes_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODES_ALL', '🎫所有优惠码'), callback_data='admin_promo_list'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODES_CREATE', '➕创建'), callback_data='admin_promo_create'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODES_GENERAL_STATS', '📊总体统计'),
                    callback_data='admin_promo_general_stats',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_promo')],
        ]
    )


def get_admin_campaigns_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CAMPAIGNS_LIST', '📋活动列表'), callback_data='admin_campaigns_list'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CAMPAIGNS_CREATE', '➕创建'), callback_data='admin_campaigns_create'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CAMPAIGNS_GENERAL_STATS', '📊总体统计'),
                    callback_data='admin_campaigns_stats',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_promo')],
        ]
    )


def get_admin_contests_root_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CONTESTS_REFERRAL', '🤝 邀请竞赛'),
                    callback_data='admin_contests_referral',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CONTESTS_DAILY', '📆 每日竞赛'),
                    callback_data='admin_contests_daily',
                )
            ],
            [
                InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_promo'),
            ],
        ]
    )


def get_admin_contests_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CONTESTS_LIST', '📋 当前竞赛'),
                    callback_data='admin_contests_list',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CONTESTS_CREATE', '➕ 新建竞赛'),
                    callback_data='admin_contests_create',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=texts.BACK,
                    callback_data='admin_contests',
                )
            ],
        ]
    )


def get_contest_mode_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CONTEST_MODE_PAID', '💳 邀请用户购买'),
                    callback_data='admin_contest_mode_paid',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CONTEST_MODE_REGISTERED', '🧑\u200d🤝\u200d🧑 仅注册即可'),
                    callback_data='admin_contest_mode_registered',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_contests_referral')],
        ]
    )


def get_daily_contest_manage_keyboard(
    template_id: int,
    is_enabled: bool,
    language: str = 'ru',
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    toggle_text = (
        _t(texts, 'ADMIN_CONTEST_DISABLE', '⏸️ 暂停')
        if is_enabled
        else _t(texts, 'ADMIN_CONTEST_ENABLE', '▶️ 开始')
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=toggle_text, callback_data=f'admin_daily_toggle_{template_id}'),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CONTEST_START_NOW', '立即开始回合'),
                    callback_data=f'admin_daily_start_{template_id}',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CONTEST_START_MANUAL', '🧪 手动启动'),
                    callback_data=f'admin_daily_manual_{template_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_EDIT_PRIZE_TYPE', '🏅 奖品类型'),
                    callback_data=f'admin_daily_edit_{template_id}_prize_type',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_EDIT_PRIZE_VALUE', '💰 奖品数值'),
                    callback_data=f'admin_daily_edit_{template_id}_prize_value',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_EDIT_MAX_WINNERS', '👥 获胜者数量'),
                    callback_data=f'admin_daily_edit_{template_id}_max_winners',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_EDIT_ATTEMPTS', '🔁 尝试次数'),
                    callback_data=f'admin_daily_edit_{template_id}_attempts_per_user',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_EDIT_TIMES', '⏰ 每日回合数'),
                    callback_data=f'admin_daily_edit_{template_id}_times_per_day',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_EDIT_SCHEDULE', '🕒 日程'),
                    callback_data=f'admin_daily_edit_{template_id}_schedule_times',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_EDIT_COOLDOWN', '⌛ 持续时间'),
                    callback_data=f'admin_daily_edit_{template_id}_cooldown_hours',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_EDIT_PAYLOAD', '🧩 附加数据'),
                    callback_data=f'admin_daily_payload_{template_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_RESET_ATTEMPTS', '🔄 重置尝试次数'),
                    callback_data=f'admin_daily_reset_attempts_{template_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CLOSE_ROUND', '❌ 结束回合'),
                    callback_data=f'admin_daily_close_{template_id}',
                ),
            ],
            [
                InlineKeyboardButton(text=texts.BACK, callback_data='admin_contests_daily'),
            ],
        ]
    )


def get_referral_contest_manage_keyboard(
    contest_id: int,
    *,
    is_active: bool,
    can_delete: bool = False,
    language: str = 'ru',
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    toggle_text = (
        _t(texts, 'ADMIN_CONTEST_DISABLE', '⏸️ 暂停')
        if is_active
        else _t(texts, 'ADMIN_CONTEST_ENABLE', '▶️ 开始')
    )

    rows = [
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_CONTEST_LEADERBOARD', '📊 排行榜'),
                callback_data=f'admin_contest_leaderboard_{contest_id}',
            ),
            InlineKeyboardButton(
                text=toggle_text,
                callback_data=f'admin_contest_toggle_{contest_id}',
            ),
        ],
        [
            InlineKeyboardButton(
                text='📈 详细统计',
                callback_data=f'admin_contest_detailed_stats_{contest_id}',
            ),
        ],
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_CONTEST_EDIT_SUMMARY_TIMES', '🕒 每日汇总时间'),
                callback_data=f'admin_contest_edit_times_{contest_id}',
            ),
        ],
        [
            InlineKeyboardButton(
                text='👻 虚拟用户',
                callback_data=f'admin_contest_vp_{contest_id}',
            ),
        ],
        [
            InlineKeyboardButton(
                text='🔄 同步',
                callback_data=f'admin_contest_sync_{contest_id}',
            ),
            InlineKeyboardButton(
                text='🔍 调试',
                callback_data=f'admin_contest_debug_{contest_id}',
            ),
        ],
    ]

    if can_delete:
        rows.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CONTEST_DELETE', '🗑 删除'),
                    callback_data=f'admin_contest_delete_{contest_id}',
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_BACK_TO_LIST', '⬅️返回列表'),
                callback_data='admin_contests_list',
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_campaign_management_keyboard(campaign_id: int, is_active: bool, language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)
    status_text = (
        _t(texts, 'ADMIN_CAMPAIGN_DISABLE', '🔴禁用')
        if is_active
        else _t(texts, 'ADMIN_CAMPAIGN_ENABLE', '🟢启用')
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CAMPAIGN_STATS', '📊统计'),
                    callback_data=f'admin_campaign_stats_{campaign_id}',
                ),
                InlineKeyboardButton(
                    text=status_text,
                    callback_data=f'admin_campaign_toggle_{campaign_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CAMPAIGN_EDIT', '✏️编辑'),
                    callback_data=f'admin_campaign_edit_{campaign_id}',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CAMPAIGN_DELETE', '🗑️删除'),
                    callback_data=f'admin_campaign_delete_{campaign_id}',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BACK_TO_LIST', '⬅️返回列表'), callback_data='admin_campaigns_list'
                )
            ],
        ]
    )


def get_campaign_edit_keyboard(
    campaign_id: int,
    *,
    bonus_type: str = None,
    is_balance_bonus: bool = None,  # deprecated, for backwards compatibility
    language: str = 'ru',
) -> InlineKeyboardMarkup:
    texts = get_texts(language)

    # Поддержка старого API
    if bonus_type is None and is_balance_bonus is not None:
        bonus_type = 'balance' if is_balance_bonus else 'subscription'

    keyboard: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_CAMPAIGN_EDIT_NAME', '✏️名称'),
                callback_data=f'admin_campaign_edit_name_{campaign_id}',
            ),
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_CAMPAIGN_EDIT_START', '🔗参数'),
                callback_data=f'admin_campaign_edit_start_{campaign_id}',
            ),
        ]
    ]

    if bonus_type == 'balance':
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CAMPAIGN_BONUS_BALANCE', '💰余额奖励'),
                    callback_data=f'admin_campaign_edit_balance_{campaign_id}',
                )
            ]
        )
    elif bonus_type == 'subscription':
        keyboard.extend(
            [
                [
                    InlineKeyboardButton(
                        text=_t(texts, 'ADMIN_CAMPAIGN_DURATION', '📅持续时间'),
                        callback_data=f'admin_campaign_edit_sub_days_{campaign_id}',
                    ),
                    InlineKeyboardButton(
                        text=_t(texts, 'ADMIN_CAMPAIGN_TRAFFIC', '🌐流量'),
                        callback_data=f'admin_campaign_edit_sub_traffic_{campaign_id}',
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=_t(texts, 'ADMIN_CAMPAIGN_DEVICES', '📱设备'),
                        callback_data=f'admin_campaign_edit_sub_devices_{campaign_id}',
                    ),
                    InlineKeyboardButton(
                        text=_t(texts, 'ADMIN_CAMPAIGN_SERVERS', '🌍服务器'),
                        callback_data=f'admin_campaign_edit_sub_servers_{campaign_id}',
                    ),
                ],
            ]
        )
    elif bonus_type == 'tariff':
        keyboard.extend(
            [
                [
                    InlineKeyboardButton(
                        text=_t(texts, 'ADMIN_CAMPAIGN_TARIFF', '🎁 套餐'),
                        callback_data=f'admin_campaign_edit_tariff_{campaign_id}',
                    ),
                    InlineKeyboardButton(
                        text=_t(texts, 'ADMIN_CAMPAIGN_DURATION', '📅持续时间'),
                        callback_data=f'admin_campaign_edit_tariff_days_{campaign_id}',
                    ),
                ],
            ]
        )
    # bonus_type == "none" - только базовые кнопки (название и параметр)

    keyboard.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_campaign_manage_{campaign_id}')])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_campaign_bonus_type_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CAMPAIGN_BONUS_BALANCE', '💰余额奖励'),
                    callback_data='campaign_bonus_balance',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CAMPAIGN_BONUS_SUBSCRIPTION', '📱订阅奖励'),
                    callback_data='campaign_bonus_subscription',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CAMPAIGN_BONUS_TARIFF', '🎁 套餐'), callback_data='campaign_bonus_tariff'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CAMPAIGN_BONUS_NONE', '🔗 仅链接'), callback_data='campaign_bonus_none'
                ),
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_campaigns')],
        ]
    )


def get_promocode_management_keyboard(promo_id: int, language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODE_EDIT', '✏️编辑'), callback_data=f'promo_edit_{promo_id}'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODE_TOGGLE', '🔄状态'), callback_data=f'promo_toggle_{promo_id}'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODE_STATS', '📊统计'), callback_data=f'promo_stats_{promo_id}'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODE_DELETE', '🗑️删除'), callback_data=f'promo_delete_{promo_id}'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BACK_TO_LIST', '⬅️返回列表'), callback_data='admin_promo_list'
                )
            ],
        ]
    )


def get_admin_messages_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MESSAGES_ALL_USERS', '📨所有用户'), callback_data='admin_msg_all'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MESSAGES_BY_SUBSCRIPTIONS', '🎯按订阅'),
                    callback_data='admin_msg_by_sub',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MESSAGES_BY_CRITERIA', '🔍按条件'), callback_data='admin_msg_custom'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MESSAGES_HISTORY', '📋历史记录'), callback_data='admin_msg_history'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PINNED_MESSAGE', '📌置顶消息'),
                    callback_data='admin_pinned_message',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_communications')],
        ]
    )


def get_pinned_message_keyboard(
    language: str = 'ru',
    send_before_menu: bool = True,
    send_on_every_start: bool = True,
) -> InlineKeyboardMarkup:
    texts = get_texts(language)

    position_label = (
        _t(texts, 'ADMIN_PINNED_POSITION_BEFORE', '⬆️菜单前发送')
        if send_before_menu
        else _t(texts, 'ADMIN_PINNED_POSITION_AFTER', '⬇️菜单后发送')
    )
    toggle_callback = 'admin_pinned_message_position'

    start_mode_label = (
        _t(texts, 'ADMIN_PINNED_START_EVERY_TIME', '🔁 每次 启动命令 时发送')
        if send_on_every_start
        else _t(texts, 'ADMIN_PINNED_START_ONCE', '🚫 仅发送一次')
    )
    start_mode_callback = 'admin_pinned_message_start_mode'

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PINNED_MESSAGE_UPDATE', '✏️更新'),
                    callback_data='admin_pinned_message_edit',
                )
            ],
            [
                InlineKeyboardButton(
                    text=position_label,
                    callback_data=toggle_callback,
                )
            ],
            [
                InlineKeyboardButton(
                    text=start_mode_label,
                    callback_data=start_mode_callback,
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PINNED_MESSAGE_DELETE', '🗑️删除并停用'),
                    callback_data='admin_pinned_message_delete',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_messages')],
        ]
    )


def get_pinned_broadcast_confirm_keyboard(
    language: str = 'ru',
    pinned_message_id: int = 0,
) -> InlineKeyboardMarkup:
    """Клавиатура для выбора: разослать сейчас или только при /start."""
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PINNED_BROADCAST_NOW', '📨 立即广播给所有人'),
                    callback_data=f'admin_pinned_broadcast_now:{pinned_message_id}',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PINNED_BROADCAST_ON_START', '⏳ 仅在 启动命令 时'),
                    callback_data=f'admin_pinned_broadcast_skip:{pinned_message_id}',
                )
            ],
        ]
    )


def get_admin_monitoring_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_START', '▶️开始'), callback_data='admin_mon_start'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_STOP', '⏸️暂停'), callback_data='admin_mon_stop'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_STATUS', '📊状态'), callback_data='admin_mon_status'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_LOGS', '📋日志'), callback_data='admin_mon_logs'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_SETTINGS_BUTTON', '⚙️设置'),
                    callback_data='admin_mon_settings',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_settings')],
        ]
    )


def get_admin_remnawave_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_REMNAWAVE_SYSTEM_STATS', '📊系统统计'),
                    callback_data='admin_rw_system',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_REMNAWAVE_MANAGE_NODES', '🖥️节点管理'),
                    callback_data='admin_rw_nodes',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_REMNAWAVE_SYNC', '🔄同步'), callback_data='admin_rw_sync'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_REMNAWAVE_MANAGE_SQUADS', '🌐节点组管理'),
                    callback_data='admin_rw_squads',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_REMNAWAVE_MIGRATION', '🚚迁移'), callback_data='admin_rw_migration'
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_REMNAWAVE_TRAFFIC', '📈流量'), callback_data='admin_rw_traffic'
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_settings')],
        ]
    )


def get_admin_statistics_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_STATS_USERS', '👥用户'), callback_data='admin_stats_users'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_STATS_SUBSCRIPTIONS', '📱订阅'), callback_data='admin_stats_subs'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_STATS_REVENUE', '💰收入'), callback_data='admin_stats_revenue'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_STATS_REFERRALS', '🤝推荐计划'), callback_data='admin_stats_referrals'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_STATS_SUMMARY', '📊总体概览'), callback_data='admin_stats_summary'
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_promo')],
        ]
    )


def get_user_management_keyboard(
    user_id: int, user_status: str, language: str = 'ru', back_callback: str = 'admin_users_list'
) -> InlineKeyboardMarkup:
    texts = get_texts(language)

    keyboard = [
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_USER_BALANCE', '💰余额'), callback_data=f'admin_user_balance_{user_id}'
            ),
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_USER_SUBSCRIPTION_SETTINGS', '📱订阅和设置'),
                callback_data=f'admin_user_subscription_{user_id}',
            ),
        ],
        [
            InlineKeyboardButton(
                text=texts.ADMIN_USER_PROMO_GROUP_BUTTON, callback_data=f'admin_user_promo_group_{user_id}'
            )
        ],
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_USER_REFERRALS_BUTTON', '🤝推荐'),
                callback_data=f'admin_user_referrals_{user_id}',
            )
        ],
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_USER_STATISTICS', '📊统计'),
                callback_data=f'admin_user_statistics_{user_id}',
            )
        ],
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_USER_TRANSACTIONS', '📋交易'),
                callback_data=f'admin_user_transactions_{user_id}',
            )
        ],
    ]

    keyboard.append(
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_USER_SEND_MESSAGE', '✉️发送消息'),
                callback_data=f'admin_user_send_message_{user_id}',
            )
        ]
    )

    # Кнопка управления ограничениями
    keyboard.append(
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_USER_RESTRICTIONS', '⚠️ 限制'),
                callback_data=f'admin_user_restrictions_{user_id}',
            )
        ]
    )

    if user_status == 'active':
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USER_BLOCK', '🚫封锁'), callback_data=f'admin_user_block_{user_id}'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USER_DELETE', '🗑️删除'), callback_data=f'admin_user_delete_{user_id}'
                ),
            ]
        )
    elif user_status == 'blocked':
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USER_UNBLOCK', '✅解除封锁'),
                    callback_data=f'admin_user_unblock_{user_id}',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USER_DELETE', '🗑️删除'), callback_data=f'admin_user_delete_{user_id}'
                ),
            ]
        )
    elif user_status == 'deleted':
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_USER_ALREADY_DELETED', '❌用户已删除'), callback_data='noop'
                )
            ]
        )

    keyboard.append([InlineKeyboardButton(text=texts.BACK, callback_data=back_callback)])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_user_restrictions_keyboard(
    user_id: int, restriction_topup: bool, restriction_subscription: bool, language: str = 'ru'
) -> InlineKeyboardMarkup:
    """Клавиатура управления ограничениями пользователя."""
    texts = get_texts(language)

    keyboard = []

    # Toggle для ограничения пополнения
    topup_emoji = '🚫' if restriction_topup else '✅'
    topup_text = f'{topup_emoji} 充值'
    keyboard.append(
        [InlineKeyboardButton(text=topup_text, callback_data=f'admin_user_restriction_toggle_topup_{user_id}')]
    )

    # Toggle для ограничения подписки
    sub_emoji = '🚫' if restriction_subscription else '✅'
    sub_text = f'{sub_emoji} 续费/购买'
    keyboard.append([InlineKeyboardButton(text=sub_text, callback_data=f'admin_user_restriction_toggle_sub_{user_id}')])

    # Кнопка изменения причины
    keyboard.append(
        [InlineKeyboardButton(text='📝 修改原因', callback_data=f'admin_user_restriction_reason_{user_id}')]
    )

    # Кнопка снятия всех ограничений (если есть хотя бы одно)
    if restriction_topup or restriction_subscription:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text='🔓 清除全部限制', callback_data=f'admin_user_restriction_clear_{user_id}'
                )
            ]
        )

    # Кнопка назад
    keyboard.append([InlineKeyboardButton(text=texts.BACK, callback_data=f'admin_user_manage_{user_id}')])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_user_promo_group_keyboard(
    promo_groups: list[tuple[Any, int]],
    user_id: int,
    current_group_ids,  # Can be Optional[int] or List[int]
    language: str = 'ru',
) -> InlineKeyboardMarkup:
    texts = get_texts(language)

    # Ensure current_group_ids is a list
    if current_group_ids is None:
        current_group_ids = []
    elif isinstance(current_group_ids, int):
        current_group_ids = [current_group_ids]

    keyboard: list[list[InlineKeyboardButton]] = []

    for group, members_count in promo_groups:
        # Check if user has this group
        has_group = group.id in current_group_ids
        prefix = '✅' if has_group else '👥'
        count_text = f' ({members_count})' if members_count else ''
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f'{prefix} {group.name}{count_text}',
                    callback_data=f'admin_user_promo_group_toggle_{user_id}_{group.id}',
                )
            ]
        )

    keyboard.append(
        [InlineKeyboardButton(text=texts.ADMIN_USER_PROMO_GROUP_BACK, callback_data=f'admin_user_manage_{user_id}')]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_confirmation_keyboard(
    confirm_action: str, cancel_action: str = 'admin_panel', language: str = 'ru'
) -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.YES, callback_data=confirm_action),
                InlineKeyboardButton(text=texts.NO, callback_data=cancel_action),
            ]
        ]
    )


def get_promocode_type_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODE_TYPE_BALANCE', '💰余额'), callback_data='promo_type_balance'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODE_TYPE_DAYS', '📅订阅天数'), callback_data='promo_type_days'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODE_TYPE_TRIAL', '🎁试用'), callback_data='promo_type_trial'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODE_TYPE_PROMO_GROUP', '🏷️促销组'),
                    callback_data='promo_type_group',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODE_TYPE_DISCOUNT', '💸一次性折扣'),
                    callback_data='promo_type_discount',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_promocodes')],
        ]
    )


def get_promocode_list_keyboard(
    promocodes: list, page: int, total_pages: int, language: str = 'ru'
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []

    for promo in promocodes:
        status_emoji = '✅' if promo.is_active else '❌'
        type_emoji = {'balance': '💰', 'subscription_days': '📅', 'trial_subscription': '🎁'}.get(promo.type, '🎫')

        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f'{status_emoji} {type_emoji} {promo.code}', callback_data=f'promo_manage_{promo.id}'
                )
            ]
        )

    if total_pages > 1:
        pagination_row = []

        if page > 1:
            pagination_row.append(InlineKeyboardButton(text='⬅️', callback_data=f'admin_promo_list_page_{page - 1}'))

        pagination_row.append(InlineKeyboardButton(text=f'{page}/{total_pages}', callback_data='current_page'))

        if page < total_pages:
            pagination_row.append(InlineKeyboardButton(text='➡️', callback_data=f'admin_promo_list_page_{page + 1}'))

        keyboard.append(pagination_row)

    keyboard.extend(
        [
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PROMOCODES_CREATE', '➕创建'), callback_data='admin_promo_create'
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_promocodes')],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_broadcast_target_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_TARGET_ALL', '👥所有用户'), callback_data='broadcast_all'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_TARGET_ACTIVE', '📱拥有订阅'), callback_data='broadcast_active'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_TARGET_TRIAL', '🎁试用'), callback_data='broadcast_trial'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_TARGET_NO_SUB', '❌未订阅'), callback_data='broadcast_no_sub'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_TARGET_EXPIRING', '⏰即将过期'),
                    callback_data='broadcast_expiring',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_TARGET_EXPIRED', '🔚已过期'), callback_data='broadcast_expired'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_TARGET_ACTIVE_ZERO', '🧊活跃0GB'),
                    callback_data='broadcast_active_zero',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_TARGET_TRIAL_ZERO', '🥶试用0GB'),
                    callback_data='broadcast_trial_zero',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_TARGET_BY_TARIFF', '📦 按套餐'),
                    callback_data='broadcast_by_tariff',
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_messages')],
        ]
    )


def get_custom_criteria_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CRITERIA_TODAY', '📅今天'), callback_data='criteria_today'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CRITERIA_WEEK', '📅本周'), callback_data='criteria_week'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CRITERIA_MONTH', '📅本月'), callback_data='criteria_month'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CRITERIA_ACTIVE_TODAY', '⚡今日活跃'),
                    callback_data='criteria_active_today',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CRITERIA_INACTIVE_WEEK', '💤7天以上未活跃'),
                    callback_data='criteria_inactive_week',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CRITERIA_INACTIVE_MONTH', '💤30天以上未活跃'),
                    callback_data='criteria_inactive_month',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CRITERIA_REFERRALS', '🤝通过推荐'), callback_data='criteria_referrals'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CRITERIA_PROMOCODES', '🎫已使用优惠码'),
                    callback_data='criteria_promocodes',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_CRITERIA_DIRECT', '🎯直接注册'), callback_data='criteria_direct'
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_messages')],
        ]
    )


def get_broadcast_history_keyboard(page: int, total_pages: int, language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []

    if total_pages > 1:
        pagination_row = []

        if page > 1:
            pagination_row.append(InlineKeyboardButton(text='⬅️', callback_data=f'admin_msg_history_page_{page - 1}'))

        pagination_row.append(InlineKeyboardButton(text=f'{page}/{total_pages}', callback_data='current_page'))

        if page < total_pages:
            pagination_row.append(InlineKeyboardButton(text='➡️', callback_data=f'admin_msg_history_page_{page + 1}'))

        keyboard.append(pagination_row)

    keyboard.extend(
        [
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_HISTORY_REFRESH', '🔄刷新'), callback_data='admin_msg_history'
                )
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_messages')],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_sync_options_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = [
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_SYNC_FULL', '🔄完全同步'), callback_data='sync_all_users'
            )
        ],
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_SYNC_TO_PANEL', '⬆️ 同步到面板'), callback_data='sync_to_panel'
            )
        ],
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_SYNC_ONLY_NEW', '🆕仅限新的'), callback_data='sync_new_users'
            )
        ],
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_SYNC_UPDATE', '📈更新数据'), callback_data='sync_update_data'
            )
        ],
        [
            InlineKeyboardButton(text=_t(texts, 'ADMIN_SYNC_VALIDATE', '🔍验证'), callback_data='sync_validate'),
            InlineKeyboardButton(text=_t(texts, 'ADMIN_SYNC_CLEANUP', '🧹清理'), callback_data='sync_cleanup'),
        ],
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_SYNC_RECOMMENDATIONS', '💡建议'), callback_data='sync_recommendations'
            )
        ],
        [InlineKeyboardButton(text=texts.BACK, callback_data='admin_remnawave')],
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_sync_confirmation_keyboard(sync_type: str, language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = [
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_SYNC_CONFIRM', '✅确认'), callback_data=f'confirm_{sync_type}'
            )
        ],
        [InlineKeyboardButton(text=_t(texts, 'ADMIN_CANCEL', '❌取消'), callback_data='admin_rw_sync')],
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_sync_result_keyboard(sync_type: str, has_errors: bool = False, language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []

    if has_errors:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SYNC_RETRY', '🔄重试'), callback_data=f'sync_{sync_type}'
                )
            ]
        )

    if sync_type != 'all_users':
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SYNC_FULL', '🔄完全同步'), callback_data='sync_all_users'
                )
            ]
        )

    keyboard.extend(
        [
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_STATS_BUTTON', '📊统计'), callback_data='admin_rw_system'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SYNC_VALIDATE', '🔍验证'), callback_data='sync_validate'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SYNC_BACK', '⬅️返回同步'), callback_data='admin_rw_sync'
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BACK_TO_MAIN', '🏠返回主菜单'), callback_data='admin_remnawave'
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_period_selection_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=_t(texts, 'ADMIN_PERIOD_TODAY', '📅今天'), callback_data='period_today'),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_PERIOD_YESTERDAY', '📅昨天'), callback_data='period_yesterday'
                ),
            ],
            [
                InlineKeyboardButton(text=_t(texts, 'ADMIN_PERIOD_WEEK', '📅本周'), callback_data='period_week'),
                InlineKeyboardButton(text=_t(texts, 'ADMIN_PERIOD_MONTH', '📅本月'), callback_data='period_month'),
            ],
            [InlineKeyboardButton(text=_t(texts, 'ADMIN_PERIOD_ALL', '📅所有时间'), callback_data='period_all')],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_statistics')],
        ]
    )


def get_node_management_keyboard(node_uuid: str, language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_NODE_ENABLE', '▶️启用'), callback_data=f'node_enable_{node_uuid}'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_NODE_DISABLE', '⏸️禁用'), callback_data=f'node_disable_{node_uuid}'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_NODE_RESTART', '🔄重启'), callback_data=f'node_restart_{node_uuid}'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_NODE_STATS', '📊统计'), callback_data=f'node_stats_{node_uuid}'
                ),
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_rw_nodes')],
        ]
    )


def get_squad_management_keyboard(squad_uuid: str, language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SQUAD_ADD_ALL', '👥添加所有用户'),
                    callback_data=f'squad_add_users_{squad_uuid}',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SQUAD_REMOVE_ALL', '❌删除所有用户'),
                    callback_data=f'squad_remove_users_{squad_uuid}',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SQUAD_EDIT', '✏️编辑'), callback_data=f'squad_edit_{squad_uuid}'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SQUAD_DELETE', '🗑️删除squad'), callback_data=f'squad_delete_{squad_uuid}'
                ),
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_rw_squads')],
        ]
    )


def get_squad_edit_keyboard(squad_uuid: str, language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SQUAD_EDIT_INBOUNDS', '🔧编辑入站'),
                    callback_data=f'squad_edit_inbounds_{squad_uuid}',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SQUAD_RENAME', '✏️重命名'), callback_data=f'squad_rename_{squad_uuid}'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BACK_TO_SQUADS', '⬅️返回节点组'),
                    callback_data=f'admin_squad_manage_{squad_uuid}',
                )
            ],
        ]
    )


def get_monitoring_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_START', '▶️开始'), callback_data='admin_mon_start'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_STOP_HARD', '⏹️停止'), callback_data='admin_mon_stop'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_FORCE_CHECK', '🔄强制检查'),
                    callback_data='admin_mon_force_check',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_TRAFFIC_CHECK', '📊 流量检查'),
                    callback_data='admin_mon_traffic_check',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_LOGS', '📋日志'), callback_data='admin_mon_logs'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_STATISTICS', '📊统计'), callback_data='admin_mon_statistics'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_TEST_NOTIFICATIONS', '🧪测试通知'),
                    callback_data='admin_mon_test_notifications',
                ),
                InlineKeyboardButton(text='⚙️ 流量设置', callback_data='admin_mon_traffic_settings'),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BACK_TO_ADMIN', '⬅️返回后台管理'), callback_data='admin_panel'
                )
            ],
        ]
    )


def get_monitoring_logs_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_HISTORY_REFRESH', '🔄刷新'), callback_data='admin_mon_logs'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_CLEAR_OLD', '🗑️清理旧日志'),
                    callback_data='admin_mon_clear_logs',
                ),
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_monitoring')],
        ]
    )


def get_monitoring_logs_navigation_keyboard(
    current_page: int, total_pages: int, has_logs: bool = True, language: str = 'ru'
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []

    if total_pages > 1:
        nav_row = []

        if current_page > 1:
            nav_row.append(InlineKeyboardButton(text='⬅️', callback_data=f'admin_mon_logs_page_{current_page - 1}'))

        nav_row.append(InlineKeyboardButton(text=f'{current_page}/{total_pages}', callback_data='current_page_info'))

        if current_page < total_pages:
            nav_row.append(InlineKeyboardButton(text='➡️', callback_data=f'admin_mon_logs_page_{current_page + 1}'))

        keyboard.append(nav_row)

    management_row = []

    refresh_button = InlineKeyboardButton(
        text=_t(texts, 'ADMIN_HISTORY_REFRESH', '🔄刷新'), callback_data='admin_mon_logs'
    )

    if has_logs:
        management_row.extend(
            [
                refresh_button,
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_CLEAR', '🗑️清理'), callback_data='admin_mon_clear_logs'
                ),
            ]
        )
    else:
        management_row.append(refresh_button)

    keyboard.append(management_row)

    keyboard.append(
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_BACK_TO_MONITORING', '⬅️返回监控页面'), callback_data='admin_monitoring'
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_log_detail_keyboard(log_id: int, current_page: int = 1, language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_DELETE_LOG', '🗑️删除此日志'),
                    callback_data=f'admin_mon_delete_log_{log_id}',
                )
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_BACK_TO_LOGS', '⬅️返回日志列表'),
                    callback_data=f'admin_mon_logs_page_{current_page}',
                )
            ],
        ]
    )


def get_monitoring_clear_confirm_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_CONFIRM_CLEAR', '✅是，清理'),
                    callback_data='admin_mon_clear_logs_confirm',
                ),
                InlineKeyboardButton(text=_t(texts, 'ADMIN_CANCEL', '❌取消'), callback_data='admin_mon_logs'),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_CLEAR_ALL', '🗑️清理所有日志'),
                    callback_data='admin_mon_clear_all_logs',
                )
            ],
        ]
    )


def get_monitoring_status_keyboard(
    is_running: bool, last_check_ago_minutes: int = 0, language: str = 'ru'
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []

    control_row = []
    if is_running:
        control_row.extend(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_STOP_HARD', '⏹️停止'), callback_data='admin_mon_stop'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_RESTART', '🔄重启'), callback_data='admin_mon_restart'
                ),
            ]
        )
    else:
        control_row.append(
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_MONITORING_START', '▶️开始'), callback_data='admin_mon_start'
            )
        )

    keyboard.append(control_row)

    monitoring_row = []

    if not is_running or last_check_ago_minutes > 10:
        monitoring_row.append(
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_MONITORING_FORCE_CHECK', '🔄强制检查'),
                callback_data='admin_mon_force_check',
            )
        )
    else:
        monitoring_row.append(
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_MONITORING_CHECK_NOW', '🔄立即检查'),
                callback_data='admin_mon_force_check',
            )
        )

    keyboard.append(monitoring_row)

    info_row = [
        InlineKeyboardButton(text=_t(texts, 'ADMIN_MONITORING_LOGS', '📋日志'), callback_data='admin_mon_logs'),
        InlineKeyboardButton(
            text=_t(texts, 'ADMIN_MONITORING_STATISTICS', '📊统计'), callback_data='admin_mon_statistics'
        ),
    ]
    keyboard.append(info_row)

    test_row = [
        InlineKeyboardButton(
            text=_t(texts, 'ADMIN_MONITORING_TEST_NOTIFICATIONS', '🧪测试通知'),
            callback_data='admin_mon_test_notifications',
        )
    ]
    keyboard.append(test_row)

    keyboard.append([InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_settings')])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_monitoring_settings_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_SET_INTERVAL', '⏱️检查间隔'),
                    callback_data='admin_mon_set_interval',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_NOTIFICATIONS', '🔔通知'),
                    callback_data='admin_mon_toggle_notifications',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_AUTOPAY_SETTINGS', '💳自动支付设置'),
                    callback_data='admin_mon_autopay_settings',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_AUTO_CLEANUP', '🧹自动清理日志'),
                    callback_data='admin_mon_auto_cleanup',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BACK_TO_MONITORING', '⬅️返回监控页面'), callback_data='admin_monitoring'
                )
            ],
        ]
    )


def get_log_type_filter_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_FILTER_SUCCESS', '✅成功'),
                    callback_data='admin_mon_logs_filter_success',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_FILTER_ERRORS', '❌错误'),
                    callback_data='admin_mon_logs_filter_error',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_FILTER_CYCLES', '🔄监控周期'),
                    callback_data='admin_mon_logs_filter_cycle',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_FILTER_AUTOPAY', '💳自动支付'),
                    callback_data='admin_mon_logs_filter_autopay',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MONITORING_ALL_LOGS', '📋所有日志'), callback_data='admin_mon_logs'
                ),
                InlineKeyboardButton(text=texts.BACK, callback_data='admin_monitoring'),
            ],
        ]
    )


def get_admin_servers_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SERVERS_LIST', '📋服务器列表'), callback_data='admin_servers_list'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SERVERS_SYNC', '🔄同步'), callback_data='admin_servers_sync'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SERVERS_ADD', '➕添加服务器'), callback_data='admin_servers_add'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SERVERS_STATS', '📊统计'), callback_data='admin_servers_stats'
                ),
            ],
            [InlineKeyboardButton(text=texts.BACK, callback_data='admin_subscriptions')],
        ]
    )


def get_server_edit_keyboard(server_id: int, is_available: bool, language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)

    toggle_text = (
        _t(texts, 'ADMIN_SERVER_DISABLE', '❌禁用')
        if is_available
        else _t(texts, 'ADMIN_SERVER_ENABLE', '✅启用')
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SERVER_EDIT_NAME', '✏️名称'),
                    callback_data=f'admin_server_edit_name_{server_id}',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SERVER_EDIT_PRICE', '💰价格'),
                    callback_data=f'admin_server_edit_price_{server_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SERVER_EDIT_COUNTRY', '🌍国家'),
                    callback_data=f'admin_server_edit_country_{server_id}',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SERVER_EDIT_LIMIT', '👥限制'),
                    callback_data=f'admin_server_edit_limit_{server_id}',
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SERVER_EDIT_DESCRIPTION', '📝描述'),
                    callback_data=f'admin_server_edit_desc_{server_id}',
                )
            ],
            [InlineKeyboardButton(text=toggle_text, callback_data=f'admin_server_toggle_{server_id}')],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_SERVER_DELETE', '🗑️删除'), callback_data=f'admin_server_delete_{server_id}'
                ),
                InlineKeyboardButton(text=texts.BACK, callback_data='admin_servers_list'),
            ],
        ]
    )


def get_admin_pagination_keyboard(
    current_page: int, total_pages: int, callback_prefix: str, back_callback: str = 'admin_panel', language: str = 'ru'
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []

    if total_pages > 1:
        row = []

        if current_page > 1:
            row.append(InlineKeyboardButton(text='⬅️', callback_data=f'{callback_prefix}_page_{current_page - 1}'))

        row.append(InlineKeyboardButton(text=f'{current_page}/{total_pages}', callback_data='current_page'))

        if current_page < total_pages:
            row.append(InlineKeyboardButton(text='➡️', callback_data=f'{callback_prefix}_page_{current_page + 1}'))

        keyboard.append(row)

    keyboard.append([InlineKeyboardButton(text=texts.BACK, callback_data=back_callback)])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_maintenance_keyboard(
    language: str, is_maintenance_active: bool, is_monitoring_active: bool, panel_has_issues: bool = False
) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = []

    if is_maintenance_active:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAINTENANCE_DISABLE', '🟢关闭维护'),
                    callback_data='maintenance_toggle',
                )
            ]
        )
    else:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAINTENANCE_ENABLE', '🔧开启维护'),
                    callback_data='maintenance_toggle',
                )
            ]
        )

    if is_monitoring_active:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAINTENANCE_STOP_MONITORING', '⏹️停止监控'),
                    callback_data='maintenance_monitoring',
                )
            ]
        )
    else:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_MAINTENANCE_START_MONITORING', '▶️开始监控'),
                    callback_data='maintenance_monitoring',
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_MAINTENANCE_CHECK_API', '🔍检查接口'), callback_data='maintenance_check_api'
            ),
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_MAINTENANCE_PANEL_STATUS', '🌐面板状态')
                + ('⚠️' if panel_has_issues else ''),
                callback_data='maintenance_check_panel',
            ),
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_MAINTENANCE_SEND_NOTIFICATION', '📢发送通知'),
                callback_data='maintenance_manual_notify',
            )
        ]
    )

    keyboard.append(
        [
            InlineKeyboardButton(text=_t(texts, 'ADMIN_REFRESH', '🔄刷新'), callback_data='maintenance_panel'),
            InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_settings'),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_sync_simplified_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)
    keyboard = [
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_SYNC_FULL', '🔄完全同步'), callback_data='sync_all_users'
            )
        ],
        [InlineKeyboardButton(text=texts.BACK, callback_data='admin_remnawave')],
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_welcome_text_keyboard(language: str = 'ru', is_enabled: bool = True) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    toggle_text = (
        _t(texts, 'ADMIN_WELCOME_DISABLE', '🔴禁用')
        if is_enabled
        else _t(texts, 'ADMIN_WELCOME_ENABLE', '🟢启用')
    )
    toggle_callback = 'toggle_welcome_text'

    keyboard = [
        [InlineKeyboardButton(text=toggle_text, callback_data=toggle_callback)],
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_WELCOME_EDIT', '📝编辑文本'), callback_data='edit_welcome_text'
            ),
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_WELCOME_SHOW', '👁️显示当前'), callback_data='show_welcome_text'
            ),
        ],
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_WELCOME_PREVIEW', '👁️预览'), callback_data='preview_welcome_text'
            ),
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_WELCOME_RESET', '🔄重置'), callback_data='reset_welcome_text'
            ),
        ],
        [
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_WELCOME_HTML', '🏷️标记格式'), callback_data='show_formatting_help'
            ),
            InlineKeyboardButton(
                text=_t(texts, 'ADMIN_WELCOME_PLACEHOLDERS', '💡占位符'), callback_data='show_placeholders_help'
            ),
        ],
        [InlineKeyboardButton(text=texts.BACK, callback_data='admin_submenu_communications')],
    ]

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


DEFAULT_BROADCAST_BUTTONS = ('home',)

BROADCAST_BUTTONS = {
    'balance': {
        'default_text': '💰 充值余额',
        'text_key': 'ADMIN_BROADCAST_BUTTON_BALANCE',
        'callback': 'balance_topup',
    },
    'referrals': {
        'default_text': '🤝 邀请返利',
        'text_key': 'ADMIN_BROADCAST_BUTTON_REFERRALS',
        'callback': 'menu_referrals',
    },
    'promocode': {
        'default_text': '🎫 优惠码',
        'text_key': 'ADMIN_BROADCAST_BUTTON_PROMOCODE',
        'callback': 'menu_promocode',
    },
    'connect': {
        'default_text': '🔗 连接',
        'text_key': 'ADMIN_BROADCAST_BUTTON_CONNECT',
        'callback': 'subscription_connect',
    },
    'subscription': {
        'default_text': '📱 订阅',
        'text_key': 'ADMIN_BROADCAST_BUTTON_SUBSCRIPTION',
        'callback': 'menu_subscription',
    },
    'support': {
        'default_text': '🛠️ 技术支持',
        'text_key': 'ADMIN_BROADCAST_BUTTON_SUPPORT',
        'callback': 'menu_support',
    },
    'home': {
        'default_text': '🏠 返回首页',
        'text_key': 'ADMIN_BROADCAST_BUTTON_HOME',
        'callback': 'back_to_menu',
    },
}

BROADCAST_BUTTON_ROWS: tuple[tuple[str, ...], ...] = (
    ('balance', 'referrals'),
    ('promocode', 'connect'),
    ('subscription', 'support'),
    ('home',),
)


def get_broadcast_button_config(language: str) -> dict[str, dict[str, str]]:
    texts = get_texts(language)
    return {
        key: {
            'text': texts.t(config['text_key'], config['default_text']),
            'callback': config['callback'],
        }
        for key, config in BROADCAST_BUTTONS.items()
    }


def get_broadcast_button_labels(language: str) -> dict[str, str]:
    return {key: value['text'] for key, value in get_broadcast_button_config(language).items()}


def get_message_buttons_selector_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    return get_updated_message_buttons_selector_keyboard_with_media(list(DEFAULT_BROADCAST_BUTTONS), False, language)


def get_broadcast_media_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_ADD_PHOTO', '📷添加照片'), callback_data='add_media_photo'
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_ADD_VIDEO', '🎥添加视频'), callback_data='add_media_video'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_ADD_DOCUMENT', '📄添加文档'),
                    callback_data='add_media_document',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_SKIP_MEDIA', '⏭️跳过媒体'), callback_data='skip_media'
                ),
            ],
            [InlineKeyboardButton(text=_t(texts, 'ADMIN_CANCEL', '❌取消'), callback_data='admin_messages')],
        ]
    )


def get_media_confirm_keyboard(language: str = 'ru') -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_USE_MEDIA', '✅使用此媒体'),
                    callback_data='confirm_media',
                ),
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_REPLACE_MEDIA', '🔄替换媒体'), callback_data='replace_media'
                ),
            ],
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_NO_MEDIA', '⏭️不含媒体'), callback_data='skip_media'
                ),
                InlineKeyboardButton(text=_t(texts, 'ADMIN_CANCEL', '❌取消'), callback_data='admin_messages'),
            ],
        ]
    )


def get_updated_message_buttons_selector_keyboard_with_media(
    selected_buttons: list, has_media: bool = False, language: str = 'ru'
) -> InlineKeyboardMarkup:
    selected_buttons = selected_buttons or []

    texts = get_texts(language)
    button_config_map = get_broadcast_button_config(language)
    keyboard: list[list[InlineKeyboardButton]] = []

    for row in BROADCAST_BUTTON_ROWS:
        row_buttons: list[InlineKeyboardButton] = []
        for button_key in row:
            button_config = button_config_map[button_key]
            base_text = button_config['text']
            if button_key in selected_buttons:
                if ' ' in base_text:
                    toggle_text = f'✅ {base_text.split(" ", 1)[1]}'
                else:
                    toggle_text = f'✅ {base_text}'
            else:
                toggle_text = base_text
            row_buttons.append(InlineKeyboardButton(text=toggle_text, callback_data=f'btn_{button_key}'))
        if row_buttons:
            keyboard.append(row_buttons)

    if has_media:
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=_t(texts, 'ADMIN_BROADCAST_CHANGE_MEDIA', '🖼️更改媒体'), callback_data='change_media'
                )
            ]
        )

    keyboard.extend(
        [
            [InlineKeyboardButton(text=_t(texts, 'ADMIN_CONTINUE', '✅继续'), callback_data='buttons_confirm')],
            [InlineKeyboardButton(text=_t(texts, 'ADMIN_CANCEL', '❌取消'), callback_data='admin_messages')],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard)
