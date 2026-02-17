
import typing
from typing import NamedTuple, List
from enum import Enum, auto
from contextlib import asynccontextmanager
import asyncio
import re

import discord
from discord import Message, Thread, TextChannel
from discord.abc import GuildChannel
from datetime import datetime

from config import *

# Emoji definitions
APPROVED_INDICATOR = '🔥'
AWAITING_SPECIALIST_INDICATOR = '♨️'
SPECS_OVERDUE_INDICATOR = '🫑'
OVERDUE_INDICATOR = '🕒'

DEFAULT_CHECK = '✅'
DEFAULT_FIX = '🔧'
DEFAULT_STOP = '🛑'
DEFAULT_GOLDCHECK = '🎉'
DEFAULT_REJECT = '❌'
DEFAULT_ALERT = '❗'
DEFAULT_QOC = '🛃'
DEFAULT_METADATA = '📝'
DEFAULT_THUMBNAIL = '🖼️'

QOC_DEFAULT_LINKERR = '🔗'
QOC_DEFAULT_BITRATE = '🔢'
QOC_DEFAULT_CLIPPING = '📢'


#===============================================#
#                    CACHE                      #
#===============================================#

class ReactAndUser(NamedTuple):
    name: str
    user_id: int

"""
QocRips are rips that are pinned in a channel and used in QoC. They have info on who reacted to what.
Exists only in QOC channels
"""
class QocRip(NamedTuple):
    text: str
    message_id: int
    channel_id: int
    message_author_id: int
    message_author_name: str
    react_and_users: List[ReactAndUser]
    created_at: datetime

"""
SubOrQueue Rips are rips posted in a submission channel, or rips posted in an accepted rips queue.
They can be in channels or threads depending on the channel type. 
They do NOT include info on who reacted to what as an optimization as this info is slow to obtain.
(This is the only difference between SubOrQueueRips and QocRips. They are split into different types 
despite being mostly the same so that it is easier to manage this difference in data layout)
"""
class SubOrQueueRip(NamedTuple):
    text: str
    message_id: int
    channel_id: int
    message_author_id: int
    message_author_name: str
    react_names: List[str]
    created_at: datetime

RIP_CACHE_QOC: dict[int, dict[int, QocRip]] = {}
RIP_CACHE_SUBORQUEUE: dict[int, dict[int, SubOrQueueRip]] = {}

CACHE_LOCK_QOC: dict[int, asyncio.Lock] = {}
CACHE_LOCK_SUBORQUEUE: dict[int, asyncio.Lock] = {}

def init_channel_cache(channel_id: int):
    if channel_id not in RIP_CACHE_SUBORQUEUE:
        RIP_CACHE_SUBORQUEUE[channel_id]: dict[int, SubOrQueueRip] = {}
    if channel_id not in RIP_CACHE_QOC:
        RIP_CACHE_QOC[channel_id]: dict[int, SubOrQueueRip] = {}
    if channel_id not in CACHE_LOCK_SUBORQUEUE:
        CACHE_LOCK_SUBORQUEUE[channel_id] = asyncio.Lock()
    if channel_id not in CACHE_LOCK_QOC:
        CACHE_LOCK_QOC[channel_id] = asyncio.Lock()

class RipFetchType(Enum):
    PINS = auto()
    ALL_MESSAGES_NO_THREADS = auto()
    ALL_MESSAGES_AND_THREADS = auto()

class ChannelInfo(NamedTuple):
    rip_fetch_type: RipFetchType
    is_cache_qoc: bool

def get_channel_info(channel: TextChannel) -> ChannelInfo:

    rip_fetch_type = RipFetchType.PINS

    if channel_is_types(channel, ['SUBS']):
        rip_fetch_type = RipFetchType.ALL_MESSAGES_NO_THREADS

    elif channel_is_types(channel, ['QUEUE', 'SUBS_THREAD']):
        rip_fetch_type = RipFetchType.ALL_MESSAGES_AND_THREADS

    is_cache_qoc = channel_is_types(channel, ['QOC'])
    
    return ChannelInfo(rip_fetch_type, is_cache_qoc)

