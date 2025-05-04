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

# Database operations with Supabase
class SupabaseDB:
    @staticmethod
    def add_to_queue(release_data):
        try:
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
        text += f"{idx}. {item['artist']} - {item['release']} ({item['platform']})\n"
    
    await message.reply(text, parse_mode="Markdown")

@dp.message(Command("queue_clear"))
async def cmd_queue_clear(message: Message):
    SupabaseDB.clear_queue()
    await message.reply("✅ Queue cleared.")

@dp.message(Command("status"))
async def cmd_status(message: Message):
    last_check = SupabaseDB.get_status('last_check')
    last_post = SupabaseDB.get_status('last_post')
    
    status_text = "🤖 *Bot Status:*\n\n"
    status_text += f"Last check: {last_check if last_check else 'Never'}\n"
    status_text += f"Last post: {last_post if last_post else 'Never'}\n"
    status_text += f"Queue length: {len(SupabaseDB.get_queue())}\n"
    
    await message.reply(status_text, parse_mode="Markdown")

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
                            parse_mode="Markdown"
                        )
                        
                        # Clean up
                        os.remove(image_path)
                    else:
                        # Send without image if download fails
                        await bot.send_message(
                            chat_id=TELEGRAM_CHANNEL_ID,
                            text=format_post(item),
                            parse_mode="Markdown"
                        )
        else:
            # Send without image
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=format_post(item),
                parse_mode="Markdown"
            )
        
        SupabaseDB.remove_from_queue(queue_id)
        SupabaseDB.mark_as_posted(f"{item['platform']}_{queue_id}", item['artist'], item['release'])
        SupabaseDB.update_status('last_post', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        logger.info(f"Posted {item['artist']} - {item['release']}")
        
    except Exception as e:
        logger.error(f"Error posting: {str(e)}")

# Main function
async def main():
    # Start periodic tasks
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
        post_task.cancel()
        await asyncio.gather(post_task, return_exceptions=True)

if __name__ == '__main__':
    asyncio.run(main())
