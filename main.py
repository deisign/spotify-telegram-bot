
import os
import logging
import time
import traceback
import random
import json
import queue
import threading
import re
import schedule
import re

def escape_markdown_v2(text):
    escape_chars = r"\\_*[]()~`>#+-=|{}.!\""
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\\\\1", text)


from datetime import datetime, timedelta
from requests.exceptions import ReadTimeout
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import telebot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

# ENV
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI')
SPOTIFY_REFRESH_TOKEN = os.environ.get('SPOTIFY_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
CHECK_INTERVAL_HOURS = int(os.environ.get('CHECK_INTERVAL_HOURS', '12'))
POST_INTERVAL_MINUTES = int(os.environ.get('POST_INTERVAL_MINUTES', '60'))
INITIAL_CHECK_DAYS = int(os.environ.get('INITIAL_CHECK_DAYS', '7'))

# Valid
for var in ['SPOTIFY_CLIENT_ID','SPOTIFY_CLIENT_SECRET','SPOTIFY_REDIRECT_URI','SPOTIFY_REFRESH_TOKEN','TELEGRAM_BOT_TOKEN','TELEGRAM_CHANNEL_ID']:
    if not os.environ.get(var): logger.error(f"Missing {var}"); exit(1)

DATA_FILE = 'last_releases.json'
INCLUDE_GENRES = True
MAX_GENRES_TO_SHOW = 5
POLL_QUESTION = "Rate this release:"
POLL_OPTIONS = ["1", "2", "3", "4", "5"]
POLL_IS_ANONYMOUS = False
message_queue = queue.Queue()
queue_processing = False

# Format caption
def build_caption(artist, release):
    caption = f"*{escape_markdown_v2(artist['name'])}*\n"
    caption += f"*{escape_markdown_v2(release['name'])}*\n"
    caption += f"{release['release_date']} #{release['type']} {release['total_tracks']} tracks\n"
    if INCLUDE_GENRES and artist.get('genres'):
        hashtags = [f"#{re.sub(r'[^\w\s]', '', g).replace(' ', '_').lower()}" for g in artist['genres'][:MAX_GENRES_TO_SHOW]]
        caption += " ".join(hashtags) + "\n"
    caption += "🎧 [Listen on Spotify](" + release['url'] + ")"
    return caption

def initialize_spotify():
    auth_manager = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope="user-follow-read user-library-read"
    )
    token_info = auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    return spotipy.Spotify(auth=token_info['access_token'], requests_timeout=10)

sp = initialize_spotify()
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def load_last_releases():
    return json.load(open(DATA_FILE)) if os.path.exists(DATA_FILE) else {}

def save_last_releases(data):
    json.dump(data, open(DATA_FILE, "w"))

def get_followed_artists():
    results = sp.current_user_followed_artists(limit=50)
    artists = []
    while results:
        for item in results['artists']['items']:
            genres = item.get('genres') or sp.artist(item['id']).get('genres', [])
            artists.append({'id': item['id'], 'name': item['name'], 'genres': genres})
        results = sp.next(results['artists']) if results['artists']['next'] else None
    return artists

def get_artist_releases(artist_id, last_check_date):
    albums = []
    try:
        results = sp.artist_albums(artist_id, album_type="album,single", limit=50, country="US")
        for album in results['items']:
            if album['release_date'] >= last_check_date:
                albums.append({
                    'id': album['id'],
                    'name': album['name'],
                    'type': album['album_type'],
                    'release_date': album['release_date'],
                    'url': album['external_urls']['spotify'],
                    'image_url': album['images'][0]['url'] if album['images'] else None,
                    'total_tracks': album.get('total_tracks', 0)
                })
    except ReadTimeout:
        logger.warning(f"Timeout getting releases for {artist_id}")
    return albums

def queue_post(artist, release):
    message_queue.put((artist, release))
    global queue_processing
    if not queue_processing:
        threading.Thread(target=process_message_queue).start()

def process_message_queue():
    global queue_processing
    queue_processing = True
    while not message_queue.empty():
        artist, release = message_queue.get()
        try:
            caption = build_caption(artist, release)
            if release['image_url']:
                msg = bot.send_photo(TELEGRAM_CHANNEL_ID, release['image_url'], caption=caption, parse_mode="MarkdownV2")
            else:
                msg = bot.send_message(TELEGRAM_CHANNEL_ID, caption, parse_mode="MarkdownV2")
            time.sleep(2)
            bot.send_poll(
                chat_id=TELEGRAM_CHANNEL_ID,
                question=f"{POLL_QUESTION} {escape_markdown_v2(artist['name'])} - {escape_markdown_v2(release['name'])[:40]}",
                options=POLL_OPTIONS,
                is_anonymous=POLL_IS_ANONYMOUS
            )
        except Exception as e:
            logger.error(f"Error posting: {e}")
        sleep_time = POST_INTERVAL_MINUTES * 60 + random.randint(-30, 30)
        logger.info(f"Waiting {sleep_time} sec before next post")
        time.sleep(max(60, sleep_time))
    queue_processing = False

def check_new_releases():
    global sp
    try: sp.current_user()
    except: sp = initialize_spotify()

    last_releases = load_last_releases()
    today = datetime.now().strftime('%Y-%m-%d')
    for artist in get_followed_artists():
        artist_id = artist['id']
        last_check = last_releases.get(artist_id, {}).get('last_check_date') or (datetime.now() - timedelta(days=INITIAL_CHECK_DAYS)).strftime('%Y-%m-%d')
        releases = get_artist_releases(artist_id, last_check)
        for release in releases:
            known_ids = last_releases.get(artist_id, {}).get('known_releases', [])
            if release['id'] not in known_ids:
                queue_post(artist, release)
                last_releases.setdefault(artist_id, {}).setdefault("known_releases", []).append(release['id'])
                last_releases[artist_id]['last_check_date'] = today
    save_last_releases(last_releases)

def run_bot():
    logger.info("Running bot with interval every {} hour(s)".format(CHECK_INTERVAL_HOURS))
    schedule.every(CHECK_INTERVAL_HOURS).hours.do(check_new_releases)
    check_new_releases()
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    try:
        run_bot()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logger.error(traceback.format_exc())
