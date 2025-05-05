import asyncio
import logging
import os
import re
from datetime import datetime

import spotipy
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
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

# Initialize services
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Spotify setup
auth_manager = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="user-follow-read",
    cache_handler=None
)
token_info = auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
sp = spotipy.Spotify(auth=token_info['access_token'])

# Queue
posting_queue = []

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

You can also send Spotify links to add them to the queue."""
    
    await message.answer(help_text)

@dp.message(Command("queue"))
async def cmd_queue(message: Message):
    logger.info("Received /queue command")
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    queue_text = "📦 Post Queue:\n\n"
    for i, item in enumerate(posting_queue, 1):
        if item.get('item_type') == 'album':
            try:
                album = sp.album(item['item_id'])
                artist_name = ', '.join([artist['name'] for artist in album['artists']])
                album_name = album['name']
                queue_text += f"{i}. {artist_name} - {album_name}\n"
            except:
                queue_text += f"{i}. album ID: {item.get('item_id')}\n"
        else:
            queue_text += f"{i}. {item.get('item_type')} ID: {item.get('item_id')}\n"
    
    await message.answer(queue_text)

@dp.message(Command("post"))
async def cmd_post(message: Message):
    logger.info("Received /post command")
    
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    item = posting_queue[0]
    try:
        if item.get('item_type') == 'album':
            album = sp.album(item['item_id'])
            
            # ОРИГИНАЛЬНЫЙ ФОРМАТ ВЫВОДА
            message_text = f"🎵 New Release Alert!\n\n" \
                          f"🎤 Artist: {', '.join([artist['name'] for artist in album['artists']])}\n" \
                          f"💿 Album: {album['name']}\n" \
                          f"📅 Release Date: {album['release_date']}\n" \
                          f"🔢 Tracks: {album['total_tracks']}\n\n" \
                          f"🔗 Listen on Spotify: https://open.spotify.com/album/{item['item_id']}"
            
            # ПОСТИНГ В КАНАЛ
            await bot.send_message(CHANNEL_ID, message_text)
            
            # УДАЛЕНИЕ ИЗ ОЧЕРЕДИ
            posting_queue.pop(0)
            
            # ОБНОВЛЕНИЕ В БАЗЕ (если таблица существует)
            try:
                supabase.table('post_queue').update({
                    'posted': True,
                    'posted_at': datetime.now().isoformat()
                }).eq('item_id', item['item_id']).eq('item_type', 'album').execute()
            except:
                pass  # Игнорируем ошибки базы данных
            
            artist_name = ', '.join([artist['name'] for artist in album['artists']])
            await message.answer(f"✅ Posted album {artist_name} - {album['name']}")
        else:
            await message.answer(f"❌ Unknown item type: {item.get('item_type')}")
            
    except Exception as e:
        logger.error(f"Error in post command: {e}")
        await message.answer(f"❌ Error posting: {str(e)}")

# ЭТОТ ОБРАБОТЧИК ДОЛЖЕН БЫТЬ ПОСЛЕДНИМ
@dp.message()
async def handle_links(message: Message):
    logger.info(f"Received message: {message.text}")
    
    if not message.text:
        return
    
    # Check Spotify
    match = re.search(r'https://open\.spotify\.com/album/([a-zA-Z0-9]+)', message.text)
    if match:
        album_id = match.group(1)
        logger.info(f"Found Spotify album ID: {album_id}")
        
        # Check if already in queue
        already_exists = any(item.get('item_id') == album_id and item.get('item_type') == 'album' for item in posting_queue)
        
        if already_exists:
            await message.answer(f"ℹ️ Album already in queue")
            return
        
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
        except:
            pass  # Ignore database errors
        
        await message.answer(f"✅ Added album to queue")
        return
    
    logger.info("No Spotify link found")

async def main():
    # Try to load queue from database if it exists
    try:
        result = supabase.table('post_queue').select('*').eq('posted', False).order('id').execute()
        global posting_queue
        posting_queue = result.data if result.data else []
        logger.info(f"Loaded {len(posting_queue)} items from queue")
    except:
        logger.info("Starting with empty queue")
    
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
