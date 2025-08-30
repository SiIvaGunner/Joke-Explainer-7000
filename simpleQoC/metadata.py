import re
import requests
from typing import Tuple, List, Dict, Set
import json

import os
from pathlib import Path
from inspect import getsourcefile

PATTERNS_FILE = Path(os.path.abspath(getsourcefile(lambda:0))).parent / 'patterns.json'

class MetadataException(Exception):
    def __init__(self, message, *args):
        self.message = message # without this you may get DeprecationWarning
  
        # allow users initialize misc. arguments as any other builtin Error
        super(MetadataException, self).__init__(message, *args) 


def get_playlist_details(playlist_id, api_key):
    url = 'https://www.googleapis.com/youtube/v3/playlists'
    params = {
        'part': 'snippet',
        'id': playlist_id,
        'key': api_key
    }

    response = requests.get(url, params=params)
    response.raise_for_status()  # Raises an HTTPError for bad responses
    data = response.json()

    if 'error' in data:
        raise MetadataException(f"API Error: {data['error']['message']}")
    elif 'items' in data and len(data['items']) > 0:
        playlist_title = data['items'][0]['snippet']['title']
        playlist_creator = data['items'][0]['snippet']['channelTitle']
        return playlist_title, playlist_creator
    else:
        raise MetadataException("Playlist not found or empty.")
    

def get_playlist_videos(playlist_id, api_key) -> List[Dict[str, str]]:
    videos = []
    next_page_token = None

    while True:
        url = f'https://www.googleapis.com/youtube/v3/playlistItems'
        params = {
            'part': 'snippet',
            'playlistId': playlist_id,
            'key': api_key,
            'pageToken': next_page_token
        }

        response = requests.get(url, params=params)
        response.raise_for_status()  # Raises an HTTPError for bad responses
        data = response.json()

        if 'error' in data:
            raise MetadataException(f"API Error: {data['error']['message']}")

        for item in data.get('items', []):
            videos.append({k: item['snippet'][k] for k in ['title', 'description']})

        next_page_token = data.get('nextPageToken')
        if not next_page_token:
            break

    return videos


def remove_links(text):
    # Regular expression pattern to match URLs
    url_pattern = r'http[s]?://\S+|www\.\S+|https?://\S+'
    # Replace all matches with "[link]"
    return re.sub(url_pattern, '[link]', text)


def desc_to_dict(description: str, start_line: int) -> Tuple[Dict[str, str], Set[str]]:
    """
    Convert a description text to dictionary.
    The description should be in the format "A: B\netc."
    """
    desc = {}
    messages = set()
    for line in description.splitlines()[start_line:]:
        if len(line) == 0:
            # Let's just ignore empty lines. Most of the time they're going to be intentional, otherwise they're very easy to spot
            continue
        try:
            key, value = line.split(':', 1)
            if key in desc.keys():
                messages.add(f'Duplicate key ``{key}`` found.')
            if len(value) > 0:
                if value[0] == ' ': value = value[1:]
                else: messages.add(f'Missing space in ``{key}`` line.')
                value = value.rstrip()
        except ValueError:
            # ignore last line if ppl decide to put channel desc
            if line != description.splitlines()[-1]:
                line_short = line if len(line) < 15 else f'{line[:7]}...{line[-7:]}'
                messages.add(f'Irregular line without : found in description: "{line_short}"')
            continue
        desc[key] = value
    
    return desc, messages


def get_music_from_desc(desc: Dict[str, str]) -> str:
    if len(desc) == 0:
        return ""
    try:
        track = desc['Music']
    except KeyError:
        track = desc[list(desc.keys())[0]]
    return track


def crosscheck_description_key(key: str, video_descs: List[str], threshold: float):
    """
    Check if key is present in existing video descriptions, e.g. at least 50%.
    If not, it is likely there is a typo in the key, e.g. "Platlist".
    Set threshold to 0 to instead just check if key is present in any video.
    """
    # return sum([key in desc_to_dict(desc.replace('\r', '').split('\n\n')[0], 0)[0].keys() for desc in video_descs]) / len(video_descs) > threshold
    if len(video_descs) == 0:
        return True

    if threshold > 0:
        return sum([key in desc for desc in video_descs]) / len(video_descs) > threshold
    else:
        return any([key in desc for desc in video_descs])


