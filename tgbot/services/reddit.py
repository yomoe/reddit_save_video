import asyncio
import html
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import TypeAlias
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp
import ffmpeg
import praw
import prawcore
from bs4 import BeautifulSoup
from environs import Env

logger = logging.getLogger(__name__)

REDDIT_USER_AGENT = os.getenv(
    'REDDIT_USER_AGENT',
    'python:reddit_save_video:1.0 (by u/yo_moe)'
)
HEADERS = {
    'user-agent': REDDIT_USER_AGENT,
    'accept': 'application/json',
}
API_URL_REDGIFS_V1 = 'https://api.redgifs.com/v1/gifs/'
API_URL_REDGIFS_V2 = 'https://api.redgifs.com/v2/gifs/'
API_URL_REDGIFS_AUTH = 'https://api.redgifs.com/v2/auth/temporary'
MAX_FILE_SIZE_MB = 48
reddit_client = None
redgifs_token = None
REDGIFS_ID_RE = re.compile(r'redgifs\.com/(?:watch|ifr)/([a-z0-9-]+)', re.IGNORECASE)


def get_video_resolution(filename: str) -> int | None:
    match = re.search(r'_(\d+)\.', filename)
    if not match:
        return None
    return int(match.group(1))


def normalize_permalink(permalink: str | None) -> str | None:
    if not permalink:
        return None
    if permalink.startswith('/'):
        return urljoin('https://www.reddit.com', permalink)
    return permalink


def iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_strings(item)


def extract_redgifs_id(*values) -> str | None:
    for value in values:
        for text in iter_strings(value):
            match = REDGIFS_ID_RE.search(text)
            if match:
                return match.group(1).lower()
    return None


@dataclass(frozen=True)
class RedgifsVideoSource:
    url: str
    has_audio: bool | None = None


def get_redgifs_video_candidates(url: str, has_audio: bool | None) -> list[str]:
    candidates = []
    if has_audio and '-silent.mp4' in url:
        candidates.append(url.replace('-silent.mp4', '-mobile.mp4'))
        candidates.append(url.replace('-silent.mp4', '.mp4'))
    candidates.append(url)

    unique_candidates = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return unique_candidates


def has_audio_stream(file_data: bytes) -> bool | None:
    tmp_file_name = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_file:
            tmp_file.write(file_data)
            tmp_file_name = tmp_file.name
        probe = ffmpeg.probe(tmp_file_name)
        return any(stream.get('codec_type') == 'audio' for stream in probe.get('streams', []))
    except (ffmpeg.Error, OSError) as error:
        logger.warning('Failed to probe RedGifs audio stream: %s', error)
        return None
    finally:
        if tmp_file_name:
            try:
                os.remove(tmp_file_name)
            except OSError as error:
                logger.warning('Failed to remove RedGifs probe temp file %s: %s', tmp_file_name, error)


@dataclass(frozen=True)
class RedditVideoVariant:
    label: str
    url: str
    resolution: int
    size_mb: float


@dataclass(frozen=True)
class RedditPostMeta:
    title: str | None
    description: str | None = None
    subreddit: str | None = None
    flair: str | None = None
    permalink: str | None = None


@dataclass
class ParsedVideoLinks:
    audio_url: str | None = None
    variants: list[RedditVideoVariant] = field(default_factory=list)


@dataclass(frozen=True)
class RedditVideoResult:
    meta: RedditPostMeta
    variants: list[RedditVideoVariant]
    audio_url: str | None = None
    nsfw: bool = False


@dataclass(frozen=True)
class RedditImageResult:
    meta: RedditPostMeta
    url: str


@dataclass(frozen=True)
class RedditGalleryItem:
    kind: str
    url: str
    media_id: str | None = None
    mime: str | None = None
    redgifs_id: str | None = None


@dataclass(frozen=True)
class RedditGalleryResult:
    meta: RedditPostMeta
    media: list[RedditGalleryItem]


@dataclass(frozen=True)
class RedgifsResult:
    meta: RedditPostMeta
    url_id: str


@dataclass(frozen=True)
class DeletedResult:
    pass


RedditResult: TypeAlias = (
    RedditVideoResult
    | RedditImageResult
    | RedditGalleryResult
    | RedgifsResult
    | DeletedResult
)


async def fetch_json(url: str, params: dict | None = None):
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout) as session:
        async with session.get(url, params=params) as response:
            response.raise_for_status()
            logger.debug('Response status code: %s', response.status)
            return await response.json()


