from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from app.config import settings
from app.localization.texts import get_texts


def get_main_reply_keyboard(language: str = 'ru') -> ReplyKeyboardMarkup:
    texts = get_texts(language)

    keyboard = [[KeyboardButton(text=texts.MENU_BALANCE), KeyboardButton(text=texts.MENU_SUBSCRIPTION)]]

    # Добавляем кнопки промокода и рефералов, учитывая настройки
    second_row = [KeyboardButton(text=texts.MENU_PROMOCODE)]

    # Добавляем кнопку рефералов только если программа 已启用
    if settings.is_referral_program_enabled():
        second_row.append(KeyboardButton(text=texts.MENU_REFERRALS))

    keyboard.append(second_row)

    keyboard.append([KeyboardButton(text=texts.MENU_SUPPORT), KeyboardButton(text=texts.MENU_RULES)])

    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=False)


def get_admin_reply_keyboard(language: str = 'ru') -> ReplyKeyboardMarkup:
    texts = get_texts(language)

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.ADMIN_USERS), KeyboardButton(text=texts.ADMIN_SUBSCRIPTIONS)],
            [KeyboardButton(text=texts.ADMIN_PROMOCODES), KeyboardButton(text=texts.ADMIN_MESSAGES)],
            [KeyboardButton(text=texts.ADMIN_STATISTICS), KeyboardButton(text=texts.ADMIN_MONITORING)],
            [KeyboardButton(text=texts.t('ADMIN_MAIN_MENU', '🏠主菜单'))],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def get_cancel_keyboard(language: str = 'ru') -> ReplyKeyboardMarkup:
    texts = get_texts(language)

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.CANCEL)]], resize_keyboard=True, one_time_keyboard=True
    )


def get_confirmation_reply_keyboard(language: str = 'ru') -> ReplyKeyboardMarkup:
    texts = get_texts(language)

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.YES), KeyboardButton(text=texts.NO)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_skip_keyboard(language: str = 'ru') -> ReplyKeyboardMarkup:
    texts = get_texts(language)
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=texts.REFERRAL_CODE_SKIP)]], resize_keyboard=True, one_time_keyboard=True
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def get_contact_keyboard(language: str = 'ru') -> ReplyKeyboardMarkup:
    texts = get_texts(language)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.t('SEND_CONTACT_BUTTON', '📱发送联系方式'), request_contact=True)],
            [KeyboardButton(text=texts.CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def get_location_keyboard(language: str = 'ru') -> ReplyKeyboardMarkup:
    texts = get_texts(language)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.t('SEND_LOCATION_BUTTON', '📍发送位置'), request_location=True)],
            [KeyboardButton(text=texts.CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

