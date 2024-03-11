from aiogram import Dispatcher
from aiogram.types import Message

from ..lexicon import lexicon_en as en
from ..misc.clear_tmp import clear_tmp_folder


async def admin_start(message: Message):
    await message.reply("Hello, admin!!")
    await message.reply(
        en.START_MSG.format(name=message.from_user.first_name),
        disable_web_page_preview=True
    )


async def del_tmp_files(message: Message):
    clear_tmp_folder()
    await message.reply("I have deleted all temporary files.")


def register_admin(dp: Dispatcher):
    dp.register_message_handler(
        admin_start, commands=["start"], state="*", is_admin=True)
    dp.register_message_handler(
        del_tmp_files, commands=["del_tmp"], state="*", is_admin=True
    )
