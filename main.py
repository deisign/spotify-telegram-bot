import asyncio
import logging
import os
import re
import aiohttp
from datetime import datetime
from bs4 import BeautifulSoup

import spotipy
from aiogram import Bot, Dispatcher
from aiogram.types import Message, FSInputFile, URLInputFile
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
except Exception as e:
    logger.error(f"Failed to initialize SpotifyOAuth: {e}")
    auth_manager = None

# Queue
posting_queue = []

# Get a fresh Spotify client before each operation
def get_spotify_client():
    try:
        if not auth_manager:
            return None
            
        # Refresh the token before using
        token_info = auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
        return spotipy.Spotify(auth=token_info['access_token'])
    except Exception as e:
        logger.error(f"Error refreshing Spotify token: {e}")
        return None

# Function to scrape Bandcamp album info
async def scrape_bandcamp(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    return None
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                # Extract data
                result = {}
                
                # Artist name
                artist_elem = soup.select_one('span[itemprop="byArtist"] a')
                if artist_elem:
                    result['artist'] = artist_elem.text.strip()
                else:
                    result['artist'] = "Unknown Artist"
                
                # Album name
                album_elem = soup.select_one('h2[itemprop="name"]')
                if album_elem:
                    result['album'] = album_elem.text.strip()
                else:
                    result['album'] = "Unknown Album"
                
                # Release date
                date_elem = soup.select_one('meta[itemprop="datePublished"]')
                if date_elem and 'content' in date_elem.attrs:
                    result['date'] = date_elem['content']
                else:
                    result['date'] = datetime.now().strftime("%Y-%m-%d")
                
                # Track count
                tracks = soup.select('table[itemprop="tracks"] tr')
                result['tracks'] = len(tracks) if tracks else "unknown"
                
                # Cover image
                img_elem = soup.select_one('#tralbumArt img')
                if img_elem and 'src' in img_elem.attrs:
                    result['cover_url'] = img_elem['src']
                
                # Type (Album or Single)
                result['type'] = "Album"  # Default to Album
                
                # Tags
                tags = []
                tags_elem = soup.select('.tag')
                for tag in tags_elem[:3]:  # Get up to 3 tags
                    tag_text = tag.text.strip()
                    if tag_text:
                        tags.append(tag_text)
                result['tags'] = tags
                
                return result
    except Exception as e:
        logger.error(f"Error scraping Bandcamp: {e}")
        return None

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

You can also send Spotify or Bandcamp links to add them to the queue."""
    
    await message.answer(help_text)

@dp.message(Command("queue"))
async def cmd_queue(message: Message):
    logger.info("Received /queue command")
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    queue_text = "📦 Post Queue:\n\n"
    
    sp = get_spotify_client()
    
    for i, item in enumerate(posting_queue, 1):
        if item.get('item_type') == 'album' and sp:
            try:
                album = sp.album(item['item_id'])
                artist_name = ', '.join([artist['name'] for artist in album['artists']])
                album_name = album['name']
                queue_text += f"{i}. {artist_name} - {album_name}\n"
            except Exception as e:
                logger.error(f"Error getting album: {e}")
                queue_text += f"{i}. album ID: {item.get('item_id')}\n"
        elif item.get('item_type') == 'bandcamp':
            metadata = item.get('metadata', {})
            url = metadata.get('url', 'unknown')
            queue_text += f"{i}. Bandcamp: {url}\n"
        else:
            queue_text += f"{i}. {item.get('item_type')} ID: {item.get('item_id')}\n"
    
    await message.answer(queue_text)

@dp.message(Command("post"))
async def cmd_post(message: Message):
    logger.info("Received /post command")
    
    if not posting_queue:
        await message.answer("📭 Post queue is empty.")
        return
    
    if not CHANNEL_ID:
        await message.answer("❌ CHANNEL_ID not configured")
        return
    
    item = posting_queue[0]
    try:
        sp = get_spotify_client()
        
        if item.get('item_type') == 'album' and sp:
            album = sp.album(item['item_id'])
            
            # Получаем информацию об альбоме
            artist_names = ', '.join([artist['name'] for artist in album['artists']])
            album_name = album['name']
            release_date = album['release_date']
            tracks = album['total_tracks']
            album_type = "Album" if album['album_type'] == 'album' else "Single"
            
            # Получаем жанры (берем из первого артиста)
            artist_genres = []
            try:
                if album['artists'] and len(album['artists']) > 0:
                    artist = sp.artist(album['artists'][0]['id'])
                    artist_genres = artist.get('genres', [])[:3]  # Берем максимум 3 жанра
            except:
                pass
            
            genre_tags = " ".join([f"#{genre.replace(' ', '')}" for genre in artist_genres]) if artist_genres else ""
            album_url = f"https://open.spotify.com/album/{item['item_id']}"
            
            # Получаем ссылку на обложку
            cover_url = None
            try:
                if album['images'] and len(album['images']) > 0:
                    cover_url = album['images'][0]['url']  # Берем самую большую обложку
            except:
                pass
            
            # ТОЧНЫЙ ФОРМАТ ВЫВОДА ДЛЯ SPOTIFY
            message_text = f"**{artist_names}**\n" \
                           f"**{album_name}**\n" \
                           f"{release_date}, {album_type}, {tracks} tracks\n" \
                           f"{genre_tags}\n" \
                           f"🎧 Listen on [Spotify]({album_url})"
            
            # ПОСТИНГ В КАНАЛ С ОБЛОЖКОЙ
            if cover_url:
                await bot.send_photo(CHANNEL_ID, cover_url, caption=message_text, parse_mode="Markdown")
            else:
                await bot.send_message(CHANNEL_ID, message_text, parse_mode="Markdown")
                
            logger.info(f"Posted to channel {CHANNEL_ID}")
            
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
            
            await message.answer(f"✅ Posted album {artist_names} - {album_name}")
        
        elif item.get('item_type') == 'bandcamp':
            # Для Bandcamp используем скрейпинг
            url = item.get('metadata', {}).get('url', 'unknown')
            
            # Scrape Bandcamp
            bandcamp_info = await scrape_bandcamp(url)
            
            if bandcamp_info:
                artist_name = bandcamp_info.get('artist', 'Unknown Artist')
                album_name = bandcamp_info.get('album', 'Unknown Album')
                release_date = bandcamp_info.get('date', datetime.now().strftime("%Y-%m-%d"))
                tracks = bandcamp_info.get('tracks', 'unknown')
                album_type = bandcamp_info.get('type', 'Album')
                cover_url = bandcamp_info.get('cover_url')
                
                # Get tags
                tags = bandcamp_info.get('tags', [])
                if not tags:
                    tags = ['bandcamp']
                
                genre_tags = " ".join([f"#{tag.replace(' ', '')}" for tag in tags])
                
                # ТОЧНЫЙ ФОРМАТ ВЫВОДА ДЛЯ BANDCAMP
                message_text = f"**{artist_name}**\n" \
                               f"**{album_name}**\n" \
                               f"{release_date}, {album_type}, {tracks} tracks\n" \
                               f"{genre_tags}\n" \
                               f"🎧 Listen on [Bandcamp]({url})"
                
                # ПОСТИНГ В КАНАЛ С ОБЛОЖКОЙ
                if cover_url:
                    await bot.send_photo(CHANNEL_ID, cover_url, caption=message_text, parse_mode="Markdown")
                else:
                    await bot.send_message(CHANNEL_ID, message_text, parse_mode="Markdown")
            else:
                # Fallback if scraping failed
                message_text = f"**Bandcamp Album**\n" \
                               f"**Unknown Album**\n" \
                               f"{datetime.now().strftime('%Y-%m-%d')}, Album, unknown tracks\n" \
                               f"#bandcamp\n" \
                               f"🎧 Listen on [Bandcamp]({url})"
                
                await bot.send_message(CHANNEL_ID, message_text, parse_mode="Markdown")
                
            logger.info(f"Posted to channel {CHANNEL_ID}")
            
            # УДАЛЕНИЕ ИЗ ОЧЕРЕДИ
            posting_queue.pop(0)
            
            # ОБНОВЛЕНИЕ В БАЗЕ (если таблица существует)
            try:
                supabase.table('post_queue').update({
                    'posted': True,
                    'posted_at': datetime.now().isoformat()
                }).eq('item_id', item['item_id']).eq('item_type', 'bandcamp').execute()
            except Exception as e:
                logger.error(f"Error updating database: {e}")
            
            await message.answer(f"✅ Posted Bandcamp album")
        
        else:
            await message.answer(f"❌ Unknown item type or Spotify not initialized")
            
    except Exception as e:
        logger.error(f"Error in post command: {e}")
        await message.answer(f"❌ Error posting: {str(e)}")

@dp.message(Command("check"))
async def cmd_check(message: Message):
    logger.info("Received /check command")
    await message.answer("🔍 Checking for new releases...")
    
    sp = get_spotify_client()
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
                # Add to queue
                already_exists = any(item.get('item_id') == rel['id'] and item.get('item_type') == 'album' for item in posting_queue)
                if not already_exists:
                    posting_queue.append({
                        'item_id': rel['id'],
                        'item_type': 'album',
                        'added_at': datetime.now().isoformat()
                    })
                    result_text += f"  ✅ Added to queue\n"
                else:
                    result_text += f"  ℹ️ Already in queue\n"
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
    
    # Проверка Spotify
    spotify_match = re.search(r'https://open\.spotify\.com/album/([a-zA-Z0-9]+)', message.text)
    if spotify_match:
        album_id = spotify_match.group(1)
        logger.info(f"Found Spotify album ID: {album_id}")
        
        # Check if already in queue
        already_exists = any(item.get('item_id') == album_id and item.get('item_type') == 'album' for item in posting_queue)
        
        if already_exists:
            await message.answer(f"ℹ️ Album already in queue")
            return
        
        # Validate album exists
        sp = get_spotify_client()
        if sp:
            try:
                album = sp.album(album_id)
                artist_name = ', '.join([artist['name'] for artist in album['artists']])
                album_name = album['name']
                
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
            except Exception as e:
                logger.error(f"Error validating album: {e}")
                await message.answer(f"❌ Error adding album: {str(e)}")
                return
        else:
            # Add without validation
            posting_queue.append({
                'item_id': album_id,
                'item_type': 'album',
                'added_at': datetime.now().isoformat()
            })
            await message.answer(f"✅ Added album to queue")
            return
    
    # Проверка Bandcamp - более общий паттерн 
    bandcamp_match = re.search(r'https?://[^/]*?bandcamp\.com/album/([^/?#]+)', message.text)
    if bandcamp_match:
        album_slug = bandcamp_match.group(1)
        logger.info(f"Found Bandcamp album: {album_slug}")
        
        item_id = f"bandcamp_{album_slug}"
        
        # Check if already in queue
        already_exists = any(item.get('item_id') == item_id for item in posting_queue)
        
        if already_exists:
            await message.answer(f"ℹ️ Album already in queue")
            return
        
        # Add to queue
        posting_queue.append({
            'item_id': item_id,
            'item_type': 'bandcamp',
            'added_at': datetime.now().isoformat(),
            'metadata': {'url': message.text}
        })
        
        # Try to save to database (if table exists)
        try:
            supabase.table('post_queue').insert({
                'item_id': item_id,
                'item_type': 'bandcamp',
                'added_at': datetime.now().isoformat()
            }).execute()
        except Exception as e:
            logger.error(f"Error saving to database: {e}")
        
        await message.answer(f"✅ Added Bandcamp album to queue")
        return
    
    logger.info("No music link found")

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
