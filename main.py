import asyncio
import logging
import os
import re
from datetime import datetime, timedelta

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

# Проверка на None
if not all([BOT_TOKEN, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    raise ValueError("Missing required environment variables")

# Initialize services
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Spotify setup with error handling
try:
    auth_manager = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope="user-follow-read",
        cache_handler=None
    )
    
    # Set the refresh token directly
    token_info = auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    sp = spotipy.Spotify(auth=token_info['access_token'])
except Exception as e:
    logger.error(f"Failed to initialize Spotify: {e}")
    sp = None

# Patterns for Spotify and Bandcamp URLs
SPOTIFY_URL_PATTERNS = [
    r'https://open\.spotify\.com/album/([a-zA-Z0-9]+)',
    r'spotify:album:([a-zA-Z0-9]+)'
]
BANDCAMP_URL_PATTERN = r'https?://(?:(.+)\.)?bandcamp\.com/album/(.+)'

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
        # Remove from memory if database insert failed
        try:
            posting_queue.remove(new_item)
        except:
            pass
        return False

@dp.message(Command("start"))
async def cmd_start(message: Message):
    welcome_text = """🎵 Spotify Release Tracker Bot

Available commands:
/help - Show this help message
/check - Check for new releases now
/queue - Show posting queue
/queue_clear - Clear entire queue
/status - Show bot status
/post - Post next item in queue manually
/set_days [number] - Set days back to check (default: 3)

You can also send Spotify or Bandcamp links to add them to the queue."""
    
    await message.answer(welcome_text)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    help_text = """🎵 Spotify Release Tracker Bot

Available commands:
/help - Show this help message
/check - Check for new releases now
/queue - Show posting queue  
/queue_clear - Clear entire queue
/status - Show bot status
/post - Post next item in queue manually
/set_days [number] - Set days back to check (default: 3)

You can also send Spotify or Bandcamp links to add them to the queue."""
    
    await message.answer(help_text)

@dp.message(Command("queue"))
async def cmd_queue(message: Message):
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    queue_text = "📦 Post Queue:\n\n"
    for i, item in enumerate(posting_queue, 1):
        item_id = item.get('item_id', 'unknown')
        item_type = item.get('item_type', 'unknown')
        
        if item_type == 'album' and sp:
            try:
                album = sp.album(item_id)
                artist_name = ', '.join([artist['name'] for artist in album['artists']])
                album_name = album['name']
                queue_text += f"{i}. {artist_name} - {album_name}\n"
            except:
                queue_text += f"{i}. {item_type} ID: {item_id}\n"
        elif item_type == 'bandcamp':
            metadata = item.get('metadata', {})
            url = metadata.get('url', 'unknown')
            queue_text += f"{i}. bandcamp ID: {item_id} - {url}\n"
        else:
            queue_text += f"{i}. {item_type} ID: {item_id}\n"
    
    await message.answer(queue_text)

@dp.message(Command("post"))
async def cmd_post(message: Message):
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    if not CHANNEL_ID:
        await message.answer("❌ CHANNEL_ID not configured")
        return
    
    item = posting_queue[0]
    try:
        if item.get('item_type') == 'album' and sp:
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
        
        # Remove from queue
        posting_queue.pop(0)
        
        # Mark as posted in database
        try:
            supabase.table('post_queue').update({
                'posted': True,
                'posted_at': datetime.now().isoformat()
            }).eq('item_id', item['item_id']).eq('item_type', item['item_type']).execute()
        except Exception as e:
            logger.error(f"Error updating database: {e}")
        
        await message.answer(f"✅ Posted {item['item_type']} {item['item_id']}")
        
    except Exception as e:
        logger.error(f"Error in post command: {e}")
        await message.answer(f"❌ Error posting: {str(e)}")

@dp.message()
async def handle_links(message: Message):
    if not message.text:
        return
    
    # Check Spotify
    for pattern in SPOTIFY_URL_PATTERNS:
        match = re.search(pattern, message.text)
        if match:
            album_id = match.group(1)
            result = await add_to_queue(album_id, 'album')
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
        result = await add_to_queue(item_id, 'bandcamp', message.text)
        if result:
            await message.answer(f"✅ Added album to queue")
        else:
            await message.answer(f"ℹ️ Album already in queue")
        return

async def main():
    # Initialize database and load queue
    await load_queue()
    
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
