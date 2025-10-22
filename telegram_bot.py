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
from telethon import TelegramClient
from telethon.errors import FilePartTooBigError, FloodWaitError
from telethon.tl.types import InputFileLocation, InputPhotoFileLocation, InputDocumentFileLocation
from telethon.tl.functions.upload import GetFileRequest
import base64
import struct

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

# AICODE-NOTE: Конфигурация Telethon для обхода ограничений Bot API
TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
TELEGRAM_PHONE = os.getenv('TELEGRAM_PHONE')  # Номер телефона для авторизации

# Проверяем наличие конфигурации Telethon
USE_TELETHON = all([TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE])
if USE_TELETHON:
    logger.info("Telethon configuration found - large file support enabled")
else:
    logger.warning("Telethon configuration missing - limited to 50MB files")

# AICODE-NOTE: Настройка таймаута конвертации через переменную окружения
CONVERSION_TIMEOUT = int(os.getenv('CONVERSION_TIMEOUT', '300'))  # По умолчанию 5 минут

# AICODE-NOTE: Ограничения Telegram Bot API
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 МБ - максимальный размер файла для загрузки через Bot API
MAX_SEND_SIZE = 50 * 1024 * 1024  # 50 МБ - максимальный размер файла для отправки через Bot API

# AICODE-NOTE: Константы для отображения размеров файлов
MB = 1024 * 1024

TMP_DIR = Path("/tmp/telegram_video_converter")
TMP_DIR.mkdir(exist_ok=True)

