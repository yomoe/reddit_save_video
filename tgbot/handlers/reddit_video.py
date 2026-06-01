import asyncio
import html
import re

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
from aiogram.utils.exceptions import MessageNotModified, NetworkError, WrongFileIdentifier
from aiogram.types import (
    InputMediaDocument,
    InputFile,
    InputMediaPhoto,
    InputMediaVideo,
)

from tgbot.lexicon import lexicon_en as en
from tgbot.services.reddit import (
    DeletedResult,
    HEADERS,
    MAX_FILE_SIZE_MB,
    RedditGalleryItem,
    RedditGalleryResult,
    RedditImageResult,
    RedditPostMeta,
    RedditVideoResult,
    RedgifsResult,
    get_links,
    get_redgifs,
)

logger = logging.getLogger(__name__)
CAPTION_LIMIT = 1024
TELEGRAM_RETRY_ATTEMPTS = 3
TELEGRAM_RETRY_DELAY = 2
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


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
        logger.exception('FFmpeg error while combining video=%s audio=%s', video_link, audio_link)
        # Вместо общего исключения, возбуждаем наше специализированное исключение
        raise FFmpegError('Failed to concat video and audio files due to FFmpeg error.') from ffmpeg_error


    except Exception as e:
        logger.exception('Failed to concat video=%s audio=%s: %r', video_link, audio_link, e)
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


def format_user(message: types.Message) -> str:
    user = message.from_user
    if not user:
        return 'unknown user'

    username = f'@{user.username}' if user.username else 'no username'
    full_name = user.full_name or 'no name'
    return f'{full_name} ({username}, id {user.id})'


def format_chat(message: types.Message) -> str:
    chat = message.chat
    title = chat.title or chat.full_name or chat.username or 'private chat'
    return f'{title} ({chat.type}, id {chat.id})'


def format_source(result) -> str:
    meta = getattr(result, 'meta', None)
    if meta and meta.permalink:
        return meta.permalink
    return 'no permalink'


def log_request(message: types.Message) -> None:
    logger.info(
        'Reddit request from %s in %s: %s',
        format_user(message),
        format_chat(message),
        message.text,
    )


def log_result(message: types.Message, result) -> None:
    logger.info(
        'Reddit result for %s: type=%s source=%s',
        format_user(message),
        type(result).__name__ if result else None,
        format_source(result) if result else 'none',
    )


async def safe_edit_text(msg: types.Message, text: str) -> None:
    try:
        await msg.edit_text(text)
    except MessageNotModified:
        logger.debug('Skip message edit because text is unchanged: %s', text)


def normalize_hashtag(value: str | None) -> str | None:
    if not value:
        return None
    hashtag = re.sub(r'[^\w]+', '_', value, flags=re.UNICODE).strip('_')
    return f'#{hashtag}' if hashtag else None


def build_hashtags(meta: RedditPostMeta) -> list[str]:
    hashtags = []
    subreddit = normalize_hashtag(f'r_{meta.subreddit}' if meta.subreddit else None)
    flair = normalize_hashtag(meta.flair)
    for hashtag in (subreddit, flair):
        if hashtag and hashtag not in hashtags:
            hashtags.append(hashtag)
    return hashtags


def fit_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return ''
    return text[:limit - 1].rstrip() + '…'


def build_caption(meta: RedditPostMeta, limit: int = CAPTION_LIMIT) -> str:
    title = meta.title or 'Reddit post'
    description = (meta.description or '').strip()
    hashtags = ' '.join(build_hashtags(meta))

    def render(title_text: str, description_text: str) -> str:
        title_block = f'<b>{html.escape(title_text)}</b>'
        body = html.escape(description_text) if description_text else ''
        footer = f'\n\n{html.escape(hashtags)}' if hashtags else ''
        if body:
            return f'{title_block}\n\n{body}{footer}'
        return f'{title_block}{footer}'

    title_limit = min(len(title), 300)
    description_limit = min(len(description), limit)
    caption = render(fit_text(title, title_limit), fit_text(description, description_limit))

    while len(caption) > limit and description_limit > 0:
        overflow = len(caption) - limit
        description_limit = max(0, description_limit - overflow - 1)
        caption = render(fit_text(title, title_limit), fit_text(description, description_limit))

    while len(caption) > limit and title_limit > 1:
        overflow = len(caption) - limit
        title_limit = max(1, title_limit - overflow - 1)
        caption = render(fit_text(title, title_limit), '')

    return caption


