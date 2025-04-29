import os
import telebot
from telebot import types
import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from datetime import datetime, timedelta
import pytz
import time
import logging
import sqlite3
import sys
import traceback

# Настройка логгера
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
    c = conn.cursor()
    
    # Создаем таблицу для хранения истории поста релизов
    c.execute('''CREATE TABLE IF NOT EXISTS posted_releases
                 (spotify_id TEXT PRIMARY KEY, post_date TEXT)''')
    
    # Создаем таблицу для очереди
    c.execute('''CREATE TABLE IF NOT EXISTS queue
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  spotify_id TEXT,
                  artist TEXT,
                  title TEXT,
                  image_url TEXT,
                  spotify_link TEXT,
                  query TEXT,
                  post_time TEXT,
                  UNIQUE(spotify_id))''')
    
    conn.commit()
    conn.close()

# Проверка, был ли релиз уже запощен
def is_release_posted(spotify_id):
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT * FROM posted_releases WHERE spotify_id = ?", (spotify_id,))
    result = c.fetchone()
    conn.close()
    return result is not None

# Отметить релиз как запощенный
def mark_release_posted(spotify_id):
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO posted_releases (spotify_id, post_date) VALUES (?, ?)", 
              (spotify_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()

# Добавление в очередь
def add_to_queue(spotify_id, artist, title, image_url, spotify_link, query, post_time):
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
    c = conn.cursor()
    try:
        c.execute('''INSERT OR IGNORE INTO queue 
                     (spotify_id, artist, title, image_url, spotify_link, query, post_time) 
                     VALUES (?, ?, ?, ?, ?, ?, ?)''',
                  (spotify_id, artist, title, image_url, spotify_link, query, post_time))
        conn.commit()
    except sqlite3.IntegrityError:
        # Игнорируем дубликаты
        pass
    conn.close()

# Получение очереди
def get_queue():
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("SELECT * FROM queue ORDER BY post_time ASC")
    queue = c.fetchall()
    conn.close()
    return queue

# Удаление из очереди
def remove_from_queue(queue_id):
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("DELETE FROM queue WHERE id = ?", (queue_id,))
    conn.commit()
    conn.close()

# Очистка очереди
def clear_queue():
    conn = sqlite3.connect('bot_data.db', check_same_thread=False)
    c = conn.cursor()
    c.execute("DELETE FROM queue")
    conn.commit()
    conn.close()

# Основной код бота
bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
spotify_client_id = os.getenv('SPOTIFY_CLIENT_ID')
spotify_client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
admin_id = 7213866  # Ваш правильный Telegram ID
channel_id = os.getenv('TELEGRAM_CHANNEL_ID')

# Массив плейлистов (обновленный список с актуальными ID)
playlist_ids = [
    '37i9dQZEVXbMDoHDwVN2tF',  # Global Top 50
    '37i9dQZEVXbNG2KDcFcKOF',  # Top Tracks Russia
    '37i9dQZF1DXcRXFNfZr7Tp',  # New Music Friday UK
    '37i9dQZF1DX0hvSv9Rf41p',  # Global 100
    '37i9dQZF1DX4UtSsGT1Sbe',  # Today's Top Hits
    '37i9dQZF1DWVKDF4ycOhu1',  # Pop Rising
    '37i9dQZF1DX5Ejj0EkURtP',  # Hot Hits USA
    '37i9dQZF1DXdPec7aLTmlC',  # Fresh Finds
    '37i9dQZF1DX4WYpdgoIcn6'   # Viral Hits
]

# Инициализация Spotify клиента
client_credentials_manager = SpotifyClientCredentials(client_id=spotify_client_id, client_secret=spotify_client_secret)
sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)

# Инициализация бота - ОТКЛЮЧАЕМ МНОГОПОТОЧНОСТЬ
bot = telebot.TeleBot(bot_token, parse_mode='HTML', threaded=False)

# Инициализация БД
init_db()

