import asyncio

from aiogram.utils.exceptions import RetryAfter
from aiogram.dispatcher import FSMContext
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
from aiogram.types import CallbackQuery, InputMediaPhoto
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

from tgbot.keyboards.inline import create_inline_kb
from tgbot.lexicon import lexicon_en as en

logger = logging.getLogger(__name__)

users: dict = {}

ua = UserAgent()
HEADERS = {
    'user-agent': ua.chrome,
}
API_URL_REDGIFS = 'https://api.redgifs.com/v1/gifs/'


class FFmpegError(Exception):
    """Исключение, возникающее при ошибках работы с FFmpeg."""
    pass


async def concat_video_audio(video_link: str, audio_link: str) -> bytes:
    output_data = None  # Инициализируем переменную для результата
    try:
        # Загрузка видео и аудио контента
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(video_link) as video_response:
                video_response.raise_for_status()
                video_content = await video_response.read()

            async with session.get(audio_link) as audio_response:
                audio_response.raise_for_status()
                audio_content = await audio_response.read()

        # Создание временных файлов в контексте менеджера контекста
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as video_file, \
                tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as audio_file, \
                tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as output_file:

            video_file.write(video_content)
            audio_file.write(audio_content)
            video_file_name, audio_file_name, output_file_name = video_file.name, audio_file.name, output_file.name

        logger.debug(f'Created temp files: {video_file_name}, {audio_file_name}')

        # Объединение видео и аудио с использованием ffmpeg
        input_video = ffmpeg.input(video_file_name)
        input_audio = ffmpeg.input(audio_file_name)
        ffmpeg.concat(input_video, input_audio, v=1, a=1).output(output_file_name).run(
            quiet=True, overwrite_output=True)

        logger.debug(f'Concatenation completed: {output_file_name}')

        # Чтение и возврат результата
        with open(output_file_name, 'rb') as ready_file:
            output_data = ready_file.read()


    except ffmpeg._run.Error as ffmpeg_error:
        # Логируем ошибку ffmpeg
        logger.error(f'FFmpeg error: {ffmpeg_error}')
        # Вместо общего исключения, возбуждаем наше специализированное исключение
        raise FFmpegError('Failed to concat video and audio files due to FFmpeg error.') from ffmpeg_error


    except Exception as e:
        logger.error(f'An error occurred: {e}')
        raise e
    finally:
        # Удаление временных файлов
        for filename in [video_file_name, audio_file_name, output_file_name]:
            try:
                os.remove(filename)
                logger.debug(f'Deleted temp file: {filename}')
            except Exception as delete_error:
                logger.error(f'Failed to delete temp file {filename}: {delete_error}')

    if output_data:
        return output_data
    # Если output_data отсутствует, возбуждаем исключение FFmpegError
    raise FFmpegError('Failed to concat video and audio files due to FFmpeg error.')


# async def gif_to_mp4(gif_link: str) -> bytes:
#     """Convert gif to mp4 and return the result."""
#     async with aiohttp.ClientSession(headers=HEADERS) as session:
#         async with session.get(gif_link) as response:
#             response.raise_for_status()
#             gif_response = await response.read()
#
#     with tempfile.NamedTemporaryFile(
#             suffix='.gif',
#             delete=False
#     ) as gif_file, tempfile.NamedTemporaryFile(
#         suffix='.mp4',
#         delete=False
#     ) as output_file:
#         gif_file.write(gif_response)
#
#     (
#         ffmpeg
#         .input(gif_file.name)
#         .output(output_file.name)
#         .run(quiet=True, overwrite_output=True)
#     )
#
#     with open(output_file.name, 'rb') as ready_file:
#         output_data = ready_file.read()
#
#     os.remove(gif_file.name)
#     os.remove(output_file.name)
#     return output_data


