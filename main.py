import os
import asyncio
import aiohttp
import aiogram
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message
import requests
import json
import logging
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import time
from urllib.parse import urlparse
import re
from supabase import create_client, Client

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
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

# Инициализация Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

# Инициализация бота
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Create spotify_api instance
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
                        
                        # Extract artist name from URL
                        artist_from_url = url.split('//')[1].split('.')[0]
                        
                        # Extract release info
                        title_elem = soup.find('h2', class_='trackTitle')
                        release_info = {
                            'artist': artist_from_url.replace('-', ' ').title(),  # Extract from URL and format
                            'title': title_elem.text.strip() if title_elem else '',
                            'release_date': '',
                            'tracks': [],
                            'genres': [],
                            'image_url': '',
                            'url': url
                        }
                        
                        # Try to get artist name from band-name element
                        band_name = soup.select_one('#band-name-location .title a')
                        if band_name:
                            release_info['artist'] = band_name.text.strip()
                        
                        # Alternative attempt from page title
                        if not release_info['artist']:
                            page_title = soup.find('meta', property='og:site_name')
                            if page_title:
                                release_info['artist'] = page_title.get('content', '').strip()
                        
                        # Get cover art
                        image_elem = soup.find('div', id='tralbumArt')
                        if image_elem and image_elem.find('img'):
                            release_info['image_url'] = image_elem.find('img')['src']
                        
                        # Get track list
                        track_table = soup.find('table', id='track_table')
                        if track_table:
                            track_rows = track_table.find_all('tr', class_='track_row_view')
                            for track in track_rows:
                                title_span = track.find('span', class_='track-title')
                                if title_span:
                                    release_info['tracks'].append(title_span.text.strip())
                        
                        # Get genres from tags
                        tag_elements = soup.find_all('a', class_='tag')
                        release_info['genres'] = [tag.text.strip() for tag in tag_elements[:3]]
                        
                        # Get release date
                        release_date_elem = soup.find('div', class_='tralbum-credits')
                        if release_date_elem:
                            date_text = release_date_elem.text
                            # Parse date from text like "released April 1, 2023"
                            import re
                            date_match = re.search(r'released\s+(\w+\s+\d+,\s+\d+)', date_text)
                            if date_match:
                                release_info['release_date'] = date_match.group(1)
                        
                        # Log the found information for debugging
                        logger.info(f"Found artist: {release_info['artist']}")
                        logger.info(f"Found title: {release_info['title']}")
                        
                        return release_info
                    else:
                        logger.error(f"Failed to fetch Bandcamp page: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error scraping Bandcamp: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None

# Database operations with Supabase
class SupabaseDB:
    @staticmethod
    def add_to_queue(release_data):
        try:
            # Ensure artist is set before adding to queue
            if not release_data.get('artist') and release_data.get('platform') == 'bandcamp':
                # Extract from URL as fallback
                url = release_data.get('listen_url', '')
                if 'bandcamp.com' in url:
                    artist_from_url = url.split('//')[1].split('.')[0]
                    release_data['artist'] = artist_from_url.replace('-', ' ').title()
                    
            data = supabase.table('post_queue').insert(release_data).execute()
            logger.info(f"Added to queue: {data}")
            return True
        except Exception as e:
            logger.error(f"Error adding to queue: {str(e)}")
            return False

    @staticmethod
    def get_queue():
        try:
            data = supabase.table('post_queue').select("*").order('id').execute()
            return data.data if data.data else []
        except Exception as e:
            logger.error(f"Error getting queue: {str(e)}")
            return []

    @staticmethod
    def remove_from_queue(queue_id):
        try:
            data = supabase.table('post_queue').delete().eq('id', queue_id).execute()
            logger.info(f"Removed from queue: {data}")
            return True
        except Exception as e:
            logger.error(f"Error removing from queue: {str(e)}")
            return False

    @staticmethod
    def clear_queue():
        try:
            data = supabase.table('post_queue').delete().neq('id', 0).execute()
            logger.info(f"Cleared queue: {data}")
            return True
        except Exception as e:
            logger.error(f"Error clearing queue: {str(e)}")
            return False

    @staticmethod
    def mark_as_posted(release_id, artist, release):
        try:
            data = supabase.table('posted_releases').insert({
                'id': release_id,
                'artist': artist,
                'release': release,
                'date_posted': datetime.now().isoformat()
            }).execute()
            logger.info(f"Marked as posted: {data}")
            return True
        except Exception as e:
            logger.error(f"Error marking as posted: {str(e)}")
            return False

    @staticmethod
    def is_posted(release_id):
        try:
            data = supabase.table('posted_releases').select("*").eq('id', release_id).execute()
            return bool(data.data)
        except Exception as e:
            logger.error(f"Error checking if posted: {str(e)}")
            return False

    @staticmethod
    def update_status(key, value):
        try:
            # Check if key exists
            existing = supabase.table('bot_status').select("*").eq('key', key).execute()
            if existing.data:
                # Update
                data = supabase.table('bot_status').update({'value': value}).eq('key', key).execute()
            else:
                # Insert
                data = supabase.table('bot_status').insert({'key': key, 'value': value}).execute()
            logger.info(f"Updated status: {data}")
            return True
        except Exception as e:
            logger.error(f"Error updating status: {str(e)}")
            return False

    @staticmethod
    def get_status(key):
        try:
            data = supabase.table('bot_status').select("value").eq('key', key).execute()
            return data.data[0]['value'] if data.data else None
        except Exception as e:
            logger.error(f"Error getting status: {str(e)}")
            return None

# Escape special characters for MarkdownV2
def escape_markdown_v2(text):
    if not text:
        return ""
    # Escape special characters for MarkdownV2
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

# Format post
def format_post(release_data):
    # Escape all text content
    artist = escape_markdown_v2(release_data['artist'])
    release = escape_markdown_v2(release_data['release'])
    release_date = escape_markdown_v2(release_data['release_date'])
    release_type = escape_markdown_v2(release_data['release_type'])
    genres = escape_markdown_v2(release_data['genres'])
    
    text = f"*{artist}*\n"
    text += f"*{release}*\n"
    text += f"{release_date}, {release_type}, {release_data['tracks_count']} tracks\n"
    text += f"Genre: {genres}\n"
    
    listen_emoji = "🎧"
    if release_data['platform'] == 'spotify':
        text += f"{listen_emoji} Listen on [Spotify]({release_data['listen_url']})"
    else:
        text += f"{listen_emoji} Listen on [Bandcamp]({release_data['listen_url']})"
    
    return text

# Spotify check for new releases
async def check_new_releases():
    logger.info("Starting to check for new releases...")
    SupabaseDB.update_status('last_check', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    # Get followed artists
    token = await spotify_api.get_access_token()
    if not token:
        logger.error("Failed to get access token")
        return
    
    url = "https://api.spotify.com/v1/me/following?type=artist&limit=50"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        artists = []
        async with aiohttp.ClientSession() as session:
            while url:
                await spotify_api.rate_limiter.acquire()
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        artists.extend(data['artists']['items'])
                        url = data['artists']['next']
                    else:
                        logger.error(f"Failed to get followed artists: {await response.text()}")
                        break
        
        logger.info(f"Found {len(artists)} followed artists")
        
        new_releases_count = 0
        
        # Process artists in smaller batches to avoid session issues
        for i in range(0, len(artists), 10):
            artist_batch = artists[i:i+10]
            
            async with aiohttp.ClientSession() as session:
                for artist in artist_batch:
                    # Get artist's latest releases
                    await spotify_api.rate_limiter.acquire()
                    album_url = f"https://api.spotify.com/v1/artists/{artist['id']}/albums?include_groups=album,single&market=US&limit=5"
                    async with session.get(album_url, headers=headers) as album_response:
                        if album_response.status == 200:
                            album_data = await album_response.json()
                            for album in album_data['items']:
                                release_id = f"spotify_{album['id']}"
                                if not SupabaseDB.is_posted(release_id):
                                    # Get full album details
                                    album_details = await spotify_api.get_album_details(album['id'])
                                    if album_details:
                                        release_data = {
                                            'artist': album_details['artists'][0]['name'],
                                            'release': album_details['name'],
                                            'release_date': album_details['release_date'],
                                            'release_type': album_details['album_type'],
                                            'tracks_count': album_details['total_tracks'],
                                            'genres': ', '.join([f"#{g}" for g in album_details.get('genres', [])]),
                                            'image_url': album_details['images'][0]['url'] if album_details['images'] else '',
                                            'listen_url': album_details['external_urls']['spotify'],
                                            'platform': 'spotify',
                                            'created_at': datetime.now().isoformat()
                                        }
                                        SupabaseDB.add_to_queue(release_data)
                                        new_releases_count += 1
                                        logger.info(f"Added {album_details['artists'][0]['name']} - {album_details['name']} to queue")
        
        logger.info(f"Check completed. Added {new_releases_count} new releases to queue.")
    except Exception as e:
        logger.error(f"Error in check_new_releases: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

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
/post - Post next item in queue manually

You can also send Spotify or Bandcamp links to add them to the queue.
"""
    await message.reply(help_text)

@dp.message(Command("queue"))
async def cmd_queue(message: Message):
    queue = SupabaseDB.get_queue()
    if not queue:
        await message.reply("📭 Post queue is empty.")
        return

    text = "📋 *Post Queue:*\n\n"
    for idx, item in enumerate(queue, 1):
        # Escape special characters for MarkdownV2
        artist = escape_markdown_v2(item['artist'])
        release = escape_markdown_v2(item['release'])
        platform = escape_markdown_v2(item['platform'])
        text += f"{idx}\\. {artist} \\- {release} \\({platform}\\)\n"
    
    await message.reply(text, parse_mode="MarkdownV2")

@dp.message(Command("queue_clear"))
async def cmd_queue_clear(message: Message):
    SupabaseDB.clear_queue()
    await message.reply("✅ Queue cleared.")

@dp.message(Command("status"))
async def cmd_status(message: Message):
    last_check = SupabaseDB.get_status('last_check')
    last_post = SupabaseDB.get_status('last_post')
    
    status_text = "🤖 *Bot Status:*\n\n"
    if last_check:
        status_text += f"Last check: {escape_markdown_v2(last_check)}\n"
    else:
        status_text += "Last check: Never\n"
    
    if last_post:
        status_text += f"Last post: {escape_markdown_v2(last_post)}\n"
    else:
        status_text += "Last post: Never\n"
    
    status_text += f"Queue length: {len(SupabaseDB.get_queue())}\n"
    
    await message.reply(status_text, parse_mode="MarkdownV2")

@dp.message(Command("check"))
async def cmd_check(message: Message):
    await message.reply("🔍 Checking for new releases...")
    try:
        await check_new_releases()
        await message.reply("✅ Check completed!")
    except Exception as e:
        logger.error(f"Error checking releases: {str(e)}")
        await message.reply("❌ Error checking releases. Check logs for details.")

@dp.message(Command("post"))
async def cmd_post(message: Message):
    """Manually post next item in queue"""
    try:
        await post_from_queue()
        await message.reply("✅ Posted next item from queue!")
    except Exception as e:
        logger.error(f"Error in manual post: {str(e)}")
        await message.reply("❌ Error posting item from queue.")

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
                        'platform': 'spotify',
                        'created_at': datetime.now().isoformat()
                    }
                    SupabaseDB.add_to_queue(release_data)
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
                            'platform': 'spotify',
                            'created_at': datetime.now().isoformat()
                        }
                        SupabaseDB.add_to_queue(release_data)
                        await message.reply("✅ Added to posting queue!")
                    else:
                        await message.reply("❌ Failed to get track/album details.")
        else:
            logger.info("No match found for Spotify regex")
            await message.reply("❌ Invalid Spotify link format")

    # Check if message contains Bandcamp URL
    elif 'bandcamp.com' in text:
        logger.info("Found Bandcamp URL")
        url_match = re.search(r'https?://[a-zA-Z0-9.-]+\.bandcamp\.com/album/[a-zA-Z0-9-]+', text)
        if url_match:
            url = url_match.group(0)
            logger.info(f"Matched Bandcamp URL: {url}")
            release_info = await BandcampScraper.get_release_info(url)
            if release_info:
                logger.info(f"Bandcamp data received: {release_info['title']}")
                release_data = {
                    'artist': release_info['artist'],
                    'release': release_info['title'],
                    'release_date': release_info['release_date'],
                    'release_type': 'album' if len(release_info['tracks']) > 1 else 'single',
                    'tracks_count': len(release_info['tracks']),
                    'genres': ', '.join([f"#{g}" for g in release_info['genres']]),
                    'image_url': release_info['image_url'],
                    'listen_url': url,
                    'platform': 'bandcamp',
                    'created_at': datetime.now().isoformat()
                }
                SupabaseDB.add_to_queue(release_data)
                await message.reply("✅ Added to posting queue!")
            else:
                await message.reply("❌ Failed to get Bandcamp release details.")
                logger.error(f"Failed to get Bandcamp details for {url}")
        else:
            logger.info("No match found for Bandcamp regex")
            await message.reply("❌ Invalid Bandcamp link format")
    else:
        logger.info("Message doesn't contain Spotify or Bandcamp URL")

# Post from queue
async def post_from_queue():
    queue = SupabaseDB.get_queue()
    if not queue:
        return

    item = queue[0]
    queue_id = item['id']
    
    try:
        # Download image
        if item['image_url']:
            async with aiohttp.ClientSession() as session:
                async with session.get(item['image_url']) as response:
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
                            caption=format_post(item),
                            parse_mode="MarkdownV2"
                        )
                        
                        # Clean up
                        os.remove(image_path)
                    else:
                        # Send without image if download fails
                        await bot.send_message(
                            chat_id=TELEGRAM_CHANNEL_ID,
                            text=format_post(item),
                            parse_mode="MarkdownV2"
                        )
        else:
            # Send without image
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=format_post(item),
                parse_mode="MarkdownV2"
            )
        
        SupabaseDB.remove_from_queue(queue_id)
        SupabaseDB.mark_as_posted(f"{item['platform']}_{queue_id}", item['artist'], item['release'])
        SupabaseDB.update_status('last_post', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        logger.info(f"Posted {item['artist']} - {item['release']}")
        
    except Exception as e:
        logger.error(f"Error posting: {str(e)}")

# Инициализация таблиц Supabase
def init_supabase_tables():
    try:
        # Create tables if they don't exist
        create_queries = [
            """
            CREATE TABLE IF NOT EXISTS posted_releases (
                id TEXT PRIMARY KEY,
                artist TEXT,
                release TEXT,
                date_posted TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS post_queue (
                id SERIAL PRIMARY KEY,
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
            """,
            """
            CREATE TABLE IF NOT EXISTS bot_status (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        ]
        
        for query in create_queries:
            try:
                supabase.rpc('run_sql', {'sql': query}).execute()
            except Exception as e:
                logger.info(f"Table already exists or error: {str(e)}")
                
    except Exception as e:
        logger.error(f"Error initializing Supabase tables: {str(e)}")

# Main function
async def main():
    # Initialize Supabase tables
    init_supabase_tables()
    
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
            
            # Check for custom posting interval
            interval_str = SupabaseDB.get_status('post_interval')
            interval_minutes = int(interval_str) if interval_str else 60  # default 60 minutes
            await asyncio.sleep(interval_minutes * 60)
    
    # Start tasks
    check_task = asyncio.create_task(periodic_check())
    post_task = asyncio.create_task(periodic_post())
    
    try:
        # Run all tasks concurrently
        await dp.start_polling(bot, skip_updates=True)
    except asyncio.CancelledError:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
    finally:
        # Cleanup
        await bot.session.close()
        check_task.cancel()
        post_task.cancel()
        await asyncio.gather(check_task, post_task, return_exceptions=True)

if __name__ == '__main__':
    asyncio.run(main())