async def cache_qoc_rip(message: Message) -> QocRip:

    init_channel_cache(message.channel.id)

    react_and_users = []
    for reaction in message.reactions:
        name = ""
        if isinstance(reaction.emoji, str):
            name = reaction.emoji
        elif hasattr(reaction.emoji, "name"):
            name = reaction.emoji.name
        # NOTE: (Ahmayk) this is slow! We have to do an api call for each user.
        # But is also neccessary
        user_ids = [user.id async for user in reaction.users()]
        for user_id in user_ids:
            react_and_users.append(ReactAndUser(name, user_id))

    qoc_rip = QocRip(message.content, message.id, message.channel.id, message.author.id, \
            str(message.author), react_and_users, message.created_at)

    RIP_CACHE_QOC[message.channel.id][message.id] = qoc_rip

    return qoc_rip

#NOTE: (Ahmayk) Dummy async context that we can call instead in the case where 
#we do not input a channel for channel.typing()
@asynccontextmanager
async def empty_async_context():
    yield

async def wait_with_typing_if_locked(lock: asyncio.Lock, typing_channel: TextChannel | Thread):
    if lock.locked():
        async with typing_channel.typing() if typing_channel is not None else empty_async_context():
            async with lock:
                pass

class GetRipsDesc(NamedTuple):
    typing_channel: TextChannel | Thread | None = None
    rebuild_cache: bool = False

async def get_qoc_rips(channel: typing.Union[GuildChannel, Thread], desc: GetRipsDesc) -> List[QocRip]:
    """
    Gets all qoc rips from a channel. Makes a cache for the channel if it doesn't already exist. 

    Blocks processing while cache is being created on that channel 
    to not allow someone to access the cache with it half finished.
    """

    init_channel_cache(channel.id)

    channel_info = get_channel_info(channel)

    ##NOTE: (Ahmayk) If we hit either of these asserts, then we are calling get_qoc_rips() incorrectly!
    assert channel_info.rip_fetch_type == RipFetchType.PINS
    assert channel_info.is_cache_qoc 

    await wait_with_typing_if_locked(CACHE_LOCK_QOC[channel.id], desc.typing_channel)

    ##NOTE: (Ahmayk) We need to prevent other processes from accessing the cache 
    ## in the case where we are updating the cache. If we allow access to the cache while
    ## it is being updated, it would likely be incomplete or wrong! 
    async with CACHE_LOCK_QOC[channel.id]:
        if not len(RIP_CACHE_QOC[channel.id]) or desc.rebuild_cache:

            async with desc.typing_channel.typing() if desc.typing_channel is not None else empty_async_context():
                RIP_CACHE_QOC[channel.id]: dict[int, QocRip] = {}

                async for message in channel.pins(limit=None):

                    if is_message_rip(message):
                        # NOTE: (Ahmayk) channel.pins does not return reaction data,
                        # so we have to fetch it manually. This is slow! But neccessary.
                        fetched_message = await channel.fetch_message(message.id)
                        await cache_qoc_rip(fetched_message)

                        # NOTE: (Ahmayk) always cache suborqueue versions
                        # so that the data is easily accessible via get_suborqueue_rips_fast()
                        cache_suborqueue_rip(fetched_message)

    result = list(RIP_CACHE_QOC[channel.id].values())
    ##NOTE: (Ahmayk) show rips in expected order, newest at top
    result.sort(key = lambda rip: rip.created_at, reverse=True)
    return result

def init_suborqueue_rip(message: Message, react_names: List[str]) -> SubOrQueueRip:
    return SubOrQueueRip(message.content, message.id, message.channel.id, \
                         message.author.id, str(message.author), react_names, message.created_at)

def cache_suborqueue_rip(message) -> SubOrQueueRip:

    init_channel_cache(message.channel.id)

    react_names = []
    for reaction in message.reactions:
        name = ""
        if isinstance(reaction.emoji, str):
            name = reaction.emoji
        elif hasattr(reaction.emoji, "name"):
            name = reaction.emoji.name
        react_names.append(name)

    suborqueue_rip = init_suborqueue_rip(message, react_names)

    RIP_CACHE_SUBORQUEUE[message.channel.id][message.id] = suborqueue_rip
    return suborqueue_rip

