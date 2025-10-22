FROM nvcr.io/nvidia/cuda:11.8-devel-ubuntu20.04

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Создание символической ссылки для python
RUN ln -s /usr/bin/python3 /usr/bin/python

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