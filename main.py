import asyncio
import logging
import os
import re
from datetime import datetime

import spotipy
from aiogram import Bot, Dispatcher, types
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

@dp.message()
async def handle_message(message: types.Message):
    if not message.text:
        return
    
    logger.info(f"Received: {message.text}")
    
    # Handle /queue command
    if message.text == "/queue":
        if not posting_queue:
            await message.answer("📭 Post queue is empty.")
            return
        
        queue_text = "📦 Post Queue:\n\n"
        for i, item in enumerate(posting_queue, 1):
            item_id = item.get('item_id', 'unknown')
            
            try:
                album = sp.album(item_id)
                artist_name = ', '.join([artist['name'] for artist in album['artists']])
                album_name = album['name']
                queue_text += f"{i}. {artist_name} - {album_name}\n"
            except:
                queue_text += f"{i}. album ID: {item_id}\n"
        
        await message.answer(queue_text)
        return
    
    # Handle /post command
    if message.text == "/post":
        if not posting_queue:
            await message.answer("📭 Post queue is empty.")
            return
        
        item = posting_queue[0]
        try:
            album = sp.album(item['item_id'])
            
            message_text = f"🎵 New Release Alert!\n\n" \
                          f"🎤 Artist: {', '.join([artist['name'] for artist in album['artists']])}\n" \
                          f"💿 Album: {album['name']}\n" \
                          f"📅 Release Date: {album['release_date']}\n" \
                          f"🔢 Tracks: {album['total_tracks']}\n\n" \
                          f"🔗 Listen on Spotify: https://open.spotify.com/album/{item['item_id']}"
            
            await bot.send_message(CHANNEL_ID, message_text, parse_mode=None)
            
            # Remove from queue
            posting_queue.pop(0)
            
            # Update database
            supabase.table('post_queue').update({
                'posted': True,
                'posted_at': datetime.now().isoformat()
            }).eq('item_id', item['item_id']).eq('item_type', 'album').execute()
            
            artist_name = ', '.join([artist['name'] for artist in album['artists']])
            await message.answer(f"✅ Posted album {artist_name} - {album['name']}")
        except Exception as e:
            logger.error(f"Error posting: {e}")
            await message.answer(f"❌ Error posting: {e}")
        return
    
    # Handle Spotify URLs
    spotify_match = re.search(r'https://open\.spotify\.com/album/([a-zA-Z0-9]+)', message.text)
    if spotify_match:
        album_id = spotify_match.group(1)
        logger.info(f"Found album ID: {album_id}")
        
        # Check if already in queue
        already_exists = any(item.get('item_id') == album_id for item in posting_queue)
        
        if already_exists:
            await message.answer(f"ℹ️ Album already in queue")
            return
        
        new_item = {
            'item_id': album_id,
            'item_type': 'album',
            'added_at': datetime.now().isoformat()
        }
        
        posting_queue.append(new_item)
        
        try:
            supabase.table('post_queue').insert({
                'item_id': album_id,
                'item_type': 'album',
                'added_at': new_item['added_at']
            }).execute()
            await message.answer(f"✅ Added album to queue")
        except Exception as e:
            posting_queue.remove(new_item)
            await message.answer(f"ℹ️ Album already in queue")
        return

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
        await message.answer("❌ Error clearing queue")

async def main():
    await load_queue()
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
