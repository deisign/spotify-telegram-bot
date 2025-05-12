import asyncio
import logging
import os
import re
import json
import requests
import signal
import sys
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

import spotipy
from aiogram import Bot, Dispatcher, Router
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
router = Router()
dp.include_router(router)

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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://bandcamp.com/',
            'DNT': '1',
        }
        
        logger.info(f"Scraping Bandcamp URL: {url}")
        
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            logger.error(f"Failed to fetch Bandcamp page: {response.status_code}")
            return None
            
        html = response.text
        
        # Сохраняем HTML для отладки
        try:
            with open('/tmp/bandcamp_debug.html', 'w', encoding='utf-8') as f:
                f.write(html)
            logger.debug("Saved HTML to /tmp/bandcamp_debug.html")
        except Exception as e:
            logger.error(f"Error saving debug HTML: {e}")
        
        # Ищем данные в JSON в исходном коде страницы
        result = {}
        
        # Поиск JSON данных напрямую в скрипте
        data_json_match = re.search(r'data-tralbum="({.*?})"', html)
        if data_json_match:
            try:
                # Подготавливаем JSON, заменяя экранированные кавычки
                data_json_str = data_json_match.group(1).replace('&quot;', '"')
                data = json.loads(data_json_str)
                
                # Логируем весь найденный JSON для отладки
                logger.info(f"Found data-tralbum JSON data")
                
                # Извлекаем данные об исполнителе
                if 'artist' in data:
                    result['artist'] = data['artist']
                    
                # Извлекаем название альбома
                if 'current' in data and 'title' in data['current']:
                    result['album'] = data['current']['title']
                elif 'title' in data:
                    result['album'] = data['title']
                    
                # Извлекаем дату выпуска
                if 'album_release_date' in data:
                    result['date'] = data['album_release_date']
                    
                # Извлекаем трэки
                if 'trackinfo' in data:
                    result['tracks'] = len(data['trackinfo'])
                    
                # Извлекаем URL обложки
                if 'art_id' in data:
                    art_id = data['art_id']
                    result['cover_url'] = f"https://f4.bcbits.com/img/a{art_id}_10.jpg"
                
                # Тип релиза
                result['type'] = "Album"
                
                # Теги/жанры
                if 'tags' in data and isinstance(data['tags'], list):
                    result['tags'] = [tag.get('name', '') for tag in data['tags'][:3]]
                elif 'genre' in data:
                    result['tags'] = [data['genre']]
                else:
                    result['tags'] = ['bandcamp']
                    
                logger.info(f"Successfully parsed data-tralbum JSON, result: {result}")
                return result
                
            except Exception as e:
                logger.error(f"Error parsing data-tralbum JSON: {e}", exc_info=True)
        
        # Поиск альтернативного формата JSON данных
        try:
            json_match = re.search(r'var TralbumData = ({.*?});', html, re.DOTALL)
            if json_match:
                data_str = json_match.group(1)
                # Исправляем некоторые особенности JavaScript, чтобы работало с JSON
                data_str = re.sub(r'(\w+):', r'"\1":', data_str)
                data_str = re.sub(r',\s*}', '}', data_str)
                data_str = re.sub(r',\s*]', ']', data_str)
                data_str = data_str.replace('\'', '"')
                
                try:
                    data = json.loads(data_str)
                    logger.info(f"Found TralbumData, extracted info")
                    
                    # Извлекаем данные
                    if 'artist' in data:
                        result['artist'] = data['artist']
                    
                    if 'current' in data and 'title' in data['current']:
                        result['album'] = data['current']['title']
                    elif 'album_title' in data:
                        result['album'] = data['album_title']
                        
                    if 'album_release_date' in data:
                        result['date'] = data['album_release_date']
                        
                    if 'trackinfo' in data:
                        result['tracks'] = len(data['trackinfo'])
                        
                    if 'artFullsizeUrl' in data:
                        result['cover_url'] = data['artFullsizeUrl']
                        
                    result['type'] = "Album"
                    
                    # Теги/жанры
                    if 'genres' in data and isinstance(data['genres'], list):
                        result['tags'] = data['genres'][:3]
                    else:
                        result['tags'] = ['bandcamp']
                        
                    logger.info(f"Successfully parsed TralbumData, result: {result}")
                    return result
                except Exception as e:
                    logger.error(f"Error parsing TralbumData JSON: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error with TralbumData regex: {e}", exc_info=True)
        
        # Если не нашли JSON, пробуем альтернативный метод
        # Поиск данных через Open Graph мета-теги
        og_title = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html)
        og_site_name = re.search(r'<meta\s+property="og:site_name"\s+content="([^"]+)"', html)
        
        if og_title and og_site_name:
            title = og_title.group(1)
            artist = og_site_name.group(1)
            
            # Поиск количества треков через регулярку
            track_count = len(re.findall(r'class="track-title"', html))
            if track_count == 0:
                track_count_match = re.search(r'(\d+) track album', html)
                if track_count_match:
                    track_count = track_count_match.group(1)
                else:
                    track_count = "unknown"
            
            # Поиск даты выпуска
            release_date_match = re.search(r'released\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})', html)
            if release_date_match:
                release_date = release_date_match.group(1)
            else:
                release_date = datetime.now().strftime("%Y-%m-%d")
                
            # Поиск URL обложки
            cover_match = re.search(r'<link\s+rel="image_src"\s+href="([^"]+)"', html)
            if cover_match:
                cover_url = cover_match.group(1)
            else:
                cover_url = None
                
            result = {
                'artist': artist,
                'album': title,
                'date': release_date,
                'tracks': track_count,
                'type': 'Album',
                'tags': ['bandcamp'],
                'cover_url': cover_url
            }
            
            logger.info(f"Parsed Bandcamp page using meta tags: {result}")
            return result
                
        # Если ничего не сработало, возвращаем базовую структуру с пометкой об ошибке
        logger.error("All methods of parsing Bandcamp page failed")
        
        # Пытаемся выделить хоть что-то из title
        title_match = re.search(r'<title>([^<]+)</title>', html)
        if title_match:
            title_text = title_match.group(1)
            logger.info(f"Found page title: {title_text}")
            
            # Если в title есть разделитель |, пробуем извлечь альбом и исполнителя
            if ' | ' in title_text:
                parts = title_text.split(' | ')
                if len(parts) >= 2:
                    result = {
                        'album': parts[0].strip(),
                        'artist': parts[1].strip().replace(" | Bandcamp", ""),
                        'date': datetime.now().strftime("%Y-%m-%d"),
                        'tracks': "unknown",
                        'type': 'Album',
                        'tags': ['bandcamp'],
                        'cover_url': None
                    }
                    logger.info(f"Extracted basic info from title: {result}")
                    return result
        
        # Совсем ничего не нашли
        return None
            
    except Exception as e:
        logger.error(f"Critical error in scrape_bandcamp: {e}", exc_info=True)
        return None

