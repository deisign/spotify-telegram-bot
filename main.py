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
