import requests
from aiogram import Dispatcher, types
from aiogram.types import (
    ReplyKeyboardMarkup, ReplyKeyboardRemove,
    KeyboardButton
)
from bs4 import BeautifulSoup
from pyhelpers.ops import is_downloadable

REDDIT_SAVE_SD_URL = 'https://rapidsave.com/sd.php?id='
REDDIT_SAVE_HD_URL = 'https://rapidsave.com/info?url='

users: dict = {}


def parse_sd_url(url):
    id_url = REDDIT_SAVE_SD_URL + url.split('/comments/')[1].split('/')[0]
    return BeautifulSoup(requests.get(id_url).text, 'html.parser')


def parse_hd_url(url):
    html = BeautifulSoup(
        requests.get(REDDIT_SAVE_HD_URL + url).text, 'html.parser')
    return html.find_all('a', class_='downloadbutton')[0].get('href')


def get_video_url(url):
    html = parse_sd_url(url)
    results = {}
    if html.find_all(
            'div', class_='col-md-8 col-md-offset-2 alert alert-danger'):
        return 'No video found'
    else:
        for row in html.find_all('tr'):
            aux = row.find_all('td')
            if len(aux) == 2:
                results[aux[0].string] = aux[1].find('a').get('href')
        results['Max (mp4)'] = parse_hd_url(url)
        return results


async def bot_get_links(message: types.Message):
    links = get_video_url(message.text)
    if links == 'No video found':
        await message.answer('Видео не найдено')
    else:
        users[message.from_user.id] = {'links': links, }
        keyboard: ReplyKeyboardMarkup = ReplyKeyboardMarkup(
            resize_keyboard=True, one_time_keyboard=True)
        keyboard.add(*[KeyboardButton(i) for i in links.keys()])
        await message.answer(
            'В каком качестве хочешь получить видос?', reply_markup=keyboard)
    print(users)


async def bot_send_video(message: types.Message):
    keyboard = ReplyKeyboardRemove()
    if is_downloadable(users[message.from_user.id]['links'][message.text]):
        response = requests.get(users[message.from_user.id]['links'][message.text])
        await message.answer(
            'Я качаю видео, пожалуйста подожди...', reply_markup=keyboard)
        await message.answer_video(
            response.content)


async def bot_send_video_group(message: types.Message):
    keyboard = ReplyKeyboardRemove()
    links = get_video_url(message.text)
    if links == 'No video found':
        await message.answer('Видео не найдено')
    else:
        users[message.from_user.id] = {'links': links, }
    if is_downloadable(list(users[message.from_user.id]['links'].values())[-2]):
        response = requests.get(
            list(users[message.from_user.id]['links'].values())[-2]
        )
        msg = await message.answer(
            'Я качаю видео, пожалуйста подожди...', reply_markup=keyboard)
        await message.answer_video(
            response.content)
        await msg.delete()


def register_get_links(dp: Dispatcher):
    dp.register_message_handler(
        bot_get_links, text_startswith=['https://www.reddit.com/r/'], chat_type=types.ChatType.PRIVATE)
    dp.register_message_handler(
        bot_send_video, text_endswith=['(mp4)'], chat_type=types.ChatType.PRIVATE)
    dp.register_message_handler(
        bot_send_video_group, text_startswith=['https://www.reddit.com/r/'])
