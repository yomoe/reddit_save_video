from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

LEXICON: dict[str, str] = {
    'but_1': 'Кнопка 1',
    'but_2': 'Кнопка 2',
    'but_3': 'Кнопка 3',
    'but_4': 'Кнопка 4',
    'but_5': 'Кнопка 5',
    'but_6': 'Кнопка 6',
    'but_7': 'Кнопка 7',
}


def create_inline_kb(row_width: int, *args, **kwargs) -> InlineKeyboardMarkup:
    inline_kb: InlineKeyboardMarkup = InlineKeyboardMarkup(row_width=row_width)
    if args:
        [inline_kb.insert(
            InlineKeyboardButton(
                text=LEXICON[button],
                callback_data=button)) for button in args]
    if kwargs:
        [inline_kb.insert(
            InlineKeyboardButton(
                text=text,
                callback_data=text)) for text in kwargs.keys()]
    return inline_kb
