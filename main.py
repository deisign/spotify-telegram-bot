import time
import logging
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import telebot
from datetime import datetime, timedelta
import json
import schedule
import os
import threading
import queue
import random
import re
import traceback

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Environment variables
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", 3))
POST_INTERVAL_MINUTES = int(os.getenv("POST_INTERVAL_MINUTES", 60))
INITIAL_CHECK_DAYS = int(os.getenv("INITIAL_CHECK_DAYS", 7))

# Spotify init
os.environ["SPOTIPY_CLIENT_ID"] = SPOTIFY_CLIENT_ID
os.environ["SPOTIPY_CLIENT_SECRET"] = SPOTIFY_CLIENT_SECRET
os.environ["SPOTIPY_REDIRECT_URI"] = SPOTIFY_REDIRECT_URI

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(scope="user-follow-read user-library-read"))
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

DATA_FILE = "last_releases.json"
message_queue = queue.Queue()
queue_processing = False

MESSAGE_TEMPLATE = """{artist_name}
{release_name}
{release_date} #{release_type_tag} {total_tracks} tracks
{genres_hashtags}
🎧 Listen on Spotify"""

POLL_QUESTION = "Rate this release:"
POLL_OPTIONS = ["1", "2", "3", "4", "5"]
POLL_IS_ANONYMOUS = False

def convert_to_hashtag(text):
    return "#" + re.sub(r"[^\w\s]", "", text).replace(" ", "_").lower()

def load_last_releases():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_last_releases(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

def get_artist_genres(artist_id):
    try:
        artist = sp.artist(artist_id)
        return artist.get("genres", [])
    except:
        return []

def get_followed_artists():
    results = sp.current_user_followed_artists(limit=50)
    artists = results["artists"]["items"]
    followed = []
    for a in artists:
        followed.append({
            "id": a["id"],
            "name": a["name"],
            "genres": get_artist_genres(a["id"])
        })
    return followed

def get_artist_releases(artist_id, last_check_date):
    results = sp.artist_albums(artist_id, album_type="album,single", country="US", limit=50)
    albums = []
    for album in results["items"]:
        date = album["release_date"]
        if len(date) == 4:
            date += "-01-01"
        elif len(date) == 7:
            date += "-01"
        if date >= last_check_date:
            albums.append({
                "id": album["id"],
                "name": album["name"],
                "release_date": album["release_date"],
                "type": album["album_type"],
                "url": album["external_urls"]["spotify"],
                "image_url": album["images"][0]["url"] if album["images"] else None,
                "total_tracks": album["total_tracks"]
            })
    return albums

def send_to_telegram(artist, release):
    genres = artist.get("genres", [])[:5]
    hashtags = " ".join([convert_to_hashtag(g) for g in genres])
    tag = convert_to_hashtag(release["type"])
    msg = MESSAGE_TEMPLATE.format(
        artist_name=artist["name"],
        release_name=release["name"],
        release_date=release["release_date"],
        release_type_tag=tag,
        total_tracks=release["total_tracks"],
        genres_hashtags=hashtags
    )
    message_queue.put({
        "photo": release["image_url"],
        "caption": msg,
        "poll_question": POLL_QUESTION + f" {artist['name']} - {release['name']}"
    })

def process_queue():
    global queue_processing
    queue_processing = True
    while not message_queue.empty():
        data = message_queue.get()
        try:
            msg = bot.send_photo(TELEGRAM_CHANNEL_ID, photo=data["photo"], caption=data["caption"], parse_mode="Markdown")
            time.sleep(2)
            bot.send_poll(TELEGRAM_CHANNEL_ID, question=data["poll_question"], options=POLL_OPTIONS, is_anonymous=POLL_IS_ANONYMOUS)
        except Exception as e:
            logger.error(f"Error sending: {e}")
        time.sleep(POST_INTERVAL_MINUTES * 60)
    queue_processing = False

def check_new_releases():
    logger.info("Checking new releases...")
    last_releases = load_last_releases()
    today = datetime.now().strftime("%Y-%m-%d")
    artists = get_followed_artists()
    for artist in artists:
        artist_id = artist["id"]
        last_date = last_releases.get(artist_id, {}).get("last_check_date", (datetime.now() - timedelta(days=INITIAL_CHECK_DAYS)).strftime("%Y-%m-%d"))
        releases = get_artist_releases(artist_id, last_date)
        for release in releases:
            send_to_telegram(artist, release)
        last_releases[artist_id] = {
            "last_check_date": today
        }
    save_last_releases(last_releases)
    if not queue_processing:
        threading.Thread(target=process_queue).start()

def run_bot():
    check_new_releases()
    schedule.every(CHECK_INTERVAL_HOURS).hours.do(check_new_releases)
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    run_bot()
