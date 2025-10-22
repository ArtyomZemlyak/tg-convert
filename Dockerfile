FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Создание рабочей директории
WORKDIR /app

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY telegram_bot.py .

# Создание директории для временных файлов
RUN mkdir -p /tmp/telegram_video_converter

# Запуск бота
CMD ["python", "telegram_bot.py"]