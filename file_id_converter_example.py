#!/usr/bin/env python3
"""
AICODE-NOTE: Пример использования конвертера file_id от Bot API в Telethon
"""

import asyncio
from pathlib import Path
from telegram_bot import BotFileIdConverter

async def example_usage():
    """
    Пример использования конвертера file_id
    """
    print("🚀 Пример использования конвертера file_id...")
    
    # Пример file_id от Bot API (замените на реальный)
    bot_file_id = "BAADBAADrwADBREAAYag8mVBjQABAg"
    
    print(f"📁 Исходный Bot API file_id: {bot_file_id}")
    
    # 1. Анализируем file_id
    print("\n🔍 Анализ file_id:")
    analysis = BotFileIdConverter.analyze_bot_file_id(bot_file_id)
    
    if analysis['is_valid']:
        print(f"✅ File ID валиден:")
        print(f"   Тип файла: {analysis['file_type_name']}")
        print(f"   DC ID: {analysis['dc_id']}")
        print(f"   Access Hash: {analysis['access_hash']}")
        print(f"   Длина данных: {analysis['raw_length']} байт")
        
        # 2. Конвертируем в Telethon location
        print("\n🔄 Конвертация в Telethon location:")
        try:
            location = BotFileIdConverter.convert_to_telethon_location(bot_file_id)
            print(f"✅ Успешно конвертирован в: {type(location).__name__}")
            print(f"   ID: {location.id}")
            print(f"   Access Hash: {location.access_hash}")
            
        except Exception as e:
            print(f"❌ Ошибка конвертации: {e}")
    else:
        print(f"❌ File ID невалиден: {analysis['error']}")

if __name__ == "__main__":
    asyncio.run(example_usage())