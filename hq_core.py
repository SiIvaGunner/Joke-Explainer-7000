
import discord
from discord import Message, Thread, TextChannel
from discord.abc import GuildChannel
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta, time

from bot_secrets import YOUTUBE_API_KEY, YOUTUBE_CHANNEL_NAME
from simpleQoC.qoc import CheckResultType, QoCCheckType, QoCCheck, performQoC, msgContainsBitrateFix, msgContainsClippingFix, msgContainsSigninErr, ffmpegExists, getFileMetadataMutagen, getFileMetadataFfprobe
from simpleQoC.metadata import checkMetadata, isDupe

import re
import functools
import typing
from typing import NamedTuple, List, Tuple
from enum import Enum, auto

from contextlib import asynccontextmanager
import asyncio
import re
import math
import random
import traceback

import discord
from discord.abc import GuildChannel

from hq_config import *
from hq_emojis import *

bot = discord.Client(
    intents = discord.Intents.all() # This was a change necessitated by an update to discord.py :/
    # https://stackoverflow.com/questions/71950432/how-to-resolve-the-following-error-in-discord-py-typeerror-init-missing
    # Also had to enable MESSAGE CONENT INTENT https://stackoverflow.com/questions/71553296/commands-dont-run-in-discord-py-2-0-no-errors-but-run-in-discord-py-1-7-3
    # 10/28/22 They changed it again!!! https://stackoverflow.com/questions/73458847/discord-py-error-message-discord-ext-commands-bot-privileged-message-content-i
)

#===============================================#
#               Rip and React Types             #
#===============================================#

class React(NamedTuple):
    id: int
    name: str

class Rip(NamedTuple):
    text: str
    message_id: int
    channel_id: int
    guild_id: int
    message_author_id: int
    message_author_name: str
    reacts: List[React]
    created_at: datetime

class ReactType(Enum):
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
    ANTIMAIL = auto()
    SENDBACK = auto()
    NUMBER = auto()

REVIEW_REACT_LIST = [ReactType.CHECK, ReactType.GOLDCHECK, ReactType.FIX, ReactType.ALERT, ReactType.REJECT]
FIX_REACT_LIST = [ReactType.FIX, ReactType.ALERT]

#===============================================#
#                AndErrors Types                #
#===============================================#

class MessageAndErrors(NamedTuple):
    message: Message | None 
    error_strings: List[str]

class MessagesAndErrors(NamedTuple):
    messages: List[Message]
    error_strings: List[str]

class UserReactDictAndErrors(NamedTuple):
    user_react_dict: dict[React, List[int]]
    error_strings: List[str]

class StringAndErrors(NamedTuple):
    string: str
    error_strings: List[str]

class RipsAndErrors(NamedTuple):
    rips: List[Rip]
    error_strings: List[str]

class BoolAndErrors(NamedTuple):
    result: bool
    error_strings: List[str]

class AuditLogEntriesAndErrors(NamedTuple):
    audit_log_entires: List[discord.AuditLogEntry] 
    error_strings: List[str]

#===============================================#
#                Discord API Calls              #
#===============================================#

async def log_exception(txt: str, error: Exception, error_strings: List[str], full_stack: bool):
    error_text = f'{txt}\n{type(error).__name__}: {error}'
    trace = traceback.format_exc()
    if full_stack:
        trace = "".join(traceback.extract_stack().format())
    print(f"\033[91m {error_text}\n{trace}\033[0m")
    log_channel = bot.get_channel(get_log_channel())
    if log_channel:
        await send(f'**{error_text}**\n```py\n{trace}\n```', log_channel)
    else:
        print("No log channel found.")
    error_strings.append(error_text)

async def discord_fetch_message(message_id: int, channel: TextChannel | Thread) -> MessageAndErrors: 
    message = None
    error_strings: List[str] = [] 
    try:
        message = await channel.fetch_message(message_id)
    except Exception as error:
        await log_exception(f'Discord API call failed to fetch message {message_id}', error, error_strings, True)
    return MessageAndErrors(message, error_strings)

async def discord_get_user_react_data(react_list: List[ReactType], message: Message) -> UserReactDictAndErrors:
    user_react_dict: dict[React, List[int]] = {}
    error_strings: List[str] = [] 
    if len(react_list):
        for reaction in message.reactions:
            react = init_react(reaction)
            if react_is_one(react_list, react.name):
                user_react_dict[react] = []
                # NOTE: (Ahmayk) this is slow! We have to do an api call for each user.
                fetched_user_ids = [] 
                try:
                    fetched_user_ids = [user.id async for user in reaction.users()]
                except Exception as error:
                    await log_exception(f'Discord API call failed to fetch reaction user ids from {reaction}', error, error_strings, True)
                for fetched_user_id in fetched_user_ids:
                    user_react_dict[react].append(fetched_user_id) 
    return UserReactDictAndErrors(user_react_dict, error_strings)

async def discord_get_channel_messages(limit: int | None, channel: TextChannel | Thread) -> MessagesAndErrors: 
    messages = [] 
    error_strings: List[str] = [] 
    try:
        messages = [message async for message in channel.history(limit=limit)]
    except Exception as error:
        await log_exception(f'Discord API call failed to fetch channel messages from {channel.name}', error, error_strings, True)
    return MessagesAndErrors(messages, error_strings) 


async def discord_get_channel_messages_after(message: Message, limit: int | None) -> MessagesAndErrors: 
    messages = [] 
    error_strings: List[str] = [] 
    try:
        messages = [message async for message in message.channel.history(limit=limit, after=message)]
    except Exception as error:
        await log_exception(f'Discord API call failed to fetch channel messages from {message.channel.name} after message {message.jump_url}', error, error_strings, True)
    return MessagesAndErrors(messages, error_strings) 


async def discord_get_channel_pins(limit: int | None, channel: TextChannel | Thread) -> MessagesAndErrors: 
    messages: List[Message] = [] 
    error_strings: List[str] = [] 
    try:
        messages = [message async for message in channel.pins(limit=limit)]
    except Exception as error:
        await log_exception(f'Discord API call failed to fetch channel pins from {channel.name}', error, error_strings, True)
    return MessagesAndErrors(messages, error_strings)


async def discord_cleanup_embeds(limit: int | None, expire_time: float, channel: TextChannel | Thread, \
                                 typing_channel: TextChannel | Thread | None) -> MessagesAndErrors: 
    deleted_messages = [] 
    error_strings: List[str] = [] 

    def should_delete(message: Message):
        result = message.author == bot.user and len(message.embeds) and (message.embeds[0].type == "rich") and (message.embeds[0].title is None)
        if result and expire_time: 
            result = (datetime.now(timezone.utc) - message.created_at) > timedelta(seconds=expire_time)
        return result 

    try:
        async with typing_channel.typing() if typing_channel is not None else empty_async_context():
            deleted_messages = await channel.purge(limit=limit, check=should_delete)
    except Exception as error:
        await log_exception(f'Discord API call failed to delete messages from {channel.name}', error, error_strings, True)

    return MessagesAndErrors(deleted_messages, error_strings)


async def discord_get_audit_log_entries(action_type: discord.AuditLogAction, limit: int | None, guild: discord.Guild) -> AuditLogEntriesAndErrors: 
    audit_log_entries = [] 
    error_strings: List[str] = [] 
    try:
        audit_log_entries = [entry async for entry in guild.audit_logs(limit=limit, action=action_type)]
    except Exception as error:
        await log_exception(f'Discord API call failed to read audit log', error, error_strings, True)
    return AuditLogEntriesAndErrors(audit_log_entries, error_strings) 


async def discord_unpin_message(message: Message) -> List[str]: 
    error_strings: List[str] = [] 
    try:
        await message.unpin()
    except Exception as error:
        await log_exception(f'Discord API call failed to unpin message: {message.id}', error, error_strings, True)
    return error_strings


async def discord_add_reaction(reaction_string: str, message: Message) -> List[str]: 
    error_strings: List[str] = [] 
    try:
        await message.add_reaction(reaction_string)
    except Exception as error:
        await log_exception(f'Discord API call failed to add reaction {reaction_string} to {message.id}', error, error_strings, True)
    return error_strings


