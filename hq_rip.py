from discord import Message, Thread, TextChannel

from typing import NamedTuple, List
from enum import Enum, auto

from contextlib import asynccontextmanager
import asyncio

from hq_config import *
from hq_types import Rip, React 
from hq_discord import *
from hq_react import *
from hq_strings import *
from hq_database import *

RIP_CACHE: dict[int, dict[int, Rip]] = {}
USER_REACT_CACHE: dict[int, dict[React, List[int]]] = {}

def init_channel_cache(channel_id: int):
    if channel_id not in RIP_CACHE:
        RIP_CACHE[channel_id] = {}

def cache_rip_in_message(message: Message):
    rip = init_rip(message)
    init_channel_cache(message.channel.id)
    RIP_CACHE[message.channel.id][message.id] = rip
    return rip

def cache_user_react_data(user_react_data: dict[React, List[int]], message_id: int):
    if len(user_react_data):
        if message_id not in USER_REACT_CACHE:
            USER_REACT_CACHE[message_id] = {} 
        for react, user_ids in user_react_data.items():
            USER_REACT_CACHE[message_id][react] = []
            for user_id in user_ids:
                USER_REACT_CACHE[message_id][react].append(user_id) 

CACHE_LOCK_CHANNEL: dict[int, asyncio.Lock] = {}
CACHE_LOCK_MESSAGE: dict[int, asyncio.Lock] = {}

# NOTE: (Ahmayk) Locking on either a channel or message cache allows us to manage congruent process and commands
## without one reading from or writing into the cache with outdated info
@asynccontextmanager
async def _lock(id: int, lock_dict: dict[int, asyncio.Lock], error_strings: list[str], typing_channel: TextChannel | Thread | None):
    if id not in lock_dict:
        lock_dict[id] = asyncio.Lock()

    dict_name = ''
    if lock_dict == CACHE_LOCK_CHANNEL:
        dict_name = "CHANNEL"
    elif lock_dict == CACHE_LOCK_MESSAGE:
        dict_name = "MESSAGE"

    if lock_dict[id].locked():
        print(f'LOCKED ON {dict_name} id: {id}. Typing channel: {typing_channel}')
        async with typing_channel.typing() if typing_channel is not None else empty_async_context():
            await lock_dict[id].acquire()
            print(f'LOCK ACQUIRED ON {dict_name} id: {id}')
    else:
        await lock_dict[id].acquire()

    try:
        #NOTE: (Ahmayk) code runs here
        yield
    except Exception as error:
        await log_exception(f'Unhandled error during lock on {dict_name} id: {id}', error, error_strings, True)

    lock_dict[id].release()

JE_DATABASE_LOCK = asyncio.Lock()

@asynccontextmanager
async def lock_channel(channel_id: int, error_strings: list[str], typing_channel: TextChannel | Thread | None):
    async with _lock(channel_id, CACHE_LOCK_CHANNEL, error_strings, typing_channel):
        yield

@asynccontextmanager
async def lock_message(message_id: int, error_strings: list[str], typing_channel: TextChannel | Thread | None):
    async with _lock(message_id, CACHE_LOCK_MESSAGE, error_strings, typing_channel):
        yield

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

    elif channel_is_types(channel, ['QUEUE', 'INACTIVE_QUEUE', 'SUBS_THREAD']):
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


def is_message_rip(message: Message) -> bool:
    result = False
    has_quotes = '```' in message.content
    if has_quotes:
        rip_links = extract_rip_link(message.content)
        if len(rip_links) > 0 or len(message.attachments):
            result = True
    return result


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

async def process_jingle_status(message: Message) -> list[str]:
    error_strings = []
    jingle_length_in_seconds = get_config("jingle_length_in_seconds")
    urls = extract_rip_link(message.content)
    if len(urls):
        floatAndErrors = await get_rip_url_length(urls[0], GetRipUrlLengthDesc())
        if not len(floatAndErrors.error_strings) and floatAndErrors.result > 0:
            if floatAndErrors.result <= jingle_length_in_seconds:
                await update_rip_status_reacts(message, [ReactType.JINGLE], [], message.guild)
            else:
                await update_rip_status_reacts(message, [], [ReactType.JINGLE], message.guild)
        else:
            error_strings.extend(floatAndErrors.error_strings)
    return error_strings