async def fetch_text(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()


async def get_redgifs_token() -> str | None:
    global redgifs_token
    if redgifs_token:
        return redgifs_token

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(headers=HEADERS, timeout=timeout) as session:
        try:
            async with session.get(API_URL_REDGIFS_AUTH) as response:
                response.raise_for_status()
                auth_json = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as error:
            logger.error('Error getting RedGifs auth token: %s', error)
            return None

    redgifs_token = auth_json.get('token')
    return redgifs_token


async def get_redgifs_video_url(url_id: str) -> str | None:
    video_sources = await get_redgifs_video_sources(url_id)
    return video_sources[0].url if video_sources else None


async def get_redgifs_video_sources(url_id: str) -> list[RedgifsVideoSource]:
    global redgifs_token
    token = await get_redgifs_token()
    if token:
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {**HEADERS, 'Authorization': f'Bearer {token}'}
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            try:
                async with session.get(API_URL_REDGIFS_V2 + url_id) as response:
                    if response.status == 401:
                        redgifs_token = None
                        return await get_redgifs_video_sources(url_id)
                    response.raise_for_status()
                    redgifs_json = await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as error:
                logger.error('Error getting RedGifs v2 json from %s: %s', url_id, error)
            else:
                gif = redgifs_json.get('gif', {})
                urls = gif.get('urls', {})
                has_audio = gif.get('hasAudio')
                logger.info('RedGifs %s has_audio=%s url_keys=%s', url_id, has_audio, sorted(urls.keys()))
                video_sources = []
                for video_url in (urls.get('hd'), urls.get('sd')):
                    if not video_url:
                        continue
                    for candidate in get_redgifs_video_candidates(video_url, has_audio):
                        source = RedgifsVideoSource(candidate, has_audio)
                        if source not in video_sources:
                            video_sources.append(source)
                if video_sources:
                    return video_sources

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(API_URL_REDGIFS_V1 + url_id) as response:
                redgifs_json = await response.json()
        except (aiohttp.ClientError, json.JSONDecodeError):
            logger.error('Error getting RedGifs v1 json from %s', url_id)
            return []

    gif = redgifs_json.get('gif', {})
    video_url = (gif.get('urls', {}).get('hd') or redgifs_json.get('gfyItem', {}).get(
        'content_urls', {}).get(
        'mp4', {}).get(
        'url'))
    if not video_url:
        return []
    return [
        RedgifsVideoSource(candidate, gif.get('hasAudio'))
        for candidate in get_redgifs_video_candidates(video_url, gif.get('hasAudio'))
    ]


async def get_redgifs(url_id: str) -> bytes or None:
    """Get the video from redgifs.com."""
    video_sources = await get_redgifs_video_sources(url_id)
    if not video_sources:
        return None
    async with aiohttp.ClientSession() as session:
        for source in video_sources:
            try:
                async with session.get(source.url) as video:
                    video.raise_for_status()
                    file_data = await video.read()
            except aiohttp.ClientError as error:
                logger.warning('Error getting RedGifs video %s from %s: %s', url_id, source.url, error)
                continue

            audio_present = has_audio_stream(file_data)
            if source.has_audio and audio_present is False:
                logger.warning('RedGifs %s candidate has no audio stream: %s', url_id, source.url)
                continue
            file_size_mb = len(file_data) / 1024 / 1024
            if file_size_mb > MAX_FILE_SIZE_MB:
                logger.warning(
                    'RedGifs %s candidate is too large: %.1f MB url=%s',
                    url_id,
                    file_size_mb,
                    source.url,
                )
                continue

            logger.info(
                'Downloaded RedGifs %s from %s size=%.1f MB expected_audio=%s audio_stream=%s',
                url_id,
                source.url,
                file_size_mb,
                source.has_audio,
                audio_present,
            )
            return file_data
    logger.error('No usable RedGifs video source found for %s', url_id)
    return None


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
    except aiohttp.ClientError as error:
        logger.error('Request to %s failed: %s', url, error)
        return 0.0


async def parse_xml(xml: str, url: str) -> ParsedVideoLinks:
    """Find video and audio links from Reddit DASH XML."""
    video_links = ParsedVideoLinks()
    logger.debug('Get xml %s', xml)
    soup = BeautifulSoup(xml, 'xml')

    audio_size = 0.0
    audio_adaptations = soup.find_all('AdaptationSet', {'contentType': 'audio'})
    if audio_adaptations:
        base_url = audio_adaptations[0].find('BaseURL').text
        audio_bandwidth = 0
        for adaptation_set in audio_adaptations:
            for representation in adaptation_set.find_all('Representation'):
                current_bandwidth = int(representation.get('bandwidth', 0))
                if current_bandwidth > audio_bandwidth:
                    base_url = representation.find('BaseURL').text
                    audio_bandwidth = current_bandwidth

        if audio_bandwidth > 0:
            audio = url + base_url
            video_links.audio_url = audio
            audio_size = await size_file(audio)

    for adaptation_set in soup.find_all('AdaptationSet', {'contentType': 'video'}):
        videos = [x.text for x in adaptation_set.find_all('BaseURL')]
        for video in videos:
            resolution = get_video_resolution(video)
            if resolution is None:
                logger.warning('Skipping video with unknown resolution: %s', video)
                continue
            link = url + video
            video_size = await size_file(link)
            total_size = video_size + audio_size
            logger.debug('Video size: %s MB, total with audio: %s MB', video_size, total_size)
            if total_size < MAX_FILE_SIZE_MB:
                video_links.variants.append(RedditVideoVariant(
                    label=f'{resolution}p {total_size:.1f}mb',
                    url=link,
                    resolution=resolution,
                    size_mb=total_size,
                ))

    logger.debug(video_links)
    return video_links


async def clear_url(url):
    """Delete parameters from link and change link to json link."""
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, allow_redirects=True) as response:
                final_url = str(response.url)
        parsed_url = urlparse(final_url)._replace(query='', fragment='')
        final_url = urlunparse(parsed_url)
        logger.debug('Extracted URL: %s', parsed_url)
        url_clear = urljoin(final_url, urlparse(final_url).path)
        logger.debug('Delete parameters from link: %s', url_clear)
        url_json = re.sub(
            r'/$',
            '.json',
            url_clear
        ) if url_clear.endswith('/') else f'{url_clear}.json'
        logger.info('Url: %s', final_url)
        return url_json
    except aiohttp.ClientError as error:
        logger.error('Request failed: %s', error)
        return None