async def discord_clear_reaction(reaction_string: str, message: Message) -> List[str]: 
    error_strings: List[str] = [] 
    try:
        await message.clear_reaction(reaction_string)
    except Exception as error:
        await log_exception(f'Discord API call failed to clear reaction {reaction_string} from {message.id}', error, error_strings, True)
    return error_strings


async def discord_edit_message(message: Message, text: str) -> List[str]: 
    error_strings: List[str] = [] 
    try:
        #NOTE: (Ahmayk) if message has attachments, components, or embeds they will disappear
        await message.edit(content=text)
    except Exception as error:
        await log_exception(f'Discord API call failed to edit message: {message.id}', error, error_strings, True)
    return error_strings

#===============================================#
#                    CACHE                      #
#===============================================#

RIP_CACHE: dict[int, dict[int, Rip]] = {}

def init_channel_cache(channel_id: int):
    if channel_id not in RIP_CACHE:
        RIP_CACHE[channel_id] = {}

#NOTE: (Ahmayk) Dummy async context that we can call instead in the case where 
#we do not input a channel for channel.typing()
@asynccontextmanager
async def empty_async_context():
    yield

#NOTE: (Ahmayk) Locking on either a channel or message cache allows us to manage congruent process and commands
## without one reading from or writing into the cache with outdated info
async def _lock(id: int, lock_dict: dict[int, asyncio.Lock], typing_channel: TextChannel | Thread | None):
    if id not in lock_dict:
        lock_dict[id] = asyncio.Lock()

    if lock_dict[id].locked():
        print(f'LOCKED ON id: {id}. Typing channel: {typing_channel}')
        async with typing_channel.typing() if typing_channel is not None else empty_async_context():
            await lock_dict[id].acquire()
            print(f'LOCK AQUIRED ON id: {id}')
    else:
        await lock_dict[id].acquire()

##NOTE: (Ahmayk) This will crash if the lock is not locked!
##This is by design to ensure concurrency
def _unlock(id: int, lock_dict: dict[int, asyncio.Lock]):
    lock_dict[id].release()

CACHE_LOCK_MESSAGE: dict[int, asyncio.Lock] = {}
async def lock_channel(channel_id: int, typing_channel: TextChannel | Thread | None):
    await _lock(channel_id, CACHE_LOCK_CHANNEL, typing_channel)

def unlock_channel(channel_id: int):
    _unlock(channel_id, CACHE_LOCK_CHANNEL)

CACHE_LOCK_CHANNEL: dict[int, asyncio.Lock] = {}
async def lock_message(message_id: int, typing_channel: TextChannel | Thread | None):
    await _lock(message_id, CACHE_LOCK_MESSAGE, typing_channel)

def unlock_message(message_id: int):
    _unlock(message_id, CACHE_LOCK_MESSAGE)

class RipFetchType(Enum):
    PINS = auto()
    ALL_MESSAGES_NO_THREADS = auto()
    ALL_MESSAGES_AND_THREADS = auto()

class ChannelInfo(NamedTuple):
    rip_fetch_type: RipFetchType
    is_cache_qoc: bool

def get_channel_info(channel: TextChannel | Thread) -> ChannelInfo:

    rip_fetch_type = RipFetchType.PINS

    if channel_is_types(channel, ['SUBS']):
        rip_fetch_type = RipFetchType.ALL_MESSAGES_NO_THREADS

    elif channel_is_types(channel, ['QUEUE', 'SUBS_THREAD']):
        rip_fetch_type = RipFetchType.ALL_MESSAGES_AND_THREADS

    is_cache_qoc = channel_is_types(channel, ['QOC'])
    
    return ChannelInfo(rip_fetch_type, is_cache_qoc)

def init_rip(message) -> Rip:

    reacts: List[React] = []

    for reaction in message.reactions:
        react = init_react(reaction)
        for i in range(0, reaction.count):
            reacts.append(react)

    return Rip(message.content, message.id, message.channel.id, message.guild.id, message.author.id, \
            str(message.author), reacts, message.created_at)

def cache_rip_in_message(message: Message):
    rip = init_rip(message)
    init_channel_cache(message.channel.id)
    RIP_CACHE[message.channel.id][message.id] = rip
    return rip

def react_needs_user_cache(react: React) -> bool:
    result = False
    for enum in UserReactCheckType:
        react_list = user_react_check_type_to_react_list(enum)
        if react_is_one(react_list, react.name):
            result = True
            break
    return result

def format_reaction_cache_error(text: str, post_text: str, react: React, message: Message) -> str:
    emoji_string = reaction_name_to_emoji_string(react.name, message.guild)
    return f'\n**{text}**\n{get_rip_title(message.content)} {message.jump_url}{emoji_string}: {post_text}'

def validate_rip_message(message: Message, user_react_data: dict[React, List[int]]) -> str:
    result = ""

    if message.id in RIP_CACHE[message.channel.id]:
        cached_rip = RIP_CACHE[message.channel.id][message.id]
        refetched_rip = init_rip(message)

        if cached_rip.text != refetched_rip.text:
            result += f'\nText outdated in cache in {get_rip_title(refetched_rip.text)}! {message.jump_url}'

        count_dict_cached = get_react_counts(cached_rip)
        count_dict_refetched = get_react_counts(refetched_rip)

        for react in count_dict_refetched.keys():

            if react not in count_dict_cached:
                result += format_reaction_cache_error(f'Reaction missing in cache. (missing entirely)', f'x{count_dict_refetched[react]}', react, message)
            elif count_dict_refetched[react] != count_dict_cached[react]:
                result += format_reaction_cache_error(f'Reaction missing in cache. (counts do not match)', f'~~x{count_dict_cached[react]}~~ x{count_dict_refetched[react]}', react, message)

        for react in count_dict_cached.keys():
            if react not in count_dict_refetched:
                result += format_reaction_cache_error(f'Reaction in cache outdated.', f'~~x{count_dict_cached[react]}~~ x0', react, message)

    else:
        result += f'\nRip missing from cache: {get_rip_title(message.content)} {message.jump_url}'

    channel_info = get_channel_info(message.channel)
    if channel_info.is_cache_qoc:

        if len(user_react_data) and message.id not in USER_REACT_CACHE:
            result += f'\n**User react dict not initialied for ${message.jump_url}**' 
            USER_REACT_CACHE[message.id] = {}

        for react, user_ids in user_react_data.items(): 
            if react not in USER_REACT_CACHE[message.id]:
                result += format_reaction_cache_error(f'User react dict missing in user react cache.', '', react, message)
            else:
                for user_id in user_ids:
                    if user_id not in USER_REACT_CACHE[message.id][react]:
                        result += format_reaction_cache_error(f'User ID dict missing in user react cache.', f'({user_id})', react, message)

        if message.id in USER_REACT_CACHE:
            for react, user_ids in USER_REACT_CACHE[message.id].copy().items():
                if len(user_ids) and react_needs_user_cache(react):
                    if react not in user_react_data:
                        result += format_reaction_cache_error(f'User react data outdated in user react cache.', f'{user_ids}', react, message)
                    else:
                        for user_id in user_ids:
                            if user_id not in user_react_data[react]:
                                result += format_reaction_cache_error(f'User ID outdated in user react cache', f'({user_id})', react, message)

    return result


def cache_user_react_data(user_react_data: dict[React, List[int]], message_id: int):
    if len(user_react_data):
        if message_id not in USER_REACT_CACHE:
            USER_REACT_CACHE[message_id] = {} 
        for react, user_ids in user_react_data.items():
            USER_REACT_CACHE[message_id][react] = []
            for user_id in user_ids:
                USER_REACT_CACHE[message_id][react].append(user_id) 

