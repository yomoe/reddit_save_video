import asyncio

from aiogram.utils.exceptions import RetryAfter
from aiogram.dispatcher import FSMContext
import logging
import os
import tempfile
from io import BytesIO
from urllib.parse import urlparse

import aiohttp
import ffmpeg
from aiogram import Dispatcher, types
from aiogram.types import (
    InputMediaAnimation,
    InputMediaDocument,
    InputFile,
)

from tgbot.lexicon import lexicon_en as en
from tgbot.services.reddit import (
    DeletedResult,
    HEADERS,
    RedditGalleryResult,
    RedditImageResult,
    RedditVideoResult,
    RedgifsResult,
    get_links,
    get_redgifs,
)

logger = logging.getLogger(__name__)


class FFmpegError(Exception):
    """Исключение, возникающее при ошибках работы с FFmpeg."""
    pass


async def concat_video_audio(video_link: str, audio_link: str) -> bytes:
    output_data = None  # Инициализируем переменную для результата
    video_file_name = None
    audio_file_name = None
    output_file_name = None
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
            if not filename:
                continue
            try:
                os.remove(filename)
                logger.debug(f'Deleted temp file: {filename}')
            except Exception as delete_error:
                logger.error(f'Failed to delete temp file {filename}: {delete_error}')

    if output_data:
        return output_data
    # Если output_data отсутствует, возбуждаем исключение FFmpegError
    raise FFmpegError('Failed to concat video and audio files due to FFmpeg error.')


def chunks(gallery, count):
    for i in range(0, len(gallery), count):
        yield gallery[i:i + count]


def get_best_video_link(result: RedditVideoResult) -> str:
    return max(result.variants, key=lambda variant: variant.resolution).url


def get_group_video_link(result: RedditVideoResult) -> str:
    if len(result.variants) > 1:
        return result.variants[-2].url
    return result.variants[0].url


async def send_video_result(
        message: types.Message,
        msg: types.Message,
        result: RedditVideoResult,
        video_selector
) -> None:
    try:
        if not result.variants:
            logger.info('No video variants fit the size limit')
            await msg.edit_text(en.VIDEO_NOT_FOUND)
            return

        await msg.edit_text(text=en.DOWNLOADING_VIDEO)
        video_link = video_selector(result)
        video_content = await download_video(video_link, result.audio_url)
        await msg.edit_text(text=en.SENDING_VIDEO)
        logger.info(
            'Sending video for chat %s, %s id %s',
            message.chat.title,
            message.chat.type,
            message.chat.id
        )
        await message.answer_video(video=video_content, caption=result.caption)
        await msg.delete()

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

    except Exception as error:
        logging.critical('Unexpected error occurred: %s', error)
        await msg.edit_text(en.UNEXPECTED_ERROR)


async def bot_get_links_private(message: types.Message, state: FSMContext) -> None:
    """Download and send the best available video."""
    msg = await message.answer(en.GET_LINKS_FOR_VIDEO)
    result = await get_links(message.text)
    logger.debug(result)
    if not result:
        logger.info('The links dictionary is empty, sending an error message')
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    elif isinstance(result, DeletedResult):
        logger.info('Video deleted, sending an error message')
        await msg.edit_text(en.SOURCE_DELETED)
    elif isinstance(result, RedgifsResult):
        await msg.edit_text(en.SENDING_REDGIFS)
        video = await get_redgifs(result.url_id)
        if not video:
            await msg.edit_text(en.VIDEO_NOT_FOUND)
        else:
            logger.info(
                'Send redgifs to user %s (%s) id %s',
                message.from_user.username,
                message.from_user.full_name,
                message.from_user.id
            )
            await message.answer_video(video, caption=result.caption)
            await msg.delete()
    elif isinstance(result, RedditImageResult):
        await msg.edit_text(en.SENDING_IMAGE)
        logger.info(
            'Send is_image to user %s (%s) id %s',
            message.from_user.username,
            message.from_user.full_name,
            message.from_user.id
        )
        try:
            ext = os.path.splitext(urlparse(result.url).path)[1].lower()
            if ext == '.gif':
                try:
                    data = await download_file(result.url)
                except Exception as e:
                    logger.error('Failed to download gif: %s', e)
                    await msg.edit_text(en.UNEXPECTED_ERROR)
                    return
                await message.answer_animation(
                    InputFile(BytesIO(data), filename='file.gif'),
                    caption=result.caption
                )
            else:
                await message.answer_photo(
                    result.url, caption=result.caption)
            await msg.delete()
        except Exception as e:
            await msg.edit_text(en.UNEXPECTED_ERROR)
            logger.error(f'Ошибка при отправке изображения: {e}')
    elif isinstance(result, RedditGalleryResult):
        # await msg.edit_text(en.SENDING_GALLERY)
        logger.info(
            'Send gallery to user %s (%s) id %s',
            message.from_user.username,
            message.from_user.full_name,
            message.from_user.id
        )
        gallery = result.media
        documents = [m for m in gallery if isinstance(m, InputMediaDocument)]
        media = [m for m in gallery if not isinstance(m, InputMediaDocument)]
        retry_delay = 5
        for chunk in chunks(media, 10):
            while True:
                try:
                    if len(chunk) >= 2:
                        await message.answer_media_group(chunk)
                    else:
                        media_item = chunk[0]
                        if isinstance(media_item, InputMediaAnimation):
                            await message.answer_animation(media_item.media, caption=media_item.caption)
                        else:
                            await message.answer_photo(media_item.media, caption=media_item.caption)
                    break  # Выйти из цикла после успешной отправки
                except RetryAfter as e:
                    logger.info(f'Flood limit exceeded. Sleep for {retry_delay} seconds')
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Увеличиваем задержку в 2 раза для следующей попытки
                except Exception as e:
                    logger.error(f'Unexpected error: {e}')
                    await msg.edit_text(en.UNEXPECTED_ERROR)
                    break  # Прерываем цикл в случае других ошибок
        for document in documents:
            while True:
                try:
                    await message.answer_document(document.media, caption=document.caption)
                    break
                except RetryAfter as e:
                    logger.info(f'Flood limit exceeded. Sleep for {retry_delay} seconds')
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                except Exception as e:
                    logger.error(f'Unexpected error: {e}')
                    await msg.edit_text(en.UNEXPECTED_ERROR)
                    break
        await msg.delete()
    else:
        await send_video_result(message, msg, result, get_best_video_link)


