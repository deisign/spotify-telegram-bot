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

# Database initialization
async def init_db():
    """Initialize database tables"""
    logger.info("INIT_DB: Starting database initialization...")
    try:
        # Check bot_status table
        supabase.from_("bot_status").select("*").limit(1).execute()
        logger.info("INIT_DB: bot_status table exists")
    except Exception as e:
        logger.error(f"INIT_DB: Error with bot_status table: {e}")
    
    try:
        # Check followed_artists table
        supabase.from_("followed_artists").select("*").limit(1).execute()
        logger.info("INIT_DB: followed_artists table exists")
    except Exception as e:
        logger.error(f"INIT_DB: Error with followed_artists table: {e}")
    
    try:
        # Check post_queue table
        supabase.from_("post_queue").select("*").limit(1).execute()
        logger.info("INIT_DB: post_queue table exists")
    except Exception as e:
        logger.error(f"INIT_DB: Error with post_queue table: {e}")
    
    logger.info("INIT_DB: Database initialization complete")

# Helper functions
async def get_bot_status(key: str, default=None):
    """Get bot status value"""
    try:
        result = supabase.table('bot_status').select('value').eq('key', key).execute()
        if result.data:
            return result.data[0]['value']
        return default
    except Exception as e:
        logger.error(f"Error getting bot status: {e}")
        return default

async def set_bot_status(key: str, value: str):
    """Set bot status value"""
    try:
        supabase.table('bot_status').upsert({
            'key': key,
            'value': value
        }).execute()
    except Exception as e:
        logger.error(f"Error setting bot status: {e}")

# Global variables
posting_queue: List[Dict] = []
check_task = None
post_task = None

async def load_queue():
    """Load posting queue from database"""
    global posting_queue
    logger.info("LOAD_QUEUE: Starting to load queue from database...")
    try:
        result = supabase.table('post_queue').select('*').eq('posted', False).order('id').execute()
        logger.info(f"LOAD_QUEUE: Database query result: {result}")
        
        posting_queue = result.data if result.data else []
        logger.info(f"LOAD_QUEUE: Loaded {len(posting_queue)} items from queue")
        logger.info(f"LOAD_QUEUE: Queue contents: {posting_queue}")
    except Exception as e:
        logger.error(f"LOAD_QUEUE: Error loading queue: {e}")
        posting_queue = []

async def save_queue():
    """Save queue to database"""
    logger.info("SAVE_QUEUE: Starting to save queue to database...")
    try:
        for i, item in enumerate(posting_queue):
            if 'id' not in item:  # New item
                logger.info(f"SAVE_QUEUE: Saving new item {i}: {item}")
                result = supabase.table('post_queue').insert({
                    'item_id': item['item_id'],
                    'item_type': item['item_type'],
                    'added_at': item.get('added_at', datetime.now().isoformat())
                }).execute()
                logger.info(f"SAVE_QUEUE: Insert result: {result}")
    except Exception as e:
        logger.error(f"SAVE_QUEUE: Error saving queue: {e}")

async def add_to_queue(item_id: str, item_type: str):
    """Add item to queue"""
    global posting_queue
    
    logger.info(f"ADD_TO_QUEUE: Attempting to add {item_type} {item_id}")
    
    # Check if already in queue
    for item in posting_queue:
        if item['item_id'] == item_id and item['item_type'] == item_type:
            logger.info(f"ADD_TO_QUEUE: Item {item_id} already exists in memory queue")
            return False
    
    new_item = {
        'item_id': item_id,
        'item_type': item_type,
        'added_at': datetime.now().isoformat()
    }
    
    logger.info(f"ADD_TO_QUEUE: Adding to memory queue: {new_item}")
    posting_queue.append(new_item)
    
    # Save to database
    try:
        logger.info(f"ADD_TO_QUEUE: Attempting to save to database...")
        result = supabase.table('post_queue').insert({
            'item_id': item_id,
            'item_type': item_type,
            'added_at': new_item['added_at']
        }).execute()
        
        logger.info(f"ADD_TO_QUEUE: Database insert result: {result}")
        logger.info(f"ADD_TO_QUEUE: Successfully added {item_type} {item_id} to queue")
        return True
    except Exception as e:
        logger.error(f"ADD_TO_QUEUE: Error adding to queue: {e}")
        # Remove from memory if database insert failed
        posting_queue.remove(new_item)
        return False

