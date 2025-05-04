import os
import asyncio
import aiohttp
import aiogram
from aiogram import Bot, Dispatcher, types, BaseMiddleware
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message
import requests
import sqlite3
import json
import logging
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import time
from urllib.parse import urlparse
import re

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Переменные окружения
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REFRESH_TOKEN = os.environ.get('SPOTIFY_REFRESH_TOKEN')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
CHECK_INTERVAL_HOURS = int(os.environ.get('CHECK_INTERVAL_HOURS', 3))
DATABASE_PATH = 'bot_data.db'

# Инициализация бота
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Rest of the code goes here...
# Database operations
class Database:
    @staticmethod
    def get_connection():
        conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
        # Register datetime adapter
        sqlite3.register_adapter(datetime, lambda val: val.isoformat())
        sqlite3.register_converter("TIMESTAMP", lambda val: datetime.fromisoformat(val.decode()))
        return conn

    @staticmethod
    def add_to_queue(release_data):
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO post_queue (artist, release, release_date, release_type, tracks_count, genres, image_url, listen_url, platform, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            release_data['artist'],
            release_data['release'],
            release_data['release_date'],
            release_data['release_type'],
            release_data['tracks_count'],
            release_data['genres'],
            release_data['image_url'],
            release_data['listen_url'],
            release_data['platform'],
            datetime.now()
        ))
        conn.commit()
        conn.close()

    @staticmethod
    def get_queue():
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id, artist, release, release_date, release_type, tracks_count, genres, image_url, listen_url, platform FROM post_queue ORDER BY id')
        results = cursor.fetchall()
        conn.close()
        return results

    @staticmethod
    def remove_from_queue(queue_id):
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM post_queue WHERE id = ?', (queue_id,))
        conn.commit()
        conn.close()

    @staticmethod
    def clear_queue():
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM post_queue')
        conn.commit()
        conn.close()

    @staticmethod
    def mark_as_posted(release_id, artist, release):
        conn = Database.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO posted_releases (id, artist, release, date_posted)
            VALUES (?, ?, ?, ?)
        ''', (release_id, artist, release, datetime.now()))
        conn.commit()
        conn.close()

# Handler for help and start commands
@dp.message(CommandStart())
@dp.message(Command("help"))
async def cmd_help(message: Message):
    help_text = """
🤖 Spotify Release Tracker Bot

Available commands:
/help - Show this help message
/check - Check for new releases now
/queue - Show posting queue
/queue_clear - Clear entire queue
/status - Show bot status

You can also send Spotify or Bandcamp links to add them to the queue.
"""
    await message.reply(help_text)

# Handler for regular messages (including URLs)
@dp.message()
async def handle_message(message: Message):
    text = message.text
    if not text:
        return

    logger.info(f"Received message: {text}")

    # Check if message contains Spotify URL
    if 'spotify.com' in text:
        logger.info("Found Spotify URL")
        url_match = re.search(r'https?://open\.spotify\.com/(album|track)/([a-zA-Z0-9]+)', text)
        if url_match:
            item_type, item_id = url_match.groups()
            logger.info(f"Matched {item_type} with ID: {item_id}")
            
            # Simple test - just add to queue with dummy data
            release_data = {
                'artist': 'Test Artist',
                'release': 'Test Release',
                'release_date': '2025-05-04',
                'release_type': 'album',
                'tracks_count': 10,
                'genres': '#rock #pop',
                'image_url': '',
                'listen_url': text,
                'platform': 'spotify'
            }
            Database.add_to_queue(release_data)
            await message.reply("✅ Added to posting queue!")
        else:
            logger.info("No match found for Spotify regex")
            await message.reply("❌ Invalid Spotify link format")

# Simple command handlers
@dp.message(Command("queue"))
async def cmd_queue(message: Message):
    queue = Database.get_queue()
    if not queue:
        await message.reply("📭 Post queue is empty.")
        return

    text = "📋 *Post Queue:*\n\n"
    for idx, item in enumerate(queue, 1):
        text += f"{idx}. {item[1]} - {item[2]} ({item[9]})\n"
    
    await message.reply(text, parse_mode="Markdown")

@dp.message(Command("queue_clear"))
async def cmd_queue_clear(message: Message):
    Database.clear_queue()
    await message.reply("✅ Queue cleared.")

# Main function
async def main():
    # Initialize database
    init_db()
    
    # Start polling
    await dp.start_polling(bot)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect(DATABASE_PATH, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
    # Register datetime adapter
    sqlite3.register_adapter(datetime, lambda val: val.isoformat())
    sqlite3.register_converter("TIMESTAMP", lambda val: datetime.fromisoformat(val.decode()))
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS posted_releases (
            id TEXT PRIMARY KEY,
            artist TEXT,
            release TEXT,
            date_posted TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS post_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artist TEXT,
            release TEXT,
            release_date TEXT,
            release_type TEXT,
            tracks_count INTEGER,
            genres TEXT,
            image_url TEXT,
            listen_url TEXT,
            platform TEXT,
            created_at TIMESTAMP,
            scheduled_time TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

if __name__ == '__main__':
    asyncio.run(main())