class BotFileIdConverter:
    """
    AICODE-NOTE: Конвертер file_id от Bot API в формат, понятный Telethon
    
    Это сложная задача, так как Bot API и MTProto используют разные форматы file_id.
    Bot API file_id имеет структуру base64-encoded данных, содержащих:
    - Тип файла и флаги (4 байта)
    - DC ID (4 байта) 
    - File ID части (8-16 байт)
    - Access Hash (переменная длина)
    
    Использование:
    1. BotFileIdConverter.analyze_bot_file_id(file_id) - анализ file_id
    2. BotFileIdConverter.convert_to_telethon_location(file_id) - конвертация в Telethon
    3. BotFileIdConverter.download_file_with_telethon(client, file_id, path) - скачивание
    """
    
    @staticmethod
    def decode_bot_file_id(file_id: str) -> dict:
        """
        Декодирует file_id от Bot API в компоненты
        
        AICODE-NOTE: Bot API file_id имеет более сложную структуру:
        - Первые 4 байта: тип файла и флаги
        - Следующие 4 байта: DC ID
        - Следующие 8 байт: file_id (часть 1)
        - Следующие 8 байт: file_id (часть 2) 
        - Остальные байты: access_hash (может быть разной длины)
        """
        try:
            # AICODE-NOTE: Добавляем padding для base64 если нужно
            missing_padding = len(file_id) % 4
            if missing_padding:
                file_id += '=' * (4 - missing_padding)
            
            # Декодируем base64
            decoded = base64.b64decode(file_id)
            
            if len(decoded) < 20:  # Минимальная длина для валидного file_id
                raise ValueError(f"Invalid file_id length: {len(decoded)} bytes (minimum 20)")
            
            # Извлекаем компоненты
            file_type_flags = struct.unpack('<I', decoded[0:4])[0]
            dc_id = struct.unpack('<I', decoded[4:8])[0]
            
            # file_id состоит из двух частей по 8 байт
            if len(decoded) >= 24:
                file_id_part1 = struct.unpack('<Q', decoded[8:16])[0]
                file_id_part2 = struct.unpack('<Q', decoded[16:24])[0]
            else:
                # Для коротких file_id используем только первую часть
                file_id_part1 = struct.unpack('<Q', decoded[8:16])[0] if len(decoded) >= 16 else 0
                file_id_part2 = 0
            
            # Access hash может быть разной длины
            access_hash_bytes = decoded[24:] if len(decoded) > 24 else b''
            if len(access_hash_bytes) >= 8:
                access_hash = struct.unpack('<Q', access_hash_bytes[:8])[0]
            elif len(access_hash_bytes) > 0:
                # Для коротких access_hash используем все доступные байты
                access_hash = int.from_bytes(access_hash_bytes, 'little')
            else:
                access_hash = 0
            
            # Определяем тип файла из флагов
            file_type = file_type_flags & 0x7  # Первые 3 бита
            
            logger.debug(f"Decoded file_id: type={file_type}, dc_id={dc_id}, access_hash={access_hash}")
            
            return {
                'file_type': file_type,
                'file_type_flags': file_type_flags,
                'dc_id': dc_id,
                'file_id': file_id_part1,
                'file_id_2': file_id_part2,
                'access_hash': access_hash,
                'raw_data': decoded
            }
        except Exception as e:
            logger.error(f"Failed to decode bot file_id '{file_id}': {e}")
            raise ValueError(f"Invalid bot file_id format: {e}")
    
    @staticmethod
    def convert_to_telethon_location(file_id: str) -> InputFileLocation:
        """
        Конвертирует Bot API file_id в InputFileLocation для Telethon
        """
        try:
            decoded = BotFileIdConverter.decode_bot_file_id(file_id)
            
            # Определяем тип файла по file_type
            file_type = decoded['file_type']
            
            # AICODE-NOTE: Проверяем валидность access_hash
            if decoded['access_hash'] == 0:
                raise ValueError("Invalid access_hash: cannot be zero")
            
            if file_type == 1:  # Photo
                logger.debug(f"Converting photo file_id to InputPhotoFileLocation")
                return InputPhotoFileLocation(
                    id=decoded['file_id'],
                    access_hash=decoded['access_hash'],
                    file_reference=b''  # file_reference может быть пустым для старых файлов
                )
            elif file_type in [2, 3, 4, 5]:  # Document types
                logger.debug(f"Converting document file_id to InputDocumentFileLocation")
                return InputDocumentFileLocation(
                    id=decoded['file_id'],
                    access_hash=decoded['access_hash'],
                    file_reference=b''  # file_reference может быть пустым для старых файлов
                )
            else:
                raise ValueError(f"Unsupported file type: {file_type}. Supported types: 1 (photo), 2-5 (documents)")
                
        except Exception as e:
            logger.error(f"Failed to convert file_id to Telethon location: {e}")
            raise ValueError(f"Cannot convert Bot API file_id to Telethon format: {e}")
    
    @staticmethod
    async def download_file_with_telethon(client: TelegramClient, bot_file_id: str, output_path: Path) -> Path:
        """
        Скачивает файл используя Telethon с конвертированным file_id
        """
        try:
            # Конвертируем Bot API file_id в Telethon InputFileLocation
            location = BotFileIdConverter.convert_to_telethon_location(bot_file_id)
            
            # Скачиваем файл используя GetFileRequest
            file_request = GetFileRequest(
                location=location,
                offset=0,
                limit=0  # 0 означает скачать весь файл
            )
            
            # Выполняем запрос
            result = await client(file_request)
            
            # Сохраняем файл
            with open(output_path, 'wb') as f:
                f.write(result.bytes)
            
            logger.info(f"Successfully downloaded file via Telethon: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Failed to download file with Telethon: {e}")
            raise Exception(f"Telethon download failed: {e}")
    
    @staticmethod
    def analyze_bot_file_id(file_id: str) -> dict:
        """
        AICODE-NOTE: Анализирует Bot API file_id для отладки
        Возвращает информацию о структуре file_id
        """
        try:
            decoded = BotFileIdConverter.decode_bot_file_id(file_id)
            
            # Определяем тип файла по file_type
            file_type_names = {
                1: "Photo",
                2: "Document (Sticker)",
                3: "Document (Audio)",
                4: "Document (Video)",
                5: "Document (Other)"
            }
            
            file_type_name = file_type_names.get(decoded['file_type'], f"Unknown ({decoded['file_type']})")
            
            return {
                'file_id': file_id,
                'file_type': decoded['file_type'],
                'file_type_name': file_type_name,
                'dc_id': decoded['dc_id'],
                'access_hash': decoded['access_hash'],
                'raw_length': len(decoded['raw_data']),
                'is_valid': True
            }
        except Exception as e:
            return {
                'file_id': file_id,
                'error': str(e),
                'is_valid': False
            }

