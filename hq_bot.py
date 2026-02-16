#!/usr/bin/python3
import discord
from discord import Message, Thread, TextChannel
from discord.abc import GuildChannel
from discord.ext import commands
from discord.ext.commands import Context
from bot_secrets import TOKEN, YOUTUBE_API_KEY, YOUTUBE_CHANNEL_NAME, CHANNELS, LOG_CHANNEL
from datetime import datetime, timezone, timedelta

from simpleQoC.qoc import performQoC, msgContainsBitrateFix, msgContainsClippingFix, msgContainsSigninErr, ffmpegExists, getFileMetadataMutagen, getFileMetadataFfprobe
from simpleQoC.metadata import checkMetadata, countDupe, isDupe
import re
import functools
import typing
from typing import NamedTuple, List
from contextlib import asynccontextmanager
from enum import Enum, auto
import math
import json
import os
import asyncio

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

latest_pin_time: datetime = datetime.now(timezone.utc)
latest_scan_time: datetime = datetime.now(timezone.utc)

COMMAND_PREFIX = '$'

bot = discord.Client(
    intents = discord.Intents.all() # This was a change necessitated by an update to discord.py :/
    # https://stackoverflow.com/questions/71950432/how-to-resolve-the-following-error-in-discord-py-typeerror-init-missing
    # Also had to enable MESSAGE CONENT INTENT https://stackoverflow.com/questions/71553296/commands-dont-run-in-discord-py-2-0-no-errors-but-run-in-discord-py-1-7-3
    # 10/28/22 They changed it again!!! https://stackoverflow.com/questions/73458847/discord-py-error-message-discord-ext-commands-bot-privileged-message-content-i
)

##NOTE: (Ahmayk) temporary while we refactor command framework
bot.command = lambda *args, **kwargs: (lambda func: func)

# bot.remove_command('help') # get rid of the dumb default !help command

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
    is_cache_suborqueue: bool

def get_channel_info(channel: TextChannel) -> ChannelInfo:

    rip_fetch_type = RipFetchType.PINS

    if channel_is_types(channel, ['SUBS']):
        rip_fetch_type = RipFetchType.ALL_MESSAGES_NO_THREADS

    elif channel_is_types(channel, ['QUEUE', 'SUBS_THREAD']):
        rip_fetch_type = RipFetchType.ALL_MESSAGES_AND_THREADS

    is_cache_qoc = channel_is_types(channel, ['QOC'])
    is_cache_suborqueue = channel_is_types(channel, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD', 'QUEUE'])
    
    return ChannelInfo(rip_fetch_type, is_cache_qoc, is_cache_suborqueue)

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

                        if channel_info.is_cache_suborqueue: 
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

async def validate_cache_all(send_channel: TextChannel | None):

    channel_ids = [k for k, v in CHANNELS.items()]
    for channel_id in channel_ids:
        channel = bot.get_channel(channel_id)
        if channel:
    
            init_channel_cache(channel.id)

            channel_info = get_channel_info(channel)

            count = 0
            match channel_info.rip_fetch_type:
                case RipFetchType.ALL_MESSAGES_NO_THREADS:
                    async for message in channel.history(limit = None):
                        if is_message_rip(message):
                            count += 1

                case RipFetchType.ALL_MESSAGES_AND_THREADS:
                    async for message in channel.history(limit = None):
                        if message.thread is not None:
                            async for thread_message in message.thread.history(limit = None):
                                if is_message_rip(thread_message):
                                    count += 1
                        if is_message_rip(message):
                                count += 1

                case RipFetchType.PINS:
                    async for message in channel.pins(limit = None):
                        if is_message_rip(message):
                            count += 1

                case _:
                    assert "Unimplemented RipFetchType"
            
            if channel_info.is_cache_qoc:
                async with CACHE_LOCK_QOC[channel.id]:
                    if count != len(RIP_CACHE_QOC[channel.id]):
                        msg = f'QOC Cache validation failed for {channel.jump_url}! ' + \
                            f'Counted {count} rips in discord but counted {len(RIP_CACHE_QOC[channel.id])} in cache. Recaching...'
                        await write_log(msg)
                        if send_channel:
                            await send_channel.send(msg)
                        await get_qoc_rips(channel, GetRipsDesc(rebuild_cache=True))

            if channel_info.is_cache_suborqueue:
                async with CACHE_LOCK_SUBORQUEUE[channel.id]:
                    if count != len(RIP_CACHE_SUBORQUEUE[channel.id]):
                        msg = f'SubOrQueue Cache validation failed for {channel.jump_url}! ' + \
                            f'Counted {count} rips in discord but {len(RIP_CACHE_SUBORQUEUE[channel.id])} in cache. Recaching...'
                        await write_log(msg)
                        if send_channel:
                            await send_channel.send(msg)
                        await get_suborqueue_rips(channel, GetRipsDesc(rebuild_cache=True))

#===============================================#
#                    EVENTS                     #
#===============================================#

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print('#################################')

    await write_log("Good morning! Caching rips...")
    channel_ids = [k for k, v in CHANNELS.items() if 'QUEUE' in v]
    for channel_id in channel_ids:
        channel = bot.get_channel(channel_id)
        if channel:
            suborqueue_rips = await get_suborqueue_rips(channel, GetRipsDesc())
            await write_log(f'Cached {len(suborqueue_rips)} queued rips in {channel.jump_url}.')

    channel_ids = [k for k, v in CHANNELS.items() if 'SUBS' in v or 'SUBS_THREAD' in v or 'SUBS_PIN' in v]
    for channel_id in channel_ids:
        channel = bot.get_channel(channel_id)
        if channel:
            suborqueue_rips = await get_suborqueue_rips(channel, GetRipsDesc())
            await write_log(f'Cached {len(suborqueue_rips)} subbed rips in {channel.jump_url}.')

    channel_ids = [k for k, v in CHANNELS.items() if 'QOC' in v]
    for channel_id in channel_ids:
        channel = bot.get_channel(channel_id)
        if channel:
            qoc_rips = await get_qoc_rips(channel, GetRipsDesc())
            await write_log(f'Cached {len(qoc_rips)} qoc rips in {channel.jump_url}.')

    await write_log('Validating cache...')
    await validate_cache_all(None)
    await write_log('Startup caching complete.')

import traceback
@bot.event
async def on_error(event, *args, **kwargs):
    # https://stackoverflow.com/a/60031624
    await write_log('{}```py\n{}\n```'.format(event, traceback.format_exc()), embed=True)


_bot_close = bot.close
async def close_with_log(self: commands.Bot):
    await write_log("Good night!")
    return await _bot_close()

import types
bot.close = types.MethodType(close_with_log, bot)


@bot.event
async def on_guild_channel_pins_update(channel: typing.Union[GuildChannel, Thread], last_pin: datetime):

    if not channel_is_types(channel, ['QOC', 'SUBS_PIN']):
        return

    global latest_pin_time

    if last_pin is None or last_pin <= latest_pin_time:
        # print("Seems to be a message being unpinned")

        if channel.id in RIP_CACHE_QOC:
            current_message_ids: list[int] = []
            async for message in channel.pins(limit=None):
                current_message_ids.append(message.id)

            message_ids_to_remove: list[int] = []
            for message_id, qoc_rip in RIP_CACHE_QOC[channel.id].items():
                if message_id not in current_message_ids:
                    message_ids_to_remove.append(message_id)

            for message_id in message_ids_to_remove: 
                RIP_CACHE_QOC[channel.id].pop(message_id)

    else:

        async for message in channel.pins(limit=1):

            latest_pin_time = last_pin

            rips = await get_suborqueue_rips_fast(channel, GetRipsDesc())

            SOFT_PIN_LIMIT = _get_config('soft_pin_limit')
            if len(rips) > SOFT_PIN_LIMIT:
                if _get_config("pinlimit_must_die_mode"):
                    await message.unpin()
                    await channel.send(f"**Error**: More than {SOFT_PIN_LIMIT} rips in pins. Unpinned.")
                else:
                    await channel.send(f"**Warning**: More than {SOFT_PIN_LIMIT} rips pinned - please handle them first :(")
        
            await cache_qoc_rip(message)

            verdict, msg = await check_qoc_and_metadata(message.content, message.id, str(message.author))

            # Send msg
            if len(verdict) > 0:
                rip_title = get_rip_title(message.content)
                link = format_message_link(channel.guild.id, channel.id, message.id)
                await channel.send("**Rip**: **[{}]({})**\n**Verdict**: {}\n{}-# React {} if this is resolved.".format(rip_title, link, verdict, msg, DEFAULT_CHECK))


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):

    channel = bot.get_channel(payload.channel_id)
    if channel:
        is_qoc_channel = channel_is_types(channel, ['QOC'])
        is_suborqueue_channel = channel_is_types(channel, ['QUEUE', 'SUBS', 'SUBS_THREAD', 'SUBS_PIN'])

        if is_qoc_channel or is_suborqueue_channel:

            message = await channel.fetch_message(payload.message_id)

            if is_qoc_channel and message.pinned:

                if payload.channel_id in RIP_CACHE_QOC and \
                    payload.message_id in RIP_CACHE_QOC[channel.id]:

                    qoc_rip = RIP_CACHE_QOC[payload.channel_id][payload.message_id]
                    inserted = False
                    react_and_user = ReactAndUser(payload.emoji.name, payload.user_id)
                    for i, cached_react_and_user in enumerate(qoc_rip.react_and_users):
                        if cached_react_and_user.name == payload.emoji.name:
                            qoc_rip.react_and_users.insert(i, react_and_user)
                            inserted = True
                            break
                    if not inserted:
                        qoc_rip.react_and_users.append(react_and_user)
                else:
                    ##NOTE: (Ahmayk) can happen if reaction happens before message is added to cache
                    qoc_rip = await cache_qoc_rip(message)

            if is_suborqueue_channel and '```' in message.content and extract_rip_link(message.content):
                ##NOTE: (Ahmayk) since we have to fetch the message anyway just recache the whole thing
                ## since this doesn't require any more api calls 
                cache_suborqueue_rip(message)