async def remove_from_queue(item_id: str, item_type: str):
    """Remove item from queue"""
    global posting_queue
    
    logger.info(f"REMOVE_FROM_QUEUE: Removing {item_type} {item_id}")
    
    posting_queue = [item for item in posting_queue 
                    if not (item['item_id'] == item_id and item['item_type'] == item_type)]
    
    try:
        # Mark as posted in database
        result = supabase.table('post_queue').update({
            'posted': True,
            'posted_at': datetime.now().isoformat()
        }).eq('item_id', item_id).eq('item_type', item_type).execute()
        
        logger.info(f"REMOVE_FROM_QUEUE: Database update result: {result}")
    except Exception as e:
        logger.error(f"REMOVE_FROM_QUEUE: Error removing from queue: {e}")

# Commands
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """Handle the /start command"""
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
async def cmd_help(message: types.Message):
    """Show help message"""
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

@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    """Manually trigger release check"""
    await message.answer("🔍 Checking for new releases...")
    await check_for_new_releases()
    await message.answer("✅ Check complete!")

@dp.message(Command("queue"))
async def cmd_queue(message: types.Message):
    """Show posting queue"""
    global posting_queue
    
    logger.info(f"CMD_QUEUE: Current queue state: {posting_queue}")
    logger.info(f"CMD_QUEUE: Queue length: {len(posting_queue)}")
    
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    queue_text = "📦 Post Queue:\n\n"
    for i, item in enumerate(posting_queue, 1):
        queue_text += f"{i}. {item['item_type']} ID: {item['item_id']}\n"
    
    await message.answer(queue_text)

@dp.message(Command("queue_clear"))
async def cmd_queue_clear(message: types.Message):
    """Clear the posting queue"""
    global posting_queue
    
    posting_queue = []
    
    try:
        # Delete all unposted items from database
        result = supabase.table('post_queue').update({
            'posted': True,
            'posted_at': datetime.now().isoformat()
        }).eq('posted', False).execute()
        
        logger.info(f"QUEUE_CLEAR: Database update result: {result}")
        await message.answer("🗑️ Queue cleared!")
    except Exception as e:
        logger.error(f"QUEUE_CLEAR: Error clearing queue: {e}")
        await message.answer("❌ Error clearing queue")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Show bot status"""
    
    last_check = await get_bot_status('last_check', 'Never')
    last_post = await get_bot_status('last_post', 'Never')
    days_back = await get_bot_status('release_days_threshold', '3')
    queue_length = len(posting_queue)
    
    status_text = f"""🤖 Bot Status:

