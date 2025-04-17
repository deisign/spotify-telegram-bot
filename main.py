
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI')
SPOTIFY_REFRESH_TOKEN = os.environ.get('SPOTIFY_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
CHECK_INTERVAL_HOURS = int(os.environ.get('CHECK_INTERVAL_HOURS', '12'))
POST_INTERVAL_MINUTES = int(os.environ.get('POST_INTERVAL_MINUTES', '60'))
INITIAL_CHECK_DAYS = int(os.environ.get('INITIAL_CHECK_DAYS', '7'))

required = {
    "SPOTIFY_CLIENT_ID": SPOTIFY_CLIENT_ID,
    "SPOTIFY_CLIENT_SECRET": SPOTIFY_CLIENT_SECRET,
    "SPOTIFY_REDIRECT_URI": SPOTIFY_REDIRECT_URI,
    "SPOTIFY_REFRESH_TOKEN": SPOTIFY_REFRESH_TOKEN,
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "TELEGRAM_CHANNEL_ID": TELEGRAM_CHANNEL_ID
}
for key, value in required.items():
    if not value:
        logger.error(f"Missing environment variable: {key}")
        exit(1)

DATA_FILE = 'last_releases.json'
MESSAGE_TEMPLATE = '\n'.join([
    "*{artist_name}*",
    "*{release_name}*",
    "{release_date} #{release_type_tag} {total_tracks} tracks",
    "{genres_hashtags}",
    "[Listen on Spotify]({release_url})"
])
INCLUDE_GENRES = True
MAX_GENRES_TO_SHOW = 5
ADD_POLL = True
POLL_QUESTION = "Rate this release:"
POLL_OPTIONS = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
POLL_IS_ANONYMOUS = False
message_queue = queue.Queue()
queue_processing = False

def initialize_spotify():
    logger.info("Initializing Spotify...")
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

def convert_to_hashtag(text):
    return f"#{re.sub(r'[^\w\s]', '', text).replace(' ', '_').lower()}"

def load_last_releases():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_last_releases(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

def get_artist_genres(artist_id):
    global sp
    try:
        artist_info = sp.artist(artist_id)
        return artist_info.get('genres', [])
    except ReadTimeout:
        logger.warning("Timeout while getting artist genres")
        return []

def get_followed_artists():
    logger.info("Getting followed artists...")
    global sp
    artists = []
    try:
        results = sp.current_user_followed_artists(limit=50)
        while results:
            for item in results['artists']['items']:
                data = {
                    'id': item['id'],
                    'name': item['name'],
                    'genres': item.get('genres') or get_artist_genres(item['id'])
                }
                artists.append(data)
            results = sp.next(results['artists']) if results['artists']['next'] else None
    except Exception as e:
        logger.error(f"Failed to get followed artists: {e}")
    return artists

def get_artist_releases(artist_id, last_check_date):
    logger.info(f"Getting releases for artist {artist_id}")
    global sp
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

def send_to_telegram(artist, release):
    try:
        genres_hashtags = ""
        if INCLUDE_GENRES and artist.get('genres'):
            hashtags = [convert_to_hashtag(g) for g in artist['genres'][:MAX_GENRES_TO_SHOW]]
            genres_hashtags = " ".join(hashtags)
        release_type_tag = convert_to_hashtag(release['type'])

        message = MESSAGE_TEMPLATE.format(
            artist_name=artist['name'],
            release_name=release['name'],
            release_date=release['release_date'],
            release_type_tag=release_type_tag,
            total_tracks=release['total_tracks'],
            genres_hashtags=genres_hashtags,
            release_url=release['url']
        )

        bot.send_message(TELEGRAM_CHANNEL_ID, message, parse_mode="Markdown", disable_web_page_preview=False)
        logger.info(f"Posted to Telegram: {artist['name']} - {release['name']}")
        if ADD_POLL:
            time.sleep(2)
            bot.send_poll(
                TELEGRAM_CHANNEL_ID,
                f"{POLL_QUESTION} {artist['name']} - {release['name'][:40]}",
                POLL_OPTIONS,
                is_anonymous=POLL_IS_ANONYMOUS
            )
    except Exception as e:
        logger.error(f"Error posting to Telegram: {e}")

def check_new_releases():
    global sp
    logger.info("=== CHECKING FOR NEW RELEASES ===")
    try:
        sp.current_user()
    except ReadTimeout:
        logger.warning("Timeout validating Spotify session")
        sp = initialize_spotify()

    last_releases = load_last_releases()
    today = datetime.now().strftime('%Y-%m-%d')
    artists = get_followed_artists()

    for artist in artists:
        artist_id = artist['id']
        last_check = last_releases.get(artist_id, {}).get('last_check_date') or (datetime.now() - timedelta(days=INITIAL_CHECK_DAYS)).strftime('%Y-%m-%d')
        releases = get_artist_releases(artist_id, last_check)

        for release in releases:
            known_ids = last_releases.get(artist_id, {}).get('known_releases', [])
            if release['id'] not in known_ids:
                send_to_telegram(artist, release)
                last_releases.setdefault(artist_id, {}).setdefault("known_releases", []).append(release['id'])
                last_releases[artist_id]["last_check_date"] = today

    save_last_releases(last_releases)
    logger.info("=== CHECK COMPLETE ===")

def run_bot():
    logger.info("Starting bot...")
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
