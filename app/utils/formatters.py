from datetime import UTC, datetime


def _display_language_code(language: str | None) -> str:
    code = (language or 'zh').split('-')[0].lower()
    return 'zh' if code != 'zh' else code


def format_datetime(dt: datetime | str, format_str: str = '%d.%m.%Y %H:%M') -> str:
    if isinstance(dt, str):
        if dt == 'now' or dt == '':
            dt = datetime.now(UTC)
        else:
            try:
                dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                dt = datetime.now(UTC)

    return dt.strftime(format_str)


def format_date(dt: datetime | str, format_str: str = '%d.%m.%Y') -> str:
    if isinstance(dt, str):
        if dt == 'now' or dt == '':
            dt = datetime.now(UTC)
        else:
            try:
                dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                dt = datetime.now(UTC)

    return dt.strftime(format_str)


def format_time_ago(dt: datetime | str, language: str = 'zh') -> str:
    if isinstance(dt, str):
        if dt == 'now' or dt == '':
            dt = datetime.now(UTC)
        else:
            try:
                dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                dt = datetime.now(UTC)

    now = datetime.now(UTC)
    diff = now - dt

    language_code = _display_language_code(language)

    if diff.days > 0:
        if diff.days == 1:
            if language_code == 'zh':
                return '昨天'
            return 'yesterday' if language_code == 'en' else 'вчера'
        if diff.days < 7:
            value = diff.days
            if language_code == 'zh':
                return f'{value} 天前'
            if language_code == 'en':
                suffix = 'day' if value == 1 else 'days'
                return f'{value} {suffix} ago'
            return f'{value} дн. назад'
        if diff.days < 30:
            value = diff.days // 7
            if language_code == 'zh':
                return f'{value} 周前'
            if language_code == 'en':
                suffix = 'week' if value == 1 else 'weeks'
                return f'{value} {suffix} ago'
            return f'{value} нед. назад'
        if diff.days < 365:
            value = diff.days // 30
            if language_code == 'zh':
                return f'{value} 个月前'
            if language_code == 'en':
                suffix = 'month' if value == 1 else 'months'
                return f'{value} {suffix} ago'
            return f'{value} мес. назад'
        value = diff.days // 365
        if language_code == 'zh':
            return f'{value} 年前'
        if language_code == 'en':
            suffix = 'year' if value == 1 else 'years'
            return f'{value} {suffix} ago'
        return f'{value} г. назад'

    if diff.seconds > 3600:
        value = diff.seconds // 3600
        if language_code == 'zh':
            return f'{value} 小时前'
        if language_code == 'en':
            suffix = 'hour' if value == 1 else 'hours'
            return f'{value} {suffix} ago'
        return f'{value} ч. назад'

    if diff.seconds > 60:
        value = diff.seconds // 60
        if language_code == 'zh':
            return f'{value} 分钟前'
        if language_code == 'en':
            suffix = 'minute' if value == 1 else 'minutes'
            return f'{value} {suffix} ago'
        return f'{value} мин. назад'

    if language_code == 'zh':
        return '刚刚'
    return 'just now' if language_code == 'en' else 'только что'


def format_days_declension(days: int, language: str = 'zh') -> str:
    language_code = _display_language_code(language)
    if language_code == 'zh':
        return f'{days}天'
    if language_code not in {'ru', 'fa'}:
        return f'{days} day{"s" if days != 1 else ""}'

    if days % 10 == 1 and days % 100 != 11:
        return f'{days} день'
    if days % 10 in [2, 3, 4] and days % 100 not in [12, 13, 14]:
        return f'{days} дня'
    return f'{days} дней'


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f'{seconds} 秒'

    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} 分钟'

    hours = minutes // 60
    if hours < 24:
        return f'{hours} 小时'

    days = hours // 24
    return f'{days} 天'


def format_bytes(bytes_value: int) -> str:
    if bytes_value == 0:
        return '0 B'

    units = ['B', 'KB', 'MB', 'GB', 'TB']
    size = float(bytes_value)
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if size == int(size):
        return f'{int(size)} {units[unit_index]}'
    return f'{size:.1f} {units[unit_index]}'