async def telegram_retry(action, description: str, message: types.Message):
    retry_delay = TELEGRAM_RETRY_DELAY
    for attempt in range(1, TELEGRAM_RETRY_ATTEMPTS + 1):
        try:
            return await action()
        except NetworkError as error:
            if 'File too large for uploading' in str(error):
                logger.error('Telegram rejected oversized file while %s for %s', description, format_user(message))
                raise
            if attempt == TELEGRAM_RETRY_ATTEMPTS:
                logger.exception(
                    'Telegram network error after %s attempts while %s for %s',
                    attempt,
                    description,
                    format_user(message),
                )
                raise
            logger.warning(
                'Telegram network error while %s for %s. Retry %s/%s in %s seconds',
                description,
                format_user(message),
                attempt + 1,
                TELEGRAM_RETRY_ATTEMPTS,
                retry_delay,
            )
            await asyncio.sleep(retry_delay)
            retry_delay *= 2


def get_best_video_variants(result: RedditVideoResult):
    return sorted(result.variants, key=lambda variant: variant.resolution, reverse=True)


def get_group_video_variants(result: RedditVideoResult):
    if len(result.variants) > 1:
        selected_index = len(result.variants) - 2
        return list(reversed(result.variants[:selected_index + 1]))
    return result.variants


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
        video_content = None
        selected_variant = None
        for variant in video_selector(result):
            logger.info(
                'Selected video candidate for %s: %sp %.1fmb url=%s source=%s',
                format_user(message),
                variant.resolution,
                variant.size_mb,
                variant.url,
                result.meta.permalink,
            )
            current_video_content = await download_video(variant.url, result.audio_url)
            current_size = len(current_video_content)
            logger.info(
                'Downloaded video candidate for %s: %sp final_size=%.1fmb source=%s',
                format_user(message),
                variant.resolution,
                current_size / 1024 / 1024,
                result.meta.permalink,
            )
            if current_size <= MAX_FILE_SIZE_BYTES:
                video_content = current_video_content
                selected_variant = variant
                break

            logger.warning(
                'Skipping video candidate for %s: %sp final_size=%.1fmb exceeds %.1fmb source=%s',
                format_user(message),
                variant.resolution,
                current_size / 1024 / 1024,
                MAX_FILE_SIZE_MB,
                result.meta.permalink,
            )

        if video_content is None or selected_variant is None:
            logger.info('No downloaded video variants fit the final size limit')
            await msg.edit_text(en.VIDEO_NOT_FOUND)
            return

        logger.info(
            'Selected video for %s: %sp %.1fmb url=%s source=%s',
            format_user(message),
            selected_variant.resolution,
            selected_variant.size_mb,
            selected_variant.url,
            result.meta.permalink,
        )
        await msg.edit_text(text=en.SENDING_VIDEO)
        logger.info(
            'Sending video to %s in %s source=%s',
            format_user(message),
            format_chat(message),
            result.meta.permalink,
        )
        await telegram_retry(
            lambda: message.answer_video(
                video=video_content,
                caption=build_caption(result.meta),
            ),
            'sending video',
            message,
        )
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

    except NetworkError as error:
        logger.exception('Telegram network error while sending video to %s', format_user(message))
        await msg.edit_text(en.FAILED_TO_SEND_VIDEO)

    except Exception as error:
        logger.exception('Unexpected error while sending video to %s: %r', format_user(message), error)
        await msg.edit_text(en.UNEXPECTED_ERROR)


