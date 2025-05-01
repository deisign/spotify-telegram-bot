# Команда для расширенного управления очередью
@bot.message_handler(commands=['manage'])
def extended_queue_manage(message):
    if message.from_user.id != admin_id:
        bot.send_message(message.chat.id, f"У вас нет доступа к этой команде. Ваш ID: {message.from_user.id}, а нужен: {admin_id}")
        return
    
    queue_items = get_queue()
    if not queue_items:
        bot.send_message(admin_id, "Очередь пуста")
        return
        
    # Создаем клавиатуру с опциями управления
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    # Добавляем кнопки для каждого релиза в очереди
    for item in queue_items[:10]:  # Ограничиваем до 10 элементов
        queue_id, _, artist, title, _, _, _, post_time = item
        post_datetime = datetime.fromisoformat(post_time)
        formatted_time = post_datetime.strftime('%H:%M, %d.%m')
        
        # Создаем кнопку с информацией о релизе
        button_text = f"🎵 {artist} - {title} ({formatted_time})"
        
        # Добавляем кнопки управления для этого релиза
        markup.add(
            types.InlineKeyboardButton(button_text, callback_data=f"info_{queue_id}")
        )
        markup.add(
            types.InlineKeyboardButton("⬆️ Вверх", callback_data=f"up_{queue_id}"),
            types.InlineKeyboardButton("⬇️ Вниз", callback_data=f"down_{queue_id}")
        )
        markup.add(
            types.InlineKeyboardButton("⏱ Изменить время", callback_data=f"time_{queue_id}"),
            types.InlineKeyboardButton("❌ Удалить", callback_data=f"del_{queue_id}")
        )
    
    # Добавляем кнопку для очистки всей очереди внизу
    markup.add(types.InlineKeyboardButton("🗑 Очистить всю очередь", callback_data="clear_all"))
    
    bot.send_message(admin_id, "📋 <b>Управление очередью постов:</b>", reply_markup=markup, parse_mode='HTML')# Обработка ссылок на Spotify
@bot.message_handler(func=lambda message: 
                    message.text and ('open.spotify.com/album/' in message.text or 'open.spotify.com/track/' in message.text))
def spotify_link_handler(message):
    if message.from_user.id != admin_id:
        bot.send_message(message.chat.id, "Только администратор может добавлять релизы в очередь.")
        return
    
    # Находим ссылку в сообщении
    words = message.text.split()
    spotify_link = None
    for word in words:
        if 'open.spotify.com/album/' in word or 'open.spotify.com/track/' in word:
            spotify_link = word
            break
    
    if not spotify_link:
        bot.send_message(message.chat.id, "Не удалось обнаружить корректную ссылку на Spotify.")
        return
    
    # Отправляем сообщение о начале обработки
    processing_msg = bot.send_message(message.chat.id, "Обрабатываю ссылку на Spotify...")
    
    # Добавляем релиз в очередь
    success, result = add_release_by_link(spotify_link)
    
    # Обновляем сообщение с результатом
    bot.edit_message_text(result, chat_id=message.chat.id, message_id=processing_msg.message_id)
    
    # Если релиз успешно добавлен, показываем обновленную очередь
    if success:
        queue_items = get_queue()
        notify_admin_about_queue(queue_items)# Функция добавления релиза в очередь по ссылке