def format_percentage(value: float, decimals: int = 1) -> str:
    return f'{value:.{decimals}f}%'


def format_number(number: float, separator: str = ' ') -> str:
    if isinstance(number, float):
        integer_part = int(number)
        decimal_part = number - integer_part

        formatted_integer = f'{integer_part:,}'.replace(',', separator)

        if decimal_part > 0:
            return f'{formatted_integer}.{decimal_part:.2f}'.split('.')[0] + f'.{str(decimal_part).split(".")[1][:2]}'
        return formatted_integer
    return f'{number:,}'.replace(',', separator)


def format_price_range(min_price: int, max_price: int) -> str:
    from app.config import settings

    min_formatted = settings.format_price(min_price)
    max_formatted = settings.format_price(max_price)

    if min_price == max_price:
        return min_formatted
    return f'{min_formatted} - {max_formatted}'


def truncate_text(text: str, max_length: int = 100, suffix: str = '...') -> str:
    if len(text) <= max_length:
        return text

    return text[: max_length - len(suffix)] + suffix


def format_username(username: str | None, user_id: int, full_name: str | None = None) -> str:
    if full_name:
        return full_name
    if username:
        return f'@{username}'
    return f'ID{user_id}'


def format_subscription_status(is_active: bool, is_trial: bool, end_date: datetime | str, language: str = 'zh') -> str:
    if isinstance(end_date, str):
        try:
            end_date = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            end_date = datetime.now(UTC)

    language_code = _display_language_code(language)
    use_russian_fallback = language_code in {'ru', 'fa'}
    use_chinese = language_code == 'zh'

    if not is_active:
        if use_chinese:
            return '❌ 未启用'
        return '❌ Неактивна' if use_russian_fallback else '❌ Inactive'

    if is_trial:
        if use_chinese:
            status = '🎁 试用中'
        else:
            status = '🎁 Тестовая' if use_russian_fallback else '🎁 Trial'
    else:
        if use_chinese:
            status = '✅ 已启用'
        else:
            status = '✅ Активна' if use_russian_fallback else '✅ Active'

    now = datetime.now(UTC)
    if end_date > now:
        days_left = (end_date - now).days
        if days_left > 0:
            if use_chinese:
                status += f'（剩余 {days_left} 天）'
            else:
                status += f' ({days_left} дн.)' if use_russian_fallback else f' ({days_left} days)'
        else:
            hours_left = (end_date - now).seconds // 3600
            if use_chinese:
                status += f'（剩余 {hours_left} 小时）'
            else:
                status += f' ({hours_left} ч.)' if use_russian_fallback else f' ({hours_left} hrs)'
    else:
        if use_chinese:
            status = '⏰ 已过期'
        else:
            status = '⏰ Истекла' if use_russian_fallback else '⏰ Expired'

    return status


def format_traffic_usage(used_gb: float, limit_gb: int, language: str = 'zh') -> str:
    language_code = _display_language_code(language)
    use_russian_fallback = language_code in {'ru', 'fa'}
    use_chinese = language_code == 'zh'

    if limit_gb == 0:
        if use_chinese:
            return f'{used_gb:.1f} GB / 无限'
        if use_russian_fallback:
            return f'{used_gb:.1f} ГБ / ∞'
        return f'{used_gb:.1f} GB / ∞'

    percentage = (used_gb / limit_gb) * 100 if limit_gb > 0 else 0

    if use_chinese:
        return f'{used_gb:.1f} GB / {limit_gb} GB ({percentage:.1f}%)'
    if use_russian_fallback:
        return f'{used_gb:.1f} ГБ / {limit_gb} ГБ ({percentage:.1f}%)'
    return f'{used_gb:.1f} GB / {limit_gb} GB ({percentage:.1f}%)'


def format_boolean(value: bool, language: str = 'zh') -> str:
    language_code = _display_language_code(language)
    if language_code == 'zh':
        return '✅ 是' if value else '❌ 否'
    if language_code in {'ru', 'fa'}:
        return '✅ Да' if value else '❌ Нет'
    return '✅ Yes' if value else '❌ No'
