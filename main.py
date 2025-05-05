import asyncio
import logging
import os
import re
from datetime import datetime

import spotipy
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from spotipy.oauth2 import SpotifyOAuth

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# Initialize services
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Spotify setup
auth_manager = SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri="http://localhost:8888/callback",
    scope="user-follow-read"
)

sp = spotipy.Spotify(auth_manager=auth_manager)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Бот работает! Попробуйте отправить ссылку на альбом Spotify")

@dp.message()
async def handle_message(message: Message):
    if message.text and "open.spotify.com/album/" in message.text:
        album_id = re.search(r"album/([a-zA-Z0-9]+)", message.text).group(1)
        
        try:
            album = sp.album(album_id)
            artist = album['artists'][0]['name']
            album_name = album['name']
            
            response = f"🎵 {artist} - {album_name}\n"
            response += f"📅 {album['release_date']}\n"
            response += f"🔗 {message.text}"
            
            await message.answer(response)
        except Exception as e:
            await message.answer(f"Ошибка: {e}")
    else:
        await message.answer("Отправьте ссылку на альбом Spotify")

async def main():
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