def add_release_by_link(spotify_link, scheduled_time=None):
    try:
        # Из ссылки выделяем ID альбома/трека
        if 'spotify.com/album/' in spotify_link:
            # Формат: https://open.spotify.com/album/1234567890
            album_id = spotify_link.split('spotify.com/album/')[1].split('?')[0]
        elif 'spotify.com/track/' in spotify_link:
            # Если это ссылка на трек, получаем его альбом
            track_id = spotify_link.split('spotify.com/track/')[1].split('?')[0]
            track = sp.track(track_id)
            album_id = track['album']['id']
        else:
            return False, "Неподдерживаемый формат ссылки. Поддерживаются ссылки на альбомы и треки."
        
        # Проверяем, не добавлен ли уже этот релиз в очередь
        current_queue = get_queue()
        queue_spotify_ids = [item[1] for item in current_queue]
        
        if album_id in queue_spotify_ids:
            return False, "Этот релиз уже находится в очереди."
        
        # Проверяем, не был ли уже опубликован этот релиз
        if is_release_posted(album_id):
            return False, "Этот релиз уже был опубликован ранее."
        
        # Получаем информацию об альбоме
        album = sp.album(album_id)
        
        # Получаем основную информацию для очереди
        artist_name = album['artists'][0]['name']
        album_title = album['name']
        image_url = album['images'][0]['url'] if album['images'] else None
        spotify_link = album['external_urls']['spotify']
        
        # Получаем первый трек альбома для запроса
        album_tracks = sp.album_tracks(album_id, limit=1)
        if album_tracks['items']:
            track = album_tracks['items'][0]
            query = f"{track['name']} - {artist_name}"
        else:
            query = f"{album_title} - {artist_name}"
        
        # Определяем время публикации
        moscow_tz = pytz.timezone('Europe/Moscow')
        if scheduled_time:
            post_time = scheduled_time
        else:
            # Если время не указано, ставим на ближайший час
            post_time = datetime.now(moscow_tz) + timedelta(hours=1)
            post_time = post_time.replace(minute=0, second=0, microsecond=0)
        
        # Добавляем в очередь
        add_to_queue(
            album_id,
            artist_name,
            album_title,
            image_url,
            spotify_link,
            query,
            post_time.isoformat()
        )
        
        return True, f"Релиз {artist_name} - {album_title} добавлен в очередь на {post_time.strftime('%H:%M, %d.%m')}"
        
    except Exception as e:
        logger.error(f"Ошибка при добавлении релиза по ссылке: {e}")
        logger.error(traceback.format_exc())
        return False, f"Ошибка при добавлении релиза: {str(e)}"import os
import telebot
from telebot import types
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from datetime import datetime, timedelta
import pytz
import time
import logging
import sqlite3
import sys
import traceback
import threading
import json

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
spotify_refresh_token = os.getenv('SPOTIFY_REFRESH_TOKEN')  # Предварительно полученный refresh_token
admin_id = int(os.getenv('TELEGRAM_ADMIN_ID', '7213866'))  # Получаем из переменных окружения или используем значение по умолчанию
channel_id = os.getenv('TELEGRAM_CHANNEL_ID')

# Инициализация Spotify клиента через OAuth с сохраненным refresh token
try:
    # Создаем объект OAuth
    sp_oauth = SpotifyOAuth(
        client_id=spotify_client_id,
        client_secret=spotify_client_secret,
        redirect_uri="http://localhost:8888/callback",  # Любой URI, не используется при refreshing
        scope="user-follow-read user-library-read",
        open_browser=False
    )
    
    # Обновляем токен напрямую с использованием refresh token
    token_info = sp_oauth.refresh_access_token(spotify_refresh_token)
    access_token = token_info['access_token']
    
    # Инициализируем клиент Spotify с полученным токеном
    sp = spotipy.Spotify(auth=access_token)
    logger.info("Spotify авторизация успешна")
    
    # Проверяем, что авторизация работает
    current_user = sp.current_user()
    logger.info(f"Авторизован как: {current_user['display_name']} (ID: {current_user['id']})")
    
except Exception as e:
    logger.error(f"Ошибка при инициализации Spotify: {e}")
    logger.error(traceback.format_exc())
    sp = None

# Инициализация бота - отключаем многопоточность для решения проблемы с конфликтами
bot = telebot.TeleBot(bot_token, parse_mode='HTML', threaded=False)

# Инициализация БД
init_db()

