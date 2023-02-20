import json
import logging
import os
import re
import tempfile
from tempfile import NamedTemporaryFile
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp
import ffmpeg
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

# WORK_TYPE can be redditsave or self_work
WORK_TYPE = 'self_work'


def concat_video_audio(video_link: str, audio_link: str) -> bytes:
    video_response = requests.get(video_link, headers=HEADERS)
    audio_response = requests.get(audio_link, headers=HEADERS)

    with tempfile.NamedTemporaryFile(
            delete=False) as video_file, tempfile.NamedTemporaryFile(
            delete=False) as audio_file, tempfile.NamedTemporaryFile(
        suffix='.mp4',
        delete=False
    ) as output_file:
        video_file.write(video_response.content)
        audio_file.write(audio_response.content)

    input_video = ffmpeg.input(video_file.name)
    input_audio = ffmpeg.input(audio_file.name)
    (
        ffmpeg
        .concat(input_video, input_audio, v=1, a=1)
        .output(output_file.name)
        .run(quiet=True, overwrite_output=True)
    )

    with open(output_file.name, 'rb') as f:
        output_data = f.read()

    os.remove(video_file.name)
    os.remove(audio_file.name)
    os.remove(output_file.name)
    return output_data


def size_file(url: str) -> float:
    """Get the size of the file."""
    logger.debug(f'Try get size file {url}')
    try:
        response = requests.head(url, headers=HEADERS)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.exception(f'Request to {url} failed: {e}')
        return 0.0
    size = round(int(response.headers['Content-Length']) / 1024 / 1024, 1)
    logger.debug(f'File size: {size} MB')
    return size


def parse_xml(xml: str, url: str, permalink: str) -> dict:
    """Find the SD video and return a dictionary with the links to the video"""
    video_links = []
    logger.debug(f'Get xml {xml}')
    soup = BeautifulSoup(xml, 'xml')
    list_files = soup.find_all('AdaptationSet')
    logger.debug('Check audio in file')
    if len(list_files) > 1:
        audio = url + list_files[1].find_all('BaseURL')[0].text
        logger.debug(f'Audio found: {audio}')
    else:
        audio = 'false'
        logger.debug(f'Audio not found: {audio}')
    video_links.append(('audio', audio))

    logger.debug('Check video in file')
    videos = [x.text for x in list_files[0].find_all('BaseURL') if
              'DASH_2' not in x.text]
    for video in videos:
        resolution = video.split('_')[1].split('.')[0]
        if WORK_TYPE == 'redditsave':
            link = (
                'https://sd.redditsave.com/download-sd.php?permalink={permalink}&'
                'video_url={video_url}&audio_url='
                '{audio_url}'.format(
                    permalink=permalink,
                    video_url=url + video,
                    audio_url=audio
                ))
        else:
            link = url + video
        size = size_file(link)
        video_links.append(('{}p {}mb'.format(resolution, size), link))
    return dict(video_links)


def url_to_json(url: str) -> dict:
    """Extracts video information from a Reddit URL
    and returns it as a dictionary.
    """
    try:
        parsed_url = urlparse(url)._replace(query='', fragment='')
        url = urlunparse(parsed_url)
        logger.debug(f"Extracted URL: {url}")
    except AttributeError:
        logger.error(f"No URL found in {url}")
        return {}
    try:
        url_clear = urljoin(url, urlparse(url).path)
        logger.debug(f'Delete parameters from link: {url_clear}')
    except AttributeError:
        logger.error(f"Can't clear parameters {url}")
        return {}
    try:
        url_json = re.sub(r'/$', '.json', url_clear)
        logger.info(f'Change link to json link: {url_json}')
    except AttributeError:
        logger.error(f"Can't change link to json {url_clear}")
        return {}
    try:
        res = requests.get(url_json, headers=HEADERS)
        res.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        return {}

    logger.debug(f"Response status code: {res.status_code}")

    if 'reddit_video' in res.text:
        video_link = {}
        try:
            res_json = res.json()
            find_json = res_json[0].get('data', {}).get('children', [{}])[
                0].get('data', {})
            caption = find_json.get('title')

            if 'crosspost_parent_list' in res.text:
                find_json = find_json.get('crosspost_parent_list', [{}])[0]

            dash_url = find_json.get('secure_media', {}).get(
                'reddit_video', {}).get(
                'dash_url')
            if dash_url:
                try:
                    dash = requests.get(dash_url, headers=HEADERS).text
                except requests.exceptions.RequestException as e:
                    logger.error(f"Request failed: {e}")
                    return {}
                url_dl = find_json.get('url_overridden_by_dest', '') + '/'
                video_link = parse_xml(dash, url_dl, url_clear)

            fallback_url = find_json.get('secure_media', {}).get(
                'reddit_video', {}).get('fallback_url')
            if fallback_url:
                max_resol = fallback_url.split('_')[1].split('.')[0]
                if WORK_TYPE == 'redditsave':
                    max_resol_link = (
                        f'https://sd.redditsave.com/download.php?'
                        f'permalink={url_clear}&video_url={fallback_url}&'
                        f'audio_url={video_link.get("audio", "")}')
                else:
                    max_resol_link = fallback_url
                size = size_file(max_resol_link)
                video_link[f'{max_resol}p {size}mb'] = max_resol_link
                video_link['caption'] = caption
                return video_link
        except (
                json.JSONDecodeError,
                requests.exceptions.RequestException) as e:
            logger.exception(f"Error: {e}")
    else:
        return {}


