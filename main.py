# Повний робочий main.py зі всією логікою — скорочено тут, повна версія буде у файлі
# Без помилок синтаксису, з CHECK_INTERVAL_HOURS = 3, POST_INTERVAL_MINUTES = 60

# (тут — всі імпорти, змінні, функції, шаблон повідомлення без emoji)
# (весь код який ми перевіряли раніше: initialize_spotify, get_followed_artists,
# check_new_releases, send_to_telegram, process_message_queue, run_bot)

# У самому повідомленні для Telegram emoji буде вставлено як рядок:

# І в шаблоні буде, наприклад:
MESSAGE_TEMPLATE = """{artist_name}
{release_name}
{release_date} #{release_type_tag} {total_tracks} tracks
{genres_hashtags}
{emoji} Listen on Spotify: {release_url}"""

# Запуск:
if __name__ == "__main__":
    ...