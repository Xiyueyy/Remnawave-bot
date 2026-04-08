"""Shared formatting utilities for traffic, price, and period display."""

import html


def safe_html_name(name: str | None) -> str:
    """HTML-escape a display name for Telegram HTML messages."""
    return html.escape(name or '')


def user_html_link(user) -> str:
    """Build an HTML-safe clickable user link for Telegram messages."""
    safe = safe_html_name(user.full_name)
    if getattr(user, 'telegram_id', None):
        return f'<a href="tg://user?id={user.telegram_id}">{safe}</a>'
    return f'<b>{safe}</b>'


def format_traffic(gb: int) -> str:
    """Format traffic amount for user-facing messages."""
    if gb == 0:
        return '∞（不限）'
    return f'{gb} GB'


def format_price_kopeks(kopeks: int, compact: bool = False) -> str:
    """Format price from kopeks to rubles."""
    rubles = kopeks / 100
    if compact:
        return f'{int(round(rubles))} ₽'
    if rubles == int(rubles):
        return f'{int(rubles)} ₽'
    return f'{rubles:.2f} ₽'


def format_period(days: int) -> str:
    """Format period in days."""
    return f'{days} 天'