async def send_redgifs_result(
        message: types.Message,
        msg: types.Message,
        result: RedgifsResult
) -> None:
    await msg.edit_text(en.SENDING_REDGIFS)
    video = await get_redgifs(result.url_id)
    if not video:
        await msg.edit_text(en.VIDEO_NOT_FOUND)
        return

    logger.info(
        'Sending redgifs to %s in %s redgifs_id=%s source=%s',
        format_user(message),
        format_chat(message),
        result.url_id,
        result.meta.permalink,
    )
    try:
        await telegram_retry(
            lambda: message.answer_video(
                InputFile(BytesIO(video), filename=f'{result.url_id}.mp4'),
                caption=build_caption(result.meta),
                supports_streaming=True,
            ),
            'sending redgifs',
            message,
        )
        await msg.delete()
    except NetworkError:
        await msg.edit_text(en.FAILED_TO_SEND_VIDEO)


async def send_image_result(
        message: types.Message,
        msg: types.Message,
        result: RedditImageResult
) -> None:
    await msg.edit_text(en.SENDING_IMAGE)
    logger.info(
        'Sending image to %s in %s image_url=%s source=%s',
        format_user(message),
        format_chat(message),
        result.url,
        result.meta.permalink,
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
            if len(data) > MAX_FILE_SIZE_BYTES:
                logger.warning(
                    'Skipping gif image for %s because file is too large: %.1f MB source=%s',
                    format_user(message),
                    len(data) / 1024 / 1024,
                    result.meta.permalink,
                )
                await msg.edit_text(en.VIDEO_NOT_FOUND)
                return
            await telegram_retry(
                lambda: message.answer_animation(
                    InputFile(BytesIO(data), filename='file.gif'),
                    caption=build_caption(result.meta),
                ),
                'sending gif image',
                message,
            )
        else:
            await telegram_retry(
                lambda: message.answer_photo(
                    result.url,
                    caption=build_caption(result.meta),
                ),
                'sending image',
                message,
            )
        await msg.delete()
    except Exception as e:
        await msg.edit_text(en.UNEXPECTED_ERROR)
        logger.exception('Failed to send image to %s: %r', format_user(message), e)


async def send_gallery_result(
        message: types.Message,
        msg: types.Message,
        result: RedditGalleryResult
) -> None:
    logger.info(
        'Sending gallery to %s in %s items=%s source=%s',
        format_user(message),
        format_chat(message),
        len(result.media),
        result.meta.permalink,
    )
    caption = build_caption(result.meta)
    album = []
    documents: list[InputMediaDocument] = []
    skipped = 0
    first_caption_added = False

    def next_caption() -> str | None:
        nonlocal first_caption_added
        if first_caption_added:
            return None
        first_caption_added = True
        return caption

    async def build_gallery_media(item: RedditGalleryItem):
        if item.kind == 'photo':
            return {
                'kind': 'photo',
                'media': item.url,
                'caption': next_caption(),
            }

        if item.kind == 'document':
            return InputMediaDocument(item.url, caption=next_caption())

        if item.kind in ('video', 'redgifs'):
            if item.kind == 'redgifs':
                if not item.redgifs_id:
                    logger.warning('Skipping RedGifs gallery item without id: %s', item)
                    return None
                video_content = await get_redgifs(item.redgifs_id)
                filename = f'{item.redgifs_id}.mp4'
            else:
                logger.info(
                    'Downloading gallery video preview for %s media_id=%s url=%s',
                    format_user(message),
                    item.media_id,
                    item.url,
                )
                video_content = await download_file(item.url)
                filename = f'{item.media_id or "reddit-gallery"}.mp4'

            if not video_content:
                logger.warning('Skipping gallery video due to empty download: %s', item)
                return None
            if len(video_content) > MAX_FILE_SIZE_BYTES:
                logger.warning(
                    'Skipping gallery video for %s because file is too large: %.1f MB media_id=%s redgifs_id=%s',
                    format_user(message),
                    len(video_content) / 1024 / 1024,
                    item.media_id,
                    item.redgifs_id,
                )
                return None
            return {
                'kind': 'video',
                'media': video_content,
                'filename': filename,
                'caption': next_caption(),
            }

        logger.warning('Skipping unknown gallery item kind=%s media_id=%s url=%s', item.kind, item.media_id, item.url)
        return None

    for item in result.media:
        try:
            media_item = await build_gallery_media(item)
        except Exception as error:
            skipped += 1
            logger.exception(
                'Failed to prepare gallery item for %s kind=%s media_id=%s redgifs_id=%s url=%s: %r',
                format_user(message),
                item.kind,
                item.media_id,
                item.redgifs_id,
                item.url,
                error,
            )
            continue

        if media_item is None:
            skipped += 1
            continue
        if isinstance(media_item, InputMediaDocument):
            documents.append(media_item)
        else:
            album.append(media_item)

    async def send_document(document: InputMediaDocument) -> None:
        try:
            await telegram_retry(
                lambda: message.answer_document(document.media, caption=document.caption),
                'sending gallery document',
                message,
            )
        except WrongFileIdentifier:
            logger.warning(
                'Telegram rejected gallery document URL for %s. Downloading and uploading it instead.',
                format_user(message),
            )
            document_content = await download_file(document.media)
            await telegram_retry(
                lambda: message.answer_document(
                    InputFile(BytesIO(document_content), filename='reddit-gallery.gif'),
                    caption=document.caption,
                ),
                'sending downloaded gallery document',
                message,
            )

    def build_album_item(media_item):
        if media_item['kind'] == 'video':
            return InputMediaVideo(
                InputFile(BytesIO(media_item['media']), filename=media_item['filename']),
                caption=media_item['caption'],
                supports_streaming=True,
            )
        return InputMediaPhoto(media_item['media'], caption=media_item['caption'])

    def build_album_chunk(chunk):
        return [build_album_item(media_item) for media_item in chunk]

    async def send_album_item(media_item) -> None:
        if media_item['kind'] == 'video':
            await telegram_retry(
                lambda: message.answer_video(
                    InputFile(BytesIO(media_item['media']), filename=media_item['filename']),
                    caption=media_item['caption'],
                    supports_streaming=True,
                ),
                'sending gallery video fallback',
                message,
            )
        else:
            await telegram_retry(
                lambda: message.answer_photo(media_item['media'], caption=media_item['caption']),
                'sending gallery photo fallback',
                message,
            )

    retry_delay = 5
    for chunk in chunks(album, 10):
        while True:
            try:
                if len(chunk) >= 2:
                    try:
                        await telegram_retry(
                            lambda: message.answer_media_group(build_album_chunk(chunk)),
                            'sending gallery media group',
                            message,
                        )
                    except WrongFileIdentifier:
                        logger.warning(
                            'Telegram rejected gallery media group for %s. Falling back to single media.',
                            format_user(message),
                        )
                        for media_item in chunk:
                            await send_album_item(media_item)
                else:
                    media_item = chunk[0]
                    await send_album_item(media_item)
                break
            except RetryAfter:
                logger.info(f'Flood limit exceeded. Sleep for {retry_delay} seconds')
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            except Exception as e:
                logger.exception('Unexpected gallery media error for %s: %r', format_user(message), e)
                await safe_edit_text(msg, en.UNEXPECTED_ERROR)
                break
    for chunk in chunks(documents, 10):
        while True:
            try:
                if len(chunk) >= 2:
                    try:
                        await telegram_retry(
                            lambda: message.answer_media_group(chunk),
                            'sending gallery document group',
                            message,
                        )
                    except WrongFileIdentifier:
                        logger.warning(
                            'Telegram rejected gallery document group for %s. Falling back to single documents.',
                            format_user(message),
                        )
                        for document in chunk:
                            await send_document(document)
                else:
                    await send_document(chunk[0])
                break
            except RetryAfter:
                logger.info(f'Flood limit exceeded. Sleep for {retry_delay} seconds')
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            except Exception as e:
                logger.exception('Unexpected gallery document error for %s: %r', format_user(message), e)
                await safe_edit_text(msg, en.UNEXPECTED_ERROR)
                break
    if not album and not documents:
        await safe_edit_text(msg, en.VIDEO_NOT_FOUND)
        return
    if skipped:
        logger.warning('Skipped %s gallery items for %s source=%s', skipped, format_user(message), result.meta.permalink)
    await msg.delete()


async def bot_get_links_private(message: types.Message, state: FSMContext) -> None:
    """Download and send the best available video."""
    msg = await message.answer(en.GET_LINKS_FOR_VIDEO)
    log_request(message)
    result = await get_links(message.text)
    log_result(message, result)
    if not result:
        logger.info('The links dictionary is empty, sending an error message')
        await msg.edit_text(en.VIDEO_NOT_FOUND)
    elif isinstance(result, DeletedResult):
        logger.info('Video deleted, sending an error message')
        await msg.edit_text(en.SOURCE_DELETED)
    elif isinstance(result, RedgifsResult):
        await send_redgifs_result(message, msg, result)
    elif isinstance(result, RedditImageResult):
        await send_image_result(message, msg, result)
    elif isinstance(result, RedditGalleryResult):
        await send_gallery_result(message, msg, result)
    else:
        await send_video_result(message, msg, result, get_best_video_variants)


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
    retry_delay = TELEGRAM_RETRY_DELAY
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout) as session:
        last_error = None
        for attempt in range(1, TELEGRAM_RETRY_ATTEMPTS + 1):
            try:
                async with session.get(url) as response:
                    response.raise_for_status()
                    return await response.read()
            except aiohttp.ClientResponseError as error:
                last_error = error
                if error.status < 500 or attempt == TELEGRAM_RETRY_ATTEMPTS:
                    logger.error('Failed to download %s: %s', url, error)
                    raise
            except (
                    aiohttp.ClientPayloadError,
                    aiohttp.ServerDisconnectedError,
                    aiohttp.ClientConnectionError,
                    asyncio.TimeoutError,
            ) as error:
                last_error = error
                if attempt == TELEGRAM_RETRY_ATTEMPTS:
                    logger.error('Failed to download %s: %s', url, error)
                    raise

            logger.warning(
                'Download failed for %s. Retry %s/%s in %s seconds: %s',
                url,
                attempt + 1,
                TELEGRAM_RETRY_ATTEMPTS,
                retry_delay,
                last_error,
            )
            await asyncio.sleep(retry_delay)
            retry_delay *= 2

        raise RuntimeError(f'Failed to download {url}')


async def bot_get_links_group(message: types.Message) -> None:
    """Send video to a group or channel in the second-to-last quality"""
    msg = await message.answer(text=en.GET_LINKS_FOR_VIDEO)
    log_request(message)
    result = await get_links(message.text)
    log_result(message, result)
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
        await send_redgifs_result(message, msg, result)
    elif isinstance(result, RedditImageResult):
        await send_image_result(message, msg, result)
    elif isinstance(result, RedditGalleryResult):
        await send_gallery_result(message, msg, result)
    else:
        await send_video_result(message, msg, result, get_group_video_variants)


def register_get_links(dp: Dispatcher) -> None:
    """Register handlers for get links"""
    dp.register_message_handler(
        bot_get_links_private,
        regexp=r'https://(www\.)?(reddit\.com/r/|redgifs\.com/(watch|ifr)/)',
        chat_type=types.ChatType.PRIVATE)
    dp.register_message_handler(
        bot_get_links_group,
        regexp=r'https://(www\.)?(reddit\.com/r/|redgifs\.com/(watch|ifr)/)')
