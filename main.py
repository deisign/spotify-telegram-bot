import logging
import time
import threading
import traceback
import os
import spotipy
import telebot
from spotipy.oauth2 import SpotifyOAuth
from flask import Flask, request, abort

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получаем данные из переменных окружения Railway
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI')
SPOTIFY_REFRESH_TOKEN = os.environ.get('SPOTIFY_REFRESH_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
CHECK_INTERVAL_HOURS = int(os.environ.get('CHECK_INTERVAL_HOURS', 3))

# Проверяем наличие токена бота
if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не найден в переменных окружения!")
    raise ValueError("TELEGRAM_BOT_TOKEN не определен в переменных окружения!")

# Инициализация бота Telegram
logger.info(f"Инициализация бота с токеном (первые 5 символов): {TELEGRAM_BOT_TOKEN[:5]}***")
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Инициализация Flask приложения
app = Flask(__name__)

# Инициализация Spotify OAuth
sp_oauth = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI
)

# Инициализация переменной для хранения объекта Spotify
spotify_refresh_token = SPOTIFY_REFRESH_TOKEN
sp = None  # Будет инициализирован при первом обновлении токена

# Структура для хранения очереди публикаций
queue = []

# Функции для работы с очередью
def add_to_queue(item):
    """Добавляет элемент в очередь публикаций"""
    queue.append(item)
    logger.info(f"Добавлено в очередь: {item}")
    
def get_queue():
    """Возвращает текущую очередь публикаций"""
    return queue

def remove_from_queue(index):
    """Удаляет элемент из очереди по индексу"""
    if 0 <= index < len(queue):
        item = queue.pop(index)
        logger.info(f"Удалено из очереди: {item}")
        return True, item
    return False, None

# Функция для обработки ссылок Spotify
def process_spotify_link(url):
    """Обрабатывает ссылку Spotify и возвращает информацию о релизе"""
    try:
        logger.info(f"Обработка ссылки Spotify: {url}")
        
        # Проверка, что это действительно ссылка Spotify
        if "spotify.com" not in url:
            return None, "Это не ссылка Spotify"
        
        # Обработка разных типов ссылок Spotify
        if "/album/" in url:
            # Ссылка на альбом
            album_id = url.split("/album/")[1].split("?")[0]
            
            # Если Spotify API не инициализирован, возвращаем базовую информацию
            if sp is None:
                return {
                    "type": "release",
                    "album_id": album_id,
                    "url": url,
                    "source": "manual"
                }, None
            
            # Если API инициализирован, получаем дополнительную информацию
            try:
                album_info = sp.album(album_id)
                return {
                    "type": "release",
                    "artist": album_info["artists"][0]["name"],
                    "album": album_info["name"],
                    "album_id": album_id,
                    "url": url,
                    "release_date": album_info.get("release_date"),
                    "source": "manual"
                }, None
            except Exception as e:
                logger.error(f"Ошибка при получении информации о альбоме из Spotify: {e}")
                return {
                    "type": "release",
                    "album_id": album_id,
                    "url": url,
                    "source": "manual"
                }, None
        
        elif "/track/" in url:
            # Ссылка на трек
            track_id = url.split("/track/")[1].split("?")[0]
            
            # Базовая информация без API
            if sp is None:
                return {
                    "type": "track",
                    "track_id": track_id,
                    "url": url,
                    "source": "manual"
                }, None
            
            # Если API инициализирован, получаем дополнительную информацию
            try:
                track_info = sp.track(track_id)
                return {
                    "type": "track",
                    "artist": track_info["artists"][0]["name"],
                    "track": track_info["name"],
                    "album": track_info["album"]["name"],
                    "track_id": track_id,
                    "url": url,
                    "source": "manual"
                }, None
            except Exception as e:
                logger.error(f"Ошибка при получении информации о треке из Spotify: {e}")
                return {
                    "type": "track",
                    "track_id": track_id,
                    "url": url,
                    "source": "manual"
                }, None
        
        elif "/artist/" in url:
            # Ссылка на артиста
            artist_id = url.split("/artist/")[1].split("?")[0]
            
            # Базовая информация без API
            if sp is None:
                return {
                    "type": "artist",
                    "artist_id": artist_id,
                    "url": url,
                    "source": "manual"
                }, None
            
            # Если API инициализирован, получаем дополнительную информацию
            try:
                artist_info = sp.artist(artist_id)
                return {
                    "type": "artist",
                    "artist": artist_info["name"],
                    "artist_id": artist_id,
                    "url": url,
                    "source": "manual"
                }, None
            except Exception as e:
                logger.error(f"Ошибка при получении информации о артисте из Spotify: {e}")
                return {
                    "type": "artist",
                    "artist_id": artist_id,
                    "url": url,
                    "source": "manual"
                }, None
        
        else:
            # Другой тип ссылки Spotify
            return {
                "type": "unknown",
                "url": url,
                "source": "manual"
            }, None
            
    except Exception as e:
        logger.error(f"Ошибка при обработке ссылки Spotify: {e}")
        return None, f"Ошибка при обработке ссылки: {str(e)}"