# Функция проверки новых релизов от избранных исполнителей
def check_followed_artists_releases():
    try:
        if not sp:
            logger.error("Spotify клиент не инициализирован, пропускаем проверку")
            return
        
        logger.info("Проверка новых релизов от подписанных исполнителей")
        moscow_tz = pytz.timezone('Europe/Moscow')
        current_time = datetime.now(moscow_tz)
        
        # Точно 7 дней назад - увеличиваем окно поиска, чтобы гарантированно не пропускать релизы
        days_ago = 7
        cutoff_date = current_time - timedelta(days=days_ago)
        cutoff_date_str = cutoff_date.strftime('%Y-%m-%d')
        
        logger.info(f"Поиск релизов с {cutoff_date_str} по {current_time.strftime('%Y-%m-%d')}")
        logger.info(f"Текущий год: {current_time.year}")
        
        new_releases = []
        
        # Получаем текущую очередь - для проверки дубликатов
        current_queue = get_queue()
        queue_spotify_ids = [item[1] for item in current_queue]  # ID релизов в очереди
        
        # Получаем список исполнителей, на которых подписан пользователь
        try:
            followed_artists = []
            results = sp.current_user_followed_artists(limit=50)
            
            while results:
                for item in results['artists']['items']:
                    followed_artists.append(item)
                
                if results['artists']['next']:
                    results = sp.next(results['artists'])
                else:
                    results = None
            
            logger.info(f"Найдено {len(followed_artists)} подписанных исполнителей")
            
            # Проверяем новые релизы для каждого исполнителя
            artist_count = 0
            for artist in followed_artists:
                artist_count += 1
                # Обрабатываем только первые 200 исполнителей для экономии времени и ресурсов
                if artist_count > 200:
                    logger.info(f"Достигнут лимит в 200 исполнителей, пропускаем остальных")
                    break
                    
                artist_id = artist['id']
                artist_name = artist['name']
                
                try:
                    # Получаем альбомы исполнителя, ограничиваем только недавними (10 последних)
                    albums = sp.artist_albums(artist_id, album_type='album,single', limit=10)
                    
                    for album in albums['items']:
                        release_date_str = album.get('release_date', '')
                        album_id = album.get('id', '')
                        
                        # Пропускаем если ID альбома отсутствует
                        if not album_id:
                            continue
                            
                        # Пропускаем, если релиз уже в очереди
                        if album_id in queue_spotify_ids:
                            logger.debug(f"Релиз {artist_name} - {album['name']} уже в очереди, пропускаем")
                            continue
                        
                        # Пропускаем, если релиз уже был опубликован
                        if is_release_posted(album_id):
                            logger.debug(f"Релиз {artist_name} - {album['name']} уже был опубликован, пропускаем")
                            continue
                        
                        if not release_date_str:
                            continue
                        
                        try:
                            # Парсим дату релиза
                            if len(release_date_str) == 4:  # Только год (YYYY)
                                year = int(release_date_str)
                                if year < current_time.year - 1:  # Пропускаем релизы старше прошлого года
                                    logger.debug(f"Пропускаем старый релиз с другим годом: {album['name']} ({release_date_str})")
                                    continue
                                # Для релизов с указанием только года используем 1 января
                                release_date = datetime(year, 1, 1)
                            elif len(release_date_str) == 7:  # Год и месяц (YYYY-MM)
                                year, month = map(int, release_date_str.split('-'))
                                if year < current_time.year - 1:  # Пропускаем релизы старше прошлого года
                                    logger.debug(f"Пропускаем старый релиз с другим годом: {album['name']} ({release_date_str})")
                                    continue
                                # Для релизов с годом и месяцем используем первый день месяца
                                release_date = datetime(year, month, 1)
                            elif len(release_date_str) == 10:  # Полная дата (YYYY-MM-DD)
                                release_date = datetime.strptime(release_date_str, '%Y-%m-%d')
                                # Для полных дат не делаем предварительную фильтрацию по году
                            else:
                                logger.warning(f"Неизвестный формат даты: {release_date_str}, пропускаем")
                                continue
                            
                            # Локализуем дату для корректного сравнения
                            release_date = moscow_tz.localize(release_date)
                            
                            # Рассчитываем разницу в днях напрямую
                            delta = current_time - release_date
                            days_difference = delta.days
                            
                            # Дебаг информация о разнице
                            logger.debug(f"Альбом {album['name']} от {artist_name} - дата: {release_date_str}, разница: {days_difference} дней")
                            
                            # Добавляем релизы за указанный период
                            if 0 <= days_difference <= days_ago:
                                logger.info(f"Найден релиз в диапазоне дат: {album['name']} от {artist_name}, {days_difference} дней назад")
                                
                                # Получаем треки из альбома для примера
                                try:
                                    album_tracks = sp.album_tracks(album_id, limit=1)
                                    
                                    if album_tracks['items']:
                                        track = album_tracks['items'][0]
                                        
                                        release_info = {
                                            'artist': artist_name,
                                            'title': album['name'],
                                            'track_name': track['name'],
                                            'image_url': album['images'][0]['url'] if album['images'] else None,
                                            'spotify_link': album['external_urls']['spotify'],
                                            'spotify_id': album_id,
                                            'release_date': release_date,
                                            'days_old': days_difference,
                                            'query': f"{track['name']} - {artist_name}"
                                        }
                                        new_releases.append(release_info)
                                        logger.info(f"Найден новый релиз: {release_info['artist']} - {release_info['title']}, дата: {release_date_str}")
                                except Exception as e:
                                    logger.error(f"Ошибка при получении треков для альбома {album_id}: {e}")
                            else:
                                if days_difference < 0:
                                    logger.debug(f"Пропускаем будущий релиз {artist_name} - {album['name']}: {release_date_str}")
                                else:
                                    logger.debug(f"Пропускаем старый релиз {artist_name} - {album['name']}: {release_date_str}, {days_difference} дней назад")
                        
                        except ValueError as date_error:
                            logger.warning(f"Ошибка при обработке даты {release_date_str}: {date_error}")
                            continue
                        except Exception as parsing_error:
                            logger.warning(f"Общая ошибка при обработке релиза {album['name']}: {parsing_error}")
                            continue
                
                except Exception as e:
                    logger.error(f"Ошибка при обработке исполнителя {artist_name}: {e}")
                    continue
        
        except Exception as e:
            logger.error(f"Ошибка при получении подписанных исполнителей: {e}")
            logger.error(traceback.format_exc())
        
        # Добавляем только новые релизы в очередь
        if new_releases:
            logger.info(f"Найдено {len(new_releases)} новых релизов. Добавляем в очередь.")
            sorted_releases = sorted(new_releases, key=lambda x: x['release_date'], reverse=True)
            
            queue_start_time = datetime.now(moscow_tz) + timedelta(minutes=5)
            
            for idx, release in enumerate(sorted_releases):
                # В качестве времени поста берем текущее время + индекс (часы)
                post_time = queue_start_time + timedelta(hours=idx)
                
                # Добавляем в очередь только если ID ещё не в очереди и не опубликован
                if not is_release_posted(release['spotify_id']) and release['spotify_id'] not in queue_spotify_ids:
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
        logger.error(f"Ошибка в check_followed_artists_releases: {e}")
        logger.error(traceback.format_exc())

