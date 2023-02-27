FROM python:3.9-buster
ENV BOT_NAME=$BOT_NAME
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN apt -y update && \
    apt install -y ffmpeg
WORKDIR /usr/src/app/"${BOT_NAME:-tg_bot}"
COPY requirements.txt /usr/src/app/"${BOT_NAME:-tg_bot}"
RUN pip install -r /usr/src/app/"${BOT_NAME:-tg_bot}"/requirements.txt
COPY . /usr/src/app/"${BOT_NAME:-tg_bot}"