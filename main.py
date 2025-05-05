import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

import schedule
import spotipy
import aiogram
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bs4 import BeautifulSoup
from spotipy.oauth2 import SpotifyOAuth

# Supabase setup
from supabase import create_client, Client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Railway environment variables
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:8888/callback")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Spotify OAuth
auth_manager = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="user-follow-read",
    cache_handler=None
)

# Set refresh token
if not auth_manager.get_cached_token():
    token_info = auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    auth_manager._token_info = token_info

sp = spotipy.Spotify(auth_manager=auth_manager)

# Telegram Bot configuration
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Supabase configuration
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Constants
SPOTIFY_URL_PATTERNS = [
    r'https://open\.spotify\.com/album/([a-zA-Z0-9]+)',
    r'spotify:album:([a-zA-Z0-9]+)'
]

# ... (keep all previous functions exactly as they are) ...

# Extract Spotify ID from various URL formats
def extract_spotify_id(url: str) -> Optional[str]:
    """Extract Spotify ID from URL"""
    for pattern in SPOTIFY_URL_PATTERNS:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

# FIX: Use more permissive message handler
@dp.message()
async def handle_message(message: types.Message):
    """Handle all messages - check if Spotify link"""
    logger.info(f"HANDLE_MESSAGE: Received message: {message.text}")
    
    if not message.text:
        return
        
    # Check if it's a Spotify URL
    for pattern in SPOTIFY_URL_PATTERNS:
        if re.search(pattern, message.text):
            logger.info(f"HANDLE_SPOTIFY_LINK: Found Spotify URL in message")
            
            album_id = extract_spotify_id(message.text)
            if album_id:
                logger.info(f"HANDLE_SPOTIFY_LINK: Matched album ID: {album_id}")
                
                # Add to queue
                result = await add_to_queue(album_id, 'album')
                logger.info(f"HANDLE_SPOTIFY_LINK: Add to queue result: {result}")
                
                if result:
                    await message.answer(f"✅ Added album to queue")
                else:
                    await message.answer(f"ℹ️ Album already in queue")
            else:
                logger.info("HANDLE_SPOTIFY_LINK: No Spotify album URL found")
                
            # Message handled, return
            return
    
    # If not a Spotify link, log and ignore
    logger.info(f"HANDLE_MESSAGE: Not a Spotify link")

# ... (keep all other functions exactly as they are) ...

# Main function
async def main():
    """Start the bot"""
    # Initialize database
    await init_db()
    
    # Load queue from database
    await load_queue()
    
    # Start background tasks
    check_task = asyncio.create_task(schedule_checker())
    post_task = asyncio.create_task(schedule_poster())
    
    logger.info("MAIN: Starting bot polling...")
    # Start bot
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