class VideoConverterBot:
    def __init__(self):
        self.bot = AsyncTeleBot(BOT_TOKEN)
        self.telethon_client = None
        self._setup_handlers()
        
        # AICODE-NOTE: Инициализируем Telethon клиент если доступна конфигурация
        if USE_TELETHON:
            # AICODE-NOTE: Создаем папку для сессий Telethon
            session_dir = Path("sessions")
            session_dir.mkdir(exist_ok=True)
            
            self.telethon_client = TelegramClient(
                str(session_dir / 'telegram_bot_session'),
                int(TELEGRAM_API_ID),
                TELEGRAM_API_HASH
            )
    
    def _setup_handlers(self):
        """Настройка обработчиков команд и сообщений"""
        # Команды
        @self.bot.message_handler(commands=['start'])
        async def start_command(message):
            await self.start_command(message)
        
        @self.bot.message_handler(commands=['help'])
        async def help_command(message):
            await self.help_command(message)
        
        @self.bot.message_handler(commands=['debug_file_id'])
        async def debug_file_id_command(message):
            await self.debug_file_id_command(message)
        
        # Обработка нажатий на кнопки
        @self.bot.callback_query_handler(func=lambda call: True)
        async def button_callback(call):
            await self.button_callback(call)
        
        # Обработка файлов
        @self.bot.message_handler(content_types=['document'])
        async def handle_document(message):
            await self.handle_document(message)
        
        # AICODE-NOTE: Обработка текстовых сообщений для кода подтверждения
        @self.bot.message_handler(content_types=['text'])
        async def handle_text(message):
            await self.handle_text(message)
        
        # AICODE-NOTE: Обработка текстовых сообщений для кода подтверждения
        @self.bot.message_handler(content_types=['text'])
        async def handle_text(message):
            await self.handle_text(message)
    
    async def start_command(self, message):
        """Обработчик команды /start"""
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("🎬 Конвертировать видео", callback_data="convert_video"))
        
        # AICODE-NOTE: Добавляем кнопку инициализации Telethon
        if USE_TELETHON:
            telethon_status = "✅ Загружен" if self.telethon_client and self.telethon_client.is_connected() else "❌ Не загружен"
            keyboard.add(types.InlineKeyboardButton(f"🔧 Инициализировать Telethon ({telethon_status})", callback_data="init_telethon"))
        
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
            "• /debug_file_id - Анализ file_id (ответьте на сообщение с файлом)\n"
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
            f"• Bot API (стандартный): {max_size_mb:.0f} МБ\n"
            f"• Telethon (большие файлы): до 2 ГБ\n"
            f"• Максимальный размер для отправки: {max_size_mb:.0f} МБ\n\n"
            "💡 Просто отправьте видео файл после нажатия кнопки конвертации!\n"
            f"⚠️ Файлы больше {max_size_mb:.0f} МБ обрабатываются через Telethon (если настроен)."
        )
        
        await self.bot.reply_to(message, help_text)
    
    async def debug_file_id_command(self, message):
        """Обработчик команды /debug_file_id для отладки file_id"""
        # AICODE-NOTE: Команда для отладки file_id конвертации
        if not message.reply_to_message or not message.reply_to_message.document:
            await self.bot.reply_to(
                message,
                "❌ Пожалуйста, ответьте на сообщение с файлом командой /debug_file_id"
            )
            return
        
        document = message.reply_to_message.document
        file_id = document.file_id
        
        try:
            # Анализируем file_id
            analysis = BotFileIdConverter.analyze_bot_file_id(file_id)
            
            if analysis['is_valid']:
                debug_text = (
                    f"🔍 Анализ file_id:\n\n"
                    f"📁 File ID: `{file_id}`\n"
                    f"📋 Тип файла: {analysis['file_type_name']} ({analysis['file_type']})\n"
                    f"🌐 DC ID: {analysis['dc_id']}\n"
                    f"🔑 Access Hash: {analysis['access_hash']}\n"
                    f"📏 Длина данных: {analysis['raw_length']} байт\n\n"
                    f"✅ File ID валиден для конвертации в Telethon"
                )
            else:
                debug_text = (
                    f"❌ Ошибка анализа file_id:\n\n"
                    f"📁 File ID: `{file_id}`\n"
                    f"🚫 Ошибка: {analysis['error']}\n\n"
                    f"⚠️ File ID не может быть конвертирован в Telethon"
                )
            
            await self.bot.reply_to(message, debug_text, parse_mode='Markdown')
            
        except Exception as e:
            await self.bot.reply_to(
                message,
                f"❌ Ошибка при анализе file_id: {str(e)}"
            )
    
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
        elif call.data == "init_telethon":
            await self._handle_telethon_init(call)
    
    async def _handle_telethon_init(self, call):
        """Обработчик инициализации Telethon"""
        if not USE_TELETHON:
            await self.bot.edit_message_text(
                "❌ Telethon не настроен!\n\n"
                "Для использования Telethon необходимо настроить переменные окружения:\n"
                "• TELEGRAM_API_ID\n"
                "• TELEGRAM_API_HASH\n"
                "• TELEGRAM_PHONE",
                call.message.chat.id,
                call.message.message_id
            )
            return
        
        # AICODE-NOTE: Проверяем, не загружен ли уже Telethon
        if self.telethon_client and self.telethon_client.is_connected():
            await self.bot.edit_message_text(
                "✅ Telethon уже загружен и готов к работе!\n\n"
                "Теперь можно обрабатывать файлы размером до 2 ГБ.",
                call.message.chat.id,
                call.message.message_id
            )
            return
        
        # AICODE-NOTE: Сохраняем ID чата для отправки кода подтверждения
        self._current_telethon_chat = call.message.chat.id
        
        # AICODE-NOTE: Показываем сообщение о начале инициализации
        await self.bot.edit_message_text(
            "🔄 Инициализирую Telethon...\n\n"
            "Это может занять некоторое время.",
            call.message.chat.id,
            call.message.message_id
        )
        
        try:
            # AICODE-NOTE: Инициализируем Telethon
            success = await self._init_telethon()
            
            if success:
                await self.bot.edit_message_text(
                    "✅ Telethon успешно инициализирован!\n\n"
                    "Теперь можно обрабатывать файлы размером до 2 ГБ.\n"
                    "Отправьте видео файл для конвертации.",
                    call.message.chat.id,
                    call.message.message_id
                )
            else:
                await self.bot.edit_message_text(
                    "❌ Не удалось инициализировать Telethon!\n\n"
                    "Проверьте настройки и попробуйте еще раз.",
                    call.message.chat.id,
                    call.message.message_id
                )
        except Exception as e:
            logger.error(f"Error initializing Telethon: {e}", exc_info=True)
            await self.bot.edit_message_text(
                f"❌ Ошибка при инициализации Telethon:\n\n{str(e)}",
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
        
        # AICODE-NOTE: Проверяем размер файла и выбираем метод загрузки
        file_size = document.file_size or 0
        use_telethon = USE_TELETHON and file_size > MAX_FILE_SIZE
        
        if file_size > MAX_FILE_SIZE and not USE_TELETHON:
            file_size_mb = file_size / MB
            max_size_mb = MAX_FILE_SIZE / MB
            await self.bot.reply_to(
                message,
                f"❌ Файл слишком большой для обработки!\n\n"
                f"📊 Размер вашего файла: {file_size_mb:.1f} МБ\n"
                f"📏 Максимальный размер Bot API: {max_size_mb:.0f} МБ\n\n"
                f"💡 Для обработки файлов больше {max_size_mb:.0f} МБ требуется настройка Telethon.\n"
                f"Пожалуйста, сожмите видео или разделите его на части."
            )
            return
        elif use_telethon:
            file_size_mb = file_size / MB
            await self.bot.reply_to(
                message,
                f"📁 Обнаружен большой файл ({file_size_mb:.1f} МБ)\n"
                f"🔄 Использую Telethon для загрузки..."
            )
        
        # Отправляем сообщение о начале обработки
        processing_msg = await self.bot.reply_to(message, "⏳ Обрабатываю видео...")
        
        try:
            # Создаем временную папку для этого запроса
            user_tmp_dir = TMP_DIR / f"user_{message.from_user.id}_{message.message_id}"
            user_tmp_dir.mkdir(exist_ok=True)
            
            # Скачиваем файл (выбираем метод в зависимости от размера)
            if use_telethon:
                try:
                    file_path = await self._download_file_telethon(document, user_tmp_dir)
                except Exception as e:
                    logger.warning(f"Telethon download failed, falling back to Bot API: {e}")
                    # AICODE-NOTE: Если Telethon не сработал, пробуем Bot API как fallback
                    if file_size <= MAX_FILE_SIZE:
                        await self.bot.edit_message_text(
                            f"⚠️ Telethon не смог загрузить файл, использую Bot API...\n"
                            f"📁 Размер файла: {file_size / MB:.1f} МБ",
                            message.chat.id,
                            processing_msg.message_id
                        )
                        file_path = await self._download_file(document, user_tmp_dir)
                    else:
                        # Файл слишком большой для Bot API, но Telethon не сработал
                        raise Exception(f"Файл слишком большой для Bot API ({file_size / MB:.1f} МБ), а Telethon не смог его загрузить: {e}")
            else:
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
    
    async def handle_text(self, message):
        """Обработчик текстовых сообщений"""
        # AICODE-NOTE: Проверяем, ожидаем ли мы код подтверждения
        if hasattr(self, '_pending_code') and self._pending_code is None:
            await self._handle_telegram_code(message)
        else:
            # AICODE-NOTE: Если это не код подтверждения, показываем справку
            await self.bot.reply_to(
                message,
                "🤖 Привет! Я бот для конвертации видео.\n\n"
                "Используйте /start для открытия главного меню или /help для справки."
            )
    
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
    
    async def _init_telethon(self):
        """Инициализирует Telethon клиент с обработкой авторизации"""
        if not self.telethon_client:
            return False
        
        # AICODE-NOTE: Проверяем, не подключен ли уже клиент
        if self.telethon_client.is_connected():
            logger.info("Telethon client already connected")
            return True
            
        try:
            # AICODE-NOTE: Запускаем Telethon клиент с обработкой авторизации
            await self.telethon_client.start(
                phone=TELEGRAM_PHONE,
                code_callback=self._telegram_code_callback,
                password=self._telegram_password_callback
            )
            logger.info("Telethon client started successfully")
            return True
        except KeyboardInterrupt:
            logger.warning("Telethon authorization cancelled by user")
            return False
        except Exception as e:
            logger.error(f"Failed to start Telethon client: {e}")
            # AICODE-NOTE: Показываем пользователю понятное сообщение об ошибке
            if "phone number invalid" in str(e).lower():
                logger.error("Invalid phone number format. Please check TELEGRAM_PHONE environment variable.")
            elif "api_id" in str(e).lower() or "api_hash" in str(e).lower():
                logger.error("Invalid API credentials. Please check TELEGRAM_API_ID and TELEGRAM_API_HASH environment variables.")
            elif "flood" in str(e).lower():
                logger.error("Too many requests. Please wait before trying again.")
            return False
    
    def _telegram_code_callback(self):
        """Обработчик ввода кода подтверждения от Telegram"""
        # AICODE-NOTE: Сохраняем код в переменную для отправки в чат
        self._pending_code = None
        self._code_event = asyncio.Event()
        
        # AICODE-NOTE: Отправляем сообщение о необходимости ввести код
        if hasattr(self, '_current_telethon_chat'):
            loop = asyncio.get_event_loop()
            loop.create_task(self.bot.send_message(
                self._current_telethon_chat,
                f"🔐 Telegram отправил код подтверждения на номер {TELEGRAM_PHONE}\n"
                f"📱 Пожалуйста, отправьте код подтверждения в этот чат:"
            ))
        
        # AICODE-NOTE: Ждем код из чата
        try:
            loop = asyncio.get_event_loop()
            # AICODE-NOTE: Ждем код в течение 5 минут
            loop.run_until_complete(asyncio.wait_for(self._code_event.wait(), timeout=300))
            return self._pending_code
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for code")
            raise Exception("Timeout waiting for code")
        except Exception as e:
            logger.error(f"Error waiting for code: {e}")
            raise
    
    async def _wait_for_code_in_chat(self):
        """Ожидает код подтверждения в чате"""
        # AICODE-NOTE: Отправляем сообщение о необходимости ввести код
        if hasattr(self, '_current_telethon_chat'):
            await self.bot.send_message(
                self._current_telethon_chat,
                f"🔐 Telegram отправил код подтверждения на номер {TELEGRAM_PHONE}\n"
                f"📱 Пожалуйста, отправьте код подтверждения в этот чат:"
            )
    
    async def _handle_telegram_code(self, message):
        """Обработчик получения кода подтверждения в чате"""
        if not hasattr(self, '_pending_code') or self._pending_code is not None:
            return
        
        code = message.text.strip()
        if code and code.isdigit() and len(code) >= 4:
            self._pending_code = code
            if hasattr(self, '_code_event'):
                self._code_event.set()
            await self.bot.reply_to(message, "✅ Код принят! Продолжаю авторизацию...")
        else:
            await self.bot.reply_to(message, "❌ Код должен содержать только цифры (минимум 4 символа)")
    
    def _telegram_password_callback(self):
        """Обработчик ввода пароля двухфакторной аутентификации"""
        print("\n🔒 Требуется пароль двухфакторной аутентификации")
        print("🔑 Пожалуйста, введите пароль:")
        
        while True:
            try:
                password = input("Пароль: ").strip()
                if password:
                    logger.info("Password entered")
                    return password
                else:
                    print("❌ Пароль не может быть пустым")
            except KeyboardInterrupt:
                print("\n❌ Отмена авторизации")
                raise
            except Exception as e:
                print(f"❌ Ошибка ввода: {e}")
    
    async def _download_file_telethon(self, document, tmp_dir: Path) -> Path:
        """Скачивает файл через Telethon (поддерживает большие файлы)"""
        if not self.telethon_client:
            raise Exception("Telethon client not available")
        
        # AICODE-NOTE: Проверяем, что Telethon клиент авторизован
        if not self.telethon_client.is_connected():
            logger.warning("Telethon client not connected, attempting to reconnect...")
            if not await self._init_telethon():
                raise Exception("Failed to initialize Telethon client")
        
        file_path = tmp_dir / document.file_name
        file_size = document.file_size or 0
        
        logger.info(f"Downloading large file via Telethon: {document.file_name} ({file_size / MB:.1f} MB)")
        
        try:
            # AICODE-NOTE: Используем конвертер для преобразования Bot API file_id в Telethon формат
            await BotFileIdConverter.download_file_with_telethon(
                self.telethon_client, 
                document.file_id, 
                file_path
            )
        
        except FilePartTooBigError:
            raise Exception("File is too big even for Telethon (over 2GB)")
        except FloodWaitError as e:
            raise Exception(f"Rate limited by Telegram, try again in {e.seconds} seconds")
        except Exception as e:
            logger.error(f"Telethon download failed: {e}")
            # AICODE-NOTE: Если конвертация не удалась, пробуем альтернативный метод
            logger.info("Trying alternative download method...")
            try:
                await self._download_file_telethon_alternative(document, file_path)
            except Exception as alt_e:
                logger.error(f"Alternative download method also failed: {alt_e}")
                raise Exception(f"All Telethon download methods failed. Original error: {e}, Alternative error: {alt_e}")
        
        logger.info(f"Successfully downloaded large file via Telethon: {file_path}")
        return file_path
    
    async def _download_file_telethon_alternative(self, document, file_path: Path):
        """
        AICODE-NOTE: Альтернативный метод скачивания через Telethon
        Используется когда конвертация file_id не удалась
        """
        try:
            # AICODE-NOTE: Пробуем найти файл через поиск в истории сообщений
            # Это менее надежный метод, но может сработать в некоторых случаях
            async for message in self.telethon_client.iter_messages('me', limit=100):
                if (message.document and 
                    message.document.file_name == document.file_name and
                    message.document.size == document.file_size):
                    
                    logger.info(f"Found matching file in message history: {message.id}")
                    
                    # Скачиваем файл
                    await self.telethon_client.download_media(message.document, file=str(file_path))
                    return
                    
            raise Exception("File not found in recent message history")
            
        except Exception as e:
            logger.error(f"Alternative download method failed: {e}")
            raise

    async def run(self):
        """Запускает бота"""
        logger.info("Starting Telegram Bot...")
        
        # AICODE-NOTE: Telethon инициализируется только по кнопке, не автоматически
        if USE_TELETHON:
            logger.info("Telethon configuration available - will initialize on demand")
        else:
            logger.warning("Telethon configuration missing - limited to 50MB files")
        
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