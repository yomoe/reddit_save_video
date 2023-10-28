from aiogram.dispatcher.filters.state import State, StatesGroup


class BotStates(StatesGroup):
    waiting_for_link = State()
    processing_link = State()
    sending_video = State()
    sending_image = State()
    sending_gallery = State()
    error_handling = State()
