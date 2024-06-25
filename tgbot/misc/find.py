import random

import aiohttp
import praw
from environs import Env
from fake_useragent import UserAgent

env = Env()
env.read_env()
# Укажите свои ключи, полученные при регистрации приложения на Reddit
REDDIT_CLIENT_ID: str = env.str('REDDIT_CLIENT_ID')
REDDIT_CLIENT_SECRET: str = env.str('REDDIT_CLIENT_SECRET')

ua = UserAgent()
HEADERS = {
    'user-agent': ua.chrome,
}

# Инициализируем клиент PRAW
reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent=ua.chrome
)


# Функция для получения случайного поста с изображением из указанного сабреддита
async def get_random_image_post(subreddit_name='FindTheSniper'):
    subreddit = reddit.subreddit(subreddit_name)
    hot_posts = list(subreddit.hot(limit=100))
    image_posts = [post for post in hot_posts if post.url.endswith(('jpg', 'jpeg', 'png'))]

    if not image_posts:
        return None

    random_post = random.choice(image_posts)
    img_url = random_post.url

    async with aiohttp.ClientSession() as session:
        async with session.get(img_url) as response:
            if response.status == 200:
                img_data = await response.read()
                return {
                    "title": random_post.title,
                    "img_data": img_data,
                    "post_url": random_post.shortlink
                }
            else:
                return None
