#!/usr/bin/env python3
"""
Telegram Bot for Video Conversion
Конвертирует видео файлы с помощью Docker контейнера jrottenberg/ffmpeg
"""

import os
import tempfile
import subprocess
import asyncio
import aiohttp
from pathlib import Path
from typing import Optional
from loguru import logger

from telebot.async_telebot import AsyncTeleBot
from telebot import types

# AICODE-NOTE: Настройка loguru логирования с красивым форматированием и ротацией
logger.remove()  # Удаляем стандартный обработчик
logger.add(
    "logs/bot.log",
    rotation="10 MB",
    retention="7 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level="INFO"
)
logger.add(
    "logs/error.log",
    rotation="10 MB",
    retention="30 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level="ERROR"
)
logger.add(
    lambda msg: print(msg, end=""),
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO"
)

# Создаем папку для логов
Path("logs").mkdir(exist_ok=True)

# Конфигурация
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

# AICODE-NOTE: Настройка таймаута конвертации через переменную окружения
CONVERSION_TIMEOUT = int(os.getenv('CONVERSION_TIMEOUT', '300'))  # По умолчанию 5 минут

# AICODE-NOTE: Ограничения Telegram Bot API
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 МБ - максимальный размер файла для загрузки через Bot API
MAX_SEND_SIZE = 50 * 1024 * 1024  # 50 МБ - максимальный размер файла для отправки через Bot API

# AICODE-NOTE: Константы для отображения размеров файлов
MB = 1024 * 1024

TMP_DIR = Path("/tmp/telegram_video_converter")
TMP_DIR.mkdir(exist_ok=True)

