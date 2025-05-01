# В начало файла добавляем блокировку файла
import fcntl
import sys

# Функция для проверки, что запущен только один экземпляр
def ensure_single_instance():
    """Гарантирует, что запущен только один экземпляр скрипта"""
    global lock_file
    lock_file_path = "/tmp/spotify_telegram_bot.lock"
    
    try:
        # Открываем файл блокировки
        lock_file = open(lock_file_path, "w")
        
        # Пытаемся получить эксклюзивную блокировку
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info("Успешно получена блокировка файла, запускаем единственный экземпляр бота")
        
        # Записываем PID в файл блокировки
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        
        return True
    except IOError:
        # Не удалось получить блокировку, значит другой экземпляр уже запущен
        logger.error("Другой экземпляр бота уже запущен. Завершаем работу.")
        return False

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

# Функции для проверки релизов и очереди
def check_followed_artists_releases():
    try:
        logger.info("Проверяем новые релизы артистов...")
        # Здесь должен быть ваш код для проверки релизов
        # Если найдены новые релизы, добавляем их в очередь
        # Пример: add_to_queue({"type": "release", "artist": "Название артиста", "album": "Название альбома", "url": "..."})
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
            # Пытаемся опубликовать в канал
            # Пример: 
            # message = f"🎵 Новый релиз!\n\n{item['artist']} - {item['album']}\n\n{item['url']}"
            # bot.send_message(TELEGRAM_CHANNEL_ID, message)
            
            # Если публикация успешна, удаляем из очереди
            remove_from_queue(0)
        except Exception as e:
            logger.error(f"Ошибка при публикации: {e}")
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
        "/status - показать статус бота"
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
    queue_message = "🔄 Текущая очередь публикаций:\n\n"
    for i, item in enumerate(current_queue):
        try:
            # Предполагаем, что item - это словарь с информацией о релизе
            # Формат элемента очереди может быть разным, адаптируйте под свой формат
            if isinstance(item, dict) and 'type' in item and item['type'] == 'release':
                queue_message += f"{i+1}. {item.get('artist', 'Неизвестный артист')} - {item.get('album', 'Неизвестный альбом')}\n"
            else:
                queue_message += f"{i+1}. {str(item)}\n"
        except Exception as e:
            logger.error(f"Ошибка при форматировании элемента очереди: {e}")
            queue_message += f"{i+1}. [Ошибка форматирования]\n"
    
    # Добавляем инструкцию по управлению очередью
    queue_message += "\nДля удаления элемента из очереди отправьте: /queue_remove [номер]"
    
    bot.reply_to(message, queue_message)

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
    logger.info(f"Получено неизвестное сообщение от пользователя {message.from_user.id}: {message.text}")
    bot.reply_to(message, "Я понимаю только команды. Используйте /help для получения списка команд.")

# Отслеживаем время запуска бота
start_time = time.time()

if __name__ == '__main__':
    logger.info("Запуск бота...")
    
    # Проверяем, что запущен только один экземпляр
    if not ensure_single_instance():
        logger.error("Завершение работы из-за обнаружения другого запущенного экземпляра")
        sys.exit(1)
    
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
    
    # Используем бесконечный цикл с одним экземпляром polling
    try:
        # Сбрасываем все предыдущие обновления, чтобы избежать конфликтов
        updates = bot.get_updates(offset=-1, limit=1, timeout=1)
        if updates:
            last_update_id = updates[-1].update_id
            bot.get_updates(offset=last_update_id+1, timeout=1)
            logger.info(f"Сброшены предыдущие обновления, последний ID: {last_update_id}")
        
        logger.info("Запуск единственного экземпляра polling")
        bot.infinity_polling(timeout=60, long_polling_timeout=30)
    except Exception as e:
        logger.error(f"Критическая ошибка в polling: {e}")
        logger.error(traceback.format_exc())
