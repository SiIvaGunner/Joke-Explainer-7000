
import discord
from discord import Message, Thread, TextChannel
from discord.abc import GuildChannel
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta, time

from bot_secrets import YOUTUBE_API_KEY, YOUTUBE_CHANNEL_NAME
from simpleQoC.qoc import performQoC, msgContainsBitrateFix, msgContainsClippingFix, msgContainsSigninErr, ffmpegExists, getFileMetadataMutagen, getFileMetadataFfprobe
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

import discord
from discord.abc import GuildChannel

from hq_config import *

bot = discord.Client(
    intents = discord.Intents.all() # This was a change necessitated by an update to discord.py :/
    # https://stackoverflow.com/questions/71950432/how-to-resolve-the-following-error-in-discord-py-typeerror-init-missing
    # Also had to enable MESSAGE CONENT INTENT https://stackoverflow.com/questions/71553296/commands-dont-run-in-discord-py-2-0-no-errors-but-run-in-discord-py-1-7-3
    # 10/28/22 They changed it again!!! https://stackoverflow.com/questions/73458847/discord-py-error-message-discord-ext-commands-bot-privileged-message-content-i
)

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
DEFAULT_SENDBACK = '➡️'

QOC_DEFAULT_LINKERR = '🔗'
QOC_DEFAULT_BITRATE = '🔢'
QOC_DEFAULT_CLIPPING = '📢'

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
        result = message.author == bot.user and len(message.embeds)
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

    return Rip(message.content, message.id, message.channel.id, message.author.id, \
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

times = []
for i in range(0, 23):
    times.append(time(hour=i, tzinfo=timezone.utc))

@tasks.loop(time=times)
async def validate_cache_regularly():
    try:
        string_and_errors = await validate_cache_all()
        if len(string_and_errors.string):
            await write_log(f'**Cache validation had issues:**\n{string_and_errors.string}')
        else:
            #TODO: (Ahmayk) remove this once cache starts being trustworthy (hopefully this happens)
            today = datetime.now()
            print(f"{today.strftime('%m/%d/%y %I:%M %p')}  Cache revalidated. No issues found.")
    except Exception as error:
        await send_crash(f'ERROR on scheduled cache revalidation', error, None)
    

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
        return None, "Error: Link is not a valid roundup channel."


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

async def send(text: str, channel: TextChannel | Thread):
    limit = get_config("character_limit")
    split_message = split_long_message(text, limit)
    for line in split_message:
        if len(line):
            try:
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

async def send_and_if_errors(txt: str, if_errors_txt: str, error_strings: List[str], channel: TextChannel | Thread):
    error_text = parse_errors(if_errors_txt, error_strings) 
    if len(txt) or len(error_text):
        await send(f'{txt}\n{error_text}', channel)

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


# https://stackoverflow.com/a/65882269
async def run_blocking(blocking_func: typing.Callable, *args, **kwargs) -> typing.Any:
    """
    Runs a blocking function in a non-blocking way.
    Needed because QoC functions take a while to run.
    """
    func = functools.partial(blocking_func, *args, **kwargs) # `run_in_executor` doesn't support kwargs, `functools.partial` does
    return await bot.loop.run_in_executor(None, func)


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
    advancedCheck = get_config('metadata')
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
        channel_ids = get_channel_ids_of_types(['QUEUE', 'QOC'])
        for channel_id in channel_ids:
            channel = bot.get_channel(channel_id)
            if channel:
                rips_and_errors = await get_rips_fast(channel, GetRipsDesc())
                rips = rips_and_errors.rips
                #TODO: (Ahmayk) handle errors

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
    
    # QoC
    qcCode, qcMsg, detectedUrl = await check_qoc(text, fullFeedback)
    if (qcCode != 0) or fullFeedback:
        verdict += code_to_verdict(qcCode, qcMsg)
        msg += qcMsg + "\n"

    # Metadata
    mtCode, mtMsg = await check_metadata(text, message_id, message_author_name, fullFeedback)
    if (mtCode != 0) or fullFeedback:
        verdict += ("" if len(verdict) == 0 else " ") + DEFAULT_METADATA
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

#===============================================#
#                    EVENTS                     #
#===============================================#

async def remove_embeds_from_channel_startup(channel_ids: List[int], expire_time_seconds: float):
    prefix = get_config("prefix")
    for channel_id in channel_ids:
        channel = bot.get_channel(channel_id)
        if channel:
            messages_and_errors = await discord_cleanup_embeds(200, expire_time_seconds, channel, None) 
            count = len(messages_and_errors.messages)
            if count > 0:
                await send(f"Good morning! Removed {count} embed messages sent more than {expire_time_seconds // 60} minutes ago. " + \
                       f"If there are older or newer embeds that should be removed, run {prefix}cleanup manually.", channel)

@bot.event
async def on_ready():
    # this should ensure a check for config.json on start up. if the file doesnt exist, the bot should close with error.
    get_config("prefix")

    print(f'Logged in as {bot.user.name}')
    print('#################################')

    await write_log("Good morning! Caching rips...")
    await rebuild_cache_all()

    await write_log('Validating cache...')
    string_and_errors = await validate_cache_all()
    validate_result = f'**Startup caching complete.**{string_and_errors.string}'
    await write_log(validate_result)

    validate_cache_regularly.start()

    qoc_channel_ids = get_channel_ids_of_types(['QOC'])
    await remove_embeds_from_channel_startup(qoc_channel_ids, get_config('embed_seconds'))

    proxy_channel_ids = get_channel_ids_of_types(['PROXY_QOC'])
    await remove_embeds_from_channel_startup(proxy_channel_ids, get_config('proxy_embed_seconds'))

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

latest_pin_time: datetime = datetime.now(timezone.utc)
latest_scan_time: datetime = datetime.now(timezone.utc)

@bot.event
async def on_guild_channel_pins_update(channel: typing.Union[GuildChannel, Thread], last_pin: datetime):

    if not channel_is_types(channel, ['QOC', 'SUBS_PIN']):
        return

    global latest_pin_time

    if last_pin is None or last_pin <= latest_pin_time:
        # print("Seems to be a message being unpinned")

        channel_info = get_channel_info(channel)

        if channel_info.rip_fetch_type == RipFetchType.PINS: 

            txt = "" 
            error_strings = []

            await lock_channel(channel.id, channel)

            current_message_ids: list[int] = []
            messages_and_errors = await discord_get_channel_pins(None, channel)
            error_strings.extend(messages_and_errors.error_strings)
            for message in messages_and_errors.messages: 
                current_message_ids.append(message.id)

            rips_to_remove: List[Rip] = []
            if channel.id in RIP_CACHE:
                for message_id, rip in RIP_CACHE[channel.id].items():
                    if message_id not in current_message_ids:
                        rips_to_remove.append(rip)

            if len(rips_to_remove):
                audit_log_entries_and_errors = await discord_get_audit_log_entries(discord.AuditLogAction.message_unpin, 10, channel.guild) 
                error_strings.extend(audit_log_entries_and_errors.error_strings)
                spec_overdue_days = get_config('spec_overdue_days')
                overdue_days = get_config('overdue_days')

                for rip in rips_to_remove:
                    await remove_rip_from_cache(rip.message_id, channel.id)

                    user_string = 'Someone' 
                    for entry in audit_log_entries_and_errors.audit_log_entires:
                        if entry.extra and entry.user and entry.extra.message_id == rip.message_id:
                            user_string = entry.user.name
                            break

                    formatted_rip = format_rip(rip, channel.guild, True, spec_overdue_days, overdue_days)
                    txt += f'\n-# :pushpin::x: **{user_string}** unpinned\n{formatted_rip}'

                rips_and_errors = await get_rips_fast(channel, GetRipsDesc())
                error_strings.extend(rips_and_errors.error_strings)
                count = len(rips_and_errors.rips)
                soft_pin_limit = get_config('soft_pin_limit')
                txt += f'-# Rip Count: {count}/{soft_pin_limit}'

            unlock_channel(channel.id)

            await send_and_if_errors(txt, "Errors during unpin.", error_strings, channel)

    else:

        return_message = ""

        messages_and_errors = await discord_get_channel_pins(1, channel)
        if len(messages_and_errors.error_strings):
            return await send_if_errors("Error: Failed to vet pinned message.", messages_and_errors.error_strings, channel)
        
        assert(len(messages_and_errors.messages))
        message = messages_and_errors.messages[0]

        error_strings: List[str] = []

        if is_message_rip(message):

            latest_pin_time = last_pin

            rips_and_errors = await get_rips_fast(channel, GetRipsDesc())
            error_strings.extend(rips_and_errors.error_strings)
            count = len(rips_and_errors.rips)

            is_valid = True
            SOFT_PIN_LIMIT = get_config('soft_pin_limit')
            if count > SOFT_PIN_LIMIT:
                if get_channel_config(channel.id).pinlimit_must_die_mode:
                    error_strings.extend(await discord_unpin_message(message))
                    error_strings.extend(await discord_add_reaction('📌', message))
                    await send(f":bangbang: **Error**: {count}/{SOFT_PIN_LIMIT} rips in pins. Unpinned.\n-# Remove the 📌 reaction when this is resolved.", channel)
                    is_valid = False
                else:
                    await send(f"**Warning: {count}/{SOFT_PIN_LIMIT}** rips pinned. Please handle other rips first :(", channel)
            elif count > max(0, SOFT_PIN_LIMIT - 10):
                await send(f"-# Warning: **{SOFT_PIN_LIMIT - count} rips** until pinlimit is reached.\n-# Rip Count: {count}/{SOFT_PIN_LIMIT}", channel)

            if is_valid:
                await lock_message(message.id, None)
                #NOTE: (Ahmayk) have to fetch message to get reaction data for cache
                message_and_errors = await discord_fetch_message(message.id, channel)
                error_strings.extend(message_and_errors.error_strings)
                if message_and_errors.message:
                    message = message_and_errors.message
                cache_rip_in_message(message)
                unlock_message(message.id)

                #TODO: (Ahmayk) if this errors at all we need to know
                verdict, msg = await check_qoc_and_metadata(message.content, message.id, str(message.author))

                if len(msg):
                    rip_title = get_rip_title(message.content)
                    link = format_message_link(channel.guild.id, channel.id, message.id)

                    if QOC_DEFAULT_LINKERR in verdict:
                        return_message += ":warning: **Rip link not Auto-QoCed**\n-# Remove the :link: reaction when the link is fixed and/or properly auto-qoced."
                        await message.add_reaction(QOC_DEFAULT_LINKERR)
                    return_message += f'\n**Rip**: **[{rip_title}]({link})**\n**Verdict**: {verdict}\n{msg}-# React {DEFAULT_CHECK} if this is resolved.'


        await send_and_if_errors(return_message, "Warning: Pining QoC rip returned errors.", error_strings, channel)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):

    channel = bot.get_channel(payload.channel_id)
    if channel:
        is_qoc_channel = channel_is_types(channel, ['QOC'])
        is_suborqueue_channel = channel_is_types(channel, ['QUEUE', 'SUBS', 'SUBS_THREAD', 'SUBS_PIN'])

        if is_qoc_channel or is_suborqueue_channel:

            await lock_channel(payload.channel_id, None)
            if payload.channel_id in RIP_CACHE and payload.message_id in RIP_CACHE[payload.channel_id]:

                await lock_message(payload.message_id, None)

                ##NOTE: (Ahmayk) could skip this if we wanted to, but
                ##we don't really lose much from doing a fetch here.
                ##It guarentees that all reactions are accurate 
                message_and_errors = await discord_fetch_message(payload.message_id, channel)

                if message_and_errors.message:
                    rip = cache_rip_in_message(message_and_errors.message)
                    if is_qoc_channel and message_and_errors.message.pinned:

                        if rip.message_id not in USER_REACT_CACHE: 
                            USER_REACT_CACHE[rip.message_id] = {} 

                        react = React(payload.emoji.id or 0, payload.emoji.name)
                        if react not in USER_REACT_CACHE[rip.message_id]: 
                            USER_REACT_CACHE[rip.message_id][react] = [] 

                        USER_REACT_CACHE[rip.message_id][react].append(payload.user_id) 

                unlock_message(payload.message_id)

            unlock_channel(payload.channel_id)


