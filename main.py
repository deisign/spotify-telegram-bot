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
except Exception as e:
    logger.error(f"Failed to initialize Telegram bot: {e}")
    sys.exit(1)

POLL_OPTIONS = ["1", "2", "3", "4", "5"]
POLL_QUESTION = "Rate this release:"
POLL_IS_ANONYMOUS = True

MESSAGE_TEMPLATE = """*{artist_name}*
*{release_name}*
{release_date} #{release_type_tag} {total_tracks} tracks
{genres_hashtags}
🎧 Listen on [Spotify]({release_url})"""

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

@retry_with_backoff(max_tries=MAX_RETRIES, exceptions=(Exception,))
def send_to_telegram(artist, release):
    """Send message to Telegram with proper error handling"""
    try:
        # Подготовка хэштегов
        genres = artist.get("genres", [])
        hashtags = " ".join(convert_to_hashtag(g) for g in genres[:5] if g)
        
        # Экранирование специальных символов Markdown для безопасного форматирования
        def escape_markdown(text):
            # Экранирование символов: _ * [ ] ( ) ~ ` > # + - = | { } . !
            symbols = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
            for symbol in symbols:
                text = text.replace(symbol, f'\\{symbol}')
            return text
        
        # Безопасное форматирование с экранированием специальных символов
        artist_name = escape_markdown(artist["name"])
        release_name = escape_markdown(release["name"])
        
        # Формирование сообщения
        msg = f"*{artist_name}*\n*{release_name}*\n{release['release_date']} #{convert_to_hashtag(release['type']).replace('#', '')} {release['total_tracks']} tracks\n{hashtags}\n🎧 Listen on [Spotify]({release['url']})"
        
        queue_item = {
            "artist": artist["name"],
            "release": release["name"],
            "message": msg,
            "image": release.get("image_url"),
            "scheduled_time": datetime.now() + timedelta(minutes=len(QUEUE_LIST) * POST_INTERVAL_MINUTES)
        }
        
        QUEUE.put(queue_item)
        QUEUE_LIST.append(queue_item)  # Добавляем в отслеживаемый список
        
        logger.info(f"Added to queue: {artist['name']} - {release['name']}. Current queue size: {len(QUEUE_LIST)}")
        
        global queue_processing
        if not queue_processing:
            threading.Thread(target=process_queue, daemon=True).start()
    except Exception as e:
        logger.error(f"Failed to queue message for Telegram: {e}")
        raise

def process_queue():
    """Process message queue with error handling"""
    global queue_processing, QUEUE_LIST
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
            
            # Удаляем обработанный элемент из отслеживаемого списка
            for i, queued_item in enumerate(QUEUE_LIST):
                if queued_item["artist"] == item["artist"] and queued_item["release"] == item["release"]:
                    QUEUE_LIST.pop(i)
                    break
            
            logger.info(f"Successfully sent message for: {item['artist']} - {item['release']}. Remaining queue: {len(QUEUE_LIST)}")
            
            # Обновляем предполагаемое время публикации для оставшихся элементов
            current_time = datetime.now()
            for i, queued_item in enumerate(QUEUE_LIST):
                queued_item["scheduled_time"] = current_time + timedelta(minutes=i * POST_INTERVAL_MINUTES)
            
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
                # Не удаляем из QUEUE_LIST, так как элемент возвращается в очередь
                time.sleep(60)  # Ожидание минуту перед повторной попыткой
            else:
                # Если превышено количество попыток, удаляем из QUEUE_LIST
                for i, queued_item in enumerate(QUEUE_LIST):
                    if queued_item["artist"] == item["artist"] and queued_item["release"] == item["release"]:
                        QUEUE_LIST.pop(i)
                        break
    
    queue_processing = False
    logger.info("Message queue processing completed")

def check_new_releases():
    """Check for new releases with error handling"""
    global NEXT_CHECK_TIME
    
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
        
        # Обновляем время следующей проверки
        NEXT_CHECK_TIME = datetime.now() + timedelta(hours=CHECK_INTERVAL_HOURS)
        logger.info(f"Check for new releases completed successfully. Next check at {NEXT_CHECK_TIME.strftime('%Y-%m-%d %H:%M:%S')}")
        
    except Exception as e:
        logger.error(f"Check for new releases failed: {e}")