def process_suborqueue_rip_caching(message: Message):
    is_suborqueue_channel = channel_is_types(message.channel, ['QUEUE', 'SUBS', 'SUBS_THREAD'])
    if is_suborqueue_channel and is_message_rip(message): 
        cache_suborqueue_rip(message)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.channel_id in RIP_CACHE_QOC and payload.message_id in RIP_CACHE_QOC[payload.channel_id]: 
        qoc_rip = RIP_CACHE_QOC[payload.channel_id][payload.message_id]
        for cached_react_and_user in qoc_rip.react_and_users:
            if cached_react_and_user == ReactAndUser(payload.emoji.name, payload.user_id):
                qoc_rip.react_and_users.remove(cached_react_and_user)
                break
    if payload.channel_id in RIP_CACHE_SUBORQUEUE and payload.message_id in RIP_CACHE_SUBORQUEUE[payload.channel_id]: 
        suborqueue_rip = RIP_CACHE_SUBORQUEUE[payload.channel_id][payload.message_id]
        if payload.emoji.name in suborqueue_rip.react_names:
            suborqueue_rip.react_names.remove(payload.emoji.name)

@bot.event
async def on_raw_reaction_clear_emoji(payload: discord.RawReactionClearEmojiEvent):
    if payload.channel_id in RIP_CACHE_QOC and payload.message_id in RIP_CACHE_QOC[payload.channel_id]: 
        qoc_rip = RIP_CACHE_QOC[payload.channel_id][payload.message_id]
        for cached_react_and_user in qoc_rip.react_and_users:
            if cached_react_and_user.name == payload.emoji.name:
                qoc_rip.react_and_users.remove(cached_react_and_user)
                break
    if payload.channel_id in RIP_CACHE_SUBORQUEUE and payload.message_id in RIP_CACHE_SUBORQUEUE[payload.channel_id]: 
        suborqueue_rip = RIP_CACHE_SUBORQUEUE[payload.channel_id][payload.message_id]
        for cached_react_name in suborqueue_rip.react_names:
            if cached_react_name == payload.emoji.name:
                suborqueue_rip.react_names.remove(cached_react_name)
                break

@bot.event
async def on_raw_reaction_clear(payload: discord.RawReactionClearEvent):
    if payload.channel_id in RIP_CACHE_QOC and payload.message_id in RIP_CACHE_QOC[payload.channel_id]: 
        qoc_rip = RIP_CACHE_QOC[payload.channel_id][payload.message_id]
        qoc_rip.react_and_users.clear()
    if payload.channel_id in RIP_CACHE_SUBORQUEUE and payload.message_id in RIP_CACHE_SUBORQUEUE[payload.channel_id]: 
        suborqueue_rip = RIP_CACHE_SUBORQUEUE[payload.channel_id][payload.message_id]
        suborqueue_rip.react_names.clear()

def remove_rip_from_cache(message_id: int, channel_id: int):
    if channel_id in RIP_CACHE_QOC and message_id in RIP_CACHE_QOC[channel_id]: 
        RIP_CACHE_QOC[channel_id].pop(message_id)
    if channel_id in RIP_CACHE_SUBORQUEUE and message_id in RIP_CACHE_SUBORQUEUE[channel_id]: 
        RIP_CACHE_SUBORQUEUE[channel_id].pop(message_id)

@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    remove_rip_from_cache(payload.message_id, payload.channel_id)


@bot.event
async def on_raw_message_edit(payload: discord.RawMessageUpdateEvent):
    if payload.channel_id in RIP_CACHE_QOC and payload.message_id in RIP_CACHE_QOC[payload.channel_id]: 
        qoc_rip = RIP_CACHE_QOC[payload.channel_id][payload.message_id]
        qoc_rip = qoc_rip._replace(text = payload.message.content)
        RIP_CACHE_QOC[payload.channel_id][payload.message_id] = qoc_rip
    if payload.channel_id in RIP_CACHE_SUBORQUEUE and payload.message_id in RIP_CACHE_SUBORQUEUE[payload.channel_id]: 
        suborqueue_rip = RIP_CACHE_SUBORQUEUE[payload.channel_id][payload.message_id]
        suborqueue_rip = suborqueue_rip._replace(text = payload.message.content)
        RIP_CACHE_SUBORQUEUE[payload.channel_id][payload.message_id] = suborqueue_rip


# ============ React Functions ============== #

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

# ============ Commands ============== #

class CommandType(Enum):
    NULL = auto()
    MANAGEMENT = auto()
    QOC = auto()

class CommandContext(NamedTuple):
    channel: TextChannel | Thread 
    user: discord.User 

class CommandInfo(NamedTuple):
    func: typing.Callable[[list[str], CommandContext], typing.Awaitable[typing.NoReturn]]
    command_type: CommandType
    brief: str
    desc: str
    format: str
    aliases: List[str]
    examples: List[str]

COMMANDS: dict[str, CommandInfo] = {}

##NOTE: (Ahmayk) This nonsense is so that we can have a simpler way of defining commands
def command(
    command_type: CommandType = CommandType.NULL,
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
            brief=brief,
            desc=desc,
            format=format,
            aliases=aliases,
            examples=examples,
        )
        return func
    return decorator


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
    color = _get_config('embed_color')
    delete_after_seconds = None
    if desc.expires:
        if channel_is_types(channel, ['PROXY_QOC']):
            delete_after_seconds = _get_config('proxy_embed_seconds')
        else: _get_config('embed_seconds')

    for i, line in enumerate(split_message):

        if i == 0: 
            embed = discord.Embed(description=line, color=color, title=desc.title)
        else:
            embed = discord.Embed(description=line, color=color)
            
        if i == len(split_message) - 1: 
            embed.set_footer(text=desc.footer)

        await channel.send(embed=embed, delete_after=delete_after_seconds)

@bot.event
async def on_message(message: Message):

    if message.author == bot.user:
        return

    process_suborqueue_rip_caching(message)

    if not message.content.startswith(COMMAND_PREFIX):
        return

    args = message.content.split(' ')
    command_name = args[0][1:].lower()
    command_info = None
    if command_name in COMMANDS:
        command_info = COMMANDS[command_name]
    else:
        for info in COMMANDS.values():
            if command_name in info.aliases:
                command_info = info
                break

    if not command_info:
        return

    command_context = CommandContext(message.channel, message.author)

    try:
        await command_info.func(args[1:], command_context)
    except Exception as error:

        error_string = f"{type(error).__name__}: {error}"
        error_data = "".join(traceback.format_exception(type(error), error, error.__traceback__))

        print(f"\033[91m {error_data}\033[0m")

        description = f":boom: Intriguing! I have encountered an unexpected error! ```{error_string}```"
        await send(description, message.channel)

        header = f'ERROR on command ${command_name}: {error_string}'
        log_channel = bot.get_channel(LOG_CHANNEL)
        split_texts = split_long_message(error_data, 2000 - len(header))
        for i, m in enumerate(split_texts):
            string = f'```py\n{m}\n```'
            if i == 0:
                string = f'**{header}**\n{string}'
            await send(string, log_channel)



@command(
    command_type=CommandType.MANAGEMENT,
    brief="Get info on all commands",
    format="[command]",
    aliases=['commands', 'halp', 'test'],
)
async def help(args: list[str], command_context: CommandContext):
    result = ''

    for name, info in COMMANDS.items():
        result += f'\n**!{name}**'

        if len(info.format):
            result += f' `{info.format}`'

        if len(info.brief):

            def emoji_match_filter(match):
                name = match.group(1)
                result = f':{name}:' 
                for emoji in command_context.channel.guild.emojis:
                    if emoji.name == name:
                        result = str(emoji)
                        break
                return result

            brief = re.sub(r':(\w+):', emoji_match_filter, info.brief) 

            def config_match_filter(match):
                result = _get_config(match.group(1))
                return str(result)

            brief = re.sub(r'%(\w+)%', config_match_filter, brief) 

            result += f' — {brief}'

    await send_embed(result, command_context.channel, EmbedDesc())

# ============ Roundup commands ============== #

class RoundupFilterType(Enum):
    NULL = auto()
    MYPINS = auto()
    MYFIXES = auto()
    NOFIXES = auto()
    MYFRESH = auto()
    FRESH = auto()
    SPICY = auto()
    SEARCH_TITLE = auto()
    SEARCH_AUTHOR = auto()
    HASREACT = auto()
    NOTHASREACT = auto()
    OVERDUE = auto()
    VET_ALL = auto()

class RoundupDesc(NamedTuple):
    roundup_filter_type: RoundupFilterType = RoundupFilterType.NULL
    message_author_id: int = 0 
    message_author_name: str = ""
    user_id: int = 0 
    conditional_string: str = ""
    search_key: str = ""
    reaction_type: ReactionType = ReactionType.NULL 
    not_found_message: str = ""