async def bot_get_links(message: types.Message) -> None:
    """Send a message with buttons to download the video"""
    msg = await message.answer(en.GET_LINKS_FOR_VIDEO)
    links = url_to_json(message.text)
    if not links:
        logger.debug('The links dictionary is empty, sending an error message')
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    else:
        logger.debug('There are links, sending a message with buttons')
        caption = links.pop('caption', None)
        audio = links.pop('audio', None)
        users[message.from_user.id] = {
            'caption': caption,
            'audio': audio,
            'links': links
        }

        await msg.edit_text(
            text=en.VIDEO_QUALITY,
            reply_markup=create_inline_kb(2, **links)
        )


async def bot_send_video(callback: CallbackQuery) -> None:
    """Send video to user"""
    try:
        await callback.message.edit_text(text=en.DOWNLOADING_VIDEO)
        if WORK_TYPE == 'redditsave':
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(
                        users[callback.from_user.id]['links'][
                            callback.data]) as response:
                    response.raise_for_status()
                    video_content = await response.read()
        else:
            video_link = users[callback.from_user.id]['links'][callback.data]
            audio_link = users[callback.from_user.id]['audio']
            if audio_link != 'false':
                video_content = (concat_video_audio(video_link, audio_link))
            else:
                async with aiohttp.ClientSession(headers=HEADERS) as session:
                    async with session.get(video_link) as response:
                        response.raise_for_status()
                        video_content = await response.read()
        await callback.message.edit_text(text=en.SENDING_VIDEO)
        logger.info(
            f'Send video to user {callback.from_user.full_name}, '
            f'id {callback.from_user.id}'
        )
        await callback.message.answer_video(
            video=video_content,
            caption=users[callback.from_user.id]['caption']
        )
        await callback.message.delete()
    except (aiohttp.ClientError, Exception) as e:
        logging.exception(f"Failed to send video: {e}")
        await callback.message.answer(en.FAILED_TO_SEND_VIDEO)


async def bot_send_video_group(message: types.Message) -> None:
    """Send video to a group or channel in the second-to-last quality"""
    msg = await message.answer(text=en.GET_LINKS_FOR_VIDEO)
    links = url_to_json(message.text)
    if not links:
        logger.debug(
            'The dictionary of links is empty, sending an error message.'
        )
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    try:
        await msg.edit_text(text=en.DOWNLOADING_VIDEO)
        if WORK_TYPE == 'redditsave':
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(list(links.values())[-2]) as response:
                    response.raise_for_status()
                    video_content = await response.read()
        else:
            video_link = list(links.values())[-2]
            audio_link = links['audio']
            if audio_link != 'false':
                video_content = (concat_video_audio(video_link, audio_link))
            else:
                async with aiohttp.ClientSession(headers=HEADERS) as session:
                    async with session.get(video_link) as response:
                        response.raise_for_status()
                        video_content = await response.read()
        await msg.edit_text(text=en.SENDING_VIDEO)
        logger.info(
            f'Sending video for chat {message.chat.title}, {message.chat.type} '
            f'id {message.chat.id}'
        )
        await message.answer_video(
            video=video_content,
            caption=links.pop('caption', None)
        )
        await msg.delete()
    except (aiohttp.ClientError, Exception) as e:
        logging.exception(f"Failed to send video: {e}")
        await msg.edit_text(en.FAILED_TO_SEND_VIDEO)


async def bot_send_video_cancel(callback: CallbackQuery) -> None:
    """Cancel sending video"""
    logger.info(
        f'User {callback.from_user.full_name}, '
        f'id {callback.from_user.id} canceled sending video'
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
