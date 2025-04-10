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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Load environment variables
logger.info("Loading environment variables")
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI', 'https://spotify-refresh-token-generator.netlify.app/callback')
SPOTIFY_REFRESH_TOKEN = os.environ.get('SPOTIFY_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
CHECK_INTERVAL_HOURS = int(os.environ.get('CHECK_INTERVAL_HOURS', '12'))
POST_INTERVAL_MINUTES = int(os.environ.get('POST_INTERVAL_MINUTES', '60'))  # Default: 1 post per hour
INITIAL_CHECK_DAYS = int(os.environ.get('INITIAL_CHECK_DAYS', '7'))  # Default: check last 7 days on first run

# Debug log environment variables (but hide secrets)
logger.info(f"SPOTIFY_CLIENT_ID: {'SET' if SPOTIFY_CLIENT_ID else 'NOT SET'}")
logger.info(f"SPOTIFY_CLIENT_SECRET: {'SET' if SPOTIFY_CLIENT_SECRET else 'NOT SET'}")
logger.info(f"SPOTIFY_REDIRECT_URI: {SPOTIFY_REDIRECT_URI}")
logger.info(f"SPOTIFY_REFRESH_TOKEN: {'SET' if SPOTIFY_REFRESH_TOKEN else 'NOT SET'}")
logger.info(f"TELEGRAM_BOT_TOKEN: {'SET' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")
logger.info(f"TELEGRAM_CHANNEL_ID: {TELEGRAM_CHANNEL_ID}")
logger.info(f"CHECK_INTERVAL_HOURS: {CHECK_INTERVAL_HOURS}")
logger.info(f"POST_INTERVAL_MINUTES: {POST_INTERVAL_MINUTES}")
logger.info(f"INITIAL_CHECK_DAYS: {INITIAL_CHECK_DAYS}")

# Also set them as environment variables for spotipy library
os.environ['SPOTIPY_CLIENT_ID'] = SPOTIFY_CLIENT_ID
os.environ['SPOTIPY_CLIENT_SECRET'] = SPOTIFY_CLIENT_SECRET
os.environ['SPOTIPY_REDIRECT_URI'] = SPOTIFY_REDIRECT_URI

# Default display settings
INCLUDE_GENRES = True
MAX_GENRES_TO_SHOW = 5

# Poll settings
ADD_POLL = True
POLL_QUESTION = "Rate this release:"
POLL_OPTIONS = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
POLL_IS_ANONYMOUS = False

# Message queue for throttled posting
message_queue = queue.Queue()
queue_processing = False

# Data file
DATA_FILE = 'last_releases.json'

# Default message template
MESSAGE_TEMPLATE = """🎵 *New release from {artist_name}*

*{release_name}*
Type: {release_type}
Release date: {release_date}
Tracks: {total_tracks}
{genres_line}
[Listen on Spotify]({release_url})"""

GENRES_TEMPLATE = "Genre: {genres}"

# Initialize Spotify API
try:
    logger.info("Initializing Spotify API")
    
    if SPOTIFY_REFRESH_TOKEN:
        # Use refresh token for authorization
        logger.info("Using refresh token for Spotify authentication")
        auth_manager = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="user-follow-read"
        )
        
        # Get access token from refresh token
        token_info = auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
        sp = spotipy.Spotify(auth=token_info['access_token'])
    else:
        # Standard browser-based authorization (for local development)
        logger.info("Using standard OAuth flow for Spotify authentication")
        auth_manager = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="user-follow-read"
        )
        sp = spotipy.Spotify(auth_manager=auth_manager)
    
    logger.info("Spotify API initialized successfully")
except Exception as e:
    logger.error(f"Error initializing Spotify API: {e}")
    raise

# Initialize Telegram bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

def load_last_releases():
    """Load data about last releases from file"""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        else:
            logger.info(f"No existing {DATA_FILE} found, creating new data")
        return {}
    except Exception as e:
        logger.error(f"Error loading data: {e}")
        return {}

def save_last_releases(data):
    """Save data about last releases to file"""
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)
            logger.info(f"Successfully saved data to {DATA_FILE}")
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def get_artist_genres(artist_id):
    """Get artist genres"""
    try:
        artist_info = sp.artist(artist_id)
        return artist_info.get('genres', [])
    except Exception as e:
        logger.error(f"Error getting artist genres: {e}")
        return []