async def process_rip_message(message: Message, refetch_message: bool, is_validate_message: bool, \
                              typing_channel: TextChannel | Thread | None) -> StringAndErrors:
    return_message = ""
    error_strings: list[str] = [] 
    if is_message_rip(message):
        async with lock_message(message.id, error_strings, typing_channel):

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

            await process_jingle_status(message)

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
                    async with lock_channel(message.thread.id, error_strings, typing_channel):
                        string_and_errors = await process_rip_channel(message.thread, False, typing_channel)
                        return_message += string_and_errors.string
                        error_strings.extend(string_and_errors.error_strings)
                        if len(RIP_CACHE[message.thread.id]):
                            thread_rips = list(RIP_CACHE[message.thread.id].values())
                            thread_rips.sort(key = lambda rip: rip.created_at, reverse=True)
                            #NOTE: (Ahmayk) we insert thread rips also in its parent channel dict so that
                            #we get all rips in theads when we get the parent channel's rips 
                            for thread_rip in thread_rips:
                                async with lock_message(thread_rip.message_id, error_strings, None):
                                    RIP_CACHE[channel.id][thread_rip.message_id] = thread_rip 
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

    error_strings: list[str] = [] 

    init_channel_cache(channel.id)

    ##NOTE: (Ahmayk) We need to prevent other processes from accessing the cache 
    ## in the case where we are updating the cache. If we allow access to the cache while
    ## it is being updated, it would likely be incomplete or wrong! 
    async with lock_channel(channel.id, error_strings, desc.typing_channel):

        if not len(RIP_CACHE[channel.id]) or desc.rebuild_cache:
            async with desc.typing_channel.typing() if desc.typing_channel is not None else empty_async_context():
                string_and_errors = await process_rip_channel(channel, False, desc.typing_channel)
                error_strings.extend(string_and_errors.error_strings)

    rips = list(RIP_CACHE[channel.id].values())

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

async def get_rips_of_channel_types(channel_types: list[str], typing_channel: TextChannel | Thread | None) -> RipsAndErrors:
    rips = []
    channels_and_errors = await get_channels_of_types(channel_types, [])
    error_strings = channels_and_errors.error_strings 
    for channel in channels_and_errors.channels:
        rips_and_errors  = await get_rips(channel, GetRipsDesc(typing_channel=typing_channel))
        rips.extend(rips_and_errors.rips)
        error_strings.extend(rips_and_errors.error_strings)
    return RipsAndErrors(rips, error_strings) 

async def get_rips_fast_of_channel_types(channel_types: list[str], typing_channel: TextChannel | Thread | None) -> RipsAndErrors:
    rips = []
    channels_and_errors = await get_channels_of_types(channel_types, [])
    error_strings = channels_and_errors.error_strings 
    for channel in channels_and_errors.channels:
        rips_and_errors  = await get_rips_fast(channel, GetRipsDesc(typing_channel=typing_channel))
        rips.extend(rips_and_errors.rips)
        error_strings.extend(rips_and_errors.error_strings)
    return RipsAndErrors(rips, error_strings) 

def get_react_counts(rip: Rip) -> dict[React, int]:
    count_dict: dict[React, int] = {}
    for react in rip.reacts:
        if react not in count_dict:
            count_dict[react] = 0
        count_dict[react] += 1
    return count_dict

async def validate_cache_all() -> StringAndErrors:
    return_string = ""
    channels_and_errors = await get_channels_of_types(['QOC', 'SUBS', 'SUBS_PIN', 'SUBS_THREAD', 'QUEUE'], [])
    validate_errors = []
    error_strings = channels_and_errors.error_strings 
    for channel in channels_and_errors.channels:
        string_and_errors = await process_rip_channel(channel, True, None)
        validate_errors.append(string_and_errors.string)
        error_strings.extend(string_and_errors.error_strings)
    if len(validate_errors):
        return_string = "\n".join(validate_errors)
    return StringAndErrors(return_string, error_strings) 


async def rebuild_cache_for_channel(channel: TextChannel | Thread) -> StringAndErrors:
    return_message = ""

    rips_and_errors = await get_rips(channel, GetRipsDesc(rebuild_cache=True))

    channel_type_string = '[Unknown type]'
    if channel_is_types(channel, ['QOC']):
        channel_type_string = 'qoc'
    elif channel_is_types(channel, ['QUEUE']):
        channel_type_string = 'queued'
    elif channel_is_types(channel, ['INACTIVE_QUEUE']):
        channel_type_string = 'inactively queued'
    elif channel_is_types(channel, ['SUBS', 'SUBS_THREAD', 'SUBS_PIN']):
        channel_type_string = 'subbed'
    return_message = f'Cached {len(rips_and_errors.rips)} {channel_type_string} rips in {channel.jump_url}.'
    await write_log(return_message)

    return StringAndErrors(return_message, rips_and_errors.error_strings) 