# Функция отправки поста в канал
def post_to_channel(release_from_queue):
    try:
        queue_id, spotify_id, artist, title, image_url, spotify_link, query, post_time = release_from_queue
        
        # Получаем дополнительную информацию об альбоме
        try:
            album = sp.album(spotify_id)
            
            # Определяем тип релиза (альбом или сингл)
            release_type = album.get('album_type', 'Unknown').capitalize()
            
            # Получаем дату релиза
            release_date = album.get('release_date', 'Unknown date')
            
            # Получаем количество треков
            track_count = album.get('total_tracks', 0)
            
            # Получаем жанры
            artist_genres = []
            for artist_item in album.get('artists', [])[:2]:  # Только первые два артиста для экономии запросов
                try:
                    artist_info = sp.artist(artist_item['id'])
                    artist_genres.extend(artist_info.get('genres', []))
                except Exception as genre_error:
                    logger.warning(f"Не удалось получить жанры для артиста {artist_item['name']}: {genre_error}")
                    continue
            
            # Убираем дубликаты и сортируем
            artist_genres = sorted(list(set(artist_genres)))
            
            # Формируем строку жанров с хэштегами
            genre_text = ""
            if artist_genres:
                genre_hashtags = [f"#{genre.replace(' ', '').replace('-', '')}" for genre in artist_genres[:3]]  # Ограничиваем до 3 жанров
                genre_text = f"Genre: {', '.join(genre_hashtags)}"
        except Exception as album_error:
            logger.warning(f"Не удалось получить информацию об альбоме {spotify_id}: {album_error}")
            release_type = "Unknown"
            release_date = "Unknown date"
            track_count = 0
            genre_text = ""
        
        # Формируем текст сообщения согласно формату
        message_text = f"<b>{artist}</b>\n"  # Артист (жирным)
        message_text += f"<b>{title}</b>\n"  # Название релиза (жирным)
        message_text += f"{release_date}, {release_type}, {track_count} tracks\n"  # Информация о релизе
        
        if genre_text:
            message_text += f"{genre_text}"  # Жанры с хэштегами
        
        # Создаем кнопку для Spotify
        keyboard = types.InlineKeyboardMarkup()
        spotify_button = types.InlineKeyboardButton(
            text="Listen on Spotify",
            url=spotify_link
        )
        keyboard.add(spotify_button)
        
        # Отправляем сообщение с обложкой альбома
        if image_url:
            try:
                response = requests.get(image_url, timeout=10)
                if response.status_code == 200:
                    bot.send_photo(channel_id, photo=response.content, caption=message_text, reply_markup=keyboard, parse_mode='HTML')
                else:
                    # Если не удалось загрузить обложку
                    bot.send_message(channel_id, message_text, reply_markup=keyboard, parse_mode='HTML')
            except Exception as img_error:
                logger.warning(f"Ошибка при загрузке обложки: {img_error}")
                bot.send_message(channel_id, message_text, reply_markup=keyboard, parse_mode='HTML')
        else:
            bot.send_message(channel_id, message_text, reply_markup=keyboard, parse_mode='HTML')
        
        # Отмечаем как запощенный и удаляем из очереди
        mark_release_posted(spotify_id)
        remove_from_queue(queue_id)
        logger.debug(f"Успешно запостили релиз: {artist} - {title}")
        
    except Exception as e:
        logger.error(f"Критическая ошибка при отправке поста: {e}")
        logger.error(traceback.format_exc())