# Функция для проверки новых релизов подписанных артистов
def check_followed_artists_releases():
    try:
        logger.info("Проверяем новые релизы артистов...")
        
        # Проверяем, есть ли доступ к Spotify API
        if sp is None:
            logger.error("API Spotify не инициализирован. Невозможно проверить новые релизы.")
            return
            
        # Получаем список подписанных артистов
        logger.info("Получаем список подписанных артистов...")
        try:
            # Ограничиваем количество подписок для проверки (максимум 50)
            followed_artists = sp.current_user_followed_artists(limit=50)
            
            if not followed_artists or 'artists' not in followed_artists or 'items' not in followed_artists['artists']:
                logger.info("Нет подписанных артистов или ошибка при получении списка")
                return
                
            artists = followed_artists['artists']['items']
            logger.info(f"Получен список из {len(artists)} подписанных артистов")
            
            # Проверяем новые релизы для каждого артиста
            for artist in artists:
                artist_id = artist['id']
                artist_name = artist['name']
                
                logger.info(f"Проверяем новые релизы для артиста: {artist_name}")
                
                # Получаем последние альбомы артиста
                albums = sp.artist_albums(artist_id, album_type='album,single', limit=3)
                
                if not albums or 'items' not in albums:
                    logger.info(f"Нет доступных альбомов для артиста {artist_name}")
                    continue
                    
                # Проверяем каждый альбом
                for album in albums['items']:
                    album_id = album['id']
                    album_name = album['name']
                    album_type = album['album_type']
                    release_date = album['release_date']
                    
                    # Проверяем, является ли релиз новым (за последние 7 дней)
                    try:
                        release_date_obj = None
                        if len(release_date) == 10:  # Формат YYYY-MM-DD
                            release_date_obj = time.strptime(release_date, "%Y-%m-%d")
                        elif len(release_date) == 7:  # Формат YYYY-MM
                            release_date_obj = time.strptime(f"{release_date}-01", "%Y-%m-%d")
                        elif len(release_date) == 4:  # Формат YYYY
                            release_date_obj = time.strptime(f"{release_date}-01-01", "%Y-%m-%d")
                        
                        if release_date_obj:
                            release_timestamp = time.mktime(release_date_obj)
                            current_timestamp = time.time()
                            days_since_release = (current_timestamp - release_timestamp) / (60 * 60 * 24)
                            
                            # Если релиз не старше 7 дней, добавляем его в очередь
                            if days_since_release <= 7:
                                logger.info(f"Найден новый релиз: {artist_name} - {album_name} ({release_date})")
                                
                                # Получаем полную информацию об альбоме
                                album_info = sp.album(album_id)
                                
                                # Создаем элемент для очереди
                                release_item = {
                                    "type": "release",
                                    "artist": artist_name,
                                    "album": album_name,
                                    "album_id": album_id,
                                    "release_date": release_date,
                                    "album_type": album_type,
                                    "total_tracks": album_info.get("total_tracks", 0),
                                    "url": album['external_urls']['spotify'],
                                    "source": "auto",
                                    "artist_id": artist_id
                                }
                                
                                # Проверяем, есть ли уже такой релиз в очереди
                                duplicate = False
                                for item in queue:
                                    if isinstance(item, dict) and item.get("album_id") == album_id:
                                        duplicate = True
                                        break
                                
                                if not duplicate:
                                    # Добавляем в очередь
                                    add_to_queue(release_item)
                            else:
                                logger.debug(f"Релиз {artist_name} - {album_name} слишком старый ({days_since_release:.1f} дней)")
                    except Exception as e:
                        logger.error(f"Ошибка при обработке даты релиза для {artist_name} - {album_name}: {e}")
                        
        except Exception as e:
            logger.error(f"Ошибка при получении подписанных артистов: {e}")
            logger.error(traceback.format_exc())
            
    except Exception as e:
        logger.error(f"Ошибка при проверке релизов: {e}")
        logger.error(traceback.format_exc())