def get_reddit_client():
    global reddit_client
    if reddit_client is not None:
        return reddit_client

    env = Env()
    env.read_env()
    client_id = env.str('REDDIT_CLIENT_ID', None)
    client_secret = env.str('REDDIT_CLIENT_SECRET', None)
    if not client_id or not client_secret:
        logger.warning('Reddit API credentials are not configured')
        return None

    reddit_client = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=env.str('REDDIT_USER_AGENT', REDDIT_USER_AGENT),
    )
    reddit_client.read_only = True
    return reddit_client


def submission_to_listing(submission) -> list:
    post = {
        'title': getattr(submission, 'title', None),
        'selftext': getattr(submission, 'selftext', None),
        'subreddit': str(getattr(submission, 'subreddit', '') or ''),
        'link_flair_text': getattr(submission, 'link_flair_text', None),
        'permalink': normalize_permalink(getattr(submission, 'permalink', None)),
        'removed_by_category': getattr(submission, 'removed_by_category', None),
        'thumbnail': getattr(submission, 'thumbnail', None),
        'post_hint': getattr(submission, 'post_hint', None),
        'media': getattr(submission, 'media', None),
        'secure_media': getattr(submission, 'secure_media', None),
        'preview': getattr(submission, 'preview', None),
        'url': getattr(submission, 'url', None),
        'url_overridden_by_dest': (
            getattr(submission, 'url_overridden_by_dest', None)
            or getattr(submission, 'url', '')
        ),
        'is_gallery': getattr(submission, 'is_gallery', False),
        'gallery_data': getattr(submission, 'gallery_data', {}),
        'media_metadata': getattr(submission, 'media_metadata', {}),
        'over_18': getattr(submission, 'over_18', False),
    }
    crosspost_parent_list = getattr(submission, 'crosspost_parent_list', None)
    if crosspost_parent_list:
        post['crosspost_parent_list'] = crosspost_parent_list
    return [{'data': {'children': [{'data': post}]}}]


def fetch_reddit_listing_with_praw(url: str) -> list | None:
    reddit = get_reddit_client()
    if reddit is None:
        return None

    submission = reddit.submission(url=url)
    submission._fetch()
    return submission_to_listing(submission)


async def get_reddit_listing(url: str) -> list | None:
    try:
        reddit_listing = await asyncio.to_thread(fetch_reddit_listing_with_praw, url)
        if reddit_listing:
            return reddit_listing
    except (
            prawcore.exceptions.PrawcoreException,
            praw.exceptions.PRAWException,
    ) as error:
        logger.error('Error getting post via Reddit API: %s', error)

    links_url = await clear_url(url)
    if not links_url:
        return None
    try:
        return await fetch_json(
            links_url,
            params={'raw_json': 1},
        )
    except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
    ) as error:
        logger.error('Error getting post json: %s', error)
        return None


async def get_links(url: str) -> RedditResult | None:
    """Extract video information from a Reddit URL."""
    direct_redgifs_id = extract_redgifs_id(url)
    if direct_redgifs_id:
        logger.info('Detected direct RedGifs URL id=%s url=%s', direct_redgifs_id, url)
        return RedgifsResult(
            meta=RedditPostMeta(
                title='RedGifs',
                permalink=url,
            ),
            url_id=direct_redgifs_id,
        )

    def as_dict(value):
        return value if isinstance(value, dict) else {}

    def get_find_json(res_json):
        find_json = res_json[0]['data'].get('children', [{}])[0]['data']

        if 'crosspost_parent_list' in find_json and find_json['crosspost_parent_list']:
            find_json = find_json.get('crosspost_parent_list', [{}])[0]
        return find_json

    def get_caption(res_json):
        return res_json[0]['data'].get('children', [{}])[0]['data'].get(
            'title')

    def get_post_meta(res_json):
        post = res_json[0]['data'].get('children', [{}])[0]['data']
        find_json = get_find_json(res_json)
        permalink = find_json.get('permalink') or post.get('permalink')
        return RedditPostMeta(
            title=post.get('title'),
            description=find_json.get('selftext') or post.get('selftext'),
            subreddit=str(find_json.get('subreddit') or post.get('subreddit') or ''),
            flair=find_json.get('link_flair_text') or post.get('link_flair_text'),
            permalink=normalize_permalink(permalink),
        )

    def is_deleted(res_json):
        return get_find_json(res_json).get('removed_by_category') == 'deleted'

    def is_image(res_json):
        file = res_json[0]['data'].get('children', [{}])[0]['data'].get(
            'post_hint', [{}])
        return file == 'image'

    def is_nsfw(res_json):
        if get_find_json(res_json).get('over_18'):
            return True
        thumbnail = get_find_json(res_json).get('thumbnail') or ''
        return 'nsfw' in thumbnail

    def is_redgifs(res_json):
        media = as_dict(get_find_json(res_json).get('media'))
        return 'redgifs.com' in (media.get('type') or '')

    def is_gallery(res_json):
        try:
            return get_find_json(res_json).get('is_gallery')
        except AttributeError:
            return False

    async def add_fallback_video_link(fallback_url, parsed_video: ParsedVideoLinks):
        if not fallback_url:
            logger.error('Reddit video has no fallback_url')
            return parsed_video

        max_resol = get_video_resolution(fallback_url)
        if max_resol is None:
            logger.warning('Skipping fallback video with unknown resolution: %s', fallback_url)
            return parsed_video

        max_resol_link = urljoin(fallback_url, urlparse(fallback_url).path)
        video_size = await size_file(max_resol_link)
        audio_size = 0.0
        audio_link = parsed_video.audio_url
        if audio_link and audio_link != 'false':
            audio_size = await size_file(audio_link)
        total_size = video_size + audio_size
        logger.debug('Video size: %s MB, total with audio: %s MB', video_size, total_size)
        if total_size < MAX_FILE_SIZE_MB:
            parsed_video.variants.append(RedditVideoVariant(
                label=f'{max_resol}p {total_size:.1f}mb',
                url=max_resol_link,
                resolution=max_resol,
                size_mb=total_size,
            ))
        logger.debug(parsed_video)
        return parsed_video

    try:
        res_json = await get_reddit_listing(url)
        if not res_json:
            return None
        if is_deleted(res_json):
            return DeletedResult()

        find_json = get_find_json(res_json)
        meta = get_post_meta(res_json)

        if is_redgifs(res_json):
            redgifs_id = extract_redgifs_id(
                find_json.get('url'),
                find_json.get('url_overridden_by_dest'),
                find_json.get('media'),
                find_json.get('secure_media'),
                find_json.get('media_embed'),
                find_json.get('secure_media_embed'),
            )
            if not redgifs_id:
                logger.warning('RedGifs post detected but id was not found: source=%s', meta.permalink)
                return None
            logger.info('Detected Reddit RedGifs post id=%s source=%s', redgifs_id, meta.permalink)
            return RedgifsResult(meta=meta, url_id=redgifs_id)

        preview = as_dict(find_json.get('preview'))
        if preview.get('reddit_video_preview'):
            logger.info(
                'Using Reddit video preview source=%s has_audio=%s fallback_url=%s',
                meta.permalink,
                as_dict(preview.get('reddit_video_preview')).get('has_audio'),
                as_dict(preview.get('reddit_video_preview')).get('fallback_url'),
            )
            find_json = preview.get('reddit_video_preview') or {}

            dash_url = find_json.get('dash_url')
            if dash_url:
                dash = await fetch_text(dash_url)
                url_dl = dash_url.split('DASHPlaylist.mpd')[0]
                video_links = await parse_xml(dash, url_dl)
            else:
                video_links = ParsedVideoLinks()

            fallback_url = find_json.get('fallback_url')
            video_links = await add_fallback_video_link(fallback_url, video_links)
            return RedditVideoResult(
                meta=meta,
                audio_url=video_links.audio_url,
                variants=video_links.variants,
                nsfw=is_nsfw(res_json),
            )

        secure_media = as_dict(get_find_json(res_json).get('secure_media'))
        if secure_media.get('reddit_video'):
            find_json = secure_media.get('reddit_video') or {}

            dash_url = find_json.get('dash_url')
            if dash_url:
                dash = await fetch_text(dash_url)
                url_dl = get_find_json(res_json).get(
                    'url_overridden_by_dest', '') + '/'
                video_links = await parse_xml(dash, url_dl)
            else:
                video_links = ParsedVideoLinks()

            fallback_url = find_json.get('fallback_url')
            video_links = await add_fallback_video_link(fallback_url, video_links)
            return RedditVideoResult(
                meta=meta,
                audio_url=video_links.audio_url,
                variants=video_links.variants,
                nsfw=is_nsfw(res_json),
            )

        if is_image(res_json):
            image_url = res_json[0]['data'].get('children', [{}])[0][
                'data'].get('url', '')
            return RedditImageResult(meta=meta, url=image_url)

        if is_gallery(res_json):
            gallery_data = get_find_json(res_json).get('gallery_data', {})
            media_metadata = get_find_json(res_json).get('media_metadata', {})
            gallery_items = []
            for i, item in enumerate(gallery_data.get('items', [])):
                media_id = item.get('media_id')
                media_meta = media_metadata.get(media_id, {})
                media = media_meta.get('s', {})
                redgifs_id = extract_redgifs_id(item, media_meta)
                url = html.unescape(media.get('mp4') or media.get('u') or media.get('gif', ''))
                if redgifs_id:
                    gallery_items.append(RedditGalleryItem(
                        kind='redgifs',
                        url=url,
                        media_id=media_id,
                        mime=media_meta.get('m', ''),
                        redgifs_id=redgifs_id,
                    ))
                    continue
                if not url:
                    logger.warning('Skipping gallery item %s due to missing media fields', media_id)
                    continue
                mime = media_meta.get('m', '')
                if media.get('mp4'):
                    logger.info(
                        'Gallery item %s is reddit mp4 preview, mime=%s url=%s',
                        media_id,
                        mime,
                        url,
                    )
                    gallery_items.append(RedditGalleryItem(
                        kind='video',
                        url=url,
                        media_id=media_id,
                        mime=mime,
                    ))
                elif 'gif' in mime:
                    gallery_items.append(RedditGalleryItem(
                        kind='document',
                        url=url,
                        media_id=media_id,
                        mime=mime,
                    ))
                else:
                    gallery_items.append(RedditGalleryItem(
                        kind='photo',
                        url=url,
                        media_id=media_id,
                        mime=mime,
                    ))
            return RedditGalleryResult(
                meta=get_post_meta(res_json),
                media=gallery_items,
            )
        return None
    except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            json.JSONDecodeError,
    ) as error:
        logger.error('Error: %s', error)
        return None