async def get_redgifs(url_id: str) -> bytes or None:
    """Get the video from redgifs.com."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(API_URL_REDGIFS + url_id) as response:
                redgifs_json = await response.json()
                video_url = (redgifs_json.get('gif', {}).get('urls', {}).get(
                    'hd') or redgifs_json.get('gfyItem', {}).get(
                    'content_urls', {}).get(
                    'mp4', {}).get(
                    'url'))
        except (aiohttp.ClientError, json.JSONDecodeError):
            logger.error('Error getting json from %s', url_id)
            return None
        try:
            async with session.get(video_url) as video:
                file_data = await video.read()
        except aiohttp.ClientError:
            logger.error('Error getting video from %s', url_id)
            return None
        return file_data


async def size_file(url: str) -> float:
    """Get the size of the file."""
    logger.debug('Try get size file %s', url)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, headers=HEADERS) as response:
                response.raise_for_status()
                size = round(
                    int(response.headers['Content-Length']) / 1024 / 1024, 1
                )
                logger.debug('File size: %s MB', size)
                return size
    except requests.exceptions.RequestException as error:
        logger.error('Request to %s failed: %s', url, error)
        return 0.0


async def parse_xml(xml: str, url: str) -> dict:
    """Find the SD video and return a dictionary with the links to the video"""
    video_links = {'audio': 'false'}
    logger.debug('Get xml %s', xml)
    soup = BeautifulSoup(xml, 'xml')
    for adaptation_set in soup.find_all('AdaptationSet'):
        content_type = adaptation_set.get('contentType')
        if content_type == 'audio':
            base_url = adaptation_set.find('BaseURL').text
            audio_bandwidth = 0
            for adaptation_set in soup.find_all('AdaptationSet', {'contentType': 'audio'}):
                for representation in adaptation_set.find_all('Representation'):
                    current_bandwidth = int(representation.get('bandwidth', 0))
                    if current_bandwidth > audio_bandwidth:
                        base_url = representation.find('BaseURL').text
                        audio_bandwidth = current_bandwidth

            if audio_bandwidth > 0:
                audio = url + base_url
                video_links['audio'] = audio
        elif content_type == 'video':
            videos = [x.text for x in adaptation_set.find_all('BaseURL')]
            for video in videos:
                resolution = video.split('_')[1].split('.')[0]
                link = url + video
                size = await size_file(link)
                logger.debug('File size: %s MB', size)
                if size < 50:
                    video_links[f'{resolution}p {size}mb'] = link
    logger.debug(video_links)
    return video_links


def clear_url(url):
    """Delete parameters from link and change link to json link"""
    try:
        response = requests.get(url, allow_redirects=True)
        url = response.url
        parsed_url = urlparse(url)._replace(query='', fragment='')
        url = urlunparse(parsed_url)
        logger.debug('Extracted URL: %s', parsed_url)
        url_clear = urljoin(url, urlparse(url).path)
        logger.debug('Delete parameters from link: %s', url_clear)
        url_json = re.sub(
            r'/$',
            '.json',
            url_clear
        ) if url_clear.endswith('/') else f'{url_clear}.json'
        logger.info('Url: %s', url)
        return url_json
    except requests.exceptions.RequestException as error:
        logger.error('Request failed: %s', error)
        return None


async def get_links(url: str) -> dict:
    """Extracts video information from a Reddit URL
    and returns it as a dictionary.
    """
    links_url = clear_url(url)
    res = requests.get(links_url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    logger.debug('Response status code: %s', res.status_code)

    def get_find_json(res_json):
        find_json = res_json[0]['data'].get('children', [{}])[0]['data']

        # Проверяем, существует ли ключ 'crosspost_parent_list' и не пуст ли список
        if 'crosspost_parent_list' in find_json and find_json['crosspost_parent_list']:
            find_json = find_json.get('crosspost_parent_list', [{}])[0]
        return find_json

    def get_caption(res_json):
        return res_json[0]['data'].get('children', [{}])[0]['data'].get(
            'title')

    def is_deleted(res_json):
        return get_find_json(res_json).get('removed_by_category') == 'deleted'

    def is_image(res_json):
        file = res_json[0]['data'].get('children', [{}])[0]['data'].get(
            'post_hint', [{}])
        return file == 'image'

    def is_nsfw(res_json):
        try:
            return 'nsfw' in get_find_json(res_json).get('thumbnail')
        except AttributeError:
            return False

    def is_redgifs(res_json):
        try:
            return 'redgifs.com' in get_find_json(res_json).get('media').get(
                'type')
        except AttributeError:
            return False

    def is_gallery(res_json):
        try:
            return get_find_json(res_json).get('is_gallery')
        except AttributeError:
            return False

    async def get_video_links(fallback_url, dict_video):
        max_resol = fallback_url.split('_')[1].split('.')[0]
        max_resol_link = urljoin(fallback_url, urlparse(fallback_url).path)
        size = await size_file(max_resol_link)
        logger.debug('File size: %s MB', size)
        if size < 50:
            dict_video[f'{max_resol}p {size:.1f}mb'] = max_resol_link
        logger.debug(dict_video)
        return dict_video

    try:
        res_json = res.json()
        video_link = {}
        if is_deleted(res_json):
            return {'error': 'Deleted'}

        if 'reddit_video_preview' in res.text:
            find_json = get_find_json(res_json).get('preview', {}).get(
                'reddit_video_preview', {})

            dash_url = find_json.get('dash_url')
            if dash_url:
                dash = requests.get(dash_url, headers=HEADERS, timeout=10).text
                url_dl = dash_url.split('DASHPlaylist.mpd')[0]
                video_link = await parse_xml(dash, url_dl)

            video_link['caption'] = get_caption(res_json)
            fallback_url = find_json.get('fallback_url')
            return await get_video_links(fallback_url, video_link)

        if 'reddit_video' in res.text:
            find_json = get_find_json(res_json).get('secure_media', {}).get(
                'reddit_video', {})

            dash_url = find_json.get('dash_url')
            if dash_url:
                dash = requests.get(dash_url, headers=HEADERS, timeout=10).text
                url_dl = get_find_json(res_json).get(
                    'url_overridden_by_dest', '') + '/'
                video_link = await parse_xml(dash, url_dl)

            video_link['caption'] = get_caption(res_json)
            video_link['nsfw'] = is_nsfw(res_json)
            fallback_url = find_json.get('fallback_url')
            return await get_video_links(fallback_url, video_link)

        if is_image(res_json):
            video_link['image'] = res_json[0]['data'].get('children', [{}])[0][
                'data'].get('urlurl', [{}])
            video_link['caption'] = get_caption(res_json)
            return video_link

        if is_redgifs(res_json):
            redgifs_url = get_find_json(res_json).get('url').split('/watch/')[
                1]
            video_link['redgifs'] = redgifs_url
            video_link['caption'] = get_caption(res_json)
            return video_link

        if is_gallery(res_json):
            gallery_data = get_find_json(res_json).get('gallery_data', {})
            media_metadata = get_find_json(res_json).get('media_metadata', {})
            photos = [
                InputMediaPhoto(
                    media_metadata.get(item['media_id'], {}).get('s', {}).get('u', '').replace('&amp;', '&'),
                    caption=get_caption(res_json) if i == 0 else None,
                ) for i, item in enumerate(gallery_data.get('items', []))
            ]
            return {'gallery': photos}
        return {}
    except (
            json.JSONDecodeError,
            requests.exceptions.RequestException
    ) as error:
        logger.error('Error: %s', error)
        return {}


def chunks(gallery, count):
    for i in range(0, len(gallery), count):
        yield gallery[i:i + count]


async def bot_get_links_private(message: types.Message, state: FSMContext) -> None:
    """Send a message with buttons to download the video"""
    msg = await message.answer(en.GET_LINKS_FOR_VIDEO)
    links = await get_links(message.text)
    logger.debug(links)
    if not links:
        logger.info('The links dictionary is empty, sending an error message')
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    elif 'error' in links:
        if links['error'] == 'Deleted':
            logger.info('Video deleted, sending an error message')
            await msg.edit_text(en.SOURCE_DELETED)
    elif 'redgifs' in links:
        await msg.edit_text(en.SENDING_REDGIFS)
        video = await get_redgifs(links['redgifs'])
        if not video:
            await msg.edit_text(en.VIDEO_NOT_FOUND)
        else:
            logger.info(
                'Send redgifs to user %s (%s) id %s',
                message.from_user.username,
                message.from_user.full_name,
                message.from_user.id
            )
            await message.answer_video(video, caption=links['caption'])
            await msg.delete()
    elif 'image' in links:
        await msg.edit_text(en.SENDING_IMAGE)
        logger.info(
            'Send is_image to user %s (%s) id %s',
            message.from_user.username,
            message.from_user.full_name,
            message.from_user.id
        )
        try:
            if os.path.splitext(links['image'])[1] == '.gif':
                await message.answer_animation(links['image'], caption=links['caption'])
            else:
                await message.answer_photo(
                    links['image'], caption=links['caption'])
            await msg.delete()
        except Exception as e:
            await msg.edit_text(en.UNEXPECTED_ERROR)
            logger.error(f'Ошибка при отправке GIF: {e}')
    elif 'gallery' in links:
        # await msg.edit_text(en.SENDING_GALLERY)
        logger.info(
            'Send gallery to user %s (%s) id %s',
            message.from_user.username,
            message.from_user.full_name,
            message.from_user.id
        )
        gallery = links['gallery']
        retry_delay = 5
        for chunk in chunks(gallery, 10):
            while True:
                try:
                    if len(chunk) >= 2:
                        await message.answer_media_group(chunk)
                    else:
                        await message.answer_photo(chunk[0].media)
                    break  # Выйти из цикла после успешной отправки
                except RetryAfter as e:
                    logger.info(f'Flood limit exceeded. Sleep for {retry_delay} seconds')
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Увеличиваем задержку в 2 раза для следующей попытки
                except Exception as e:
                    logger.error(f'Unexpected error: {e}')
                    await msg.edit_text(en.UNEXPECTED_ERROR)
                    break  # Прерываем цикл в случае других ошибок
        await msg.delete()
    else:
        logger.debug('There are links, sending a message with buttons')
        nsfw = links.pop('nsfw', None)
        caption = links.pop('caption', None)
        audio = links.pop('audio', None)
        users[message.from_user.id] = {
            'caption': caption,
            'audio': audio,
            'nsfw': nsfw,
            'links': links,
        }
        await msg.edit_text(
            text=en.VIDEO_QUALITY,
            reply_markup=create_inline_kb(2, **links)
        )


async def download_video(video_link: str, audio_link: str) -> bytes:
    """Download video and audio"""
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
        # nsfw = users[callback.from_user.id]['nsfw'] # for future use
        video_content = await download_video(video_link, audio_link)
        await callback.message.edit_text(text=en.SENDING_VIDEO)
        logger.info(
            'Send video to user %s (%s) id %s',
            callback.from_user.username,
            callback.from_user.full_name,
            callback.from_user.id
        )
        await callback.message.answer_video(
            video=video_content,
            caption=users[callback.from_user.id]['caption'],
        )
        await callback.message.delete()

    # Добавляем обработку исключений FFmpegError
    except FFmpegError as error:
        logger.critical('FFmpeg error occurred: %s', error)
        await callback.message.edit_text(en.FAILED_TO_PROCESS_VIDEO)

    except aiohttp.ClientResponseError as error:
        logging.critical('Failed to send video: %s', error)
        await callback.message.answer(en.FAILED_TO_SEND_VIDEO)

    except aiohttp.ClientPayloadError as error:
        logging.critical('Failed to send video: %s', error)
        await callback.message.answer(en.FAILED_TO_SEND_VIDEO)

    except aiohttp.ServerDisconnectedError as error:
        logging.critical('Failed to send video: %s', error)
        await callback.message.answer(en.FAILED_TO_SEND_VIDEO)

    except aiohttp.ClientConnectionError as error:
        logging.critical('Failed to send video: %s', error)
        await callback.message.answer(en.FAILED_TO_SEND_VIDEO)

    except Exception as error:  # Перехватываем все остальные исключения
        logging.critical('Unexpected error occurred: %s', error)
        await callback.message.answer(en.UNEXPECTED_ERROR)


async def bot_get_links_group(message: types.Message) -> None:
    """Send video to a group or channel in the second-to-last quality"""
    msg = await message.answer(text=en.GET_LINKS_FOR_VIDEO)
    links = await get_links(message.text)
    logger.debug(links)
    try:
        # Попытка удалить сообщение пользователя
        await message.delete()
    except Exception as e:
        # Обработка возможных исключений
        logger.error(f'Ошибка при попытке удаления сообщения пользователя: {e}')
    if not links:
        logger.info(
            'The dictionary of links is empty, sending an error message.'
        )
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    elif 'error' in links:
        if links['error'] == 'Deleted':
            logger.info('Video deleted, sending an error message')
            await msg.edit_text(en.SOURCE_DELETED)
    elif 'redgifs' in links:
        await msg.edit_text(en.SENDING_REDGIFS)
        video = await get_redgifs(links['redgifs'])
        if not video:
            await msg.edit_text(en.VIDEO_NOT_FOUND)
        else:
            logger.info(
                'Sending redgifs for chat %s, %s id %s',
                message.chat.title,
                message.chat.type,
                message.chat.id
            )
            await message.answer_video(video, caption=links['caption'])
            await msg.delete()
    elif 'image' in links:
        await msg.edit_text(en.SENDING_IMAGE)
        logger.info(
            'Sending image for chat %s, %s id %s',
            message.chat.title,
            message.chat.type,
            message.chat.id
        )
        try:
            if os.path.splitext(links['image'])[1] == '.gif':
                await message.answer_animation(links['image'], caption=links['caption'])
            else:
                await message.answer_photo(
                    links['image'], caption=links['caption'])
            await msg.delete()
        except Exception as e:
            await msg.edit_text(en.UNEXPECTED_ERROR)
            logger.error(f'Ошибка при отправке GIF: {e}')
    elif 'gallery' in links:
        # await msg.edit_text(en.SENDING_GALLERY)
        logger.info(
            'Sending gallery for chat %s, %s id %s',
            message.chat.title,
            message.chat.type,
            message.chat.id
        )
        gallery = links['gallery']
        retry_delay = 5
        for chunk in chunks(gallery, 10):
            while True:
                try:
                    if len(chunk) >= 2:
                        await message.answer_media_group(chunk)
                    else:
                        await message.answer_photo(chunk[0].media)
                    break  # Выйти из цикла после успешной отправки
                except RetryAfter as e:
                    logger.info(f'Flood limit exceeded. Sleep for {retry_delay} seconds')
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Увеличиваем задержку в 2 раза для следующей попытки
                except Exception as e:
                    logger.error(f'Unexpected error: {e}')
                    await msg.edit_text(en.UNEXPECTED_ERROR)
                    break  # Прерываем цикл в случае других ошибок
        await msg.delete()
    else:
        try:
            await msg.edit_text(text=en.DOWNLOADING_VIDEO)
            audio_link = links.pop('audio', None)
            caption = links.pop('caption', None)
            nsfw = links.pop('nsfw', None)
            if len(links) > 1:
                video_link = list(links.values())[-2]
            else:
                video_link = list(links.values())[0]
            video_content = await download_video(video_link, audio_link)
            await msg.edit_text(text=en.SENDING_VIDEO)
            logger.info(
                'Sending video for chat %s, %s id %s',
                message.chat.title,
                message.chat.type,
                message.chat.id
            )
            await message.answer_video(video=video_content, caption=caption)
            await msg.delete()

        # Добавляем обработку исключений FFmpegError
        except FFmpegError as error:
            logger.critical('FFmpeg error occurred: %s', error)
            await msg.edit_text(en.FAILED_TO_PROCESS_VIDEO)

        except aiohttp.ClientResponseError as error:
            logging.critical('Failed to send video: %s', error)
            await msg.edit_text(en.FAILED_TO_SEND_VIDEO)

        except aiohttp.ClientPayloadError as error:
            logging.critical('Failed to send video: %s', error)
            await msg.edit_text(en.FAILED_TO_SEND_VIDEO)

        except aiohttp.ServerDisconnectedError as error:
            logging.critical('Failed to send video: %s', error)
            await msg.edit_text(en.FAILED_TO_SEND_VIDEO)

        except aiohttp.ClientConnectionError as error:
            logging.critical('Failed to send video: %s', error)
            await msg.edit_text(en.FAILED_TO_SEND_VIDEO)


async def bot_send_video_cancel(callback: CallbackQuery) -> None:
    """Cancel sending video"""
    logger.info(
        'User %s, id %s canceled sending video',
        callback.from_user.full_name,
        callback.from_user.id
    )
    await callback.message.edit_text(
        text=en.SEND_VIDEO_CANCEL)


def register_get_links(dp: Dispatcher) -> None:
    """Register handlers for get links"""
    dp.register_message_handler(
        bot_get_links_private, regexp='https://(www\.)?reddit\.com/r/',
        chat_type=types.ChatType.PRIVATE)
    dp.register_callback_query_handler(
        bot_send_video, text_endswith='mb',
        chat_type=types.ChatType.PRIVATE)
    dp.register_callback_query_handler(
        bot_send_video_cancel, text_endswith='cancel',
        chat_type=types.ChatType.PRIVATE)
    dp.register_message_handler(
        bot_get_links_group, regexp='https://(www\.)?reddit\.com/r/')
