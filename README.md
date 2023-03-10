# Reddit_Save_Videos_Bot

## Технологии

<img alt="GitHub Pipenv locked Python version" src="https://img.shields.io/github/pipenv/locked/python-version/yomoe/reddit_save_video?logo=python&logoColor=white&style=flat-square"> <img alt="GitHub Pipenv locked dependency version" src="https://img.shields.io/github/pipenv/locked/dependency-version/yomoe/reddit_save_video/aiogram?style=flat-square"> <img alt="GitHub Pipenv locked dependency version" src="https://img.shields.io/github/pipenv/locked/dependency-version/yomoe/reddit_save_video/beautifulsoup4?label=beautiful%20soup%204&style=flat-square"> <img alt="GitHub Pipenv locked dependency version" src="https://img.shields.io/github/pipenv/locked/dependency-version/yomoe/reddit_save_video/aiohttp?style=flat-square"> <img alt="GitHub Pipenv locked dependency version" src="https://img.shields.io/github/pipenv/locked/dependency-version/yomoe/reddit_save_video/ffmpeg-python?style=flat-square">

## О боте

Бот для телеграма, который по ссылке из Reddit.com пересылает видео или гифку
в телеграм канал или личку.

Структура бота взята у `https://github.com/Latand/tgbot_template`

`.env.exemple` - пример файла с переменными окружения

Вся логика бота лежит в `tgbot/handlers/reddit_video.py`

---

## Принцип работы бота

При отправке личного сообщения боту в виде ссылки на видео из Reddit.com,
пример ссылки:

```
https://www.reddit.com/r/oddlysatisfying/comments/1125p83/lighter_filmed_1000_times_slower_than_real_life/
```

Бот преобразует ссылку в json формат:

```
https://www.reddit.com/r/oddlysatisfying/comments/1125p83/lighter_filmed_1000_times_slower_than_real_life.json
```

Из json делает проверку пост содержит видео, гиф или нет, если содержит, то
получает ссылку на видео в лучшем качестве из все того же json, так же получает
xml файл со ссылками на видео в разных качествах, и ссылку на звуковой файл.

Если общение идет в приватном чате, то бот спрашивает у пользователя какое
качество видео он хочет получить, после выбора качества видео, скачивает по
отдельности видео и аудио файлы и объединяет их через `ffmpeg`, после чего
отправляет видео в личку.

Если бот находится в группе, выбирает лучшее качество из SD варианта (для
уменьшения размера файла). Скачивает по отдельности видео и аудио файлы и
объединяет их через `ffmpeg`, после чего отправляет видео в группу.

Бот убирает из списка видео самого низкого качества ***220р*** и ***240р***,
мне показались они не актуальными.

---

### Планы

* Перенести бота на `aiogram 3`, так как он имеет интересные фишки, которые
  помогут реализовать некоторые мои хотелки. Например, скрытие постов под
  спойлер
  через `has_spoiler`, это позволит контролировать `nsfw` контент.

* Сделать сохранение пользователей в базу данных, чтобы бот запоминал кто какие
  качества видео выбирал, и при следующем запросе отправлял ему видео в том же
  качестве.

* Добавить языковые настройки, чтобы пользователь мог выбрать язык бота.

---

### Как запустить проект:

Клонировать репозиторий и перейти в него в командной строке:

```
git clone https://github.com/yomoe/reddit_save_video.git
```

Создать и активировать виртуальное окружение:

```
python3 -m venv env
```

```
source env/bin/activate
```

```
python3 -m pip install --upgrade pip
```

Установить зависимости из файла requirements.txt:

```
pip install -r requirements.txt
```

Создать файл .env и заполнить его переменными окружения:

Запустить проект:

```
python3 bot.py
```