async def process_suborqueue_rip_caching(message: Message):
    is_suborqueue_channel = channel_is_types(message.channel, ['QUEUE', 'SUBS', 'SUBS_THREAD'])
    if is_suborqueue_channel and is_message_rip(message): 
        cache_rip_in_message(message)


async def remove_reaction_from_cache(channel_id: int, message_id: int, emoji: discord.PartialEmoji, user_id: int | None, remove_all: bool):
    in_rip_cache = channel_id in RIP_CACHE and message_id in RIP_CACHE[channel_id]
    in_user_react_cache = message_id in USER_REACT_CACHE
    if in_rip_cache or in_user_react_cache:
        await lock_message(message_id, None)

    react = React(emoji.id or 0, emoji.name)

    if in_rip_cache:
        rip = RIP_CACHE[channel_id][message_id]
        for cached_react in rip.reacts:
            if cached_react == react:
                rip.reacts.remove(cached_react)
                if not remove_all:
                    break

    if in_user_react_cache: 
        if user_id and \
            react in USER_REACT_CACHE[message_id] and \
            user_id in USER_REACT_CACHE[message_id][react]:
            USER_REACT_CACHE[message_id][react].remove(user_id)
        
        if remove_all or not len(USER_REACT_CACHE[message_id]):
            USER_REACT_CACHE.pop(message_id)

    if in_rip_cache or in_user_react_cache:
        unlock_message(message_id)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    await remove_reaction_from_cache(payload.channel_id, payload.message_id, payload.emoji, payload.user_id, False)

