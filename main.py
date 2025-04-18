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
import functools

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
QUEUE_LIST = []  # Для отслеживания элементов в очереди
queue_processing = False
sp = None  # Will be initialized properly
START_TIME = datetime.now()
NEXT_CHECK_TIME = None

try:
    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
    logger.info("Telegram bot initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize Telegram bot: {e}")
    sys.exit(1)

POLL_OPTIONS = ["1", "2", "3", "4", "5"]
POLL_QUESTION = "Rate this release:"
POLL_IS_ANONYMOUS = True

# Собственная реализация экспоненциальной задержки при сбоях API
def retry_with_backoff(max_tries, exceptions=(Exception,)):
    """Декоратор для повторных попыток с экспоненциальной задержкой"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while attempt < max_tries:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    attempt += 1
                    if attempt == max_tries:
                        logger.error(f"All {max_tries} retry attempts failed")
                        raise
                    wait_time = 2 ** attempt  # Экспоненциальная задержка
                    logger.warning(f"Attempt {attempt} failed with error: {e}. Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
        return wrapper
    return decorator

@retry_with_backoff(max_tries=MAX_RETRIES, 
                   exceptions=(spotipy.exceptions.SpotifyException, Exception))
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

@retry_with_backoff(max_tries=MAX_RETRIES, exceptions=(spotipy.exceptions.SpotifyException, Exception))
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

@retry_with_backoff(max_tries=MAX_RETRIES, exceptions=(spotipy.exceptions.SpotifyException, Exception))
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

def process_queue():
    """Process message queue with error handling"""
    global queue_processing
    queue_processing = True
    
    try:
        while not QUEUE.empty():
            item = QUEUE.get()
            try:
                # Отправка сообщения
                if item["image"]:
                    bot.send_photo(TELEGRAM_CHANNEL_ID, photo=item["image"], 
                                   caption=item["message"], parse_mode="Markdown")
                else:
                    bot.send_message(TELEGRAM_CHANNEL_ID, 
                                     item["message"], parse_mode="Markdown")
                
                # Отправка опроса
                time.sleep(2)
                poll_q = f"{POLL_QUESTION} {item['artist']} - {item['release']}"
                bot.send_poll(TELEGRAM_CHANNEL_ID, question=poll_q[:255], 
                              options=POLL_OPTIONS, is_anonymous=POLL_IS_ANONYMOUS)
                
                # Удаляем обработанный элемент из отслеживаемого списка
                for i, queued_item in enumerate(QUEUE_LIST):
                    if queued_item.get("id") == item.get("id"):
                        QUEUE_LIST.pop(i)
                        break
                
                logger.info(f"Sent message for: {item['artist']} - {item['release']}")
                
                # Ожидание перед следующим сообщением
                if not QUEUE.empty():
                    logger.info(f"Waiting {POST_INTERVAL_MINUTES} minutes before next message")
                    time.sleep(POST_INTERVAL_MINUTES * 60)
                    
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                # Перейти к следующему сообщению при ошибке
    except Exception as e:
        logger.error(f"Error in queue processing: {e}")
    
    queue_processing = False
    logger.info("Queue processing completed")

def send_to_telegram(artist, release):
    """Send message to Telegram with proper error handling"""
    try:
        # Подготовка хэштегов
        genres = artist.get("genres", [])
        hashtags = " ".join(convert_to_hashtag(g) for g in genres[:5] if g)
        
        # Форматирование сообщения
        msg = f"*{artist['name']}*\n*{release['name']}*\n{release['release_date']} #{release['type']} {release['total_tracks']} tracks\n{hashtags}\n🎧 Listen on [Spotify]({release['url']})"
        
        # Создаем уникальный ID для элемента очереди
        item_id = f"{artist['id']}_{release['id']}"
        
        # Проверка на дубликаты в очереди
        for item in QUEUE_LIST:
            if item.get("id") == item_id:
                logger.info(f"Release already in queue: {artist['name']} - {release['name']}")
                return
        
        queue_item = {
            "id": item_id,
            "artist": artist["name"],
            "release": release["name"],
            "message": msg,
            "image": release.get("image_url")
        }
        
        QUEUE.put(queue_item)
        QUEUE_LIST.append(queue_item)
        
        logger.info(f"Added to queue: {artist['name']} - {release['name']}. Current queue size: {len(QUEUE_LIST)}")
        
        # Запускаем обработчик очереди, если он не запущен
        global queue_processing
        if not queue_processing:
            threading.Thread(target=process_queue, daemon=True).start()
            logger.info("Started queue processing thread")
            
    except Exception as e:
        logger.error(f"Failed to queue message for Telegram: {e}")

def check_new_releases():
    """Check for new releases with error handling"""
    global NEXT_CHECK_TIME
    
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        last = load_last_releases()
        
        followed_artists = get_followed_artists()
        logger.info(f"Checking releases for {len(followed_artists)} artists")
        
        new_releases_found = 0
        
        for artist in followed_artists:
            aid = artist["id"]
            known = last.get(aid, {}).get("known_releases", [])
            since = last.get(aid, {}).get(
                "last_check_date", 
                (datetime.now() - timedelta(days=INITIAL_CHECK_DAYS)).strftime("%Y-%m-%d")
            )
            
            try:
                releases = get_artist_releases(aid, since)
                if releases:
                    logger.info(f"Found {len(releases)} potential new releases for {artist['name']}")
                
                for release in releases:
                    if release["id"] not in known:
                        logger.info(f"New release found: {artist['name']} - {release['name']} (ID: {release['id']})")
                        send_to_telegram(artist, release)
                        known.append(release["id"])
                        new_releases_found += 1
                
                last[aid] = {
                    "last_check_date": today,
                    "known_releases": known
                }
            except Exception as e:
                logger.error(f"Error processing artist {artist['name']}: {e}")
        
        save_last_releases(last)
        
        # Обновляем время следующей проверки
        NEXT_CHECK_TIME = datetime.now() + timedelta(hours=CHECK_INTERVAL_HOURS)
        logger.info(f"Check completed. Found {new_releases_found} new releases. Next check at {NEXT_CHECK_TIME.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Возвращаем количество найденных релизов
        return new_releases_found
        
    except Exception as e:
        logger.error(f"Check for new releases failed: {e}")
        # В случае ошибки возвращаем -1
        return -1

# Обработчики команд для Telegram бота
@bot.message_handler(commands=['queue'])
def show_queue(message):
    """Показать текущую очередь релизов"""
    try:
        if not QUEUE_LIST:
            bot.send_message(message.chat.id, "Очередь пуста. Нет запланированных релизов.")
            return
        
        current_time = datetime.now()
        queue_info = ["*Очередь релизов:*"]
        
        for i, item in enumerate(QUEUE_LIST, 1):
            eta = f"скоро" if i == 1 else f"через ~{i*POST_INTERVAL_MINUTES} мин"
            queue_info.append(f"{i}. *{item['artist']}* - *{item['release']}* ({eta})")
        
        # Отправка сообщения с информацией о очереди
        bot.send_message(message.chat.id, "\n".join(queue_info), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in show_queue handler: {e}")
        bot.send_message(message.chat.id, "Ошибка при получении информации об очереди.")

# Статус бота и мониторинг
@bot.message_handler(commands=['status'])
def show_status(message):
    """Показать статус бота"""
    try:
        uptime = datetime.now() - START_TIME
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        next_check = NEXT_CHECK_TIME.strftime('%Y-%m-%d %H:%M:%S') if NEXT_CHECK_TIME else 'Не запланирована'
        
        status_info = [
            "*Статус Spotify Telegram бота:*",
            f"Время работы: {days}d {hours}h {minutes}m {seconds}s",
            f"Очередь: {len(QUEUE_LIST)} релизов в ожидании",
            f"Интервал проверки: каждые {CHECK_INTERVAL_HOURS} часов",
            f"Интервал публикации: каждые {POST_INTERVAL_MINUTES} минут",
            f"Следующая проверка новых релизов: {next_check}"
        ]
        
        # Отправка сообщения со статусом
        bot.send_message(message.chat.id, "\n".join(status_info), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in show_status handler: {e}")
        bot.send_message(message.chat.id, "Ошибка при получении статуса бота.")

# Команда для очистки очереди
@bot.message_handler(commands=['clear_queue'])
def clear_queue(message):
    """Очистить очередь публикации"""
    try:
        global QUEUE_LIST
        
        if not QUEUE_LIST:
            bot.send_message(message.chat.id, "Очередь уже пуста.")
            return
        
        queue_size = len(QUEUE_LIST)
        
        # Очистка очереди
        with QUEUE.mutex:
            QUEUE.queue.clear()
        QUEUE_LIST.clear()
        
        logger.info(f"Queue cleared by user {message.from_user.username} (ID: {message.from_user.id}). {queue_size} items removed.")
        bot.send_message(message.chat.id, f"Очередь очищена. Удалено {queue_size} релизов из очереди публикации.")
    except Exception as e:
        logger.error(f"Error in clear_queue handler: {e}")
        bot.send_message(message.chat.id, "Ошибка при очистке очереди.")

# Команда для ручного запуска проверки новых релизов
@bot.message_handler(commands=['check_now'])
def manual_check(message):
    """Запустить проверку новых релизов вручную"""
    try:
        # Отправляем уведомление о начале проверки
        bot.send_message(message.chat.id, "Запуск проверки новых релизов...")
        logger.info(f"Manual check triggered by user {getattr(message.from_user, 'username', 'Unknown')} (ID: {message.from_user.id})")
        
        # Проверяем напрямую вместо запуска отдельного потока
        try:
            # Принудительно обновляем токен Spotify перед проверкой
            global sp
            sp = initialize_spotify()
            logger.info("Spotify token refreshed before manual check")
            
            # Запускаем проверку
            check_result = check_new_releases()
            
            # Отправляем отчет о результате
            bot.send_message(message.chat.id, f"Проверка новых релизов завершена. Найдено новых релизов: {check_result}")
        except Exception as check_error:
            logger.error(f"Error during manual check: {check_error}")
            bot.send_message(message.chat.id, f"Ошибка при проверке релизов: {str(check_error)}")
    except Exception as e:
        logger.error(f"Error in manual_check handler: {e}")
        try:
            bot.send_message(message.chat.id, "Произошла ошибка при запуске проверки релизов.")
        except:
            pass

# Команда для помощи
@bot.message_handler(commands=['help'])
def show_help(message):
    """Показать список доступных команд"""
    try:
        help_text = [
            "*Доступные команды:*",
            "/queue - Показать текущую очередь публикации релизов",
            "/status - Показать статус бота",
            "/clear_queue - Очистить очередь публикации",
            "/check_now - Запустить проверку новых релизов",
            "/help - Показать эту справку"
        ]
        
        bot.send_message(message.chat.id, "\n".join(help_text), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in show_help handler: {e}")
        bot.send_message(message.chat.id, "Ошибка при отображении справки.")

# Обработчик текстовых сообщений
@bot.message_handler(func=lambda message: True)
def echo_message(message):
    """Обработчик всех остальных сообщений"""
    try:
        bot.send_message(message.chat.id, "Используйте /help для просмотра доступных команд.")
    except Exception as e:
        logger.error(f"Error in echo_message handler: {e}")


def run_bot():
    """Main bot function with simplified and robust architecture"""
    global sp
    
    logger.info("Starting Spotify Telegram Bot")
    
    try:
        # Инициализация Spotify клиента
        sp = initialize_spotify()
        if not sp:
            logger.error("Failed to initialize Spotify client")
            return
        
        logger.info(f"Bot configured to check every {CHECK_INTERVAL_HOURS} hour(s)")
        
        # Запуск проверки релизов в отдельном потоке
        def check_releases_periodically():
            try:
                # Первая проверка при запуске
                logger.info("Running initial check for new releases")
                check_new_releases()
                
                # Периодическая проверка
                while True:
                    logger.info(f"Waiting {CHECK_INTERVAL_HOURS} hours until next check")
                    time.sleep(CHECK_INTERVAL_HOURS * 3600)
                    logger.info("Running scheduled check for new releases")
                    check_new_releases()
            except Exception as e:
                logger.error(f"Error in check thread: {e}")
        
        # Запуск потока периодической проверки релизов
        check_thread = threading.Thread(target=check_releases_periodically, daemon=True)
        check_thread.start()
        logger.info("Started release check thread")
        
        # Обновление токена Spotify каждые 50 минут
        def refresh_token_periodically():
            try:
                while True:
                    time.sleep(50 * 60)  # 50 минут
                    logger.info("Refreshing Spotify token")
                    global sp
                    sp = initialize_spotify()
                    logger.info("Spotify token refreshed")
            except Exception as e:
                logger.error(f"Error in token refresh thread: {e}")
        
        # Запуск потока обновления токена
        token_thread = threading.Thread(target=refresh_token_periodically, daemon=True)
        token_thread.start()
        logger.info("Started token refresh thread")
        
        # Запуск Telegram бота в основном потоке
        logger.info("Starting Telegram bot polling")
        bot.infinity_polling(timeout=60, long_polling_timeout=30)
        
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
