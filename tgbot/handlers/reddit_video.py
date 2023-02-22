import json
import logging
import os
import re
import tempfile
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


async def concat_video_audio(video_link: str, audio_link: str) -> bytes:
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(video_link) as response:
            response.raise_for_status()
            video_response = await response.read()
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(audio_link) as response:
            response.raise_for_status()
            audio_response = await response.read()

    with tempfile.NamedTemporaryFile(
            delete=False) as video_file, tempfile.NamedTemporaryFile(
        delete=False) as audio_file, tempfile.NamedTemporaryFile(
        suffix='.mp4',
        delete=False
    ) as output_file:
        video_file.write(video_response)
        audio_file.write(audio_response)

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


async def size_file(url: str) -> float:
    """Get the size of the file."""
    logger.debug(f'Try get size file {url}')
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, headers=HEADERS) as response:
                response.raise_for_status()
                size = round(
                    int(response.headers['Content-Length']) / 1024 / 1024, 1)
                logger.debug(f'File size: {size} MB')
                return size
    except requests.exceptions.RequestException as e:
        logger.exception(f'Request to {url} failed: {e}')
        return 0.0


async def parse_xml(xml: str, url: str) -> dict:
    """Find the SD video and return a dictionary with the links to the video"""
    video_links = {'audio': 'false'}
    logger.debug(f'Get xml {xml}')
    soup = BeautifulSoup(xml, 'xml')
    for adaptation_set in soup.find_all('AdaptationSet'):
        content_type = adaptation_set.get('contentType')
        if content_type == 'audio':
            base_url = adaptation_set.find('BaseURL').text
            audio = url + base_url
            video_links['audio'] = audio
        elif content_type == 'video':
            videos = [x.text for x in adaptation_set.find_all('BaseURL') if
                      'DASH_2' not in x.text]
            for video in videos:
                resolution = video.split('_')[1].split('.')[0]
                link = url + video
                size = await size_file(link)
                video_links[f'{resolution}p {size}mb'] = link
    return video_links


async def url_to_json(url: str) -> dict:
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

    def get_find_json(res_json):
        find_json = res_json[0]['data'].get('children', [{}])[0]['data']
        if 'crosspost_parent_list' in res.text:
            find_json = find_json.get('crosspost_parent_list', [{}])[0]
        return find_json

    def get_caption(res_json):
        return get_find_json(res_json).get('title')

    def is_gif(res_json):
        file = res_json[0]['data'].get('children', [{}])[0]['data'].get(
            'url', [{}])
        return os.path.splitext(file)[1] == '.gif'

    def is_nsfw(res_json):
        return 'nsfw' in get_find_json(res_json).get('thumbnail')

    async def get_video_links(fallback_url, dict_video):
        max_resol = fallback_url.split('_')[1].split('.')[0]
        max_resol_link = urljoin(fallback_url, urlparse(fallback_url).path)
        size = await size_file(max_resol_link)
        dict_video[f'{max_resol}p {size:.1f}mb'] = max_resol_link
        return dict_video

    try:
        res_json = res.json()
        video_link = {}
        if 'reddit_video_preview' in res.text:
            find_json = get_find_json(res_json).get('preview', {}).get(
                'reddit_video_preview', {})

            dash_url = find_json.get('dash_url')
            if dash_url:
                dash = requests.get(dash_url, headers=HEADERS).text
                url_dl = dash_url.split('DASHPlaylist.mpd')[0]
                video_link = await parse_xml(dash, url_dl)

            video_link['caption'] = get_caption(res_json)
            video_link['permalink'] = url_clear
            fallback_url = find_json.get('fallback_url')
            return await get_video_links(fallback_url, video_link)

        elif 'reddit_video' in res.text:
            find_json = get_find_json(res_json).get('secure_media', {}).get(
                'reddit_video', {})

            dash_url = find_json.get('dash_url')
            if dash_url:
                dash = requests.get(dash_url, headers=HEADERS).text
                url_dl = get_find_json(res_json).get(
                    'url_overridden_by_dest', '') + '/'
                video_link = await parse_xml(dash, url_dl)

            video_link['caption'] = get_caption(res_json)
            video_link['permalink'] = url_clear
            video_link['nsfw'] = is_nsfw(res_json)
            fallback_url = find_json.get('fallback_url')
            return await get_video_links(fallback_url, video_link)
        elif is_gif(res_json):
            video_link['gif'] = res_json[0]['data'].get('children', [{}])[0][
                'data'].get('url', [{}])
            video_link['caption'] = get_caption(res_json)
            return video_link
        else:
            return {}
    except (json.JSONDecodeError, requests.exceptions.RequestException) as e:
        logger.exception(f"Error: {e}")


