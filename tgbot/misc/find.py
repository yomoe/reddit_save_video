import random

import asyncpraw
from environs import Env
from fake_useragent import UserAgent

env = Env()
env.read_env()

# Укажите свои ключи, полученные при регистрации приложения на Reddit
REDDIT_CLIENT_ID: str = env.str('REDDIT_CLIENT_ID')
REDDIT_CLIENT_SECRET: str = env.str('REDDIT_CLIENT_SECRET')

ua = UserAgent()

# Инициализируем клиент Async PRAW
reddit = asyncpraw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent=ua.chrome
)


# Асинхронная функция для получения случайного поста с изображением из указанного сабреддита
async def get_random_image_post(subreddit_name='FindTheSniper'):
    subreddit = await reddit.subreddit(subreddit_name)
    # Получаем список горячих постов
    hot_posts = [post async for post in subreddit.hot(limit=100)]
    # Фильтруем посты, оставляя только те, которые содержат изображения
    image_posts = [post for post in hot_posts if post.url.endswith(('jpg', 'jpeg', 'png'))]

    if not image_posts:
        return None

    # Возвращаем случайный пост из отфильтрованного списка
    random_post = random.choice(image_posts)
    return {
        "title": random_post.title,
        "img_url": random_post.url,
        "post_url": random_post.shortlink
    }
