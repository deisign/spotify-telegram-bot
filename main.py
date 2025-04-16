
import os
import logging
from datetime import datetime

import spotipy
from spotipy.oauth2 import SpotifyOAuth
import telebot
import time
import schedule
import json
import queue
import threading
import traceback
import random
import re

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

logger.info("=== ENVIRONMENT VARIABLES DEBUG ===")
logger.info(f"SPOTIFY_CLIENT_ID: {SPOTIFY_CLIENT_ID}")
logger.info(f"SPOTIFY_CLIENT_SECRET: {'SET' if SPOTIFY_CLIENT_SECRET else 'NOT SET'}")
logger.info(f"SPOTIFY_REDIRECT_URI: {SPOTIFY_REDIRECT_URI}")
logger.info(f"SPOTIFY_REFRESH_TOKEN: {'SET' if SPOTIFY_REFRESH_TOKEN else 'NOT SET'}")
logger.info(f"TELEGRAM_BOT_TOKEN: {'SET' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
logger.info(f"TELEGRAM_CHANNEL_ID: {TELEGRAM_CHANNEL_ID}")
logger.info(f"CHECK_INTERVAL_HOURS: {CHECK_INTERVAL_HOURS}")
logger.info(f"POST_INTERVAL_MINUTES: {POST_INTERVAL_MINUTES}")
logger.info(f"INITIAL_CHECK_DAYS: {INITIAL_CHECK_DAYS}")

required_env_vars = [
    'SPOTIFY_CLIENT_ID',
    'SPOTIFY_CLIENT_SECRET',
    'TELEGRAM_BOT_TOKEN',
    'TELEGRAM_CHANNEL_ID'
]
missing_vars = [var for var in required_env_vars if not os.environ.get(var)]

if missing_vars:
    logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
    print(f"EXITING: Missing vars: {missing_vars}")
    exit(1)

def initialize_spotify():
    logger.info("Initializing Spotify client...")
    auth_manager = SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope="user-follow-read user-library-read"
    )
    token_info = auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
    return spotipy.Spotify(auth=token_info['access_token'])

sp = initialize_spotify()
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def debug_spotify_api():
    logger.info("===== DEBUG SPOTIFY API =====")
    try:
        user = sp.current_user()
        logger.info(f"Connected as: {user['display_name']} (ID: {user['id']})")
        artists = sp.current_user_followed_artists(limit=5)
        for artist in artists['artists']['items']:
            logger.info(f"Followed artist: {artist['name']} ({artist['id']})")
    except Exception as e:
        logger.error(f"Debug error: {e}")
        logger.error(traceback.format_exc())

def run_bot():
    logger.info("Bot running with schedule...")
    schedule.every(CHECK_INTERVAL_HOURS).hours.do(debug_spotify_api)
    debug_spotify_api()
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    try:
        debug_spotify_api()
        run_bot()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logger.error(traceback.format_exc())
