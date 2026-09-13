
import shelve 
import asyncio 
import numpy
from typing import NamedTuple
from enum import StrEnum
from datetime import datetime, timedelta, timezone
import pytz
from dateutil import tz

from hq_strings import TitleType, score_title_similarity, truncate_string
from hq_config import get_config
from hq_discord import FloatAndErrors, StringAndErrors, ChannelsAndErrors, MessagesAndErrors, get_channels_of_types, discord_find_channel, discord_delete_messages, discord_get_channel_messages, discord_fetch_message, datetime_to_relative_timestamp, get_name_of_user
from hq_qoc import getAudioLengthInSecondsFFprobe 
from hq_youtube import PlaylistVideo 

JE_DATABASE = shelve.open("je_database", writeback=True)
THUMBNAIL_DATABASE = shelve.open("thumbnail_database", writeback=True)
PLAYLIST_VIDEO_CACHE = shelve.open("playlist_video_cache", writeback=True)

class JEDatabaseKey(StrEnum):
    RIP_LENGTH = "RIP_LENGTH" 
    SENT_EMBED_TO_EXPIRE = "SENT_EMBED_TO_EXPIRE" 
    PLAYLIST_VIDEOS = "PLAYLIST_VIDEOS"
    REMINDER = "REMINDER"

JE_DATABASE_LOCK = asyncio.Lock()
THUMBNAIL_DATABASE_LOCK = asyncio.Lock()
PLAYLIST_VIDEO_CACHE_LOCK = asyncio.Lock()

class GetRipUrlLengthDesc(NamedTuple):
    force_download: bool = False

async def store_in_database_float(value: float, key: str, database_key: str):
    if (
        database_key != JEDatabaseKey.RIP_LENGTH
    ):
        assert f"{database_key} does not support float as input"

    await JE_DATABASE_LOCK.acquire()
    try:
        JE_DATABASE[database_key][key] = value 
        JE_DATABASE.sync()
    finally:
        JE_DATABASE_LOCK.release()

async def get_rip_url_length(url: str, desc: GetRipUrlLengthDesc) -> FloatAndErrors: 
    duration = 0.0
    error_strings = []

    if JEDatabaseKey.RIP_LENGTH not in JE_DATABASE:
        JE_DATABASE[JEDatabaseKey.RIP_LENGTH] = {}

    if desc.force_download or (url not in JE_DATABASE[JEDatabaseKey.RIP_LENGTH]):
        floatAndErrors = await getAudioLengthInSecondsFFprobe(url)
        if not len(floatAndErrors.error_strings):
            duration = floatAndErrors.result
        else:
            error_strings.extend(floatAndErrors.error_strings)

        await JE_DATABASE_LOCK.acquire()
        try:
            #NOTE: (Ahmayk) store 0 when we error. 
            # This results in not retrying on paths where don't want to download anything
            JE_DATABASE[JEDatabaseKey.RIP_LENGTH][url] = duration
            JE_DATABASE.sync()
        finally:
            JE_DATABASE_LOCK.release()
    else:
        duration = JE_DATABASE[JEDatabaseKey.RIP_LENGTH][url]

    return FloatAndErrors(duration, error_strings)


async def remove_urls_from_database(urls_to_remove: list[str]):
    if len(urls_to_remove):
        await JE_DATABASE_LOCK.acquire()
        try:
            for url in urls_to_remove:
                JE_DATABASE[JEDatabaseKey.RIP_LENGTH].pop(url)
            JE_DATABASE.sync()
        finally:
            JE_DATABASE_LOCK.release()


class SentEmbed(NamedTuple):
    channel_id: int
    message_id: int
    sent_time: datetime

async def store_sent_embeds_in_expire_database(message_ids: list[int], channel_id: int):

    if JEDatabaseKey.SENT_EMBED_TO_EXPIRE not in JE_DATABASE:
        JE_DATABASE[JEDatabaseKey.SENT_EMBED_TO_EXPIRE] = [] 

    await JE_DATABASE_LOCK.acquire()
    try:
        for message_id in message_ids:
            JE_DATABASE[JEDatabaseKey.SENT_EMBED_TO_EXPIRE].append(SentEmbed(channel_id, message_id, datetime.now(timezone.utc)))
        JE_DATABASE.sync()
    finally:
        JE_DATABASE_LOCK.release()


async def cleanup_sent_embeds_in_expire_database():

    if JEDatabaseKey.SENT_EMBED_TO_EXPIRE not in JE_DATABASE:
        JE_DATABASE[JEDatabaseKey.SENT_EMBED_TO_EXPIRE] = [] 

    expire_time_proxy_qoc = get_config('proxy_embed_seconds')
    expire_time_qoc = get_config('embed_seconds')
    channel_and_errors = await get_channels_of_types(['PROXY_QOC'], [])
    proxy_channel_ids = []
    for channel in channel_and_errors.channels:
        proxy_channel_ids.append(channel.id)

    sent_embeds_to_delete = []
    for sent_embed in JE_DATABASE[JEDatabaseKey.SENT_EMBED_TO_EXPIRE]:
        expire_time = expire_time_qoc
        if sent_embed.channel_id in proxy_channel_ids:
            expire_time = expire_time_proxy_qoc
        if (datetime.now(timezone.utc) - sent_embed.sent_time) > timedelta(seconds=expire_time):
            sent_embeds_to_delete.append(sent_embed)

    error_strings = []

    if len(sent_embeds_to_delete):

        channel_dict_channels = {}
        channel_dict_message_ids = {}

        for sent_embed in sent_embeds_to_delete:
            if sent_embed.channel_id not in channel_dict_channels:
                channel_and_errors = await discord_find_channel(sent_embed.channel_id)
                if channel_and_errors.channel:
                    channel_dict_channels[sent_embed.channel_id] = channel_and_errors.channel
            
            if sent_embed.channel_id not in channel_dict_message_ids:
                channel_dict_message_ids[sent_embed.channel_id] = []

            channel_dict_message_ids[sent_embed.channel_id].append(sent_embed.message_id)

        for channel in channel_dict_channels.values():
            await discord_delete_messages(channel_dict_message_ids[sent_embed.channel_id], channel)

        await JE_DATABASE_LOCK.acquire()
        try:
            for sent_embed in sent_embeds_to_delete:
                JE_DATABASE[JEDatabaseKey.SENT_EMBED_TO_EXPIRE].remove(sent_embed)
            JE_DATABASE.sync()
        finally:
            JE_DATABASE_LOCK.release()


THUMBNAIL_DICT_KEY = "THUMBNAIL_DICT_KEY"

async def refresh_thumbnail_cache() -> StringAndErrors: 

    string = ""
    error_strings = []

    channels_and_errors = await get_channels_of_types(['THUMBNAILS'], [])
    error_strings.extend(channels_and_errors.error_strings)

    if not len(error_strings) and not len(channels_and_errors.channels):
        error_strings.append(f"Thumbnail channel not defined in bot config (contact bot maintainer).")

    messages_and_errors = MessagesAndErrors([], []) 
    if not len(error_strings):
        ##NOTE: (Ahmayk) only expect one thumbnail channel to be defined
        messages_and_errors = await discord_get_channel_messages(None, channels_and_errors.channels[0])
        error_strings.extend(messages_and_errors.error_strings)

    if not len(error_strings):

        thumbnail_dict = {} 
        for message in messages_and_errors.messages:
            if (
                len(message.content)
                and len(message.attachments)
                and message.attachments[0].width
                and message.attachments[0].height
            ):
                thumbnail_dict[message.id] = message.content
        
        await THUMBNAIL_DATABASE_LOCK.acquire()
        try:
            THUMBNAIL_DATABASE[THUMBNAIL_DICT_KEY] = thumbnail_dict 
            THUMBNAIL_DATABASE.sync()
        finally:
            THUMBNAIL_DATABASE_LOCK.release()

        ##NOTE: (Ahmayk) output list in text of everything in database, cause it's maybe useful 
        #And doesn't take that much space
        output_file_text = ""
        for v in thumbnail_dict.values():
            output_file_text += f'\n{v}'
        with open("thumbnail_message_titles.txt", "a", encoding="utf-8") as f:
            f.write(output_file_text)

        string = f"All done!"
        string += f"\n- {len(messages_and_errors.messages)} searched messages."
        string += f"\n- {len(thumbnail_dict)} image messages."

    return StringAndErrors(string, error_strings)

async def search_thumbnail_cache(input_title: str) -> MessagesAndErrors:

    messages = []
    error_strings = []

    if THUMBNAIL_DICT_KEY not in THUMBNAIL_DATABASE:
        error_strings.append("Thumbnail cache is empty. Cache must be filled before thumbnails can be searched.")

    channels_and_errors = ChannelsAndErrors([], []) 
    if not len(error_strings):
        channels_and_errors = await get_channels_of_types(['THUMBNAILS'], [])
        error_strings.extend(channels_and_errors.error_strings)

    if not len(error_strings) and not len(channels_and_errors.channels):
        error_strings.append(f"Thumbnail channel not defined in bot config (contact bot maintainer).")

    if not len(error_strings):
        class ScoredFrame(NamedTuple):
            score: float
            discord_id: int
            message_content: str

        scored_frames = []

        for discord_id, message_content in THUMBNAIL_DATABASE[THUMBNAIL_DICT_KEY].items():
            score = score_title_similarity(message_content, input_title, TitleType.THUMBNAIL) 
            scored_frames.append(ScoredFrame(score, discord_id, message_content))

        scores = []
        for scored in scored_frames:
            scores.append(scored.score)
        score_cutoff = 0
        if len(scores):
            track_mean = numpy.mean(scores) 
            standard_deviation = numpy.maximum(0.0, numpy.std(scores))
            max_score = numpy.max(scores)
            score_cutoff = numpy.minimum(max_score, track_mean + standard_deviation)

        scored_frames_all = list(sorted(scored_frames, key=lambda scored: scored.score, reverse=True))
        scored_frames = scored_frames_all[:5]

        for scored in scored_frames:
            if scored.score > 0 and scored.score >= score_cutoff:
                message_and_errors = await discord_fetch_message(scored.discord_id, channels_and_errors.channels[0])
                error_strings.extend(message_and_errors.error_strings)
                if message_and_errors.message:
                    messages.append(message_and_errors.message)

    return MessagesAndErrors(messages, error_strings)

class PlaylistVideosCacheEntry(NamedTuple):
    playlist_videos: list[PlaylistVideo]
    time: datetime

async def set_playlist_video_cache(playlist_id: str, playlist_videos: list[PlaylistVideo]):
    playlist_video_cache_entry = PlaylistVideosCacheEntry(playlist_videos, datetime.now(timezone.utc))
    await PLAYLIST_VIDEO_CACHE_LOCK.acquire()
    try:
        PLAYLIST_VIDEO_CACHE[playlist_id] = playlist_video_cache_entry
        PLAYLIST_VIDEO_CACHE.sync()
    finally:
        PLAYLIST_VIDEO_CACHE_LOCK.release()


async def cleanup_expired_playlist_video_cache():
    expire_time = get_config("playlist_videos_cache_time")
    keys_to_delete = []
    for playlist_id, entry in PLAYLIST_VIDEO_CACHE.items():
        if (datetime.now(timezone.utc) - entry.time) > timedelta(seconds=expire_time):
            keys_to_delete.append(playlist_id)

    await PLAYLIST_VIDEO_CACHE_LOCK.acquire()
    try:
        for key in keys_to_delete:
            PLAYLIST_VIDEO_CACHE.pop(key)
        PLAYLIST_VIDEO_CACHE.sync()
    finally:
        PLAYLIST_VIDEO_CACHE_LOCK.release()
    

class Reminder(NamedTuple):
    remind_time: datetime
    set_time: datetime
    text: str
    channel_id: int
    user_id: int
    is_countdown: bool
    next_countdown_delta: timedelta | None

REMINDER_COUNTDOWN_LIST = [
    timedelta(hours=1),
    timedelta(hours=12),
    timedelta(hours=24),
    timedelta(hours=48),
    timedelta(days=3),
    timedelta(weeks=1),
    timedelta(weeks=4),
]

def format_to_explicit_pst_time(time: datetime) -> str:
    time_local = time.astimezone(tz.gettz('America/Los_Angeles'))
    result = f'{time_local.strftime("%b %d, %I:%M %p PST")}' 
    return result

def get_next_countdown_reminders(reminder: Reminder) -> str:
    result = ""
    if datetime.now(timezone.utc) > reminder.remind_time:
        result += f':boom: Final reminder: now!'
    elif not reminder.next_countdown_delta:
        next_timestamp = datetime_to_relative_timestamp(reminder.remind_time)
        result += f':bangbang: Final reminder: {next_timestamp}'
    else:
        result += f'Next reminders: '
        countdown_index = 0
        for i, delta in enumerate(REMINDER_COUNTDOWN_LIST):
            if delta >= reminder.next_countdown_delta:
                countdown_index = i 
                break
        for i in range(countdown_index, -1, -1):
            mid_time = reminder.remind_time - REMINDER_COUNTDOWN_LIST[i]
            result += datetime_to_relative_timestamp(mid_time)
            result += ', ' 
        result += datetime_to_relative_timestamp(reminder.remind_time) 
    return result

def format_reminder(reminder: Reminder) -> str:
    header = ":point_up::nerd: Reminder"
    if reminder.is_countdown:
        header = ":point_up::alarm_clock: Countdown"
    message_truncated = truncate_string(reminder.text, 100)
    timestamp_set = datetime_to_relative_timestamp(reminder.set_time)
    timestring_remind = "now"
    if datetime.now(timezone.utc) < reminder.remind_time:
        timestring_remind = datetime_to_relative_timestamp(reminder.remind_time)
    timestring_remind += f' - {format_to_explicit_pst_time(reminder.remind_time)}'
    user_string = get_name_of_user(reminder.user_id)
    remind_verb = "for"
    if reminder.is_countdown:
        remind_verb = "ends"
    result = f"{message_truncated}\n-# {header} scheduled by {user_string} {timestamp_set}\n-# {remind_verb} {timestring_remind}" 
    if reminder.is_countdown:
        result += f'\n-# {get_next_countdown_reminders(reminder)}'
    return result


async def add_reminders_to_database(reminders: list[Reminder]):
    if JEDatabaseKey.REMINDER not in JE_DATABASE:
        JE_DATABASE[JEDatabaseKey.REMINDER] = {} 

    await JE_DATABASE_LOCK.acquire()
    try:
        for reminder in reminders:
            if reminder.channel_id not in JE_DATABASE[JEDatabaseKey.REMINDER]:
                JE_DATABASE[JEDatabaseKey.REMINDER][reminder.channel_id] = []
            JE_DATABASE[JEDatabaseKey.REMINDER][reminder.channel_id].append(reminder)
        JE_DATABASE.sync()
    finally:
        JE_DATABASE_LOCK.release()
    
async def remove_reminders(reminders: list[Reminder]):
    if len(reminders):
        await JE_DATABASE_LOCK.acquire()
        try:
            for reminder in reminders:
                JE_DATABASE[JEDatabaseKey.REMINDER][reminder.channel_id].remove(reminder)
            JE_DATABASE.sync()
        finally:
            JE_DATABASE_LOCK.release()