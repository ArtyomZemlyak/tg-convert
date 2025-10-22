FROM python:3.11-slim

# Установка системных зависимостей для Docker-in-Docker
RUN apt-get update && apt-get install -y \
    docker.io \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

# AICODE-NOTE: Настройка Docker-in-Docker для запуска контейнеров jrottenberg/ffmpeg

# Создание рабочей директории
WORKDIR /app

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка Python зависимостей
RUN pip3 install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY telegram_bot.py .

# Создание директории для временных файлов
RUN mkdir -p /tmp/telegram_video_converter

# Запуск бота
CMD ["python3", "telegram_bot.py"]