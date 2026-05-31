import asyncio
import html
import json
import logging
import os
import re
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp
import praw
import prawcore
import requests
from aiogram.types import InputMediaDocument, InputMediaPhoto
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
API_URL_REDGIFS = 'https://api.redgifs.com/v1/gifs/'
MAX_FILE_SIZE_MB = 50
reddit_client = None


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
    except aiohttp.ClientError as error:
        logger.error('Request to %s failed: %s', url, error)
        return 0.0


async def parse_xml(xml: str, url: str) -> dict:
    """Find video and audio links from Reddit DASH XML."""
    video_links = {'audio': 'false'}
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
            video_links['audio'] = audio
            audio_size = await size_file(audio)

    for adaptation_set in soup.find_all('AdaptationSet', {'contentType': 'video'}):
        videos = [x.text for x in adaptation_set.find_all('BaseURL')]
        for video in videos:
            resolution = video.split('_')[1].split('.')[0]
            link = url + video
            video_size = await size_file(link)
            total_size = video_size + audio_size
            logger.debug('Video size: %s MB, total with audio: %s MB', video_size, total_size)
            if total_size < MAX_FILE_SIZE_MB:
                video_links[f'{resolution}p {total_size:.1f}mb'] = link

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
            requests.exceptions.RequestException,
    ) as error:
        logger.error('Error getting post via Reddit API: %s', error)

    links_url = await clear_url(url)
    if not links_url:
        return None
    try:
        res = requests.get(
            links_url,
            headers=HEADERS,
            params={'raw_json': 1},
            timeout=10,
        )
        res.raise_for_status()
        logger.debug('Response status code: %s', res.status_code)
        return res.json()
    except (
            json.JSONDecodeError,
            requests.exceptions.RequestException
    ) as error:
        logger.error('Error getting post json: %s', error)
        return None


async def get_links(url: str) -> dict:
    """Extract video information from a Reddit URL."""
    def get_find_json(res_json):
        find_json = res_json[0]['data'].get('children', [{}])[0]['data']

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
        if not fallback_url:
            logger.error('Reddit video has no fallback_url')
            return dict_video

        max_resol = fallback_url.split('_')[1].split('.')[0]
        max_resol_link = urljoin(fallback_url, urlparse(fallback_url).path)
        video_size = await size_file(max_resol_link)
        audio_size = 0.0
        audio_link = dict_video.get('audio')
        if audio_link and audio_link != 'false':
            audio_size = await size_file(audio_link)
        total_size = video_size + audio_size
        logger.debug('Video size: %s MB, total with audio: %s MB', video_size, total_size)
        if total_size < MAX_FILE_SIZE_MB:
            dict_video[f'{max_resol}p {total_size:.1f}mb'] = max_resol_link
        logger.debug(dict_video)
        return dict_video

    try:
        res_json = await get_reddit_listing(url)
        if not res_json:
            return {}
        video_link = {}
        if is_deleted(res_json):
            return {'error': 'Deleted'}

        find_json = get_find_json(res_json)

        if find_json.get('preview', {}).get('reddit_video_preview'):
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

        if get_find_json(res_json).get('secure_media', {}).get('reddit_video'):
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
                'data'].get('url', '')
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
            photos = []
            for i, item in enumerate(gallery_data.get('items', [])):
                meta = media_metadata.get(item['media_id'], {})
                media = meta.get('s', {})
                url = html.unescape(media.get('u') or media.get('gif') or media.get('mp4', ''))
                if not url:
                    logger.warning('Skipping gallery item %s due to missing media fields', item.get('media_id'))
                    continue
                caption = get_caption(res_json) if i == 0 else None
                mime = meta.get('m', '')
                if 'gif' in mime:
                    photos.append(InputMediaDocument(url, caption=caption))
                else:
                    photos.append(InputMediaPhoto(url, caption=caption))
            return {'gallery': photos}
        return {}
    except (
            json.JSONDecodeError,
            requests.exceptions.RequestException
    ) as error:
        logger.error('Error: %s', error)
        return {}