async def rebuild_cache_of_types(include_types: list[str], exclude_types: list[str]) -> StringAndErrors:
    return_message = ""
    channels_and_errors = await get_channels_of_types(include_types, exclude_types)
    error_strings = channels_and_errors.error_strings
    for channel in channels_and_errors.channels:
        string_and_errors = await rebuild_cache_for_channel(channel)
        return_message += f'\n{string_and_errors.string}'
        error_strings.extend(string_and_errors.error_strings)
    return StringAndErrors(return_message, error_strings) 


async def rebuild_cache() -> StringAndErrors:
    ##NOTE: (Ahmayk) we rebuild non-qoc channels first to put them in cache quicker 
    ## because fetching them is very fast. QoC channels take longer because we fetch react user data
    string_and_errors_1 = await rebuild_cache_of_types(['SUBS', 'SUBS_PIN', 'SUBS_THREAD', 'QUEUE', 'INACTIVE_QUEUE'], ['QOC'])
    string_and_errors_2 = await rebuild_cache_of_types(['QOC'], [])
    string = f'{string_and_errors_1.string}\n{string_and_errors_2.string}'
    error_strings = []
    error_strings.extend(string_and_errors_1.error_strings)
    error_strings.extend(string_and_errors_2.error_strings)
    return StringAndErrors(string, error_strings)


async def remove_rip_from_cache(message_id: int, channel_id: int):
    ##NOTE: (Ahmayk) if something is processing on the message, we want to wait for that to finish
    ##so that we can then remove whatever was being processed
    ##TODO: (Ahmayk) return errors (tho do we need to?)
    async with lock_message(message_id, [], None):
        if channel_id in RIP_CACHE and message_id in RIP_CACHE[channel_id]: 
            RIP_CACHE[channel_id].pop(message_id)
        if message_id in USER_REACT_CACHE:
            USER_REACT_CACHE.pop(message_id)

    CACHE_LOCK_MESSAGE.pop(message_id)


async def user_is_react(user_react_check_type: UserReactCheckType, user_id: int, rip: Rip,\
                        typing_channel: TextChannel | Thread | None) -> BoolAndErrors:
    result = False
    error_strings: list[str] = []

    async with lock_message(rip.message_id, error_strings, typing_channel):

        if rip.message_id not in USER_REACT_CACHE:
            USER_REACT_CACHE[rip.message_id] = {} 

        react_list = user_react_check_type_to_react_list(user_react_check_type)

        fetch_user_ids = False 
        for react in rip.reacts:
            if react_is_one(react_list, react.name) and react not in USER_REACT_CACHE[rip.message_id]:
                fetch_user_ids = True
                break

        if fetch_user_ids: 
            channel_and_errors = await discord_find_channel(rip.channel_id)
            error_strings.extend(channel_and_errors.error_strings)
            if channel_and_errors.channel:
                # NOTE: (Ahmayk) this message fetch isn't actually neccessary, but we need the
                # message react object to call reaction.users().
                # As far as I can tell this is the only way discord.py exposes this API call.
                # An optimization here would be to figure out how to do this without the reaction object,
                # perhaps bypassing discord.py to make the direct api call we need
                # In pratice we shouldn't be calling this code path too often anyway during regular usage
                # So isn't the biggest problem
                message_and_errors = await discord_fetch_message(rip.message_id, channel_and_errors.channel)
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


    return BoolAndErrors(result, error_strings)


def format_rip_timecode(seconds: float) -> str:
    result = ""
    #NOTE: (Ahmayk) 0 is stored in database when the call errors
    # we display these as blank 
    if seconds > 0:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        result = f"{minutes:02d}:{secs:02d}"
        if hours > 0:
            result = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return result 


async def get_formatted_rip_length(text: str, force_download: bool, use_jingle_emoji: bool, guild: Guild) -> StringAndErrors:
    urls = extract_rip_link(text)
    return_message = ""
    error_strings = []
    if len(urls):
        floatAndErrors = await get_rip_url_length(urls[0], GetRipUrlLengthDesc(force_download=force_download))
        error_strings.extend(floatAndErrors.error_strings)
        return_message = format_rip_timecode(floatAndErrors.result) 
        if (not len(floatAndErrors.error_strings) 
            and len(return_message) 
            and use_jingle_emoji 
            and floatAndErrors.result <= get_config("jingle_length_in_seconds")
        ):
            return_message = f'{react_type_to_react(ReactType.JINGLE, guild).string} {return_message}'
    return StringAndErrors(return_message, error_strings)