async def get_suborqueue_rips(channel: typing.Union[GuildChannel, Thread], desc: GetRipsDesc) -> List[SubOrQueueRip]:
    """
    Gets all SubOrQueue rips from a channel. Detects the channel type and  
    grabs either 
    - all messages (SUBS)
    - all messages and all messages in threads (QUEUE, SUBS_THREAD)
    - all pins (SUBS_PIN, QOC, anything else)

    Discord can process messages in order very quickly, so this function is fast 
    in most cases (excpet for pins, which require an extra call to fetch reaction data)

    Blocks processing while cache is being created on that channel 
    to not allow someone to access the cache with it half finished.
    """

    init_channel_cache(channel.id)
    channel_info = get_channel_info(channel)

    await wait_with_typing_if_locked(CACHE_LOCK_SUBORQUEUE[channel.id], desc.typing_channel)

    ##NOTE: (Ahmayk) We need to prevent other processes from accessing the cache 
    ## in the case where we are updating the cache. If we allow access to the cache while
    ## it is being updated, it would likely be incomplete or wrong! 
    async with CACHE_LOCK_SUBORQUEUE[channel.id]:

        if not len(RIP_CACHE_SUBORQUEUE[channel.id]) or desc.rebuild_cache:

            async with desc.typing_channel.typing() if desc.typing_channel is not None else empty_async_context():

                match channel_info.rip_fetch_type: 
                    case RipFetchType.ALL_MESSAGES_NO_THREADS:
                        async for message in channel.history(limit = None):
                            if is_message_rip(message):
                                cache_suborqueue_rip(message)

                    case RipFetchType.ALL_MESSAGES_AND_THREADS:
                        async for message in channel.history(limit = None):
                            if message.thread is not None:
                                thread_suborqueue_rips = await get_suborqueue_rips(message.thread, GetRipsDesc(rebuild_cache=desc.rebuild_cache))
                                #NOTE: (Ahmayk) we insert thread rips also in its parent channel dict so that
                                #we get all rips in theads when we get the parent channel's rips 
                                for thread_suborqueue_rip in thread_suborqueue_rips:
                                    RIP_CACHE_SUBORQUEUE[channel.id][thread_suborqueue_rip.message_id] = thread_suborqueue_rip 
                            if is_message_rip(message):
                                cache_suborqueue_rip(message)

                    case RipFetchType.PINS:
                        async for message in channel.pins(limit = None):
                            if is_message_rip(message):
                                # NOTE: (Ahmayk) channel.pins does not return reaction data,
                                # so we have to fetch it manually. This is slow! But neccessary.
                                fetched_message = await channel.fetch_message(message.id)
                                cache_suborqueue_rip(fetched_message)

                    case _:
                        assert "Unimplemented RipFetchType"

    result = list(RIP_CACHE_SUBORQUEUE[channel.id].values())
    ##NOTE: (Ahmayk) show rips in expected order, newest at top
    result.sort(key = lambda rip: rip.created_at, reverse=True)
    return result

async def get_suborqueue_rips_fast(channel: typing.Union[GuildChannel, Thread], desc: GetRipsDesc) -> typing.List[SubOrQueueRip]:
    """
    Will return suborqueue rips of any channel type as fast as possible but may not have reaction data.
    This function is for when we we don't care about reactions 
    and only care about the number of rips or its metadata.

    Channels that are not labeled with a matching channel type are assumed to be pin channels.
    """

    init_channel_cache(channel.id)
    channel_info = get_channel_info(channel)

    result = []

    if channel_info.rip_fetch_type == RipFetchType.ALL_MESSAGES_AND_THREADS or \
       channel_info.rip_fetch_type == RipFetchType.ALL_MESSAGES_NO_THREADS:

        #NOTE: (Ahmayk) The path to get rips normally is already as fast
        #as we can get so just do that
        result = await get_suborqueue_rips(channel, desc)

    else:
        if not CACHE_LOCK_SUBORQUEUE[channel.id].locked() and len(RIP_CACHE_SUBORQUEUE[channel.id]):
            result = await get_suborqueue_rips(channel, desc)
        else:
            async with desc.typing_channel.typing() if desc.typing_channel is not None else empty_async_context():
                async for message in channel.pins(limit = None):
                    if is_message_rip(message):
                        ##NOTE: (Ahmayk) No fetching message for reactions for speed!
                        suborqueue_rip = init_suborqueue_rip(message, [])
                        result.append(suborqueue_rip)
                result.sort(key = lambda rip: rip.created_at, reverse=True)

    return result


