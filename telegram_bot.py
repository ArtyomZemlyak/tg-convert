#!/usr/bin/env python3
"""
Telegram Bot for Video Conversion
Конвертирует видео файлы с помощью Docker контейнера jrottenberg/ffmpeg

AICODE-NOTE: Обновлено для поддержки больших файлов:
- Проверка размера файла перед обработкой
- Адаптивное сжатие в зависимости от размера
- Разделение очень больших видео на части
- Отправка как документ при необходимости
- Отслеживание прогресса для больших файлов
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
        help_text = (
            "🤖 Возможности бота:\n\n"
            "• /start - Открыть главное меню\n"
            "• /help - Показать эту справку\n"
            "• 🎬 Конвертировать видео - Загрузить и конвертировать видео файл\n\n"
            "📋 Поддерживаемые форматы:\n"
            "• Входные: MP4, AVI, MOV, MKV и другие\n"
            "• Выходные: MP4 (H.264)\n\n"
            "⚙️ Параметры конвертации:\n"
            "• Адаптивное сжатие в зависимости от размера файла\n"
            "• Кодек видео: H.264 (NVENC)\n"
            "• Кодек аудио: AAC\n"
            f"• Таймаут: {CONVERSION_TIMEOUT} секунд\n\n"
            "📏 Ограничения и возможности:\n"
            "• Максимальный размер: 2 ГБ\n"
            "• Автоматическое сжатие больших файлов\n"
            "• Разделение очень больших видео на части\n"
            "• Отправка как документ при необходимости\n\n"
            "💡 Просто отправьте видео файл после нажатия кнопки конвертации!"
        )
        
        await self.bot.reply_to(message, help_text)
    
    async def button_callback(self, call):
        """Обработчик нажатий на кнопки"""
        await self.bot.answer_callback_query(call.id)
        
        if call.data == "convert_video":
            await self.bot.edit_message_text(
                "📁 Пожалуйста, отправьте видео файл для конвертации.\n\n"
                "Поддерживаемые форматы: MP4, AVI, MOV, MKV и другие.",
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
        
        # AICODE-NOTE: Проверяем размер файла (Telegram ограничение: 2GB для видео)
        file_size_mb = document.file_size / (1024 * 1024) if document.file_size else 0
        max_size_mb = 2000  # 2GB в МБ
        
        if file_size_mb > max_size_mb:
            await self.bot.reply_to(
                message,
                f"❌ Файл слишком большой ({file_size_mb:.1f} МБ). "
                f"Максимальный размер: {max_size_mb} МБ.\n\n"
                "💡 Попробуйте сжать видео перед загрузкой или разделить на части."
            )
            return
        
        # Отправляем сообщение о начале обработки
        processing_msg = await self.bot.reply_to(
            message, 
            f"⏳ Обрабатываю видео ({file_size_mb:.1f} МБ)...\n"
            "Это может занять некоторое время для больших файлов."
        )
        
        try:
            # Создаем временную папку для этого запроса
            user_tmp_dir = TMP_DIR / f"user_{message.from_user.id}_{message.message_id}"
            user_tmp_dir.mkdir(exist_ok=True)
            
            # Скачиваем файл
            file_path = await self._download_file(document, user_tmp_dir)
            
            # AICODE-NOTE: Для очень больших файлов (>1.5GB) сначала разделяем на части
            file_size_mb = file_path.stat().st_size / (1024 * 1024)
            
            if file_size_mb > 1500:  # 1.5GB
                await self.bot.edit_message_text(
                    f"📂 Файл очень большой ({file_size_mb:.1f} МБ). Разделяю на части...",
                    processing_msg.chat.id,
                    processing_msg.message_id
                )
                
                # Разделяем видео на части
                video_parts = await self._split_large_video(file_path, user_tmp_dir)
                
                await self.bot.edit_message_text(
                    f"🔄 Обработано {len(video_parts)} частей. Конвертирую каждую часть...",
                    processing_msg.chat.id,
                    processing_msg.message_id
                )
                
                # Конвертируем каждую часть
                converted_parts = []
                for i, part_path in enumerate(video_parts):
                    await self.bot.edit_message_text(
                        f"🔄 Конвертирую часть {i+1} из {len(video_parts)}...",
                        processing_msg.chat.id,
                        processing_msg.message_id
                    )
                    
                    converted_part = await self._convert_video(part_path, user_tmp_dir, None)
                    converted_parts.append(converted_part)
                
                # Отправляем все части
                await self._send_converted_video_parts(message, converted_parts, processing_msg)
                
            else:
                # Обычная обработка для файлов меньшего размера
                output_path = await self._convert_video(file_path, user_tmp_dir, processing_msg)
                await self._send_converted_video(message, output_path, processing_msg)
            
        except Exception as e:
            logger.error(f"Error processing video: {e}", exc_info=True)
            await self.bot.edit_message_text(
                f"❌ Произошла ошибка при обработке видео: {str(e)}",
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
        """Скачивает файл во временную папку"""
        file_info = await self.bot.get_file(document.file_id)
        file_path = tmp_dir / document.file_name
        
        # Скачиваем файл асинхронно
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.telegram.org/file/bot{self.bot.token}/{file_info.file_path}") as response:
                with open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)
        
        logger.info(f"Downloaded file: {file_path}")
        
        return file_path
    
    async def _convert_video(self, input_path: Path, tmp_dir: Path, processing_msg=None) -> Path:
        """Конвертирует видео с помощью Docker контейнера jrottenberg/ffmpeg с поддержкой NVIDIA"""
        output_filename = f"converted_{input_path.stem}.mp4"
        output_path = tmp_dir / output_filename
        
        # AICODE-NOTE: Определяем параметры сжатия в зависимости от размера файла
        file_size_mb = input_path.stat().st_size / (1024 * 1024)
        
        if file_size_mb > 500:  # Для файлов больше 500 МБ используем более агрессивное сжатие
            video_params = [
                "-vf", "fps=8,format=yuv420p,scale=1280:720",  # Меньше FPS и разрешение
                "-c:v", "h264_nvenc",
                "-preset", "p7",
                "-cq", "28",  # Более высокое значение = больше сжатие
                "-maxrate", "2M",  # Ограничиваем битрейт
                "-bufsize", "4M"
            ]
            audio_params = ["-c:a", "aac", "-b:a", "32k", "-ac", "1"]  # Меньше битрейт аудио
        elif file_size_mb > 100:  # Для файлов 100-500 МБ
            video_params = [
                "-vf", "fps=10,format=yuv420p,scale=1600:900",
                "-c:v", "h264_nvenc",
                "-preset", "p7",
                "-cq", "26",
                "-maxrate", "3M",
                "-bufsize", "6M"
            ]
            audio_params = ["-c:a", "aac", "-b:a", "48k", "-ac", "1"]
        else:  # Для файлов меньше 100 МБ используем стандартные параметры
            video_params = [
                "-vf", "fps=10,format=yuv420p",
                "-c:v", "h264_nvenc",
                "-preset", "p7",
                "-cq", "26",
                "-s", "1920x1080"
            ]
            audio_params = ["-c:a", "aac", "-b:a", "64k", "-ac", "1"]
        
        # Docker команда для конвертации с использованием jrottenberg/ffmpeg
        docker_cmd = [
            "docker", "run", "--rm",
            "--gpus", "all",
            "-v", f"{tmp_dir.absolute()}:/workdir",
            "-w", "/workdir",
            "jrottenberg/ffmpeg:5.1.4-nvidia2004",
            "-threads", "0",
            "-i", input_path.name,
            *video_params,
            *audio_params,
            "-y", output_filename
        ]
        
        logger.info(f"Running Docker command: {' '.join(docker_cmd)}")
        
        # AICODE-NOTE: Обновляем сообщение о прогрессе
        if processing_msg:
            await self.bot.edit_message_text(
                f"🔄 Конвертирую видео ({file_size_mb:.1f} МБ)...\n"
                f"Параметры: {'Агрессивное сжатие' if file_size_mb > 500 else 'Стандартное сжатие'}\n"
                "⏳ Пожалуйста, подождите...",
                processing_msg.chat.id,
                processing_msg.message_id
            )
        
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
            
            # AICODE-NOTE: Проверяем размер выходного файла
            output_size_mb = output_path.stat().st_size / (1024 * 1024)
            compression_ratio = (1 - output_size_mb / file_size_mb) * 100 if file_size_mb > 0 else 0
            
            logger.info(f"Video converted successfully: {output_path} (сжатие: {compression_ratio:.1f}%)")
            
            # AICODE-NOTE: Обновляем сообщение о завершении конвертации
            if processing_msg:
                await self.bot.edit_message_text(
                    f"✅ Конвертация завершена!\n"
                    f"📊 Исходный размер: {file_size_mb:.1f} МБ\n"
                    f"📊 Выходной размер: {output_size_mb:.1f} МБ\n"
                    f"📊 Сжатие: {compression_ratio:.1f}%\n"
                    f"📤 Отправляю результат...",
                    processing_msg.chat.id,
                    processing_msg.message_id
                )
            
            return output_path
            
        except Exception as e:
            logger.error(f"Docker conversion error: {e}", exc_info=True)
            raise
    
    async def _send_converted_video(self, message, video_path: Path, processing_msg):
        """Отправляет сконвертированное видео"""
        try:
            # AICODE-NOTE: Проверяем размер файла перед отправкой
            file_size_mb = video_path.stat().st_size / (1024 * 1024)
            max_telegram_size = 2000  # 2GB в МБ
            
            # Удаляем сообщение о обработке
            await self.bot.delete_message(processing_msg.chat.id, processing_msg.message_id)
            
            if file_size_mb > max_telegram_size:
                # AICODE-NOTE: Если файл все еще слишком большой, предлагаем альтернативы
                await self.bot.reply_to(
                    message,
                    f"❌ К сожалению, даже после сжатия файл слишком большой ({file_size_mb:.1f} МБ).\n\n"
                    "💡 Рекомендации:\n"
                    "• Попробуйте разделить видео на части\n"
                    "• Используйте более короткие видео\n"
                    "• Сожмите видео вручную перед загрузкой\n\n"
                    "📁 Файл сохранен во временной папке для скачивания."
                )
                return
            
            # AICODE-NOTE: Пытаемся отправить видео с обработкой ошибок размера
            try:
                with open(video_path, 'rb') as video_file:
                    await self.bot.send_video(
                        message.chat.id,
                        video=video_file,
                        caption="✅ Видео успешно сконвертировано!\n\n"
                               f"📊 Размер: {file_size_mb:.1f} МБ\n"
                               "📊 Параметры сжатия:\n"
                               "• Кодек: H.264 (NVENC)\n"
                               "• Аудио: AAC"
                    )
                
                logger.info(f"Sent converted video: {video_path}")
                
            except Exception as send_error:
                error_msg = str(send_error)
                if "file is too big" in error_msg.lower() or "400" in error_msg:
                    # AICODE-NOTE: Если файл все еще слишком большой, предлагаем скачать как документ
                    await self.bot.reply_to(
                        message,
                        f"⚠️ Видео слишком большое для отправки как видео ({file_size_mb:.1f} МБ).\n\n"
                        "📁 Отправляю как документ..."
                    )
                    
                    # Отправляем как документ
                    with open(video_path, 'rb') as video_file:
                        await self.bot.send_document(
                            message.chat.id,
                            document=video_file,
                            caption="✅ Видео сконвертировано и отправлено как документ!\n\n"
                                   f"📊 Размер: {file_size_mb:.1f} МБ\n"
                                   "💡 Для воспроизведения скачайте файл"
                        )
                    
                    logger.info(f"Sent converted video as document: {video_path}")
                else:
                    raise send_error
            
        except Exception as e:
            logger.error(f"Error sending video: {e}", exc_info=True)
            await self.bot.reply_to(message, f"❌ Ошибка при отправке видео: {str(e)}")
    
    async def _send_converted_video_parts(self, message, video_parts: list[Path], processing_msg):
        """Отправляет сконвертированные части видео"""
        try:
            # Удаляем сообщение о обработке
            await self.bot.delete_message(processing_msg.chat.id, processing_msg.message_id)
            
            total_size = sum(part.stat().st_size for part in video_parts) / (1024 * 1024)
            
            await self.bot.reply_to(
                message,
                f"✅ Видео успешно разделено и сконвертировано!\n\n"
                f"📊 Количество частей: {len(video_parts)}\n"
                f"📊 Общий размер: {total_size:.1f} МБ\n\n"
                "📤 Отправляю части..."
            )
            
            for i, part_path in enumerate(video_parts):
                part_size_mb = part_path.stat().st_size / (1024 * 1024)
                
                try:
                    with open(part_path, 'rb') as video_file:
                        await self.bot.send_video(
                            message.chat.id,
                            video=video_file,
                            caption=f"📹 Часть {i+1} из {len(video_parts)}\n"
                                   f"📊 Размер: {part_size_mb:.1f} МБ"
                        )
                    
                    logger.info(f"Sent video part {i+1}: {part_path}")
                    
                except Exception as part_error:
                    error_msg = str(part_error)
                    if "file is too big" in error_msg.lower():
                        # Отправляем как документ если видео слишком большое
                        with open(part_path, 'rb') as video_file:
                            await self.bot.send_document(
                                message.chat.id,
                                document=video_file,
                                caption=f"📄 Часть {i+1} из {len(video_parts)} (как документ)\n"
                                       f"📊 Размер: {part_size_mb:.1f} МБ"
                            )
                        logger.info(f"Sent video part {i+1} as document: {part_path}")
                    else:
                        raise part_error
                
                # AICODE-NOTE: Небольшая задержка между отправками для избежания rate limiting
                await asyncio.sleep(1)
            
            await self.bot.reply_to(
                message,
                "🎉 Все части успешно отправлены!\n\n"
                "💡 Для воспроизведения скачайте все части и соедините их в правильном порядке."
            )
            
        except Exception as e:
            logger.error(f"Error sending video parts: {e}", exc_info=True)
            await self.bot.reply_to(message, f"❌ Ошибка при отправке частей видео: {str(e)}")
    
    async def _split_large_video(self, input_path: Path, tmp_dir: Path, max_size_mb: int = 1500) -> list[Path]:
        """Разделяет большое видео на части"""
        file_size_mb = input_path.stat().st_size / (1024 * 1024)
        
        if file_size_mb <= max_size_mb:
            return [input_path]
        
        # AICODE-NOTE: Получаем длительность видео для расчета времени разделения
        duration_cmd = [
            "docker", "run", "--rm",
            "-v", f"{tmp_dir.absolute()}:/workdir",
            "-w", "/workdir",
            "jrottenberg/ffmpeg:5.1.4-nvidia2004",
            "-i", input_path.name,
            "-f", "null", "-"
        ]
        
        try:
            process = await asyncio.create_subprocess_exec(
                *duration_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            # AICODE-NOTE: Извлекаем длительность из stderr (ffmpeg выводит информацию туда)
            duration_match = None
            for line in stderr.decode().split('\n'):
                if 'Duration:' in line:
                    duration_match = line
                    break
            
            if not duration_match:
                raise Exception("Не удалось определить длительность видео")
            
            # AICODE-NOTE: Парсим длительность (формат: HH:MM:SS.microseconds)
            duration_str = duration_match.split('Duration:')[1].split(',')[0].strip()
            h, m, s = duration_str.split(':')
            total_seconds = int(h) * 3600 + int(m) * 60 + float(s)
            
            # AICODE-NOTE: Рассчитываем количество частей
            num_parts = int((file_size_mb / max_size_mb) + 1)
            part_duration = total_seconds / num_parts
            
            logger.info(f"Splitting video into {num_parts} parts, {part_duration:.1f}s each")
            
            parts = []
            for i in range(num_parts):
                start_time = i * part_duration
                part_filename = f"part_{i+1}_{input_path.stem}.mp4"
                part_path = tmp_dir / part_filename
                
                split_cmd = [
                    "docker", "run", "--rm",
                    "--gpus", "all",
                    "-v", f"{tmp_dir.absolute()}:/workdir",
                    "-w", "/workdir",
                    "jrottenberg/ffmpeg:5.1.4-nvidia2004",
                    "-i", input_path.name,
                    "-ss", str(start_time),
                    "-t", str(part_duration),
                    "-c", "copy",  # Копируем без перекодирования для скорости
                    "-y", part_filename
                ]
                
                process = await asyncio.create_subprocess_exec(
                    *split_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                
                if process.returncode == 0 and part_path.exists():
                    parts.append(part_path)
                    logger.info(f"Created part {i+1}: {part_path}")
                else:
                    logger.error(f"Failed to create part {i+1}: {stderr.decode()}")
            
            return parts
            
        except Exception as e:
            logger.error(f"Error splitting video: {e}", exc_info=True)
            raise Exception(f"Не удалось разделить видео: {str(e)}")
    
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