import asyncio
import logging
import os
import re
from datetime import datetime
from typing import List, Dict, Optional

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

# Patterns for Spotify and Bandcamp URLs
SPOTIFY_URL_PATTERNS = [
    r'https://open\.spotify\.com/album/([a-zA-Z0-9]+)',
    r'spotify:album:([a-zA-Z0-9]+)'
]

BANDCAMP_URL_PATTERN = r'https?://(?:(.+)\.)?bandcamp\.com/album/(.+)'

# Queue
posting_queue: List[Dict] = []

async def load_queue():
    global posting_queue
    try:
        result = supabase.table('post_queue').select('*').eq('posted', False).order('id').execute()
        posting_queue = result.data if result.data else []
        logger.info(f"Loaded {len(posting_queue)} items from queue")
    except Exception as e:
        logger.error(f"Error loading queue: {e}")
        posting_queue = []

async def add_to_queue(item_id: str, item_type: str, url: str = None):
    global posting_queue
    
    # Check if already in queue
    for item in posting_queue:
        if item.get('item_id') == item_id and item.get('item_type') == item_type:
            return False
    
    new_item = {
        'item_id': item_id,
        'item_type': item_type,
        'added_at': datetime.now().isoformat(),
        'metadata': {'url': url} if url else None
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

@dp.message(lambda message: message and message.text and (
    any(re.search(pattern, message.text) for pattern in SPOTIFY_URL_PATTERNS) or
    re.search(BANDCAMP_URL_PATTERN, message.text)
))
async def handle_links(message: types.Message):
    """Handle Spotify and Bandcamp links"""
    logger.info(f"Received message: {message.text}")
    
    # Check Spotify
    for pattern in SPOTIFY_URL_PATTERNS:
        match = re.search(pattern, message.text)
        if match:
            album_id = match.group(1)
            logger.info(f"Found Spotify album ID: {album_id}")
            
            result = await add_to_queue(album_id, 'spotify')
            if result:
                await message.answer(f"✅ Added album to queue")
            else:
                await message.answer(f"ℹ️ Album already in queue")
            return
    
    # Check Bandcamp
    match = re.search(BANDCAMP_URL_PATTERN, message.text)
    if match:
        band = match.group(1) or 'unknown'
        album_url = match.group(2)
        item_id = f"{band}_{album_url}"
        logger.info(f"Found Bandcamp album: {item_id}")
        
        result = await add_to_queue(item_id, 'bandcamp', message.text)
        if result:
            await message.answer(f"✅ Added album to queue")
        else:
            await message.answer(f"ℹ️ Album already in queue")
        return
    
    await message.answer("Invalid link")

@dp.message(Command("queue"))
async def cmd_queue(message: types.Message):
    """Show queue"""
    global posting_queue
    
    logger.info(f"Queue command: Current queue length = {len(posting_queue)}")
    
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    queue_text = "📦 Post Queue:\n\n"
    for i, item in enumerate(posting_queue, 1):
        item_id = item.get('item_id', 'unknown')
        item_type = item.get('item_type', 'unknown')
        queue_text += f"{i}. {item_type} ID: {item_id}\n"
    
    await message.answer(queue_text)

@dp.message(Command("post"))
async def cmd_post(message: types.Message):
    """Post from queue"""
    global posting_queue
    
    logger.info(f"Post command: Queue length = {len(posting_queue)}")
    
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    item = posting_queue[0]
    try:
        if item.get('item_type') == 'spotify':
            album = sp.album(item['item_id'])
            
            # ОРИГИНАЛЬНЫЙ ФОРМАТ ВЫВОДА:
            message_text = f"🎵 New Release Alert!\n\n" \
                          f"🎤 Artist: {', '.join([artist['name'] for artist in album['artists']])}\n" \
                          f"💿 Album: {album['name']}\n" \
                          f"📅 Release Date: {album['release_date']}\n" \
                          f"🔢 Tracks: {album['total_tracks']}\n\n" \
                          f"🔗 Listen on Spotify: https://open.spotify.com/album/{item['item_id']}"
            
            await bot.send_message(CHANNEL_ID, message_text)
            
        elif item.get('item_type') == 'bandcamp':
            # Bandcamp format
            metadata = item.get('metadata', {})
            url = metadata.get('url', 'unknown')
            
            message_text = f"🎵 New Release Alert!\n\n" \
                          f"🎤 Bandcamp Release\n" \
                          f"💿 Album ID: {item['item_id']}\n\n" \
                          f"🔗 Listen on Bandcamp: {url}"
            
            await bot.send_message(CHANNEL_ID, message_text)
        
        await remove_from_queue(item['item_id'], item['item_type'])
        await message.answer(f"✅ Posted {item['item_type']} {item['item_id']}")
        
    except Exception as e:
        logger.error(f"Error in post command: {e}", exc_info=True)
        await message.answer(f"❌ Error posting: {str(e)}")

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    """Clear queue"""
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

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Show help"""
    help_text = """🎵 Spotify Release Tracker Bot

Available commands:
/help - Show this help message
/queue - Show posting queue
/post - Post next item in queue manually
/clear - Clear posting queue

You can also send Spotify or Bandcamp links to add them to the queue."""
    
    await message.answer(help_text)

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Show status"""
    status_text = f"""🤖 Bot Status:

Queue length: {len(posting_queue)}"""
    
    await message.answer(status_text)

async def schedule_poster():
    """Background task to post items from queue"""
    while True:
        if posting_queue:
            item = posting_queue[0]
            try:
                if item.get('item_type') == 'spotify':
                    album = sp.album(item['item_id'])
                    
                    message_text = f"🎵 New Release Alert!\n\n" \
                                  f"🎤 Artist: {', '.join([artist['name'] for artist in album['artists']])}\n" \
                                  f"💿 Album: {album['name']}\n" \
                                  f"📅 Release Date: {album['release_date']}\n" \
                                  f"🔢 Tracks: {album['total_tracks']}\n\n" \
                                  f"🔗 Listen on Spotify: https://open.spotify.com/album/{item['item_id']}"
                    
                    await bot.send_message(CHANNEL_ID, message_text)
                    
                elif item.get('item_type') == 'bandcamp':
                    metadata = item.get('metadata', {})
                    url = metadata.get('url', 'unknown')
                    
                    message_text = f"🎵 New Release Alert!\n\n" \
                                  f"🎤 Bandcamp Release\n" \
                                  f"💿 Album ID: {item['item_id']}\n\n" \
                                  f"🔗 Listen on Bandcamp: {url}"
                    
                    await bot.send_message(CHANNEL_ID, message_text)
                
                await remove_from_queue(item['item_id'], item['item_type'])
                logger.info(f"Posted {item['item_type']} {item['item_id']}")
                
            except Exception as e:
                logger.error(f"Error posting: {e}")
        
        # Wait for interval
        await asyncio.sleep(3600)  # 1 hour

async def main():
    logger.info("Starting bot initialization...")
    
    await load_queue()
    logger.info(f"Queue loaded. Length: {len(posting_queue)}")
    
    # Start background tasks
    post_task = asyncio.create_task(schedule_poster())
    
    logger.info("Starting bot polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