async def process_rip_message(message: Message, refetch_message: bool, is_validate_message: bool, \
                              typing_channel: TextChannel | Thread | None) -> StringAndErrors:
    return_message = ""
    error_strings = [] 
    if is_message_rip(message):
        await lock_message(message.id, typing_channel)

        if refetch_message:
            message_and_errors = await discord_fetch_message(message.id, message.channel)
            if message_and_errors.message:
                message = message_and_errors.message
            error_strings.extend(message_and_errors.error_strings)

        user_react_data: dict[React, List[int]] = {}
        channel_info = get_channel_info(message.channel)
        if channel_info.is_cache_qoc:
            react_list = []
            for enum in UserReactCheckType:
                reacts_of_check_type = user_react_check_type_to_react_list(enum)
                for react in reacts_of_check_type:
                    if react not in react_list:
                        react_list.append(react)
            
            user_react_data_and_errors = await discord_get_user_react_data(react_list, message)
            user_react_data = user_react_data_and_errors.user_react_dict
            error_strings.extend(user_react_data_and_errors.error_strings)

        if is_validate_message:
            return_message = validate_rip_message(message, user_react_data)

        cache_rip_in_message(message)
        if channel_info.is_cache_qoc:
            cache_user_react_data(user_react_data, message.id)

        channel_info = get_channel_info(message.channel)

        unlock_message(message.id)
    return StringAndErrors(return_message, error_strings) 

async def process_rip_channel(channel: TextChannel | Thread, is_validate_message: bool, typing_channel: TextChannel | Thread | None) -> StringAndErrors:
    return_message = ""
    error_strings = [] 
    channel_info = get_channel_info(channel)
    match channel_info.rip_fetch_type: 
        case RipFetchType.ALL_MESSAGES_NO_THREADS:
            messages_and_error = await discord_get_channel_messages(None, channel)
            error_strings.extend(messages_and_error.error_strings)
            for message in messages_and_error.messages:
                string_and_errors = await process_rip_message(message, False, is_validate_message, typing_channel)
                return_message += string_and_errors.string
                error_strings.extend(string_and_errors.error_strings)

        case RipFetchType.ALL_MESSAGES_AND_THREADS:
            messages_and_error = await discord_get_channel_messages(None, channel)
            error_strings.extend(messages_and_error.error_strings)
            for message in messages_and_error.messages: 
                if message.thread is not None:
                    init_channel_cache(message.thread.id)
                    await lock_channel(message.thread.id, typing_channel)
                    string_and_errors = await process_rip_channel(message.thread, False, typing_channel)
                    return_message += string_and_errors.string
                    error_strings.extend(string_and_errors.error_strings)
                    if len(RIP_CACHE[message.thread.id]):
                        thread_rips = list(RIP_CACHE[message.thread.id].values())
                        thread_rips.sort(key = lambda rip: rip.created_at, reverse=True)
                        #NOTE: (Ahmayk) we insert thread rips also in its parent channel dict so that
                        #we get all rips in theads when we get the parent channel's rips 
                        for thread_rip in thread_rips:
                            await lock_message(thread_rip.message_id, None)
                            RIP_CACHE[channel.id][thread_rip.message_id] = thread_rip 
                            unlock_message(thread_rip.message_id)
                    unlock_channel(message.thread.id)
                else:
                    string_and_errors = await process_rip_message(message, False, is_validate_message, typing_channel)
                    return_message += string_and_errors.string
                    error_strings.extend(string_and_errors.error_strings)

        case RipFetchType.PINS:
            messages_and_errors = await discord_get_channel_pins(None, channel)
            error_strings.extend(messages_and_errors.error_strings)
            for message in messages_and_errors.messages:
                string_and_errors = await process_rip_message(message, True, is_validate_message, typing_channel)
                return_message += string_and_errors.string
                error_strings.extend(string_and_errors.error_strings)

        case _:
            assert "Unimplemented RipFetchType"

    return StringAndErrors(return_message, error_strings) 

class GetRipsDesc(NamedTuple):
    typing_channel: TextChannel | Thread | None = None
    rebuild_cache: bool = False

async def get_rips(channel: TextChannel | Thread, desc: GetRipsDesc) -> RipsAndErrors: 

    error_strings = [] 

    init_channel_cache(channel.id)

    ##NOTE: (Ahmayk) We need to prevent other processes from accessing the cache 
    ## in the case where we are updating the cache. If we allow access to the cache while
    ## it is being updated, it would likely be incomplete or wrong! 
    await lock_channel(channel.id, desc.typing_channel)

    if not len(RIP_CACHE[channel.id]) or desc.rebuild_cache:
        async with desc.typing_channel.typing() if desc.typing_channel is not None else empty_async_context():
            string_and_errors = await process_rip_channel(channel, False, desc.typing_channel)
            error_strings.extend(string_and_errors.error_strings)

    rips = list(RIP_CACHE[channel.id].values())

    unlock_channel(channel.id)

    ##NOTE: (Ahmayk) show rips in expected order, newest at top
    rips.sort(key = lambda rip: rip.created_at, reverse=True)
    return RipsAndErrors(rips, error_strings)


async def get_rips_fast(channel: TextChannel | Thread, desc: GetRipsDesc) -> RipsAndErrors: 

    init_channel_cache(channel.id)
    channel_info = get_channel_info(channel)

    rips = []
    error_strings = []

    if channel_info.rip_fetch_type == RipFetchType.ALL_MESSAGES_AND_THREADS or \
       channel_info.rip_fetch_type == RipFetchType.ALL_MESSAGES_NO_THREADS:

        #NOTE: (Ahmayk) The path to get rips normally is already as fast
        #as we can get so just do that
        rips_and_errors = await get_rips(channel, desc)
        rips = rips_and_errors.rips
        error_strings = rips_and_errors.error_strings

    else:
        if len(RIP_CACHE[channel.id]) and (not channel.id in CACHE_LOCK_CHANNEL or not CACHE_LOCK_CHANNEL[channel.id].locked()):
            rips_and_errors = await get_rips(channel, desc)
            rips = rips_and_errors.rips
            error_strings = rips_and_errors.error_strings
        else:
            async with desc.typing_channel.typing() if desc.typing_channel is not None else empty_async_context():
                messages_and_errors = await discord_get_channel_pins(None, channel)
                error_strings.extend(messages_and_errors.error_strings)
                for message in messages_and_errors.messages:
                    if is_message_rip(message):
                        ##NOTE: (Ahmayk) No fetching message for reactions for speed!
                        rip = init_rip(message)
                        rips.append(rip)
                rips.sort(key = lambda rip: rip.created_at, reverse=True)

    return RipsAndErrors(rips, error_strings)

def init_react(reaction: discord.Reaction) -> React:
    name = ""
    id = 0 
    if isinstance(reaction.emoji, str):
        name = reaction.emoji
    elif isinstance(reaction.emoji, discord.Emoji) or isinstance(reaction.emoji, discord.PartialEmoji):
        name = reaction.emoji.name
        if reaction.emoji.id:
            id = reaction.emoji.id
    else:
        assert False, "Unrecognized reaction type" # This shouldn't happen
    return React(id, name) 

def get_react_counts(rip: Rip) -> dict[React, int]:
    count_dict: dict[React, int] = {}
    for react in rip.reacts:
        if react not in count_dict:
            count_dict[react] = 0
        count_dict[react] += 1
    return count_dict

async def validate_cache_all() -> StringAndErrors:
    return_string = ""
    error_strings = []
    for channel_id in get_channel_ids_of_types(['QOC', 'SUBS', 'SUBS_PIN', 'SUBS_THREAD', 'QUEUE']):
        channel = bot.get_channel(channel_id)
        if channel:
            string_and_errors = await process_rip_channel(channel, True, None)
            return_string = string_and_errors.string
            error_strings = string_and_errors.error_strings
        else:
            error_strings.append(f'Failed to find channel of id {channel_id}')
    return StringAndErrors(return_string, error_strings) 