# Обработчики команд для Telegram бота
@bot.message_handler(commands=['queue'])
def show_queue(message):
    """Показать текущую очередь релизов"""
    # Проверка авторизации (только определенные пользователи могут видеть очередь)
    # Если нужно ограничить доступ к команде, раскомментируйте следующие строки
    # if message.from_user.id not in AUTHORIZED_USERS:
    #     bot.reply_to(message, "У вас нет прав на просмотр очереди.")
    #     return
    
    if not QUEUE_LIST:
        bot.reply_to(message, "Очередь пуста. Нет запланированных релизов.")
        return
    
    current_time = datetime.now()
    queue_info = ["*Очередь релизов:*"]
    
    for i, item in enumerate(QUEUE_LIST, 1):
        publish_time = item["scheduled_time"]
        time_diff = publish_time - current_time
        minutes_left = max(0, int(time_diff.total_seconds() / 60))
        
        if minutes_left == 0:
            eta = "публикуется сейчас"
        else:
            hours = minutes_left // 60
            mins = minutes_left % 60
            if hours > 0:
                eta = f"через {hours}ч {mins}м"
            else:
                eta = f"через {mins}м"
        
        # Экранирование для MarkdownV2
        artist = item['artist'].replace('.', '\\.').replace('-', '\\-').replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)')
        release = item['release'].replace('.', '\\.').replace('-', '\\-').replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)')
        eta_escaped = eta.replace('.', '\\.').replace('-', '\\-').replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)')
        
        queue_info.append(f"{i}\\. *{artist}* \\- *{release}* \\({eta_escaped}\\)")
    
    # Отправка сообщения с информацией о очереди
    bot.reply_to(message, "\n".join(queue_info), parse_mode="MarkdownV2")

# Статус бота и мониторинг
@bot.message_handler(commands=['status'])
def show_status(message):
    """Показать статус бота"""
    # Проверка авторизации если нужно
    
    uptime = datetime.now() - START_TIME
    days = uptime.days
    hours, remainder = divmod(uptime.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    next_check = NEXT_CHECK_TIME.strftime('%Y\\-%m\\-%d %H:%M:%S') if NEXT_CHECK_TIME else 'Не запланирована'
    
    status_info = [
        "*Статус Spotify Telegram бота:*",
        f"Время работы: {days}d {hours}h {minutes}m {seconds}s",
        f"Очередь: {len(QUEUE_LIST)} релизов в ожидании",
        f"Интервал проверки: каждые {CHECK_INTERVAL_HOURS} часов",
        f"Интервал публикации: каждые {POST_INTERVAL_MINUTES} минут",
        f"Следующая проверка новых релизов: {next_check}"
    ]
    
    # Отправка сообщения со статусом
    bot.reply_to(message, "\n".join(status_info), parse_mode="MarkdownV2")

# Команда для очистки очереди
@bot.message_handler(commands=['clear_queue'])
def clear_queue(message):
    """Очистить очередь публикации"""
    # Проверка авторизации (только определенные пользователи могут очищать очередь)
    # Если нужно ограничить доступ к команде, раскомментируйте следующие строки
    # if message.from_user.id not in AUTHORIZED_USERS:
    #     bot.reply_to(message, "У вас нет прав на очистку очереди.")
    #     return
    
    global QUEUE_LIST
    
    if not QUEUE_LIST:
        bot.reply_to(message, "Очередь уже пуста.")
        return
    
    queue_size = len(QUEUE_LIST)
    
    # Очистка очереди
    with QUEUE.mutex:
        QUEUE.queue.clear()
    QUEUE_LIST.clear()
    
    logger.info(f"Queue cleared by user {message.from_user.username} (ID: {message.from_user.id}). {queue_size} items removed.")
    bot.reply_to(message, f"Очередь очищена. Удалено {queue_size} релизов из очереди публикации.")

# Команда для ручного запуска проверки новых релизов
@bot.message_handler(commands=['check_now'])
def manual_check(message):
    """Запустить проверку новых релизов вручную"""
    # Проверка авторизации
    # if message.from_user.id not in AUTHORIZED_USERS:
    #     bot.reply_to(message, "У вас нет прав на запуск проверки.")
    #     return
    
    bot.reply_to(message, "Запуск проверки новых релизов...")
    logger.info(f"Manual check triggered by user {message.from_user.username} (ID: {message.from_user.id})")
    
    # Запуск проверки в отдельном потоке
    threading.Thread(target=check_new_releases, daemon=True).start()

# Команда для помощи
@bot.message_handler(commands=['help'])
def show_help(message):
    """Показать список доступных команд"""
    help_text = [
        "*Доступные команды:*",
        "/queue \\- Показать текущую очередь публикации релизов",
        "/status \\- Показать статус бота",
        "/clear\\_queue \\- Очистить очередь публикации",
        "/check\\_now \\- Запустить проверку новых релизов",
        "/help \\- Показать эту справку"
    ]
    
    bot.reply_to(message, "\n".join(help_text), parse_mode="MarkdownV2")

# Обработчик текстовых сообщений
@bot.message_handler(func=lambda message: True)
def echo_message(message):
    """Обработчик всех остальных сообщений"""
    bot.reply_to(message, "Используйте /help для просмотра доступных команд.")

def run_bot():
    """Main bot function with improved error handling"""
    global sp, NEXT_CHECK_TIME
    
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
        
        # Запускаем отдельный поток для обработки сообщений бота
        polling_thread = threading.Thread(target=start_bot_polling, daemon=True)
        polling_thread.start()
        logger.info("Telegram bot polling thread started")
        
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

def start_bot_polling():
    """Start polling in a safe way with restart capability"""
    while True:
        try:
            logger.info("Starting Telegram bot polling")
            bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(10)  # Пауза перед повторной попыткой
        else:
            # Если функция завершилась без исключения, прерываем цикл
            break

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
