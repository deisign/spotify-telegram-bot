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

# Railway environment variables - NO load_dotenv() needed
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# Check if required variables are set
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not found in environment variables!")
if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
    raise ValueError("Spotify credentials not found in environment variables!")
if not SPOTIFY_REFRESH_TOKEN:
    raise ValueError("SPOTIFY_REFRESH_TOKEN not found in environment variables!")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Supabase credentials not found in environment variables!")
if not CHANNEL_ID:
    raise ValueError("TELEGRAM_CHANNEL_ID not found in environment variables!")

# Spotify OAuth - для доступа к подпискам пользователя
auth_manager = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope="user-follow-read",  # Нужно для получения подписок
    cache_handler=None  # В памяти
)

# Устанавливаем refresh token
auth_manager.get_cached_token()
# Если токен отсутствует, нужно использовать refresh token
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

# Rest of the code remains the same...
# [Continue with all functions from the previous version]

# Important: Modified get_followed_artists function to fetch from Spotify:
async def get_followed_artists() -> Set[str]:
    """Get list of followed artists from Spotify API and sync with database"""
    try:
        # Get from Spotify
        results = sp.current_user_followed_artists(limit=50)
        
        followed_ids = set()
        
        # Process all pages of followed artists
        while results:
            for artist in results['artists']['items']:
                followed_ids.add(artist['id'])
                
                # Update in database
                supabase.table('followed_artists').upsert({
                    'id': artist['id'],
                    'name': artist['name'],
                    'last_release_date': None,
                    'created_at': datetime.now().isoformat()
                }).execute()
            
            # Get next page
            if results['artists']['next']:
                results = sp.next(results['artists'])
            else:
                break
        
        logger.info(f"Synced {len(followed_ids)} followed artists from Spotify")
        return followed_ids
        
    except Exception as e:
        logger.error(f"Error getting followed artists from Spotify: {e}")
        # Fallback to database if Spotify fails
        try:
            result = supabase.table('followed_artists').select('id').execute()
            return {artist['id'] for artist in result.data} if result.data else set()
        except Exception as db_error:
            logger.error(f"Error getting followed artists from database: {db_error}")
            return set()

# Update check_for_new_releases to sync artists first:
async def check_for_new_releases():
    """Check for new releases from followed artists"""
    logger.info("Starting to check for new releases...")
    
    # First sync followed artists from Spotify
    followed_artists = await get_followed_artists()
    logger.info(f"Found {len(followed_artists)} followed artists")
    
    # ... rest of the function remains the same

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
