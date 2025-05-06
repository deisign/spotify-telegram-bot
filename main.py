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
/check - Check for new releases

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
        if item.get('item_type') == 'album' and sp:
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
        if item.get('item_type') == 'album' and sp:
            album = sp.album(item['item_id'])
            
            # ОРИГИНАЛЬНЫЙ ФОРМАТ ВЫВОДА
            message_text = f"🎵 New Release Alert!\n\n" \
                          f"🎤 Artist: {', '.join([artist['name'] for artist in album['artists']])}\n" \
                          f"💿 Album: {album['name']}\n" \
                          f"📅 Release Date: {album['release_date']}\n" \
                          f"🔢 Tracks: {album['total_tracks']}\n\n" \
                          f"🔗 Listen on Spotify: https://open.spotify.com/album/{item['item_id']}"
            
            # ПОСТИНГ В КАНАЛ
            if CHANNEL_ID:
                await bot.send_message(CHANNEL_ID, message_text)
                logger.info(f"Posted to channel {CHANNEL_ID}")
            else:
                logger.error("CHANNEL_ID not set")
                await message.answer("CHANNEL_ID not configured")
                return
            
            # УДАЛЕНИЕ ИЗ ОЧЕРЕДИ
            posting_queue.pop(0)
            
            # ОБНОВЛЕНИЕ В БАЗЕ (если таблица существует)
            try:
                supabase.table('post_queue').update({
                    'posted': True,
                    'posted_at': datetime.now().isoformat()
                }).eq('item_id', item['item_id']).eq('item_type', 'album').execute()
            except Exception as e:
                logger.error(f"Error updating database: {e}")
            
            artist_name = ', '.join([artist['name'] for artist in album['artists']])
            await message.answer(f"✅ Posted album {artist_name} - {album['name']}")
        else:
            await message.answer(f"❌ Unknown item type or Spotify not initialized")
            
    except Exception as e:
        logger.error(f"Error in post command: {e}")
        await message.answer(f"❌ Error posting: {str(e)}")

@dp.message(Command("check"))
async def cmd_check(message: Message):
    logger.info("Received /check command")
    await message.answer("🔍 Checking for new releases...")
    
    if not sp:
        await message.answer("❌ Spotify not initialized")
        return
    
    try:
        # Get followed artists
        results = sp.current_user_followed_artists(limit=10)
        artists = results['artists']['items']
        
        if not artists:
            await message.answer("No followed artists found")
            return
        
        # Check for new releases
        new_releases = []
        for artist in artists:
            albums = sp.artist_albums(artist['id'], album_type='album,single', limit=5)
            for album in albums['items']:
                release_date = album['release_date']
                if '-' in release_date:  # Has at least month
                    try:
                        release_datetime = datetime.strptime(release_date, '%Y-%m-%d')
                    except:
                        try:
                            release_datetime = datetime.strptime(release_date, '%Y-%m')
                        except:
                            continue
                    
                    # Check if recent (within 3 days)
                    if (datetime.now() - release_datetime).days <= 3:
                        new_releases.append({
                            'artist': artist['name'],
                            'album': album['name'],
                            'id': album['id']
                        })
        
        if new_releases:
            result_text = f"Found {len(new_releases)} recent releases:\n\n"
            for rel in new_releases:
                result_text += f"• {rel['artist']} - {rel['album']}\n"
        else:
            result_text = "No recent releases found"
        
        await message.answer(result_text)
        
    except Exception as e:
        logger.error(f"Error checking releases: {e}")
        await message.answer(f"❌ Error: {str(e)}")

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
        except Exception as e:
            logger.error(f"Error saving to database: {e}")
        
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
    except Exception as e:
        logger.error(f"Error loading queue: {e}")
        logger.info("Starting with empty queue")
    
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
