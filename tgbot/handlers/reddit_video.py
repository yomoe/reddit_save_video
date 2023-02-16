import logging
import re
from urllib import request
from urllib.parse import urljoin, urlparse

import requests
from aiogram import Dispatcher, types
from aiogram.types import CallbackQuery
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

from tgbot.keyboards.inline import create_inline_kb
from ..lexicon import lexicon_en as en

logger = logging.getLogger(__name__)

users: dict = {}

ua = UserAgent()
HEADERS = {
    'user-agent': ua.chrome,
}


def size_file(url: str) -> str:
    """Get the size of the file"""
    logger.debug(f'Try get size file {url}')
    req = request.Request(url, headers=HEADERS)
    f = request.urlopen(req)
    size = str(round(int(f.headers['Content-Length']) / 1024 / 1024, 1))
    logger.debug('File size: ' + size + 'mb')
    return size


def parse_xml(xml: str, url: str, url_clear: str) -> dict:
    """Find the SD video and return a dictionary with the links to the video"""
    video_link_sd = {}
    logger.debug(f'Get xml file {url}')
    soup = BeautifulSoup(xml, 'xml')
    list_files = soup.find_all('AdaptationSet')
    logger.debug('Check audio in file')
    if len(list_files) > 1:
        audio = url + list_files[1].find_all('BaseURL')[0].text
        logger.debug(f'Audio found: {audio}')
    else:
        audio = 'false'
        logger.debug(f'Audio not found: {audio}')
    video_link_sd['audio'] = audio

    logger.debug('Check video in file')
    videos = list_files[0].find_all('BaseURL')
    if len(videos) > 2:
        for resol in videos:
            if 'DASH_2' not in resol.text:
                resol_link = (
                    'https://sd.redditsave.com/download-sd.php?permalink='
                    '{permalink}&video_url={video_url}&audio_url='
                    '{audio_url}'.format(
                        permalink=url_clear,
                        video_url=url + resol.text,
                        audio_url=video_link_sd['audio'])
                )
                resol = (resol.text.split('_')[1].split('.')[0]) + 'p'
                logger.debug(f'{resol} have link {resol_link}')
                size = size_file(resol_link)
                video_link_sd[resol + ' ' + size + 'mb'] = resol_link
    return video_link_sd


def url_to_json(url):
    video_link = {}
    url = re.search("(?P<url>https?://[^\s]+)", url).group("url")
    logger.debug('Get only link from message: ' + url)
    url_clear = urljoin(url, urlparse(url).path)
    logger.debug('Delete parameters from link: ' + url_clear)
    url_json = re.sub(r'/$', '.json', url_clear)
    logger.debug('Change link to json link: ' + url_json)
    res = requests.get(url_json, headers=HEADERS)
    logger.debug('Find video in json file')
    if 'reddit_video' in res.text:
        res_json = res.json()
        find_json = res_json[0]['data']['children'][0]['data']

        logger.debug('Get a caption from post')
        caption = find_json['title']

        logger.debug('Checking crosspost in json')
        if 'crosspost_parent_list' in res.text:
            find_json = find_json['crosspost_parent_list'][0]

        logger.debug('Get a link to a file with SD video and audio')
        dash = requests.get(
            find_json['secure_media']['reddit_video']['dash_url']).text
        logger.debug('Get a major link for downloads')
        url_dl = find_json['url_overridden_by_dest'] + '/'
        logger.debug('Sending data for parsing')
        video_link = parse_xml(dash, url_dl, url_clear)
        logger.debug('Get a max resolution')
        max_resol = (find_json['secure_media']['reddit_video'][
            'fallback_url'].split('_')[1].split('.')[0]) + 'p'
        logger.debug('Get a link to a file with HD video')
        max_resol_link = (
            'https://sd.redditsave.com/download.php?permalink='
            '{permalink}&video_url={video_url}&audio_url={audio_url}'.format(
                permalink=url_clear,
                video_url=(
                    find_json['secure_media']['reddit_video']['fallback_url']
                ),
                audio_url=video_link['audio']))
        video_link.pop('audio', None)
        size = size_file(max_resol_link)
        video_link[max_resol + ' ' + size + 'mb'] = max_resol_link
        video_link['caption'] = caption
        return video_link
    else:
        return video_link


async def bot_get_links(message: types.Message) -> None:
    """Send a message with buttons to download the video"""
    msg = await message.answer(en.GET_LINKS_FOR_VIDEO)
    # logger.info(message.from_user.language_code)
    links = url_to_json(message.text)
    if not links:
        logger.debug('Словарь ссылок пустой, отправляем сообщение об ошибке')
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    else:
        logger.debug('Ссылки есть, отправляем сообщение с кнопками')
        caption = links.pop('caption', None)
        keyboard = create_inline_kb(2, **links)
        users[message.from_user.id] = {
            'caption': caption,
            'links': links,
        }
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
        headers=HEADERS
    )
    logger.info(
        f'Отправляю видео для пользователя {callback.from_user.full_name}, '
        f'id {callback.from_user.id}'
    )
    await callback.message.edit_text(
        text=en.SENDING_VIDEO)
    await callback.message.answer_video(
        video=response.content,
        caption=users[callback.from_user.id]['caption'],
    )
    logger.debug("Удаляю сообщение о загрузке")
    await callback.message.delete()


async def bot_send_video_group(message: types.Message) -> None:
    """Отправляем видео в группу или канал предпоследнего качества"""
    msg = await message.answer(text=en.GET_LINKS_FOR_VIDEO)
    links = url_to_json(message.text)
    if not links:
        logger.debug('Словарь ссылок пустой, отправляем сообщение об ошибке')
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    else:
        users[message.from_user.id] = {'links': links, }
    await msg.edit_text(en.DOWNLOADING_VIDEO)
    logger.info(
        f'Отправляю видео для чата {message.chat.title}, '
        f'id {message.chat.id}'
    )
    response = requests.get(
        list(users[message.from_user.id]['links'].values())[-2],
        headers=HEADERS
    )
    await msg.edit_text(en.SENDING_VIDEO)
    await message.answer_video(
        response.content,
        caption=links.pop('caption', None),
    )
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