async def bot_get_links_private(message: types.Message) -> None:
    """Send a message with buttons to download the video"""
    msg = await message.answer(en.GET_LINKS_FOR_VIDEO)
    links = await url_to_json(message.text)
    if not links:
        logger.debug('The links dictionary is empty, sending an error message')
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    elif 'gif' in links:
        await msg.edit_text(en.SENDING_GIF)
        logger.info(
            f'Send gif to user {message.from_user.full_name}, '
            f'id {message.from_user.id}'
        )
        await message.answer_animation(links['gif'], caption=links['caption'])
        await msg.delete()
    else:
        logger.debug('There are links, sending a message with buttons')
        nsfw = links.pop('nsfw', None)
        caption = links.pop('caption', None)
        audio = links.pop('audio', None)
        permalink = links.pop('permalink', None)
        users[message.from_user.id] = {
            'caption': caption,
            'audio': audio,
            'permalink': permalink,
            'nsfw': nsfw,
            'links': links,
        }
        await msg.edit_text(
            text=en.VIDEO_QUALITY,
            reply_markup=create_inline_kb(2, **links)
        )


async def download_video(
        video_link: str, audio_link: str, permalink: str
) -> bytes:
    if WORK_TYPE == 'redditsave':
        link = (
            'https://sd.redditsave.com/download-sd.php?permalink={permalink}&'
            'video_url={video_url}&audio_url={audio_url}'.format(
                permalink=permalink,
                video_url=video_link,
                audio_url=audio_link
            ))
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(link) as response:
                response.raise_for_status()
                video_content = await response.read()
    else:
        if audio_link != 'false':
            video_content = await concat_video_audio(video_link, audio_link)
        else:
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                async with session.get(video_link) as response:
                    response.raise_for_status()
                    video_content = await response.read()
    return video_content


async def bot_send_video(callback: CallbackQuery) -> None:
    """Send video to user"""
    try:
        await callback.message.edit_text(text=en.DOWNLOADING_VIDEO)
        video_link = users[callback.from_user.id]['links'][callback.data]
        audio_link = users[callback.from_user.id]['audio']
        permalink = users[callback.from_user.id]['permalink']
        nsfw = users[callback.from_user.id]['nsfw']
        video_content = await download_video(video_link, audio_link, permalink)
        await callback.message.edit_text(text=en.SENDING_VIDEO)
        logger.info(
            f'Send video to user {callback.from_user.full_name}, '
            f'id {callback.from_user.id}'
        )
        await callback.message.answer_video(
            video=video_content,
            caption=users[callback.from_user.id]['caption'],
        )
        await callback.message.delete()
    except (aiohttp.ClientError, Exception) as e:
        logging.exception(f"Failed to send video: {e}")
        await callback.message.answer(en.FAILED_TO_SEND_VIDEO)


async def bot_get_links_group(message: types.Message) -> None:
    """Send video to a group or channel in the second-to-last quality"""
    msg = await message.answer(text=en.GET_LINKS_FOR_VIDEO)
    links = await url_to_json(message.text)
    if not links:
        logger.debug(
            'The dictionary of links is empty, sending an error message.'
        )
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    elif 'gif' in links:
        await msg.edit_text(en.SENDING_GIF)
        await message.answer_animation(links['gif'], caption=links['caption'])
        await msg.delete()
    else:
        try:
            await msg.edit_text(text=en.DOWNLOADING_VIDEO)

            audio_link = links.pop('audio', None)
            caption = links.pop('caption', None)
            permalink = links.pop('permalink', None)
            if len(links) > 1:
                video_link = list(links.values())[-2]
            else:
                video_link = list(links.values())[0]
            video_content = await download_video(
                video_link, audio_link, permalink)
            await msg.edit_text(text=en.SENDING_VIDEO)
            logger.info(
                f'Sending video for chat {message.chat.title}, '
                f'{message.chat.type} id {message.chat.id}'
            )
            await message.answer_video(
                video=video_content,
                caption=caption
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
        bot_get_links_private, text_startswith=['https://www.reddit.com/r/'],
        chat_type=types.ChatType.PRIVATE)
    dp.register_callback_query_handler(
        bot_send_video, text_endswith='mb',
        chat_type=types.ChatType.PRIVATE)
    dp.register_callback_query_handler(
        bot_send_video_cancel, text_endswith='cancel',
        chat_type=types.ChatType.PRIVATE)
    dp.register_message_handler(
        bot_get_links_group, text_startswith=['https://www.reddit.com/r/'])
