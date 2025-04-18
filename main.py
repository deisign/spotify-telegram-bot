import os
import logging
import time
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import telebot
import json
import queue
import threading
import schedule
import random
import re
from datetime import datetime, timedelta
import sys
import backoff

# Logging
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("spotify_telegram_bot.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Проверка обязательных переменных окружения
REQUIRED_ENV_VARS = [
    "SPOTIFY_CLIENT_ID", 
    "SPOTIFY_CLIENT_SECRET", 
    "SPOTIFY_REFRESH_TOKEN", 
    "TELEGRAM_BOT_TOKEN", 
    "TELEGRAM_CHANNEL_ID"
]

missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
    sys.exit(1)

# ENV
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "https://example.com/callback")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", 3))
POST_INTERVAL_MINUTES = int(os.getenv("POST_INTERVAL_MINUTES", 60))
INITIAL_CHECK_DAYS = int(os.getenv("INITIAL_CHECK_DAYS", 7))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 5))

# Spotipy config
os.environ["SPOTIPY_CLIENT_ID"] = SPOTIFY_CLIENT_ID
os.environ["SPOTIPY_CLIENT_SECRET"] = SPOTIFY_CLIENT_SECRET
os.environ["SPOTIPY_REDIRECT_URI"] = SPOTIFY_REDIRECT_URI

# Global
DATA_FILE = "last_releases.json"
QUEUE = queue.Queue()
queue_processing = False
sp = None  # Will be initialized properly

try:
    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
except Exception as e:
    logger.error(f"Failed to initialize Telegram bot: {e}")
    sys.exit(1)

POLL_OPTIONS = ["1", "2", "3", "4", "5"]
POLL_QUESTION = "Rate this release:"
POLL_IS_ANONYMOUS = False

MESSAGE_TEMPLATE = """{artist_name}
{release_name}
{release_date} #{release_type_tag} {total_tracks} tracks
{genres_hashtags}
🎧 Listen on Spotify: {release_url}"""

# Экспоненциальная задержка при сбоях API
@backoff.on_exception(backoff.expo, 
                     (spotipy.exceptions.SpotifyException, 
                      telebot.apihelper.ApiHTTPException),
                     max_tries=MAX_RETRIES)
def initialize_spotify():
    """Initialize Spotify client with proper error handling and retry"""
    try:
        auth = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="user-follow-read user-library-read",
            open_browser=False
        )
        token_info = auth.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
        return spotipy.Spotify(auth=token_info["access_token"])
    except Exception as e:
        logger.error(f"Spotify authentication failed: {e}")
        raise

def convert_to_hashtag(text):
    """Convert text to a valid hashtag"""
    if not text:
        return ""
    return "#" + re.sub(r"[^\w\s]", "", text).replace(" ", "_").lower()

def load_last_releases():
    """Load last processed releases from file with error handling"""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load releases data: {e}")
    return {}

def save_last_releases(data):
    """Save processed releases to file with error handling"""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to save releases data: {e}")

@backoff.on_exception(backoff.expo, spotipy.exceptions.SpotifyException, max_tries=MAX_RETRIES)
def get_followed_artists():
    """Get list of followed artists with retries for API failures"""
    followed = []
    try:
        results = sp.current_user_followed_artists(limit=50)
        while results:
            for a in results["artists"]["items"]:
                genres = a.get("genres", [])
                followed.append({
                    "id": a["id"],
                    "name": a["name"],
                    "genres": genres
                })
            if results["artists"]["next"]:
                results = sp.next(results["artists"])
            else:
                break
        logger.info(f"Found {len(followed)} followed artists")
        return followed
    except Exception as e:
        logger.error(f"Failed to get followed artists: {e}")
        raise