async def send_roundup(roundup_desc: RoundupDesc, command_context: CommandContext):
    """
    Sends a roundup message of all rips that the roundup channel the message was sent in points to.
    The roundup_filter_type describes how the roundup will be filtered.
    """
    if not channel_is_types(command_context.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("roundup", command_context.user.name)

    channel = await get_qoc_channel(command_context.channel)
    if channel is None: return

    qoc_rips = await get_qoc_rips(channel, GetRipsDesc(typing_channel=command_context.channel))

    is_spec_overdue_days = _get_config('spec_overdue_days')
    is_overdue_days = _get_config('overdue_days')
    search_keys = roundup_desc.search_key.split('|')

    ##TODO: (Ahmayk) fuzzy username input (ie typing "ahmayk" and matching to their username or display name)
    user_id = command_context.user.id
    if roundup_desc.user_id:
        match = re.search(r'\d+', str(roundup_desc.user_id))
        if match:
            ID = int(match.group(0))
            if command_context.channel.guild:
                search_author = command_context.channel.guild.get_member(ID)
                if search_author:
                    await command_context.channel.send(f"Searching for rips {roundup_desc.conditional_string} by {search_author.name}")

    result = ""

    for qoc_rip in qoc_rips:

        reacts = ""
        num_checks = 0
        num_rejects = 0
        num_goldchecks = 0
        specs_required = 1
        checks_required = 3
        specs_needed = False
        fix_or_alert = False

        for react_and_user in qoc_rip.react_and_users:
            if react_is(ReactionType.GOLDCHECK, react_and_user.name):
                num_goldchecks += 1
            elif react_is(ReactionType.CHECKREQ, react_and_user.name):
                try:
                    checks_required = int(react_and_user.name.split("check")[0])
                except ValueError:
                    print("Error parsing checkreq react: {}".format(react_and_user.name))
            elif react_is(ReactionType.CHECK, react_and_user.name):
                num_checks += 1
            elif react_is(ReactionType.REJECT, react_and_user.name):
                num_rejects += 1
            elif react_is_one([ReactionType.FIX, ReactionType.ALERT], react_and_user.name):
                fix_or_alert = True
            elif react_is(ReactionType.STOP, react_and_user.name):
                specs_needed = True
            elif react_is(ReactionType.NUMBER, react_and_user.name):
                specs_required = KEYCAP_EMOJIS[react_and_user.name]

            reacts += reaction_name_to_emoji_string(react_and_user.name, command_context.channel.guild) 
            reacts += " " 

        indicator = ""
        check_passed = (num_checks - num_rejects >= checks_required) and not fix_or_alert
        specs_passed = (not specs_needed or num_goldchecks >= specs_required)
        is_spec_overdue = (datetime.now(timezone.utc) - qoc_rip.created_at) > timedelta(days=is_spec_overdue_days)
        is_overdue =      (datetime.now(timezone.utc) - qoc_rip.created_at) > timedelta(days=is_overdue_days)
        if check_passed:
            indicator = APPROVED_INDICATOR if specs_passed else AWAITING_SPECIALIST_INDICATOR
        elif specs_needed and not specs_passed and is_spec_overdue:
            indicator = SPECS_OVERDUE_INDICATOR
        elif is_overdue:
            indicator = OVERDUE_INDICATOR

        rip_title = get_rip_title(qoc_rip.text)
        author = get_rip_author(qoc_rip.text, qoc_rip.message_author_name)
        author = author.replace('*', '').replace('_', '')

        review_react_list = [ReactionType.CHECK, ReactionType.GOLDCHECK, ReactionType.FIX, ReactionType.ALERT, ReactionType.REJECT]
        fix_react_list = [ReactionType.FIX, ReactionType.ALERT]

        is_valid = True
        match (roundup_desc.roundup_filter_type):
            case RoundupFilterType.MYPINS:
                is_valid = qoc_rip.message_author_id == roundup_desc.message_author_id
            case RoundupFilterType.MYFIXES:
                is_valid = qoc_rip_has_reaction_one_from_user(fix_react_list, user_id, qoc_rip)
            case RoundupFilterType.NOFIXES:
                is_valid = not qoc_rip_has_reaction_one(fix_react_list, qoc_rip)
            case RoundupFilterType.MYFRESH:
                is_valid = not qoc_rip_has_reaction_one_from_user(review_react_list, user_id, qoc_rip)
            case RoundupFilterType.FRESH:
                is_valid = not qoc_rip_has_reaction_one(review_react_list, qoc_rip)
            case RoundupFilterType.SPICY:
                is_valid = qoc_rip_has_reaction(ReactionType.CHECK, qoc_rip) and \
                            qoc_rip_has_reaction(ReactionType.REJECT, qoc_rip)
            case RoundupFilterType.SEARCH_TITLE:
                is_valid = False
                for key in search_keys:
                    if line_contains_substring(rip_title, key):
                        is_valid = True
                        break
            case RoundupFilterType.SEARCH_AUTHOR:
                is_valid = False
                for key in search_keys:
                    if line_contains_substring(author, key):
                        is_valid = True
                        break
            case RoundupFilterType.HASREACT:
                is_valid = qoc_rip_has_reaction(roundup_desc.reaction_type, qoc_rip)
            case RoundupFilterType.NOTHASREACT:
                is_valid = True
                for react_and_user in qoc_rip.react_and_users:
                    if react_is(roundup_desc.reaction_type, react_and_user.name):
                        is_valid = False
                        break
            case RoundupFilterType.OVERDUE:
                is_valid = is_overdue
            case RoundupFilterType.VET_ALL:
                vet_reacts, msg = await vet_message(qoc_rip.text) 
                reacts = vet_reacts
                is_valid = True 

        if is_valid:
            link = format_message_link(channel.guild.id, channel.id, qoc_rip.message_id)
            base_message = f'**[{rip_title}]({link})**\n{author}'
            if len(indicator) > 0:
                base_message = f'{indicator} **[{rip_title}]({link})** {indicator}\n{author}'
            result += base_message + f' | {reacts}\n'
            result += "━━━━━━━━━━━━━━━━━━\n" # a line for readability!

    if result != "":

        if roundup_desc.roundup_filter_type == RoundupFilterType.VET_ALL:
            result += f"```\nLEGEND:\n{QOC_DEFAULT_LINKERR}: Link cannot be parsed\n{DEFAULT_CHECK}: Rip is OK\n{DEFAULT_FIX}: Rip has potential issues, see below\n{QOC_DEFAULT_BITRATE}: Bitrate is not 320kbps\n{QOC_DEFAULT_CLIPPING}: Clipping```"

        await send_embed(result, command_context.channel, EmbedDesc(expires=True))
    else:

        not_found_message = "No rips."
        if len(roundup_desc.not_found_message):
            not_found_message = roundup_desc.not_found_message
        await send(not_found_message, command_context.channel)


@command(
    command_type=CommandType.QOC,
    aliases = ['down_taunt', 'qoc', 'qocparty', 'roudnup', 'links', 'list', 'ls'],
    brief="show all Qoc rips",
)
async def roundup(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc()
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="show QoC rips you've pinned :pushpin:",
)
async def mypins(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYPINS,
                               message_author_id = command_context.user.id, not_found_message = "No pins are yours.")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="show QoC rips you've wrenched :fix: :alert:",
)
async def myfixes(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYFIXES, \
                               user_id = command_context.user.id, conditional_string = "with wrenches")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="show QoC rips you've not reviewed",
)
async def myfresh(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYFRESH, \
                               user_id = command_context.user.id, conditional_string = "not reviewed")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    aliases = ['blank', 'bald', 'clean', 'noreacts'],
    brief="show QoC rips no one has reviewed",
)
async def fresh(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.FRESH, \
            not_found_message = "No fresh rips.")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="show QoC rips with at least one :check: and one :reject:",
)
async def spicy(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SPICY, \
            not_found_message = "No spicy rips :(")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    format="<search text>",
    brief="search for QoC rips with text in title",
    desc="Does not need quotes.",
    examples=["Deltarune", "PAL", "Mother 3"]
)
async def search(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Inclue what you want to search for! I'll search for it in the titles of QoC rips.", \
                           command_context.channel)

    input_text = "".join([str(s) for s in args])

    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SEARCH_TITLE, \
            search_key=input_text,\
            not_found_message = f'No rips containing `{input_text}` in title found.')
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="show QoC email rips",
    aliases=["email"]
)
async def emails(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SEARCH_AUTHOR, \
            search_key= "email",\
            not_found_message = "No emails.")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    format="<event name>",
    brief="show QoC event rips",
    aliases=["event"],
    desc="text is searched in the rip's author line. Does not need quotes.",
    examples=["christmas", "secret", "deez nuts day"]
)
async def events(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please include the event name tagged in rips.", \
                           command_context.channel)

    input_text = "".join([str(s) for s in args])

    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SEARCH_AUTHOR, \
            search_key= input_text,\
            not_found_message = f'No `{input_text}` rips.')
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="show QoC rips with :check:",
    desc="does not consider :goldcheck: or check number requirements such as :7check:",
)
async def checks(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactionType.CHECK, not_found_message="No checks found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="show QoC rips WITHOUT :check:",
    aliases=["nocheck"]
)
async def nochecks(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.NOTHASREACT, \
                               reaction_type=ReactionType.CHECK, not_found_message="No non-checked rips found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="show QoC rips with :reject:",
)
async def rejects(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactionType.REJECT, not_found_message="No rejected rips found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="show QoC rips WITHOUT :reject:",
    aliases=["noreject"]
)
async def norejects(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.NOTHASREACT, \
                               reaction_type=ReactionType.REJECT, not_found_message="No non-rejected rips found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="show QoC rips with :fix:",
    aliases=['wrenches', 'fix', 'wrench']
)
async def fixes(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactionType.FIX, not_found_message="No wrenches found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="show QoC rips WITHOUT :fix:",
    aliases=["nowrenches", "nofix", "nowrench"]
)
async def nofixes(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.NOTHASREACT, \
                               reaction_type=ReactionType.FIX, not_found_message="No non-fix rips found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="show QoC rips with :stop:",
)
async def stops(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactionType.STOP, not_found_message="No octogons found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief=f'show QoC rips pinned for over %overdue_days% days'
)
async def overdue(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.OVERDUE, not_found_message="No overdue rips.")
    await send_roundup(roundup_desc, command_context)


