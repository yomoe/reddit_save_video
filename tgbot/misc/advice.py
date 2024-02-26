import logging

import aiohttp
logger = logging.getLogger(__name__)

async def get_advice():
    async with aiohttp.ClientSession() as session:
        async with session.get('https://fucking-great-advice.ru/api/random') as response:
            if response.status == 200:
                data = await response.json()
                logger.info("Получили совет")
                return data['text']
            else:
                logger.error("Ошибка получения совета")
                return "Пшл нах!"
