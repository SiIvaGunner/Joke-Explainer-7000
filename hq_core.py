
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


def is_message_rip(message: Message) -> bool:
    result = False
    is_valid_message = message.channel is Thread or not (message.channel is Thread)
    has_quotes = '```' in message.content
    if is_valid_message and has_quotes:
        rip_links = extract_rip_link(message.content)
        if len(rip_links) > 0:
            result = True
    return result

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