def check_and_post_from_queue():
    try:
        if not queue:
            # Очередь пуста
            return
            
        # Берем первый элемент из очереди и публикуем его в канал
        item = queue[0]
        logger.info(f"Публикация из очереди: {item}")
        
        try:
            # Пытаемся опубликовать в канал в соответствии с требуемым форматом
            if isinstance(item, dict):
                if item.get("type") == "release":
                    # Извлекаем доступную информацию
                    artist = item.get("artist", "")
                    album = item.get("album", "")
                    release_date = item.get("release_date", "")
                    album_type = "Альбом" # По умолчанию
                    track_count = ""
                    genres = []
                    
                    # Если есть доступ к Spotify API и есть album_id, получаем дополнительную информацию
                    if sp and "album_id" in item:
                        try:
                            album_info = sp.album(item["album_id"])
                            if not artist and "artists" in album_info and album_info["artists"]:
                                artist = album_info["artists"][0]["name"]
                            if not album and "name" in album_info:
                                album = album_info["name"]
                            if not release_date and "release_date" in album_info:
                                release_date = album_info["release_date"]
                            if "album_type" in album_info:
                                album_type = "Сингл" if album_info["album_type"] == "single" else "Альбом"
                            if "total_tracks" in album_info:
                                track_count = f", {album_info['total_tracks']} треков"
                            
                            # Получаем жанры артиста
                            if "artists" in album_info and album_info["artists"]:
                                artist_id = album_info["artists"][0]["id"]
                                artist_info = sp.artist(artist_id)
                                if "genres" in artist_info and artist_info["genres"]:
                                    genres = ["#" + genre.replace(" ", "_") for genre in artist_info["genres"][:3]]
                        except Exception as e:
                            logger.error(f"Ошибка при получении дополнительной информации из Spotify: {e}")
                    
                    # Форматирование в соответствии с требованиями
                    message = f"<b>{artist}</b>\n<b>{album}</b>\n"
                    
                    # Добавляем дату, тип и количество треков если есть
                    details = []
                    if release_date:
                        details.append(release_date)
                    if album_type:
                        details.append(album_type)
                    if track_count:
                        details.append(track_count)
                    
                    if details:
                        message += f"{', '.join(details)}\n"
                    
                    # Добавляем жанры, если есть
                    if genres:
                        message += f"Жанры: {' '.join(genres)}\n"
                    
                    # Добавляем ссылку
                    message += f"\n{item['url']}"
                    
                elif item.get("type") == "track" and "artist" in item and "track" in item:
                    # Формат для трека - используем тот же формат, что и для релиза
                    artist = item.get("artist", "")
                    track = item.get("track", "")
                    album = item.get("album", "")
                    
                    message = f"<b>{artist}</b>\n<b>{track}</b>\n"
                    if album:
                        message += f"Из альбома: {album}\n"
                    
                    # Пытаемся получить жанры, если доступен Spotify API
                    genres = []
                    if sp and "artist_id" in item:
                        try:
                            artist_info = sp.artist(item["artist_id"])
                            if "genres" in artist_info and artist_info["genres"]:
                                genres = ["#" + genre.replace(" ", "_") for genre in artist_info["genres"][:3]]
                        except Exception as e:
                            logger.error(f"Ошибка при получении жанров артиста из Spotify: {e}")
                    
                    if genres:
                        message += f"Жанры: {' '.join(genres)}\n"
                    
                    message += f"\n{item['url']}"
                    
                elif item.get("type") == "artist" and "artist" in item:
                    # Формат для артиста
                    artist = item.get("artist", "")
                    
                    message = f"<b>{artist}</b>\n\n"
                    
                    # Пытаемся получить жанры, если доступен Spotify API
                    genres = []
                    if sp and "artist_id" in item:
                        try:
                            artist_info = sp.artist(item["artist_id"])
                            if "genres" in artist_info and artist_info["genres"]:
                                genres = ["#" + genre.replace(" ", "_") for genre in artist_info["genres"][:3]]
                        except Exception as e:
                            logger.error(f"Ошибка при получении жанров артиста из Spotify: {e}")
                    
                    if genres:
                        message += f"Жанры: {' '.join(genres)}\n\n"
                    
                    message += f"{item['url']}"
                    
                else:
                    # Для неизвестного типа или неполных данных
                    message = f"Новый контент на Spotify\n\n{item.get('url', 'Ссылка отсутствует')}"
            else:
                # Если элемент очереди не словарь
                message = str(item)
            
            # Отправляем сообщение в канал с HTML-форматированием
            bot.send_message(TELEGRAM_CHANNEL_ID, message, parse_mode="HTML")
            logger.info(f"Сообщение успешно отправлено в канал: {TELEGRAM_CHANNEL_ID}")
            
            # Если публикация успешна, удаляем из очереди
            remove_from_queue(0)
        except Exception as e:
            logger.error(f"Ошибка при публикации: {e}")
            logger.error(traceback.format_exc())
    except Exception as e:
        logger.error(f"Ошибка при обработке очереди: {e}")
        logger.error(traceback.format_exc())

