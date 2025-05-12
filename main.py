import asyncio
import logging
import os
import re
import aiohttp
import requests
import signal
import sys
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

import spotipy
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramConflictError
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
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", "12"))

# Initialize services
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
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

# Improved function to scrape Bandcamp album info
async def scrape_bandcamp(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # Синхронный запрос, потому что некоторые Bandcamp страницы не работают с aiohttp
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return None
            
        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        # Extract data
        result = {}
        
        # Artist name - попробуем несколько селекторов
        artist_name = None
        selectors = [
            'span[itemprop="byArtist"] a',
            '.albumTitle span a',
            '#name-section h3 span a',
            '.band-name a'
        ]
        
        for selector in selectors:
            artist_elem = soup.select_one(selector)
            if artist_elem:
                artist_name = artist_elem.text.strip()
                break
        
        if not artist_name:
            artist_name = "Unknown Artist"
        
        result['artist'] = artist_name
        
        # Album name - попробуем несколько селекторов
        album_name = None
        selectors = [
            'h2[itemprop="name"]',
            '.trackTitle',
            '.title'
        ]
        
        for selector in selectors:
            album_elem = soup.select_one(selector)
            if album_elem:
                album_name = album_elem.text.strip()
                break
        
        if not album_name:
            album_name = "Unknown Album"
        
        result['album'] = album_name
        
        # Release date - попробуем несколько способов
        release_date = None
        
        # Вариант 1: meta-теги
        date_elem = soup.select_one('meta[itemprop="datePublished"]')
        if date_elem and 'content' in date_elem.attrs:
            release_date = date_elem['content']
        
        # Вариант 2: текст выпуска
        if not release_date:
            release_text = soup.find(string=re.compile(r'released', re.IGNORECASE))
            if release_text:
                date_match = re.search(r'released\s+(\w+\s+\d+,\s+\d{4})', release_text, re.IGNORECASE)
                if date_match:
                    release_date = date_match.group(1)
        
        if not release_date:
            release_date = datetime.now().strftime("%Y-%m-%d")
        
        result['date'] = release_date
        
        # Track count - попробуем несколько селекторов
        tracks = 0
        selectors = [
            'table[itemprop="tracks"] tr',
            '.track_list tr',
            '.track_row_view'
        ]
        
        for selector in selectors:
            track_elems = soup.select(selector)
            if track_elems:
                tracks = len(track_elems)
                break
        
        result['tracks'] = tracks if tracks else "unknown"
        
        # Cover image - попробуем несколько селекторов
        cover_url = None
        selectors = [
            '#tralbumArt img',
            '.popupImage',
            'img.album_art'
        ]
        
        for selector in selectors:
            img_elem = soup.select_one(selector)
            if img_elem and 'src' in img_elem.attrs:
                cover_url = img_elem['src']
                break
        
        if cover_url:
            result['cover_url'] = cover_url
        
        # Type (Album or Single)
        result['type'] = "Album"  # Default to Album
        
        # Tags - используем теги alt в обложке или любые другие
        tags = []
        tags_elem = soup.select('.tag')
        for tag in tags_elem[:3]:  # Get up to 3 tags
            tag_text = tag.text.strip()
            if tag_text:
                tags.append(tag_text)
        
        if not tags:
            tags = ['bandcamp']
        
        result['tags'] = tags
        
        return result
    except Exception as e:
        logger.error(f"Error scraping Bandcamp: {e}")
        return None

# Функции для корректного завершения
async def on_startup(dispatcher):
    # Удаляем веб-хук перед запуском бота
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot started, webhook deleted")
    
    # Проверяем подключение
    bot_info = await bot.get_me()
    logger.info(f"Connected as @{bot_info.username}")

async def on_shutdown(dispatcher):
    logger.info("Shutting down bot...")
    await bot.session.close()

# Обработчик сигналов
def signal_handler(sig, frame):
    logger.info(f"Received signal {sig}, shutting down...")
    # Будет вызвана функция asyncio.run(on_shutdown(dp)) в main() при выходе
    sys.exit(0)

def register_shutdown_handlers():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

# Функция для обновления статуса бота
async def update_bot_status():
    while True:
        try:
            current_time = datetime.now().isoformat()
            supabase.table('bot_status').upsert({
                'key': 'heartbeat',
                'value': current_time
            }).execute()
            logger.debug(f"Heartbeat updated at {current_time}")
        except Exception as e:
            logger.error(f"Error updating heartbeat: {e}")
        
        await asyncio.sleep(30)

# Обработчики команд
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
            
            # ТОЧНЫЙ ФОРМАТ ВЫВОДА ДЛЯ SPOTIFY (с форматированием HTML)
            message_text = f"coma.fm\n" \
                          f"{artist_names}\n" \
                          f"{album_name}\n" \
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
                
                # ТОЧНЫЙ ФОРМАТ ВЫВОДА ДЛЯ BANDCAMP (без форматирования оригинального текста)
                message_text = f"coma.fm\n" \
                              f"Bandcamp Album\n" \
                              f"{album_name}\n" \
                              f"{release_date}, {album_type}, {tracks} tracks\n" \
                              f"#bandcamp\n" \
                              f"🎧 Listen on [Bandcamp]({url})"
                
                # ПОСТИНГ В КАНАЛ С ОБЛОЖКОЙ
                if cover_url:
                    await bot.send_photo(CHANNEL_ID, cover_url, caption=message_text, parse_mode="Markdown")
                else:
                    await bot.send_message(CHANNEL_ID, message_text, parse_mode="Markdown")
            else:
                # Fallback if scraping failed
                message_text = f"coma.fm\n" \
                              f"Bandcamp Album\n" \
                              f"Unknown Album\n" \
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
        # Get followed artists - использовать пагинацию чтобы получить ВСЕ артисты
        all_artists = []
        results = sp.current_user_followed_artists(limit=50)
        
        artists = results['artists']['items']
        all_artists.extend(artists)
        
        # Получаем все страницы артистов
        while results['artists']['next']:
            results = sp.next(results['artists'])
            all_artists.extend(results['artists']['items'])
        
        logger.info(f"Found {len(all_artists)} followed artists")
        
        if not all_artists:
            await message.answer("No followed artists found")
            return
        
        # Получаем days_back из базы данных
        days_back = 3
        try:
            result = supabase.table('bot_status').select('value').eq('key', 'release_days_threshold').execute()
            if result.data:
                days_back = int(result.data[0]['value'])
        except:
            pass
        
        cutoff_date = datetime.now() - timedelta(days=days_back)
        
        # Check for new releases
        new_releases = []
        new_releases_added = 0
        
        # Проверяем только первые 20 артистов, чтобы не тратить лимиты API
        for artist in all_artists[:20]:
            try:
                artist_id = artist['id']
                artist_name = artist['name']
                
                albums = sp.artist_albums(artist_id, album_type='album,single', country='US', limit=5)
                
                for album in albums['items']:
                    album_id = album['id']
                    album_name = album['name']
                    release_date = album['release_date']
                    
                    # Parse release date
                    try:
                        if len(release_date) == 4:  # Year only
                            release_datetime = datetime.strptime(release_date, '%Y')
                        elif len(release_date) == 7:  # Year-month
                            release_datetime = datetime.strptime(release_date, '%Y-%m')
                        else:  # Full date
                            release_datetime = datetime.strptime(release_date, '%Y-%m-%d')
                    except:
                        continue
                    
                    # Check if within threshold
                    if release_datetime >= cutoff_date:
                        logger.info(f"Found recent release: {artist_name} - {album_name} ({release_date})")
                        
                        # Add to result for user
                        new_releases.append({
                            'artist': artist_name,
                            'album': album_name,
                            'id': album_id
                        })
                        
                        # Check if already in queue
                        already_exists = any(
                            item.get('item_id') == album_id and item.get('item_type') == 'album' 
                            for item in posting_queue
                        )
                        
                        if not already_exists:
                            # Add to queue
                            posting_queue.append({
                                'item_id': album_id,
                                'item_type': 'album',
                                'added_at': datetime.now().isoformat()
                            })
                            
                            # Save to database
                            try:
                                supabase.table('post_queue').insert({
                                    'item_id': album_id,
                                    'item_type': 'album',
                                    'added_at': datetime.now().isoformat()
                                }).execute()
                                
                                new_releases_added += 1
                            except Exception as e:
                                logger.error(f"Error saving to database: {e}")
            except Exception as e:
                logger.error(f"Error checking artist {artist['name']}: {e}")
        
        # Update last check time
        try:
            supabase.table('bot_status').upsert({
                'key': 'last_check',
                'value': datetime.now().isoformat()
            }).execute()
        except:
            pass
        
        if new_releases:
            result_text = f"Found {len(new_releases)} recent releases, added {new_releases_added} to queue:\n\n"
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
    try:
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
    except Exception as e:
        logger.error(f"Error in message handler: {e}", exc_info=True)
        try:
            await message.answer(f"❌ Ошибка при обработке сообщения: {str(e)}")
        except:
            logger.error("Failed to send error message")

async def main():
    # Регистрируем обработчики сигналов
    register_shutdown_handlers()
    
    # Пробуем загрузить очередь из базы данных
    try:
        result = supabase.table('post_queue').select('*').eq('posted', False).order('id').execute()
        global posting_queue
        posting_queue = result.data if result.data else []
        logger.info(f"Loaded {len(posting_queue)} items from queue")
    except Exception as e:
        logger.error(f"Error loading queue: {e}")
        logger.info("Starting with empty queue")
    
    # Запускаем задачу обновления статуса
    asyncio.create_task(update_bot_status())
    
    # Добавляем обработчики запуска и завершения
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Сбрасываем webhook перед запуском
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook deleted successfully")
    except Exception as e:
        logger.error(f"Error deleting webhook: {e}")
    
    logger.info("Starting bot polling...")
    
    # Запускаем поллинг
    try:
        await dp.start_polling(bot, skip_updates=True)
    except TelegramConflictError as e:
        logger.error(f"Telegram conflict error: {e}")
        # Ждем перед повторной попыткой
        await asyncio.sleep(10)
        
        # Пробуем снова
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot, skip_updates=True)
        except Exception as retry_e:
            logger.error(f"Failed to restart after conflict: {retry_e}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error starting bot: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