async def rebuild_cache_for_channel(channel_id: int) -> StringAndErrors:
    return_message = ""
    error_strings = [] 

    channel = bot.get_channel(channel_id)
    if channel:
        rips_and_errors = await get_rips(channel, GetRipsDesc(rebuild_cache=True))
        error_strings.extend(rips_and_errors.error_strings)

        channel_type_string = '[Unknown type]'
        if channel_is_types(channel, ['QOC']):
            channel_type_string = 'qoc'
        elif channel_is_types(channel, ['QUEUE']):
            channel_type_string = 'queued'
        elif channel_is_types(channel, ['SUBS', 'SUBS_THREAD', 'SUBS_PIN']):
            channel_type_string = 'subbed'
        return_message = f'Cached {len(rips_and_errors.rips)} {channel_type_string} rips in {channel.jump_url}.'

        await write_log(return_message)
    else:
        error_strings.append(f'Error caching channel: Failed to find channel: <#{channel_id}>')
        await write_log(return_message)

    return StringAndErrors(return_message, error_strings) 

async def rebuild_cache_all() -> StringAndErrors:

    return_message = ""
    error_strings = []

    channel_ids_qoc = get_channel_ids_of_types(['QOC'])
    channel_ids_all = get_channel_ids_of_types(['QOC', 'SUBS', 'SUBS_PIN', 'SUBS_THREAD', 'QUEUE'])
    for channel_id in channel_ids_all:
        if channel_id not in channel_ids_qoc:
            string_and_errors = await rebuild_cache_for_channel(channel_id)
            return_message += f'\n{string_and_errors.string}'
            error_strings.extend(string_and_errors.error_strings)

    for channel_id in channel_ids_qoc:
        string_and_errors = await rebuild_cache_for_channel(channel_id)
        return_message += f'\n{string_and_errors.string}'
        error_strings.extend(string_and_errors.error_strings)

    return StringAndErrors(return_message, error_strings) 


async def remove_rip_from_cache(message_id: int, channel_id: int):
    ##NOTE: (Ahmayk) if something is processing on the message, we want to wait for that to finish
    ##so that we can then remove whatever was being processed
    await lock_message(message_id, None)
    if channel_id in RIP_CACHE and message_id in RIP_CACHE[channel_id]: 
        RIP_CACHE[channel_id].pop(message_id)
    if message_id in USER_REACT_CACHE:
        USER_REACT_CACHE.pop(message_id)
    unlock_message(message_id)
    CACHE_LOCK_MESSAGE.pop(message_id)

#===============================================#
#                    REACTS
#===============================================#

KEYCAP_EMOJIS = {'2️⃣': 2, '3️⃣': 3, '4️⃣': 4, '5️⃣': 5, '6️⃣': 6, '7️⃣': 7, '8️⃣': 8, '9️⃣': 9, '🔟': 10}

def react_is(reaction_type: ReactType, name: str) -> bool:
    result = False
    name_lower = name.lower()
    match (reaction_type):
        case ReactType.GOLDCHECK:
            result = name_lower == "goldcheck" or name_lower == DEFAULT_GOLDCHECK
        case ReactType.CHECKREQ:
            result = name_lower.endswith("check") and name_lower[0].isdigit()
        case ReactType.CHECK:
            if not react_is(ReactType.GOLDCHECK, name) and not react_is(ReactType.CHECKREQ, name):
                result = name_lower == "check" or name_lower == DEFAULT_CHECK
        case ReactType.FIX:
            result = name_lower == "fix" or name_lower == "wrench" or name_lower == DEFAULT_FIX
        case ReactType.REJECT:
            result = name_lower == "reject" or name_lower == DEFAULT_REJECT
        case ReactType.STOP:
            result = name_lower == "stop" or name_lower == "octagonal" or name_lower == DEFAULT_STOP
        case ReactType.ALERT:
            result = name_lower == "alert" or name_lower == DEFAULT_ALERT
        case ReactType.QOC:
            result = name_lower == "qoc" or name_lower == DEFAULT_QOC
        case ReactType.METADATA:
            result = name_lower == "metadata" or name_lower == DEFAULT_METADATA
        case ReactType.THUMBNAIL:
            result = name_lower == "thumbnail" or name_lower == DEFAULT_THUMBNAIL
        case ReactType.EMAILSENT:
            result = name_lower == "emailsent"
        case ReactType.ANTIMAIL:
            result = name_lower == "antimail"
        case ReactType.SENDBACK:
            result = name_lower == "sendback" or name_lower == DEFAULT_SENDBACK
        case ReactType.NUMBER:
            result = name in KEYCAP_EMOJIS
        case _:
            assert "Unimplemented ReactionType"

    return result

def react_is_one(reaction_type_list: List[ReactType], name: str) -> bool:
    for reaction_type in reaction_type_list:
        if react_is(reaction_type, name):
            return True
    return False

def rip_has_react(reaction_type_list: List[ReactType], rip: Rip):
    for react in rip.reacts:
        if react_is_one(reaction_type_list, react.name):
            return True
    return False

USER_REACT_CACHE: dict[int, dict[React, List[int]]] = {}

#NOTE: (Ahmayk) This is an enum so we can iterate 
# all possible user react checks for caching on startup and validation
class UserReactCheckType(Enum):
    REVIEW = auto()
    FIX = auto()
    CHECK = auto()
    REJECT = auto()

def user_react_check_type_to_react_list(user_react_check_type: UserReactCheckType) -> List[ReactType]:
    react_list = []
    match(user_react_check_type):
        case UserReactCheckType.REVIEW: 
            react_list = REVIEW_REACT_LIST 
        case UserReactCheckType.FIX:
            react_list = FIX_REACT_LIST 
        case UserReactCheckType.CHECK:
            react_list = [ReactType.CHECK] 
        case UserReactCheckType.REJECT:
            react_list = [ReactType.REJECT] 
        case _:
            assert f'Unimplemented UserReactCheckType {user_react_check_type}'
    return react_list

async def user_is_react(user_react_check_type: UserReactCheckType, user_id: int, rip: Rip,\
                        typing_channel: TextChannel | Thread | None) -> BoolAndErrors:
    result = False
    error_strings = []

    await lock_message(rip.message_id, typing_channel)

    if rip.message_id not in USER_REACT_CACHE:
        USER_REACT_CACHE[rip.message_id] = {} 

    react_list = user_react_check_type_to_react_list(user_react_check_type)

    fetch_user_ids = False 
    for react in rip.reacts:
        if react_is_one(react_list, react.name) and react not in USER_REACT_CACHE[rip.message_id]:
            fetch_user_ids = True
            break

    if fetch_user_ids: 
        channel = bot.get_channel(rip.channel_id)
        if channel:
            # NOTE: (Ahmayk) this message fetch isn't actually neccessary, but we need the
            # message react object to call reaction.users().
            # As far as I can tell this is the only way discord.py exposes this API call.
            # An optimization here would be to figure out how to do this without the reaction object,
            # perhaps bypassing discord.py to make the direct api call we need
            # In pratice we shouldn't be calling this code path too often anyway during regular usage
            # So isn't the biggest problem
            message_and_errors = await discord_fetch_message(rip.message_id, channel)
            error_strings.extend(message_and_errors.error_strings)
            if message_and_errors.message:
                user_react_data_and_errors = await discord_get_user_react_data(react_list, message_and_errors.message)
                error_strings.extend(user_react_data_and_errors.error_strings)
                cache_user_react_data(user_react_data_and_errors.user_react_dict, message_and_errors.message.id)

    for react in rip.reacts:
        if react_is_one(react_list, react.name):
            # NOTE: (Ahmayk) if the react isn't in the cache then we programmed something wrong
            assert react in USER_REACT_CACHE[rip.message_id]
            if user_id in USER_REACT_CACHE[rip.message_id][react]:
                result = True
                break


    unlock_message(rip.message_id)

    return BoolAndErrors(result, error_strings)


def reaction_name_to_emoji_string(name: str, guild: discord.Guild | None) -> str:
    result = f'{name}' 
    if guild:
        for emoji in guild.emojis:
            if emoji.name == name:
                result = str(emoji)
                break
    return result

def get_qoc_emoji(guild: discord.Guild) -> str:
    qoc_emote = DEFAULT_QOC
    if guild:
        for e in guild.emojis:
            if e.name.lower() == "qoc":
                qoc_emote = str(e)
                break
    return qoc_emote