#===============================================#
#                    REACTS
#===============================================#

KEYCAP_EMOJIS = {'2️⃣': 2, '3️⃣': 3, '4️⃣': 4, '5️⃣': 5, '6️⃣': 6, '7️⃣': 7, '8️⃣': 8, '9️⃣': 9, '🔟': 10}

class ReactionType(Enum):
    NULL = auto()
    GOLDCHECK = auto()
    CHECKREQ = auto()
    CHECK = auto()
    FIX = auto()
    REJECT = auto()
    STOP = auto()
    ALERT = auto()
    QOC = auto()
    METADATA = auto()
    THUMBNAIL = auto()
    EMAILSENT = auto()
    NUMBER = auto()

def react_is(reaction_type: ReactionType, name: str) -> bool:
    result = False
    name_lower = name.lower()
    match (reaction_type):
        case ReactionType.GOLDCHECK:
            result = name_lower == "goldcheck" or name_lower == DEFAULT_GOLDCHECK
        case ReactionType.CHECKREQ:
            result = name_lower.endswith("check") and name_lower[0].isdigit()
        case ReactionType.CHECK:
            if not react_is(ReactionType.GOLDCHECK, name) and not react_is(ReactionType.CHECKREQ, name):
                result = name_lower == "check" or name_lower == DEFAULT_CHECK
        case ReactionType.FIX:
            result = name_lower == "fix" or name_lower == "wrench" or name_lower == DEFAULT_FIX
        case ReactionType.REJECT:
            result = name_lower == "reject" or name_lower == DEFAULT_REJECT
        case ReactionType.STOP:
            result = name_lower == "stop" or name_lower == "octagonal" or name_lower == DEFAULT_STOP
        case ReactionType.ALERT:
            result = name_lower == "alert" or name_lower == DEFAULT_ALERT
        case ReactionType.QOC:
            result = name_lower == "qoc" or name_lower == DEFAULT_QOC
        case ReactionType.METADATA:
            result = name_lower == "metadata" or name_lower == DEFAULT_METADATA
        case ReactionType.THUMBNAIL:
            result = name_lower == "thumbnail" or name_lower == DEFAULT_THUMBNAIL
        case ReactionType.EMAILSENT:
            result = name_lower == "emailsent"
        case ReactionType.NUMBER:
            result = name in KEYCAP_EMOJIS
        case _:
            assert "Unimplemented ReactionType"

    return result

def react_is_one(reaction_type_list: List[ReactionType], name: str) -> bool:
    for reaction_type in reaction_type_list:
        if react_is(reaction_type, name):
            return True
    return False

def suborqueue_rip_has_reaction(reaction_type: ReactionType, suborqueue_rip: SubOrQueueRip) -> bool:
    for name in suborqueue_rip.react_names:
        if react_is(reaction_type, name):
            return True
    return False

def qoc_rip_has_reaction(reaction_type: ReactionType, qoc_rip: QocRip) -> bool:
    for react_and_user in qoc_rip.react_and_users:
        if react_is(reaction_type, react_and_user.name):
            return True
    return False

def qoc_rip_has_reaction_one(reaction_type_list: List[ReactionType], qoc_rip: QocRip):
    for react_and_user in qoc_rip.react_and_users:
        if react_is_one(reaction_type_list, react_and_user.name):
            return True
    return False

