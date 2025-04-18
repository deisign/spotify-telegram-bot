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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Load environment variables from Railway
logger.info("Loading environment variables")
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI', 'https://spotify-refresh-token-generator.netlify.app/callback')
SPOTIFY_REFRESH_TOKEN = os.environ.get('SPOTIFY_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
CHECK_INTERVAL_HOURS = int(os.environ.get('CHECK_INTERVAL_HOURS', '12'))
POST_INTERVAL_MINUTES = int(os.environ.get('POST_INTERVAL_MINUTES', '60'))
INITIAL_CHECK_DAYS = int(os.environ.get('INITIAL_CHECK_DAYS', '7'))

os.environ['SPOTIPY_CLIENT_ID'] = SPOTIFY_CLIENT_ID
os.environ['SPOTIPY_CLIENT_SECRET'] = SPOTIFY_CLIENT_SECRET
os.environ['SPOTIPY_REDIRECT_URI'] = SPOTIFY_REDIRECT_URI

INCLUDE_GENRES = True
MAX_GENRES_TO_SHOW = 5
ADD_POLL = True
POLL_QUESTION = "Rate this release:"
POLL_OPTIONS = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
POLL_IS_ANONYMOUS = False

message_queue = queue.Queue()
queue_processing = False

DATA_FILE = 'last_releases.json'

MESSAGE_TEMPLATE = """*{artist_name}*
*{release_name}*
{release_date} #{release_type_tag} {total_tracks} tracks
{genres_hashtags}
🎧 [Listen on Spotify]({release_url})"""

