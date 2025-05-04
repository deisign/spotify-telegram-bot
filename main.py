import os
import asyncio
import aiohttp
import aiogram
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile
import requests
import sqlite3
import json
import logging
from datetime import datetime, timedelta
import schedule
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bot_status (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    conn.commit()
    conn.close()

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

    async def get_followed_artists(self):
        token = await self.get_access_token()
        if not token:
            return []

        artists = []
        url = "https://api.spotify.com/v1/me/following?type=artist&limit=50"
        headers = {"Authorization": f"Bearer {token}"}

        async with aiohttp.ClientSession() as session:
            while url:
                await self.rate_limiter.acquire()
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        artists.extend(data['artists']['items'])
                        url = data['artists']['next']
                    elif response.status == 429:
                        # Handle rate limit specifically
                        retry_after = int(response.headers.get('Retry-After', 60))
                        logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                        await asyncio.sleep(retry_after)
                        continue
                    else:
                        logger.error(f"Failed to get followed artists: {await response.text()}")
                        break

        return artists

    async def get_artist_new_releases(self, artist_id):
        token = await self.get_access_token()
        if not token:
            return []

        await self.rate_limiter.acquire()
        
        url = f"https://api.spotify.com/v1/artists/{artist_id}/albums?include_groups=album,single&market=US&limit=5"
        headers = {"Authorization": f"Bearer {token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['items']
                elif response.status == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                    return await self.get_artist_new_releases(artist_id)  # Retry
                else:
                    logger.error(f"Failed to get artist releases: {await response.text()}")
                    return []

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

spotify_api = SpotifyAPI()

# Bandcamp web scraping
class BandcampScraper:
    @staticmethod
    async def get_release_info(url):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        html = await response.text()
                        soup = BeautifulSoup(html, 'html.parser')
                        
                        # Extract release info
                        title_elem = soup.find('h2', class_='trackTitle')
                        artist_elem = soup.find('span', itemprop='byArtist')
                        release_info = {
                            'artist': artist_elem.text.strip() if artist_elem else '',
                            'title': title_elem.text.strip() if title_elem else '',
                            'release_date': '',
                            'tracks': [],
                            'genres': [],
                            'image_url': '',
                            'url': url
                        }
                        
                        # Get cover art
                        image_elem = soup.find('div', class_='popupImage')
                        if image_elem and image_elem.find('img'):
                            release_info['image_url'] = image_elem.find('img')['src']
                        
                        # Get track list
                        tracks = soup.find_all('div', class_='track_row_view')
                        release_info['tracks'] = [track.find('span', class_='track-title').text.strip() for track in tracks if track.find('span', class_='track-title')]
                        
                        # Get genres from tags
                        tag_elements = soup.find_all('a', class_='tag')
                        release_info['genres'] = [tag.text.strip() for tag in tag_elements[:3]]
                        
                        # Get release date
                        album_info = soup.find('div', class_='albumInfo')
                        if album_info:
                            date_text = album_info.text
                            # Parse date from text like "released April 1, 2023"
                            import re
                            date_match = re.search(r'released\s+(\w+\s+\d+,\s+\d+)', date_text)
                            if date_match:
                                release_info['release_date'] = date_match.group(1)
                        
                        return release_info
                    else:
                        logger.error(f"Failed to fetch Bandcamp page: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error scraping Bandcamp: {str(e)}")
            return None

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
        cursor.execute('SELECT id, artist, release, release_date, release_type, tracks_count, genres, listen_url, platform FROM post_queue ORDER BY id')
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

    @staticmethod
    def mark_as_posted(release_id, artist, release):
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO posted_releases (id, artist, release, date_posted)
            VALUES (?, ?, ?, ?)
        ''', (release_id, artist, release, datetime.now()))
        conn.commit()
        conn.close()

    @staticmethod
    def is_posted(release_id):
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM posted_releases WHERE id = ?', (release_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None

    @staticmethod
    def update_status(key, value):
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO bot_status (key, value) VALUES (?, ?)', (key, value))
        conn.commit()
        conn.close()

    @staticmethod
    def get_status(key):
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM bot_status WHERE key = ?', (key,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None

# Format post
def format_post(release_data):
    text = f"*{release_data['artist']}*\n"
    text += f"*{release_data['release']}*\n"
    text += f"{release_data['release_date']}, {release_data['release_type']}, {release_data['tracks_count']} tracks\n"
    text += f"Genre: {release_data['genres']}\n"
    
    listen_emoji = "🎧"
    if release_data['platform'] == 'spotify':
        text += f"{listen_emoji} Listen on Spotify {release_data['listen_url']}"
    else:
        text += f"{listen_emoji} Listen on Bandcamp {release_data['listen_url']}"
    
    return text

# Bot commands
@dp.message(Command("start", "help"))
async def cmd_help(message: types.Message):
    help_text = """
🤖 Spotify Release Tracker Bot

Available commands:
/help - Show this help message
/check - Check for new releases now
/queue - Show posting queue
/queue_remove [number] - Remove item from queue
/queue_clear - Clear entire queue
/status - Show bot status

You can also send Spotify or Bandcamp links to add them to the queue.
"""
    await message.reply(help_text)

@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    await message.reply("🔍 Checking for new releases...")
    try:
        await check_new_releases()
        await message.reply("✅ Check completed!")
    except Exception as e:
        logger.error(f"Error checking releases: {str(e)}")
        await message.reply("❌ Error checking releases. Check logs for details.")

@dp.message(Command("queue"))
async def cmd_queue(message: types.Message):
    queue = Database.get_queue()
    if not queue:
        await message.reply("📭 Post queue is empty.")
        return

    text = "📋 *Post Queue:*\n\n"
    for idx, item in enumerate(queue, 1):
        text += f"{idx}. {item[1]} - {item[2]} ({item[8]})\n"
    
    await message.reply(text, parse_mode="Markdown")

@dp.message(Command("queue_remove"))
async def cmd_queue_remove(message: types.Message):
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.reply("Usage: /queue_remove [number]")
            return

        index = int(args[1]) - 1
        queue = Database.get_queue()
        
        if index < 0 or index >= len(queue):
            await message.reply("Invalid queue number.")
            return

        queue_id = queue[index][0]
        Database.remove_from_queue(queue_id)
        await message.reply(f"✅ Removed item {args[1]} from queue.")
    except ValueError:
        await message.reply("Invalid number format.")
    except Exception as e:
        logger.error(f"Error removing from queue: {str(e)}")
        await message.reply("❌ Error removing from queue.")

@dp.message(Command("queue_clear"))
async def cmd_queue_clear(message: types.Message):
    Database.clear_queue()
    await message.reply("✅ Queue cleared.")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    last_check = Database.get_status('last_check')
    last_post = Database.get_status('last_post')
    
    status_text = "🤖 *Bot Status:*\n\n"
    status_text += f"Last check: {last_check if last_check else 'Never'}\n"
    status_text += f"Last post: {last_post if last_post else 'Never'}\n"
    status_text += f"Queue length: {len(Database.get_queue())}\n"
    
    await message.reply(status_text, parse_mode="Markdown")

# Handle URLs
@dp.message()
async def handle_url(message: types.Message):
    text = message.text
    if not text:
        return

    # Check if message contains Spotify URL
    if 'spotify.com' in text:
        url_match = re.search(r'https?://open\.spotify\.com/(album|track)/([a-zA-Z0-9]+)', text)
        if url_match:
            item_type, item_id = url_match.groups()
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

    # Check if message contains Bandcamp URL
    elif 'bandcamp.com' in text:
        url_match = re.search(r'https?://[a-zA-Z0-9.-]+\.bandcamp\.com/album/[a-zA-Z0-9-]+', text)
        if url_match:
            url = url_match.group(0)
            release_info = await BandcampScraper.get_release_info(url)
            if release_info:
                release_data = {
                    'artist': release_info['artist'],
                    'release': release_info['title'],
                    'release_date': release_info['release_date'],
                    'release_type': 'album' if len(release_info['tracks']) > 1 else 'single',
                    'tracks_count': len(release_info['tracks']),
                    'genres': ', '.join([f"#{g}" for g in release_info['genres']]),
                    'image_url': release_info['image_url'],
                    'listen_url': url,
                    'platform': 'bandcamp'
                }
                Database.add_to_queue(release_data)
                await message.reply("✅ Added to posting queue!")
            else:
                await message.reply("❌ Failed to get Bandcamp release details.")

# Check new releases
async def check_new_releases():
    logger.info("Starting to check for new releases...")
    Database.update_status('last_check', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    artists = await spotify_api.get_followed_artists()
    logger.info(f"Found {len(artists)} followed artists")
    
    new_releases_count = 0
    
    for artist in artists:
        releases = await spotify_api.get_artist_new_releases(artist['id'])
        
        for release in releases:
            release_id = release['id']
            
            if not Database.is_posted(release_id):
                album_data = await spotify_api.get_album_details(release_id)
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
                    new_releases_count += 1
                    logger.info(f"Added {album_data['artists'][0]['name']} - {album_data['name']} to queue")
    
    logger.info(f"Check completed. Added {new_releases_count} new releases to queue.")

# Post from queue
async def post_from_queue():
    queue = Database.get_queue()
    if not queue:
        return

    item = queue[0]
    queue_id, artist, release, release_date, release_type, tracks_count, genres, image_url, listen_url, platform = item
    
    release_data = {
        'artist': artist,
        'release': release,
        'release_date': release_date,
        'release_type': release_type,
        'tracks_count': tracks_count,
        'genres': genres,
        'image_url': image_url,
        'listen_url': listen_url,
        'platform': platform
    }
    
    try:
        # Download image
        if image_url:
            async with aiohttp.ClientSession() as session:
                async with session.get(image_url) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        image_path = f"temp_image_{queue_id}.jpg"
                        with open(image_path, 'wb') as f:
                            f.write(image_data)
                        
                        # Send post with image
                        photo = FSInputFile(image_path)
                        await bot.send_photo(
                            chat_id=TELEGRAM_CHANNEL_ID,
                            photo=photo,
                            caption=format_post(release_data),
                            parse_mode="Markdown"
                        )
                        
                        # Clean up
                        os.remove(image_path)
                    else:
                        # Send without image if download fails
                        await bot.send_message(
                            chat_id=TELEGRAM_CHANNEL_ID,
                            text=format_post(release_data),
                            parse_mode="Markdown"
                        )
        else:
            # Send without image
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=format_post(release_data),
                parse_mode="Markdown"
            )
        
        Database.remove_from_queue(queue_id)
        Database.mark_as_posted(f"{platform}_{queue_id}", artist, release)
        Database.update_status('last_post', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        logger.info(f"Posted {artist} - {release}")
        
    except Exception as e:
        logger.error(f"Error posting: {str(e)}")

# Main function
async def main():
    # Initialize database
    init_db()
    
    # Start periodic tasks
    async def periodic_check():
        while True:
            try:
                await check_new_releases()
            except Exception as e:
                logger.error(f"Error in periodic check: {str(e)}")
            await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)
    
    async def periodic_post():
        while True:
            try:
                await post_from_queue()
            except Exception as e:
                logger.error(f"Error in periodic post: {str(e)}")
            await asyncio.sleep(3600)  # Post every hour
    
    # Start tasks
    check_task = asyncio.create_task(periodic_check())
    post_task = asyncio.create_task(periodic_post())
    bot_task = asyncio.create_task(dp.start_polling(bot))
    
    try:
        # Run all tasks concurrently
        await asyncio.gather(check_task, post_task, bot_task)
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
    finally:
        # Cleanup
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
