FROM python:3.11-buster
ENV BOT_NAME=$BOT_NAME
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN apt-get update &&\
    apt install -y ffmpeg
WORKDIR /app/"${BOT_NAME:-tg_bot}"
COPY requirements.txt /app/"${BOT_NAME:-tg_bot}"
RUN pip install -r /app/"${BOT_NAME:-tg_bot}"/requirements.txt
COPY . /app/"${BOT_NAME:-tg_bot}"