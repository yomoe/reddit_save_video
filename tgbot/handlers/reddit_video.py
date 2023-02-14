import logging
from urllib import request

import requests
from aiogram import Dispatcher, types
from aiogram.types import CallbackQuery
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from pyhelpers.ops import is_downloadable
from ..lexicon import lexicon_en as en

from tgbot.keyboards.inline import create_inline_kb

REDDIT_SAVE_SD_URL = 'https://rapidsave.com/sd.php?id='
REDDIT_SAVE_HD_URL = 'https://rapidsave.com/info?url='

logger = logging.getLogger(__name__)

users: dict = {}

ua = UserAgent()
headers = {
    'user-agent': ua.chrome,
}


def parse_hd_url(url: str) -> str:
    """Link to the HD video"""
    html = BeautifulSoup(
        requests.get(
            REDDIT_SAVE_HD_URL + url,
            headers=headers
        ).text, 'html.parser')
    return html.find_all('a', class_='downloadbutton')[0].get('href')


def parse_sd_url(url: str) -> dict:
    """Find the SD video and return a dictionary with the links to the video"""
    results = {}
    logger.debug(f'Получаем id ссылки {url} на sd видео')
    id_url = REDDIT_SAVE_SD_URL + url.split('/comments/')[1].split('/')[0]
    logger.debug(
        f'Получилась ссылка {id_url} для парсинга. '
        f'Получаем страницу и отдаем ее'
    )
    html = BeautifulSoup(
        requests.get(
            id_url,
            headers=headers
        ).text, 'html.parser')
    if html.find_all(
            'div', class_='col-md-8 col-md-offset-2 alert alert-danger'):
        logger.debug('Видео не найдено, возвращаем пустой словарь')
        return results
    for row in html.find_all('tr'):
        logger.debug('Получаем ссылки на видео в sd качестве')
        aux = row.find_all('td')
        if len(aux) == 2 and not aux[0].string.startswith('2'):
            req = request.Request(
                aux[1].find('a').get('href'),
                headers=headers
            )
            f = request.urlopen(req)
            size = aux[0].string + ' ' + str(
                round(int(f.headers['Content-Length']) / 1024 / 1024, 1)
            ) + 'mb'
            results[size] = aux[1].find('a').get('href')
            logger.debug(
                f'Записываем в словарь ключ {[aux[0].string]} и ссылку '
                f'{results[size]}'
            )
    max_url = parse_hd_url(url)
    req = request.Request(
        max_url,
        headers=headers
    )
    f = request.urlopen(req)
    size = 'Max (mp4) ' + str(
        round(int(f.headers['Content-Length']) / 1024 / 1024, 1)
    ) + 'mb'
    results[size] = max_url
    logger.debug(
        'Записываем в словарь ключ \'Max (mp4)\' и ссылку '
        f'{results[size]}'
    )
    return results


async def bot_get_links(message: types.Message) -> None:
    """Send a message with buttons to download the video"""
    msg = await message.answer(en.GET_LINKS_FOR_VIDEO)
    logger.info(message.from_user.language_code)
    links = parse_sd_url(message.text)
    if not links:
        logger.debug('Словарь ссылок пустой, отправляем сообщение об ошибке')
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    else:
        logger.debug('Ссылки есть, отправляем сообщение с кнопками')
        keyboard = create_inline_kb(2, **links)
        users[message.from_user.id] = {'links': links, }
        await msg.edit_text(
            text=en.VIDEO_QUALITY,
            reply_markup=keyboard)


async def bot_send_video(callback: CallbackQuery) -> None:
    """Отправляем видео"""
    await callback.message.edit_text(
        text=en.DOWNLOADING_VIDEO
    )
    response = requests.get(
        users[callback.from_user.id]['links'][callback.data],
        headers=headers
    )
    logger.info(
        f'Отправляю видео для пользователя {callback.from_user.full_name}, '
        f'id {callback.from_user.id}'
    )
    await callback.message.edit_text(
        text=en.SENDING_VIDEO)
    await callback.message.answer_video(
        response.content)
    logger.debug("Удаляю сообщение о загрузке")
    await callback.message.delete()


async def bot_send_video_group(message: types.Message) -> None:
    """Отправляем видео в группу или канал предпоследнего качества"""
    links = parse_sd_url(message.text)
    if not links:
        logger.debug('Словарь ссылок пустой, отправляем сообщение об ошибке')
        await message.answer(en.VIDEO_NOT_FOUND)
    else:
        users[message.from_user.id] = {'links': links, }
    logger.info(
        f'Отправляю видео для чата {message.chat.title}, '
        f'id {message.chat.id}'
    )
    response = requests.get(
        list(users[message.from_user.id]['links'].values())[-2],
        headers=headers
    )
    msg = await message.answer(en.DOWNLOADING_VIDEO)
    await message.answer_video(
        response.content)
    logger.debug("Удаляю сообщение о загрузке")
    await msg.delete()


async def bot_send_video_cancel(callback: CallbackQuery) -> None:
    """Отменяем отправку видео"""
    logger.debug(
        f'Пользователь {callback.from_user.full_name}, '
        f'id {callback.from_user.id} отменил отправку видео'
    )
    await callback.message.edit_text(
        text=en.SEND_VIDEO_CANCEL)


def register_get_links(dp: Dispatcher) -> None:
    dp.register_message_handler(
        bot_get_links, text_startswith=['https://www.reddit.com/r/'],
        chat_type=types.ChatType.PRIVATE)
    dp.register_callback_query_handler(
        bot_send_video, text_endswith='mb',
        chat_type=types.ChatType.PRIVATE)
    dp.register_callback_query_handler(
        bot_send_video_cancel, text_endswith='cancel',
        chat_type=types.ChatType.PRIVATE)
    dp.register_message_handler(
        bot_send_video_group, text_startswith=['https://www.reddit.com/r/'])