def parse_emojis_in_string(string: str, guild: discord.Guild):

    def emoji_match_filter(match):
        name = match.group(1)
        result = f':{name}:' 
        for emoji in guild.emojis:
            if emoji.name == name:
                result = str(emoji)
                break
        return result

    result = re.sub(r':(\w+):', emoji_match_filter, string) 

    def config_match_filter(match):
        result = get_config(match.group(1))
        return str(result)

    result = re.sub(r'%(\w+)%', config_match_filter, result) 

    return result

#===============================================#
#                   COMMANDS 
#===============================================#

class CommandType(Enum):
    NULL = auto()
    QOC = auto()
    SUBS = auto()
    QUEUE = auto()
    STATS = auto()
    ANALYZE = auto()
    SECRET = auto()
    MANAGEMENT = auto()

class CommandTypeData(NamedTuple):
    desc: str

COMMAND_TYPE_DATA = {}
COMMAND_TYPE_DATA[CommandType.QOC] = CommandTypeData('get info on pinned rips in a QoC channel')
COMMAND_TYPE_DATA[CommandType.SUBS] = CommandTypeData('get info on rips in submission channels')
COMMAND_TYPE_DATA[CommandType.QUEUE] = CommandTypeData('get info on rips in approved queues')
COMMAND_TYPE_DATA[CommandType.STATS] = CommandTypeData('get miscellaneous info on all rips')
COMMAND_TYPE_DATA[CommandType.ANALYZE] = CommandTypeData('analyze rip metadata or audio for common issues')
COMMAND_TYPE_DATA[CommandType.MANAGEMENT] = CommandTypeData('manage or learn about the bot')

class CommandContext(NamedTuple):
    channel: TextChannel | Thread 
    user: discord.User 
    message_reference: discord.MessageReference

class CommandInfo(NamedTuple):
    name: str
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
            name=func.__name__,
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


def find_command_info(input: str) -> CommandInfo | None:
    command_info = None
    if input in COMMANDS:
        command_info = COMMANDS[input]
    else:
        for info in COMMANDS.values():
            if input in info.aliases:
                command_info = info
                break
    return command_info 

#===============================================#
#                  RIP VETTING                  #
#===============================================#

class VetRipDesc(NamedTuple):
    rip: Rip | None = None
    message: Message | None = None
    use_youtube_api: bool = False
    past_rip_message_content: str = ""

class VetRipResult(NamedTuple):
    qoced_url: str
    qoc_checks_dict: dict[QoCCheckType, QoCCheck]
    metadata_checks: list[QoCCheck] 
    rip_message_text: str
    rip_message_link: str
    everything_passed: bool
    link_error: bool
    past_rip_message_content: str
    past_vet_message: Message | None 
    message_editor_name: str
    error_strings: list[str]

async def vet_rip_or_url(rip_text_or_url: str, desc: VetRipDesc) -> VetRipResult:

    rip_message_text = ""
    urls = [] 

    if '```' in rip_text_or_url:
        rip_message_text = rip_text_or_url
        urls = extract_rip_link(rip_text_or_url)
    
    if not len(urls) and desc.rip:
        rip_message_text = desc.rip.text
        urls = extract_rip_link(desc.rip.text)

    if not len(urls) and desc.message:
        rip_message_text = desc.message.content
        urls = extract_rip_link(desc.message.content)
        #TODO: (Ahmayk) extract link from message attachment

    if not len(urls):
        urls = [rip_text_or_url]

    qoc_checks_dict = {} 
    qoced_url = "" 
    for url in urls:
        qoc_checks_dict = await run_blocking(performQoC, url)
        if QoCCheckType.LINK in qoc_checks_dict and qoc_checks_dict[QoCCheckType.LINK].result != CheckResultType.ERROR:
            qoced_url = url
            break

    link_error = QoCCheckType.LINK in qoc_checks_dict and \
        qoc_checks_dict[QoCCheckType.LINK].result == CheckResultType.ERROR

    is_qoc_pass_all = len(qoc_checks_dict) > 0
    for qoc_check in qoc_checks_dict.values():
        if qoc_check.result != CheckResultType.PASS:
            is_qoc_pass_all = False
            break

    error_strings = []
    metadata_checks: List[QoCCheck] = []
    rip_message_link = "" 
    message_editor_name = ""
    if len(rip_message_text):
        description = get_rip_description(rip_message_text)
        is_unusual_metadata = "unusual metadata" in rip_message_text.lower()
        if not is_unusual_metadata and len(description) > 0:
            api_key = "" 
            if desc.use_youtube_api:
                api_key = YOUTUBE_API_KEY 
            playlistId = extract_playlist_id('\n'.join(rip_message_text.splitlines()[1:])) # ignore author line
            advancedCheck = get_config('metadata')
            metadata_checks = await run_blocking(checkMetadata, description, YOUTUBE_CHANNEL_NAME, playlistId, api_key, advancedCheck)

        message_id = 0
        vetted_message_link = ""
        message_author_name = ""
        if desc.rip:
            vetted_message_link = format_message_link(desc.rip.guild_id, desc.rip.channel_id, desc.rip.message_id)
            message_author_name = desc.rip.message_author_name
            message_id = desc.rip.message_id
        if desc.message:
            vetted_message_link = desc.message.jump_url
            message_author_name = str(desc.message.author)
            message_editor_name = message_author_name
            message_id = desc.message.id

        if len(vetted_message_link):
            rip_message_link = f'[{get_rip_title(rip_message_text)}]({vetted_message_link})' 

        if not len(metadata_checks) and "[Unusual Pin Format]" in get_rip_author(rip_message_text, message_author_name):
            metadata_checks.append(QoCCheck(CheckResultType.FAIL, "Rip author is missing."))

        if not is_unusual_metadata:
            rips = []
            channel_ids = get_channel_ids_of_types(['QUEUE', 'QOC'])
            for channel_id in channel_ids:
                channel = bot.get_channel(channel_id)
                if channel:
                    rips_and_errors = await get_rips_fast(channel, GetRipsDesc())
                    rips = rips_and_errors.rips
                    error_strings.extend(rips_and_errors.error_strings)

            title = get_raw_rip_title(rip_message_text)
            for rip in rips:
                if rip.message_id != message_id:
                    rip_title = get_raw_rip_title(rip.text)
                    if title == rip_title:
                        link = format_message_link(channel.guild.id, channel.id, rip.message_id)
                        metadata_checks.append(QoCCheck(CheckResultType.FAIL, f"Video title already exists in <#{rip.channel_id}>: [{rip_title}]({link})."))
                    if isDupe(description, get_rip_description(rip.text), True):
                        link = format_message_link(channel.guild.id, channel.id, rip.message_id)
                        metadata_checks.append(QoCCheck(CheckResultType.FAIL, f"Main mix detected in <#{rip.channel_id}>: [{rip_title}]({link}). Add something on the author line to avoid uploading this early."))

            # Check for lines between the rip description and link - if it does not start with "Joke", add a warning
            # in order to minimize accidental joke lines when uploading
            if len(qoced_url):
                try:
                    for line in rip_message_text.split('```', 2)[2].splitlines():
                        line = "".join(c for c in line if c.isprintable())
                        if qoced_url in line:
                            break
                        elif len(line) > 0 and not line.startswith('Joke') and not line == '||':
                            metadata_checks.append(QoCCheck(CheckResultType.FAIL, "Line not starting with ``Joke`` detected between description and rip URL. Recommend putting the URL directly under description to avoid accidentally uploading joke lines."))
                            break
                except IndexError:
                    pass

    everything_passed = is_qoc_pass_all and not len(metadata_checks)

    past_vet_message = None 
    if desc.message and channel_is_types(desc.message.channel, ['QOC']):
        if link_error:
            errors = await discord_add_reaction(QOC_DEFAULT_LINKERR, desc.message)
            error_strings.extend(errors)
        elif message_has_react(QOC_DEFAULT_LINKERR, desc.message):
            errors = await discord_clear_reaction(QOC_DEFAULT_LINKERR, desc.message)
            error_strings.extend(errors)

        after_messages_and_errors = await discord_get_channel_messages_after(desc.message, 15)
        error_strings.extend(after_messages_and_errors.error_strings)
        assert bot.user
        for message in after_messages_and_errors.messages:
            if message.author.id == bot.user.id and desc.message.jump_url in message.content: 
                past_vet_message = message
                break

    vet_rip_result = VetRipResult(qoced_url, qoc_checks_dict, metadata_checks, rip_message_text, \
                                  rip_message_link, everything_passed, link_error, \
                                  desc.past_rip_message_content, past_vet_message, \
                                  message_editor_name, error_strings) 

    if past_vet_message:
        format_desc_past = FormatVetRipResultDesc(is_new_pinned_message=True)
        past_text = format_vet_rip_result(format_desc_past, vet_rip_result)
        errors = await discord_edit_message(past_vet_message, past_text)
        error_strings.extend(errors)

    return vet_rip_result