@backoff.on_exception(backoff.expo, spotipy.exceptions.SpotifyException, max_tries=MAX_RETRIES)
def get_artist_releases(artist_id, since_date):
    """Get artist releases with improved date handling and retries"""
    releases = []
    try:
        results = sp.artist_albums(artist_id, album_type="album,single", country="US", limit=50)
        
        for r in results["items"]:
            release_date = r["release_date"]
            
            # Преобразование дат в полный формат для корректного сравнения
            parsed_date = None
            if len(release_date) == 4:  # Только год
                parsed_date = f"{release_date}-12-31"  # Берем конец года для включения всех релизов
            elif len(release_date) == 7:  # Год и месяц
                # Определяем последний день месяца
                year, month = map(int, release_date.split('-'))
                if month in [1, 3, 5, 7, 8, 10, 12]:
                    last_day = 31
                elif month in [4, 6, 9, 11]:
                    last_day = 30
                else:  # Февраль
                    # Проверка на високосный год
                    if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
                        last_day = 29
                    else:
                        last_day = 28
                parsed_date = f"{release_date}-{last_day}"
            else:
                parsed_date = release_date
            
            # Используем оригинальную дату для отображения, но полную для сравнения
            if parsed_date >= since_date:
                # Получаем дополнительную информацию о релизе
                try:
                    full_album = sp.album(r["id"])
                    popularity = full_album.get("popularity", 0)
                except Exception as e:
                    logger.warning(f"Failed to get full album info for {r['id']}: {e}")
                    popularity = 0
                
                releases.append({
                    "id": r["id"],
                    "name": r["name"],
                    "release_date": release_date,  # Оригинальная дата для отображения
                    "type": r["album_type"],
                    "url": r["external_urls"]["spotify"],
                    "image_url": r["images"][0]["url"] if r["images"] else None,
                    "total_tracks": r.get("total_tracks", 0),
                    "popularity": popularity
                })
        
        return releases
    except Exception as e:
        logger.error(f"Failed to get releases for artist {artist_id}: {e}")
        raise

@backoff.on_exception(backoff.expo, telebot.apihelper.ApiHTTPException, max_tries=MAX_RETRIES)
def send_to_telegram(artist, release):
    """Send message to Telegram with proper error handling"""
    try:
        # Подготовка хэштегов с экранированием специальных символов для MarkdownV2
        genres = artist.get("genres", [])
        hashtags = " ".join(convert_to_hashtag(g) for g in genres[:5] if g)
        
        # Экранирование специальных символов для MarkdownV2
        artist_name = artist["name"].replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
        release_name = release["name"].replace("_", "\\_").replace("*", "\\*").replace("[", "\\[").replace("`", "\\`")
        
        msg = MESSAGE_TEMPLATE.format(
            artist_name=artist_name,
            release_name=release_name,
            release_date=release["release_date"],
            release_type_tag=convert_to_hashtag(release["type"]),
            total_tracks=release["total_tracks"],
            genres_hashtags=hashtags,
            release_url=release["url"]
        )
        
        QUEUE.put({
            "artist": artist["name"],
            "release": release["name"],
            "message": msg,
            "image": release.get("image_url")
        })
        
        global queue_processing
        if not queue_processing:
            threading.Thread(target=process_queue, daemon=True).start()
    except Exception as e:
        logger.error(f"Failed to queue message for Telegram: {e}")
        raise