# ============ Counting Commands ============== #


@bot.command(name='count', brief="counts all pinned rips")
async def count(ctx: Context):
    """
    Count the number of pinned messages that contain rips according to the roundup channel.
    """
    heard_command("count", ctx.message.author.name)

    if channel_is_type(ctx.channel, 'PROXY_QOC'):
        channel = await get_qoc_channel(ctx.channel)
        if channel is None: return
        else: proxy = f"\n-# Showing results from <#{channel.id}>."
    else:
        channel = ctx.channel
        proxy = ""

    rips = await get_suborqueue_rips_fast(channel, GetRipsDesc(typing_channel=ctx.channel))
    pincount = len(rips)

    if (pincount < 1):
        result = "`* Determination.`"
    else:
        result = f"`* {pincount} left.`"

    result += proxy
    await ctx.channel.send(result)


@bot.command(name='limitcheck', aliases=['pinlimit'], brief="pin limit checker")
async def limitcheck(ctx: Context):
    """
    Count the number of available rips below the 50 rip limit.
    """
    heard_command("limitcheck", ctx.message.author.name)

    if channel_is_type(ctx.channel, 'PROXY_QOC'):
        channel = await get_qoc_channel(ctx.channel)
        if channel is None: return
        else: proxy = f"\n-# Showing results from <#{channel.id}>."
    else:
        channel = ctx.channel
        proxy = ""

    rips = await get_suborqueue_rips_fast(channel, GetRipsDesc(typing_channel=ctx.channel))
    result = f"You can pin {_get_config('soft_pin_limit') - len(rips)} more rips until I start complaining about pin space."

    result += proxy
    await ctx.channel.send(result)


@bot.command(name='count_subs', brief='count number of remaining submissions')
async def count_subs(ctx: Context, sub_channel_link: str = None):
    """
    Count number of messages in a channel (e.g. submissions).
    Retrieve the entire history of a channel and count the number of messages not in threads.
    Accepts an optional link argument to the subs-type channel to view - if not, first valid channel in config is used.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("count_subs", ctx.message.author.name)

    sub_channel_id, msg = parse_channel_link(sub_channel_link, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'])
    if len(msg) > 0:
        await ctx.channel.send(msg)
        if sub_channel_id == -1: return

    channel = bot.get_channel(sub_channel_id)

    rips = await get_suborqueue_rips_fast(channel, GetRipsDesc(typing_channel=ctx.channel))
    count = len(rips)

    if (count < 1):
        result = "```ansi\n\u001b[0;31m* Determination.\u001b[0;0m```"
    else:
        result = f"```ansi\n\u001b[0;31m* {count} left.\u001b[0;0m```"

    await ctx.channel.send(result)


# ============ SubOrQueue Rip Commands ============== #

class SubOrQueueRipFilterType(Enum):
    NULL = auto()
    HASREACT = auto()
    UNSENT = auto()
    SEARCH = auto()
    EVENT = auto()
    SCOUT = auto()

class SendSubOrQueueDesc(NamedTuple):
    suborqueue_rip_filter_type: SubOrQueueRipFilterType = SubOrQueueRipFilterType.NULL 
    reaction_type: ReactionType = ReactionType.NULL 
    channel_types: List[str] = []
    search_key: str = ""
    not_found_message: str = ""

async def send_suborqueue_rips(send_suborqueue_desc: SendSubOrQueueDesc, channel_link: str, optional_time: float, ctx: Context):
    """
    Sends a list of rips from either all submission or queue channels according to a filter.
    """
    channel_id, msg = parse_channel_link(channel_link, send_suborqueue_desc.channel_types)
    if len(msg) > 0 or channel_link is None:
        if channel_link is not None:
            await ctx.channel.send("Warning: something went wrong parsing channel link. Defaulting to showing from all known queues.")
        channel_ids = []
        for k, v in CHANNELS.items():
            for type in send_suborqueue_desc.channel_types:
                if type in v:
                    channel_ids.append(k)
    else:
        channel_ids = [channel_id]

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    async with ctx.channel.typing():
        result = ""
        count = 0

        search_keys = send_suborqueue_desc.search_key.split('|')
        prefix = send_suborqueue_desc.search_key.lower()

        qoc_emote = DEFAULT_QOC
        if ctx.guild:
            for e in ctx.guild.emojis:
                if e.name.lower() == "qoc":
                    qoc_emote = e

        for channel_id in channel_ids:
            channel = bot.get_channel(channel_id)

            rips = await get_suborqueue_rips(channel, GetRipsDesc(typing_channel=ctx.channel))

            result += f'<#{channel_id}>:\n'

            for suborqueue_rip in rips:

                rip_title = get_rip_title(suborqueue_rip.text)
                rip_author = get_raw_rip_author(suborqueue_rip.text)

                is_valid = False
                match(send_suborqueue_desc.suborqueue_rip_filter_type):
                    case SubOrQueueRipFilterType.HASREACT:
                        is_valid = suborqueue_rip_has_reaction(send_suborqueue_desc.reaction_type, suborqueue_rip)
                    case SubOrQueueRipFilterType.UNSENT:
                        is_valid = line_contains_substring(rip_author, 'email') and \
                                not suborqueue_rip_has_reaction(ReactionType.EMAILSENT, suborqueue_rip)
                    case SubOrQueueRipFilterType.SEARCH:
                        for key in search_keys:
                            if line_contains_substring(rip_title, key):
                                is_valid = True
                                break
                    case SubOrQueueRipFilterType.EVENT:
                        for key in search_keys:
                            if line_contains_substring(rip_author, key):
                                is_valid = True
                                break
                    case SubOrQueueRipFilterType.SCOUT:
                        is_valid = rip_title.lower().startswith(prefix)
                    case _:
                        assert "Unimplemented SubOrQueueRipFilterType"

                if is_valid:
                    if suborqueue_rip_has_reaction(ReactionType.QOC, suborqueue_rip):
                        result += f"{qoc_emote} "
                    rip_link = format_message_link(channel.guild.id, suborqueue_rip.channel_id, suborqueue_rip.message_id)
                    result += f'**[{rip_title}]({rip_link})**\n'
                    count += 1

            result += '------------------------------\n'

        if count == 0:
            not_found_message = "No rips found."
            if len(send_suborqueue_desc.not_found_message):
                not_found_message = send_suborqueue_desc.not_found_message
            await ctx.channel.send(not_found_message)
        else:
            await send_embed_old(ctx.channel, result, time)


@bot.command(name='search_subs', aliases = ['search_sub'], brief='search for submissions with text in title')
async def search_subs(ctx: Context, search_key: str, sub_channel_link: str = None, optional_time = None):
    """
    Search for submissions by rip title.
    Supports `|` for multiple search keys.
    """
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SEARCH, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'], \
                              search_key = search_key, \
                              not_found_message = "No submissions containing indicated text in title found.")
    await send_suborqueue_rips(desc, sub_channel_link, optional_time, ctx)


@bot.command(name='event_subs', aliases = ['event_sub'], brief='shows event submissions from linked channel')
async def event_subs(ctx: Context, event: str = None, sub_channel_link: str = None, optional_time = None):
    """
    Retrieve all messages in a submission channel that are tagged as for an event.
    The provided string must appear in the rip's author label (case insensitive).
    Supports `|` for multiple search keys.
    """
    if channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']) and event is None:
        await ctx.channel.send("Error: Please indicate the event name. Rips should be tagged with this name.")
        return

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.EVENT, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'], \
                              search_key = event, \
                              not_found_message = "No submissions containing indicated text in author line found.")
    await send_suborqueue_rips(desc, sub_channel_link, optional_time, ctx)


@bot.command(name='frames', brief='find approved rips with thumbnail reacts')
async def frames(ctx: Context, channel_link: str = None, optional_time = None):
    """
    Search queue channel for rips with "thumbnail needed" react.
    """
    heard_command("frames", ctx.message.author.name)

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.HASREACT, \
                              channel_types = ["QUEUE"], \
                              reaction_type = ReactionType.THUMBNAIL)
    await send_suborqueue_rips(desc, channel_link, optional_time, ctx)


@bot.command(name='alerts', brief='find approved rips with alert reacts')
async def alerts(ctx: Context, channel_link: str = None, optional_time = None):
    """
    Search queue channel for rips with alert react.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("alerts", ctx.message.author.name)

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.HASREACT, \
                              channel_types = ["QUEUE"], \
                              reaction_type = ReactionType.ALERT)
    await send_suborqueue_rips(desc, channel_link, optional_time, ctx)


@bot.command(name='metadata', brief='find approved rips with metadata reacts')
async def metadata(ctx: Context, channel_link: str = None, optional_time = None):
    """
    Search queue channel for rips with metadata react.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("metadata", ctx.message.author.name)

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.HASREACT, \
                              channel_types = ["QUEUE"], \
                              reaction_type = ReactionType.METADATA)
    await send_suborqueue_rips(desc, channel_link, optional_time, ctx)

@bot.command(name='unsent', brief='find approved emails with no emailsent reacts')
async def unsent(ctx: Context, channel_link: str = None, optional_time = None):
    """
    Search queue channel for rips tagged email with no "approval email sent" react.
    """
    heard_command("unsent", ctx.message.author.name)

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.UNSENT, \
                              channel_types = ["QUEUE"])
    await send_suborqueue_rips(desc, channel_link, optional_time, ctx)


@bot.command(name='scout', brief='find approved rips with specific title prefix')
async def scout(ctx: Context, prefix: str = None, channel_link: str = None, optional_time = None):
    """
    Search queue channel for rips starting with the specific prefix (e.g. letter E).
    The prefix is case insensitive.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("scout", ctx.message.author.name)

    if prefix is None:
        await ctx.channel.send("Error: Please provide a prefix string.")
        return

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SCOUT, \
                              channel_types = ['QUEUE'], \
                              search_key = prefix, \
                              not_found_message = 'No approved rips starting with {prefix} found.')
    await send_suborqueue_rips(desc, channel_link, optional_time, ctx)


