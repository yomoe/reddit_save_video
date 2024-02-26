import logging

from aiogram import Dispatcher
from aiogram.types import Message

from ..lexicon import lexicon_en as en
from ..misc.advice import get_advice
logger = logging.getLogger(__name__)

async def user_start(message: Message):
    await message.reply(
        en.START_MSG.format(name=message.from_user.first_name),
        disable_web_page_preview=True
    )


async def user_advice(message: Message):
    user = message.from_user
    if not user.username:
        user = user.full_name
    else:
        user = '@' + user.username
    advice = await get_advice()
    logger.info(f"Отправляем совет для {user}")
    await message.reply(f'{user}, {advice.lower()}')


def register_user(dp: Dispatcher):
    dp.register_message_handler(user_start, commands=["start"], state="*")
    dp.register_message_handler(user_advice, commands=["advice"], state="*")