def qoc_rip_has_reaction_one_from_user(reaction_type_list: List[ReactionType], user_id: int, qoc_rip: QocRip):
    for react_and_user in qoc_rip.react_and_users:
        if react_and_user.user_id == user_id and react_is_one(reaction_type_list, react_and_user.name):
            return True
    return False

def reaction_name_to_emoji_string(name: str, guild: discord.Guild) -> str:
    result = f'{name}' 
    for emoji in guild.emojis:
        if emoji.name == name:
            result = str(emoji)
            break
    return result

#===============================================#
#                   COMMANDS 
#===============================================#

class CommandType(Enum):
    NULL = auto()
    MANAGEMENT = auto()
    ROUNDUP = auto()
    STATS = auto()
    SUB = auto()
    QUEUE = auto()
    ANALYZE = auto()
    SECRET = auto()

class CommandContext(NamedTuple):
    channel: TextChannel | Thread 
    user: discord.User 
    message_reference: discord.MessageReference

class CommandInfo(NamedTuple):
    func: typing.Callable[[list[str], CommandContext], typing.Awaitable[typing.NoReturn]]
    command_type: CommandType
    public: bool 
    admin: bool 
    brief: str
    desc: str
    format: str
    aliases: List[str]
    examples: List[str]

COMMANDS: dict[str, CommandInfo] = {}

##NOTE: (Ahmayk) This nonsense is so that we can have a simpler way of defining commands
def command(
    command_type: CommandType = CommandType.NULL,
    public: bool = False,
    admin: bool = False,
    brief: str = "",
    desc: str = "",
    format: str = "",
    aliases: list[str] = [],
    examples: list[str] = [],
) -> typing.Callable:
    def decorator(func: typing.Callable) -> typing.Callable:
        COMMANDS[func.__name__] = CommandInfo(
            func=func,
            command_type=command_type,
            public=public,
            admin=admin,
            brief=brief,
            desc=desc,
            format=format,
            aliases=aliases,
            examples=examples,
        )
        return func
    return decorator


#===============================================#
#                    HELPERS                    #
#===============================================#

def channel_is_type(channel: typing.Union[GuildChannel, Thread], type: str):
    return type in get_channel_config(channel.id).types or hasattr(channel, "parent") and channel_is_type(channel.parent, type)

def channel_is_types(channel: typing.Union[GuildChannel, Thread], types: typing.List[str]):
    return any([t in get_channel_config(channel.id).types for t in types]) or hasattr(channel, "parent") and channel_is_types(channel.parent, types)

def extract_rip_link(text: str) -> typing.List[str]:
    """
    Extract potential rip links from text.
    Ignores Youtube links.
    """
    # Regular expression to match links that start with "http"
    pattern = r'\b(http[^\s]+)\b'
    # Find all matches in the text
    matches = re.findall(pattern, text)
    # Filter out any matches that contain "youtu"
    ret = []
    for match in matches:
        if "youtu" not in match:
            ret.append(match)
    return ret

def is_message_rip(message: Message) -> bool:
    result = False
    is_valid_message = message.channel is Thread or not (message.channel is Thread)
    has_quotes = '```' in message.content
    if is_valid_message and has_quotes:
        rip_links = extract_rip_link(message.content)
        if len(rip_links) > 0:
            result = True
    return result

def split_long_message(a_message: str, character_limit: int) -> list[str]:  # avoid Discord's character limit
    """
    Split a long message to fit Discord's character limit.
    Aug 6 2025: apparently embeds have a higher character limit?
    """
    result = []
    all_lines = a_message.splitlines()
    wall_of_text = ""
    for line in all_lines:
        line = line.replace('@', '')  # no more pings lol
        next_length = len(wall_of_text) + len(line)
        if next_length > character_limit:
            result.append(wall_of_text[:-1])  # append and get rid of the last newline
            wall_of_text = line + '\n'
        else:
            wall_of_text += line + '\n'

    result.append(wall_of_text[:-1])  # add anything remaining
    return result

async def send(text: str, channel: TextChannel | Thread):
    split_message = split_long_message(text, 2000)
    for line in split_message:
        await channel.send(line)


class EmbedDesc(NamedTuple):
    expires: bool = False
    title: str = ""
    footer: str = ""

