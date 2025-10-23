#!/usr/bin/env python3
"""
Telegram Bot for Video Conversion
Конвертирует видео файлы с помощью Docker контейнера jrottenberg/ffmpeg
Полностью построен на библиотеке Telethon
"""
import sys
import os
import tempfile
import subprocess
import asyncio
import aiohttp
from pathlib import Path
from typing import Optional, Dict, Any
from loguru import logger

from telethon import TelegramClient, events
from telethon.tl.types import Message, DocumentAttributeVideo, DocumentAttributeFilename, Document
from telethon.errors import FilePartTooBigError, FloodWaitError, SessionPasswordNeededError
from telethon.tl.functions.messages import GetBotCallbackAnswerRequest
from telethon.tl.custom import Button

# AICODE-NOTE: Настройка loguru логирования с красивым форматированием и ротацией
logger.remove()  # Удаляем стандартный обработчик
log_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

# Console output
logger.add(
    sys.stdout, format=log_format, level="DEBUG", colorize=True, backtrace=True, diagnose=True
)

# Создаем папку для логов
Path("logs").mkdir(exist_ok=True)

# Конфигурация
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

# AICODE-NOTE: Конфигурация Telethon (обязательна для работы бота)
TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')

if not all([TELEGRAM_API_ID, TELEGRAM_API_HASH]):
    raise ValueError("TELEGRAM_API_ID and TELEGRAM_API_HASH environment variables are required")

# AICODE-NOTE: Настройка таймаута конвертации через переменную окружения
CONVERSION_TIMEOUT = int(os.getenv('CONVERSION_TIMEOUT', '300'))  # По умолчанию 5 минут

# AICODE-NOTE: Ограничения Telegram (через Telethon можно загружать файлы до 2 ГБ)
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 ГБ - максимальный размер файла для загрузки через Telethon
MAX_SEND_SIZE = 2 * 1024 * 1024 * 1024  # 2 ГБ - максимальный размер файла для отправки через Telethon

# AICODE-NOTE: Константы для отображения размеров файлов
MB = 1024 * 1024
GB = 1024 * MB

TMP_DIR = Path("/tmp/telegram_video_converter")
TMP_DIR.mkdir(exist_ok=True)

class VideoConverterBot:
    def __init__(self):
        # AICODE-NOTE: Создаем папку для сессий Telethon
        session_dir = Path("sessions")
        session_dir.mkdir(exist_ok=True)
        
        # AICODE-NOTE: Инициализируем Telethon клиент
        self.client = TelegramClient(
            str(session_dir / 'telegram_bot_session'),
            int(TELEGRAM_API_ID),
            TELEGRAM_API_HASH
        )
        
        
        # AICODE-NOTE: Настройка обработчиков событий
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Настройка обработчиков команд и сообщений"""
        
        # AICODE-NOTE: Обработчик команды /start
        @self.client.on(events.NewMessage(pattern='/start'))
        async def start_handler(event):
            logger.info(f"/start")
            await self.start_command(event)
        
        # AICODE-NOTE: Обработчик команды /help
        @self.client.on(events.NewMessage(pattern='/help'))
        async def help_handler(event):
            logger.info(f"/help")
            await self.help_command(event)
        
        # AICODE-NOTE: Обработчик нажатий на кнопки (callback queries)
        @self.client.on(events.CallbackQuery)
        async def callback_handler(event):
            logger.info(f"/button")
            await self.button_callback(event)
        
        # AICODE-NOTE: Обработчик документов (видео файлов)
        @self.client.on(events.NewMessage(func=lambda e: e.document))
        async def document_handler(event):
            logger.info(f"/document")
            await self.handle_document(event)
        
        # AICODE-NOTE: Обработчик текстовых сообщений
        @self.client.on(events.NewMessage(func=lambda e: e.text and not e.text.startswith('/')))
        async def text_handler(event):
            await self.handle_text(event)
    
    async def start_command(self, event):
        """Обработчик команды /start"""
        # AICODE-NOTE: Создаем клавиатуру с помощью объектов Button из telethon.tl.custom
        keyboard = [
            [Button.inline("🎬 Конвертировать видео", b"convert_video")],
            [Button.inline("🔧 Статус бота", b"bot_status")]
        ]
        
        welcome_text = (
            "🎥 Добро пожаловать в бот для конвертации видео!\n\n"
            "Этот бот полностью работает на библиотеке Telethon и поддерживает файлы до 2 ГБ.\n\n"
            "Выберите действие из меню ниже:"
        )
        
        await event.respond(welcome_text, buttons=keyboard)
    
    async def help_command(self, event):
        """Обработчик команды /help"""
        max_size_gb = MAX_FILE_SIZE / GB
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
            f"• Максимальный размер для загрузки: {max_size_gb:.1f} ГБ\n"
            f"• Максимальный размер для отправки: {max_size_gb:.1f} ГБ\n\n"
            "💡 Просто отправьте видео файл после нажатия кнопки конвертации!\n"
            "🚀 Бот работает на Telethon - быстрая и надежная работа с большими файлами."
        )
        
        await event.respond(help_text)
    
    async def button_callback(self, event):
        """Обработчик нажатий на кнопки"""
        await event.answer()
        
        if event.data == b"convert_video":
            max_size_gb = MAX_FILE_SIZE / GB
            await event.edit(
                f"📁 Пожалуйста, отправьте видео файл для конвертации.\n\n"
                f"📋 Поддерживаемые форматы: MP4, AVI, MOV, MKV и другие\n"
                f"📏 Максимальный размер файла: {max_size_gb:.1f} ГБ\n\n"
                f"🚀 Бот поддерживает файлы до 2 ГБ благодаря Telethon!"
            )
        elif event.data == b"bot_status":
            status_text = (
                "🔧 Статус бота:\n\n"
                "✅ Telethon: Активен\n"
                "✅ Docker: Готов к работе\n"
                "✅ Конвертация: Доступна\n\n"
                f"📊 Поддерживаемые размеры файлов: до {MAX_FILE_SIZE / GB:.1f} ГБ\n"
                f"⏱️ Таймаут конвертации: {CONVERSION_TIMEOUT} секунд\n\n"
                "🚀 Бот готов к работе!"
            )
            await event.edit(status_text)
    
    async def handle_document(self, event):
        """Обработчик загруженных файлов"""
        logger.info(f"Обрабатываю документ...")
        document = event.document
        
        # Проверяем, что это видео файл
        if not self._is_video_file(document):
            await event.respond(
                "❌ Пожалуйста, отправьте видео файл (MP4, AVI, MOV, MKV и т.д.)"
            )
            return
        
        logger.info(f"Обрабатываю видео...")
        
        # AICODE-NOTE: Проверяем размер файла
        file_size = document.size or 0
        if file_size > MAX_FILE_SIZE:
            file_size_gb = file_size / GB
            max_size_gb = MAX_FILE_SIZE / GB
            await event.respond(
                f"❌ Файл слишком большой для обработки!\n\n"
                f"📊 Размер вашего файла: {file_size_gb:.1f} ГБ\n"
                f"📏 Максимальный размер: {max_size_gb:.1f} ГБ\n\n"
                f"💡 Пожалуйста, сожмите видео или разделите его на части."
            )
            return
        
        logger.info(f"Документ доступного размера...")
        # Отправляем сообщение о начале обработки

        processing_msg = await event.respond("⏳ Обрабатываю видео...")
        
        try:
            # Create temporary directory
            user_tmp_dir = TMP_DIR / f"user_{event.sender_id}_{event.id}"
            user_tmp_dir.mkdir(exist_ok=True)
            logger.info(f"Создали tmp dir: {user_tmp_dir}")
            
            # ✅ FIXED: Pass correct parameters
            file_path = await self._download_file_telethon(document, event.chat_id, user_tmp_dir)
            logger.info(f"Файл загружен на сервер")
            
            # Конвертируем видео
            output_path = await self._convert_video(file_path, user_tmp_dir)
            logger.info(f"Файл сконвертирован")
            
            # Проверяем размер сконвертированного файла
            if output_path.stat().st_size > MAX_SEND_SIZE:
                output_size_gb = output_path.stat().st_size / GB
                max_send_gb = MAX_SEND_SIZE / GB
                await processing_msg.edit(
                    f"⚠️ Видео успешно сконвертировано, но слишком большое для отправки!\n\n"
                    f"📊 Размер сконвертированного файла: {output_size_gb:.1f} ГБ\n"
                    f"📏 Максимальный размер для отправки: {max_send_gb:.1f} ГБ\n\n"
                    f"💡 Попробуйте использовать более агрессивные настройки сжатия или разделите видео на части."
                )

                logger.info(f"Файл слишком большой для отправки")
                return
            
            # Отправляем результат
            await self._send_converted_video(event, output_path, processing_msg)
            
        except Exception as e:
            logger.error(f"Error processing video: {e}", exc_info=True)
            
            # AICODE-NOTE: Специальная обработка ошибок
            error_message = str(e)
            if "file is too big" in error_message.lower():
                error_text = (
                    "❌ Файл слишком большой для обработки!\n\n"
                    f"📊 Размер файла превышает лимит ({MAX_FILE_SIZE / GB:.1f} ГБ).\n\n"
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
            
            await processing_msg.edit(error_text)
        finally:
            # Очищаем временные файлы
            await self._cleanup_temp_files(user_tmp_dir)
    
    async def handle_text(self, event):
        """Обработчик текстовых сообщений"""
        # AICODE-NOTE: Показываем справку для текстовых сообщений
        await event.respond(
            "🤖 Привет! Я бот для конвертации видео.\n\n"
            "Используйте /start для открытия главного меню или /help для справки."
        )
    
    def _is_video_file(self, document) -> bool:
        """Проверяет, является ли файл видео"""
        # AICODE-NOTE: Проверяем MIME тип и атрибуты документа
        if hasattr(document, 'mime_type') and document.mime_type:
            if document.mime_type.startswith('video/'):
                return True
        
        # AICODE-NOTE: Проверяем атрибуты документа
        for attr in document.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                return True
            elif isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name.lower()
                video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v'}
                if any(filename.endswith(ext) for ext in video_extensions):
                    return True
        
        return False
    
    async def _download_file_telethon(self, document: Document, chat_id: int, tmp_dir: Path) -> Path:
        """Proper file download implementation with progress tracking"""
        filename = "input_video" # Имя по умолчанию на случай, если атрибут не найден
        for attr in document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
                break

        file_path = tmp_dir / filename
        file_size = document.size or 0
        MB = 1024 * 1024

        # Send initial progress message
        progress_msg = await self.client.send_message(
            chat_id, 
            f"⏳ Downloading file... ({(file_size / MB):.1f} MB)"
        )
        
        try:
            downloaded_size = 0
            last_update = 0
            MB = 1024 * 1024
            
            # Download file with progress tracking
            with open(file_path, 'wb') as f:
                async for chunk in self.client.iter_download(document):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        
                        # Update progress every 5MB
                        if downloaded_size - last_update > (5 * MB) or downloaded_size == file_size:
                            if file_size > 0:
                                progress_percent = (downloaded_size / file_size * 100)
                                # Экранируем символ % с помощью %%
                                progress_text = (
                                    f"📥 Downloading: {downloaded_size / MB:.1f}MB/{file_size / MB:.1f}MB "
                                    f"({progress_percent:.1f}%%)"
                                )
                            else:
                                progress_text = f"📥 Downloading: {downloaded_size / MB:.1f}MB"
                            
                            try:
                                await self.client.edit_message(
                                    chat_id, 
                                    progress_msg, 
                                    text=progress_text
                                )
                                last_update = downloaded_size
                            except Exception as e:
                                logger.warning(f"Could not update progress: {e}")
            
            # Final success message
            await self.client.edit_message(
                chat_id, 
                progress_msg, 
                text=f"✅ File downloaded! Size: {(file_size / MB):.1f} MB"
            )
            
            return file_path
            
        except Exception as e:
            # Clean up on error
            if file_path.exists():
                file_path.unlink()
            await self.client.edit_message(
                chat_id, 
                progress_msg, 
                text=f"❌ Download failed: {str(e)}"
            )
            raise
    
    async def _convert_video(self, input_path: Path, tmp_dir: Path) -> Path:
        """Конвертирует видео с помощью Docker контейнера jrottenberg/ffmpeg с поддержкой NVIDIA"""
        output_filename = f"converted_video.mp4"
        output_path = tmp_dir / output_filename

        # Docker команда для конвертации с использованием jrottenberg/ffmpeg
        docker_cmd = [
            "docker", "run", "--rm",
            "--privileged",
            "--gpus", "all",
            # "--entrypoint", "/bin/sh",
            "-v", f"{tmp_dir.absolute()}:/workdir",
            "-w", "/workdir",
            "jrottenberg/ffmpeg:5.1.4-nvidia2004",
            # "-c", "ffmpeg",
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
    
    async def _send_converted_video(self, event, video_path: Path, processing_msg):
        """Отправляет сконвертированное видео"""
        try:
            # Удаляем сообщение о обработке
            await processing_msg.delete()
            
            # Отправляем видео
            await event.respond(
                "✅ Видео успешно сконвертировано!\n\n"
                "📊 Параметры:\n"
                "• Разрешение: 1920x1080\n"
                "• Частота кадров: 10 FPS\n"
                "• Кодек: H.264 (NVENC)\n"
                "• Аудио: AAC, 64kbps",
                file=video_path,
            )
            
            logger.info(f"Sent converted video: {video_path}")
            
        except Exception as e:
            logger.error(f"Error sending video: {e}", exc_info=True)
            await event.respond(f"❌ Ошибка при отправке видео: {str(e)}")
    
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
        logger.info("Starting Telegram Bot with Telethon...")
        
        try:
            # AICODE-NOTE: Запускаем Telethon клиент как бот (только bot_token)
            await self.client.start(bot_token=BOT_TOKEN)
            logger.info("Telethon client started successfully")
            
            # AICODE-NOTE: Запускаем бота
            await self.client.run_until_disconnected()
            
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Bot error: {e}", exc_info=True)
    

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