import logging
import time
import threading
import traceback
import os
import spotipy
import telebot
from spotipy.oauth2 import SpotifyOAuth

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получаем данные из переменных окружения Railway
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')  # Исправлено имя переменной
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI')
SPOTIFY_REFRESH_TOKEN = os.environ.get('SPOTIFY_REFRESH_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
CHECK_INTERVAL_HOURS = int(os.environ.get('CHECK_INTERVAL_HOURS', 3))

# Проверяем наличие токена бота
if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не найден в переменных окружения!")
    # Выводим все переменные окружения для диагностики (без значений токенов)
    for key in os.environ:
        value = os.environ[key]
        # Скрываем чувствительные данные
        if 'TOKEN' in key or 'SECRET' in key:
            logger.info(f"Переменная окружения: {key} = ***")
        else:
            logger.info(f"Переменная окружения: {key} = {value}")
    
    # Завершаем программу с ошибкой
    raise ValueError("TELEGRAM_BOT_TOKEN не определен в переменных окружения!")

# Инициализация бота Telegram
logger.info(f"Инициализация бота с токеном (первые 5 символов): {TELEGRAM_BOT_TOKEN[:5]}***")
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Инициализация Spotify OAuth
sp_oauth = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI
)

# Инициализация переменной для хранения объекта Spotify
spotify_refresh_token = SPOTIFY_REFRESH_TOKEN
sp = None  # Будет инициализирован при первом обновлении токена

# Функции для проверки релизов и очереди
def check_followed_artists_releases():
    try:
        logger.info("Проверяем новые релизы артистов...")
        # Ваш код для проверки релизов
    except Exception as e:
        logger.error(f"Ошибка при проверке релизов: {e}")
        logger.error(traceback.format_exc())

def check_and_post_from_queue():
    try:
        # Ваш код для проверки и обработки очереди
        pass
    except Exception as e:
        logger.error(f"Ошибка при обработке очереди: {e}")

# Обработчики команд Telegram
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    logger.info(f"Получена команда /start или /help от пользователя {message.from_user.id}")
    bot.reply_to(message, "Привет! Я бот для отслеживания новых релизов на Spotify. Используйте /help для получения списка команд.")

@bot.message_handler(commands=['check'])
def force_check(message):
    logger.info(f"Получена команда /check от пользователя {message.from_user.id}")
    bot.reply_to(message, "Запускаю проверку новых релизов...")
    
    # Запускаем проверку в отдельном потоке, чтобы не блокировать бота
    check_thread = threading.Thread(target=check_followed_artists_releases, daemon=True)
    check_thread.start()

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
    bot.reply_to(message, "Я понимаю только команды. Используйте /help для получения списка команд.")

# Отслеживаем время запуска бота
start_time = time.time()

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
