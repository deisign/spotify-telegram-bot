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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "https://spotify-refresh-token-generator.netlify.app/callback")
SPOTIFY_REFRESH_TOKEN = os.environ.get("SPOTIFY_REFRESH_TOKEN")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
CHECK_INTERVAL_HOURS = int(os.environ.get("CHECK_INTERVAL_HOURS", "3"))
POST_INTERVAL_MINUTES = int(os.environ.get("POST_INTERVAL_MINUTES", "60"))
INITIAL_CHECK_DAYS = int(os.environ.get("INITIAL_CHECK_DAYS", "7"))

os.environ["SPOTIPY_CLIENT_ID"] = SPOTIFY_CLIENT_ID
os.environ["SPOTIPY_CLIENT_SECRET"] = SPOTIFY_CLIENT_SECRET
os.environ["SPOTIPY_REDIRECT_URI"] = SPOTIFY_REDIRECT_URI

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
message_queue = queue.Queue()
queue_processing = False

MESSAGE_TEMPLATE = """{artist_name}
{release_name}
{release_date} #{release_type_tag} {total_tracks} tracks
{genres_hashtags}
🎧 [Listen on Spotify]({release_url})"""

POLL_QUESTION = "Rate this release:"
POLL_OPTIONS = ["1", "2", "3", "4", "5"]
POLL_IS_ANONYMOUS = False
ADD_POLL = True

DATA_FILE = "last_releases.json"
INCLUDE_GENRES = True
MAX_GENRES_TO_SHOW = 5

def initialize_spotify():
    auth_manager = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope="user-follow-read"
    )
    token_info = auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    return spotipy.Spotify(auth=token_info["access_token"])

sp = initialize_spotify()

def convert_to_hashtag(text):
    hashtag = re.sub(r"[^\w\s]", "", text).replace(" ", "_").lower()
    return f"#{hashtag}"

def load_last_releases():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_last_releases(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

def get_artist_genres(artist_id):
    try:
        return sp.artist(artist_id).get("genres", [])
    except:
        return []

def get_followed_artists():
    results = sp.current_user_followed_artists(limit=50)
    followed_artists = []
    while results:
        for item in results["artists"]["items"]:
            artist = {
                "id": item["id"],
                "name": item["name"],
                "genres": item.get("genres") or get_artist_genres(item["id"])
            }
            followed_artists.append(artist)
        if results["artists"]["next"]:
            results = sp.next(results["artists"])
        else:
            break
    return followed_artists

def date_is_after(release_date, check_date):
    if len(release_date) == 4:
        release_date += "-01-01"
    elif len(release_date) == 7:
        release_date += "-01"
    return release_date >= check_date

def get_artist_releases(artist_id, last_check_date=None):
    if not last_check_date:
        last_check_date = (datetime.now() - timedelta(days=INITIAL_CHECK_DAYS)).strftime("%Y-%m-%d")
    albums = []
    results = sp.artist_albums(artist_id, album_type="album,single", limit=50, country="US")
    while results:
        for album in results["items"]:
            release_date = album["release_date"]
            if date_is_after(release_date, last_check_date):
                albums.append({
                    "id": album["id"],
                    "name": album["name"],
                    "type": album["album_type"],
                    "release_date": release_date,
                    "url": album["external_urls"]["spotify"],
                    "image_url": album["images"][0]["url"] if album["images"] else None,
                    "total_tracks": album.get("total_tracks", 0)
                })
        if results["next"]:
            results = sp.next(results)
        else:
            break
    return albums

def send_to_telegram(artist, release):
    genres = artist["genres"][:MAX_GENRES_TO_SHOW]
    hashtags = " ".join([convert_to_hashtag(g) for g in genres])
    message = MESSAGE_TEMPLATE.format(
        artist_name=artist["name"],
        release_name=release["name"],
        release_date=release["release_date"],
        release_type_tag=convert_to_hashtag(release["type"]),
        total_tracks=release["total_tracks"],
        genres_hashtags=hashtags,
        release_url=release["url"]
    )
    message_queue.put({
        "artist": artist,
        "release": release,
        "message": message,
        "image_url": release["image_url"]
    })
    global queue_processing
    if not queue_processing:
        threading.Thread(target=process_message_queue).start()

def process_message_queue():
    global queue_processing
    queue_processing = True
    while not message_queue.empty():
        item = message_queue.get()
        try:
            msg = bot.send_photo(
                TELEGRAM_CHANNEL_ID,
                item["image_url"],
                caption=item["message"],
                parse_mode="Markdown",
            )
            time.sleep(2)
            poll_msg = f"{item['artist']['name']} - {item['release']['name']}"
            if len(poll_msg) > 80:
                poll_msg = poll_msg[:77] + "..."
            bot.send_poll(
                TELEGRAM_CHANNEL_ID,
                question=POLL_QUESTION + " " + poll_msg,
                options=POLL_OPTIONS,
                is_anonymous=POLL_IS_ANONYMOUS,
                disable_notification=True
            )
        except Exception as e:
            logger.error(f"Error sending message: {e}")
        message_queue.task_done()
        sleep_time = max(60, POST_INTERVAL_MINUTES * 60 + random.randint(-60, 60))
        logger.info(f"Waiting {sleep_time} seconds before next post...")
        time.sleep(sleep_time)
    queue_processing = False

def check_new_releases():
    logger.info("Checking new releases...")
    last_releases = load_last_releases()
    today = datetime.now().strftime("%Y-%m-%d")
    followed = get_followed_artists()
    for artist in followed:
        aid = artist["id"]
        last_check = last_releases.get(aid, {}).get("last_check_date")
        known_ids = last_releases.get(aid, {}).get("known_releases", [])
        releases = get_artist_releases(aid, last_check)
        for r in releases:
            if r["id"] not in known_ids:
                send_to_telegram(artist, r)
                last_releases.setdefault(aid, {}).setdefault("known_releases", []).append(r["id"])
                last_releases[aid]["last_check_date"] = today
    save_last_releases(last_releases)

def run_bot():
    logger.info(f"Running bot with interval every {CHECK_INTERVAL_HOURS} hour(s)")
    check_new_releases()
    schedule.every(CHECK_INTERVAL_HOURS).hours.do(check_new_releases)
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    run_bot()