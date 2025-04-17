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
from datetime import datetime, timedelta
from requests.exceptions import ReadTimeout
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import telebot

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

SPOTIFY_CLIENT_ID = os.environ['SPOTIFY_CLIENT_ID']
SPOTIFY_CLIENT_SECRET = os.environ['SPOTIFY_CLIENT_SECRET']
SPOTIFY_REDIRECT_URI = os.environ['SPOTIFY_REDIRECT_URI']
SPOTIFY_REFRESH_TOKEN = os.environ['SPOTIFY_REFRESH_TOKEN']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHANNEL_ID = os.environ['TELEGRAM_CHANNEL_ID']
CHECK_INTERVAL_HOURS = int(os.environ.get('CHECK_INTERVAL_HOURS', 3))
POST_INTERVAL_MINUTES = int(os.environ.get('POST_INTERVAL_MINUTES', 60))
INITIAL_CHECK_DAYS = int(os.environ.get('INITIAL_CHECK_DAYS', 7))

DATA_FILE = 'last_releases.json'
INCLUDE_GENRES = True
MAX_GENRES_TO_SHOW = 5
ADD_POLL = True
POLL_QUESTION = "Rate this release:"
POLL_OPTIONS = ["1", "2", "3", "4", "5"]
POLL_IS_ANONYMOUS = False
message_queue = queue.Queue()
queue_processing = False

MESSAGE_TEMPLATE = """*{artist_name}*
*{release_name}*
{release_date} #{release_type_tag} {total_tracks} tracks
{genres_hashtags}
[Listen on Spotify]({release_url})"""

def escape_markdown_v2(text):
    return re.sub(r'([_*\[\]()~`>#+=|{}.!-])', r'\\\1', text)

def initialize_spotify():
    auth = SpotifyOAuth(client_id=SPOTIFY_CLIENT_ID,
                        client_secret=SPOTIFY_CLIENT_SECRET,
                        redirect_uri=SPOTIFY_REDIRECT_URI,
                        scope="user-follow-read user-library-read")
    token = auth.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    return spotipy.Spotify(auth=token['access_token'], requests_timeout=10)

sp = initialize_spotify()
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def convert_to_hashtag(text):
    return "#" + re.sub(r'[^\w\s]', '', text).replace(' ', '_').lower()

def load_last_releases():
    return json.load(open(DATA_FILE)) if os.path.exists(DATA_FILE) else {}

def save_last_releases(data):
    json.dump(data, open(DATA_FILE, "w"))

def get_artist_genres(artist_id):
    try:
        return sp.artist(artist_id).get('genres', [])
    except:
        return []

def get_followed_artists():
    followed = []
    result = sp.current_user_followed_artists(limit=50)
    while result:
        for artist in result['artists']['items']:
            genres = artist.get('genres') or get_artist_genres(artist['id'])
            followed.append({
                'id': artist['id'],
                'name': artist['name'],
                'genres': genres
            })
        result = sp.next(result['artists']) if result['artists']['next'] else None
    return followed

def get_artist_releases(artist_id, last_check):
    albums = []
    try:
        result = sp.artist_albums(artist_id, album_type='album,single', limit=50, country='US')
        for a in result['items']:
            if a['release_date'] >= last_check:
                albums.append({
                    'id': a['id'],
                    'name': a['name'],
                    'type': a['album_type'],
                    'release_date': a['release_date'],
                    'url': a['external_urls']['spotify'],
                    'image_url': a['images'][0]['url'] if a['images'] else None,
                    'total_tracks': a.get('total_tracks', 0)
                })
    except:
        pass
    return albums

def build_caption(artist, release):
    name = escape_markdown_v2(artist['name'])
    title = escape_markdown_v2(release['name'])
    rtype = escape_markdown_v2(release['type'])
    url = escape_markdown_v2(release['url'])
    tags = ""
    if INCLUDE_GENRES and artist.get('genres'):
        tags = " ".join([convert_to_hashtag(g) for g in artist['genres'][:MAX_GENRES_TO_SHOW]])
    return MESSAGE_TEMPLATE.format(
        artist_name=name,
        release_name=title,
        release_date=release['release_date'],
        release_type_tag=rtype,
        total_tracks=release['total_tracks'],
        genres_hashtags=tags,
        release_url=url
    )

def send_to_telegram(artist, release):
    try:
        caption = build_caption(artist, release)
        if release['image_url']:
            bot.send_photo(TELEGRAM_CHANNEL_ID, release['image_url'], caption=caption, parse_mode="MarkdownV2")
        else:
            bot.send_message(TELEGRAM_CHANNEL_ID, caption, parse_mode="MarkdownV2")
        time.sleep(2)
        if ADD_POLL:
            bot.send_poll(TELEGRAM_CHANNEL_ID,
                          f"{POLL_QUESTION} {artist['name']} - {release['name'][:40]}",
                          POLL_OPTIONS, is_anonymous=POLL_IS_ANONYMOUS)
    except Exception as e:
        logger.error(f"Telegram error: {e}")

def queue_post(artist, release):
    message_queue.put((artist, release))
    global queue_processing
    if not queue_processing:
        threading.Thread(target=process_queue).start()

def process_queue():
    global queue_processing
    queue_processing = True
    while not message_queue.empty():
        artist, release = message_queue.get()
        send_to_telegram(artist, release)
        wait = max(60, POST_INTERVAL_MINUTES * 60 + random.randint(-30, 30))
        logger.info(f"Waiting {wait} sec before next post")
        time.sleep(wait)
    queue_processing = False

def check_new_releases():
    last = load_last_releases()
    today = datetime.now().strftime('%Y-%m-%d')
    for artist in get_followed_artists():
        aid = artist['id']
        last_check = last.get(aid, {}).get('last_check_date') or (datetime.now() - timedelta(days=INITIAL_CHECK_DAYS)).strftime('%Y-%m-%d')
        for r in get_artist_releases(aid, last_check):
            known = last.get(aid, {}).get('known_releases', [])
            if r['id'] not in known:
                queue_post(artist, r)
                last.setdefault(aid, {}).setdefault('known_releases', []).append(r['id'])
                last[aid]['last_check_date'] = today
    save_last_releases(last)
    logger.info(f"Queue size after scan: {message_queue.qsize()}")

def run_bot():
    logger.info("Starting bot...")
    schedule.every(CHECK_INTERVAL_HOURS).hours.do(check_new_releases)
    check_new_releases()
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    run_bot()