def get_followed_artists():
    """Get list of followed artists on Spotify"""
    followed_artists = []
    try:
        results = sp.current_user_followed_artists(limit=50)
        
        while results:
            for item in results['artists']['items']:
                artist_data = {
                    'id': item['id'],
                    'name': item['name'],
                    'uri': item['uri']
                }
                
                # Get genres if needed
                if INCLUDE_GENRES:
                    artist_data['genres'] = item.get('genres', [])
                    
                    # If genres were not obtained from the followed artists list,
                    # request them directly
                    if not artist_data['genres']:
                        artist_data['genres'] = get_artist_genres(item['id'])
                
                followed_artists.append(artist_data)
            
            if results['artists']['next']:
                results = sp.next(results)
            else:
                results = None
        
        logger.info(f"Found {len(followed_artists)} followed artists")
    except Exception as e:
        logger.error(f"Error getting followed artists: {e}")
    
    return followed_artists

def get_artist_releases(artist_id, last_check_date=None):
    """Get artist releases since last check date"""
    if not last_check_date:
        # If last check date is not specified, check releases for the configured days
        last_check_date = (datetime.now() - timedelta(days=INITIAL_CHECK_DAYS)).strftime('%Y-%m-%d')
        logger.info(f"No last check date, using {INITIAL_CHECK_DAYS} days ago: {last_check_date}")
    
    albums = []
    try:
        results = sp.artist_albums(artist_id, album_type='album,single', limit=50)
        
        while results:
            for album in results['items']:
                # Check that the album was released after the last check date
                release_date = album['release_date']
                if release_date >= last_check_date:
                    # Get additional information about the release
                    album_info = {
                        'name': album['name'],
                        'type': album['album_type'],
                        'release_date': release_date,
                        'url': album['external_urls']['spotify'],
                        'image_url': album['images'][0]['url'] if album['images'] else None,
                        'total_tracks': album.get('total_tracks', 0)
                    }
                    
                    albums.append(album_info)
            
            if results['next']:
                results = sp.next(results)
            else:
                results = None
    except Exception as e:
        logger.error(f"Error getting artist releases: {e}")
    
    return albums

def send_to_telegram(artist, release):
    """Prepare message and add it to the queue"""
    try:
        artist_name = artist['name']
        
        # Prepare genre line
        genres_line = ""
        if INCLUDE_GENRES and artist.get('genres'):
            # Get first MAX_GENRES_TO_SHOW genres
            genres = artist['genres'][:MAX_GENRES_TO_SHOW]
            
            # Format genre string
            genres_str = ", ".join(genres)
            
            # If there are more genres than MAX_GENRES_TO_SHOW, add "and more"
            if len(artist['genres']) > MAX_GENRES_TO_SHOW:
                genres_str += " and more"
            
            genres_line = GENRES_TEMPLATE.format(genres=genres_str)
        
        # Prepare base message
        message = MESSAGE_TEMPLATE.format(
            artist_name=artist_name,
            release_name=release['name'],
            release_type=release['type'].capitalize(),
            release_date=release['release_date'],
            total_tracks=release.get('total_tracks', 'N/A'),
            release_url=release['url'],
            genres_line=genres_line
        )

        # Add message to queue
        message_queue.put({
            'artist_name': artist_name,
            'release_name': release['name'],
            'message': message,
            'image_url': release['image_url'],
            'add_poll': ADD_POLL
        })
        
        logger.info(f"Added to posting queue: {artist_name} - {release['name']}")
        
        # Start queue processing if not already running
        global queue_processing
        if not queue_processing:
            threading.Thread(target=process_message_queue).start()
        
        return True
    except Exception as e:
        logger.error(f"Error preparing message for Telegram: {e}")
        return False