# Обработчики команд Telegram
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    logger.info(f"Получена команда /start или /help от пользователя {message.from_user.id}")
    help_text = (
        "Привет! Я бот для отслеживания новых релизов на Spotify.\n\n"
        "Доступные команды:\n"
        "/help - показать это сообщение\n"
        "/check - принудительно проверить новые релизы\n"
        "/checknow - то же самое, что и /check\n"
        "/queue - показать очередь публикаций\n"
        "/queue_remove [номер] - удалить элемент из очереди\n"
        "/queue_clear - очистить всю очередь\n"
        "/status - показать статус бота\n\n"
        "Вы также можете отправить мне ссылку на Spotify (альбом, трек или артиста), и я добавлю её в очередь публикаций."
    )
    bot.reply_to(message, help_text)

@bot.message_handler(commands=['check', 'checknow'])
def force_check(message):
    logger.info(f"Получена команда /check или /checknow от пользователя {message.from_user.id}")
    bot.reply_to(message, "Запускаю проверку новых релизов...")
    
    # Запускаем проверку в отдельном потоке, чтобы не блокировать бота
    check_thread = threading.Thread(target=check_followed_artists_releases, daemon=True)
    check_thread.start()

@bot.message_handler(commands=['queue'])
def show_queue(message):
    logger.info(f"Получена команда /queue от пользователя {message.from_user.id}")
    
    # Проверяем, есть ли что-то в очереди
    current_queue = get_queue()
    if not current_queue:
        bot.reply_to(message, "Очередь публикаций пуста.")
        return
    
    # Формируем сообщение с текущей очередью
    queue_message = "🔄 <b>Текущая очередь публикаций:</b>\n\n"
    for i, item in enumerate(current_queue):
        try:
            # Предполагаем, что item - это словарь с информацией о релизе
            if isinstance(item, dict):
                if item.get("type") == "release" and "artist" in item and "album" in item:
                    queue_message += f"{i+1}. 💿 <b>{item['artist']}</b> - <b>{item['album']}</b>\n"
                elif item.get("type") == "track" and "artist" in item and "track" in item:
                    queue_message += f"{i+1}. 🎧 <b>{item['artist']}</b> - <b>{item['track']}</b>\n"
                elif item.get("type") == "artist" and "artist" in item:
                    queue_message += f"{i+1}. 👤 <b>{item['artist']}</b>\n"
                else:
                    queue_message += f"{i+1}. 🔗 {item.get('url', 'Ссылка')}\n"
            else:
                queue_message += f"{i+1}. {str(item)}\n"
        except Exception as e:
            logger.error(f"Ошибка при форматировании элемента очереди: {e}")
            queue_message += f"{i+1}. [Ошибка форматирования]\n"
    
    # Добавляем инструкцию по управлению очередью
    queue_message += "\nДля удаления элемента из очереди отправьте: /queue_remove [номер]"
    
    bot.reply_to(message, queue_message, parse_mode="HTML")