def format_rip(rip: Rip, durationString: str, guild: discord.Guild, make_smol: bool, spec_overdue_days: int, overdue_days: int) -> str:

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
        elif react_is(ReactType.CHECK, react_and_user.name):
            num_checks += 1
        elif react_is(ReactType.REJECT, react_and_user.name):
            num_rejects += 1
        elif react_is_one([ReactType.FIX, ReactType.ALERT], react_and_user.name):
            fix_or_alert = True
        elif react_is(ReactType.STOP, react_and_user.name):
            specs_needed = True
        elif react_is_category(ReactCategory.CHECKREQ, react_and_user.name):
            try:
                checks_required = int(react_and_user.name.split("check")[0])
            except ValueError:
                print("Error parsing checkreq react: {}".format(react_and_user.name))
        elif react_is_category(ReactCategory.NUMBER, react_and_user.name):
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

    info_body = ""
    if len(reacts):
        info_body += f'{reacts} '

    timestamp = datetime_to_relative_timestamp(rip.created_at)
    info_body += f'{author} {timestamp}'
    if len(indicator) > 0:
        info_body += f' {indicator}'

    if len(durationString):
        info_body += f' `{durationString}`'

    if make_smol:
        return f'-# {title_body} {info_body}\n'
    else:
        return f'{title_body}\n{info_body}\n'


async def format_suborqueue_rips(rips: list[Rip], channel_id_order: list[int], included_react_name: str, guild: Guild) -> StringAndErrors:
    text = ""
    error_strings = []

    channel_id_order_display = []
    channel_id_order_display.extend(channel_id_order)

    rip_dict: dict[int, list[Rip]] = {}
    for rip in rips:
        if rip.channel_id not in rip_dict:
            rip_dict[rip.channel_id] = []
            if rip.channel_id not in channel_id_order_display:
                channel_id_order_display.append(rip.channel_id)
        rip_dict[rip.channel_id].append(rip)

    display_emoji = reaction_name_to_emoji_string(included_react_name, guild)

    for channel_id in channel_id_order_display:
        if channel_id in rip_dict:
            text += f'<#{channel_id}>:\n'
            for rip in rip_dict[channel_id]:
                string_and_errors = await get_formatted_rip_length(rip.text, False, False, guild)  
                error_strings.extend(string_and_errors.error_strings)
                if len(string_and_errors.string):
                    text += f"`{string_and_errors.string}` "
                else:
                    text += '`??:??` '

                shown_react_types = [
                    ReactType.ALERT,
                    ReactType.STOP,
                    ReactType.JINGLE,
                    ReactType.QOC,
                    ReactType.THUMBNAIL,
                    ReactType.CHECK,
                    ReactType.METADATA,
                    ReactType.EMAILSENT,
                    ReactType.SENDBACK,
                    ReactType.CALENDAR,
                ]

                display_react_type = react_name_to_react_type(included_react_name)
                if display_react_type not in shown_react_types:
                    text += f"{display_emoji} "

                for react_type in shown_react_types:
                    if rip_has_react([react_type], rip):
                        react = react_type_to_react(react_type, guild)
                        text += f"{react.string} "

                rip_title = get_rip_title(rip.text)
                rip_link = format_message_link(guild.id, rip.channel_id, rip.message_id)
                text += f'**[{rip_title}]({rip_link})**\n'

            text += '------------------------------\n'

    return StringAndErrors(text, error_strings)

async def sort_rips_by_duration(rips: list[Rip]) -> list[str]:

    class RipAndDuration(NamedTuple):
        rip: Rip
        duration: float

    rips_and_durations = []
    error_strings = []

    for rip in rips:
        urls = extract_rip_link(rip.text)
        if len(urls):
            floatAndErrors = await get_rip_url_length(urls[0], GetRipUrlLengthDesc())
            if not len(floatAndErrors.error_strings):
                rips_and_durations.append(RipAndDuration(rip, floatAndErrors.result))
            else:
                error_strings.extend(floatAndErrors.error_strings)
    
    rips_and_durations.sort(key = lambda x: x.duration)
    rips.clear()
    for rip_and_duration in rips_and_durations:
        rips.append(rip_and_duration.rip)

    return error_strings
