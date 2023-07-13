FROM python:3.11.4-slim-buster

RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y ffmpeg libpq-dev gcc python3-dev && \
    apt-get clean && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app/redditbot/
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .