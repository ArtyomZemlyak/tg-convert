#!/usr/bin/env python3
"""
Telegram Bot for Video Conversion
Конвертирует видео файлы с помощью Docker контейнера jrottenberg/ffmpeg
"""

import os
import tempfile
import subprocess
import logging
from pathlib import Path
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

# AICODE-NOTE: Настройка таймаута конвертации через переменную окружения
CONVERSION_TIMEOUT = int(os.getenv('CONVERSION_TIMEOUT', '300'))  # По умолчанию 5 минут

TMP_DIR = Path("/tmp/telegram_video_converter")
TMP_DIR.mkdir(exist_ok=True)

class VideoConverterBot:
    def __init__(self):
        self.app = Application.builder().token(BOT_TOKEN).build()
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Настройка обработчиков команд и сообщений"""
        # Команды
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        
        # Обработка нажатий на кнопки
        self.app.add_handler(CallbackQueryHandler(self.button_callback))
        
        # Обработка файлов
        self.app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        keyboard = [
            [InlineKeyboardButton("🎬 Конвертировать видео", callback_data="convert_video")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = (
            "🎥 Добро пожаловать в бот для конвертации видео!\n\n"
            "Выберите действие из меню ниже:"
        )
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /help"""
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
            "💡 Просто отправьте видео файл после нажатия кнопки конвертации!"
        )
        
        await update.message.reply_text(help_text)
    
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "convert_video":
            await query.edit_message_text(
                "📁 Пожалуйста, отправьте видео файл для конвертации.\n\n"
                "Поддерживаемые форматы: MP4, AVI, MOV, MKV и другие."
            )
    
    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик загруженных файлов"""
        document = update.message.document
        
        # Проверяем, что это видео файл
        if not self._is_video_file(document.file_name):
            await update.message.reply_text(
                "❌ Пожалуйста, отправьте видео файл (MP4, AVI, MOV, MKV и т.д.)"
            )
            return
        
        # Отправляем сообщение о начале обработки
        processing_msg = await update.message.reply_text("⏳ Обрабатываю видео...")
        
        try:
            # Создаем временную папку для этого запроса
            user_tmp_dir = TMP_DIR / f"user_{update.effective_user.id}_{update.update_id}"
            user_tmp_dir.mkdir(exist_ok=True)
            
            # Скачиваем файл
            file_path = await self._download_file(document, user_tmp_dir)
            
            # Конвертируем видео
            output_path = await self._convert_video(file_path, user_tmp_dir)
            
            # Отправляем результат
            await self._send_converted_video(update, output_path, processing_msg)
            
        except Exception as e:
            logger.error(f"Error processing video: {e}")
            await processing_msg.edit_text(
                f"❌ Произошла ошибка при обработке видео: {str(e)}"
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
        """Скачивает файл во временную папку"""
        file = await document.get_file()
        file_path = tmp_dir / document.file_name
        
        await file.download_to_drive(file_path)
        logger.info(f"Downloaded file: {file_path}")
        
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
            # Запускаем Docker контейнер
            result = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=CONVERSION_TIMEOUT  # Настраиваемый таймаут
            )
            
            if result.returncode != 0:
                raise Exception(f"Docker command failed: {result.stderr}")
            
            if not output_path.exists():
                raise Exception("Output file was not created")
            
            logger.info(f"Video converted successfully: {output_path}")
            return output_path
            
        except subprocess.TimeoutExpired:
            raise Exception(f"Конвертация видео заняла слишком много времени (лимит: {CONVERSION_TIMEOUT} секунд)")
        except Exception as e:
            logger.error(f"Docker conversion error: {e}")
            raise
    
    async def _send_converted_video(self, update: Update, video_path: Path, processing_msg):
        """Отправляет сконвертированное видео"""
        try:
            # Удаляем сообщение о обработке
            await processing_msg.delete()
            
            # Отправляем видео
            with open(video_path, 'rb') as video_file:
                await update.message.reply_video(
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
            logger.error(f"Error sending video: {e}")
            await update.message.reply_text(f"❌ Ошибка при отправке видео: {str(e)}")
    
    async def _cleanup_temp_files(self, tmp_dir: Path):
        """Очищает временные файлы"""
        try:
            import shutil
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
                logger.info(f"Cleaned up temp directory: {tmp_dir}")
        except Exception as e:
            logger.error(f"Error cleaning up temp files: {e}")
    
    def run(self):
        """Запускает бота"""
        logger.info("Starting Telegram Bot...")
        self.app.run_polling()

def main():
    """Главная функция"""
    try:
        bot = VideoConverterBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}")

if __name__ == "__main__":
    main()