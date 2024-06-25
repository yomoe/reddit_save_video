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
    random_image_post = await get_random_image_post()
    if isinstance(random_image_post, dict):
        logger.info('Начинаем поиск')
        await message.answer_photo(
            random_image_post['img_url'],
            f'{random_image_post["title"]}\n\nYou can look up the answer here: {random_image_post["post_url"]}'
        )
    elif not random_image_post:
        logger.info('Какая-то ошибка')
        await message.reply(f'There\'s some kind of error, try again: /find')


def register_user(dp: Dispatcher):
    dp.register_message_handler(user_start, commands=["start"], state="*")
    dp.register_message_handler(user_advice, commands=["advice"], state="*")
    dp.register_message_handler(user_find, commands=["find"], state="*")
