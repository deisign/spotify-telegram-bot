import os
import telebot
from telebot import types
import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from datetime import datetime, timedelta
import base64
import pytz
import time
import logging
import sqlite3
import sys
import traceback
import threading

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
admin_id = 6400164260  # Ваш Telegram ID
channel_id = os.getenv('TELEGRAM_CHANNEL_ID')

# Массив плейлистов
playlist_ids = [
    '37i9dQZF1DX6J5NfMJS675',
    '37i9dQZF1DX4JAvHpjipBk',
    '37i9dQZF1DXcBWIGoYBM5M',
    '37i9dQZF1DX0XUsuxWHRQd',
    '37i9dQZF1DX10zKzsJ2jva',
    '37i9dQZF1DWWjGdmeTyeJ6',
    '37i9dQZF1DWVmps5U8gHNv',
    '37i9dQZF1DXcF6B6QPhFDv',
    '37i9dQZF1DWUa8ZRTfalHk',
    '37i9dQZF1DX0BcQWzuB7ZO',
    '37i9dQZF1DX4dyzvuaRJ0n',
    '37i9dQZF1DX82Zzp6AKx64',
    '37i9dQZF1DXcZDD7cfEKhW',
    '37i9dQZF1DX7KNKjOK0o75',
    '37i9dQZF1DX4sWSpwq3LiO',
    '37i9dQZF1DX4SBhb3fqCJd'
]

# Инициализация Spotify клиента
client_credentials_manager = SpotifyClientCredentials(client_id=spotify_client_id, client_secret=spotify_client_secret)
sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)

# Инициализация бота
bot = telebot.TeleBot(bot_token, parse_mode='HTML')

# Инициализация БД
init_db()

# Функция проверки плейлистов
def check_playlists_for_updates():
    try:
        logger.debug("Проверка плейлистов на новые релизы")
        moscow_tz = pytz.timezone('Europe/Moscow')
        current_time = datetime.now(moscow_tz)
        
        new_releases = []
        days_ago = 3  # Идем на 3 дня назад
        
        for playlist_id in playlist_ids:
            try:
                playlist = sp.playlist(playlist_id)
                playlist_name = playlist['name']
                logger.debug(f"Проверка плейлиста: {playlist_name}")
                
                for item in playlist['tracks']['items']:
                    track = item['track']
                    if track and track.get('album'):
                        release_date_str = track['album'].get('release_date', '')
                        
                        if release_date_str:
                            release_date = None
                            if len(release_date_str) == 10:
                                release_date = datetime.strptime(release_date_str, '%Y-%m-%d')
                            elif len(release_date_str) == 7:
                                release_date = datetime.strptime(release_date_str + '-01', '%Y-%m-%d')
                            
                            if release_date:
                                release_date = moscow_tz.localize(release_date)
                                days_difference = (current_time - release_date).days
                                
                                if 0 <= days_difference <= days_ago:
                                    spotify_id = track['album']['id']
                                    
                                    # Проверяем, не был ли уже запощен этот релиз
                                    if not is_release_posted(spotify_id):
                                        # Проверяем, нет ли уже в очереди
                                        if not any(release['spotify_id'] == spotify_id for release in new_releases):
                                            release_info = {
                                                'artist': track['artists'][0]['name'],
                                                'title': track['name'],
                                                'image_url': track['album']['images'][0]['url'] if track['album']['images'] else None,
                                                'spotify_link': track['external_urls']['spotify'],
                                                'spotify_id': spotify_id,
                                                'release_date': release_date,
                                                'days_old': days_difference,
                                                'query': f"{track['name']} - {track['artists'][0]['name']}"
                                            }
                                            new_releases.append(release_info)
                                            logger.debug(f"Найден новый релиз: {release_info['artist']} - {release_info['title']}")
            
            except Exception as e:
                logger.error(f"Ошибка при обработке плейлиста {playlist_id}: {e}")
                logger.error(traceback.format_exc())
                continue
        
        # Добавляем только новые релизы в очередь
        if new_releases:
            logger.debug(f"Найдено {len(new_releases)} новых релизов. Добавляем в очередь.")
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
            
            # Отправляем уведомление админу
            queue_info = get_queue()
            notify_admin_about_queue(queue_info)
        else:
            logger.debug("Новых релизов не найдено")
                
    except Exception as e:
        logger.error(f"Ошибка в check_playlists_for_updates: {e}")
        logger.error(traceback.format_exc())

# Функция отправки поста в канал
def post_to_channel(release_from_queue):
    try:
        queue_id, spotify_id, artist, title, image_url, spotify_link, query, post_time = release_from_queue
        
        message_text = f"🆕 <b>{artist} - {title}</b>"
        
        keyboard = types.InlineKeyboardMarkup()
        
        query_parts = query.split(' - ')
        song_title = query_parts[0]
        artist_name = query_parts[1] if len(query_parts) > 1 else query_parts[0]
        
        query_encoded = base64.b64encode(query.encode('utf-8')).decode('utf-8')
        apple_query = f"{song_title} {artist_name}"
        apple_query_encoded = base64.b64encode(apple_query.encode('utf-8')).decode('utf-8')
        
        yandex_button = types.InlineKeyboardButton(
            text="Яндекс Музыка",
            url=f"https://deisigner-m.vercel.app?ytquery={query_encoded}"
        )
        apple_button = types.InlineKeyboardButton(
            text="Apple Music",
            url=f"https://deisigner-a.vercel.app?amquery={apple_query_encoded}"
        )
        youtube_button = types.InlineKeyboardButton(
            text="YouTube",
            url=f"https://deisigner-ym.vercel.app?ytmquery={query_encoded}"
        )
        spotify_button = types.InlineKeyboardButton(
            text="Spotify",
            url=spotify_link
        )
        
        keyboard.row(spotify_button, apple_button)
        keyboard.row(yandex_button, youtube_button)
        
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

# Команда редактирования очереди
@bot.message_handler(commands=['queue_manage'])
def manage_queue(message):
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

# Таймеры без APScheduler
def run_periodic_check():
    while True:
        check_playlists_for_updates()
        time.sleep(3 * 60 * 60)  # Каждые 3 часа

def run_queue_check():
    while True:
        check_and_post_from_queue()
        time.sleep(60)  # Каждую минуту

# Команда проверки новых релизов
@bot.message_handler(commands=['check'])
def check_updates_command(message):
    if message.from_user.id == admin_id:
        bot.send_message(message.chat.id, "Проверяю новые релизы...")
        check_playlists_for_updates()
        bot.send_message(message.chat.id, "Проверка завершена")

# Команда показа очереди
@bot.message_handler(commands=['queue'])
def show_queue(message):
    if message.from_user.id == admin_id:
        queue_items = get_queue()
        notify_admin_about_queue(queue_items)

if __name__ == '__main__':
    # Запускаем треды для периодических задач
    periodic_check_thread = threading.Thread(target=run_periodic_check, daemon=True)
    queue_check_thread = threading.Thread(target=run_queue_check, daemon=True)
    
    periodic_check_thread.start()
    queue_check_thread.start()
    
    # Запускаем бота
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            logger.error(f"Ошибка бота: {e}")
            logger.error(traceback.format_exc())
            time.sleep(5)
