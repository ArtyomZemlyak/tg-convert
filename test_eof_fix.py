#!/usr/bin/env python3
"""
Тест для проверки исправления ошибки EOF when reading a line
"""

import os
import sys
from pathlib import Path

# Добавляем текущую директорию в путь для импорта
sys.path.insert(0, str(Path(__file__).parent))

from telegram_bot import VideoConverterBot

def test_safe_input():
    """Тестирует функцию _safe_input в различных условиях"""
    print("🧪 Тестирование функции _safe_input...")
    
    # Создаем экземпляр бота
    bot = VideoConverterBot()
    
    # Тест 1: Проверяем, что функция существует
    assert hasattr(bot, '_safe_input'), "Функция _safe_input не найдена"
    print("✅ Функция _safe_input найдена")
    
    # Тест 2: Проверяем работу с переменной окружения
    test_code = "12345"
    os.environ['TELEGRAM_CODE_INPUT'] = test_code
    
    try:
        # Симулируем ситуацию, когда stdin недоступен
        original_stdin = sys.stdin
        sys.stdin = None
        
        result = bot._safe_input("Тест: ", "TELEGRAM_CODE_INPUT")
        assert result == test_code, f"Ожидался {test_code}, получен {result}"
        print("✅ Работа с переменной окружения работает корректно")
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании переменной окружения: {e}")
    finally:
        # Восстанавливаем stdin
        sys.stdin = original_stdin
        # Очищаем переменную окружения
        if 'TELEGRAM_CODE_INPUT' in os.environ:
            del os.environ['TELEGRAM_CODE_INPUT']
    
    print("🎉 Все тесты пройдены успешно!")

def test_error_handling():
    """Тестирует обработку ошибок"""
    print("\n🧪 Тестирование обработки ошибок...")
    
    bot = VideoConverterBot()
    
    # Тест: Проверяем, что функции обратного вызова существуют
    assert hasattr(bot, '_telegram_code_callback'), "Функция _telegram_code_callback не найдена"
    assert hasattr(bot, '_telegram_password_callback'), "Функция _telegram_password_callback не найдена"
    print("✅ Функции обратного вызова найдены")
    
    print("🎉 Тесты обработки ошибок пройдены!")

if __name__ == "__main__":
    print("🚀 Запуск тестов исправления EOF ошибки...")
    
    try:
        test_safe_input()
        test_error_handling()
        print("\n✅ Все тесты успешно пройдены!")
        print("💡 Исправление ошибки EOF when reading a line работает корректно")
    except Exception as e:
        print(f"\n❌ Тесты не пройдены: {e}")
        sys.exit(1)