# Функция проверки новых релизов
def check_new_releases():
    try:
        logger.info("Проверка новых релизов в Spotify")
        moscow_tz = pytz.timezone('Europe/Moscow')
        current_time = datetime.now(moscow_tz)
        
        new_releases = []
        days_ago = 3  # Ищем релизы за последние 3 дня
        
        logger.info(f"Поиск релизов за последние {days_ago} дней")
        logger.info(f"Текущая дата: {current_time.strftime('%Y-%m-%d')}")
        logger.info(f"Ищем релизы с: {(current_time - timedelta(days=days_ago)).strftime('%Y-%m-%d')}")
        
        # Получаем новые релизы через API
        try:
            # Получаем новые альбомы
            results = sp.new_releases(country='RU', limit=50)
            
            logger.info(f"Получено {len(results['albums']['items'])} новых релизов")
            
            for album in results['albums']['items']:
                # Получаем дату релиза
                release_date_str = album.get('release_date', '')
                
                if release_date_str:
                    release_date = None
                    if len(release_date_str) == 10:  # YYYY-MM-DD
                        release_date = datetime.strptime(release_date_str, '%Y-%m-%d')
                        logger.debug(f"Найден альбом с датой {release_date_str}: {album['name']} - {album['artists'][0]['name']}")
                    elif len(release_date_str) == 7:  # YYYY-MM
                        release_date = datetime.strptime(release_date_str + '-01', '%Y-%m-%d')
                        logger.debug(f"Найден альбом с месяцем {release_date_str}: {album['name']} - {album['artists'][0]['name']}")
                    elif len(release_date_str) == 4:  # YYYY
                        release_date = datetime.strptime(release_date_str + '-01-01', '%Y-%m-%d')
                        logger.debug(f"Найден альбом с годом {release_date_str}: {album['name']} - {album['artists'][0]['name']}")
                    
                    if release_date:
                        release_date = moscow_tz.localize(release_date)
                        days_difference = (current_time - release_date).days
                        
                        logger.debug(f"Альбом {album['name']} от {album['artists'][0]['name']} - {days_difference} дней назад")
                        
                        if 0 <= days_difference <= days_ago:
                            spotify_id = album['id']
                            
                            # Проверяем, не был ли уже запощен этот релиз
                            if not is_release_posted(spotify_id):
                                # Получаем первый трек из альбома для примера
                                try:
                                    album_tracks = sp.album_tracks(spotify_id, limit=1)
                                    
                                    if album_tracks['items']:
                                        track = album_tracks['items'][0]
                                        
                                        release_info = {
                                            'artist': album['artists'][0]['name'],
                                            'title': album['name'],
                                            'track_name': track['name'],
                                            'image_url': album['images'][0]['url'] if album['images'] else None,
                                            'spotify_link': album['external_urls']['spotify'],
                                            'spotify_id': spotify_id,
                                            'release_date': release_date,
                                            'days_old': days_difference,
                                            'query': f"{track['name']} - {album['artists'][0]['name']}"
                                        }
                                        new_releases.append(release_info)
                                        logger.info(f"Найден новый релиз: {release_info['artist']} - {release_info['title']}, дата: {release_date_str}")
                                except Exception as e:
                                    logger.error(f"Ошибка при получении треков для альбома {spotify_id}: {e}")
                            else:
                                logger.debug(f"Релиз уже отправлялся: {album['artists'][0]['name']} - {album['name']}")
                        else:
                            logger.debug(f"Релиз слишком старый ({days_difference} дней): {album['artists'][0]['name']} - {album['name']}")
        
        except Exception as e:
            logger.error(f"Ошибка при получении новых релизов: {e}")
            logger.error(traceback.format_exc())
        
        # Добавляем только новые релизы в очередь
        if new_releases:
            logger.info(f"Найдено {len(new_releases)} новых релизов. Добавляем в очередь.")
            sorted_releases = sorted(new_releases, key=lambda x: x['release_date'], reverse=True)
            
            queue_start_time = datetime.now(moscow_tz) + timedelta(minutes=5)
            
            for idx, release in enumerate(sorted_releases):
                post_time = queue_start_time + timedelta(hours=idx)
                add_to_queue(
                    release['spotify_id'],
                    release['artist'],
                    release['title'],
                    release['image_url'],
                    release['spotify_link'],
                    release['query'],
                    post_time.isoformat()
                )
                logger.info(f"Добавлен в очередь: {release['artist']} - {release['title']}, запланировано на: {post_time.strftime('%H:%M, %d.%m')}")
            
            # Отправляем уведомление админу
            queue_info = get_queue()
            notify_admin_about_queue(queue_info)
        else:
            logger.info("Новых релизов не найдено")
                
    except Exception as e:
        logger.error(f"Ошибка в check_new_releases: {e}")
        logger.error(traceback.format_exc())