def checkMetadata(description: str, channel_name: str, playlist_id: str, api_key: str, advanced: bool) -> Tuple[int, List[str]]:
    """
    Perform metadata checking.

    Types of metadata errors detected:
    - Title is longer than 100 characters
    - Linked playlist is not from `channel_name`
    - Title (first line of description) already exists in playlist
    - Any patterns in `patterns.json` under "MISTAKE"
    - [Advanced] Any field in `description`, e.g. "Music:", that does not appear in more than 50% of videos in playlist
    - [Advanced] Title does not match any regex patterns in `patterns.json` under "TITLE", given the track name in description
    - [Advanced] Game name does not match playlist name, or any videos in playlist
    """
    messages = set()
    desc_lines = description.splitlines()
    title = desc_lines[0]

    # Title limit check
    if len(title) > 100:
        messages.add('Title has more than 100 characters.')

    # Parse description for more in-depth metadata checking
    desc, adv_messages = desc_to_dict(description, 1)

    if 'Playlist' in desc.keys():
        if len(desc['Playlist']) == 0 and len(playlist_id) > 0:
            # If the Playlist field exists but it is empty and playlist_id is not empty,
            # likely a rare case of the Joke line containing a playlist not intended as for the rip.
            playlist_id = ""
        elif len(desc['Playlist']) > 0 and len(playlist_id) == 0:
            # Playlist field contains non-playlist link
            # raise issue if it is not a redirect link or Google Drive link,
            # assuming any other intentional cases are rare
            if 'drive' not in desc['Playlist'] and 'redirect' not in desc['Playlist']:
                adv_messages.add('Playlist field is not a valid playlist, YouTube redirect or Drive link. Ignore if this is intentional.')

    # Check metadata based on provided playlist ID
    if len(playlist_id) > 0:
        try:
            try:
                playlist_name, channel = get_playlist_details(playlist_id, api_key)
                videos = get_playlist_videos(playlist_id, api_key)
        
            except requests.exceptions.Timeout:
                raise MetadataException('Request timed out.')
            except requests.exceptions.TooManyRedirects:
                raise MetadataException('Bad URL.')
            except requests.exceptions.HTTPError as http_err:
                raise MetadataException(f"HTTP error occurred: {http_err}")
            except requests.exceptions.RequestException as e: # Other errors
                raise MetadataException('Unknown URL error. {}'.format(e))
            
        except MetadataException as e:
            messages.add(remove_links(e.message))
            return -1, messages

        if channel_name != channel:
            # Playlist source check
            messages.add("Playlist is not from {} (found playlist from {})".format(channel_name, channel))
        else:
            # Duplicate title check
            if title in [video['title'] for video in videos]:
                messages.add("Video title already exists in playlist.")
    else:
        playlist_name = None
        videos = []

    # Check metadata by patterns
    with open(PATTERNS_FILE, 'r', encoding='utf-8') as file:
        patterns = json.load(file)

        # Common mistake patterns
        for p in patterns["MISTAKE"]:
            if "pattern" in p.keys() and p["pattern"] in description:
                if "message" in p.keys(): messages.add(p["message"])
                if "adv_message" in p.keys(): adv_messages.add(p["adv_message"])
            elif "reg_pattern" in p.keys() and re.search(p["reg_pattern"], description) is not None:
                if "message" in p.keys(): messages.add(p["message"])
                if "adv_message" in p.keys(): adv_messages.add(p["adv_message"])

        if len(desc) == 0 or 'music' not in list(desc.keys())[0].lower():
            # If the first description line is not "Music:", assume the metadata is intentionally unusual
            # just return nothing?
            return 0, []
        else:
            # Compare desc with existing videos
            existing_descs = [video['description'] for video in videos]
            extra_fields = False
            for key in desc.keys():
                # ignore ones already covered by patterns.json
                if (key + ":") in [p["pattern"] for p in patterns["MISTAKE"] if "pattern" in p.keys()]:
                    continue
                if not crosscheck_description_key(key, existing_descs, 0):
                    extra_fields = True
                    adv_messages.add(f'``{key}`` field not present in any existing videos in playlist.')

            # Check the order of keys
            if not extra_fields and len(existing_descs) > 0 \
                and not any([list(desc.keys()) == list(desc_to_dict(d.replace('\r', '').split('\n\n')[0], 0)[0].keys()) for d in existing_descs]):
                adv_messages.add(f'Order of lines does not match any existing videos in playlist.')
            
            # Compare desc['Music'] and title
            track = get_music_from_desc(desc)

            game = None
            temp_messages = set()
            good_match = False

            for p in patterns["TITLE"]:
                match = re.match(p.replace('[[TRACK]]', re.escape(track)), title)
                if match:
                    game = match.group('game')
                    existing_titles = [video['title'] for video in videos]

                    # Check game name
                    if p.startswith('[[TRACK]]'):
                        game_match = any([video.endswith(game) for video in existing_titles])
                    elif p.endswith('[[TRACK]]'):
                        game_match = any([video.startswith(game) for video in existing_titles])
                    else:
                        # unsupported game matching
                        game_match = True
                    
                    if len(existing_titles) > 0 and (game != playlist_name) and not game_match:
                        if title[-1] == ' ':
                            temp_messages.add('Trailing whitespace detected at end of title.')
                        elif len(title) == 100 or (game in playlist_name or any([game in video for video in existing_titles])):
                            temp_messages.add('Game in title appears to be cut off. Ignore if this was intentional to go under 100-character limit.')
                        else:
                            temp_messages.add(f'Game in title does not match playlist name (``{playlist_name}``) nor any existing videos in playlist.')
                    else:
                        # Check that at least one other existing video has the same title formatting
                        other_p = p.replace('[[TRACK]]', r'(?P<track>[^\n]*)').replace(r'(?P<game>[^\n]*)', re.escape(game))
                        if len(existing_titles) > 0 and not any([re.match(other_p, t) is not None for t in existing_titles]):
                            game = None
                            continue
                        good_match = True

            if not good_match:
                adv_messages = adv_messages.union(temp_messages)
            
            if game is None:
                if title == track:
                    adv_messages.add('Game name in Music field should be removed.')
                else:
                    adv_messages.add('Title format does not match {}, or Music line is incorrect (e.g. missing mixname).'.format('existing videos in playlist' if len(videos) > 0 else 'any known pattern'))
    
    if advanced: messages = messages.union(adv_messages)
    return int(len(messages) > 0), list(messages)