# Функции для корректного завершения
async def on_startup():
    # Удаляем веб-хук перед запуском бота
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot started, webhook deleted")
    
    # Проверяем подключение
    bot_info = await bot.get_me()
    logger.info(f"Connected as @{bot_info.username}")

async def on_shutdown():
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
@router.message(Command("start"))
async def cmd_start(message: Message):
    logger.info("Received /start command")
    await message.answer("Bot is working! Send a Spotify link or use /help")

@router.message(Command("help"))
async def cmd_help(message: Message):
    logger.info("Received /help command")
    help_text = """🎵 Spotify Release Tracker Bot

Available commands:
/help - Show this help message
/queue - Show posting queue
/post - Post next item in queue manually
/check - Check for new releases
/debug - Debug a Bandcamp URL

You can also send Spotify or Bandcamp links to add them to the queue."""
    
    await message.answer(help_text)

@router.message(Command("debug"))
async def cmd_debug(message: Message):
    logger.info("Received /debug command")
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Please provide a URL to debug\nExample: /debug https://example.bandcamp.com/album/example")
        return
        
    url = args[1].strip()
    await message.answer(f"🔍 Debugging URL: {url}")
    
    if "bandcamp.com" in url:
        await message.answer("Scraping Bandcamp URL...")
        result = await scrape_bandcamp(url)
        if result:
            formatted_result = json.dumps(result, indent=2)
            await message.answer(f"Scrape result:\n```\n{formatted_result}\n```", parse_mode="Markdown")
        else:
            await message.answer("❌ Failed to scrape Bandcamp URL")
    else:
        await message.answer("❌ Only Bandcamp URLs are supported for debug")

