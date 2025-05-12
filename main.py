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
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramConflictError
from spotipy.oauth2 import SpotifyOAuth
from supabase import create_client, Client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", "12"))

# Initialize services
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()  # Используем диспетчер без параметров
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Остальная часть кода (auth_manager, get_spotify_client, scrape_bandcamp...)
# ...

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: Message):
    logger.info("Received /start command")
    await message.answer("Bot is working! Send a Spotify link or use /help")

@dp.message(Command("help"))
async def cmd_help(message: Message):
    logger.info("Received /help command")
    help_text = """🎵 Spotify Release Tracker Bot

Available commands:
/help - Show this help message
/queue - Show posting queue
/post - Post next item in queue manually
/check - Check for new releases

You can also send Spotify or Bandcamp links to add them to the queue."""
    
    await message.answer(help_text)

@dp.message(Command("queue"))
async def cmd_queue(message: Message):
    logger.info("Received /queue command")
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    queue_text = "📦 Post Queue:\n\n"
    
    sp = get_spotify_client()
    
    for i, item in enumerate(posting_queue, 1):
        if item.get('item_type') == 'album' and sp:
            try:
                album = sp.album(item['item_id'])
                artist_name = ', '.join([artist['name'] for artist in album['artists']])
                album_name = album['name']
                queue_text += f"{i}. {artist_name} - {album_name}\n"
            except Exception as e:
                logger.error(f"Error getting album: {e}")
                queue_text += f"{i}. album ID: {item.get('item_id')}\n"
        elif item.get('item_type') == 'bandcamp':
            metadata = item.get('metadata', {})
            url = metadata.get('url', 'unknown')
            queue_text += f"{i}. Bandcamp: {url}\n"
        else:
            queue_text += f"{i}. {item.get('item_type')} ID: {item.get('item_id')}\n"
    
    await message.answer(queue_text)

@dp.message(Command("post"))
async def cmd_post(message: Message):
    # Реализация функции cmd_post
    # ...

@dp.message(Command("check"))
async def cmd_check(message: Message):
    # Реализация функции cmd_check
    # ...

# Обработчик всех остальных сообщений - должен быть последним!
@dp.message()
async def handle_links(message: Message):
    try:
        logger.info(f"Received message: {message.text}")
        
        if not message.text:
            return
        
        # Проверка Spotify
        spotify_match = re.search(r'https://open\.spotify\.com/album/([a-zA-Z0-9]+)', message.text)
        if spotify_match:
            album_id = spotify_match.group(1)
            logger.info(f"Found Spotify album ID: {album_id}")
            
            # Check if already in queue
            already_exists = any(item.get('item_id') == album_id and item.get('item_type') == 'album' for item in posting_queue)
            
            if already_exists:
                await message.answer(f"ℹ️ Album already in queue")
                return
            
            # Validate album exists
            sp = get_spotify_client()
            if sp:
                try:
                    album = sp.album(album_id)
                    artist_name = ', '.join([artist['name'] for artist in album['artists']])
                    album_name = album['name']
                    
                    # Add to queue
                    posting_queue.append({
                        'item_id': album_id,
                        'item_type': 'album',
                        'added_at': datetime.now().isoformat()
                    })
                    
                    # Try to save to database (if table exists)
                    try:
                        supabase.table('post_queue').insert({
                            'item_id': album_id,
                            'item_type': 'album',
                            'added_at': datetime.now().isoformat()
                        }).execute()
                    except Exception as e:
                        logger.error(f"Error saving to database: {e}")
                    
                    await message.answer(f"✅ Added album to queue")
                    return
                except Exception as e:
                    logger.error(f"Error validating album: {e}")
                    await message.answer(f"❌ Error adding album: {str(e)}")
                    return
            else:
                # Add without validation
                posting_queue.append({
                    'item_id': album_id,
                    'item_type': 'album',
                    'added_at': datetime.now().isoformat()
                })
                await message.answer(f"✅ Added album to queue")
                return
        
        # Проверка Bandcamp - более общий паттерн 
        bandcamp_match = re.search(r'https?://[^/]*?bandcamp\.com/album/([^/?#]+)', message.text)
        if bandcamp_match:
            album_slug = bandcamp_match.group(1)
            logger.info(f"Found Bandcamp album: {album_slug}")
            
            item_id = f"bandcamp_{album_slug}"
            
            # Check if already in queue
            already_exists = any(item.get('item_id') == item_id for item in posting_queue)
            
            if already_exists:
                await message.answer(f"ℹ️ Album already in queue")
                return
            
            # Add to queue
            posting_queue.append({
                'item_id': item_id,
                'item_type': 'bandcamp',
                'added_at': datetime.now().isoformat(),
                'metadata': {'url': message.text}
            })
            
            # Try to save to database (if table exists)
            try:
                supabase.table('post_queue').insert({
                    'item_id': item_id,
                    'item_type': 'bandcamp',
                    'added_at': datetime.now().isoformat()
                }).execute()
            except Exception as e:
                logger.error(f"Error saving to database: {e}")
            
            await message.answer(f"✅ Added Bandcamp album to queue")
            return
        
        logger.info("No music link found")
    except Exception as e:
        logger.error(f"Error in message handler: {e}", exc_info=True)
        try:
            await message.answer(f"❌ Ошибка при обработке сообщения: {str(e)}")
        except:
            logger.error("Failed to send error message")

# Функции для корректного завершения
async def on_startup(dispatcher):
    # Удаляем веб-хук перед запуском бота
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot started, webhook deleted")
    
    # Проверяем подключение
    bot_info = await bot.get_me()
    logger.info(f"Connected as @{bot_info.username}")

async def on_shutdown(dispatcher):
    logger.info("Shutting down bot...")
    await bot.session.close()

# Обработчик сигналов
def signal_handler(sig, frame):
    logger.info(f"Received signal {sig}, shutting down...")
    # Будет вызвана функция asyncio.run(on_shutdown(dp)) в main() при выходе
    sys.exit(0)

def register_shutdown_handlers():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

# Функция для обновления статуса бота
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
        
        await asyncio.sleep(30)

# Главная функция
async def main():
    # Глобальная переменная для очереди
    global posting_queue
    posting_queue = []
    
    # Регистрируем обработчики сигналов
    register_shutdown_handlers()
    
    # Пробуем загрузить очередь из базы данных
    try:
        result = supabase.table('post_queue').select('*').eq('posted', False).order('id').execute()
        posting_queue = result.data if result.data else []
        logger.info(f"Loaded {len(posting_queue)} items from queue")
    except Exception as e:
        logger.error(f"Error loading queue: {e}")
        logger.info("Starting with empty queue")
    
    # Запускаем задачу обновления статуса
    asyncio.create_task(update_bot_status())
    
    # Добавляем обработчики запуска и завершения
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    logger.info("Starting bot polling...")
    
    # Запускаем поллинг
    try:
        await dp.start_polling(bot, skip_updates=True)
    except TelegramConflictError as e:
        logger.error(f"Telegram conflict error: {e}")
        # Ждем перед повторной попыткой
        await asyncio.sleep(10)
        
        # Пробуем снова
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot, skip_updates=True)
        except Exception as retry_e:
            logger.error(f"Failed to restart after conflict: {retry_e}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error starting bot: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