def isDupe(desc1: str, desc2: str) -> bool:
    """
    Check if 2 descriptions are dupes of each other.
    Input should be full descriptions, including title.
    """
    if len(desc1) == 0 or len(desc2) == 0:
        return False
    
    D1, _ = desc_to_dict(desc1, 1)
    D2, _ = desc_to_dict(desc2, 1)

    if len(D1) == 0 or len(D2) == 0:
        # Desc has nothing, check dupe based on title only
        # Remove all instances of "(<anything>)" from titles,
        # then check if they are equal
        title1 = desc1.splitlines()[0]
        title2 = desc2.splitlines()[0]
        return re.sub(r'\s*\(.*?\)\s*', ' ', title1) == re.sub(r'\s*\(.*?\)\s*', ' ', title2)
    else:
        # Check dupe based on the 'Music' key
        # Assuming all mixnames are "(<anything>)" added at the end of the track name,
        # then rsplit by the last ( should yield the main mix track name
        track1 = get_music_from_desc(D1)
        track2 = get_music_from_desc(D2)
        # trying to account for track names with parentheses
        track1_base = track1.rsplit(' (', 1)[0]
        track2_base = track2.rsplit(' (', 1)[0]
        return track1_base == track2_base or track1_base == track2


def countDupe(description: str, channel_name: str, playlist_id: str, api_key: str) -> Tuple[int, str]:
    """
    Check the playlist and count the number of dupes.
    TODO: a lot of code is borrowed from checkMetadata. Merge them?
    """
    if len(playlist_id) > 0:
        try:
            try:
                _, channel = get_playlist_details(playlist_id, api_key)
                videos = get_playlist_videos(playlist_id, api_key)
        
            except requests.exceptions.Timeout:
                raise MetadataException('Request timed out.')
            except requests.exceptions.TooManyRedirects:
                raise MetadataException('Bad URL.')
            except requests.exceptions.HTTPError as http_err:
                raise MetadataException(f"HTTP error occurred: {http_err}")
            except requests.exceptions.RequestException as e: # Other errors
                raise MetadataException('Unknown URL error. {}'.format(e))
            
        except MetadataException as e:
            return 0, remove_links(e.message)

        if channel_name != channel:
            return 0, "Playlist is not from {} (found playlist from {})".format(channel_name, channel)
    else:
        return 0, "Playlist not found."
    
    if len(videos) == 0:
        return 0, "Playlist is empty."

    return sum([isDupe(description, video['title'] + '\n' + video['description'].replace('\r', '').split('\n\n')[0]) for video in videos]), ""


# Example usage
from bot_secrets import YOUTUBE_CHANNEL_NAME, YOUTUBE_API_KEY

# paste description here for testing
DESC = """
"""

if __name__ == "__main__":
    CHANNEL_NAME = YOUTUBE_CHANNEL_NAME if len(YOUTUBE_CHANNEL_NAME) > 0 else input("Paste expected channel name: ")
    API_KEY = YOUTUBE_API_KEY if len(YOUTUBE_API_KEY) > 0 else input("Paste the API key: ")
    match = re.search(r'(?:https?://)?(?:www\.)?(?:youtube\.com/|youtu\.be/)playlist\?list=([a-zA-Z0-9_-]+)', DESC)
    playlist = match.group(1) if match else ""

    code, msgs = checkMetadata(DESC, CHANNEL_NAME, playlist, API_KEY, True)
    print(f"Code: {code}")
    for msg in msgs:
        print(msg)