@router.message(Command("queue"))
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

@router.message(Command("post"))
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
            logger.info(f"Processing Bandcamp URL: {url}")
            
            # Scrape Bandcamp
            bandcamp_info = await scrape_bandcamp(url)
            logger.info(f"Bandcamp scrape result: {bandcamp_info}")
            
            if bandcamp_info and bandcamp_info.get('album') != "Unknown Album":
                # Если успешно получили данные
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
                message_text = f"coma.fm\n" \
                              f"{artist_name}\n" \
                              f"{album_name}\n" \
                              f"{release_date}, {album_type}, {tracks} tracks\n" \
                              f"{genre_tags}\n" \
                              f"🎧 Listen on [Bandcamp]({url})"
                
                # ПОСТИНГ В КАНАЛ С ОБЛОЖКОЙ
                if cover_url:
                    await bot.send_photo(CHANNEL_ID, cover_url, caption=message_text, parse_mode="Markdown")
                else:
                    await bot.send_message(CHANNEL_ID, message_text, parse_mode="Markdown")
            else:
                # Если не удалось получить данные, используем прямой парсинг HTML
                # Fallback если скрапинг не сработал - пытаемся получить информацию из превью Bandcamp
                try:
                    # Получаем превью из Telegram для URL
                    await message.answer("Primary scraping failed, attempting to get Bandcamp preview...")
                    
                    # Используем тот же User-Agent для получения информации
                    response = requests.get(url, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/109.0.0.0 Safari/537.36'
                    })
                    html = response.text
                    
                    # Извлекаем данные через регулярные выражения из HTML
                    title_match = re.search(r'<title>([^|]+) \| ([^<]+)</title>', html)
                    if title_match:
                        album_name = title_match.group(1).strip()
                        artist_name = title_match.group(2).strip().replace(" | Bandcamp", "")
                    else:
                        album_name = "Unknown Album"
                        artist_name = "Unknown Artist"
                        
                    # Поиск количества треков
                    track_count_match = re.search(r'(\d+) track album', html)
                    if track_count_match:
                        tracks = track_count_match.group(1)
                    else:
                        tracks = "unknown"
                        
                    message_text = f"coma.fm\n" \
                                 f"{artist_name}\n" \
                                 f"{album_name}\n" \
                                 f"{datetime.now().strftime('%Y-%m-%d')}, Album, {tracks} tracks\n" \
                                 f"#bandcamp\n" \
                                 f"🎧 Listen on [Bandcamp]({url})"
                                 
                    await bot.send_message(CHANNEL_ID, message_text, parse_mode="Markdown")
                    
                except Exception as e:
                    logger.error(f"Fallback parsing failed too: {e}", exc_info=True)
                    # Если все методы провалились, публикуем с минимумом информации
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

@router.message(Command("check"))
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
@router.message()
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
                    
                    await message.answer(f"✅ Added album to queue: {artist_name} - {album_name}")
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
                await message.answer(f"✅ Added Spotify album to queue (without validation)")
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
            
            await message.answer(f"✅ Added Bandcamp album to queue: {message.text}")
            return
        
        # Если не найдена ни одна ссылка
        logger.info("No music link found")
    except Exception as e:
        logger.error(f"Error in message handler: {e}", exc_info=True)
        await message.answer(f"❌ Error processing message: {str(e)}")

async def main():
    # Логируем старт
    logger.info("Starting bot application...")
    
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
    
    # Сбрасываем webhook перед запуском
    try:
        await on_startup()
    except Exception as e:
        logger.error(f"Error in startup: {e}")
    
    # Запускаем поллинг
    try:
        logger.info("Starting polling...")
        await dp.start_polling(bot)
    except TelegramConflictError as e:
        logger.error(f"Telegram conflict error: {e}")
        # Ждем перед повторной попыткой
        await asyncio.sleep(10)
        
        # Пробуем снова
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        except Exception as retry_e:
            logger.error(f"Failed to restart after conflict: {retry_e}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Error starting bot: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
