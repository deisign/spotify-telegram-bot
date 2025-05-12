import asyncio
import logging
import os
import re
import aiohttp
import requests
import signal
import sys
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

import spotipy
from aiogram import Bot, Dispatcher
from aiogram.types import Message, FSInputFile, URLInputFile
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramConflictError
from spotipy.oauth2 import SpotifyOAuth
from supabase import create_client, Client

# Остальной код оставляем без изменений...

# Добавляем эти новые функции перед main()

async def on_startup(dispatcher):
    # Удаляем веб-хук перед запуском бота
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook deleted, bot started")

async def on_shutdown(dispatcher):
    # Закрываем соединения
    await bot.session.close()
    logger.info("Bot shutdown complete")

# Обработчик ручного прерывания
def signal_handler(sig, frame):
    logger.info(f'Received signal {sig}, shutting down...')
    # Создаем новый event loop для закрытия соединений
    new_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(new_loop)
    new_loop.run_until_complete(bot.session.close())
    logger.info("Bot session closed")
    sys.exit(0)

def register_shutdown_handlers():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

# Функция для обновления статуса бота (heartbeat)
async def update_bot_status():
    while True:
        try:
            current_time = datetime.now().isoformat()
            supabase.table('bot_status').upsert({
                'key': 'heartbeat',
                'value': current_time
            }).execute()
            logger.debug(f"Heartbeat updated at {current_time}")
        except Exception as e:
            logger.error(f"Error updating heartbeat: {e}")
        
        await asyncio.sleep(30)  # Обновляем каждые 30 секунд

# Теперь модифицируем main(), но НЕ переписываем его полностью
async def main():
    # Регистрируем обработчики сигналов
    register_shutdown_handlers()
    
    # Try to load queue from database if it exists
    try:
        result = supabase.table('post_queue').select('*').eq('posted', False).order('id').execute()
        global posting_queue
        posting_queue = result.data if result.data else []
        logger.info(f"Loaded {len(posting_queue)} items from queue")
    except Exception as e:
        logger.error(f"Error loading queue: {e}")
        logger.info("Starting with empty queue")
    
    # Запускаем задачу обновления статуса
    asyncio.create_task(update_bot_status())
    
    logger.info("Starting bot...")
    try:
        await dp.start_polling(
            bot,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
            skip_updates=True,
            timeout=60,
            allowed_updates=['message', 'callback_query', 'my_chat_member', 'chat_member']
        )
    except TelegramConflictError as e:
        logger.error(f"Telegram conflict error: {e}")
        # Подождем некоторое время и повторим попытку
        await asyncio.sleep(15)
        logger.info("Retrying after conflict...")
        
        # Удаляем webhook и пробуем снова
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(
                bot,
                on_startup=on_startup,
                on_shutdown=on_shutdown,
                skip_updates=True,
                timeout=60,
                allowed_updates=['message', 'callback_query', 'my_chat_member', 'chat_member']
            )
        except Exception as retry_error:
            logger.error(f"Failed to restart after conflict: {retry_error}")
            sys.exit(1)