@bot.message_handler(commands=['queue_remove'])
def remove_queue_item(message):
    logger.info(f"Получена команда /queue_remove от пользователя {message.from_user.id}")
    
    # Проверяем, есть ли аргумент с номером элемента для удаления
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "Пожалуйста, укажите номер элемента для удаления.\nПример: /queue_remove 1")
        return
    
    try:
        # Получаем номер элемента и преобразуем в индекс (с учетом, что нумерация для пользователя начинается с 1)
        item_number = int(parts[1])
        index = item_number - 1
        
        # Удаляем элемент из очереди
        success, removed_item = remove_from_queue(index)
        
        if success:
            bot.reply_to(message, f"✅ Элемент #{item_number} успешно удален из очереди.")
        else:
            bot.reply_to(message, f"❌ Элемент #{item_number} не найден в очереди. Используйте /queue для просмотра доступных элементов.")
    except ValueError:
        bot.reply_to(message, "❌ Неверный формат номера элемента. Должно быть число.")
    except Exception as e:
        logger.error(f"Ошибка при удалении из очереди: {e}")
        bot.reply_to(message, "❌ Произошла ошибка при удалении элемента из очереди.")

@bot.message_handler(commands=['queue_clear'])
def clear_queue(message):
    logger.info(f"Получена команда /queue_clear от пользователя {message.from_user.id}")
    
    global queue
    queue_size = len(queue)
    queue = []
    
    bot.reply_to(message, f"✅ Очередь очищена. Удалено элементов: {queue_size}")

@bot.message_handler(commands=['status'])
def bot_status(message):
    logger.info(f"Получена команда /status от пользователя {message.from_user.id}")
    uptime = time.time() - start_time
    hours, remainder = divmod(int(uptime), 3600)
    minutes, seconds = divmod(remainder, 60)
    
    status_message = f"✅ Бот работает\n⏱ Время работы: {hours}ч {minutes}м {seconds}с"
    bot.reply_to(message, status_message)

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    logger.info(f"Получено сообщение от пользователя {message.from_user.id}: {message.text}")
    
    # Проверяем, содержит ли сообщение ссылку Spotify
    if "spotify.com" in message.text.lower():
        # Обрабатываем ссылку
        item, error = process_spotify_link(message.text.strip())
        
        if error:
            bot.reply_to(message, f"❌ {error}")
            return
        
        if item:
            # Добавляем в очередь
            add_to_queue(item)
            
            # Формируем ответное сообщение
            if item["type"] == "release" and "artist" in item and "album" in item:
                reply = f"✅ Добавлено в очередь:\n💿 <b>{item['artist']}</b> - <b>{item['album']}</b>"
            elif item["type"] == "track" and "artist" in item and "track" in item:
                reply = f"✅ Добавлено в очередь:\n🎧 <b>{item['artist']}</b> - <b>{item['track']}</b>"
            elif item["type"] == "artist" and "artist" in item:
                reply = f"✅ Добавлено в очередь:\n👤 <b>{item['artist']}</b>"
            else:
                reply = f"✅ Ссылка добавлена в очередь публикаций"
            
            bot.reply_to(message, reply, parse_mode="HTML")
            return
    
    # Если это не ссылка Spotify или обработка не удалась
    bot.reply_to(message, "Я понимаю только команды и ссылки Spotify. Используйте /help для получения списка команд.")