async def download_video(video_link: str, audio_link: str) -> bytes:
    """Download video and audio"""
    if audio_link and audio_link != 'false':
        video_content = await concat_video_audio(video_link, audio_link)
    else:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(video_link) as response:
                response.raise_for_status()
                video_content = await response.read()
    return video_content


async def download_file(url: str) -> bytes:
    """Download a file and return bytes."""
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.read()
        except Exception as error:
            logger.error('Failed to download %s: %s', url, error)
            raise


async def bot_get_links_group(message: types.Message) -> None:
    """Send video to a group or channel in the second-to-last quality"""
    msg = await message.answer(text=en.GET_LINKS_FOR_VIDEO)
    result = await get_links(message.text)
    logger.debug(result)
    try:
        # Попытка удалить сообщение пользователя
        await message.delete()
    except Exception as e:
        # Обработка возможных исключений
        logger.error(f'Ошибка при попытке удаления сообщения пользователя: {e}')
    if not result:
        logger.info(
            'The dictionary of links is empty, sending an error message.'
        )
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    elif isinstance(result, DeletedResult):
        logger.info('Video deleted, sending an error message')
        await msg.edit_text(en.SOURCE_DELETED)
    elif isinstance(result, RedgifsResult):
        await msg.edit_text(en.SENDING_REDGIFS)
        video = await get_redgifs(result.url_id)
        if not video:
            await msg.edit_text(en.VIDEO_NOT_FOUND)
        else:
            logger.info(
                'Sending redgifs for chat %s, %s id %s',
                message.chat.title,
                message.chat.type,
                message.chat.id
            )
            await message.answer_video(video, caption=result.caption)
            await msg.delete()
    elif isinstance(result, RedditImageResult):
        await msg.edit_text(en.SENDING_IMAGE)
        logger.info(
            'Sending image for chat %s, %s id %s',
            message.chat.title,
            message.chat.type,
            message.chat.id
        )
        try:
            ext = os.path.splitext(urlparse(result.url).path)[1].lower()
            if ext == '.gif':
                try:
                    data = await download_file(result.url)
                except Exception as e:
                    logger.error('Failed to download gif: %s', e)
                    await msg.edit_text(en.UNEXPECTED_ERROR)
                    return
                await message.answer_animation(
                    InputFile(BytesIO(data), filename='file.gif'),
                    caption=result.caption
                )
            else:
                await message.answer_photo(
                    result.url, caption=result.caption)
            await msg.delete()
        except Exception as e:
            await msg.edit_text(en.UNEXPECTED_ERROR)
            logger.error(f'Ошибка при отправке изображения: {e}')
    elif isinstance(result, RedditGalleryResult):
        # await msg.edit_text(en.SENDING_GALLERY)
        logger.info(
            'Sending gallery for chat %s, %s id %s',
            message.chat.title,
            message.chat.type,
            message.chat.id
        )
        gallery = result.media
        documents = [m for m in gallery if isinstance(m, InputMediaDocument)]
        media = [m for m in gallery if not isinstance(m, InputMediaDocument)]
        retry_delay = 5
        for chunk in chunks(media, 10):
            while True:
                try:
                    if len(chunk) >= 2:
                        await message.answer_media_group(chunk)
                    else:
                        media_item = chunk[0]
                        if isinstance(media_item, InputMediaAnimation):
                            await message.answer_animation(media_item.media, caption=media_item.caption)
                        else:
                            await message.answer_photo(media_item.media, caption=media_item.caption)
                    break  # Выйти из цикла после успешной отправки
                except RetryAfter as e:
                    logger.info(f'Flood limit exceeded. Sleep for {retry_delay} seconds')
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Увеличиваем задержку в 2 раза для следующей попытки
                except Exception as e:
                    logger.error(f'Unexpected error: {e}')
                    await msg.edit_text(en.UNEXPECTED_ERROR)
                    break  # Прерываем цикл в случае других ошибок
        for document in documents:
            while True:
                try:
                    await message.answer_document(document.media, caption=document.caption)
                    break
                except RetryAfter as e:
                    logger.info(f'Flood limit exceeded. Sleep for {retry_delay} seconds')
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                except Exception as e:
                    logger.error(f'Unexpected error: {e}')
                    await msg.edit_text(en.UNEXPECTED_ERROR)
                    break
        await msg.delete()
    else:
        await send_video_result(message, msg, result, get_group_video_link)


def register_get_links(dp: Dispatcher) -> None:
    """Register handlers for get links"""
    dp.register_message_handler(
        bot_get_links_private, regexp=r'https://(www\.)?reddit\.com/r/',
        chat_type=types.ChatType.PRIVATE)
    dp.register_message_handler(
        bot_get_links_group, regexp=r'https://(www\.)?reddit\.com/r/')