REACT_CHECK_TO_IGNORE_STRING = f'-# React {DEFAULT_CHECK} to mark all as ignored.'

class FormatVetRipResultDesc(NamedTuple):
    full_feedback: bool = False
    is_new_pinned_message: bool = False
    smol_update: bool = False

def format_vet_rip_result(desc: FormatVetRipResultDesc, vet_rip_result: VetRipResult) -> str:

    return_message = ""
    if desc.full_feedback or desc.smol_update or not vet_rip_result.everything_passed or vet_rip_result.past_vet_message:

        intro_warnings = [] 
        if not len(vet_rip_result.qoced_url) and not vet_rip_result.link_error:
            intro_warnings.append(":warning: No rip links detected.")

        verdict_emojis: List[str] = [] 

        if vet_rip_result.link_error: 
            verdict_emojis.append(QOC_DEFAULT_LINKERR)
            intro_warnings.append(":warning: **Rip link not Auto-QoCed**")

        issue_list = []
        for qoc_check_type, qoc_check in vet_rip_result.qoc_checks_dict.items():
            if len(qoc_check.msg) and (desc.full_feedback or qoc_check.result != CheckResultType.PASS):
                issue_list.append(qoc_check.msg)

            if qoc_check.result == CheckResultType.FAIL and DEFAULT_FIX not in verdict_emojis:
                verdict_emojis.append(DEFAULT_FIX)
                if qoc_check_type == QoCCheckType.BITRATE:
                    verdict_emojis.append(QOC_DEFAULT_BITRATE)
                if qoc_check_type == QoCCheckType.CLIPPING:
                    verdict_emojis.append(QOC_DEFAULT_CLIPPING)

            if qoc_check.result == CheckResultType.ERROR and DEFAULT_ERROR not in verdict_emojis:
                verdict_emojis += DEFAULT_ERROR

        if len(vet_rip_result.metadata_checks):
            verdict_emojis.append(DEFAULT_METADATA)
            for qoc_check in vet_rip_result.metadata_checks:
                if len(qoc_check.msg):
                    issue_list.append(qoc_check.msg)
        elif desc.full_feedback:
            issue_list.append('Metadata is OK.')

        if vet_rip_result.everything_passed:
            verdict_emojis.append(DEFAULT_CHECK)

        return_header = "" 
        if len(vet_rip_result.rip_message_link):
            return_header_title = "Rip"
            if vet_rip_result.past_rip_message_content:

                old_rip_links = extract_rip_link(vet_rip_result.past_rip_message_content)
                old_rip_link = ""
                if len(old_rip_links):
                    old_rip_link = old_rip_links[0]
                is_link_updated = vet_rip_result.qoced_url and old_rip_link != vet_rip_result.qoced_url

                #NOTE: (Ahmayk) assumes that "metadata" is everything before the audio rip link
                linkless_metadata_old = vet_rip_result.past_rip_message_content 
                if len(old_rip_link):
                    linkless_metadata_old = vet_rip_result.past_rip_message_content.split(old_rip_link)[0]
                linkless_metadata_new = vet_rip_result.rip_message_text
                if len(vet_rip_result.qoced_url):
                    linkless_metadata_new = vet_rip_result.rip_message_text.split(vet_rip_result.qoced_url)[0]
                is_metadata_updated = linkless_metadata_old != linkless_metadata_new 

                if is_link_updated and is_metadata_updated:
                    return_header_title = f'{QOC_DEFAULT_LINKERR}{DEFAULT_METADATA} Link and Metadata Updated'
                elif is_link_updated:
                    return_header_title = f'{QOC_DEFAULT_LINKERR} Link Updated'
                elif is_metadata_updated:
                    return_header_title = f'{DEFAULT_METADATA} Metadata Updated'
                else:
                    return_header_title = f'Message Updated'

            return_header = f'**{return_header_title}: {vet_rip_result.rip_message_link}**'

        crossed_out_lines: List[str] = []
        new_lines: List[str] = []

        if vet_rip_result.past_vet_message:
            old_lines = vet_rip_result.past_vet_message.content.splitlines()
            ignored_issues_from_result: List[str] = []
            for old_line in old_lines:

                reparsed_line = ""
                is_crossed_out = False
                is_new_line = False

                matching_issue_string = ""
                match_found = False
                for issue_string in issue_list:
                    if issue_string in old_line:
                        matching_issue_string = issue_string
                        match_found = True

                is_ignored = old_line.startswith('-# - (Marked as ignored by ')
                is_fixed = old_line.startswith('-# - (Fixed')

                #NOTE: (Ahmayk) if a previously current issue doesn't exist anymore, assume it's fixed!
                change_to_fixed = not is_ignored and not is_fixed and \
                    old_line.startswith('- ') and not len(matching_issue_string)

                #NOTE: (Ahmayk) filter out ignored issues.
                # if the ignored issue doesn't exist anymore, convert it to fixed
                if is_ignored: 
                    if match_found:
                        ignored_issues_from_result.append(matching_issue_string)
                    else:
                        matches = re.findall(r'~~(.+)~~', old_line)
                        if len(matches):
                            old_line = matches[0]
                        change_to_fixed = True

                if is_ignored or is_fixed:
                    matches = re.findall(r'^-# - (.+)', old_line)
                    if len(matches):
                        old_line = matches[0]
                else:
                    matches = re.findall(r'^- (.+)', old_line)
                    if len(matches):
                        old_line = matches[0]

                if change_to_fixed:
                    is_crossed_out = True
                    is_new_line = True
                    reparsed_line = f'(Fixed) ~~{old_line}~~'
                    if len(vet_rip_result.message_editor_name):
                        reparsed_line = f'(Fixed by {vet_rip_result.message_editor_name}) ~~{old_line}~~'
                elif is_ignored or is_fixed:
                    is_crossed_out = True
                    reparsed_line = old_line

                if len(reparsed_line):
                    if is_crossed_out:
                        crossed_out_lines.append(reparsed_line)
                    if is_new_line:
                        new_lines.append(reparsed_line)

            for issue_string in issue_list:
                is_matched = False 
                for old_line in old_lines:
                    if issue_string == old_line:
                        is_matched = True
                        break
                if not is_matched:
                    new_lines.append(issue_string)

            for ignored in ignored_issues_from_result:
                assert ignored in issue_list
                issue_list.remove(ignored)
            
        else:
            new_lines = issue_list

        if desc.smol_update:
            return_message = f'-# {return_header} {" ".join(verdict_emojis)}'
            if len(new_lines):
                return_message += "\n-# - " + "\n-# - ".join(new_lines)
        else:
            intro_warnings_string = "\n".join(intro_warnings)
            return_message = f'{intro_warnings_string}\n{return_header}\n**Verdict**: {" ".join(verdict_emojis)}'
            if len(crossed_out_lines):
                return_message += "\n-# - " + "\n-# - ".join(crossed_out_lines)
            if len(issue_list):
                return_message += "\n- " + "\n- ".join(issue_list)
            if not vet_rip_result.everything_passed and desc.is_new_pinned_message and len(issue_list):
                return_message += f'\n{REACT_CHECK_TO_IGNORE_STRING}'

    return return_message