class VideoConverterBot:
    def __init__(self):
        self.bot = AsyncTeleBot(BOT_TOKEN)
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Настройка обработчиков команд и сообщений"""
        # Команды
        @self.bot.message_handler(commands=['start'])
        async def start_command(message):
            await self.start_command(message)
        
        @self.bot.message_handler(commands=['help'])
        async def help_command(message):
            await self.help_command(message)
        
        # Обработка нажатий на кнопки
        @self.bot.callback_query_handler(func=lambda call: True)
        async def button_callback(call):
            await self.button_callback(call)
        
        # Обработка файлов
        @self.bot.message_handler(content_types=['document'])
        async def handle_document(message):
            await self.handle_document(message)
    
    async def start_command(self, message):
        """Обработчик команды /start"""
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("🎬 Конвертировать видео", callback_data="convert_video"))
        
        welcome_text = (
            "🎥 Добро пожаловать в бот для конвертации видео!\n\n"
            "Выберите действие из меню ниже:"
        )
        
        await self.bot.reply_to(message, welcome_text, reply_markup=keyboard)
    
    async def help_command(self, message):
        """Обработчик команды /help"""
        max_size_mb = MAX_FILE_SIZE / MB
        help_text = (
            "🤖 Возможности бота:\n\n"
            "• /start - Открыть главное меню\n"
            "• /help - Показать эту справку\n"
            "• 🎬 Конвертировать видео - Загрузить и конвертировать видео файл\n\n"
            "📋 Поддерживаемые форматы:\n"
            "• Входные: MP4, AVI, MOV, MKV и другие\n"
            "• Выходные: MP4 (H.264, 1920x1080, 10fps)\n\n"
            "⚙️ Параметры конвертации:\n"
            "• Разрешение: 1920x1080\n"
            "• Частота кадров: 10 FPS\n"
            "• Кодек видео: H.264 (NVENC)\n"
            "• Кодек аудио: AAC, 64kbps, моно\n"
            f"• Таймаут: {CONVERSION_TIMEOUT} секунд\n\n"
            f"📏 Ограничения размера файлов:\n"
            f"• Максимальный размер для загрузки: {max_size_mb:.0f} МБ\n"
            f"• Максимальный размер для отправки: {max_size_mb:.0f} МБ\n\n"
            "💡 Просто отправьте видео файл после нажатия кнопки конвертации!\n"
            "⚠️ Если файл больше {max_size_mb:.0f} МБ, сожмите его перед загрузкой."
        )
        
        await self.bot.reply_to(message, help_text)
    
    async def button_callback(self, call):
        """Обработчик нажатий на кнопки"""
        await self.bot.answer_callback_query(call.id)
        
        if call.data == "convert_video":
            max_size_mb = MAX_FILE_SIZE / MB
            await self.bot.edit_message_text(
                f"📁 Пожалуйста, отправьте видео файл для конвертации.\n\n"
                f"📋 Поддерживаемые форматы: MP4, AVI, MOV, MKV и другие\n"
                f"📏 Максимальный размер файла: {max_size_mb:.0f} МБ\n\n"
                f"⚠️ Если ваш файл больше {max_size_mb:.0f} МБ, пожалуйста, сожмите его перед загрузкой.",
                call.message.chat.id,
                call.message.message_id
            )
    
    async def handle_document(self, message):
        """Обработчик загруженных файлов"""
        document = message.document
        
        # Проверяем, что это видео файл
        if not self._is_video_file(document.file_name):
            await self.bot.reply_to(
                message,
                "❌ Пожалуйста, отправьте видео файл (MP4, AVI, MOV, MKV и т.д.)"
            )
            return
        
        # AICODE-NOTE: Проверяем размер файла перед загрузкой
        if document.file_size and document.file_size > MAX_FILE_SIZE:
            file_size_mb = document.file_size / MB
            max_size_mb = MAX_FILE_SIZE / MB
            await self.bot.reply_to(
                message,
                f"❌ Файл слишком большой для обработки!\n\n"
                f"📊 Размер вашего файла: {file_size_mb:.1f} МБ\n"
                f"📏 Максимальный размер: {max_size_mb:.0f} МБ\n\n"
                f"💡 Telegram Bot API ограничивает размер загружаемых файлов до {max_size_mb:.0f} МБ.\n"
                f"Пожалуйста, сожмите видео или разделите его на части."
            )
            return
        
        # Отправляем сообщение о начале обработки
        processing_msg = await self.bot.reply_to(message, "⏳ Обрабатываю видео...")
        
        try:
            # Создаем временную папку для этого запроса
            user_tmp_dir = TMP_DIR / f"user_{message.from_user.id}_{message.message_id}"
            user_tmp_dir.mkdir(exist_ok=True)
            
            # Скачиваем файл
            file_path = await self._download_file(document, user_tmp_dir)
            
            # Конвертируем видео
            output_path = await self._convert_video(file_path, user_tmp_dir)
            
            # Проверяем размер сконвертированного файла
            if output_path.stat().st_size > MAX_SEND_SIZE:
                output_size_mb = output_path.stat().st_size / MB
                max_send_mb = MAX_SEND_SIZE / MB
                await self.bot.edit_message_text(
                    f"⚠️ Видео успешно сконвертировано, но слишком большое для отправки!\n\n"
                    f"📊 Размер сконвертированного файла: {output_size_mb:.1f} МБ\n"
                    f"📏 Максимальный размер для отправки: {max_send_mb:.0f} МБ\n\n"
                    f"💡 Попробуйте использовать более агрессивные настройки сжатия или разделите видео на части.",
                    message.chat.id,
                    processing_msg.message_id
                )
                return
            
            # Отправляем результат
            await self._send_converted_video(message, output_path, processing_msg)
            
        except Exception as e:
            logger.error(f"Error processing video: {e}", exc_info=True)
            
            # AICODE-NOTE: Специальная обработка ошибок Telegram API
            error_message = str(e)
            if "file is too big" in error_message.lower():
                error_text = (
                    "❌ Файл слишком большой для обработки!\n\n"
                    f"📊 Размер файла превышает лимит Telegram Bot API ({MAX_FILE_SIZE / (1024*1024):.0f} МБ).\n\n"
                    "💡 Рекомендации:\n"
                    "• Сожмите видео перед загрузкой\n"
                    "• Разделите длинное видео на части\n"
                    "• Используйте более низкое качество\n"
                    "• Попробуйте другой формат файла"
                )
            elif "bad request" in error_message.lower():
                error_text = (
                    "❌ Некорректный запрос к Telegram API!\n\n"
                    "Возможные причины:\n"
                    "• Файл поврежден или имеет неподдерживаемый формат\n"
                    "• Проблемы с сетью\n"
                    "• Временные проблемы с серверами Telegram\n\n"
                    "💡 Попробуйте загрузить файл еще раз через несколько минут."
                )
            else:
                error_text = f"❌ Произошла ошибка при обработке видео: {error_message}"
            
            await self.bot.edit_message_text(
                error_text,
                message.chat.id,
                processing_msg.message_id
            )
        finally:
            # Очищаем временные файлы
            await self._cleanup_temp_files(user_tmp_dir)
    
    def _is_video_file(self, filename: str) -> bool:
        """Проверяет, является ли файл видео"""
        if not filename:
            return False
        
        video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v'}
        return Path(filename).suffix.lower() in video_extensions
    
    async def _download_file(self, document, tmp_dir: Path) -> Path:
        """Скачивает файл во временную папку с отслеживанием прогресса"""
        file_info = await self.bot.get_file(document.file_id)
        file_path = tmp_dir / document.file_name
        
        # AICODE-NOTE: Получаем размер файла для отслеживания прогресса
        file_size = document.file_size or 0
        downloaded_size = 0
        
        logger.info(f"Starting download: {document.file_name} ({file_size / MB:.1f} MB)")
        
        try:
            # Скачиваем файл асинхронно с отслеживанием прогресса
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.telegram.org/file/bot{self.bot.token}/{file_info.file_path}") as response:
                    if response.status != 200:
                        raise Exception(f"Failed to download file: HTTP {response.status}")
                    
                    with open(file_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            f.write(chunk)
                            downloaded_size += len(chunk)
                            
                            # AICODE-NOTE: Логируем прогресс каждые 10 МБ
                            if downloaded_size % (10 * MB) == 0 or downloaded_size == file_size:
                                progress_percent = (downloaded_size / file_size * 100) if file_size > 0 else 0
                                logger.info(f"Download progress: {downloaded_size / MB:.1f} MB / {file_size / MB:.1f} MB ({progress_percent:.1f}%)")
        
        except aiohttp.ClientError as e:
            raise Exception(f"Network error during download: {str(e)}")
        except Exception as e:
            raise Exception(f"Download failed: {str(e)}")
        
        # Проверяем, что файл был скачан полностью
        if file_size > 0 and file_path.stat().st_size != file_size:
            raise Exception(f"Download incomplete: expected {file_size} bytes, got {file_path.stat().st_size} bytes")
        
        logger.info(f"Successfully downloaded file: {file_path}")
        return file_path
    
    async def _convert_video(self, input_path: Path, tmp_dir: Path) -> Path:
        """Конвертирует видео с помощью Docker контейнера jrottenberg/ffmpeg с поддержкой NVIDIA"""
        output_filename = f"converted_{input_path.stem}.mp4"
        output_path = tmp_dir / output_filename
        
        # Docker команда для конвертации с использованием jrottenberg/ffmpeg
        docker_cmd = [
            "docker", "run", "--rm",
            "--gpus", "all",
            "-v", f"{tmp_dir.absolute()}:/workdir",
            "-w", "/workdir",
            "jrottenberg/ffmpeg:5.1.4-nvidia2004",
            "-threads", "0",
            "-i", input_path.name,
            "-vf", "fps=10,format=yuv420p",
            "-c:v", "h264_nvenc",
            "-preset", "p7",
            "-cq", "26",
            "-s", "1920x1080",
            "-c:a", "aac",
            "-b:a", "64k",
            "-ac", "1",
            "-y", output_filename
        ]
        
        logger.info(f"Running Docker command: {' '.join(docker_cmd)}")
        
        try:
            # Запускаем Docker контейнер асинхронно
            process = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=CONVERSION_TIMEOUT
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise Exception(f"Конвертация видео заняла слишком много времени (лимит: {CONVERSION_TIMEOUT} секунд)")
            
            if process.returncode != 0:
                raise Exception(f"Docker command failed: {stderr.decode()}")
            
            if not output_path.exists():
                raise Exception("Output file was not created")
            
            logger.info(f"Video converted successfully: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Docker conversion error: {e}", exc_info=True)
            raise
    
    async def _send_converted_video(self, message, video_path: Path, processing_msg):
        """Отправляет сконвертированное видео"""
        try:
            # Удаляем сообщение о обработке
            await self.bot.delete_message(processing_msg.chat.id, processing_msg.message_id)
            
            # Отправляем видео
            with open(video_path, 'rb') as video_file:
                await self.bot.send_video(
                    message.chat.id,
                    video=video_file,
                    caption="✅ Видео успешно сконвертировано!\n\n"
                           "📊 Параметры:\n"
                           "• Разрешение: 1920x1080\n"
                           "• Частота кадров: 10 FPS\n"
                           "• Кодек: H.264 (NVENC)\n"
                           "• Аудио: AAC, 64kbps"
                )
            
            logger.info(f"Sent converted video: {video_path}")
            
        except Exception as e:
            logger.error(f"Error sending video: {e}", exc_info=True)
            await self.bot.reply_to(message, f"❌ Ошибка при отправке видео: {str(e)}")
    
    async def _cleanup_temp_files(self, tmp_dir: Path):
        """Очищает временные файлы"""
        try:
            import shutil
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
                logger.info(f"Cleaned up temp directory: {tmp_dir}")
        except Exception as e:
            logger.error(f"Error cleaning up temp files: {e}", exc_info=True)
    
    async def run(self):
        """Запускает бота"""
        logger.info("Starting Telegram Bot...")
        await self.bot.polling(none_stop=True)

async def main():
    """Главная функция"""
    try:
        bot = VideoConverterBot()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())