@bot.command(name='scout_stats', brief='summarize approved rips with specific letter prefix')
async def scout_stats(ctx: Context, channel_link: str = None, optional_time = None):
    """
    show count of rips starting with each letter.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("scout_stats", ctx.message.author.name)

    channel_id, msg = parse_channel_link(channel_link, ['QUEUE'])
    if len(msg) > 0:
        await ctx.channel.send(msg)
        return

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    channel = bot.get_channel(channel_id)
    if channel is None: await ctx.channel.send("Error: Invalid channel found. Contact bot developers to update list of channels.")

    suborqueue_rips = await get_suborqueue_rips_fast(channel, GetRipsDesc(typing_channel=ctx.channel))

    count = {}
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ': # could have done string.ascii_uppercase but i dont think the alphabet is getting any updates
        count[letter] = 0

    for suborqueue_rip in suborqueue_rips:
        rip_title = get_raw_rip_title(suborqueue_rip.text)
        prefix = rip_title.lower()[0]
        if prefix in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ':  # isalpha becomes fucked with unicode characters i think
            count[prefix.upper()] += 1
        else:
            if prefix in count.keys():
                count[prefix] += 1
            else:
                count[prefix] = 1
    
    result = ""
    maxCount = max(max(count.values()), 20)
    for k, v in sorted(count.items()):
        result += f"{k}: " + ("▮" * int(v / maxCount * 20)) + f" ({v})\n"

    if len(suborqueue_rips) == 0:
        await ctx.channel.send("No approved rips found.")
    else:
        await send_embed_old(ctx.channel, result, time)


# ============ Basic QoC commands ============== #

@bot.command(name='vet', brief='scan pinned messages for bitrate and clipping issues')
async def vet(ctx: Context, optional_arg = None):
    """
    Find rips in pinned messages with bitrate/clipping issues and show their details
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return

    if optional_arg is not None:
        await ctx.channel.send("WARNING: ``!vet`` takes no argument. Did you mean to use ``!vet_msg`` or ``!vet_url``?")
        return
    
    if ctx.message.reference is not None:
        await ctx.channel.send("WARNING: ``!vet`` takes no argument (nor replies). Did you mean to use ``!vet_msg`` or ``!vet_url``?")
        return
    
    await vet_from(ctx)


@bot.command(name='vet_from', brief='!vet but start from a message')
async def vet_from(ctx: Context, from_msg):
    """
    Find rips in pinned messages with bitrate/clipping issues and show their details, only counting messages not older than linked message
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("vet_from", ctx.message.author.name)

    vet_all_pins = from_msg is None
    if not vet_all_pins:
        _, _, from_message, status = await parse_message_link(from_msg)
        if from_message is None:
            await ctx.channel.send(status)
            return
        from_timestamp = from_message.created_at

    channel = await get_qoc_channel(ctx.channel)
    if channel is None: return

    if not ffmpegExists():
        await ctx.channel.send("WARNING: ffmpeg command not found on the bot's server. Please contact the developers.")
        return

    async with ctx.channel.typing():
        rips = await get_suborqueue_rips_fast(channel, GetRipsDesc())

        for rip in rips:
            if not vet_all_pins and rip.created_at < from_timestamp:
                continue
            qcCode, qcMsg, _ = await check_qoc(rip.text, False)

            if qcCode != 0:
                rip_title = get_rip_title(rip.text)
                verdict = code_to_verdict(qcCode, qcMsg)
                link = format_message_link(channel.guild.id, channel.id, rip.message_id)
                await ctx.channel.send("**Rip**: **[{}]({})**\n**Verdict**: {}\n{}\n-# React {} if this is resolved.".format(rip_title, link, verdict, qcMsg, DEFAULT_CHECK))

        if len(rips) == 0:
            await ctx.channel.send("No pinned rips found to QoC.")
        else:
            await ctx.channel.send("Finished QoC-ing. Please note that these are only automated detections - you should verify the issues in Audacity and react manually.")


@bot.command(name='vet_all', brief='vet all pinned messages and show summary')
async def vet_all(ctx: Context, optional_time = None):
    """
    Retrieve all pinned messages (except the first one) and perform basic QoC, giving emoji labels.
    """
    heard_command("vet_all", ctx.message.author.name)

    if not ffmpegExists():
        await ctx.channel.send("WARNING: ffmpeg command not found on the bot's server. Please contact the developers.")
        return

    async with ctx.channel.typing():
        roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.VET_ALL)
        await send_roundup(roundup_desc, optional_time, ctx)


@bot.command(name='vet_msg', brief='vet a single message link')
async def vet_msg(ctx: Context, msg_link: str = None):
    """
    Perform basic QoC on a linked message.
    The first non-YouTube link found in the message is treated as the rip URL.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("vet_msg", ctx.message.author.name)

    if msg_link is None:
        await ctx.channel.send("Error: Please provide a link to message.")
        return

    async with ctx.channel.typing():
        server, channel, message, status = await parse_message_link(msg_link)
        if message is None:
            await ctx.channel.send(status)
            return

        verdict, msg = await check_qoc_and_metadata(message.content, message.id, str(message.author), True)
        rip_title = get_rip_title(message.content)

        await ctx.channel.send("**Rip**: **{}**\n**Verdict**: {}\n**Comments**:\n{}".format(rip_title, verdict, msg))


