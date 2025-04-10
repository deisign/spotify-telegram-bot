import time
import logging
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
import json
import schedule
import os

# Import configuration
try:
    from config import (
        SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI,
        TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, CHECK_INTERVAL_HOURS,
        DATA_FILE, MESSAGE_TEMPLATE, GENRES_TEMPLATE,
        INCLUDE_GENRES, MAX_GENRES_TO_SHOW,
        ADD_POLL, POLL_QUESTION, POLL_OPTIONS, POLL_IS_ANONYMOUS
    )
except ImportError:
    # If config file is not found, use environment variables directly
    SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID')
    SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET')
    SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI', 'https://spotify-refresh-token-generator.netlify.app/callback')
    SPOTIFY_REFRESH_TOKEN = os.environ.get('SPOTIFY_REFRESH_TOKEN')
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
    CHECK_INTERVAL_HOURS = int(os.environ.get('CHECK_INTERVAL_HOURS', '12'))
    DATA_FILE = 'last_releases.json'
    
    # Default display settings
    INCLUDE_GENRES = True
    MAX_GENRES_TO_SHOW = 5
    
    # Poll settings
    ADD_POLL = True
    POLL_QUESTION = "Rate this release:"
    POLL_OPTIONS = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
    POLL_IS_ANONYMOUS = False
    
    # Default message template
    MESSAGE_TEMPLATE = """🎵 *New release from {artist_name}*

*{release_name}*
Type: {release_type}
Release date: {release_date}
Tracks: {total_tracks}
{genres_line}
[Listen on Spotify]({release_url})"""
    
    GENRES_TEMPLATE = "Genre: {genres}"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Initialize Spotify API with refresh token support
try:
    SPOTIFY_REFRESH_TOKEN = os.environ.get('SPOTIFY_REFRESH_TOKEN')
    
    if SPOTIFY_REFRESH_TOKEN:
        # Use refresh token for authorization
        logger.info("Using refresh token for Spotify authentication")
        auth_manager = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="user-follow-read",
            cache_path=".spotify_cache"
        )
        
        # Get access token from refresh token
        token_info = auth_manager.refresh_access_token(SPOTIFY_REFRESH_TOKEN)
        sp = spotipy.Spotify(auth=token_info['access_token'])
    else:
        # Standard browser-based authorization (for local development)
        logger.info("Using standard OAuth flow for Spotify authentication")
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope="user-follow-read",
            cache_path=".spotify_cache"
        ))
    
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
        return {}
    except Exception as e:
        logger.error(f"Error loading data: {e}")
        return {}

def save_last_releases(data):
    """Save data about last releases to file"""
    try:
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)
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
                results = sp.next(results['artists'])
            else:
                results = None
        
        logger.info(f"Found {len(followed_artists)} followed artists")
    except Exception as e:
        logger.error(f"Error getting followed artists: {e}")
    
    return followed_artists

def get_artist_releases(artist_id, last_check_date=None):
    """Get artist releases since last check date"""
    if not last_check_date:
        # If last check date is not specified, check releases for the last month
        last_check_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    
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
    """Send information about new release to Telegram channel"""
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
        
        # Send message with image if available
        if release['image_url']:
            sent_message = bot.send_photo(
                TELEGRAM_CHANNEL_ID,
                release['image_url'],
                caption=message,
                parse_mode='Markdown'
            )
        else:
            sent_message = bot.send_message(
                TELEGRAM_CHANNEL_ID,
                message,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
        
        # Add poll for rating the release if enabled
        if ADD_POLL:
            try:
                poll_message = f"{artist_name} - {release['name']}"
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
                
                logger.info(f"Poll created for release: {artist_name} - {release['name']}")
            except Exception as poll_error:
                logger.error(f"Error creating poll: {poll_error}")
        
        logger.info(f"Message sent about release: {artist_name} - {release['name']}")
        return True
    except Exception as e:
        logger.error(f"Error sending message to Telegram: {e}")
        return False

def check_new_releases():
    """Check for new releases from followed artists"""
    logger.info("Starting new releases check")
    
    # Load data about last releases
    last_releases = load_last_releases()
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Get list of followed artists
    followed_artists = get_followed_artists()
    
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
                    # Send message about new release
                    if send_to_telegram(artist, release):
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
    logger.info("New releases check completed")

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
    
    missing_vars = [var for var in required_env_vars if not locals().get(var)]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        exit(1)
    
    try:
        run_bot()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"An error occurred: {e}")
