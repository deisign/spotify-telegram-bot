import os
from dotenv import load_dotenv

# Load environment variables from .env file (for local development)
load_dotenv()

# Spotify API
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI', 'http://localhost:8888/callback')

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')

# Settings
CHECK_INTERVAL_HOURS = int(os.environ.get('CHECK_INTERVAL_HOURS', '12'))
DATA_FILE = 'last_releases.json'

# Display settings
INCLUDE_GENRES = True        # Include artist genres
MAX_GENRES_TO_SHOW = 5       # Maximum number of genres to display

# Poll settings
ADD_POLL = True              # Add a poll to rate the release
POLL_QUESTION = "Rate this release:"
POLL_OPTIONS = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
POLL_IS_ANONYMOUS = False    # Is the poll anonymous

# Message template
MESSAGE_TEMPLATE = """🎵 *New release from {artist_name}*

*{release_name}*
Type: {release_type}
Release date: {release_date}
Tracks: {total_tracks}
{genres_line}
[Listen on Spotify]({release_url})"""

# Genre line template
GENRES_TEMPLATE = "Genre: {genres}"