#===============================================#
#                    HELPERS                    #
#===============================================#

def channel_is_type(channel: typing.Union[GuildChannel, Thread], type: str) -> bool:
    return type in get_channel_config(channel.id).types or hasattr(channel, "parent") and channel_is_type(channel.parent, type)

def channel_is_types(channel: typing.Union[GuildChannel, Thread], types: typing.List[str]) -> bool:
    return any([t in get_channel_config(channel.id).types for t in types]) or hasattr(channel, "parent") and channel_is_types(channel.parent, types)

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

    #TODO: (Ahmayk) handle server being null
    channel = server.get_channel_or_thread(channel_id)

    #TODO: (Ahmayk) handle message being null
    #TODO: (Ahmayk) handle error 
    message_and_errors = await discord_fetch_message(msg_id, channel)

    return server, channel, message_and_errors.message, ""


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
        default_id = get_channel_ids_all()[0]
    except IndexError:
        return -1, "Error: No default channels found."
    
    if link is None or not len(link):
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
        return -1, "Error: Link is not a valid roundup channel."


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


def split_long_message(a_message: str, character_limit) -> list[str]:  # avoid Discord's character limit
    """
    Split a long message to fit Discord's character limit.
    Aug 6 2025: apparently embeds have a higher character limit?
    """
    result: List[str] = []
    #TODO: (Ahmayk) While unlikely, this could split things into a group that is bigger than 2000.
    #we're not accounting for that currently.
    all_lines = a_message.splitlines()
    wall_of_text = ""
    is_in_regular_codeblock = False
    is_in_python_codeblock = False
    for i, line in enumerate(all_lines):
        line = line.replace('@', '')  # no more pings lol

        if "```py" in line:
            is_in_python_codeblock = True
        elif "```" in line:
            if is_in_python_codeblock:
                is_in_python_codeblock = False
            else:
                is_in_regular_codeblock = not is_in_regular_codeblock

        need_new_block = False 
        if len(wall_of_text) + len(line) > character_limit:
            need_new_block = True 

        if is_in_python_codeblock or is_in_regular_codeblock:
            if len(wall_of_text) + len(line) + len("\n```") > character_limit:
                need_new_block = True 
            if i != len(all_lines) - 1:
                len_current_and_next = len(wall_of_text) + len(line) + len(all_lines[i + 1])
                if len_current_and_next <= character_limit and \
                   len_current_and_next + len("\n```") > character_limit:
                    need_new_block = True 

        if need_new_block: 
            new_block = wall_of_text[:-1]
            wall_of_text = "" 
            if is_in_python_codeblock:
                new_block += '\n```' 
                wall_of_text = "```py\n" 
            if is_in_regular_codeblock:
                new_block += '\n```' 
                wall_of_text = "```" 
            result.append(new_block)

        wall_of_text += line + '\n'

    result.append(wall_of_text[:-1])  # add anything remaining
    return result

async def send(text: str, channel: TextChannel | Thread, delete_after: int = 0):
    limit = get_config("character_limit")
    split_message = split_long_message(text, limit)
    for line in split_message:
        if len(line):
            try:
                if delete_after:
                    await channel.send(line, delete_after=delete_after)
                else:
                    await channel.send(line)
            except Exception as error:
                #NOTE: (Ahmayk) write_log here could cause infinite error loops so just stay quiet about this one 
                txt = f'Failed to send message to {channel.jump_url}: {type(error).__name__}: {error}'
                print(f"\033[91m {txt}\033[0m")

class EmbedDesc(NamedTuple):
    expires: bool = False
    title: str = ""
    footer: str = ""
    seperator: str = "\n"

async def send_embed(text: str, channel: TextChannel | Thread, desc: EmbedDesc):
    color = get_config('embed_color')
    delete_after_seconds = None
    if desc.expires:
        if channel_is_types(channel, ['PROXY_QOC']):
            delete_after_seconds = get_config('proxy_embed_seconds')
        else: 
            delete_after_seconds = get_config('embed_seconds')

    embed_character_limit_title = get_config("embed_character_limit_title")
    title = desc.title
    if len(title) > embed_character_limit_title:
        title = title[:embed_character_limit_title]

    embed_character_limit_footer = get_config("embed_character_limit_footer")
    footer = desc.footer
    if len(footer) > embed_character_limit_footer:
        footer = footer[:embed_character_limit_footer]

    ##NOTE: (Ahmayk) we want to send the least amount of messages for speed
    ##since sending each message in sequence takes time
    ##As of writing, embed descs can hold 4096 characters
    ##however messages in total can only hold 6000 characters
    ##so in order to send the least amount of messages possible,
    ##we send the most characters we can in each message
    ##while also staying under the embed desc limit per embed 

    embed_character_limit_total = get_config("embed_character_limit_total")

    split_messages: List[str] = []
    all_lines = text.split(desc.seperator)
    wall_of_text = ""
    total_len_added = 0
    for line in all_lines:
        # line = line.replace('@', '')  # pings are fine specifically in embed
        next_length = len(wall_of_text) + len(line)

        desc_limit = embed_character_limit_total 
        if not len(split_messages):
            desc_limit -= len(title) 
        elif len(text) - total_len_added < embed_character_limit_total:
            desc_limit -= len(footer) 

        if next_length > desc_limit:
            new_desc = wall_of_text[:-len(desc.seperator)]
            split_messages.append(new_desc)
            total_len_added = len(new_desc)
            wall_of_text = line + desc.seperator 
        else:
            wall_of_text += line + desc.seperator

    split_messages.append(wall_of_text[:-len(desc.seperator)])

    embed_character_limit_desc = get_config("embed_character_limit_desc")

    embed_groups: List[List[discord.Embed]] = []
    for i, text_part in enumerate(split_messages):

        split_subgroups: List[str] = [] 
        if len(text_part) <= embed_character_limit_desc:
            split_subgroups.append(text_part)
        elif len(text_part) == 0:
            continue
        else:
            #NOTE: (Ahmayk) split in half, assuming that half of the  
            #max message character length (6000)
            #will fit inside the max embed desc length (4096)
            embed_desc_1 = "" 
            embed_desc_2 = "" 
            lines = text_part.split(desc.seperator)
            for line_i, line in enumerate(lines):

                to_add = line
                if line_i != (len(lines) - 1):
                    to_add += desc.seperator

                if line_i < math.ceil(len(lines) / 2):
                    embed_desc_1 += to_add 
                else:
                    embed_desc_2 += to_add 
            split_subgroups.append(embed_desc_1)
            split_subgroups.append(embed_desc_2)

        embed_list: List[discord.Embed] = []
        for k, subgroup in enumerate(split_subgroups):
            embed = None
            if i == 0 and k == 0: 
                embed = discord.Embed(description=subgroup, color=color, title=title)
            else:
                embed = discord.Embed(description=subgroup, color=color)
            
            if i == len(split_messages) - 1 and k == len(split_subgroups) - 1: 
                embed.set_footer(text=desc.footer)
            embed_list.append(embed)
        
        embed_groups.append(embed_list)

    for embed_group in embed_groups:
        try:
            await channel.send(embeds=embed_group, delete_after=delete_after_seconds)
        except Exception as error:
            await write_log(f'Failed to send embed to {channel.jump_url}: {type(error).__name__}: {error}')

def parse_errors(if_errors_txt: str, error_strings: List[str]) -> str:
    max_errors_to_send = 3
    return_text = "" 
    if len(error_strings):
        return_text += f':bangbang:{if_errors_txt}\n```' 
        return_text += "\n".join(error_strings[:max_errors_to_send])
        if len(error_strings) > max_errors_to_send:
            return_text += '\n ...(errors truncated)...'
        return_text += "```" 
    return return_text

async def send_if_errors(if_errors_txt: str, error_strings: List[str], channel: TextChannel | Thread):
    return_text = parse_errors(if_errors_txt, error_strings) 
    await send(return_text, channel)