# Обработчик для webhook
@app.route('/webhook/' + TELEGRAM_BOT_TOKEN, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return ''
    else:
        abort(403)

# Отслеживаем время запуска бота
start_time = time.time()

# Фоновый поток для периодических задач
def run_background_tasks():
    last_check_time = time.time()
    last_queue_check = time.time()
    last_token_refresh = time.time()
    
    while True:
        try:
            # Обновляем токен каждый час
            if time.time() - last_token_refresh > 60 * 60:
                logger.info("Обновляем токен Spotify...")
                try:
                    token_info = sp_oauth.refresh_access_token(spotify_refresh_token)
                    global sp
                    sp = spotipy.Spotify(auth=token_info['access_token'])
                    logger.info("Токен Spotify успешно обновлен")
                except Exception as e:
                    logger.error(f"Ошибка при обновлении токена: {e}")
                last_token_refresh = time.time()
            
            # Проверяем новые релизы каждые N часов (из переменной окружения)
            if time.time() - last_check_time > CHECK_INTERVAL_HOURS * 60 * 60:
                logger.info(f"Проверка новых релизов (интервал: {CHECK_INTERVAL_HOURS} ч)...")
                check_followed_artists_releases()
                last_check_time = time.time()
            
            # Проверяем очередь каждую минуту
            if time.time() - last_queue_check > 60:
                logger.debug("Проверка очереди...")
                check_and_post_from_queue()
                last_queue_check = time.time()
            
            time.sleep(1)
        except Exception as e:
            logger.error(f"Ошибка в фоновых задачах: {e}")
            logger.error(traceback.format_exc())
            time.sleep(5)

# Главная функция
if __name__ == '__main__':
    # Проверяем и удаляем webhook для избежания конфликтов
    bot.remove_webhook()
    time.sleep(0.5)
    
    # Получаем URL для webhook
    webhook_host = os.environ.get('RAILWAY_STATIC_URL')
    if not webhook_host:
        webhook_host = f"{os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'localhost')}"
    
    webhook_url = f"https://{webhook_host}/webhook/{TELEGRAM_BOT_TOKEN}"
    
    # Настраиваем webhook
    bot.set_webhook(url=webhook_url)
    logger.info(f"Webhook установлен по адресу: {webhook_url}")
    
    # Инициализируем Spotify API
    try:
        token_info = sp_oauth.refresh_access_token(spotify_refresh_token)
        global sp
        sp = spotipy.Spotify(auth=token_info['access_token'])
        logger.info("Токен Spotify успешно инициализирован")
    except Exception as e:
        logger.error(f"Ошибка при инициализации токена Spotify: {e}")
    
    # Запускаем фоновый поток для периодических задач
    background_thread = threading.Thread(target=run_background_tasks, daemon=True)
    background_thread.start()
    
    # Запускаем проверку новых релизов при старте
    check_thread = threading.Thread(target=check_followed_artists_releases, daemon=True)
    check_thread.start()
    
    # Запускаем Flask приложение
    port = int(os.environ.get('PORT', 8080))
    logger.info(f"Запуск webhook сервера на порту {port}...")
    
    try:
        app.run(host='0.0.0.0', port=port)
    except Exception as e:
        logger.error(f"Ошибка при запуске веб-сервера: {e}")
        logger.error(traceback.format_exc())
