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

# ... (код до строки 713 пропущен для краткости)

        try:
            # Получаем альбомы, используя параметр include_groups вместо album_type
            results = sp.artist_albums(
    artist_id, 
    album_type='album,single',  # Заменено include_groups на album_type
    limit=50,
    country='US'
)
        except spotipy.client.SpotifyException as se:
            # If token expired, reinitialize Spotify client
            if se.http_status == 401:
                logger.warning("Token expired, refreshing Spotify client")
                sp = initialize_spotify()
                 results = sp.artist_albums(
    artist_id, 
    album_type='album,single',  # Заменено include_groups на album_type
    limit=50,
    country='US'
)
            else:
                raise

# ... (остальной код опущен)