Last check: {last_check}
Last post: {last_post}
Queue length: {queue_length}
Checking releases from last {days_back} days"""
    
    await message.answer(status_text)

@dp.message(Command("post"))
async def cmd_post(message: types.Message):
    """Manually post next item in queue"""
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    item = posting_queue[0]
    success = await post_next_item()
    
    if success:
        await message.answer(f"✅ Posted {item['item_type']} {item['item_id']}")
    else:
        await message.answer(f"❌ Failed to post {item['item_type']} {item['item_id']}")

@dp.message(Command("set_days"))
async def cmd_set_days(message: types.Message):
    """Set days back to check for releases"""
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("Usage: /set_days [number]")
            return
        
        days = int(args[1])
        if days < 1 or days > 30:
            await message.answer("Days must be between 1 and 30")
            return
        
        await set_bot_status('release_days_threshold', str(days))
        await message.answer(f"✅ Set release check to {days} days back")
    except ValueError:
        await message.answer("Invalid number. Usage: /set_days [number]")

# Extract Spotify ID from various URL formats
def extract_spotify_id(url: str) -> Optional[str]:
    """Extract Spotify ID from URL"""
    for pattern in SPOTIFY_URL_PATTERNS:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

# Message handler for links
@dp.message(lambda message: message.text and any(re.search(pattern, message.text) for pattern in SPOTIFY_URL_PATTERNS))
async def handle_spotify_link(message: types.Message):
    """Handle Spotify album links"""
    logger.info(f"HANDLE_SPOTIFY_LINK: Received message: {message.text}")
    
    album_id = extract_spotify_id(message.text)
    if album_id:
        logger.info(f"HANDLE_SPOTIFY_LINK: Found Spotify album URL")
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

# Follow artists function
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

# Check for new releases
async def check_for_new_releases():
    """Check for new releases from followed artists"""
    logger.info("Starting to check for new releases...")
    
    followed_artists = await get_followed_artists()
    logger.info(f"Found {len(followed_artists)} followed artists")
    
    if not followed_artists:
        logger.info("No followed artists to check")
        return
    
    # Get release days threshold
    days_back = int(await get_bot_status('release_days_threshold', '3'))
    
    cutoff_date = datetime.now() - timedelta(days=days_back)
    
    # Check followed artists
    for artist_id in followed_artists:
        try:
            # Get artist albums with limit
            albums = sp.artist_albums(artist_id, album_type='album,single', country='US', limit=10)
            
            for album in albums['items']:
                release_date = album['release_date']
                
                # Parse release date
                try:
                    if len(release_date) == 4:  # Year only
                        release_datetime = datetime.strptime(release_date, '%Y')
                    elif len(release_date) == 7:  # Year-month
                        release_datetime = datetime.strptime(release_date, '%Y-%m')
                    else:  # Full date
                        release_datetime = datetime.strptime(release_date, '%Y-%m-%d')
                except ValueError:
                    logger.error(f"Failed to parse release date: {release_date}")
                    continue
                
                # Check if within threshold
                if release_datetime >= cutoff_date:
                    album_id = album['id']
                    logger.info(f"Found recent release: {album['name']} ({release_date})")
                    await add_to_queue(album_id, 'album')
        
        except Exception as e:
            logger.error(f"Error checking artist {artist_id}: {e}")
    
    # Update last check time
    await set_bot_status('last_check', datetime.now().isoformat())

# Post next item from queue
async def post_next_item() -> bool:
    """Post next item from queue to Telegram channel"""
    global posting_queue
    
    if not posting_queue:
        return False
    
    item = posting_queue[0]
    
    try:
        if item['item_type'] == 'album':
            album = sp.album(item['item_id'])
            
            # Create message text
            message_text = f"🎵 New Release Alert!\n\n"
            message_text += f"🎤 Artist: {', '.join([artist['name'] for artist in album['artists']])}\n"
            message_text += f"💿 Album: {album['name']}\n"
            message_text += f"📅 Release Date: {album['release_date']}\n"
            message_text += f"🔢 Tracks: {album['total_tracks']}\n\n"
            message_text += f"🔗 Listen on Spotify: https://open.spotify.com/album/{item['item_id']}"
            
            # Send to channel
            await bot.send_message(CHANNEL_ID, message_text, parse_mode=ParseMode.MARKDOWN_V2)
            
            # Remove from queue
            await remove_from_queue(item['item_id'], item['item_type'])
            
            # Update last post time
            await set_bot_status('last_post', datetime.now().isoformat())
            
            return True
    
    except Exception as e:
        logger.error(f"Error posting item: {e}")
        return False

# Schedule functions
async def schedule_checker():
    """Background task to check for new releases"""
    while True:
        try:
            await check_for_new_releases()
        except Exception as e:
            logger.error(f"Error in schedule_checker: {e}")
        
        # Wait for CHECK_INTERVAL_HOURS or default to 1 hour
        interval_hours = int(os.getenv('CHECK_INTERVAL_HOURS', '1'))
        await asyncio.sleep(interval_hours * 3600)

async def schedule_poster():
    """Background task to post items from queue"""
    while True:
        logger.info(f"SCHEDULE_POSTER: Checking queue. Length: {len(posting_queue)}")
        if posting_queue:
            success = await post_next_item()
            if success:
                logger.info("SCHEDULE_POSTER: Posted next item from queue")
            else:
                logger.error("SCHEDULE_POSTER: Failed to post next item")
        else:
            logger.info("SCHEDULE_POSTER: Queue is empty")
        
        # Wait for defined interval or default to 1 hour
        interval = int(await get_bot_status('post_interval', '3600'))
        await asyncio.sleep(interval)

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
