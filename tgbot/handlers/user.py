from aiogram import Dispatcher
from aiogram.types import Message
from ..lexicon import lexicon_en as en


async def user_start(message: Message):
    await message.reply(
        en.START_MSG.format(name=message.from_user.first_name),
        disable_web_page_preview=True
    )


def register_user(dp: Dispatcher):
    dp.register_message_handler(user_start, commands=["start"], state="*")