# Уведомление админа о очереди
def notify_admin_about_queue(queue_items):
    if not queue_items:
        bot.send_message(admin_id, "Очередь постов пуста")
        return
    
    queue_text = "📋 <b>Очередь постов:</b>\n\n"
    
    for item in queue_items:
        queue_id, spotify_id, artist, title, _, _, _, post_time = item
        post_datetime = datetime.fromisoformat(post_time)
        formatted_time = post_datetime.strftime('%H:%M, %d.%m')
        queue_text += f"{queue_id}. {artist} - {title}\n📅 {formatted_time}\n\n"
    
    queue_text += f"Всего в очереди: {len(queue_items)} постов"
    bot.send_message(admin_id, queue_text)

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
        bot.send_message(message.chat.id, f"У вас нет доступа к этой команде. Ваш ID: {message.from_user.id}, а нужен: {admin_id}")
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

# Расширенный обработчик callback-запросов
@bot.callback_query_handler(func=lambda call: True)
def extended_callback_query(call):
    if call.from_user.id != admin_id:
        bot.answer_callback_query(call.id, "У вас нет прав для этого действия")
        return
    
    # Обработка для удаления элемента
    if call.data.startswith("del_"):
        queue_id = int(call.data.replace("del_", ""))
        remove_from_queue(queue_id)
        bot.answer_callback_query(call.id, "Пост удален из очереди")
        
        # Обновляем сообщение
        queue_items = get_queue()
        if queue_items:
            # Обновляем интерфейс управления очередью
            try:
                bot.edit_message_text("Пост удален. Используйте /manage для обновления интерфейса управления.", 
                                     chat_id=call.message.chat.id, 
                                     message_id=call.message.message_id)
            except:
                pass
            notify_admin_about_queue(queue_items)
        else:
            bot.edit_message_text("Очередь пуста", 
                                 chat_id=call.message.chat.id, 
                                 message_id=call.message.message_id)
    
    # Обработка для очистки всей очереди
    elif call.data == "clear_all":
        clear_queue()
        bot.answer_callback_query(call.id, "Очередь очищена")
        bot.edit_message_text("Очередь полностью очищена", 
                             chat_id=call.message.chat.id, 
                             message_id=call.message.message_id)
    
    # Обработка перемещения элемента вверх
    elif call.data.startswith("up_"):
        queue_id = int(call.data.replace("up_", ""))
        
        # Получаем текущую очередь
        queue_items = get_queue()
        
        # Ищем позицию элемента в очереди
        item_position = None
        for i, item in enumerate(queue_items):
            if item[0] == queue_id:
                item_position = i
                break
        
        # Проверяем, что элемент найден и не является первым
        if item_position is not None and item_position > 0:
            # Получаем элемент и элемент перед ним
            current_item = queue_items[item_position]
            prev_item = queue_items[item_position - 1]
            
            # Меняем местами времена публикации
            conn = sqlite3.connect('bot_data.db', check_same_thread=False)
            c = conn.cursor()
            c.execute("UPDATE queue SET post_time = ? WHERE id = ?", (prev_item[7], current_item[0]))
            c.execute("UPDATE queue SET post_time = ? WHERE id = ?", (current_item[7], prev_item[0]))
            conn.commit()
            conn.close()
            
            bot.answer_callback_query(call.id, "Релиз перемещен вверх в очереди")
        else:
            bot.answer_callback_query(call.id, "Невозможно переместить: элемент уже первый в очереди или не найден")
        
        # Обновляем интерфейс
        bot.edit_message_text("Очередь обновлена. Используйте /manage для обновления интерфейса управления.", 
                             chat_id=call.message.chat.id, 
                             message_id=call.message.message_id)
    
    # Обработка перемещения элемента вниз
    elif call.data.startswith("down_"):
        queue_id = int(call.data.replace("down_", ""))
        
        # Получаем текущую очередь
        queue_items = get_queue()
        
        # Ищем позицию элемента в очереди
        item_position = None
        for i, item in enumerate(queue_items):
            if item[0] == queue_id:
                item_position = i
                break
        
        # Проверяем, что элемент найден и не является последним
        if item_position is not None and item_position < len(queue_items) - 1:
            # Получаем элемент и элемент после него
            current_item = queue_items[item_position]
            next_item = queue_items[item_position + 1]
            
            # Меняем местами времена публикации
            conn = sqlite3.connect('bot_data.db', check_same_thread=False)
            c = conn.cursor()
            c.execute("UPDATE queue SET post_time = ? WHERE id = ?", (next_item[7], current_item[0]))
            c.execute("UPDATE queue SET post_time = ? WHERE id = ?", (current_item[7], next_item[0]))
            conn.commit()
            conn.close()
            
            bot.answer_callback_query(call.id, "Релиз перемещен вниз в очереди")
        else:
            bot.answer_callback_query(call.id, "Невозможно переместить: элемент уже последний в очереди или не найден")
        
        # Обновляем интерфейс
        bot.edit_message_text("Очередь обновлена. Используйте /manage для обновления интерфейса управления.", 
                             chat_id=call.message.chat.id, 
                             message_id=call.message.message_id)
    
    # Обработка информации о релизе
    elif call.data.startswith("info_"):
        queue_id = int(call.data.replace("info_", ""))
        
        # Получаем информацию о релизе
        conn = sqlite3.connect('bot_data.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT * FROM queue WHERE id = ?", (queue_id,))
        item = c.fetchone()
        conn.close()
        
        if item:
            _, spotify_id, artist, title, image_url, spotify_link, _, post_time = item
            post_datetime = datetime.fromisoformat(post_time)
            formatted_time = post_datetime.strftime('%H:%M, %d.%m')
            
            # Формируем детальное сообщение о релизе
            info_text = f"📊 <b>Информация о релизе в очереди:</b>\n\n"
            info_text += f"<b>ID:</b> {queue_id}\n"
            info_text += f"<b>Артист:</b> {artist}\n"
            info_text += f"<b>Название:</b> {title}\n"
            info_text += f"<b>Запланировано на:</b> {formatted_time}\n"
            info_text += f"<b>Spotify ID:</b> {spotify_id}\n"
            
            # Создаем клавиатуру с кнопкой назад
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔙 Назад к управлению", callback_data="back_to_manage"))
            markup.add(types.InlineKeyboardButton("🔄 Опубликовать сейчас", callback_data=f"publish_now_{queue_id}"))
            
            bot.edit_message_text(info_text, 
                                 chat_id=call.message.chat.id, 
                                 message_id=call.message.message_id,
                                 reply_markup=markup,
                                 parse_mode='HTML')
        else:
            bot.answer_callback_query(call.id, "Релиз не найден в очереди")
    
    # Возврат к интерфейсу управления
    elif call.data == "back_to_manage":
        # Отправляем новое сообщение с меню управления
        extended_queue_manage(call.message)
        
        # Удаляем старое сообщение
        try:
            bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
        except:
            pass
    
    # Публикация релиза немедленно
    elif call.data.startswith("publish_now_"):
        queue_id = int(call.data.replace("publish_now_", ""))
        
        # Получаем данные релиза
        conn = sqlite3.connect('bot_data.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT * FROM queue WHERE id = ?", (queue_id,))
        item = c.fetchone()
        conn.close()
        
        if item:
            bot.answer_callback_query(call.id, "Релиз будет опубликован немедленно")
            bot.edit_message_text("Публикация релиза...", 
                                 chat_id=call.message.chat.id, 
                                 message_id=call.message.message_id)
            
            # Публикуем релиз
            try:
                post_to_channel(item)
                bot.edit_message_text("Релиз успешно опубликован!", 
                                     chat_id=call.message.chat.id, 
                                     message_id=call.message.message_id)
            except Exception as e:
                bot.edit_message_text(f"Ошибка при публикации релиза: {str(e)}", 
                                     chat_id=call.message.chat.id, 
                                     message_id=call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "Релиз не найден в очереди")
    
    # Обработка изменения времени публикации
    elif call.data.startswith("time_"):
        queue_id = int(call.data.replace("time_", ""))
        
        # Получаем данные релиза
        conn = sqlite3.connect('bot_data.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT * FROM queue WHERE id = ?", (queue_id,))
        item = c.fetchone()
        conn.close()
        
        if item:
            # Создаем сообщение с инструкцией
            bot.edit_message_text("Введите новую дату и время публикации в формате: ДД.ММ.ГГГГ ЧЧ:ММ\n"
                                 "Например: 01.05.2025 14:30", 
                                 chat_id=call.message.chat.id, 
                                 message_id=call.message.message_id)
            
            # Сохраняем ID релиза для следующего шага
            bot.register_next_step_handler(call.message, process_new_time, queue_id)
        else:
            bot.answer_callback_query(call.id, "Релиз не найден в очереди")

# Обработчик для ввода нового времени публикации
def process_new_time(message, queue_id):
    try:
        # Парсим введенное время
        new_time = datetime.strptime(message.text, '%d.%m.%Y %H:%M')
        
        # Локализуем время
        moscow_tz = pytz.timezone('Europe/Moscow')
        new_time = moscow_tz.localize(new_time)
        
        # Обновляем время публикации в БД
        conn = sqlite3.connect('bot_data.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("UPDATE queue SET post_time = ? WHERE id = ?", (new_time.isoformat(), queue_id))
        conn.commit()
        
        # Получаем информацию о релизе для ответа
        c.execute("SELECT artist, title FROM queue WHERE id = ?", (queue_id,))
        result = c.fetchone()
        conn.close()
        
        if result:
            artist, title = result
            bot.send_message(message.chat.id, f"Время публикации релиза '{artist} - {title}' изменено на {new_time.strftime('%d.%m.%Y %H:%M')}")
        else:
            bot.send_message(message.chat.id, "Время публикации обновлено.")
        
        # Показываем обновленную очередь
        queue_items = get_queue()
        notify_admin_about_queue(queue_items)
    
    except ValueError:
        bot.send_message(message.chat.id, "Неверный формат даты/времени. Используйте формат: ДД.ММ.ГГГГ ЧЧ:ММ")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка при изменении времени: {str(e)}")

# Команда проверки новых релизов
@bot.message_handler(commands=['check'])
def check_updates_command(message):
    logger.debug(f"Команда /check от пользователя {message.from_user.id}")
    if message.from_user.id != admin_id:
        bot.send_message(message.chat.id, f"У вас нет доступа к этой команде. Ваш ID: {message.from_user.id}, а нужен: {admin_id}")
        return
        
    check_message = bot.send_message(message.chat.id, "Проверяю новые релизы от подписанных исполнителей...")
    
    # Запускаем проверку в отдельном потоке, чтобы не блокировать основной поток
    def check_and_update():
        try:
            # Запускаем проверку
            check_followed_artists_releases()
            # Получаем обновленную очередь
            queue_items = get_queue()
            
            # Отправляем результат
            if queue_items:
                bot.edit_message_text(f"Проверка завершена. Найдено {len(queue_items)} релизов в очереди.", 
                                      message.chat.id, check_message.message_id)
                notify_admin_about_queue(queue_items)
            else:
                bot.edit_message_text("Проверка завершена. Новых релизов не найдено.", 
                                     message.chat.id, check_message.message_id)
        except Exception as e:
            logger.error(f"Ошибка при выполнении проверки: {e}")
            bot.edit_message_text("Произошла ошибка при проверке релизов. Подробности в логах.", 
                                 message.chat.id, check_message.message_id)
    
    # Запускаем проверку в отдельном потоке
    check_thread = threading.Thread(target=check_and_update)
    check_thread.daemon = True
    check_thread.start()

# Команда показа очереди
@bot.message_handler(commands=['queue'])
def show_queue(message):
    logger.debug(f"Команда /queue от пользователя {message.from_user.id}")
    if message.from_user.id == admin_id:
        queue_items = get_queue()
        notify_admin_about_queue(queue_items)
    else:
        bot.send_message(message.chat.id, f"У вас нет доступа к этой команде. Ваш ID: {message.from_user.id}, а нужен: {admin_id}")

# Новая команда для отладки
@bot.message_handler(commands=['debug'])
def debug_command(message):
    logger.debug(f"Команда /debug от пользователя {message.from_user.id}")
    if message.from_user.id != admin_id:
        bot.send_message(message.chat.id, f"У вас нет доступа к этой команде. Ваш ID: {message.from_user.id}, а нужен: {admin_id}")
        return
        
    debug_text = "📊 <b>Отладочная информация:</b>\n\n"
    
    # Проверка доступа к Spotify API
    try:
        # Тестовый запрос
        user = sp.current_user()
        followed = sp.current_user_followed_artists(limit=1)
        debug_text += f"✅ Доступ к Spotify API: работает\n"
        debug_text += f"✅ Текущий пользователь: {user['display_name']} ({user['id']})\n"
        debug_text += f"✅ Подписан на исполнителей: {followed['artists']['total']}\n\n"
    except Exception as e:
        debug_text += f"❌ Ошибка доступа к Spotify API: {str(e)}\n\n"
    
    # Проверка БД
    try:
        queue_items = get_queue()
        conn = sqlite3.connect('bot_data.db', check_same_thread=False)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM posted_releases")
        posted_count = c.fetchone()[0]
        conn.close()
        
        debug_text += f"✅ Доступ к базе данных: работает\n"
        debug_text += f"✅ Записей в очереди: {len(queue_items)}\n"
        debug_text += f"✅ Записей опубликованных релизов: {posted_count}\n\n"
    except Exception as e:
        debug_text += f"❌ Ошибка доступа к базе данных: {str(e)}\n\n"
    
    # Даты и время
    moscow_tz = pytz.timezone('Europe/Moscow')
    current_time = datetime.now(moscow_tz)
    debug_text += f"⏰ Текущая дата и время: {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
    debug_text += f"⏰ Ищем релизы с: {(current_time - timedelta(days=3)).strftime('%Y-%m-%d')}\n\n"
    
    # Информация о подключении
    debug_text += f"🤖 Telegram bot API: работает\n"
    debug_text += f"👤 Ваш ID: {message.from_user.id}\n"
    debug_text += f"📢 Channel ID: {channel_id}\n"
    
    bot.send_message(message.chat.id, debug_text)

# Команда для обновления токена Spotify вручную
@bot.message_handler(commands=['refresh_token'])
def refresh_token_command(message):
    logger.debug(f"Команда /refresh_token от пользователя {message.from_user.id}")
    if message.from_user.id != admin_id:
        bot.send_message(message.chat.id, f"У вас нет доступа к этой команде. Ваш ID: {message.from_user.id}, а нужен: {admin_id}")
        return
        
    try:
        global sp
        
        # Обновляем токен вручную
        token_info = sp_oauth.refresh_access_token(spotify_refresh_token)
        access_token = token_info['access_token']
        
        # Переинициализируем клиент Spotify
        sp = spotipy.Spotify(auth=access_token)
        
        # Проверяем, что авторизация работает
        current_user = sp.current_user()
        
        bot.send_message(message.chat.id, 
                         f"✅ Токен успешно обновлен!\n\n"
                         f"Пользователь: {current_user['display_name']} (ID: {current_user['id']})")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка при обновлении токена: {str(e)}")
        logger.error(f"Ошибка при обновлении токена: {e}")
        logger.error(traceback.format_exc())

if __name__ == '__main__':
    logger.info("Запуск бота...")
    
    # Очищаем webhook если он был установлен
    try:
        bot.remove_webhook()
        logger.info("Webhook удален")
    except Exception as e:
        logger.error(f"Ошибка при удалении webhook: {e}")
    
    # Проверяем, что бот единственный экземпляр
    try:
        bot.delete_webhook()
        logger.info("Webhook удален (метод delete_webhook)")
    except Exception as e:
        logger.error(f"Ошибка при удалении webhook альтернативным методом: {e}")
    
    # Периодические задачи
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
                
                # Проверяем новые релизы каждые 3 часа
                if time.time() - last_check_time > 3 * 60 * 60:
                    logger.info("Проверка новых релизов...")
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
    
    # Запускаем фоновые задачи в отдельном потоке
    background_thread = threading.Thread(target=run_background_tasks, daemon=True)
    background_thread.start()
    
    # Сразу запускаем проверку новых релизов при старте
    logger.info("Запуск первичной проверки релизов...")
    
    # НЕ блокируем основной поток для запуска проверки
    check_thread = threading.Thread(target=check_followed_artists_releases, daemon=True)
    check_thread.start()
    
    # Запускаем бота с использованием polling
    logger.info("Бот запущен и готов к работе")
    
    # Создаем отдельные сессии для Telegram API
    while True:
        try:
            # Это запускает новый процесс polling с новым соединением
            bot.stop_polling()
            time.sleep(1)
            bot.polling(none_stop=True, interval=3, timeout=30)
        except Exception as e:
            logger.error(f"Ошибка в polling: {e}")
            logger.error(traceback.format_exc())
            time.sleep(10)  # Увеличиваем паузу при ошибках
