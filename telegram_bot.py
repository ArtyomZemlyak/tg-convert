#!/usr/bin/env python3
"""
Telegram Bot for Video Conversion (Async Version)
Конвертирует видео файлы с помощью Docker контейнера jrottenberg/ffmpeg
"""

import os
import tempfile
import asyncio
import aiohttp
import aiofiles
from pathlib import Path
from typing import Optional
from loguru import logger

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

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

TMP_DIR = Path("/tmp/telegram_video_converter")
TMP_DIR.mkdir(exist_ok=True)

# FSM States
class VideoStates(StatesGroup):
    waiting_for_video = State()

class VideoConverterBot:
    def __init__(self):
        self.bot = Bot(token=BOT_TOKEN)
        self.dp = Dispatcher(storage=MemoryStorage())
        self._setup_handlers()
        self.active_conversions = {}  # AICODE-NOTE: Отслеживание активных конвертаций
        self.conversion_semaphore = asyncio.Semaphore(3)  # AICODE-NOTE: Ограничение одновременных конвертаций
        self._cleanup_task = None  # AICODE-NOTE: Задача для очистки старых конвертаций
    
    def _setup_handlers(self):
        """Настройка обработчиков команд и сообщений"""
        # Команды
        self.dp.message.register(self.start_command, Command("start"))
        self.dp.message.register(self.help_command, Command("help"))
        
        # Обработка нажатий на кнопки
        self.dp.callback_query.register(self.button_callback)
        
        # Обработка файлов
        self.dp.message.register(self.handle_document, F.document)
    
    async def start_command(self, message: Message):
        """Обработчик команды /start"""
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎬 Конвертировать видео", callback_data="convert_video")]
        ])
        
        welcome_text = (
            "🎥 Добро пожаловать в бот для конвертации видео!\n\n"
            "Выберите действие из меню ниже:"
        )
        
        await message.answer(welcome_text, reply_markup=keyboard)
    
    async def help_command(self, message: Message):
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
        
        await message.answer(help_text)
    
    async def button_callback(self, call: CallbackQuery, state: FSMContext):
        """Обработчик нажатий на кнопки"""
        await call.answer()
        
        if call.data == "convert_video":
            await call.message.edit_text(
                "📁 Пожалуйста, отправьте видео файл для конвертации.\n\n"
                "Поддерживаемые форматы: MP4, AVI, MOV, MKV и другие."
            )
            await state.set_state(VideoStates.waiting_for_video)
    
    async def handle_document(self, message: Message, state: FSMContext):
        """Обработчик загруженных файлов"""
        current_state = await state.get_state()
        if current_state != VideoStates.waiting_for_video:
            return
        
        document = message.document
        
        # Проверяем, что это видео файл
        if not self._is_video_file(document.file_name):
            await message.answer(
                "❌ Пожалуйста, отправьте видео файл (MP4, AVI, MOV, MKV и т.д.)"
            )
            return
        
        # Проверяем, не конвертируется ли уже видео для этого пользователя
        user_id = message.from_user.id
        if user_id in self.active_conversions:
            await message.answer(
                "⏳ У вас уже идет конвертация видео. Пожалуйста, дождитесь завершения."
            )
            return
        
        # Отправляем сообщение о начале обработки
        processing_msg = await message.answer("⏳ Обрабатываю видео...")
        
        # Добавляем пользователя в список активных конвертаций
        self.active_conversions[user_id] = True
        
        # AICODE-NOTE: Используем семафор для ограничения одновременных конвертаций
        async with self.conversion_semaphore:
        
            try:
                # Создаем временную папку для этого запроса
                user_tmp_dir = TMP_DIR / f"user_{user_id}_{message.message_id}"
                user_tmp_dir.mkdir(exist_ok=True)
                
                # Скачиваем файл асинхронно
                file_path = await self._download_file(document, user_tmp_dir)
                
                # Конвертируем видео асинхронно
                output_path = await self._convert_video(file_path, user_tmp_dir)
                
                # Отправляем результат
                await self._send_converted_video(message, output_path, processing_msg)
                
            except Exception as e:
                logger.error(f"Error processing video: {e}", exc_info=True)
                await processing_msg.edit_text(
                    f"❌ Произошла ошибка при обработке видео: {str(e)}"
                )
            finally:
                # Удаляем пользователя из списка активных конвертаций
                self.active_conversions.pop(user_id, None)
                # Очищаем временные файлы
                await self._cleanup_temp_files(user_tmp_dir)
                # Сбрасываем состояние
                await state.clear()
    
    def _is_video_file(self, filename: str) -> bool:
        """Проверяет, является ли файл видео"""
        if not filename:
            return False
        
        video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv', '.webm', '.m4v'}
        return Path(filename).suffix.lower() in video_extensions
    
    async def _download_file(self, document, tmp_dir: Path) -> Path:
        """Асинхронно скачивает файл во временную папку"""
        file_path = tmp_dir / document.file_name
        
        # Получаем информацию о файле
        file_info = await self.bot.get_file(document.file_id)
        
        # Скачиваем файл асинхронно
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/file/bot{self.bot.token}/{file_info.file_path}") as response:
                async with aiofiles.open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        await f.write(chunk)
        
        logger.info(f"Downloaded file: {file_path}")
        
        return file_path
    
    async def _convert_video(self, input_path: Path, tmp_dir: Path) -> Path:
        """Асинхронно конвертирует видео с помощью Docker контейнера jrottenberg/ffmpeg с поддержкой NVIDIA"""
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
                stderr=asyncio.subprocess.PIPE,
                cwd=tmp_dir
            )
            
            # Ждем завершения с таймаутом
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
    
    async def _send_converted_video(self, message: Message, video_path: Path, processing_msg: Message):
        """Асинхронно отправляет сконвертированное видео"""
        try:
            # Удаляем сообщение о обработке
            await processing_msg.delete()
            
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
            await message.answer(f"❌ Ошибка при отправке видео: {str(e)}")
    
    async def _cleanup_temp_files(self, tmp_dir: Path):
        """Асинхронно очищает временные файлы"""
        try:
            import shutil
            if tmp_dir.exists():
                # AICODE-NOTE: Используем asyncio для неблокирующего удаления
                await asyncio.get_event_loop().run_in_executor(
                    None, shutil.rmtree, tmp_dir
                )
                logger.info(f"Cleaned up temp directory: {tmp_dir}")
        except Exception as e:
            logger.error(f"Error cleaning up temp files: {e}", exc_info=True)
    
    async def _cleanup_background_task(self):
        """AICODE-NOTE: Фоновая задача для очистки завершенных конвертаций"""
        while True:
            try:
                await asyncio.sleep(60)  # Проверяем каждую минуту
                completed_users = []
                for user_id, status in self.active_conversions.items():
                    if status is True:  # Простая проверка статуса
                        # В реальной реализации здесь была бы проверка статуса задачи
                        pass
            except Exception as e:
                logger.error(f"Error in cleanup background task: {e}")
    
    async def run(self):
        """Запускает бота асинхронно"""
        logger.info("Starting Async Telegram Bot...")
        
        # Запускаем фоновую задачу очистки
        self._cleanup_task = asyncio.create_task(self._cleanup_background_task())
        
        try:
            await self.dp.start_polling(self.bot)
        finally:
            # Отменяем фоновую задачу
            if self._cleanup_task:
                self._cleanup_task.cancel()
            await self.bot.session.close()

async def main():
    """Главная асинхронная функция"""
    try:
        bot = VideoConverterBot()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())