def process_message_queue():
    """Process message queue with delays between posts"""
    global queue_processing
    queue_processing = True
    
    logger.info("Started message queue processing thread")
    
    try:
        while not message_queue.empty():
            # Get next message from queue
            message_data = message_queue.get()
            
            try:
                # Send message with image if available
                if message_data['image_url']:
                    sent_message = bot.send_photo(
                        TELEGRAM_CHANNEL_ID,
                        message_data['image_url'],
                        caption=message_data['message'],
                        parse_mode='Markdown'
                    )
                else:
                    sent_message = bot.send_message(
                        TELEGRAM_CHANNEL_ID,
                        message_data['message'],
                        parse_mode='Markdown',
                        disable_web_page_preview=False
                    )
                
                logger.info(f"Posted message: {message_data['artist_name']} - {message_data['release_name']}")
                
                # Add poll if enabled
                if message_data['add_poll']:
                    try:
                        # Wait a moment before posting poll
                        time.sleep(1)
                        
                        poll_message = f"{message_data['artist_name']} - {message_data['release_name']}"
                        if len(poll_message) > 100:
                            # Telegram poll question length limit
                            poll_message = poll_message[:97] + "..."
                        
                        poll_question = f"{POLL_QUESTION} {poll_message}"
                        
                        # Create poll
                        bot.send_poll(
                            TELEGRAM_CHANNEL_ID,
                            question=poll_question,
                            options=POLL_OPTIONS,
                            is_anonymous=POLL_IS_ANONYMOUS
                        )
                        
                        logger.info(f"Poll created for release: {message_data['artist_name']} - {message_data['release_name']}")
                    except Exception as poll_error:
                        logger.error(f"Error creating poll: {poll_error}")
                
                # Mark message as processed
                message_queue.task_done()
                
                # Wait before posting next message
                # Add some randomness to make it look more natural
                sleep_time = POST_INTERVAL_MINUTES * 60 + random.randint(-60, 60)
                sleep_time = max(60, sleep_time)  # Ensure at least 1 minute
                logger.info(f"Waiting {sleep_time} seconds before posting next message")
                time.sleep(sleep_time)
                
            except Exception as e:
                logger.error(f"Error posting message: {e}")
                message_queue.task_done()
    except Exception as e:
        logger.error(f"Error in message queue processing thread: {e}")
    
    queue_processing = False
    logger.info("Message queue processing thread finished")

def check_new_releases():
    """Check for new releases from followed artists"""
    logger.info("Starting new releases check")
    
    # Load data about last releases
    last_releases = load_last_releases()
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Get list of followed artists
    followed_artists = get_followed_artists()
    new_releases_found = 0
    
    for artist in followed_artists:
        artist_id = artist['id']
        artist_name = artist['name']
        
        # Get last check date for the artist
        last_check_date = last_releases.get(artist_id, {}).get('last_check_date', None)
        
        # Get artist releases
        releases = get_artist_releases(artist_id, last_check_date)
        
        if releases:
            logger.info(f"Found {len(releases)} new releases for artist {artist_name}")
            
            # Get last known release
            last_known_release = last_releases.get(artist_id, {}).get('last_release', None)
            
            for release in releases:
                # Check if the release is new
                is_new = not last_known_release or release['name'] != last_known_release['name']
                
                if is_new:
                    # Add release to the posting queue
                    if send_to_telegram(artist, release):
                        new_releases_found += 1
                        
                        # Update information about last release
                        if artist_id not in last_releases:
                            last_releases[artist_id] = {}
                        
                        last_releases[artist_id]['last_release'] = release
                        last_releases[artist_id]['last_check_date'] = today
        else:
            # Update last check date
            if artist_id in last_releases:
                last_releases[artist_id]['last_check_date'] = today
            else:
                last_releases[artist_id] = {'last_check_date': today}
    
    # Save data about last releases
    save_last_releases(last_releases)
    logger.info(f"New releases check completed. Found {new_releases_found} new releases.")

def run_bot():
    """Run bot on schedule"""
    logger.info("Starting Spotify to Telegram bot")
    
    # Schedule new releases check with specified interval
    schedule.every(CHECK_INTERVAL_HOURS).hours.do(check_new_releases)
    
    # Run check at start
    check_new_releases()
    
    # Run scheduler
    while True:
        schedule.run_pending()
        time.sleep(60)

if __name__ == "__main__":
    # Check for all required environment variables
    required_env_vars = [
        'SPOTIFY_CLIENT_ID',
        'SPOTIFY_CLIENT_SECRET', 
        'TELEGRAM_BOT_TOKEN',
        'TELEGRAM_CHANNEL_ID'
    ]
    
    missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        exit(1)
    
    try:
        run_bot()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"An error occurred: {e}")
