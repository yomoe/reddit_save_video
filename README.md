# Reddit_Save_Videos_Bot

<img height="30em" src="https://raw.githubusercontent.com/anki-geo/ultimate-geography/a44a569a922e1d241517113e2917736af808eed7/src/media/flags/ug-flag-russia.svg" alt="russian">

Бот для телеграма, который по ссылке из Reddit.com пересылает видео в телеграм
канал или личку.

## О боте

Структура бота взята у `https://github.com/Latand/tgbot_template`

Видео скачиваются через `https://rapidsave.com/`

`.env.exemple` - пример файла с переменными окружения

Вся логика бота лежит в `tgbot/handlers/reddit_video.py`

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

Из json делает проверку пост содержит видео или нет, если содержит,
то получает ссылку на видео в лучшем качестве из все того же json, так же
получает
xml файл со ссылками на видео в разных качествах, и ссылку на звуковой файл.

При получении ссылок происходит формирование ссылки на закачку файла
с https://rapidsave.com/
получается ссылка вида:

```
https://sd.redditsave.com/download.php?permalink={permalink}&video_url={video_url}&audio_url={audio_url}
```

Или такого, в зависимости от качества видео:

```
https://sd.redditsave.com/download-sd.php?permalink={permalink}&video_url={video_url}&audio_url={audio_url}
```

Если общение идет в приватном чате, то бот спрашивает у пользователя какое
качество видео он хочет получить, после выбора качества видео, бот отправляет
видео в личку.

Если бот находится в группе, то бот отправляет видео в группу, выбирая лучшее
качество из SD варианта (для уменьшения размера файла).

Бот убирает из списка видео самого низкого качества ***220р*** и ***240р***,
мне показались они не актуальными

### ЗЫ

Я знаю что еще есть что доработать, но пока что он работает и будет
допиливаться по мере необходимости.

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