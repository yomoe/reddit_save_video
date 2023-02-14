from aiogram import Dispatcher
from aiogram.dispatcher.filters import CommandStart
from aiogram.types import Message
from ..lexicon import lexicon_en as en


async def admin_start(message: Message):
    await message.reply("Hello, admin!!")
    await message.reply(
        en.START_MSG.format(name=message.from_user.first_name),
        disable_web_page_preview=True
    )


def register_admin(dp: Dispatcher):
    dp.register_message_handler(
        admin_start, commands=["start"], state="*", is_admin=True)