@bot.event
async def on_raw_reaction_clear_emoji(payload: discord.RawReactionClearEmojiEvent):
    await remove_reaction_from_cache(payload.channel_id, payload.message_id, payload.emoji, None, True)

@bot.event
async def on_raw_reaction_clear(payload: discord.RawReactionClearEvent):
    in_rip_cache = payload.channel_id in RIP_CACHE and payload.message_id in RIP_CACHE[payload.channel_id]
    in_user_react_cache = payload.message_id in USER_REACT_CACHE
    if in_rip_cache or in_user_react_cache:
        await lock_message(payload.message_id, None)

    if in_rip_cache: 
        RIP_CACHE[payload.channel_id][payload.message_id].reacts.clear()
    if in_user_react_cache: 
        USER_REACT_CACHE.pop(payload.message_id)

    if in_rip_cache or in_user_react_cache:
        unlock_message(payload.message_id)


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    await remove_rip_from_cache(payload.message_id, payload.channel_id)


@bot.event
async def on_raw_message_edit(payload: discord.RawMessageUpdateEvent):
    if payload.channel_id in RIP_CACHE and payload.message_id in RIP_CACHE[payload.channel_id]: 
        await lock_message(payload.message_id, None)
        rip = RIP_CACHE[payload.channel_id][payload.message_id]
        rip = rip._replace(text = payload.message.content)
        RIP_CACHE[payload.channel_id][payload.message_id] = rip
        unlock_message(payload.message_id)


@bot.event
async def on_message(message: Message):
    if message.author == bot.user:
        return

    prefix = get_config("prefix")
    await process_suborqueue_rip_caching(message)

    if not message.content.startswith(prefix):
        return

    args = message.content.split(' ')
    command_name = args[0][1:].lower()
    command_info = find_command_info(command_name)

    if not command_info:
        return

    if not command_info.public and not channel_is_types(message.channel, ['QOC', 'PROXY_QOC']):
        ##NOTE: (Ahmayk) consider giving feedback on a command not being avaliable
        # if not having any feedback is confusing, probably is fine tho
        return

    if command_info.admin:
        assert type(message.author) is discord.Member
        if not message.author.guild_permissions.administrator:
            return await send("Error: Only users with admin access in this server can use this command.", message.channel)

    command_context = CommandContext(message.channel, message.author, message.reference)

    today = datetime.now() # Technically not useful, but it looks gorgeous on my CRT monitor
    print(f"{today.strftime('%m/%d/%y %I:%M %p')}  ~~~  Heard {command_name} command from {message.author.name}!")

    try:
        await command_info.func(args[1:], command_context)
    except Exception as error:
        await send_crash(f'ERROR on command {prefix}{command_name}:', error, message.channel)
