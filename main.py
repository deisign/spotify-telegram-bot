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
