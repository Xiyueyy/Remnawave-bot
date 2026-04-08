"""Locale resolution utilities for multi-locale landing page text fields."""

from app.config import settings

SUPPORTED_LOCALES: tuple[str, ...] = ('zh',)
DEFAULT_LOCALE: str = ((getattr(settings, 'DEFAULT_LANGUAGE', 'zh') or 'zh').split('-')[0].lower() or 'zh')


def resolve_locale_text(data: dict[str, str] | str | None, lang: str = DEFAULT_LOCALE) -> str:
    """Resolve a localized text dict to a single string for the given language.

    Fallback chain: requested lang -> DEFAULT_LOCALE -> 'zh' -> 'ru' -> 'en' -> first available value -> ''.
    Accepts plain strings for backward compatibility with pre-migration data.
    """
    if data is None:
        return ''
    if isinstance(data, str):
        return data
    requested_lang = (lang or DEFAULT_LOCALE).split('-')[0].lower()
    return (
        data.get(requested_lang)
        or data.get(DEFAULT_LOCALE)
        or data.get('zh')
        or data.get('ru')
        or data.get('en')
        or next(iter(data.values()), '')
    )


def ensure_locale_dict(value: dict[str, str] | str | None) -> dict[str, str]:
    """Coerce a value to a locale dict. Plain strings become ``{DEFAULT_LOCALE: value}``."""
    if value is None:
        return {}
    if isinstance(value, str):
        return {DEFAULT_LOCALE: value} if value else {}
    return value


def validate_locale_dict(
    value: dict[str, str],
    *,
    max_length: int | None = None,
    field_name: str = 'field',
) -> dict[str, str]:
    """Validate that all keys are supported locales and values respect length limits."""
    for locale, text in value.items():
        if locale not in SUPPORTED_LOCALES:
            raise ValueError(f'Unsupported locale "{locale}" in {field_name}. Allowed: {", ".join(SUPPORTED_LOCALES)}')
        if not isinstance(text, str):
            raise ValueError(f'{field_name}[{locale}] must be a string')
        if max_length is not None and len(text) > max_length:
            raise ValueError(f'{field_name}[{locale}] exceeds max length {max_length} (got {len(text)})')
    return value