async def send_and_if_errors(txt: str, if_errors_txt: str, error_strings: List[str], channel: TextChannel | Thread, delete_after: float = 0):
    error_text = parse_errors(if_errors_txt, error_strings) 
    if len(txt) or len(error_text):
        await send(f'{txt}\n{error_text}', channel, delete_after)

async def write_log(msg: str = "Placeholder message", embed: bool = False):
    """
    Logging function.
    If LOG_CHANNEL is valid, send a message there.
    Also write to a log file as backup.
    """
    try:
        log_channel = bot.get_channel(get_log_channel())
        if embed:
            await send_embed(msg, log_channel, EmbedDesc())
        else:
            await send(msg, log_channel)
    except (discord.InvalidData, discord.HTTPException, discord.Forbidden) as e:
        msg += "\nError fetching log channel: {}".format(e.text)
    except discord.NotFound:
        pass
    
    with open('logs.txt', 'a', encoding='utf-8') as file:
        file.write(datetime.now(timezone.utc).strftime('%m/%d/%y %I:%M %p'))
        file.write('\n')
        file.write(msg)
        file.write('\n=========================================\n')


async def send_crash(txt: str, error: Exception, channel: TextChannel | Thread | None):
    error_string = f"{type(error).__name__}: {error}"
    description = f":boom: Intriguing! I have encountered an unexpected error! ```{error_string}```"
    if channel:
        await send(description, channel)

    await log_exception(txt, error, [], False)


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
    elif rip_title == 'ansi':
        # fairly sure we will never upload a video named "ansi". other codeblock formats exist but this one is the most common for color so
        return get_raw_rip_title(text.replace('```ansi', '```', 1))
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
    return  f"<https://discord.com/channels/{str(guild_id)}/{str(channel_id)}/{str(message_id)}>"


def extract_discord_link(text: str) -> str:
    regex = r'(https://discord\.com/channels/\d+/\d+/\d+)'
    match = re.search(regex, text)
    if match:
        return match.group(1)
    else:
        return ""


def line_contains_substring(line: str, substring: str) -> bool:
    """
    Helper function to search substrings in Discord markdown-formatted line, ignoring case and formatting
    """
    # Temporary "hacky" support for regex search until/if we figure out a better system
    if substring.startswith("r\'") and substring.endswith("\'") or substring.startswith("r\"") and substring.endswith("\""):
        regex_search_key = substring[2:-1]
        return re.search(regex_search_key, line) is not None
    return substring.lower() in line.replace('*', '').replace('_', '').replace('|', '').replace('#', '').lower()


def is_message_rip(message: Message) -> bool:
    result = False
    has_quotes = '```' in message.content
    if has_quotes:
        rip_links = extract_rip_link(message.content)
        if len(rip_links) > 0 or len(message.attachments):
            result = True
    return result


def message_has_react(emoji: str, message: Message) -> bool:
    result = False
    for reaction in message.reactions:
        if reaction.emoji == emoji:
            result = True
            break
    return result


# https://stackoverflow.com/a/65882269
async def run_blocking(blocking_func: typing.Callable, *args, **kwargs) -> typing.Any:
    """
    Runs a blocking function in a non-blocking way.
    Needed because QoC functions take a while to run.
    """
    func = functools.partial(blocking_func, *args, **kwargs) # `run_in_executor` doesn't support kwargs, `functools.partial` does
    return await bot.loop.run_in_executor(None, func)

def format_rip(rip: Rip, guild: discord.Guild, make_smol: bool, spec_overdue_days: int, overdue_days: int) -> str:

    reacts = ""
    num_checks = 0
    num_rejects = 0
    num_goldchecks = 0
    specs_required = 1
    checks_required = 3
    specs_needed = False
    fix_or_alert = False

    for react_and_user in rip.reacts:
        if react_is(ReactType.GOLDCHECK, react_and_user.name):
            num_goldchecks += 1
        elif react_is(ReactType.CHECKREQ, react_and_user.name):
            try:
                checks_required = int(react_and_user.name.split("check")[0])
            except ValueError:
                print("Error parsing checkreq react: {}".format(react_and_user.name))
        elif react_is(ReactType.CHECK, react_and_user.name):
            num_checks += 1
        elif react_is(ReactType.REJECT, react_and_user.name):
            num_rejects += 1
        elif react_is_one([ReactType.FIX, ReactType.ALERT], react_and_user.name):
            fix_or_alert = True
        elif react_is(ReactType.STOP, react_and_user.name):
            specs_needed = True
        elif react_is(ReactType.NUMBER, react_and_user.name):
            specs_required = KEYCAP_EMOJIS[react_and_user.name]

        reacts += reaction_name_to_emoji_string(react_and_user.name, guild) 
        reacts += " " 

    indicator = ""
    check_passed = (num_checks - num_rejects >= checks_required) and not fix_or_alert
    specs_passed = (not specs_needed or num_goldchecks >= specs_required)
    is_spec_overdue = (datetime.now(timezone.utc) - rip.created_at) > timedelta(days=spec_overdue_days)
    is_overdue =      (datetime.now(timezone.utc) - rip.created_at) > timedelta(days=overdue_days)
    if check_passed:
        indicator = APPROVED_INDICATOR if specs_passed else AWAITING_SPECIALIST_INDICATOR
    elif specs_needed and not specs_passed and is_spec_overdue:
        indicator = SPECS_OVERDUE_INDICATOR
    elif is_overdue:
        indicator = OVERDUE_INDICATOR

    rip_title = get_rip_title(rip.text)
    author = get_rip_author(rip.text, rip.message_author_name)
    author = author.replace('*', '').replace('_', '')

    link = format_message_link(guild.id, rip.channel_id, rip.message_id)
    title_body = f'**[{rip_title}]({link})**'
    if len(indicator) > 0:
        title_body = f'{indicator} {title_body} {indicator}'

    utc = int(rip.created_at.replace(tzinfo=timezone.utc).timestamp())
    info_body = f'{author} <t:{utc}:R>'
    if len(reacts):
        info_body += f' | {reacts}'

    if make_smol:
        return f'-# {title_body} {info_body}\n'
    else:
        return f'{title_body}\n{info_body}\n'


def choose_random_rips(rips: List[Rip], random_count: int) -> List[int]:
    result = []
    random.shuffle(rips)
    ##NOTE: (Ahmayk) consider default 0 as 1, clamp by rip size
    clamped_count = min(max(1, random_count), len(rips))
    for i in range(clamped_count):
        result.append(rips[i].message_id)
    return result

#NOTE: (Ahmayk) does not check if input is actually an emoji
def emoji_to_react_name_if_emoji(s: str) -> str:
    react_input = s 
    match = re.fullmatch(r'<a?:(\w+):\d+>', react_input)
    if match:
        react_input = match.group(1)
    return react_input

class ParsedSearchInput(NamedTuple):
    search_keys: List[str]
    is_not: bool
    containing_error_string: str

def parse_search_input(args: List[str]) -> ParsedSearchInput:
    is_not = False
    if "NOT" in args:
        is_not = True
        args.remove("NOT")
    search_keys = " ".join([str(s) for s in args]).split('|')

    prefix = 'containing'
    if is_not:
        prefix = 'NOT containing'

    containing_error_string = ''
    if len(search_keys) == 1:
        containing_error_string = f'{prefix} `{search_keys[0]}`'
    elif len(search_keys) == 2:
        containing_error_string = f'{prefix} `{search_keys[0]}` or `{search_keys[1]}`'
    elif len(search_keys) > 2:
        containing_error_string = f'{prefix} `{search_keys}`'

    return ParsedSearchInput(search_keys, is_not, containing_error_string)

def search_with_parsed_input(text: str, parsed_search_input: ParsedSearchInput) -> bool:
    is_valid = False
    for key in parsed_search_input.search_keys:
        if line_contains_substring(text, key):
            is_valid = True
            break
    if parsed_search_input.is_not:
        is_valid = not is_valid 
    return is_valid
