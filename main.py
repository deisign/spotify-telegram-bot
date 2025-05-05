import asyncio
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

import spotipy
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
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

if not auth_manager.get_cached_token():
    token_info = auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    auth_manager._token_info = token_info

sp = spotipy.Spotify(auth_manager=auth_manager)

# Patterns for Spotify URLs
SPOTIFY_URL_PATTERNS = [
    r'https://open\.spotify\.com/album/([a-zA-Z0-9]+)',
    r'spotify:album:([a-zA-Z0-9]+)'
]

# Queue
posting_queue = []

async def load_queue():
    global posting_queue
    try:
        result = supabase.table('post_queue').select('*').eq('posted', False).order('id').execute()
        posting_queue = result.data if result.data else []
        logger.info(f"Loaded {len(posting_queue)} items from queue")
    except Exception as e:
        logger.error(f"Error loading queue: {e}")
        posting_queue = []

async def add_to_queue(item_id: str, item_type: str):
    global posting_queue
    
    # Check if already in queue
    for item in posting_queue:
        if item.get('item_id') == item_id and item.get('item_type') == item_type:
            return False
    
    new_item = {
        'item_id': item_id,
        'item_type': item_type,
        'added_at': datetime.now().isoformat()
    }
    
    posting_queue.append(new_item)
    
    try:
        supabase.table('post_queue').insert({
            'item_id': item_id,
            'item_type': item_type,
            'added_at': new_item['added_at']
        }).execute()
        logger.info(f"Added {item_type} {item_id} to queue")
        return True
    except Exception as e:
        logger.error(f"Error adding to queue: {e}")
        posting_queue.remove(new_item)
        return False

async def remove_from_queue(item_id: str, item_type: str):
    global posting_queue
    posting_queue = [item for item in posting_queue 
                    if not (item.get('item_id') == item_id and item.get('item_type') == item_type)]
    
    try:
        supabase.table('post_queue').update({
            'posted': True,
            'posted_at': datetime.now().isoformat()
        }).eq('item_id', item_id).eq('item_type', item_type).execute()
    except Exception as e:
        logger.error(f"Error removing from queue: {e}")

@dp.message(lambda message: message.text and any(re.search(pattern, message.text) for pattern in SPOTIFY_URL_PATTERNS))
async def handle_spotify_link(message: types.Message):
    """Handle Spotify album links"""
    logger.info(f"Received Spotify link: {message.text}")
    
    for pattern in SPOTIFY_URL_PATTERNS:
        match = re.search(pattern, message.text)
        if match:
            album_id = match.group(1)
            logger.info(f"Found album ID: {album_id}")
            
            result = await add_to_queue(album_id, 'album')
            if result:
                await message.answer(f"✅ Added album to queue")
            else:
                await message.answer(f"ℹ️ Album already in queue")
            return
    
    await message.answer("Invalid Spotify link")

@dp.message(Command("queue"))
async def cmd_queue(message: types.Message):
    global posting_queue
    
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    queue_text = "📦 Post Queue:\n\n"
    for i, item in enumerate(posting_queue, 1):
        queue_text += f"{i}. {item.get('item_type')} ID: {item.get('item_id')}\n"
    
    await message.answer(queue_text)

@dp.message(Command("post"))
async def cmd_post(message: types.Message):
    global posting_queue
    
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    item = posting_queue[0]
    try:
        if item.get('item_type') == 'album':
            album = sp.album(item['item_id'])
            
            message_text = f"🎵 {album['name']}\n" \
                          f"🎤 {', '.join([artist['name'] for artist in album['artists']])}\n" \
                          f"📅 {album['release_date']}\n" \
                          f"🔗 https://open.spotify.com/album/{item['item_id']}"
            
            await bot.send_message(CHANNEL_ID, message_text)
            
            await remove_from_queue(item['item_id'], item['item_type'])
            
            await message.answer(f"✅ Posted {item['item_type']} {item['item_id']}")
    except Exception as e:
        logger.error(f"Error posting: {e}")
        await message.answer(f"❌ Error posting: {e}")

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    global posting_queue
    posting_queue = []
    try:
        supabase.table('post_queue').update({
            'posted': True,
            'posted_at': datetime.now().isoformat()
        }).eq('posted', False).execute()
        await message.answer("🗑️ Queue cleared!")
    except Exception as e:
        logger.error(f"Error clearing queue: {e}")
        await message.answer("❌ Error clearing queue")

async def main():
    await load_queue()
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