##TODO: (Ahmayk) smarter embed packaging 
async def send_embed(text: str, channel: TextChannel | Thread, desc: EmbedDesc):
    split_message = split_long_message(text, 4028)
    color = get_config('embed_color')
    delete_after_seconds = None
    if desc.expires:
        if channel_is_types(channel, ['PROXY_QOC']):
            delete_after_seconds = get_config('proxy_embed_seconds')
        else: get_config('embed_seconds')

    for i, line in enumerate(split_message):

        if i == 0: 
            embed = discord.Embed(description=line, color=color, title=desc.title)
        else:
            embed = discord.Embed(description=line, color=color)
            
        if i == len(split_message) - 1: 
            embed.set_footer(text=desc.footer)

        await channel.send(embed=embed, delete_after=delete_after_seconds)


def extract_playlist_id(text: str) -> str:
    """
    Extract the YouTube playlist ID from text.
    Assumes it is the first YouTube link.
    """
    playlist_regex = r'(?:https?://)?(?:www\.)?(?:youtube\.com/|youtu\.be/)playlist\?list=([a-zA-Z0-9_-]+)'
    match = re.search(playlist_regex, text)
    if match:
        # Return the extracted playlist ID
        return match.group(1)
    else:
        return ""  # Return empty string if no valid links are found


def get_raw_rip_title(text: str) -> str:
    """
    Return the rip title line of a Discord message.
    Assumes the message follows the format where the rip title is after the first instance of ```
    """
    # Update: now use regex to find the first instance of "```[\n][text][\n]"
    pattern = r'\`\`\`\n*.*\n'
    rip_title = re.search(pattern, text)
    if rip_title is not None:
        rip_title = rip_title.group(0)
        rip_title = rip_title.replace('`', '')
        rip_title = rip_title.replace('\n', '')

    return rip_title


def get_rip_title(text: str) -> str:
    """
    Wrapper function to format unusual or spoiler rip titles
    """
    rip_title = get_raw_rip_title(text)
    if rip_title is None:
        return "`[Unusual Pin Format]`"
    elif '||' in text.split('```')[0]:
        # if || is detected in the message before the first ```, make the rip title into spoiler
        return "`[Rip Contains Spoiler]`"
    else:
        return rip_title


def get_raw_rip_author(text: str) -> str:
    """
    Return the rip author line of a Discord message.
    Assumes the message follows the format where the rip author is after the first instance of ```
    """
    author = text.split("```")[0]
    author = author.replace('\n', '')
    author = author.replace('||', '') # in case of spoilered rips

    return author


def get_rip_author(text: str, message_author_name: str) -> str:
    """
    Wrapper function to format author line.
    If the line contains "by me", append the message sender's name to the author line.
    """
    author = get_raw_rip_author(text)
    
    if len(re.findall(r'\bby\b', author.lower())) == 0:
        # If "by" is not found, notify that the "author line" might be unusual
        author = author + " [Unusual Pin Format]"

    elif len(re.findall(r'\bby me\b', author.lower())) > 0: 
        # Overwrite it and do something else if the rip's author and the pinner are the same
        cleaned_author = message_author_name.split('#')[0]
        author += (f' (**{cleaned_author}**)')

    return author


def get_rip_description(text: str) -> str:
    """
    Return the description of a rip, i.e. the part inside ```
    """
    # Use a regular expression to find text between two ``` markers
    match = re.search(r'```(.*?)```', text, re.DOTALL)

    if match:
        # Return the extracted text, stripping any leading/trailing whitespace
        return match.group(1).strip()
    else:
        return ""  # Return empty string if no match was found

def format_message_link(guild_id: int, channel_id: int, message_id: int):
    return  f"<https://discordapp.com/channels/{str(guild_id)}/{str(channel_id)}/{str(message_id)}>"


def line_contains_substring(line: str, substring: str) -> bool:
    """
    Helper function to search substrings in Discord markdown-formatted line, ignoring case and formatting
    """
    return substring.lower() in line.replace('*', '').replace('_', '').replace('|', '').replace('#', '').lower()
