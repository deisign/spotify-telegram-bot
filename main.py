import os
import asyncio
import aiohttp
import aiogram
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message
import requests
import sqlite3
import json
import logging
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import time
from urllib.parse import urlparse
import re

# Rate limiter class
class RateLimiter:
    def __init__(self, max_calls, period):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        async with self.lock:
            now = time.time()
            # Remove old calls
            self.calls = [call for call in self.calls if now - call < self.period]
            
            if len(self.calls) >= self.max_calls:
                # Wait until the oldest call is outside the period
                sleep_time = self.calls[0] + self.period - now
                if sleep_time > 0:
                    logger.info(f"Rate limit reached. Sleeping for {sleep_time:.2f} seconds...")
                    await asyncio.sleep(sleep_time)
                    return await self.acquire()
            
            self.calls.append(now)
            return True

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Переменные окружения
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REFRESH_TOKEN = os.environ.get('SPOTIFY_REFRESH_TOKEN')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
CHECK_INTERVAL_HOURS = int(os.environ.get('CHECK_INTERVAL_HOURS', 3))
DATABASE_PATH = 'bot_data.db'

# Инициализация бота
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Spotify API
class SpotifyAPI:
    def __init__(self):
        self.access_token = None
        self.token_expires_at = None
        self.rate_limiter = RateLimiter(max_calls=100, period=60)  # 100 calls per minute

    async def get_access_token(self):
        if self.access_token and datetime.now() < self.token_expires_at:
            return self.access_token

        await self.rate_limiter.acquire()
        
        url = "https://accounts.spotify.com/api/token"
        data = {
            "grant_type": "refresh_token",
            "refresh_token": SPOTIFY_REFRESH_TOKEN
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, auth=aiohttp.BasicAuth(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)) as response:
                if response.status == 200:
                    data = await response.json()
                    self.access_token = data['access_token']
                    self.token_expires_at = datetime.now() + timedelta(seconds=data['expires_in'])
                    return self.access_token
                else:
                    logger.error(f"Failed to get Spotify access token: {await response.text()}")
                    return None

    async def get_track_details(self, track_id):
        token = await self.get_access_token()
        if not token:
            return None

        await self.rate_limiter.acquire()
        
        url = f"https://api.spotify.com/v1/tracks/{track_id}"
        headers = {"Authorization": f"Bearer {token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                elif response.status == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                    return await self.get_track_details(track_id)  # Retry
                else:
                    logger.error(f"Failed to get track details: {await response.text()}")
                    return None

    async def get_album_details(self, album_id):
        token = await self.get_access_token()
        if not token:
            return None

        await self.rate_limiter.acquire()
        
        url = f"https://api.spotify.com/v1/albums/{album_id}"
        headers = {"Authorization": f"Bearer {token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    # Get genres from artist
                    artist_id = data['artists'][0]['id']
                    await self.rate_limiter.acquire()
                    artist_url = f"https://api.spotify.com/v1/artists/{artist_id}"
                    async with session.get(artist_url, headers=headers) as artist_response:
                        if artist_response.status == 200:
                            artist_data = await artist_response.json()
                            data['genres'] = artist_data.get('genres', [])
                        elif artist_response.status == 429:
                            retry_after = int(artist_response.headers.get('Retry-After', 60))
                            logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                            await asyncio.sleep(retry_after)
                    return data
                elif response.status == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                    return await self.get_album_details(album_id)  # Retry
                else:
                    logger.error(f"Failed to get album details: {await response.text()}")
                    return None

# Create spotify_api instance
spotify_api = SpotifyAPI()

# Database operations
class Database:
    @staticmethod
    def get_connection():
        conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
        # Register datetime adapter
        sqlite3.register_adapter(datetime, lambda val: val.isoformat())
        sqlite3.register_converter("TIMESTAMP", lambda val: datetime.fromisoformat(val.decode()))
        return conn

    @staticmethod
    def add_to_queue(release_data):
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO post_queue (artist, release, release_date, release_type, tracks_count, genres, image_url, listen_url, platform, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            release_data['artist'],
            release_data['release'],
            release_data['release_date'],
            release_data['release_type'],
            release_data['tracks_count'],
            release_data['genres'],
            release_data['image_url'],
            release_data['listen_url'],
            release_data['platform'],
            datetime.now()
        ))
        conn.commit()
        conn.close()

    @staticmethod
    def get_queue():
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id, artist, release, release_date, release_type, tracks_count, genres, image_url, listen_url, platform FROM post_queue ORDER BY id')
        results = cursor.fetchall()
        conn.close()
        return results

    @staticmethod
    def remove_from_queue(queue_id):
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM post_queue WHERE id = ?', (queue_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def clear_queue():
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM post_queue')
        conn.commit()
        conn.close()

# Commands
@dp.message(CommandStart())
@dp.message(Command("help"))
async def cmd_help(message: Message):
    help_text = """
🤖 Spotify Release Tracker Bot

Available commands:
/help - Show this help message
/check - Check for new releases now
/queue - Show posting queue
/queue_clear - Clear entire queue
/status - Show bot status

You can also send Spotify or Bandcamp links to add them to the queue.
"""
    await message.reply(help_text)

@dp.message(Command("queue"))
async def cmd_queue(message: Message):
    queue = Database.get_queue()
    if not queue:
        await message.reply("📭 Post queue is empty.")
        return

    text = "📋 *Post Queue:*\n\n"
    for idx, item in enumerate(queue, 1):
        text += f"{idx}. {item[1]} - {item[2]} ({item[9]})\n"
    
    await message.reply(text, parse_mode="Markdown")

@dp.message(Command("queue_clear"))
async def cmd_queue_clear(message: Message):
    Database.clear_queue()
    await message.reply("✅ Queue cleared.")

# Handle all messages (including URLs)
@dp.message()
async def handle_message(message: Message):
    text = message.text
    if not text:
        return

    logger.info(f"Received message: {text}")

    # Check if message contains Spotify URL
    if 'spotify.com' in text:
        logger.info("Found Spotify URL")
        url_match = re.search(r'https?://open\.spotify\.com/(album|track)/([a-zA-Z0-9]+)', text)
        if url_match:
            item_type, item_id = url_match.groups()
            logger.info(f"Matched {item_type} with ID: {item_id}")
            
            if item_type == 'album':
                album_data = await spotify_api.get_album_details(item_id)
                if album_data:
                    release_data = {
                        'artist': album_data['artists'][0]['name'],
                        'release': album_data['name'],
                        'release_date': album_data['release_date'],
                        'release_type': album_data['album_type'],
                        'tracks_count': album_data['total_tracks'],
                        'genres': ', '.join([f"#{g}" for g in album_data.get('genres', [])]),
                        'image_url': album_data['images'][0]['url'] if album_data['images'] else '',
                        'listen_url': album_data['external_urls']['spotify'],
                        'platform': 'spotify'
                    }
                    Database.add_to_queue(release_data)
                    await message.reply("✅ Added to posting queue!")
                else:
                    await message.reply("❌ Failed to get album details.")
            elif item_type == 'track':
                track_data = await spotify_api.get_track_details(item_id)
                if track_data:
                    # Get track's album info
                    album_id = track_data['album']['id']
                    album_data = await spotify_api.get_album_details(album_id)
                    if album_data:
                        release_data = {
                            'artist': album_data['artists'][0]['name'],
                            'release': album_data['name'],
                            'release_date': album_data['release_date'],
                            'release_type': album_data['album_type'],
                            'tracks_count': album_data['total_tracks'],
                            'genres': ', '.join([f"#{g}" for g in album_data.get('genres', [])]),
                            'image_url': album_data['images'][0]['url'] if album_data['images'] else '',
                            'listen_url': album_data['external_urls']['spotify'],
                            'platform': 'spotify'
                        }
                        Database.add_to_queue(release_data)
                        await message.reply("✅ Added to posting queue!")
                    else:
                        await message.reply("❌ Failed to get track/album details.")
        else:
            logger.info("No match found for Spotify regex")
            await message.reply("❌ Invalid Spotify link format")

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
    # Register datetime adapter
    sqlite3.register_adapter(datetime, lambda val: val.isoformat())
    sqlite3.register_converter("TIMESTAMP", lambda val: datetime.fromisoformat(val.decode()))
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posted_releases (
            id TEXT PRIMARY KEY,
            artist TEXT,
            release TEXT,
            date_posted TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS post_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist TEXT,
            release TEXT,
            release_date TEXT,
            release_type TEXT,
            tracks_count INTEGER,
            genres TEXT,
            image_url TEXT,
            listen_url TEXT,
            platform TEXT,
            created_at TIMESTAMP,
            scheduled_time TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# Main function
async def main():
    # Initialize database
    init_db()
    
    # Start polling
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
