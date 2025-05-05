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

# Spotify setup with error handling
try:
    auth_manager = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope="user-follow-read",
        cache_handler=None
    )
    token_info = auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    sp = spotipy.Spotify(auth=token_info['access_token'])
except Exception as e:
    logger.error(f"Failed to initialize Spotify: {e}")
    sp = None

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
        queue_text += f"{i}. {item.get('item_type')} ID: {item.get('item_id')}\n"
    
    await message.answer(queue_text)

@dp.message(Command("post"))
async def cmd_post(message: Message):
    logger.info("Received /post command")  
    await message.answer("/post command received")

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
        
        # Add to queue (simplified)
        posting_queue.append({
            'item_id': album_id,
            'item_type': 'album',
            'added_at': datetime.now().isoformat()
        })
        
        await message.answer(f"✅ Added album to queue")
        return
    
    logger.info("No Spotify link found")

async def main():
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