@bot.command(name='vet_url', brief='vet a single url')
async def vet_url(ctx: Context, url: str = None):
    """
    Perform basic QoC on an URL.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("vet_url", ctx.message.author.name)

    urls = extract_rip_link(url)
    if len(urls) == 0:
        await ctx.channel.send("Error: Please provide an URL to rip.")
        return

    async with ctx.channel.typing():
        code, msg = await run_blocking(performQoC, urls[0])
        verdict = code_to_verdict(code, msg)

        await ctx.channel.send("**Verdict**: {}\n**Comments**:\n{}".format(verdict, msg))


@bot.command(name='count_dupe', brief='count the number of dupes')
async def count_dupe(ctx: Context, msg_link: str = None, check_queues: str = None):
    """
    Count the number of dupes for a given link to rip message.
    Accepts an optional argument to also count rips in queues, which can take longer.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("count_dupe", ctx.message.author.name)

    if msg_link is None:
        await ctx.channel.send("Error: Please provide a link to message.")
        return

    server, channel, message, status = await parse_message_link(msg_link)
    if message is None:
        await ctx.channel.send(status)
        return

    playlistId = extract_playlist_id('\n'.join(message.content.splitlines()[1:])) # ignore author line
    description = get_rip_description(message.content)
    rip_title = get_rip_title(message.content)

    p, msg = await run_blocking(countDupe, description, YOUTUBE_CHANNEL_NAME, playlistId, YOUTUBE_API_KEY)
    if len(msg) > 0:
        await ctx.channel.send(msg)
        if check_queues is None: return

    if check_queues is not None:
        q = 0
        queue_channels = [k for k, v in CHANNELS.items() if 'QUEUE' in v]
        for queue_channel_id in queue_channels:
            queue_channel = server.get_channel(queue_channel_id)
            if queue_channel:
                suborqueue_rips = await get_suborqueue_rips_fast(queue_channel, GetRipsDesc(typing_channel=ctx.channel))
                q += sum([isDupe(description, get_rip_description(r.text)) for r in suborqueue_rips if r.message_id != message.id])

        # https://codegolf.stackexchange.com/questions/4707/outputting-ordinal-numbers-1st-2nd-3rd#answer-4712 how
        ordinal = lambda n: "%d%s" % (n,"tsnrhtdd"[(n//10%10!=1)*(n%10<4)*n%10::4])

        if check_queues is not None:
            await ctx.channel.send(f"**Rip**: **{rip_title}**\nFound {p + q} rips of the same track ({p} on the channel, {q} in queues). This is the {ordinal(p + q + 1)} rip of this track.")
        else:
            await ctx.channel.send(f"**Rip**: **{rip_title}**\nFound {p} rips of the same track on the channel. This is the {ordinal(p + 1)} rip of this track.")


@bot.command(name='scan', brief='scan queue/sub channel for metadata issues')
async def scan(ctx: Context, channel_link: str = None, start_index: int = None, end_index: int = None):
    """
    Scan through a submission or queue channel for metadata issues.
    Channel link must be provided as argument.
    Accepts two optional arguments to specify the range of rips to scan through, if the channel has too many rips.

    - `start_index`: First rip to look at (inclusive). Index 1 means start from the oldest rip.
    - `end_index`: Last rip to look at (inclusive). Index 100 means scan until and including the 100th oldest rip. If this is not provided, scan to the latest rip.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("scan", ctx.message.author.name)

    global latest_scan_time
    if latest_scan_time is not None and datetime.now(timezone.utc) - latest_scan_time > timedelta(minutes=30):
        await ctx.channel.send("Please wait at least 30 minutes and contact bot developers before running this command again.")
        return

    if channel_link is None:
        await ctx.channel.send("Please provide a link to the channel you want to scan.")
        return

    channel_id, msg = parse_channel_link(channel_link, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD', 'QUEUE'])
    if len(msg) > 0:
        await ctx.channel.send(msg)
        return
    
    channel = bot.get_channel(channel_id)
    if channel is None: await ctx.channel.send("Error: Invalid channel found. Contact bot developers to update list of channels.")

    rips = await get_suborqueue_rips_fast(channel, GetRipsDesc(typing_channel=ctx.channel))
    rips.reverse()
    num_rips = len(rips)

    if start_index is not None:
        try:
            sInd = int(start_index)
            if sInd < 1: raise ValueError()
        except ValueError:
            await ctx.channel.send("Invalid start index argument.")
            return
        sInd = max(sInd, 1)
        
        if end_index is not None:
            try:
                eInd = int(start_index)
                if eInd < sInd: raise ValueError()
            except ValueError:
                await ctx.channel.send("Invalid end index argument.")
                return
            eInd = min(eInd, num_rips)
        else:
            eInd = num_rips
    else:
        sInd = 1
        eInd = num_rips

    if eInd - sInd > 100:
        await ctx.channel.send("Warning: More than 100 rips found. Limit the scanning range by specifying the indexes, e.g. `!scan [link] 1 50` to scan the oldest 50 rips.")
        return

    async with ctx.channel.typing():
        index = 0
        for rip in rips:
            index += 1
            if (index < sInd):
                continue
            if (index > eInd):
                break
            
            rip_title = get_rip_title(rip.text)

            mtCode, mtMsg = await check_metadata(rip.text, rip.message_id, rip.message_author_name)
            if mtCode == -1:
                await write_log("Warning: cannot check metadata of message\nRip: {}\n{}".format(rip_title, mtMsg))

            if mtCode == 1:
                link = format_message_link(ctx.guild.id, channel_id, rip.message_id)
                await ctx.channel.send("**Rip**: **[{}]({})**\n**Verdict**: {}\n{}".format(rip_title, link, DEFAULT_METADATA, mtMsg))
        
        latest_scan_time = datetime.now(timezone.utc)
        await ctx.channel.send("Finished checking metadata of {} rips. Wait for ~30 minutes and contact bot developers if you wish to use this command again today.".format(eInd - sInd))


@bot.command(name='peek_msg', brief='print file metadata from message link')
async def peek_msg(ctx: Context, msg_link: str = None, use_ffprobe = None):
    """
    Prints the file metadata of the rip at linked message.
    The first non-YouTube link found in the message is treated as the rip URL.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("peek_msg", ctx.message.author.name)

    if msg_link is None:
        await ctx.channel.send("Error: Please provide a link to message.")
        return

    async with ctx.channel.typing():
        server, channel, message, status = await parse_message_link(msg_link)
        if message is None:
            await ctx.channel.send(status)
            return
        
        rip_title = get_rip_title(message.content)
        
        urls = extract_rip_link(message.content)
        errs = []
        for url in urls:
            if use_ffprobe is not None:
                if not ffmpegExists():
                    await ctx.channel.send("ffmpeg not found on remote. Please contact developers, or run this command without the extra argument.")
                    return
                code, msg = await run_blocking(getFileMetadataFfprobe, url)
            else:
                code, msg = await run_blocking(getFileMetadataMutagen, url)
            
            if code != -1:
                break
            errs.append(msg)
        if code == -1:
            await ctx.channel.send("Error reading message:\n{}".format('\n'.join(errs)))
        else:
            long_message = split_long_message("**Rip**: **{}**\n**File metadata**:\n{}".format(rip_title, msg), _get_config('character_limit'))
            for line in long_message:
                await ctx.channel.send(line)


@bot.command(name='peek_url', brief='print file metadata from url')
async def peek_url(ctx: Context, url: str = None, use_ffprobe = None):
    """
    Prints the file metadata of the rip at linked URL.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("peek_url", ctx.message.author.name)

    urls = extract_rip_link(url)
    if len(urls) == 0:
        await ctx.channel.send("Error: Please provide an URL to rip.")
        return

    async with ctx.channel.typing():
        if use_ffprobe is not None:
            if not ffmpegExists():
                await ctx.channel.send("ffmpeg not found on remote. Please contact developers, or run this command without the extra argument.")
                return
            code, msg = await run_blocking(getFileMetadataFfprobe, url)
        else:
            code, msg = await run_blocking(getFileMetadataMutagen, url)
        
        if code == -1:
            await ctx.channel.send("Error reading URL: {}".format(msg))
        else:
            long_message = split_long_message("**File metadata**:\n{}".format(msg), _get_config('character_limit'))
            for line in long_message:
                await ctx.channel.send(line)

@bot.command(name='validate_cache', brief='makes sure all rips are in rip cache')
async def validate_cache(ctx: Context):

    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("validate_cache", ctx.message.author.name)

    await ctx.channel.send("Validating cache of rips in all channels...")
    await validate_cache_all(ctx.channel)
    await ctx.channel.send("Cache validated!")


@bot.command(name='reset_cache', brief='force reset rip cache for a channel')
async def reset_cache(ctx: Context, channel_link: str = None):

    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("reset_cache", ctx.message.author.name)

    if channel_link is None:
        await ctx.channel.send("Please provide a link to the channel you want to reset the cache for.")
        return

    channel_id, msg = parse_channel_link(channel_link, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD', 'QUEUE', 'QOC'], False)
    if len(msg) > 0:
        await ctx.channel.send(msg)
    if not channel_id:
        return
    
    channel = bot.get_channel(channel_id)
    init_channel_cache(channel.id)
    channel_info = get_channel_info(channel)

    if not channel_info.is_cache_qoc and not channel_info.is_cache_suborqueue:
        return await ctx.channel.send(f'{channel.jump_url} is not cacheable!')

    await ctx.channel.send("Rebuilding cache...")

    suborqueue_count_old = 0 
    async with CACHE_LOCK_SUBORQUEUE[channel.id]:
        suborqueue_count_old = len(RIP_CACHE_SUBORQUEUE[channel.id])
    qoc_count_old = 0
    async with CACHE_LOCK_QOC[channel.id]:
        qoc_count_old = len(RIP_CACHE_QOC[channel.id])

    return_message = f'Cache rebuilt!'

    if channel_info.is_cache_suborqueue:
        suborqueue_rips = await get_suborqueue_rips(channel, GetRipsDesc(typing_channel=ctx.channel, rebuild_cache=True))
        return_message += f'\nSubOrQueue rip count: {suborqueue_count_old} => {len(suborqueue_rips)}' 

    if channel_info.is_cache_qoc:
        qoc_rips = await get_qoc_rips(channel, GetRipsDesc(typing_channel=ctx.channel, rebuild_cache=True))
        return_message += f'\nQoc rip count: {qoc_count_old} => {len(qoc_rips)}' 

    await ctx.channel.send(return_message)

# ============ Config commands ============== #

@bot.command(name='enable_metadata')
async def enable_metadata(ctx: Context): 
    _set_config('metadata', True)
    await ctx.channel.send("Advanced metadata checking enabled.")

@bot.command(name='disable_metadata')
async def disable_metadata(ctx: Context): 
    _set_config('metadata', False)
    await ctx.channel.send("Advanced metadata checking disabled.")

@bot.command(name='enable_pinlimit_must_die')
async def enable_pinlimit_must_die(ctx: Context): 
    _set_config('pinlimit_must_die_mode', True)
    await ctx.channel.send("Soft pin limit is now hard pin limit. Good luck.")

@bot.command(name='disable_pinlimit_must_die')
async def disable_pinlimit_must_die(ctx: Context): 
    _set_config('pinlimit_must_die_mode', False)
    await ctx.channel.send("Back to normal.")


def _set_config(config: str, value):
    if os.path.exists('config.json'):
        with open('config.json', 'r', encoding='utf-8') as file:
            configs = json.load(file)
    else:
        configs = {}
    configs[config] = value
    with open('config.json', 'w', encoding='utf-8') as file:
        json.dump(configs, file, indent=4)

def _get_config(config: str):
    if os.path.exists('config.json'):
        with open('config.json', 'r', encoding='utf-8') as file:
            configs = json.load(file)
            try:
                value = configs[config]
            except KeyError:
                raise Exception("Unknown Config: " + config)
                value = None
            return value
    else:
        return None

# ============ Helper/test commands ============== #

@bot.command(name='help_old')
async def help_old(ctx: Context):    
    async with ctx.channel.typing():
        result = "_**YOU ARE NOW QoCING:**_\n`!roundup [embed_minutes: float]`" + roundup.brief \
            + "\n_**Special lists:**_\n`!mypins` " + mypins.brief \
            + "\n`!myfixes <user_id: str>` " + myfixes.brief \
            + "\n`!myfresh <user_id: str>` " + myfresh.brief\
            + "\n`!fresh` " + fresh.brief\
            + "\n`!spicy` " + spicy.brief\
            + "\n`!search <arg1: str|arg2: str|...>` " + search.brief \
            + "\n`!emails` " + emails.brief + "\n`!events <arg1: str|arg2: str|...>` " + events.brief \
            + "\n`!checks`, `!rejects`, `!wrenches`, `!stops`" \
            + "\n`!nochecks`, `!norejects`, `!nofixes`, `!nostops" \
            + "\n`!overdue` " + overdue.brief.replace('X', str(_get_config('overdue_days'))) \
            + "\n_**Misc. tools:**_\n`!count` " + count.brief \
            + "\n`!limitcheck` " + limitcheck.brief \
            + "\n`!count_subs [sub_channel: link]` " + count_subs.brief \
            + "\n`!search_subs <arg1: str|arg2: str|...> [sub_channel: link]` " + search_subs.brief \
            + "\n`!event_subs <arg1: str|arg2: str|...> [sub_channel: link]` " + event_subs.brief \
            + "\n`!stats [show_queues: any]` " + stats.brief \
            + "\n`!channel_list` " + channel_list.brief \
            + "\n`!cleanup [search_limit: int]` " + cleanup.brief \
            + "\n`!frames, !alerts, !metadata, !unsent [queue_channel: link]` " \
            + "\n`!scout <prefix: str> [queue_channel: link]` " + scout.brief \
            + "\n`!scout_stats [queue_channel: link]` " + scout_stats.brief \
            + "\n_**Auto QoC tools:**_\n`!vet` " + vet.brief + "\n`!vet_all` " + vet_all.brief \
            + "\n`!vet_msg <message: link>` " + vet_msg.brief + "\n`!vet_url <URL: link>` " + vet_url.brief \
            + "\n`!peek_msg <message: link> [ffprobe: any]` " + peek_msg.brief + "\n`!peek_url <URL: link> [ffprobe: any]` " + peek_url.brief \
            + "\n`!count_dupe <message: link> [count_queues: any]` " + count_dupe.brief \
            + "\n_**Experimental tools:**_\n`!scan <queue_channel: link> [start_index: int] [end_index: int]` " + scan.brief \
            + "\n_**Config:**_\n`![enable/disable]_metadata` enables/disables advanced metadata checking (currently {})".format("enabled" if _get_config('metadata') else "disabled") \
            + "\n=====================================" \
            + "\n_**Legend:**_\n`<argument: type>` Mandatory argument\n`[argument: type]` Optional argument" \
            + "\n_**Tips:**_\nUse quotes for string arguments with spaces, e.g. \"Main Theme\"\nAll embed commands accept the [embed_minutes] optional argument"
        await send_embed_old(ctx.channel, result)


@bot.command(name='channel_list', brief='show channels and their supported commands')
async def channel_list(ctx: Context):
    async with ctx.channel.typing():
        channels = [f"<#{channel_id}>: " + ", ".join(types) for channel_id, types in CHANNELS.items()]
        message = [
            "_**Command channel types**_",
            "`QOC`: QoC channel. Rips are pinned. All Qoc tools are avaliable here.",
            "`PROXY_QOC`: Allows running QOC commands in a different channel (and embed last longer by default).",
            "`DEBUG`: For developer testing purposes.",
            "_**Stats channel types**_",
            "`SUBS`: Submission channel. Rips are posted as messages in main channel.",
            "`SUBS_PIN`: Submission channel. Rips are pinned.",
            "`SUBS_THREAD`: Submission channel. Rips are posted in threads.",
            "`QUEUE`: Queue channel. Rips are posted as messages in main channel or threads.",
            "_**Channels**_",
        ]
        message.extend(channels)
        result = "\n".join(message)
        
        await send_embed_old(ctx.channel, result)


@bot.command(name='cleanup', brief='remove bot\'s old embed messages')
async def cleanup(ctx: Context, search_limit: int = None):
    if search_limit is None:
        search_limit = 200
    
    count = 0
    async for message in ctx.channel.history(limit = search_limit):
        if message.author == bot.user and message.embeds:
            await message.delete()
            count += 1
    
    await ctx.channel.send(f"Removed {count} embed messages.")


@bot.command(name='op')
async def test(ctx: Context):
    print(f"op ({ctx.message.author.name})")
    await ctx.channel.send("op")

@bot.command(name='cat', aliases = ['meow'], brief='cat')
async def cat(ctx: Context):
    print(f"cat ({ctx.message.author.name})")
    await ctx.channel.send("meow!")


async def get_suborqueue_rip_stats_string(channel_id: int, typing_channel: TextChannel | Thread) -> str:
    ret = ""
    channel = bot.get_channel(channel_id)
    if channel:

        rips = await get_suborqueue_rips_fast(channel, GetRipsDesc(typing_channel=typing_channel))

        if channel_is_types(channel, ['SUBS', 'SUBS_PIN']):
            ret += f"- <#{channel_id}>: **{len(rips)}** rips\n"

        elif channel_is_types(channel, ['QUEUE', 'SUBS_THREAD']):

            thread_count_dict: dict[int, int] = {}
            for rip in rips:
                if rip.channel_id:
                    if rip.channel_id not in thread_count_dict:
                        thread_count_dict[rip.channel_id] = 0
                    thread_count_dict[rip.channel_id] += 1

            if len(rips) > 0 and (channel_is_type(channel, 'SUBS_THREAD') or len(thread_count_dict) > 1):
                ret += f"- <#{channel_id}>:\n"
                for channel_id, count in thread_count_dict.items():
                    if count > 0:
                        ret += f"  - <#{channel_id}>: **{count}** rips\n"
            else:
                ret += f"- <#{channel_id}>: **{len(rips)}** rips\n"

    return ret

@bot.command(name='stats', brief='show remaining number of rips across channels')
async def stats(ctx: Context, optional_arg = None):
    """
    Display the number of rips in the QoC and submission channels.
    Accepts an optional argument to show queue channels too.
    """
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return
    heard_command("stats", ctx.message.author.name)

    ret = "**QoC channels**\n"

    sub_channels = [k for k, v in CHANNELS.items() if any(t in v for t in ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'])]

    qoc_channels = [k for k, v in CHANNELS.items() if 'QOC' in v and k not in sub_channels]
    for channel_id in qoc_channels:
        team_count = 0
        email_count = 0
        channel = bot.get_channel(channel_id)
        if channel:
            rips = await get_suborqueue_rips_fast(channel, GetRipsDesc(typing_channel=ctx.channel))
            for rip in rips:
                author = get_rip_author(rip.text, rip.message_author_name)
                if 'email' in author.lower():
                    email_count += 1
                else:
                    team_count += 1
            ret += f"- <#{channel_id}>: **{team_count + email_count}** rips\n  - {team_count} team subs\n  - {email_count} email subs\n"

    ret += "**Submission channels**\n"
    for channel_id in sub_channels:
        ret += await get_suborqueue_rip_stats_string(channel_id, ctx.channel)

    if optional_arg is not None:
        ret += "**Queues**\n"
        queue_channels = [k for k, v in CHANNELS.items() if 'QUEUE' in v]
        for channel_id in queue_channels:
            ret += await get_suborqueue_rip_stats_string(channel_id, ctx.channel)

    long_message = split_long_message(ret, _get_config('character_limit'))
    for line in long_message:
        await ctx.channel.send(line)


# While it might occur to folks in the future that a good command to write would be a rip feedback-sending command, something like that
# would be way too impersonal imo.

# This thing here is for when I start attempting slash commands again. Until then, this should be unused.
# Thank you to cibere on the Digiwind server for having the patience of a saint.
#@bot.command(name='sync_commands')
#@commands.is_owner()
#async def sync(ctx):
#  cmds = await bot.tree.sync()
#  await ctx.send(f"Synced {len(cmds)} commands globally!")

# some owner-only commands to config or kill bot if necessary

@bot.command(name='shutdown')
@commands.is_owner()
async def shutdown(ctx: Context):
    await bot.close()

@bot.command(name='current_config', aliases = ['get_config'])
@commands.is_owner()
async def current_config(ctx: Context, conf: str = None):
    if os.path.exists('config.json'):
        with open('config.json', 'r', encoding='utf-8') as file:
            configs = json.load(file)
            if conf is None:
                all_configs = ""
                for k, v in configs.items():
                    all_configs += f"{k}: {v}\n"
                await ctx.channel.send(all_configs)
            else:
                try:
                    await ctx.channel.send(configs[conf])
                except KeyError:
                    await ctx.channel.send(f"Error: No config named {conf}.")
    else:
        await ctx.channel.send("Error: Config file not found.")

@bot.command(name='modify_config', aliases = ['set_config'])
@commands.is_owner()
async def modify_config(ctx: Context, conf: str, value: str):
    if conf is None or value is None:
        await ctx.channel.send("Invalid syntax.")
        return
    
    cur_val = _get_config(conf)
    if value == 'true':
        new_val = True
    elif value == 'false':
        new_val = False
    else:
        try:
            new_val = int(value)
        except ValueError:
            await ctx.channel.send("Error: Invalid value type.")
            return
    
    _set_config(conf, new_val)
    await ctx.channel.send(f"Modified config {conf} from {cur_val} to {new_val}.")

#===============================================#
#               HELPER FUNCTIONS                #
#===============================================#

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


async def send_embed_old(channel: typing.Union[GuildChannel, Thread], message: str, delete_after: float = None):
    """
    Send a long message as embed.

    - message: Text to send as embed
    - delete_after: Number of seconds to automatically remove the message. Defaults to constant at the beginning of file. If set to None, message will not delete.
    """
    long_message = split_long_message(message, _get_config('embed_character_limit'))
    for line in long_message:
        fancy_message = discord.Embed(description=line, color=_get_config('embed_color'))
        await channel.send(embed=fancy_message, delete_after=delete_after)

def channel_is_type(channel: typing.Union[GuildChannel, Thread], type: str):
    return channel.id in CHANNELS.keys() and type in CHANNELS[channel.id] or hasattr(channel, "parent") and channel_is_type(channel.parent, type)

def channel_is_types(channel: typing.Union[GuildChannel, Thread], types: typing.List[str]):
    return channel.id in CHANNELS.keys() and any([t in CHANNELS[channel.id] for t in types]) or hasattr(channel, "parent") and channel_is_types(channel.parent, types)

async def get_qoc_channel(channel: TextChannel | Thread):
    """
    Gets the first channel labeled QOC in bot_secrets.py 
    """
    if channel_is_type(channel, 'PROXY_QOC'):
        qoc_channel, msg = parse_channel_link(None, ["QOC"])
        if len(msg) > 0:
            await channel.send(msg)
            if qoc_channel == -1: return None
        channel = bot.get_channel(qoc_channel)
    return channel

def heard_command(command_name: str, user: str):
    today = datetime.now() # Technically not useful, but it looks gorgeous on my CRT monitor
    print(f"{today.strftime('%m/%d/%y %I:%M %p')}  ~~~  Heard {command_name} command from {user}!")


def parse_optional_time(channel: typing.Union[GuildChannel, Thread], optional_time: float):
    """
    Get the number of houminutesrs from user input for roundup embed commands.
    """
    time = _get_config('proxy_embed_seconds') if channel_is_type(channel, 'PROXY_QOC') else _get_config('embed_seconds')
    msg = None
    if optional_time:
        try:
            new_time = optional_time * 60
            if math.isnan(new_time) or math.isinf(new_time) or new_time < 1:
                raise ValueError
        except ValueError:
            msg = "Warning: Cannot parse time argument - make sure it is a valid value. Using default time of {:.2f} minutes.".format(time / 60)
        else:
            time = new_time
    
    return time, msg    


# https://stackoverflow.com/a/65882269
async def run_blocking(blocking_func: typing.Callable, *args, **kwargs) -> typing.Any:
    """
    Runs a blocking function in a non-blocking way.
    Needed because QoC functions take a while to run.
    """
    func = functools.partial(blocking_func, *args, **kwargs) # `run_in_executor` doesn't support kwargs, `functools.partial` does
    return await bot.loop.run_in_executor(None, func)


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


async def vet_message(text: str) -> typing.Tuple[str, str]:
    """
    Return the QoC verdict of a message as emoji reactions.
    """
    urls = extract_rip_link(text)
    reacts = ""
    for url in urls:
        code, msg = await run_blocking(performQoC, url)
        reacts = code_to_verdict(code, msg)
        
        # debug
        if code == -1:
            await write_log("Message: {}\n\nURL: {}\n\nError: {}".format(text, url, msg))
        else:
            break

    return reacts, ""

def code_to_verdict(code: int, msg: str) -> str:
    """
    Helper function to convert performQoC code output to emoji
    """
    # TODO: use server reaction?
    verdict = {
        -1: QOC_DEFAULT_LINKERR,
        0: DEFAULT_CHECK,
        1: DEFAULT_FIX,
    }[code]
    if code == 1:
        if msgContainsSigninErr(msg):
            verdict = QOC_DEFAULT_LINKERR
        if msgContainsBitrateFix(msg):
            verdict += ' ' + QOC_DEFAULT_BITRATE
        if msgContainsClippingFix(msg):
            verdict += ' ' + QOC_DEFAULT_CLIPPING
    return verdict


async def check_qoc(text: str, fullFeedback: bool = False) -> typing.Tuple[int, str, str]:
    """
    Perform simpleQoC on a message.
    """
    urls = extract_rip_link(text)
    qcCode, qcMsg = -1, "No links detected."
    detectedUrl = "" 
    for url in urls:
        qcCode, qcMsg = await run_blocking(performQoC, url, fullFeedback)
        if qcCode != -1:
            detectedUrl = url
            break
    return qcCode, qcMsg, detectedUrl

##TODO: (Ahmayk) Now that checking rip metadata is fast due to being in cache, have this run on every qoc pin without the youtube check
async def check_metadata(text: str, message_id: int, message_author_name: str, fullFeedback: bool = False) -> typing.Tuple[int, str]:
    """
    Perform metadata checking on rip info.
    If info contains the phrase "unusual metadata", skip most checks
    """
    playlistId = extract_playlist_id('\n'.join(text.splitlines()[1:])) # ignore author line
    description = get_rip_description(text)
    advancedCheck = _get_config('metadata')
    skipCheck = "unusual metadata" in text.lower()
    mtCode = 0
    mtMsgs = []
    if not skipCheck and len(description) > 0:
        mtCode, mtMsgs = await run_blocking(checkMetadata, description, YOUTUBE_CHANNEL_NAME, playlistId, YOUTUBE_API_KEY, advancedCheck)
        pass

    if mtCode != -1 and "[Unusual Pin Format]" in get_rip_author(text, message_author_name):
        mtCode = 1
        mtMsgs.append("Rip author is missing.")

    if mtCode != -1 and not skipCheck:
        rips = []
        channel_ids = [k for k, v in CHANNELS.items() if 'QUEUE' in v or 'QOC' in v]
        for channel_id in channel_ids:
            channel = bot.get_channel(channel_id)
            if channel:
                rips = await get_suborqueue_rips_fast(channel, GetRipsDesc(typing_channel=ctx.channel))

        title = get_raw_rip_title(text)
        desc = get_rip_description(text)
        for rip in rips:
            if rip.message_id != message_id:
                rip_title = get_raw_rip_title(rip.text)
                if title == rip_title:
                    link = format_message_link(channel.guild.id, channel.id, rip.message_id)
                    mtMsgs.append(f"Video title already exists in <#{rip.channel_id}>: [{rip_title}]({link}).")
                    mtCode = 1
                if isDupe(desc, get_rip_description(rip.text), True):
                    link = format_message_link(channel.guild.id, channel.id, rip.message_id)
                    mtMsgs.append(f"Main mix detected in <#{rip.channel_id}>: [{rip_title}]({link}). Add something on the author line to avoid uploading this early.")

    mtMsg = '\n'.join(["- " + m for m in mtMsgs]) if len(mtMsgs) > 0 else ("- Metadata is OK." if fullFeedback else "")

    return mtCode, mtMsg


async def check_qoc_and_metadata(text: str, message_id: int, message_author_name: str, fullFeedback: bool = False) -> typing.Tuple[str, str]:
    """
    Perform simpleQoC and metadata checking on a message.

    - **message**: Message to check
    - **fullFeedback**: If True, display "OK" messages. Otherwise, display only issues.
    """
    verdict = ""
    msg = ""
    rip_title = get_rip_title(text)
    
    # QoC
    qcCode, qcMsg, detectedUrl = await check_qoc(text, fullFeedback)
    if qcCode == -1:
        await write_log("Warning: cannot QoC message\nRip: {}\n{}".format(rip_title, qcMsg))
    elif (qcCode == 1) or fullFeedback:
        verdict += code_to_verdict(qcCode, qcMsg)
        msg += qcMsg + "\n"

    # Metadata
    mtCode, mtMsg = await check_metadata(text, message_id, message_author_name, fullFeedback)
    if mtCode == -1:
        await write_log("Warning: cannot check metadata of message\nRip: {}\n{}".format(rip_title, mtMsg))
    elif mtCode == 1:
        verdict += ("" if len(verdict) == 0 else " ") + DEFAULT_METADATA
    if (mtCode == 1) or fullFeedback:
        msg += mtMsg + "\n"

    # Check for lines between the rip description and link - if it does not start with "Joke", add a warning
    # in order to minimize accidental joke lines when uploading
    if detectedUrl is not None:
        try:
            for line in text.split('```', 2)[2].splitlines():
                line = "".join(c for c in line if c.isprintable())
                if detectedUrl in line:
                    break
                elif len(line) > 0 and not line.startswith('Joke') and not line == '||':
                    msg += "- Line not starting with ``Joke`` detected between description and rip URL. Recommend putting the URL directly under description to avoid accidentally uploading joke lines.\n"
                    break
        except IndexError:
            pass

    return verdict, msg

##TODO: (Ahmayk) refactor input system, don't give default on error by default
def parse_channel_link(link: str | None, types: typing.List[str], give_default: bool = True) -> typing.Tuple[int, str]:
    """
    Parse the channel link and return the channel ID if it matches the specified types.
    If channel is invalid or does not match the types, returns the first channel in config matching the types.
    Returns null channel if no such channel types exists - the caller function should return early.

    Return values:
    - `channel_id`: Parsed channel ID if it is valid, default channel if it isn't, and -1 if no default channel
    - `msg`: Message to print if `channel_id` is not parsed from `link`, empty string otherwise
    """
    try:
        default_id = [k for k, v in CHANNELS.items() if any(t in v for t in types)][0]
    except IndexError:
        return -1, f"Error: No default channels found."
    
    if link is None:
        return default_id, ""

    try:
        arg = int(link.split('/')[5])
    except IndexError:
        return -1, "Error: Cannot parse argument - make sure it is a valid link to channel."

    channel = bot.get_channel(arg)
    if channel_is_types(channel, types):
        return arg, ""
    elif give_default:
        return default_id, f"Warning: Link is not a valid roundup channel, defaulting to <#{default_id}>."
    else:
        return None, f"Error: Link is not a valid roundup channel."

async def parse_message_link(link: str):
    """
    Parse the message link and return the server, channel and message objects.
    """
    try:
        ids = link.replace('>', '').split('/')
        server_id = int(ids[4])
        channel_id = int(ids[5])
        msg_id = int(ids[6])
    except IndexError:
        return None, None, None, "Error: Cannot parse argument - make sure it is a valid link to message (right click > Copy Link)."

    server = bot.get_guild(server_id)
    channel = server.get_channel_or_thread(channel_id)
    message = await channel.fetch_message(msg_id)

    return server, channel, message, ""


def line_contains_substring(line: str, substring: str) -> bool:
    """
    Helper function to search substrings in Discord markdown-formatted line, ignoring case and formatting
    """
    return substring.lower() in line.replace('*', '').replace('_', '').replace('|', '').replace('#', '').lower()


async def write_log(msg: str = "Placeholder message", embed: bool = False):
    """
    Logging function.
    If LOG_CHANNEL is valid, send a message there.
    Also write to a log file as backup.
    """
    try:
        log_channel = bot.get_channel(LOG_CHANNEL)
        if embed:
            await send_embed_old(log_channel, msg)
        else:
            await log_channel.send(msg)
    except (discord.InvalidData, discord.HTTPException, discord.Forbidden) as e:
        msg += "\nError fetching log channel: {}".format(e.text)
    except discord.NotFound:
        pass
    
    with open('logs.txt', 'a', encoding='utf-8') as file:
        file.write(datetime.now(timezone.utc).strftime('%m/%d/%y %I:%M %p'))
        file.write('\n')
        file.write(msg)
        file.write('\n=========================================\n')


# Now that everything's defined, run the dang thing
bot.run(TOKEN)
