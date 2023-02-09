import logging

import requests
from aiogram import Dispatcher, types
from aiogram.types import CallbackQuery
from bs4 import BeautifulSoup
from pyhelpers.ops import is_downloadable

from tgbot.keyboards.inline import create_inline_kb

REDDIT_SAVE_SD_URL = 'https://rapidsave.com/sd.php?id='
REDDIT_SAVE_HD_URL = 'https://rapidsave.com/info?url='

logger = logging.getLogger(__name__)

users: dict = {}


def parse_hd_url(url: str) -> str:
    """Ссылка на видео в максимальном качестве"""
    html = BeautifulSoup(
        requests.get(REDDIT_SAVE_HD_URL + url).text, 'html.parser')
    return html.find_all('a', class_='downloadbutton')[0].get('href')


def parse_sd_url(url: str) -> dict:
    """Формируем словарь со ссылками на видео в разных качествах"""
    results = {}
    logger.debug(f'Получаем id ссылки {url} на sd видео')
    id_url = REDDIT_SAVE_SD_URL + url.split('/comments/')[1].split('/')[0]
    logger.debug(
        f'Получилась ссылка {id_url} для парсинга. '
        f'Получаем страницу и отдаем ее'
    )
    html = BeautifulSoup(requests.get(id_url).text, 'html.parser')
    if html.find_all(
            'div', class_='col-md-8 col-md-offset-2 alert alert-danger'):
        logger.debug('Видео не найдено, возвращаем пустой словарь')
        return results
    for row in html.find_all('tr'):
        logger.debug('Получаем ссылки на видео в sd качестве')
        aux = row.find_all('td')
        if len(aux) == 2:
            results[aux[0].string] = aux[1].find('a').get('href')
            logger.debug(
                f'Записываем в словарь ключ {[aux[0].string]} и ссылку '
                f'{results[aux[0].string]}'
            )
    results['Max (mp4)'] = parse_hd_url(url)
    logger.debug(
        'Записываем в словарь ключ [\'Max (mp4)\'] и ссылку '
        f'{results["Max (mp4)"]}'
    )
    return results


async def bot_get_links(message: types.Message) -> None:
    """Получаем ссылки на видео в разных качествах и предлагаем выбрать"""
    links = parse_sd_url(message.text)
    if not links:
        logger.debug('Словарь ссылок пустой, отправляем сообщение об ошибке')
        await message.answer('Видео не найдено')
    else:
        logger.debug('Ссылки есть, отправляем сообщение с кнопками')
        keyboard = create_inline_kb(2, **links)
        users[message.from_user.id] = {'links': links, }
        await message.answer(
            text='В каком качестве хочешь получить видос?',
            reply_markup=keyboard)


async def bot_send_video(callback: CallbackQuery) -> None:
    """Отправляем видео"""
    await callback.message.edit_text(
        text='Я качаю видео, пожалуйста подожди...'
    )
    if is_downloadable(users[callback.from_user.id]['links'][callback.data]):
        response = requests.get(
            users[callback.from_user.id]['links'][callback.data])
        logger.info(
            f'Отправляю видео для пользователя {callback.from_user.full_name}, '
            f'id {callback.from_user.id}'
        )
        await callback.message.edit_text(
            text='Отправляю видео...')
        await callback.message.answer_video(
            response.content)
        logger.debug("Удаляю сообщение о загрузке")
        await callback.message.delete()


async def bot_send_video_group(message: types.Message) -> None:
    """Отправляем видео в группу или канал предпоследнего качества"""
    links = parse_sd_url(message.text)
    if not links:
        logger.debug('Словарь ссылок пустой, отправляем сообщение об ошибке')
        await message.answer('Видео не найдено')
    else:
        users[message.from_user.id] = {'links': links, }
    if is_downloadable(
            list(users[message.from_user.id]['links'].values())[-2]):
        logger.info(
            f'Отправляю видео для чата {message.chat.title}, '
            f'id {message.chat.id}'
        )
        response = requests.get(
            list(users[message.from_user.id]['links'].values())[-2]
        )
        msg = await message.answer(
            'Я качаю видео, пожалуйста подожди...')
        await message.answer_video(
            response.content)
        logger.debug("Удаляю сообщение о загрузке")
        await msg.delete()


def register_get_links(dp: Dispatcher) -> None:
    dp.register_message_handler(
        bot_get_links, text_startswith=['https://www.reddit.com/r/'],
        chat_type=types.ChatType.PRIVATE)
    dp.register_callback_query_handler(
        bot_send_video, text_endswith='(mp4)',
        chat_type=types.ChatType.PRIVATE)
    dp.register_message_handler(
        bot_send_video_group, text_startswith=['https://www.reddit.com/r/'])
