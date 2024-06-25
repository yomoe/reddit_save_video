import asyncio
import logging

from aiogram import Dispatcher
from aiogram.types import Message

from ..lexicon import lexicon_en as en
from ..misc.advice import get_advice
from ..misc.find import get_random_image_post

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


async def user_find(message):
    try:
        async with asyncio.timeout(10):  # Установка таймаута в 10 секунд
            random_image_post = await get_random_image_post()
            if isinstance(random_image_post, dict):
                await message.answer_photo(
                    random_image_post['img_url'],
                    caption=f'{random_image_post["title"]}\n\n{random_image_post["post_url"]}'
                )
            else:
                await message.reply("There's some kind of error, try again: /find")
    except asyncio.TimeoutError:
        await message.reply("Request timed out, please try again later.")


def register_user(dp: Dispatcher):
    dp.register_message_handler(user_start, commands=["start"], state="*")
    dp.register_message_handler(user_advice, commands=["advice"], state="*")
    dp.register_message_handler(user_find, commands=["find"], state="*")