# Функция отправки поста в канал
def post_to_channel(release_from_queue):
    try:
        queue_id, spotify_id, artist, title, image_url, spotify_link, query, post_time = release_from_queue
        
        message_text = f"🆕 <b>{artist} - {title}</b>"
        
        keyboard = types.InlineKeyboardMarkup()
        spotify_button = types.InlineKeyboardButton(
            text="Слушать в Spotify",
            url=spotify_link
        )
        keyboard.add(spotify_button)
        
        if image_url:
            response = requests.get(image_url)
            if response.status_code == 200:
                bot.send_photo(channel_id, photo=response.content, caption=message_text, reply_markup=keyboard)
            else:
                bot.send_message(channel_id, message_text, reply_markup=keyboard)
        else:
            bot.send_message(channel_id, message_text, reply_markup=keyboard)
        
        # Отмечаем как запощенный и удаляем из очереди
        mark_release_posted(spotify_id)
        remove_from_queue(queue_id)
        logger.debug(f"Запостили релиз: {artist} - {title}")
        
    except Exception as e:
        logger.error(f"Ошибка при отправке поста: {e}")
        logger.error(traceback.format_exc())

# Уведомление админа о очереди
def notify_admin_about_queue(queue_items):
    if not queue_items:
        bot.send_message(admin_id, "Очередь постов пуста")
        return
    
    queue_text = "📋 <b>Очередь постов:</b>\n\n"
    
    for item in queue_items:
        queue_id, spotify_id, artist, title, _, _, query, post_time = item
        post_datetime = datetime.fromisoformat(post_time)
        formatted_time = post_datetime.strftime('%H:%M, %d.%m')
        queue_text += f"{queue_id}. {artist} - {title}\n📅 {formatted_time}\n\n"
    
    queue_text += f"Всего в очереди: {len(queue_items)} постов"
    bot.send_message(admin_id, queue_text)

# Тестовая команда - доступна всем
@bot.message_handler(commands=['start'])
def start_command(message):
    logger.info(f"Команда /start от пользователя {message.from_user.id}")
    bot.send_message(message.chat.id, "Бот работает! Ваш ID: " + str(message.from_user.id))

# Команда редактирования очереди
@bot.message_handler(commands=['queue_manage'])
def manage_queue(message):
    logger.debug(f"Команда /queue_manage от пользователя {message.from_user.id}")
    if message.from_user.id != admin_id:
        return
    
    queue_items = get_queue()
    if not queue_items:
        bot.send_message(admin_id, "Очередь пуста")
        return
    
    markup = types.InlineKeyboardMarkup()
    
    for item in queue_items[:10]:  # Показываем первые 10
        queue_id, _, artist, title, _, _, _, _ = item
        button_text = f"{queue_id}. {artist[:15]}... - {title[:15]}..."
        callback_data = f"del_{queue_id}"
        markup.add(types.InlineKeyboardButton(text=button_text, callback_data=callback_data))
    
    markup.add(types.InlineKeyboardButton(text="❌ Очистить всю очередь", callback_data="clear_all"))
    
    bot.send_message(admin_id, "Выберите пост для удаления:", reply_markup=markup)

# Обработка нажатий на кнопки
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    if call.from_user.id != admin_id:
        return
    
    if call.data.startswith("del_"):
        queue_id = int(call.data.replace("del_", ""))
        remove_from_queue(queue_id)
        bot.answer_callback_query(call.id, "Пост удален из очереди")
        
        # Обновляем сообщение
        queue_items = get_queue()
        notify_admin_about_queue(queue_items)
        
    elif call.data == "clear_all":
        clear_queue()
        bot.answer_callback_query(call.id, "Очередь очищена")
        bot.send_message(admin_id, "Очередь полностью очищена")

