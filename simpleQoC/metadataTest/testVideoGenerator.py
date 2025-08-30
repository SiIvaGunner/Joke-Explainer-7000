import json
import os
from pathlib import Path
from inspect import getsourcefile

from bot_secrets import YOUTUBE_API_KEY
from simpleQoC.metadata import get_playlist_videos

TEST_DIR = Path(os.path.abspath(getsourcefile(lambda:0))).parent

PLAYLIST_ID = input("Paste the playlist ID: ")
OUT_FILE = input("Output filename: ")

videos = get_playlist_videos(PLAYLIST_ID, YOUTUBE_API_KEY)
with open(TEST_DIR / OUT_FILE, 'w', encoding='utf-8') as file:
    json.dump(videos, file, indent=4)