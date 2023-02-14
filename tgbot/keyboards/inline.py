from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from ..lexicon import lexicon_en as en


def create_inline_kb(row_width: int, **kwargs) -> InlineKeyboardMarkup:
    inline_kb: InlineKeyboardMarkup = InlineKeyboardMarkup(row_width=row_width)
    if kwargs:
        [inline_kb.insert(
            InlineKeyboardButton(
                text=text,
                callback_data=text)) for text in kwargs.keys()]
    inline_kb.add(InlineKeyboardButton(
            text=en.CANCEL_INLINE,
            callback_data='cancel'
        ))
    return inline_kb