# Функция проверки и отправки постов из очереди
def check_and_post_from_queue():
    try:
        current_time = datetime.now(pytz.timezone('Europe/Moscow'))
        queue_items = get_queue()
        
        for item in queue_items:
            post_time = datetime.fromisoformat(item[7])
            if post_time.tzinfo is None:
                post_time = pytz.timezone('Europe/Moscow').localize(post_time)
            
            if current_time >= post_time:
                post_to_channel(item)
                
    except Exception as e:
        logger.error(f"Ошибка в check_and_post_from_queue: {e}")
        logger.error(traceback.format_exc())

# Команда проверки плейлистов
@bot.message_handler(commands=['check_playlists'])
def check_playlists_availability(message):
    logger.debug(f"Команда /check_playlists от пользователя {message.from_user.id}")
    if message.from_user.id == admin_id:
        bot.send_message(message.chat.id, "Проверяю доступность плейлистов...")
        available_playlists = []
        unavailable_playlists = []
        
        for playlist_id in playlist_ids:
            try:
                playlist = sp.playlist(playlist_id)
                available_playlists.append(f"{playlist['name']} (ID: {playlist_id})")
            except:
                unavailable_playlists.append(playlist_id)
        
        response = "📊 <b>Статус плейлистов:</b>\n\n"
        if available_playlists:
            response += "✅ <b>Доступные плейлисты:</b>\n"
            for playlist in available_playlists:
                response += f"• {playlist}\n"
        
        if unavailable_playlists:
            response += "\n❌ <b>Недоступные плейлисты:</b>\n"
            for playlist_id in unavailable_playlists:
                response += f"• {playlist_id}\n"
        
        bot.send_message(message.chat.id, response)

# Команда проверки новых релизов
@bot.message_handler(commands=['check'])
def check_updates_command(message):
    logger.debug(f"Команда /check от пользователя {message.from_user.id}")
    if message.from_user.id == admin_id:
        check_message = bot.send_message(message.chat.id, "Проверяю новые релизы...")
        check_new_releases()
        queue_items = get_queue()
        
        if queue_items:
            bot.edit_message_text(f"Проверка завершена. Найдено {len(queue_items)} релизов в очереди.", 
                                  message.chat.id, check_message.message_id)
            notify_admin_about_queue(queue_items)
        else:
            bot.edit_message_text("Проверка завершена. Новых релизов не найдено.", 
                                 message.chat.id, check_message.message_id)
    else:
        bot.send_message(message.chat.id, f"У вас нет доступа к этой команде. Ваш ID: {message.from_user.id}, а нужен: {admin_id}")

# Команда показа очереди
@bot.message_handler(commands=['queue'])
def show_queue(message):
    logger.debug(f"Команда /queue от пользователя {message.from_user.id}")
    if message.from_user.id == admin_id:
        queue_items = get_queue()
        notify_admin_about_queue(queue_items)
    else:
        bot.send_message(message.chat.id, f"У вас нет доступа к этой команде. Ваш ID: {message.from_user.id}, а нужен: {admin_id}")

if __name__ == '__main__':
    logger.info("Запуск бота...")
    
    # Очищаем webhook если он был установлен
    try:
        bot.remove_webhook()
        logger.info("Webhook удален")
    except Exception as e:
        logger.error(f"Ошибка при удалении webhook: {e}")
    
    # Ждем перед запуском для решения проблем с конфликтами
    logger.info("Ожидание 10 секунд перед запуском для предотвращения конфликтов...")
    time.sleep(10)
    
    # Запускаем периодические задачи в фоне
    def background_tasks():
        last_check_time = time.time()
        last_queue_check = time.time()
        
        while True:
            try:
                # Проверяем новые релизы каждые 3 часа
                if time.time() - last_check_time > 3 * 60 * 60:
                    logger.info("Проверка новых релизов...")
                    check_new_releases()
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
    
    # Запускаем фоновые задачи в отдельном потоке
    import threading
    background_thread = threading.Thread(target=background_tasks, daemon=True)
    background_thread.start()
    
    # Сразу запускаем проверку новых релизов при старте
    logger.info("Запуск первичной проверки релизов...")
    check_new_releases()
    
    # Запускаем бота с использованием polling
    logger.info("Бот запущен и готов к работе")
    
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            logger.error(f"Ошибка бота: {e}")
            logger.error(traceback.format_exc())
            time.sleep(5)