def process_queue():
    """Process message queue with error handling"""
    global queue_processing
    queue_processing = True
    
    while not QUEUE.empty():
        item = QUEUE.get()
        try:
            # Отправка сообщения с картинкой или без
            if item["image"]:
                bot.send_photo(
                    TELEGRAM_CHANNEL_ID, 
                    photo=item["image"], 
                    caption=item["message"], 
                    parse_mode="MarkdownV2"
                )
            else:
                bot.send_message(
                    TELEGRAM_CHANNEL_ID, 
                    item["message"], 
                    parse_mode="MarkdownV2"
                )
            
            # Отправка опроса
            time.sleep(2)  # Небольшая пауза между сообщениями
            poll_q = f"{POLL_QUESTION} {item['artist']} - {item['release']}"
            bot.send_poll(
                TELEGRAM_CHANNEL_ID, 
                question=poll_q[:255],  # Ограничение длины вопроса
                options=POLL_OPTIONS, 
                is_anonymous=POLL_IS_ANONYMOUS
            )
            
            logger.info(f"Successfully sent message for: {item['artist']} - {item['release']}")
            
            # Ожидание перед отправкой следующего сообщения
            if not QUEUE.empty():
                logger.info(f"Waiting {POST_INTERVAL_MINUTES} minutes before sending next message")
                time.sleep(POST_INTERVAL_MINUTES * 60)
                
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            # Возвращаем в очередь при ошибке (не более 3 раз)
            retries = item.get("retries", 0)
            if retries < 3:
                item["retries"] = retries + 1
                logger.info(f"Requeueing message (retry {retries + 1}/3)")
                QUEUE.put(item)
                time.sleep(60)  # Ожидание минуту перед повторной попыткой
    
    queue_processing = False
    logger.info("Message queue processing completed")

def check_new_releases():
    """Check for new releases with error handling"""
    logger.info("Checking new releases...")
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        last = load_last_releases()
        
        followed_artists = get_followed_artists()
        logger.info(f"Checking releases for {len(followed_artists)} artists")
        
        for artist in followed_artists:
            aid = artist["id"]
            known = last.get(aid, {}).get("known_releases", [])
            since = last.get(aid, {}).get(
                "last_check_date", 
                (datetime.now() - timedelta(days=INITIAL_CHECK_DAYS)).strftime("%Y-%m-%d")
            )
            
            try:
                releases = get_artist_releases(aid, since)
                logger.info(f"Found {len(releases)} potential new releases for {artist['name']}")
                
                for release in releases:
                    if release["id"] not in known:
                        logger.info(f"New release found: {artist['name']} - {release['name']}")
                        send_to_telegram(artist, release)
                        known.append(release["id"])
                
                last[aid] = {
                    "last_check_date": today,
                    "known_releases": known
                }
            except Exception as e:
                logger.error(f"Error processing artist {artist['name']}: {e}")
                # Продолжаем с другими артистами даже при ошибке
        
        save_last_releases(last)
        logger.info("Check for new releases completed successfully")
    except Exception as e:
        logger.error(f"Check for new releases failed: {e}")

def run_bot():
    """Main bot function with improved error handling"""
    global sp
    
    logger.info("Starting Spotify Telegram Bot")
    
    try:
        # Инициализация Spotify клиента
        sp = initialize_spotify()
        if not sp:
            logger.error("Failed to initialize Spotify client")
            return
        
        logger.info(f"Bot configured to check every {CHECK_INTERVAL_HOURS} hour(s)")
        
        # Первая проверка при запуске
        check_new_releases()
        
        # Планировщик регулярных проверок
        schedule.every(CHECK_INTERVAL_HOURS).hours.do(check_new_releases)
        
        # Обновление токена каждые 50 минут (токен действителен 1 час)
        def refresh_spotify_token():
            global sp
            logger.info("Refreshing Spotify token")
            try:
                sp = initialize_spotify()
                logger.info("Spotify token refreshed successfully")
            except Exception as e:
                logger.error(f"Failed to refresh Spotify token: {e}")
        
        schedule.every(50).minutes.do(refresh_spotify_token)
        
        logger.info("Bot is running. Press Ctrl+C to stop.")
        
        # Основной цикл с обработкой прерывания
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")
            # Перезапуск цикла после непредвиденной ошибки
            time.sleep(60)
            run_bot()
            
    except Exception as e:
        logger.error(f"Bot initialization failed: {e}")

if __name__ == "__main__":
    # Установка обработчика неперехваченных исключений
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
    
    sys.excepthook = handle_exception
    
    # Запуск бота с защитой от падения
    try:
        run_bot()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        sys.exit(1)
