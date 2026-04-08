from __future__ import annotations

import html


_DISPLAY_NAME_ALIASES = {
    'Стандартный': '标准套餐',
    'Базовый юзер': '基础用户',
}


def localize_display_name(value: str | None) -> str:
    if not value:
        return ''
    return _DISPLAY_NAME_ALIASES.get(value, value)


def escape_display_name(value: str | None) -> str:
    return html.escape(localize_display_name(value))


def format_tariff_label(value: str | None, *, quoted: bool = False, bold: bool = False) -> str:
    display_name = escape_display_name(value)
    if not display_name:
        return ''
    if quoted:
        display_name = f'「{display_name}」'
    label = '<b>套餐：</b>' if bold else '套餐：'
    return f'📦 {label} {display_name}'
