
import discord
from discord import TextChannel, Thread, Guild
from datetime import datetime, timezone, timedelta

from bot_secrets import YOUTUBE_API_KEY, YOUTUBE_CHANNEL_NAME, PLAYLISTS_SPREADSHEET_ID
from hq_qoc import ffmpegExists, getFileMetadataMutagen, getFileMetadataFfprobe 
from hq_metadata import isDupe
from hq_source import search_rip_sources 

from hq_types import *
from hq_config import *
from hq_discord import *
from hq_youtube import *
from hq_embed import *
from hq_react import *
from hq_rip import *
from hq_vet import * 
from hq_sheets import * 
from hq_database import * 
from hq_playlist import * 
from hq_specialist import * 

import re
import typing
from typing import NamedTuple, List, Tuple
from enum import Enum, auto
import json
import os
import random
import dateparser
import pytz

class CommandType(Enum):
    NULL = auto()
    QOC = auto()
    SUBS = auto()
    QUEUE = auto()
    STATS = auto()
    ANALYZE = auto()
    SOURCE = auto()
    UPLOAD_MISTAKES = auto()
    PLAYLIST = auto()
    REMIND = auto()
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
COMMAND_TYPE_DATA[CommandType.SOURCE] = CommandTypeData('search for rip sources from online VGM databases')
COMMAND_TYPE_DATA[CommandType.UPLOAD_MISTAKES] = CommandTypeData('commands related to reported upload mistakes')
COMMAND_TYPE_DATA[CommandType.PLAYLIST] = CommandTypeData('assists with sorting YouTube playlists')
COMMAND_TYPE_DATA[CommandType.REMIND] = CommandTypeData('for scheduling reminders')
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


@command(
    command_type=CommandType.MANAGEMENT,
    public=True,
    brief="Get info on all commands",
    format="[command]",
    aliases=['commands', 'halp', 'test', 'helpme', 'h'],
)
async def help(args: list[str], command_context: CommandContext):

    result = "" 

    prefix = get_config("prefix")
    qoc_react = react_type_to_react(ReactType.QOC, command_context.channel.guild)

    if len(args):

        search_input = args[0].lower()

        command_info = find_command_info(search_input)
        if not command_info:
            return await send(f'No command named `{prefix}{search_input}`', command_context.channel)

        if command_info.command_type == CommandType.SECRET:
            text = random.choice(["What?", "Hm?", "Excuse me?", "Um?", "Huh?", "...what?", "Uh...", "Uh", "Ehh?"]) 
            return await send(text, command_context.channel)

        title = f'{prefix}{command_info.name} {command_info.format}'

        brief = parse_emojis_in_string(command_info.brief, command_context.channel.guild)
        desc = f':small_blue_diamond: __**Description**__: {brief}' 

        if len(command_info.desc):
            details = parse_emojis_in_string(command_info.desc, command_context.channel.guild)
            desc += f'\n:small_blue_diamond: __**Details**__: {details}'

        if not command_info.public:
            desc += f'\n\n{qoc_react.string} *Only accessible in QoC channels.*' 
        if command_info.admin:
            desc += f'\n\n:nerd: *Only accessible by admins of this Discord server.*' 

        desc += '\n'

        if len(command_info.aliases):
            desc += '\n__*Aliases:*__ '
        for alias in command_info.aliases:
            desc += f'`{prefix}{alias}` ' 

        if len(command_info.examples):
            desc += '\n__*Examples:*__: '
        for example in command_info.examples:
            desc += f'\n- `{prefix}{command_info.name} {example}` ' 

        return await send_embed(desc, command_context.channel, EmbedDesc(title=title))
        
    else:

        for enum in CommandType:

            if enum == CommandType.SECRET or enum == CommandType.NULL: 
                continue

            assert enum in COMMAND_TYPE_DATA

            result += f'\n\n:small_blue_diamond: __**{enum.name}**__ — *{COMMAND_TYPE_DATA[enum].desc}*'

            for name, info in COMMANDS.items():

                if info.command_type == enum:

                    result += '\n'

                    if not info.public:
                        result += f'{qoc_react.string} '

                    result += f'**{prefix}{name}**'

                    if len(info.format):
                        result += f' `{info.format}`:'
                    else:
                        result += f':'

                    if len(info.brief):
                        brief = parse_emojis_in_string(info.brief, command_context.channel.guild)
                        result += f' {brief}'

        qoc_channels_strings = []
        channel_and_errors= await get_channels_of_types(['QOC', 'PROXY_QOC'], [])
        for channel in channel_and_errors.channels:
            qoc_channels_strings.append(channel.jump_url)

        result += '\n\n__**Legend:**__'
        result += '\n`<argument>`: Required argument'
        result += '\n`[argument]`: Optional argument'
        result += '\n`<argument1 | arguemnt2>`: Either argument1 or argument2 is valid input'
        result += f'\n{qoc_react.string}: Command only accessible in QoC channels:'
        result += f'\n{" ".join(qoc_channels_strings)}'
        result += f'\n\n*To learn more about a command, use `{prefix}help <command>`*'

        await send_embed(result, command_context.channel, EmbedDesc(seperator='\n\n', title="Commands"))

# ============ Roundup commands ============== #

def choose_random_rips(rips: List[Rip], random_count: int, parsed_search_input: ParsedSearchInput, is_search_author: bool) -> List[int]:
    result = []
    valid_rips = rips
    if len(parsed_search_input.search_keys) or len(parsed_search_input.regex_search_keys):
        valid_rips = []
        for rip in rips:
            text = ""
            if is_search_author:
                text = get_raw_rip_author(rip.text)
            else:
                text = get_rip_title(rip.text)
            if text and search_with_parsed_input(text, parsed_search_input):
                valid_rips.append(rip)

    random.shuffle(valid_rips)
    ##NOTE: (Ahmayk) consider default 0 as 1, clamp by rip size
    clamped_count = min(max(1, random_count), len(valid_rips))
    for i in range(clamped_count):
        result.append(valid_rips[i].message_id)
    return result

class RoundupFilterType(Enum):
    NULL = auto()
    MYPINS = auto()
    MYFIXES = auto()
    NOFIXES = auto()
    MYFRESH = auto()
    MYCHECKS = auto()
    MYREJECTS = auto()
    FRESH = auto()
    SPICY = auto()
    SEARCH_TITLE = auto()
    SEARCH_AUTHOR = auto()
    SEARCH_REACTION = auto()
    HASREACT = auto()
    NOTHASREACT = auto()
    UNSENTFIXES = auto()
    UNSENTTEAMFIXES = auto()
    NOSENDBACK = auto()
    OVERDUE = auto()
    RANDOM = auto()
    RANDOM_AUTHOR = auto()
    SAVEQOC = auto()
    MYSAVEQOC = auto()
    SORTBYLENGTH = auto()

class RoundupDesc(NamedTuple):
    roundup_filter_type: RoundupFilterType = RoundupFilterType.NULL
    message_author_id: int = 0 
    message_author_name: str = ""
    user_id: int = 0 
    conditional_string: str = ""
    parsed_search_input: ParsedSearchInput = ParsedSearchInput([], [], False, "", "") 
    react_name: str = "" 
    reaction_type: ReactType = ReactType.NULL 
    not_found_message: str = ""
    parsed_random_input: ParsedRandomInput = ParsedRandomInput(0, ParsedSearchInput([], [], False, "", ""), "", False, "")

async def send_roundup(roundup_desc: RoundupDesc, command_context: CommandContext):
    """
    Sends a roundup message of all rips that the roundup channel the message was sent in points to.
    The roundup_filter_type describes how the roundup will be filtered.
    """

    channel_and_errors = await get_qoc_channel(command_context.channel)
    if len(channel_and_errors.error_strings):
        return await send_if_errors("Roundup failed, QoC Channel not found.", channel_and_errors.error_strings, command_context.channel)
    if not channel_and_errors.channel:
        return await send("ERROR: Channel not found.", command_context.channel)

    get_rips_desc = GetRipsDesc(typing_channel=command_context.channel)
    rips_and_errors = await get_rips(channel_and_errors.channel, get_rips_desc)
    error_strings = rips_and_errors.error_strings
    rips = rips_and_errors.rips

    spec_overdue_days = get_config('spec_overdue_days')
    overdue_days = get_config('overdue_days')

    ##TODO: (Ahmayk) fuzzy username input (ie typing "ahmayk" and matching to their username or display name)
    user_id = command_context.user.id

    selected_rip_message_ids = [] 
    if roundup_desc.roundup_filter_type == RoundupFilterType.RANDOM:
        selected_rip_message_ids = choose_random_rips(rips_and_errors.rips, 
                                                      roundup_desc.parsed_random_input.random_count,
                                                      roundup_desc.parsed_random_input.parsed_search_input, 
                                                      False)

    if roundup_desc.roundup_filter_type == RoundupFilterType.RANDOM_AUTHOR:
        selected_rip_message_ids = choose_random_rips(rips_and_errors.rips, 
                                                      roundup_desc.parsed_random_input.random_count,
                                                      roundup_desc.parsed_random_input.parsed_search_input, 
                                                      True)


    if roundup_desc.roundup_filter_type == RoundupFilterType.SORTBYLENGTH:
        errors = await sort_rips_by_duration(rips)
        error_strings.extend(errors)

    readability_line = "━━━━━━━━━━━━━━━━━━\n"

    result = ""

    valid_count = 0
    for rip in rips:

        vet_reacts = ""

        is_valid = True
        match (roundup_desc.roundup_filter_type):
            case RoundupFilterType.MYPINS:
                is_valid = rip.message_author_id == roundup_desc.message_author_id
            case RoundupFilterType.MYFIXES:
                bool_and_errors = await user_is_react(UserReactCheckType.FIX, user_id, rip, command_context.channel)
                is_valid = bool_and_errors.result 
                error_strings.extend(bool_and_errors.error_strings)
            case RoundupFilterType.NOFIXES:
                is_valid = not rip_has_react(FIX_REACT_LIST, rip)
            case RoundupFilterType.MYFRESH:
                bool_and_errors = await user_is_react(UserReactCheckType.REVIEW, user_id, rip, command_context.channel)
                is_valid = not bool_and_errors.result 
                error_strings.extend(bool_and_errors.error_strings)
            case RoundupFilterType.MYCHECKS:
                bool_and_errors = await user_is_react(UserReactCheckType.CHECK, user_id, rip, command_context.channel)
                is_valid = bool_and_errors.result 
                error_strings.extend(bool_and_errors.error_strings)
            case RoundupFilterType.MYREJECTS:
                bool_and_errors = await user_is_react(UserReactCheckType.REJECT, user_id, rip, command_context.channel)
                is_valid = bool_and_errors.result 
                error_strings.extend(bool_and_errors.error_strings)
            case RoundupFilterType.FRESH:
                is_valid = not rip_has_react(REVIEW_REACT_LIST, rip)
            case RoundupFilterType.SPICY:
                is_valid = rip_has_react([ReactType.CHECK], rip) and \
                            rip_has_react([ReactType.REJECT], rip)
            case RoundupFilterType.SEARCH_TITLE:
                is_valid = False 
                title = get_rip_title(rip.text)
                if title:
                    is_valid = search_with_parsed_input(title, roundup_desc.parsed_search_input) 
            case RoundupFilterType.SEARCH_AUTHOR:
                author = get_rip_author(rip.text, rip.message_author_name)
                is_valid = search_with_parsed_input(author, roundup_desc.parsed_search_input) 
            case RoundupFilterType.HASREACT:
                is_valid = rip_has_react([roundup_desc.reaction_type], rip)
            case RoundupFilterType.NOTHASREACT:
                is_valid = not rip_has_react([roundup_desc.reaction_type], rip)
            case RoundupFilterType.UNSENTFIXES:
                is_valid = rip_has_react([ReactType.FIX], rip) and not rip_has_react([ReactType.SENDBACK], rip)
            case RoundupFilterType.UNSENTTEAMFIXES:
                is_valid = False
                is_unsent_fix = rip_has_react([ReactType.FIX], rip) and not rip_has_react([ReactType.SENDBACK], rip)
                if is_unsent_fix:
                    author = get_rip_author(rip.text, rip.message_author_name)
                    is_valid = not search_with_parsed_input(author, parse_search_input(["email"])) 
            case RoundupFilterType.NOSENDBACK:
                is_valid = not rip_has_react([ReactType.SENDBACK], rip) 
            case RoundupFilterType.SEARCH_REACTION:
                is_valid = False 
                for react in rip.reacts:
                    if roundup_desc.react_name == react.name:
                        is_valid = True 
                        break
            case RoundupFilterType.OVERDUE:
                is_overdue = (datetime.now(timezone.utc) - rip.created_at) > timedelta(days=overdue_days)
                is_valid = is_overdue
            case RoundupFilterType.RANDOM:
                is_valid = rip.message_id in selected_rip_message_ids
            case RoundupFilterType.RANDOM_AUTHOR:
                is_valid = rip.message_id in selected_rip_message_ids
            case RoundupFilterType.SAVEQOC:
                is_valid = rip_is_one_check_away_from_accept(rip) 
            case RoundupFilterType.MYSAVEQOC:
                is_valid = False
                if rip_is_one_check_away_from_accept(rip):
                    bool_and_errors = await user_is_react(UserReactCheckType.CHECK, user_id, rip, command_context.channel)
                    is_valid = not bool_and_errors.result 

        if is_valid:
            string_and_errors = await get_formatted_rip_length(rip.text, False, False, command_context.channel.guild)
            error_strings.extend(string_and_errors.error_strings)

            result += format_rip(rip, string_and_errors.string, command_context.channel.guild, False, spec_overdue_days, overdue_days) + vet_reacts
            result += readability_line 
            valid_count += 1

    if result != "":
        footer = f'#{channel_and_errors.channel.name}   -   {valid_count} of {len(rips)} Rips'
        await send_embed(result, command_context.channel, EmbedDesc(expires=True, seperator=readability_line, footer=footer))
        await send_if_errors("Roundup had errors", error_strings, command_context.channel)
    else:
        not_found_message = "No rips."
        if len(roundup_desc.not_found_message):
            not_found_message = roundup_desc.not_found_message
        await send_and_if_errors(not_found_message, "Roundup had errors.", error_strings, command_context.channel)


@command(
    command_type=CommandType.QOC,
    aliases = ['down_taunt', 'qoc', 'qocparty', 'roudnup', 'links', 'list', 'ls'],
    brief="Show all QoC rips",
)
async def roundup(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc()
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips you've pinned :pushpin:",
)
async def mypins(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYPINS,
                               message_author_id = command_context.user.id, not_found_message = "No pins are yours.")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips you've wrenched :fix: :alert:",
    aliases=['mywrenches'],
)
async def myfixes(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYFIXES, 
                               user_id = command_context.user.id, not_found_message="No fixes are yours.")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips you've not reviewed",
)
async def myfresh(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYFRESH, \
                               user_id = command_context.user.id, not_found_message = "You have nothing to reveiw! Good work!")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips you've approved :check:",
    aliases=["myapproved"]
)
async def mychecks(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYCHECKS, \
                               user_id = command_context.user.id, not_found_message = "No checks are yours.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips you've rejected :reject:",
    aliases=["myrejected"]
)
async def myrejects(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYREJECTS, \
                               user_id = command_context.user.id, not_found_message = "No rejects are yours.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    aliases = ['blank', 'bald', 'clean', 'noreacts'],
    brief="Show QoC rips nobody's reviewed",
)
async def fresh(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.FRESH, \
            not_found_message = "No fresh rips.")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips with at least one :check: and one :reject:",
)
async def spicy(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SPICY, \
            not_found_message = "No spicy rips :(")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips where one :check: would approve the rip",
    desc="Shows rips that have two more checks than rejects, no fixes or alerts, and no stops unaccompanied by a goldencheck."
)
async def saveqoc(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SAVEQOC, \
            not_found_message = "No rips can be approved with only one check. All is lost!")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips where your new :check: would approve the rip",
    desc="Shows rips you havne't checked that have two more checks than rejects, no fixes or alerts, and no stops unaccompanied by a goldencheck."
)
async def mysaveqoc(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYSAVEQOC, \
            not_found_message = "You cannot approve any rips with only one check!")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    format="[NOT] <search text | regex>",
    brief="Search QoC rip titles",
    desc="Does not need quotes. Include NOT to search for rips that don't inlude the searched input.",
    examples=["Deltarune", "PAL", "Mother 3"]
)
async def search(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Include what you want to search for! I'll search for it in the titles of QoC rips.", \
                           command_context.channel)

    parsed_search_input = parse_search_input(args)
    if len(parsed_search_input.invalid_input_error_string):
        return await send(parsed_search_input.invalid_input_error_string, command_context.channel)

    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SEARCH_TITLE, \
            parsed_search_input=parsed_search_input,\
            not_found_message = f'No rips {parsed_search_input.containing_error_string} in title found.')
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="Show QoC email rips",
    aliases=["email"]
)
async def emails(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SEARCH_AUTHOR, \
            parsed_search_input = parse_search_input(["email"]),\
            not_found_message = "No emails.")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    format="[NOT] <event name>",
    brief="Search QoC event rips",
    aliases=["event"],
    desc="This can also be used as a general purpose author line search tool. Does not need quotes. Include NOT to search for rips that don't inlude the searched input.",
    examples=["christmas", "secret", "deez nuts day", "ahmayk"]
)
async def events(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please include the event name tagged in rips.", \
                           command_context.channel)

    parsed_search_input = parse_search_input(args)
    if len(parsed_search_input.invalid_input_error_string):
        return await send(parsed_search_input.invalid_input_error_string, command_context.channel)

    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SEARCH_AUTHOR, \
            parsed_search_input=parsed_search_input,\
            not_found_message = f'No rips {parsed_search_input.containing_error_string} in author line.')
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips with :check:",
    desc="does not consider :goldcheck: or check number requirements such as :7check:",
)
async def checks(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactType.CHECK, not_found_message="No checks found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips without :check:",
    aliases=["nocheck"]
)
async def nochecks(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.NOTHASREACT, \
                               reaction_type=ReactType.CHECK, not_found_message="No non-checked rips found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips with :reject:",
)
async def rejects(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactType.REJECT, not_found_message="No rejected rips found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips without :reject:",
    aliases=["noreject"]
)
async def norejects(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.NOTHASREACT, \
                               reaction_type=ReactType.REJECT, not_found_message="No non-rejected rips found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips with :fix:",
    aliases=['wrenches', 'fix', 'wrench', 'fixes']
)
async def fixes(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactType.FIX, not_found_message="No wrenches found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips without :fix:",
    aliases=["nowrenches", "nofix", "nowrench"]
)
async def nofixes(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.NOTHASREACT, \
                               reaction_type=ReactType.FIX, not_found_message="No non-fix rips found.")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips with :fix: and not :sendback:",
    aliases=['unsentwrenches']
)
async def unsentfixes(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.UNSENTFIXES, \
                               not_found_message="No unsent fixes found. Good job!")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="Show non-email QoC rips with :fix: and not :sendback:",
    aliases=['unsentteamwrenches', 'unsentfixesteam', 'unsentwrenchesteam']
)
async def unsentteamfixes(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.UNSENTTEAMFIXES, \
                               not_found_message="No non-email unsent fixes found. Good job, team!")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips without :sendback:",
    aliases=['unsentwrenches']
)
async def nosendback(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.NOSENDBACK, \
                               not_found_message="No non-senback rips. Time to wait?")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips with :stop:",
)
async def stops(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactType.STOP, not_found_message="No octogons found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    format="<emoji>",
    brief="Show QoC rips with an inputted react",
    examples=[":fire:", ":qoc:", ":sob:"],
    aliases=["has_react"]
)
async def hasreact(args: list[str], command_context: CommandContext):

    if not len(args): 
        return await send("Error: Please include an emoji to search for. I'll show rips reacted by that emoji", command_context.channel)

    react_input = emoji_to_react_name_if_emoji(args[0])

    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SEARCH_REACTION, \
                               react_name=react_input, not_found_message=f'No rips with {args[0]} found.')
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips with a jingle emoji :jinglebell:",
    aliases=["jingle"]
)
async def jingles(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactType.JINGLE,
                               not_found_message=f'No jingle rips found.')
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief="Show QoC rips sorted by rip length",
    aliases=['sortlength', 'sortripsbylength']
)
async def sortbylength(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SORTBYLENGTH, \
                               not_found_message=f'Length of all QoC rips: **Zero Seconds!**')
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief=f'Show QoC rips pinned over %overdue_days% days'
)
async def overdue(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.OVERDUE, not_found_message="No overdue rips.")
    await send_roundup(roundup_desc, command_context)


async def roundup_random(roundup_filter_type: RoundupFilterType, args: list[str], command_context: CommandContext):
    search_line_string = "title"
    if roundup_filter_type == RoundupFilterType.RANDOM_AUTHOR:
        search_line_string = "author line"

    parsed_random_input = await parse_random_input(args, search_line_string)
    if len(parsed_random_input.invalid_input_error_string):
        if parsed_random_input.invalid_input_error_string_is_embed:
            return await send_embed(parsed_random_input.invalid_input_error_string, command_context.channel, EmbedDesc())
        else:
            return await send(parsed_random_input.invalid_input_error_string, command_context.channel)

    roundup_desc = RoundupDesc(roundup_filter_type = roundup_filter_type, \
                               parsed_random_input=parsed_random_input, \
                               not_found_message=parsed_random_input.not_found_error_string)
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    brief=f'Show a random QoC rip, optionally searching title',
    format="[count] [[NOT] <search text | regex>]",
    aliases=['random', 'randomtitle', 'randomqoc', 'random_qoc', 'lucky', 'letsgogambling!']
)
async def randompull(args: list[str], command_context: CommandContext):
    await roundup_random(RoundupFilterType.RANDOM, args, command_context)


@command(
    command_type=CommandType.QOC,
    brief=f'Show a random QoC rip, optionally searching author line',
    format="[count] [[NOT] <search text | regex>]",
    aliases=['randomevent', 'randomauthor']
)
async def random_event(args: list[str], command_context: CommandContext):
    await roundup_random(RoundupFilterType.RANDOM_AUTHOR, args, command_context)


async def current_or_qoc_channel(channel: TextChannel | Thread) -> ChannelAndErrors:
    error_strings = []
    if channel_is_type(channel, 'PROXY_QOC'):
        channel_and_errors = await get_qoc_channel(channel)
        if len(channel_and_errors.error_strings):
            error_strings.extend(channel_and_errors.error_strings)
        if channel_and_errors.channel:
            channel = channel_and_errors.channel
    return ChannelAndErrors(channel, error_strings)


@command(
    command_type=CommandType.QOC,
    public=True,
    brief='Count pinned QoC rips for channel',
    desc='Only counts pinned messages with valid rips.'
)
async def count(args: list[str], command_context: CommandContext):

    channel_and_errors = await current_or_qoc_channel(command_context.channel)
    if len(channel_and_errors.error_strings):
        return await send_if_errors("QoC Channel not found.", channel_and_errors.error_strings, command_context.channel)

    if channel_and_errors.channel:
        rips_and_errors = await get_rips_fast(channel_and_errors.channel, GetRipsDesc(typing_channel=command_context.channel))
        if len(rips_and_errors.error_strings):
            return await send_if_errors("Can't count today. :(", rips_and_errors.error_strings, command_context.channel)

        pincount = len(rips_and_errors.rips)

        if (pincount < 1):
            result = "`* Determination.`"
        else:
            result = f"`* {pincount} left.`"

        if channel_and_errors.channel.id != command_context.channel.id:
            result += f"\n-# Showing results from <#{channel_and_errors.channel.id}>."

        await send(result, command_context.channel)


@command(
    command_type=CommandType.QOC,
    public=True,
    brief='Report proximity to pinlimit for channel',
    desc='Only counts pinned messages with valid rips.',
    aliases=['pinlimit'],
)
async def limitcheck(args: list[str], command_context: CommandContext):

    channel_and_errors = await current_or_qoc_channel(command_context.channel)
    if len(channel_and_errors.error_strings):
        return await send_if_errors("QoC Channel not found.", channel_and_errors.error_strings, command_context.channel)

    if channel_and_errors.channel:
        rips_and_errors = await get_rips_fast(channel_and_errors.channel, GetRipsDesc(typing_channel=command_context.channel))
        if len(rips_and_errors.error_strings):
            return await send_if_errors("idk how to count rn sorry", rips_and_errors.error_strings, command_context.channel)
        count = len(rips_and_errors.rips)
        result = f"You can pin {get_config('soft_pin_limit') - count} more rips until I start complaining about pin space."
        if channel_and_errors.channel.id != command_context.channel.id:
            result += f"\n-# Showing results from <#{channel_and_errors.channel.id}>."
        await send(result, command_context.channel)


@command(
    command_type=CommandType.SUBS,
    public=True,
    format="[channel link]",
    brief='Count # of subs',
    desc=\
    """
    Counts the number of messages in the default submission channel.
    By default chooses the subs channel listed first in this bot's config.
    Also accepts an optional link to a subs channel to count. 
    """
)
##TODO: (Ahmayk) input the channel however you want, id, link, name
async def count_subs(args: list[str], command_context: CommandContext):

    sub_channel_link = ""
    if len(args):
        sub_channel_link = args[0]

    parse_result = await parse_channel_link(sub_channel_link, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'])
    if len(parse_result.error_strings):
        return await send_if_errors("No counting today.", parse_result.error_strings, command_context.channel)
    if len(parse_result.input_error):
        return await send(parse_result.input_error, command_context.channel)

    count = 0
    if parse_result.channel:
        rips_and_errors = await get_rips_fast(parse_result.channel, GetRipsDesc(typing_channel=command_context.channel))
        if len(rips_and_errors.error_strings):
            return await send_if_errors("o noes", rips_and_errors.error_strings, command_context.channel)
        count = len(rips_and_errors.rips)

    if (count < 1):
        result = "```ansi\n\u001b[0;31m* Determination.\u001b[0;0m```"
    else:
        result = f"```ansi\n\u001b[0;31m* {count} left.\u001b[0;0m```"

    await send(result, command_context.channel)


# ============ SubOrQueue Rip Commands ============== #

class SubOrQueueRipFilterType(Enum):
    NULL = auto()
    HASREACT = auto()
    SEARCH_REACTION = auto()
    UNSENT = auto()
    SEARCH_TITLE = auto()
    SEARCH_AUTHOR = auto()
    SCOUT = auto()
    ALL = auto()
    RANDOM = auto()
    RANDOM_AUTHOR = auto()
    SORTBYLENGTH = auto()

class SendSubOrQueueDesc(NamedTuple):
    suborqueue_rip_filter_type: SubOrQueueRipFilterType = SubOrQueueRipFilterType.NULL 
    channels: List[TextChannel | Thread] = []
    channel_types: List[str] = []
    ##NOTE: (Ahmayk) channel_input requires channel_types to define defaults in case of error
    channel_input_args: list[str] = []
    reaction_type: ReactType = ReactType.NULL 
    react_name: str = ""
    parsed_search_input: ParsedSearchInput = ParsedSearchInput([], [], False, "", "") 
    parsed_random_input: ParsedRandomInput = ParsedRandomInput(0, ParsedSearchInput([], [], False, "", ""), "", False, "")
    not_found_message: str = ""

async def send_suborqueue_rips(desc: SendSubOrQueueDesc, command_context: CommandContext):
    """
    Sends a list of rips from either all submission or queue channels according to a filter.
    """
    channels = []
    error_strings = []

    if len(desc.channels):
        channels = desc.channels
    elif len(desc.channel_types):
        if len(desc.channel_input_args): 
            parse_result = await parse_channel_link(desc.channel_input_args[0], desc.channel_types)
            if len(parse_result.input_error):
                return await send_and_if_errors(parse_result.input_error, "Errors happened!", parse_result.error_strings, command_context.channel)
            error_strings = parse_result.error_strings
            if parse_result.channel:
                channels.append(parse_result.channel)
        else:
            channels_and_errors = await get_channels_of_types(desc.channel_types, [])
            channels = channels_and_errors.channels
            error_strings = channels_and_errors.error_strings
    else:
        return await send("Internal Error: Must supply channels or channel types to SendSubOrQueueDesc() (Contact bot maintainer)", command_context.channel) 

    selected_rip_message_ids = [] 
    if (
        desc.suborqueue_rip_filter_type == SubOrQueueRipFilterType.RANDOM 
        or desc.suborqueue_rip_filter_type == SubOrQueueRipFilterType.RANDOM_AUTHOR
    ): 
        temp_rips_all: List[Rip] = []
        for channel in channels:
            temp_rips_and_errors = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
            error_strings.extend(temp_rips_and_errors.error_strings)
            temp_rips_all.extend(temp_rips_and_errors.rips)

        search_by_author = desc.suborqueue_rip_filter_type == SubOrQueueRipFilterType.RANDOM_AUTHOR 
        selected_rip_message_ids = choose_random_rips(temp_rips_all, 
                                                    desc.parsed_random_input.random_count,
                                                    desc.parsed_random_input.parsed_search_input, 
                                                    search_by_author)
    total_count = 0
    filtered_rips: list[Rip] = []
    for channel in channels:
        rips_and_errors = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
        error_strings.extend(rips_and_errors.error_strings)
        rips = rips_and_errors.rips

        if desc.suborqueue_rip_filter_type == SubOrQueueRipFilterType.SORTBYLENGTH:
            errors = await sort_rips_by_duration(rips)
            error_strings.extend(errors)

        for rip in rips_and_errors.rips:

            total_count += 1

            rip_title = get_rip_title(rip.text)
            rip_author = get_raw_rip_author(rip.text)

            is_valid = False
            match(desc.suborqueue_rip_filter_type):
                case SubOrQueueRipFilterType.HASREACT:
                    is_valid = rip_has_react([desc.reaction_type], rip)
                case SubOrQueueRipFilterType.SEARCH_REACTION:
                    for react in rip.reacts:
                        if desc.react_name == react.name:
                            is_valid = True 
                            break
                case SubOrQueueRipFilterType.UNSENT:
                    is_valid = line_contains_substring(rip_author, 'email') and \
                            not rip_has_react([ReactType.EMAILSENT, ReactType.ANTIMAIL], rip)
                case SubOrQueueRipFilterType.SEARCH_TITLE:
                    if rip_title:
                        is_valid = search_with_parsed_input(rip_title, desc.parsed_search_input) 
                case SubOrQueueRipFilterType.SEARCH_AUTHOR:
                    is_valid = search_with_parsed_input(rip_author, desc.parsed_search_input) 
                case SubOrQueueRipFilterType.SCOUT:
                    is_valid = False
                    if rip_title:
                        for key in desc.parsed_search_input.search_keys:
                            if rip_title.lower().startswith(key.lower()):
                                is_valid = True
                                break
                case SubOrQueueRipFilterType.ALL:
                    is_valid = True
                case SubOrQueueRipFilterType.RANDOM:
                    is_valid = rip.message_id in selected_rip_message_ids
                case SubOrQueueRipFilterType.RANDOM_AUTHOR:
                    is_valid = rip.message_id in selected_rip_message_ids
                case SubOrQueueRipFilterType.SORTBYLENGTH:
                    is_valid = True
                case _:
                    assert "Unimplemented SubOrQueueRipFilterType"

            if is_valid:
                filtered_rips.append(rip)

    display_rect_name = "" 
    if desc.suborqueue_rip_filter_type == SubOrQueueRipFilterType.SEARCH_REACTION:
        display_rect_name = desc.react_name

    channel_id_order = []
    for channel in channels:
        channel_id_order.append(channel.id)
    string_and_errors = await format_suborqueue_rips(filtered_rips, channel_id_order, display_rect_name, command_context.channel.guild)
    error_strings.extend(string_and_errors.error_strings)

    if len(filtered_rips)== 0:
        not_found_message = "No rips found."
        if len(desc.not_found_message):
            not_found_message = desc.not_found_message
        await send_and_if_errors(not_found_message, "Errors during getting rips:", error_strings, command_context.channel)
    else:
        footer = f'{len(filtered_rips)} of {total_count} Rips'
        await send_embed(string_and_errors.string, command_context.channel, EmbedDesc(expires=True, footer=footer))
        await send_if_errors("Errors during getting rips:", error_strings, command_context.channel)


@command(
    command_type=CommandType.SUBS,
    public=True,
    format="[NOT] <search text | regex>",
    brief='Search submission rip titles',
    desc="Does not need quotes. Include NOT to search for rips that don't inlude the searched input.",
    aliases=['search_sub'],
)
async def search_subs(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Include what you want to search for! I'll search for it in the titles of submitted rips.", \
                           command_context.channel)

    parsed_search_input = parse_search_input(args)
    if len(parsed_search_input.invalid_input_error_string):
        return await send(parsed_search_input.invalid_input_error_string, command_context.channel)

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SEARCH_TITLE, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'], \
                              parsed_search_input = parsed_search_input, \
                              not_found_message = f'No submissions {parsed_search_input.containing_error_string} in title found.')
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.SUBS,
    public=True,
    format="[NOT] <event text>",
    brief='Search for submission event rips',
    desc="This can also be used as a general purpose author line search tool. Does not need quotes. Include NOT to search for rips that don't inlude the searched input.",
    aliases=['event_sub'],
)
async def event_subs(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please include the event name tagged in submitted rips.", \
                           command_context.channel)

    parsed_search_input = parse_search_input(args)
    if len(parsed_search_input.invalid_input_error_string):
        return await send(parsed_search_input.invalid_input_error_string, command_context.channel)

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SEARCH_AUTHOR, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'], \
                              parsed_search_input = parsed_search_input, \
                              not_found_message = f'No submissions {parsed_search_input.containing_error_string} in author line found.')
    await send_suborqueue_rips(desc, command_context)


async def random_suborqueue(suborqueue_rip_filter_type: SubOrQueueRipFilterType, channel_types: list[str],
                             args: list[str], command_context: CommandContext):

    search_line_string = "title"
    if suborqueue_rip_filter_type == SubOrQueueRipFilterType.RANDOM_AUTHOR:
        search_line_string = "author line"

    parsed_random_input = await parse_random_input(args, search_line_string)
    if len(parsed_random_input.invalid_input_error_string):
        if parsed_random_input.invalid_input_error_string_is_embed:
            return await send_embed(parsed_random_input.invalid_input_error_string, command_context.channel, EmbedDesc())
        else:
            return await send(parsed_random_input.invalid_input_error_string, command_context.channel)

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = suborqueue_rip_filter_type, \
                              parsed_random_input= parsed_random_input, \
                              channel_types = channel_types, \
                              not_found_message = parsed_random_input.not_found_error_string)
    await send_suborqueue_rips(desc, command_context)

@command(
    command_type=CommandType.SUBS,
    public=True,
    format="[count] [[NOT] <search text | regex>]",
    brief=f'Show a random submitted rip, optionally searching title',
    aliases=['randomsub', 'randomqoc', 'luckysub', 'letsgogambling!sub!']
)
async def random_sub(args: list[str], command_context: CommandContext):
    await random_suborqueue(SubOrQueueRipFilterType.RANDOM, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'], args, command_context)


@command(
    command_type=CommandType.SUBS,
    public=True,
    format="[count] [[NOT] <search text | regex>]",
    brief=f'Show a random submitted rip, optionally searching author line',
    desc='Insert a number to roll that many rips.',
    aliases=['random_event_sub', 'randomeventsub', 'randomauthor_sub', 'random_author_sub', 'randomauthorsub']
)
async def randomevent_sub(args: list[str], command_context: CommandContext):
    await random_suborqueue(SubOrQueueRipFilterType.RANDOM_AUTHOR, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'], args, command_context)


@command(
    command_type=CommandType.SUBS,
    format="<emoji>",
    public=True,
    brief="Show submitted rips with an inputted react",
    aliases=["hasreact_subs", "has_react_sub", "has_react_subs"],
    examples=[":fire:", ":qoc:", ":sob:"],
)
async def hasreact_sub(args: list[str], command_context: CommandContext):

    if not len(args): 
        return await send("Error: Please include an emoji to search for. I'll show submitted rips reacted by that emoji", command_context.channel)

    react_input = emoji_to_react_name_if_emoji(args[0])

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SEARCH_REACTION, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'], \
                              react_name = react_input)
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.SUBS,
    brief="Show submitted rips with a jingle emoji :jinglebell:",
    public=True,
    aliases=["jingles_subs", "jingle_sub", "jingle_subs"]
)
async def jingles_sub(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.HASREACT, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'], \
                              reaction_type = ReactType.JINGLE)
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.SUBS,
    format="[channel link]",
    brief="Show subbed rips sorted by rip length",
    aliases=['sortlength_sub', 'sortripsbylength_sub', 'sortlength_subs', 'sortripsbylength_subs'],
)
async def sortbylength_sub(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SORTBYLENGTH, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'],
                              channel_input_args = args)
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    brief='Show queued rips with a "thumbnail needed" react :thumbnail:',
    aliases=['thumbnails'],
)
async def frames(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.HASREACT, \
                              channel_types = ["QUEUE"], \
                              reaction_type = ReactType.THUMBNAIL)
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    brief='Show queued rips with an alert react :alert:',
)
async def alerts(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.HASREACT, \
                              channel_types = ["QUEUE"], \
                              reaction_type = ReactType.ALERT)
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    brief='Show queued rips with a metadata react :metadata:',
)
async def metadata(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.HASREACT, \
                              channel_types = ["QUEUE"], \
                              reaction_type = ReactType.METADATA)
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    brief='Show queued email rips with no emailsent react :emailsent:',
)
async def unsent(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.UNSENT, \
                              channel_types = ["QUEUE"])
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    brief='Search for a specific queued rip title.',
)
async def lookup(args: list[str], command_context: CommandContext):
    def lookup_result(url: str | None, length: str | None, note: str):
        return f"URL: {url}\nLength: {length}\nNote: {note}"
    
    if not len(args):
        return await send(lookup_result(None, None, "Missing search query!"), command_context.channel)

    lookup_title = ' '.join(args)
    lookup_url = None
    error_strings = []
    
    rips_and_errors = await get_rips_of_channel_types(["QUEUE"], command_context.channel)
    error_strings.extend(rips_and_errors.error_strings)
    for rip in rips_and_errors.rips:
        rip_title = get_rip_title(rip.text)
        if rip_title == lookup_title:
            lookup_url = format_message_link(rip.guild_id, rip.channel_id, rip.message_id)
            ##NOTE: (Ahmayk) Not the right guild for how this is actually used but doens't matter
            string_and_errors = await get_formatted_rip_length(rip.text, False, False, command_context.channel.guild)  
            error_strings.extend(string_and_errors.error_strings)
            lookup_note = '\n'.join(error_strings) if len(error_strings) else "All good!"
            return await send(lookup_result(lookup_url, string_and_errors.string, lookup_note), command_context.channel)
    
    lookup_note = '\n'.join(error_strings) if len(error_strings) else "Rip not found!"
    return await send(lookup_result(None, None, lookup_note), command_context.channel)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    format="[NOT] <search text | regex>",
    brief='Search queued rip titles',
    desc="Does not need quotes. Include NOT to search for rips that don't inlude the searched input.",
    aliases=['search_queue', 'search_queues', 'search_qs'],
)
async def search_q(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Include what you want to search for! I'll search for it in the titles of accepted queued rips.", \
                           command_context.channel)

    parsed_search_input = parse_search_input(args)
    if len(parsed_search_input.invalid_input_error_string):
        return await send(parsed_search_input.invalid_input_error_string, command_context.channel)

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SEARCH_TITLE, \
                              channel_types = ['QUEUE'], \
                              parsed_search_input=parsed_search_input, \
                              not_found_message = f'No submissions {parsed_search_input.containing_error_string} in title found.')
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    format="[NOT] <event text>",
    brief='Search for queued event rips',
    desc="This can also be used as a general purpose author line search tool. Does not need quotes. Include NOT to search for rips that don't inlude the searched input.",
    aliases=['event_queue', 'event_queues', "event_qs"],
)
async def event_q(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please include the event name tagged in queued rips.", \
                           command_context.channel)

    parsed_search_input = parse_search_input(args)
    if len(parsed_search_input.invalid_input_error_string):
        return await send(parsed_search_input.invalid_input_error_string, command_context.channel)

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SEARCH_AUTHOR, \
                              channel_types = ['QUEUE'], \
                              parsed_search_input= parsed_search_input, \
                              not_found_message = f'No submissions {parsed_search_input.containing_error_string} in author line found.')
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    format="<emoji>",
    brief="Show queued rips with an inputted react",
    examples=[":fire:", ":check:", ":sob:"],
    aliases=["has_react_q"]
)
async def hasreact_q(args: list[str], command_context: CommandContext):

    if not len(args): 
        return await send("Error: Please include an emoji to search for. I'll show queued rips reacted by that emoji", command_context.channel)

    react_input = emoji_to_react_name_if_emoji(args[0])

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SEARCH_REACTION, \
                              channel_types = ['QUEUE'], \
                              react_name = react_input)
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    brief="Show queued rips with a jingle emoji :jinglebell:",
    public=True,
    aliases=["jingle_q"]
)
async def jingles_q(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.HASREACT, \
                              channel_types = ['QUEUE'], \
                              reaction_type = ReactType.JINGLE)
    await send_suborqueue_rips(desc, command_context)

@command(
    command_type=CommandType.QUEUE,
    format="[channel link]",
    brief="Show queued rips sorted by rip length",
    aliases=['sortlength_q', 'sortripsbylength_q'],
)
async def sortbylength_q(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SORTBYLENGTH, \
                              channel_types = ['QUEUE'],
                              channel_input_args = args)
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    format="<prefix> NOT",
    brief='Search queued rips starting with prefix',
    desc='The prefix can contain spaces. Include NOT to search for rips that don\'t start with the input.',
    examples=['e', 'Level', 'deez nuts', 'NOT deez nuts']
)
async def scout(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please provide a prefix. I'll find approved rips that start with it.", \
                           command_context.channel)

    parsed_search_input = parse_search_input(args)
    if len(parsed_search_input.invalid_input_error_string):
        return await send(parsed_search_input.invalid_input_error_string, command_context.channel)

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SCOUT, \
                              channel_types = ['QUEUE'], \
                              parsed_search_input = parsed_search_input, \
                              not_found_message = f'No approved rips {parsed_search_input.containing_error_string} in their first letters found.')
    await send_suborqueue_rips(desc, command_context)

@command(
    command_type=CommandType.QUEUE,
    public=True,
    format="[channel_link]",
    brief='Tally queued rips via first letter in title',
    desc='Optionally takes a channel link as an argument to tally only that queue channel.',
)
async def scout_stats(args: list[str], command_context: CommandContext):

    channel_link = ""
    if len(args):
        channel_link = args[0]

    parse_result = await parse_channel_link(channel_link, ['QUEUE'])
    if len(parse_result.error_strings):
        return await send_if_errors("No scouting today.", parse_result.error_strings, command_context.channel)
    if len(parse_result.input_error):
        return await send(parse_result.input_error, command_context.channel)

    rips = []
    if parse_result.channel:
        rips_and_errors = await get_rips_fast(parse_result.channel, GetRipsDesc(typing_channel=command_context.channel))
        if len(rips_and_errors.error_strings):
            return await send_if_errors("Scout died.", rips_and_errors.error_strings, command_context.channel)
        rips = rips_and_errors.rips

    count = {}
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ': # could have done string.ascii_uppercase but i dont think the alphabet is getting any updates
        count[letter] = 0

    for rip in rips:
        rip_title = get_raw_rip_title(rip.text)
        if rip_title:
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

    if len(rips_and_errors.rips) == 0:
        await send("No approved rips found.", command_context.channel)
    else:
        await send_embed(result, command_context.channel, EmbedDesc(expires=True))


async def send_vibes(rips: List[Rip], max_emojis: int, command_context: CommandContext):
    react_dict: dict[str, int] = {}

    for rip in rips:
        react_counts = get_react_counts(rip)
        for react in react_counts.keys():
            if react.name and len(react.name):
                if react.name not in react_dict:
                    react_dict[react.name] = 0
                react_dict[react.name] += react_counts[react] 

    min = 0
    if max_emojis != 0:
        while len(react_dict.keys()) > max_emojis:
            min += 1
            _temp_dict = {} 
            for k, v in react_dict.items():
                if v > min:
                    _temp_dict[k] = v
            react_dict = _temp_dict
    
    if len(react_dict):
        result = ""

        if min > 0:
            result += f'*Showing reactions with minimum **{min}** occurances.*\n\n'

        maxCount = max(max(react_dict.values()), 20)
        for k, v in sorted(react_dict.items(), key=lambda item: item[1], reverse=True):
            emoji_stirng = reaction_name_to_emoji_string(k, command_context.channel.guild)
            result += f"{emoji_stirng}: " + ("▮" * int(v / maxCount * 20)) + f" ({v})\n"

        await send_embed(result, command_context.channel, EmbedDesc(expires=True))
    else:
        await send("zero vibes yey we chillin", command_context.channel)

@command(
    command_type=CommandType.QOC,
    brief='Tally the vibes of QoC rips',
    format='[all]',
    desc='Only shows 40 reactions at most. Include `all` or `a` to show everything.',
    aliases=['vibes', 'reacts', 'stats_reacts', 'stats_react', 'react_stats', 'howbadisit', 'howgoodisit'],
)
async def vibecheck(args: list[str], command_context: CommandContext):

    channel_and_errors = await get_qoc_channel(command_context.channel)
    if len(channel_and_errors.error_strings):
        return await send_if_errors("Vibes are way off today.", channel_and_errors.error_strings, command_context.channel)
    if not channel_and_errors.channel:
        return await send("ERROR: Channel not found.", command_context.channel)

    rips_and_errors = await get_rips(channel_and_errors.channel, GetRipsDesc(typing_channel=command_context.channel))
    if len(rips_and_errors.error_strings):
        return await send_if_errors("no vibes. only sad. all is lost. aaa", rips_and_errors.error_strings, command_context.channel)

    await send_vibes(rips_and_errors.rips, 0, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    format='[all]',
    brief='Tally the vibes of queued rips',
    desc='Only shows 30 reactions at most. Include `all` or `a` to show everything.',
    aliases=['vibes_q', 'reacts_q', 'stats_reacts_q', 'stats_react_q', 'react_stats_q', 'howbadisit_q', 'howgoodisit_q'],
)
async def vibecheck_q(args: list[str], command_context: CommandContext):
    max = 30
    if len(args) > 0:
        max = 0 
    rips_and_errors = await get_rips_of_channel_types(["QUEUE"], command_context.channel)
    if len(rips_and_errors.error_strings):
        return await send_if_errors("The vibes are bad!", rips_and_errors.error_strings, command_context.channel)
    await send_vibes(rips_and_errors.rips, max, command_context)


@command(
    command_type=CommandType.SUBS,
    public=True,
    brief='Tally the vibes of submitted rips',
    desc='Only shows 30 reactions at most. Include `all` or `a` to show everything.',
    aliases=['vibes_subs', 'reacts_subs', 'stats_reacts_subs', 'stats_react_subs', 'react_stats_subs', 'howbadisit_subs', 'howgoodisit_subs'],
)
async def vibecheck_subs(args: list[str], command_context: CommandContext):
    max = 30
    if len(args) > 0:
        max = 0 
    rips_and_errors = await get_rips_of_channel_types(['SUBS', 'SUBS_THREAD', 'SUBS_PIN'], command_context.channel)
    if len(rips_and_errors.error_strings):
        return await send_if_errors("The vibes are bad!", rips_and_errors.error_strings, command_context.channel)
    await send_vibes(rips_and_errors.rips, max, command_context)


async def send_viberank(rips: List[Rip], react_name: str, max_rips: int, command_context: CommandContext):

    class EmojiCount(NamedTuple):
        rip: Rip
        num_emojis: int

    emoji_count_list: List[EmojiCount] = [] 

    for rip in rips:
        react_counts = get_react_counts(rip)
        for react in react_counts.keys():
            if react.name and len(react.name) and react.name == react_name:
                emoji_count_list.append(EmojiCount(rip, react_counts[react]))

    emoji_count_list.sort(key=lambda e: e.num_emojis, reverse=True)

    if len(emoji_count_list):
        result = ""
        emoji_count_list_most = emoji_count_list 
        if max_rips > 0:
            emoji_count_list_most = emoji_count_list_most[:max_rips]
        if len(emoji_count_list_most) < len(emoji_count_list):
            result += f'*Showing top **{len(emoji_count_list_most)}** rips with {react_name}.*\n\n'

        emoji_stirng = reaction_name_to_emoji_string(react_name, command_context.channel.guild)
        for emoji_count in emoji_count_list_most:
            rip_title = get_rip_title(emoji_count.rip.text)
            rip_link = format_message_link(rip.guild_id, emoji_count.rip.channel_id, emoji_count.rip.message_id)
            result += f'{emoji_stirng} **x{emoji_count.num_emojis}**: **[{rip_title}]({rip_link})**\n'

        await send_embed(result, command_context.channel, EmbedDesc(expires=True))
    else:
        await send(f'zero {react_name} we chillin', command_context.channel)

@command(
    command_type=CommandType.QOC,
    brief='Ranks QoC rips with the most of a react.',
    format='<emoji> [all]',
    desc='Only shows 30 rips at most. Include `all` or `a` to show everything.',
    aliases=['reactrank'],
)
async def viberank(args: list[str], command_context: CommandContext):

    if not len(args): 
        return await send("Error: Please include an emoji to search for. I'll show QoC rips that have the most of that emoji", command_context.channel)

    channel_and_errors = await get_qoc_channel(command_context.channel)
    if len(channel_and_errors.error_strings):
        return await send_if_errors("Vibes are way off today.", channel_and_errors.error_strings, command_context.channel)
    if not channel_and_errors.channel:
        return await send("ERROR: Channel not found.", command_context.channel)

    react_input = emoji_to_react_name_if_emoji(args[0])

    max = 30
    if len(args) > 1:
        max = 0 

    rips_and_errors = await get_rips(channel_and_errors.channel, GetRipsDesc(typing_channel=command_context.channel))
    if len(rips_and_errors.error_strings):
        return await send_if_errors("o noes. Rank is error.", rips_and_errors.error_strings, command_context.channel)

    await send_viberank(rips_and_errors.rips, react_input, max, command_context)

@command(
    command_type=CommandType.QUEUE,
    public=True,
    format='<emoji> [all]',
    brief='Rank queued rips with the most of a react',
    desc='Only shows 30 rips at most. Include `all` or `a` to show everything.',
    aliases=['viberank_queue', 'viberank_queues', 'reactrank_q', 'reactrank_queue', 'reactrank_queues'],
)
async def viberank_q(args: list[str], command_context: CommandContext):

    if not len(args): 
        return await send("Error: Please include an emoji to search for. I'll show queued rips that have the most of that emoji", command_context.channel)

    react_input = emoji_to_react_name_if_emoji(args[0])

    max = 30
    if len(args) > 1:
        max = 0 

    rips_and_errors = await get_rips_of_channel_types(["QUEUE"], command_context.channel)
    if len(rips_and_errors.error_strings):
        return await send_if_errors("The vibes are bad!", rips_and_errors.error_strings, command_context.channel)
    await send_viberank(rips_and_errors.rips, react_input, max, command_context)


@command(
    command_type=CommandType.SUBS,
    public=True,
    format='<emoji> [all]',
    brief='Rank subbed rips with the most of a react',
    desc='Only shows 30 rips at most. Include `all` or `a` to show everything.',
    aliases=['viberank_sub', 'reactrank_sub'],
)
async def viberank_subs(args: list[str], command_context: CommandContext):

    if not len(args): 
        return await send("Error: Please include an emoji to search for. I'll show subbed rips that have the most of that emoji", command_context.channel)

    react_input = emoji_to_react_name_if_emoji(args[0])

    max = 30
    if len(args) > 1:
        max = 0 

    rips_and_errors = await get_rips_of_channel_types(['SUBS', 'SUBS_THREAD', 'SUBS_PIN'], command_context.channel)
    if len(rips_and_errors.error_strings):
        return await send_if_errors("The vibes are bad!", rips_and_errors.error_strings, command_context.channel)
    await send_viberank(rips_and_errors.rips, react_input, max, command_context)

@command(
    command_type=CommandType.SUBS,
    brief='Show all subbed rips',
    desc="Warning: Long!",
    aliases=['all_subs', 'sub_all', 'gubmeup'],
)
async def subs_all(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.ALL, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'])
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    format="[count] [[NOT] <search text | regex>]",
    brief=f'Show a random queued rip, optionally searching title',
    aliases=['randomq', 'randomqueue', 'randomaccepted', 'luckyqueue', 'letsgogambling!queue!']
)
async def random_q(args: list[str], command_context: CommandContext):
    await random_suborqueue(SubOrQueueRipFilterType.RANDOM, ['QUEUE'], args, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    format="[count] [[NOT] <search text | regex>]",
    brief=f'Show a random queued rip, optionally searching author line',
    desc='Insert a number to roll that many rips.',
    aliases=['random_event_q', 'randomauthor_q', 'random_author_q']
)
async def randomevent_q(args: list[str], command_context: CommandContext):
    await random_suborqueue(SubOrQueueRipFilterType.RANDOM_AUTHOR, ['QUEUE'], args, command_context)


@command(
    command_type=CommandType.QUEUE,
    brief='Show all queued rips',
    desc="Warning: Long! You're welcome minindo.",
    aliases=['all_queues', 'all_rips', 'q_all', 'fuckyou'],
)
async def queue_all(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.ALL, \
                              channel_types = ['QUEUE'])
    await send_suborqueue_rips(desc, command_context)

from dateutil import parser

class RipDate(NamedTuple):
    rip: Rip
    dates: list[datetime]
    day_strings: list[str]
    when_strings: list[str]

def is_unique_ripdate_string(string: str, ripdate: RipDate) -> bool:
    is_unique = True  
    for date in ripdate.dates: 
        if (
            date.strftime('%b %d').lower() in string.lower()
            or date.strftime('%B %d').lower() in string.lower()
         ):
            is_unique = False 
            break
    
    if is_unique: 
        for day_string in ripdate.day_strings:
            if (day_string is not string and (string in day_string)):
                is_unique = False
                break

    if is_unique: 
        for when_string in ripdate.when_strings:
            if (when_string is not string and (string in when_string)):
                is_unique = False
                break

    return is_unique

def format_ripdates(ripdates: list[RipDate]) -> str:
    result = ""
    for i_ripdate, ripdate in enumerate(ripdates):
        if i_ripdate > 0:
            result += f'\n_ _'

        if len(ripdate.dates):
            for i_date, date in enumerate(ripdate.dates):
                datestring = date.strftime('%b %d')
                if date.year != date.now().year:
                    datestring = date.strftime('%b %d %Y')
                if i_date == 0:
                    result += '\n'
                else:
                    result += ' \\ '
                result += f"🗓️ **{datestring}:**" 
        elif rip_has_react([ReactType.CALENDAR], ripdate.rip):
            result += f"\n🗓️ **???**" 

        posted_when_string = False
        when_result = ""
        for when_string in ripdate.when_strings:
            if is_unique_ripdate_string(when_string, ripdate):
                posted_when_string = True
                when_result += f"\n➡️️ **{when_string}:**" 

        for day_string in ripdate.day_strings:
            if not posted_when_string or is_unique_ripdate_string(day_string, ripdate):
                result += f"\n☀️ **{day_string}:**" 
        
        result += when_result

        rip = ripdate.rip
        rip_author = get_rip_author(rip.text, rip.message_author_name)
        rip_author = rip_author.replace('*', '').replace('_', '').replace('|', '').replace('#', '').lower()
        rip_title = get_rip_title(rip.text)
        rip_link = format_message_link(rip.guild_id, rip.channel_id, rip.message_id)
        result += f'\n**[{rip_title}]({rip_link})**\n{rip_author}'
    return result

@command(
    command_type=CommandType.QUEUE,
    brief='Show info of non-alerted limbo rips',
)
async def limbo(args: list[str], command_context: CommandContext):

    if not channel_type_is_defined_in_config('LIMBO'):
        return await send("No limbo channels are defined. This needs to be set up in the bot! Contact a bot maintainer.", command_context.channel)

    rips_and_errors = await get_rips_of_channel_types(['LIMBO'], command_context.channel)
    error_strings = rips_and_errors.error_strings 

    ripdates: list[RipDate] = []
    total_rip_count = 0

    for rip in rips_and_errors.rips:
        total_rip_count += 1
        if not rip_has_react(FIX_REACT_LIST, rip):
            text_to_parse = rip.text
            re.sub('(```\n*````)', '', text_to_parse)

            dates = [] 
            DATE_PATTERNS = [
                r'\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
                r'jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
                r'\s+\d{1,2}(?:st|nd|rd|th)?(?:\s+\d{2,4})?\b',

                r'\b\d{1,2}(?:st|nd|rd|th)?\s+'
                r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
                r'jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
                r'(?:\s+\d{2,4})?\b',

                r'\b\d{4}-\d{2}-\d{2}\b',
                r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b',
            ]
            for date_pattern in DATE_PATTERNS:
                for date_string in re.findall(date_pattern, text_to_parse, re.IGNORECASE):
                    try:
                        date = parser.parse(date_string, fuzzy=True)
                        if date is not None and date not in dates:
                            dates.append(date)
                    except:
                        pass

            author_line = get_raw_rip_author(rip.text)
            day_strings_unclean = re.findall(r'^(.+?\s+day)\b', author_line, re.IGNORECASE | re.MULTILINE)
            day_strings = clean_strings_markdown(day_strings_unclean)

            when_strings_unclean = re.findall(r'((?:for|if|after|before|needs|awaiting|pending|alongside|when|christmas|halloween)\s+.+?)(?:\s+by\b|[.(),!?]|$)', author_line, re.IGNORECASE | re.MULTILINE)
            when_strings = clean_strings_markdown(when_strings_unclean)

            ripdate = RipDate(rip, dates, day_strings, when_strings)
            ripdates.append(ripdate)


    ripdates_date_dict: dict[datetime, list[RipDate]] = {}
    ripdates_when_dict: dict[str, list[RipDate]] = {}
    ripdates_day_dict: dict[str, list[RipDate]] = {}
    ripdates_uncategorized: list[RipDate] = []

    for ripdate in ripdates: 

        if len(ripdate.dates) or rip_has_react([ReactType.CALENDAR], ripdate.rip):
            key = datetime.max 
            if len(ripdate.dates):
                key = ripdate.dates[0]
            if key not in ripdates_date_dict:
                ripdates_date_dict[key] = []
            ripdates_date_dict[key].append(ripdate)

        elif len(ripdate.day_strings):
            key = 'day'
            words = ripdate.day_strings[0].split()
            if len(words) > 1:
                key = words[-2].lower()
            if key not in ripdates_day_dict:
                ripdates_day_dict[key] = []
            ripdates_day_dict[key].append(ripdate)

        elif len(ripdate.when_strings):
            is_inserted = False
            for when_key in ripdates_when_dict.keys():
                for when_string in ripdate.when_strings:
                    if when_key in when_string.lower():
                        ripdates_when_dict[when_key].append(ripdate)
                        is_inserted = True
                        break
            if not is_inserted:
                key = ripdate.when_strings[0].lower()
                ripdates_when_dict[key] = []
                ripdates_when_dict[key].append(ripdate)

        else:
            ripdates_uncategorized.append(ripdate)

    result = ""

    READABILITY_LINE = "\n━━━━━━━━━━━━━━━━━━"

    count = 0

    for date, ripdates in sorted(ripdates_date_dict.items(), key=lambda item: item[0]):
        count += len(ripdates)
        result += format_ripdates(ripdates)
        result += READABILITY_LINE 

    for day, ripdates in sorted(ripdates_day_dict.items(), key=lambda item: item[0]):
        count += len(ripdates)
        result += format_ripdates(ripdates)
        result += READABILITY_LINE 

    for when_string, ripdates in sorted(ripdates_when_dict.items(), key=lambda item: item[0]):
        count += len(ripdates)
        result += format_ripdates(ripdates)
        result += READABILITY_LINE 

    if len(ripdates_uncategorized):
        result += f'\n# **UNCATEGORIZED:**'
    for ripdate in ripdates_uncategorized: 
        count += 1 
        result += format_ripdates([ripdate])
        result += READABILITY_LINE 

    footer = f'{count} of {total_rip_count} Rips (hiding rips with alert)'

    if len(result):
        await send_embed(result, command_context.channel, EmbedDesc(expires=True, footer=footer, seperator=READABILITY_LINE))
        await send_if_errors("Errors during parsing limbo rips", error_strings, command_context.channel)
    else:
        await send_and_if_errors("No limbo rips!", "Errors during parsing limbo rips", error_strings, command_context.channel)

##TODO: (Ahmayk) generic !vet command that auto-detects discord message vs url link input

@command(
    command_type=CommandType.ANALYZE,
    format='<message link/reply>',
    brief='Vet rip in message link',
    desc='The first non-YouTube link found in the message is treated as the rip URL.'
)
async def vet_msg(args: list[str], command_context: CommandContext):

    if not len(args) and not command_context.message_reference:
        return await send("Error: Please reply to a message or provide a link to message. I'll check that message for QoC issues (clipping, low bitrate, metdata issues, etc).", command_context.channel)

    async with command_context.channel.typing():
        messageAndErrors = await get_message_from_referece_or_args(command_context.message_reference, args)
        if len(messageAndErrors.error_strings):
            return await send_if_errors("Errors during grabbing message", messageAndErrors.error_strings, command_context.channel)

        vet_desc = VetRipDesc(message=messageAndErrors.message, full_feedback=True)
        vet_report = await vet_rip_or_url(messageAndErrors.message.content, vet_desc, messageAndErrors.message.guild)
        await send_and_if_errors(vet_report.string, "Errors during vetting:", vet_report.error_strings, command_context.channel)


@command(
    command_type=CommandType.ANALYZE,
    format='<file url>',
    brief='Vet rip in audio url',
)
async def vet_url(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please provide an url to a rip", command_context.channel)

    urls = extract_rip_link(args[0])

    if not len(urls):
        return await send(f'Error: no url found in {args[0]}', command_context.channel)

    async with command_context.channel.typing():
        vet_desc = VetRipDesc(full_feedback=True)
        vet_report = await vet_rip_or_url(urls[0], vet_desc, command_context.channel.guild)
        await send_and_if_errors(vet_report.string, "Errors during vetting:", vet_report.error_strings, command_context.channel)


@command(
    command_type=CommandType.ANALYZE,
    format='<message url/reply> [all]',
    brief='List dupes of a rip on YouTube and in rip queues',
    aliases=['dupe', 'count_dupe', 'count_dupes', 'countdupe', 'countdupes']
)
async def dupes(args: list[str], command_context: CommandContext):

    if not len(args) and not command_context.message_reference:
        return await send("Error: Please reply to a message or provide a link to message. I'll count how many of that rip are in rip queues and on the YouTube channel.", command_context.channel)

    list_all = len(args) > 1

    async with command_context.channel.typing():
        messageAndErrors = await get_message_from_referece_or_args(command_context.message_reference, args)
        if len(messageAndErrors.error_strings):
            return await send_if_errors("Errors during grabbing message", messageAndErrors.error_strings, command_context.channel)
        message = messageAndErrors.message
        if not message:
            return await send("Message not found! (and no error? Something went wrong)", command_context.channel)

        youtube_playlist = YouTubePlaylist()
        videos: list[PlaylistVideo] = []
        error_strings: list[str] = []
        playlist_id = extract_playlist_id('\n'.join(message.content.splitlines()[1:])) # ignore author line
        if len(playlist_id):
            youtube_playlist = await get_playlist_details(playlist_id, YOUTUBE_API_KEY)
            error_strings.extend(youtube_playlist.error_strings)
            if not len(error_strings):
                playlist_videos_and_errors = await get_playlist_videos(playlist_id, YOUTUBE_API_KEY)
                videos = playlist_videos_and_errors.videos
                error_strings.extend(playlist_videos_and_errors.error_strings)

        if (
            not len(error_strings)
            and len(YOUTUBE_CHANNEL_NAME)
            and len(youtube_playlist.channel_name) 
            and YOUTUBE_CHANNEL_NAME!= youtube_playlist.channel_name
        ):
            error_strings.append(f"Playlist is not from {YOUTUBE_CHANNEL_NAME} (found playlist from {youtube_playlist.channel_name})")
        
        if not len(error_strings):
            dupe_desc = ""
            description = get_rip_description(message.content)
            dupe_videos: list[PlaylistVideo] = []
            cutoff = 20
            if len(videos) > 0:
                for video in videos:
                    video_desc = video.title + '\n' + video.desc.replace('\r', '').split('\n\n')[0]
                    if isDupe(description, video_desc):
                        dupe_videos.append(video)

                dupe_videos.sort(key=lambda v: v.date)
                dupe_videos_display: list[PlaylistVideo] = []
                dupe_videos_display.extend(dupe_videos)
                channel_index_offset = 0 
                if not list_all and len(dupe_videos) > cutoff:
                    channel_index_offset = len(dupe_videos) - cutoff
                    dupe_videos_display = dupe_videos[len(dupe_videos)-cutoff:]
                    dupe_desc += f'*Showing latest {cutoff} dupes of {len(dupe_videos)}*\n'

                for i in range(len(dupe_videos_display)):
                    date_string = dupe_videos_display[i].date.strftime("%Y %b %d") 
                    title_string = f'[{dupe_videos_display[i].title}](https://www.youtube.com/watch?v={dupe_videos_display[i].video_id})'
                    if i > 0 and dupe_videos_display[i - 1].date.year != dupe_videos_display[i].date.year:
                        dupe_desc += '------------------------------\n'
                    dupe_desc += f'{i + channel_index_offset + 1}. `{date_string}` {title_string}\n'

            matching_queue_rips = []
            matching_queue_rips_includes_message = False
            rips_and_errors = await get_rips_fast_of_channel_types(['QUEUE', 'INACTIVE_QUEUE'], None)
            error_strings.extend(rips_and_errors.error_strings)
            for rip in rips_and_errors.rips:
                if isDupe(description, get_rip_description(rip.text)):
                    matching_queue_rips.append(rip)
                    if (rip.message_id == message.id):
                        matching_queue_rips_includes_message = True

            matching_queue_rips.sort(key=lambda r: r.created_at)
            matching_queue_rips_display = []
            matching_queue_rips_display.extend(matching_queue_rips)
            if not list_all and len(matching_queue_rips) > cutoff:
                matching_queue_rips_display = matching_queue_rips[len(matching_queue_rips)-cutoff:]
                dupe_desc += f'\n*Showing latest {cutoff} dupes of {len(matching_queue_rips)}*'
            if len(matching_queue_rips):
                string_and_errors = await format_suborqueue_rips(matching_queue_rips_display, [], "", message.guild)
                error_strings.extend(string_and_errors.error_strings)
                if len(string_and_errors.string):
                    dupe_desc += f'\n{string_and_errors.string}'

            # https://codegolf.stackexchange.com/questions/4707/outputting-ordinal-numbers-1st-2nd-3rd#answer-4712 how
            ordinal = lambda n: "%d%s" % (n,"tsnrhtdd"[(n//10%10!=1)*(n%10<4)*n%10::4])
            numbered_dupe = len(dupe_videos) + len(matching_queue_rips) + 1
            if matching_queue_rips_includes_message:
                numbered_dupe -= 1
            ordinal_string = ordinal(numbered_dupe)

            rip_title = get_rip_title(message.content)
            dupe_title = f"## **[{rip_title}]({message.jump_url})**\n**{ordinal_string} rip** of this track ({len(dupe_videos)} rips on channel, {len(matching_queue_rips)} rips in queues)"

            await send_embed(f"{dupe_title}\n{dupe_desc}", command_context.channel, EmbedDesc())

        await send_if_errors("Errors during processing dupes.", error_strings, command_context.channel)


@command(
    command_type=CommandType.ANALYZE,
    format='<message url/reply>',
    brief='Get rip audio metadata from message url',
    desc='The first non-YouTube link found in the message is treated as the rip URL.'
)
async def peek_msg(args: list[str], command_context: CommandContext):

    if not len(args) and not command_context.message_reference:
        return await send("Error: Please reply to a message or provide a link to a message. I'll lookup audio metadata info of the audio link", command_context.channel)

    async with command_context.channel.typing():
        messageAndErrors = await get_message_from_referece_or_args(command_context.message_reference, args)
        if len(messageAndErrors.error_strings):
            return await send_if_errors("Errors during grabbing message", messageAndErrors.error_strings, command_context.channel)
        message = messageAndErrors.message
        
        rip_title = get_rip_title(message.content)

        #TODO: (Ahmayk) making a new command that uses ffprobe instead
        use_ffprobe = len(args) > 1
        
        urls = extract_rip_link(message.content)
        if not len(urls):
            return await send("No links found in message", command_context.channel)

        code = 0
        errs = []
        for url in urls:
            if use_ffprobe:
                if not ffmpegExists():
                    return await send("ffmpeg not found on remote. Please contact developers, or run this command without the extra argument.", command_context.channel)
                code, msg = await getFileMetadataFfprobe(url)
            else:
                code, msg = await getFileMetadataMutagen(url)
            
            if code != -1:
                break
            errs.append(msg)
        if code == -1:
            await send("Error reading message:\n{}".format('\n'.join(errs)), command_context.channel)
        else:
            await send("**Rip**: **{}**\n**File metadata**:\n{}".format(rip_title, msg), command_context.channel)


@command(
    command_type=CommandType.ANALYZE,
    format='<file url>',
    brief='Get rip audio metadata from rip url',
    desc='The first non-YouTube link found in the message is treated as the rip URL.'
)
async def peek_url(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please provide a file url. I'll show that audio file's metadata", command_context.channel)

    urls = extract_rip_link(args[0])

    if not len(urls):
        return await send(f'Error: no url found in {args[0]}', command_context.channel)

    url = urls[0]

    async with command_context.channel.typing():

        #TODO: (Ahmayk) making a new command that uses ffprob instead
        #also compress
        use_ffprobe = len(args) > 1

        if use_ffprobe:
            if not ffmpegExists():
                await send("ffmpeg not found on remote. Please contact developers, or run this command without the extra argument.", command_context.channel)
                return
            code, msg = await getFileMetadataFfprobe(url)
        else:
            code, msg = await getFileMetadataMutagen(url)
        
        if code == -1:
            await send(f'Error reading URL: {msg}', command_context.channel)
        else:
            await send(f'**File metadata**:\n{msg}', command_context.channel)


@command(
    command_type=CommandType.ANALYZE,
    format='<message url/reply>',
    brief='Get length in seconds of rip audio in message',
    desc='Always redownloads the rip, skipping the internal cache.'
)
async def length_msg(args: list[str], command_context: CommandContext):

    if not len(args) and not command_context.message_reference:
        return await send("Error: Please reply to a message or provide a link to a message. I'll lookup how long the audio is in the rip.", command_context.channel)

    async with command_context.channel.typing():
        messageAndErrors = await get_message_from_referece_or_args(command_context.message_reference, args)
        if len(messageAndErrors.error_strings):
            return await send_if_errors("Errors during grabbing message", messageAndErrors.error_strings, command_context.channel)
        message = messageAndErrors.message
        
        return_message = ""
        error_strings = []
        string_and_errors = await get_formatted_rip_length(message.content, True, True, command_context.channel.guild)
        if not len(string_and_errors.error_strings):
            rip_title = get_rip_title(message.content)
            return_message = f'{rip_title} - {string_and_errors.string}'
        else:
            error_strings.extend(string_and_errors.error_strings)

        await send_and_if_errors(return_message, "Errors on getting length", error_strings, command_context.channel)


@command(
    command_type=CommandType.ANALYZE,
    format='<file url>',
    brief='Get length in seconds of rip audio in rip URL',
    desc='Always redownloads the rip, skipping the internal cache.'
)
async def length_url(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please provie a file url. I'll lookup how long the audio is in the rip.", command_context.channel)

    async with command_context.channel.typing():
        return_message = ""
        error_strings = []
        floatAndErrors = await get_rip_url_length(args[0], GetRipUrlLengthDesc(force_download=True))
        if not len(floatAndErrors.error_strings):
            return_message += format_rip_timecode(floatAndErrors.result) 
            if len(return_message) and floatAndErrors.result <= get_config("jingle_length_in_seconds"):
                return_message = f'{react_type_to_react(ReactType.JINGLE, command_context.channel.guild).string} {return_message}'
        else:
            error_strings.extend(floatAndErrors.error_strings)

        await send_and_if_errors(return_message, "Errors on getting length", error_strings, command_context.channel)


async def parse_source_input(message_reference: discord.MessageReference, args: list[str]) -> StringAndErrors:
    text = ""
    error_strings = []
    message_link = "" 
    if len(args):
        text = " ".join(args) 
        message_link = extract_discord_link(args[0])

    if (message_reference or message_link) and not (len(text) and not message_link):
        messageAndErrors = await get_message_from_referece_or_args(message_reference, [message_link])
        error_strings.extend(messageAndErrors.error_strings)
        if messageAndErrors.message:
            text = messageAndErrors.message.content
    return StringAndErrors(text, error_strings) 

@command(
    command_type=CommandType.SOURCE,
    format='<message link/reply | text>',
    brief='Search for sources in rip msg or text',
    public=True
)
async def source(args: list[str], command_context: CommandContext):

    if not len(args) and not command_context.message_reference:
        return await send("Error: Please provide a link to a rip message (or reply to one) OR text formatted as a rip title to lookup sources for. (Text example: slider - mario 64)", command_context.channel)

    async with command_context.channel.typing():
        string_and_errors = await parse_source_input(command_context.message_reference, args)
        if len(string_and_errors.error_strings):
            return await send_if_errors("Errors during !source", string_and_errors.error_strings, command_context.channel)

        credentials_and_errors = await refresh_credentials()
        if len(credentials_and_errors.error_strings) or not credentials_and_errors.credentials:
            return await send_if_errors("Failed to connect to Google API", string_and_errors.error_strings, command_context.channel)

        qoc_sheet_data = await get_qoc_sheet_data(GetQoCSheetDataDesc(), credentials_and_errors.credentials)
        text = search_rip_sources(string_and_errors.string, qoc_sheet_data)
        await send_embed(text, command_context.channel, EmbedDesc(title="Sources"))

@command(
    command_type=CommandType.SOURCE,
    format='<message link/reply | text>',
    brief='Search for QoC specialists',
    desc='The internal specialists cache is bypassed, guarenteeing that what is on the google sheet will be displayed (This is not the case elsewhere, such as when pinning a new QoC rip).',
    aliases=['specialist'],
    public=True
)
async def specialists(args: list[str], command_context: CommandContext):

    if not len(args) and not command_context.message_reference:
        return await send("Error: Please provide a link to a rip message (or reply to one) OR text formatted as a rip title to lookup specialists for. (Text example: slider - mario 64)", command_context.channel)

    async with command_context.channel.typing():
        string_and_errors = await parse_source_input(command_context.message_reference, args)
        if len(string_and_errors.error_strings):
            return await send_if_errors("Errors during grabbing message", string_and_errors.error_strings, command_context.channel)

        credentials_and_errors = await refresh_credentials()
        if len(credentials_and_errors.error_strings) or not credentials_and_errors.credentials:
            return await send_if_errors("Failed to connect to Google API", string_and_errors.error_strings, command_context.channel)

        #NOTE: (Ahmayk) bypass cache so that we are guarenteed to get what is on the sheet right now
        qoc_sheet_data = await get_qoc_sheet_data(GetQoCSheetDataDesc(bypass_cache=True), credentials_and_errors.credentials)
        text = search_specialists(string_and_errors.string, qoc_sheet_data, command_context.channel.guild)
        if len(text):
            await send_embed(text, command_context.channel, EmbedDesc(title="Specialists"))
        else:
            await send("No specialists found.", command_context.channel)

@command(
    command_type=CommandType.SOURCE,
    brief='Post link to QoC specialist spreadsheet.',
    aliases=['specialistsheet', 'sheet'],
)
async def qocsheet(args: list[str], command_context: CommandContext):
    if not SPECIALISTS_SPREADSHEET_ID:
        await send("Specialist spreadsheet is not configured in bot.", command_context.channel)
    link = f'https://docs.google.com/spreadsheets/d/{SPECIALISTS_SPREADSHEET_ID}/edit?usp=sharing'
    await send(link, command_context.channel)


@command(
    command_type=CommandType.UPLOAD_MISTAKES,
    brief='chooses a random unarchived post in uploading mistakes forum',
    aliases=['randommistake', 'savethechannel'],
    public=True
)
async def fixthechannel(args: list[str], command_context: CommandContext):
    if not channel_type_is_defined_in_config('UPLOAD_MISTAKE_FORUM'):
        return await send(f"ERROR: upload mistake channel not defined in bot config (contact bot maintainer).", command_context.channel)
    error_strings: list[str] = []
    channels_and_errors = await get_channels_of_types(['UPLOAD_MISTAKE_FORUM'], [])
    if not len(channels_and_errors.channels):
        return await send_and_if_errors(f"ERROR: upload mistake channel not found.", "Error grabbing channel", error_strings, command_context.channel)
    channel = channels_and_errors.channels[0]
    if channel.type != discord.ChannelType.forum:
        return await send_and_if_errors(f"ERROR: upload mistake channel {channel.jump_url} is not a forum channel.", "Error grabbing channel", error_strings, command_context.channel)

    unresolved = []
    for thread in channel.threads:
        for tag in thread.applied_tags:
            if tag.name == "Unresolved":
                unresolved.append(thread)
                break

    chosen_thread = random.choice(unresolved)
    fix_react = react_type_to_react(ReactType.FIX, channel.guild) 
    return await send(f'Random recent channel mistake by **{chosen_thread.owner.display_name}**: {fix_react.string} **[{chosen_thread.name}]({chosen_thread.jump_url})**', command_context.channel)

@command(
    command_type=CommandType.MANAGEMENT,
    brief='Check that all rips are in bot\'s cache',
)
async def validate_cache(args: list[str], command_context: CommandContext):
    await send("Validating cache of rips in all channels. This will take a few minutes...", command_context.channel)

    async with command_context.channel.typing():
        string_and_errors = await validate_cache_all()
        txt = ""
        if len(string_and_errors.string):
            txt = f'{string_and_errors.string}\n**Validation complete. Issues found.**'
        else:
            txt = "Validation complete! No issues found."
        await send_and_if_errors(txt, "Errors occurred during validation. Cache may not be accurate.", string_and_errors.error_strings, command_context.channel)
        

@command(
    command_type=CommandType.MANAGEMENT,
    format='[channel link]',
    brief='Refetches rip data from Discord',
    desc='Insert a channel to rebuild the cache for just that channel.',
    aliases=['refresh_cache', 'rebuild_cache']
)
async def reset_cache(args: list[str], command_context: CommandContext):

    string_and_errors = StringAndErrors("None", [])
    prefix = get_config('prefix')
    if len(args):
        parse_result = await parse_channel_link(args[0], ['SUBS', 'SUBS_PIN', 'SUBS_THREAD', 'QUEUE', 'QOC'])
        if len(parse_result.input_error):
            return await send_and_if_errors(parse_result.input_error, "Errors during finding channel:", parse_result.error_strings, command_context.channel)
        if parse_result.channel:
            async with command_context.channel.typing():
                await send(f'Rebuilding cache for <#{parse_result.channel.id}>. This will take a few minutes...', command_context.channel)
                await write_log(f'`{prefix}rebuild_cache` run by {command_context.user.name} for <#{parse_result.channel.id}> in {command_context.channel.jump_url}')
                string_and_errors = await rebuild_cache_for_channel(parse_result.channel)
    else:
        await send(f'Rebuilding cache for all channels. This may take a few minutes...', command_context.channel)
        async with command_context.channel.typing():
            await write_log(f'`{prefix}rebuild_cache` run by {command_context.user.name} in {command_context.channel.jump_url}')
            string_and_errors = await rebuild_cache()

    txt = f'Cache rebuilt!\n{string_and_errors.string}'
    await send_and_if_errors(txt, "Errors occurred during rebuild. Cache may not be accurate.", string_and_errors.error_strings, command_context.channel)


# ============ Config commands ============== #

@command(
    command_type=CommandType.MANAGEMENT,
    brief="Enable advanced metadata checking"
)
async def enable_metadata(args: list[str], command_context: CommandContext):
    set_config('metadata', True)
    await send("Advanced metadata checking enabled.", command_context.channel)

@command(
    command_type=CommandType.MANAGEMENT,
    brief="Disable advanced metadata checking"
)
async def disable_metadata(args: list[str], command_context: CommandContext):
    set_config('metadata', False)
    await send("Advanced metadata checking disabled.", command_context.channel)

@command(
    command_type=CommandType.MANAGEMENT,
    brief="Unpin pinned rips when over pinlimit"
)
async def enable_pinlimit_must_die(args: list[str], command_context: CommandContext):
    set_channel_pinlimit_mode(command_context.channel.id, True)
    await send("Soft pin limit is now hard pin limit. Good luck.", command_context.channel)

@command(
    command_type=CommandType.MANAGEMENT,
    brief="Disable pinlimit must die"
)
async def disable_pinlimit_must_die(args: list[str], command_context: CommandContext):
    set_channel_pinlimit_mode(command_context.channel.id, False)
    await send("Back to normal.", command_context.channel)


# ============ Helper/test commands ============== #

@command(
    command_type=CommandType.MANAGEMENT,
    public=True,
    brief="Show info on channels"
)
async def channel_list(args: list[str], command_context: CommandContext):
    async with command_context.channel.typing():
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
            "_**Channels**_ (That are hard coded into the bot's config)",
        ]
        channel_ids = []
        channels_json = get_config(CHANNEL_KEY)
        for channel in channels_json:
            channel_ids.append(channel["id"])
        for channel_id in channel_ids: 
            channel_config = get_channel_config(channel_id)
            message.append(
                f"<#{channel_config.id}>: " \
                + ", ".join(channel_config.types) \
                + (" [pinlimit must die mode {}]".format("enabled" if channel_config.pinlimit_must_die_mode else "disabled") if 'QOC' in channel_config.types else "")
            )
        result = "\n".join(message)
        
        await send_embed(result, command_context.channel, EmbedDesc())

@command(
    command_type=CommandType.MANAGEMENT,
    format='[search limit]',
    public=True,
    brief="Remove bot's old embed messages",
    desc=\
    """
    By default searches the channel for messages with embeds up to 200 messages.
    Include a different number to search back a different amount of messages."
    """
)
async def cleanup(args: list[str], command_context: CommandContext):

    search_limit = 200
    if len(args):
        search_limit = int(args[0]) 

    messages_and_errors = await discord_cleanup_embeds(search_limit, 0, command_context.channel, command_context.channel)
    count = len(messages_and_errors.messages)
    await send_and_if_errors(f"Removed {count} embed messages.", "...and I messed up too!", messages_and_errors.error_strings, command_context.channel)


async def get_suborqueue_rip_stats_string(channel: TextChannel | Thread, typing_channel: TextChannel | Thread) -> StringAndErrors:
    ret = ""
    rips_and_errors = await get_rips_fast(channel, GetRipsDesc(typing_channel=typing_channel))
    error_strings = rips_and_errors.error_strings

    if channel_is_types(channel, ['SUBS', 'SUBS_PIN']):
        ret += f"- <#{channel.id}>: **{len(rips_and_errors.rips)}** rips\n"

    elif channel_is_types(channel, ['QUEUE', 'SUBS_THREAD']):

        thread_count_dict: dict[int, int] = {}
        for rip in rips_and_errors.rips:
            if rip.channel_id:
                if rip.channel_id not in thread_count_dict:
                    thread_count_dict[rip.channel_id] = 0
                thread_count_dict[rip.channel_id] += 1

        if len(rips_and_errors.rips) > 0 and (channel_is_type(channel, 'SUBS_THREAD') or len(thread_count_dict) > 1):
            ret += f"- <#{channel.id}>:\n"
            for channel_id, count in thread_count_dict.items():
                if count > 0:
                    ret += f"  - <#{channel_id}>: **{count}** rips\n"
        else:
            ret += f"- <#{channel.id}>: **{len(rips_and_errors.rips)}** rips\n"

    return StringAndErrors(ret, error_strings) 

@command(
    command_type=CommandType.STATS,
    public=True,
    format="[all]",
    brief="Show # of rips in channels",
    desc="include `all` or `a` to show rips in all queue channels as well",
)
async def stats(args: list[str], command_context: CommandContext):

    ret = "**QoC channels**\n"
    error_strings = []

    qoc_channels_and_errors = await get_channels_of_types(['QOC'], ['SUBS', 'SUBS_THREAD', 'SUBS_PIN'])
    error_strings.extend(qoc_channels_and_errors.error_strings)
    sub_channels_and_errors = await get_channels_of_types(['SUBS', 'SUBS_THREAD', 'SUBS_PIN'], [])
    error_strings.extend(sub_channels_and_errors.error_strings)

    for channel in qoc_channels_and_errors.channels:
        team_count = 0
        email_count = 0
        rips_and_errors = await get_rips_fast(channel, GetRipsDesc(typing_channel=command_context.channel))
        error_strings.extend(rips_and_errors.error_strings)
        for rip in rips_and_errors.rips:
            author = get_rip_author(rip.text, rip.message_author_name)
            if 'email' in author.lower():
                email_count += 1
            else:
                team_count += 1
        ret += f"- <#{channel.id}>: **{team_count + email_count}** rips\n  - {team_count} team subs\n  - {email_count} email subs\n"

    ret += "**Submission channels**\n"
    for channel in sub_channels_and_errors.channels:
        string_and_errors = await get_suborqueue_rip_stats_string(channel, command_context.channel)
        ret += string_and_errors.string
        error_strings.extend(string_and_errors.error_strings)

    ##TODO: (Ahmayk) considering any arguemnts to show everything is kind of jank, but maybe fine since that's what ppl are used to
    if len(args):
        ret += "**Queues**\n"
        queue_channels_and_errors = await get_channels_of_types(['QUEUE'], [])
        error_strings.extend(queue_channels_and_errors.error_strings)
        for channel in queue_channels_and_errors.channels:
            string_and_errors = await get_suborqueue_rip_stats_string(channel, command_context.channel)
            ret += string_and_errors.string
            error_strings.extend(string_and_errors.error_strings)

    await send_and_if_errors(ret, "Stats may be inaccurate, errors during counting.", error_strings, command_context.channel)


@command(
    command_type=CommandType.PLAYLIST,
    public=True,
    format="[youtube rip playlist]",
    brief="Get or generate a sorting playlist sheet",
)
async def playlistsheet(args: list[str], command_context: CommandContext):
    input_link = ""
    if len(args):
        input_link = args[0]
    await start_interactive_playlist_gen(input_link, command_context.channel)


async def process_reminder_input(args: list[str], is_countdown: bool, command_context: CommandContext):

    reminders: list[Reminder] = []
    input_errors: list[str] = []

    input = " ".join(args)

    if "/" not in input:
        prefix = get_config("prefix")
        input_errors.append(f"ERROR: Missing the `/` symbol. Send an english phrase of a time (5 hours, sunday, tomorrow, April 1st), the `/` character, and a message for me to send. I'll post that message in this channel verbatim at that time.\n-# Example: `{prefix}remind 72 hours / qoc stingy's rip`")

    input_split = [] 
    time_strings = []
    text_string = "" 
    if not len(input_errors):
        input_split = input.split('/', 1)
        time_strings = input_split[0].split(',')
        text_string = input_split[1].strip(" ")

        if is_countdown and len(time_strings) > 1:
            input_errors.append(f"You can only input one time for a countdown reminder, not {len(time_strings)}")

        if not len(text_string):
            input_errors.append(f"Remind you of what now? Please put a message after the `/`")

        if not len(time_strings):
            input_errors.append(f"Gotta give a time")

        mention_ids = re.findall(r'@(everyone|here|[!&]?[0-9]{17,20})', text_string)
        for mention_id in mention_ids:
            if mention_id != str(command_context.user.id):
                input_errors.append(f"You can only ping yourself with a reminder. If you want to ping others, someone must do it themselves after the reminder is sent.")

    set_time = datetime.now(timezone.utc)

    remind_times = []
    if not len(input_errors):
        for timeString in time_strings:
            remind_time = dateparser.parse(timeString, settings={'TIMEZONE': 'US/Pacific', 'RETURN_AS_TIMEZONE_AWARE': True, 'PREFER_DATES_FROM': 'future'})
            if remind_time:
                remind_time = remind_time.astimezone(pytz.UTC)
                if remind_time >= set_time:
                    remind_times.append(remind_time)
                else:
                    timestamp = datetime_to_relative_timestamp(remind_time)
                    input_errors.append(f'{timestamp} is in the past! How would that work? (Or maybe I misunderstood?)') 
            else:
                input_errors.append(f'Intriguing. What time is **"{input_split[0].strip(" ")}"** supposed to be?') 

    if len(input_errors):
        return await send_list_of_input_errors(input_errors, command_context.channel)

    for remind_time in remind_times:

        next_countdown_delta = None
        if is_countdown:
            set_delta = (remind_time - set_time) - (REMINDER_COUNTDOWN_LIST[0] * 0.5)
            for i in range(len(REMINDER_COUNTDOWN_LIST) - 1, -1, -1):
                if set_delta > REMINDER_COUNTDOWN_LIST[i]:
                    next_countdown_delta = REMINDER_COUNTDOWN_LIST[i]
                    break

        reminders.append(Reminder(remind_time, set_time, text_string, command_context.channel.id, command_context.user.id, is_countdown, next_countdown_delta))

    await add_reminders_to_database(reminders)

    text_truncated = truncate_string(reminders[0].text, 40) 
    text_truncated = discord.utils.escape_mentions(text_truncated)
    text_truncated = text_truncated.replace(str(command_context.user.id), str(command_context.user.global_name))
    
    timestring_remind = "" 
    for i, reminder in enumerate(reminders): 
        timestring_remind += datetime_to_relative_timestamp(reminder.remind_time)
        timestring_remind += f' - {format_to_explicit_pst_time(reminder.remind_time)}'
        if i is not len(reminders) - 1:
            timestring_remind += ', '

    return_message = f':point_up::nerd: Will send here {timestring_remind}: `{text_truncated}`'
    if is_countdown:
        if reminders[0].next_countdown_delta:
            countdown_timestamps_string = get_next_countdown_reminders(reminder) 
            return_message = f':point_up::alarm_clock: Countdown started. The countdown ends {timestring_remind}, sending here each time: `{text_truncated}`\n-# {countdown_timestamps_string}'
        else:
            ##TODO: (Ahmayk) programatically determine minimum. Or just remember to change this text if it changes lol
            return_message = f':point_up::alarm_clock: Countdown started. But the given time is too soon countdown (Minimum is 1 hour). No reminders will be given other than at the final time {timestring_remind}, sending here each time: `{text_truncated}`'

    try:
        await command_context.channel.send(return_message)
    except Exception as error:
        await log_exception(f"Failed to send message in {command_context.channel.jump_url}", error, [], True)


@command(
    command_type=CommandType.REMIND,
    public=True,
    format="[engish time phrases seperated by commas] '/' [message]",
    brief="Schedules one or multiple reminders in that channel",
    aliases=['reminder', "remind_je"]
)
async def remind(args: list[str], command_context: CommandContext):
    await process_reminder_input(args, False, command_context)


@command(
    command_type=CommandType.REMIND,
    public=True,
    format="[engish time phrase] '/' [message]",
    brief="Schedules an increasingly frequent reminder in that channel",
)
async def countdown(args: list[str], command_context: CommandContext):
    await process_reminder_input(args, True, command_context)


def format_reminders(reminders: list[Reminder], include_numbers: bool) -> str:
    result = ""
    for i, reminder in enumerate(reminders): 
        result += "\n\n"
        if include_numbers:
            result += f'**{str(i + 1)}.** ' 
        result += format_reminder(reminder)
    return result


@command(
    command_type=CommandType.REMIND,
    public=True,
    brief="Shows scheduled reminders for this channel"
)
async def reminders(args: list[str], command_context: CommandContext):

    if (
        command_context.channel.id not in JE_DATABASE[JEDatabaseKey.REMINDER]
        or not len(JE_DATABASE[JEDatabaseKey.REMINDER][command_context.channel.id])
    ):
        prefix = get_config("prefix")
        return await send(f"No reminders are scheduled in this channel. Use {prefix} to schedule one.", command_context.channel)

    desc = format_reminders(JE_DATABASE[JEDatabaseKey.REMINDER][command_context.channel.id], False)
    await send_embed(desc, command_context.channel, EmbedDesc(title=f"Reminders for {command_context.channel.name}"))


@command(
    command_type=CommandType.REMIND,
    public=True,
    format="(insert number after prompt)",
    brief="Stops a reminder for this channel",
    aliases=["stop_reminders", "stopreminder", "stopreminders" "reminder_stop", "reminderstop", "remindersstop"]
)
async def stop_reminder(args: list[str], command_context: CommandContext):

    if (
        command_context.channel.id not in JE_DATABASE[JEDatabaseKey.REMINDER]
        or not len(JE_DATABASE[JEDatabaseKey.REMINDER][command_context.channel.id])
    ):
        prefix = get_config("prefix")
        return await send(f"No reminders are scheduled in this channel. Use {prefix} to schedule one.", command_context.channel)

    reminders = JE_DATABASE[JEDatabaseKey.REMINDER][command_context.channel.id]
    desc = format_reminders(reminders, True)

    await send_embed(desc, command_context.channel, EmbedDesc(title=f":octagonal_sign: Choose a Reminder to Stop"))

    valid_number_string = "1"
    if len(reminders) > 1:
        valid_number_string = f"1 - {len(reminders)}"

    is_valid_input = False
    instructions = f'Please enter the number(s) ({valid_number_string}) of a reminder to stop. Type `n` to cancel.'
    prompt = instructions
    selections: list[int] = [] 
    while not is_valid_input:

        selections = []

        await send(prompt, command_context.channel)

        def check(message):
            return message.author.id == command_context.user.id 

        message = await bot.wait_for('message', check=check)

        if message.content == 'n':
            break

        inputs = message.content.replace(",", " ").split()

        input_errors = ""
        for input in inputs:
            if input.isdigit():
                input_number = int(input)
                if input_number < 1:
                    input_errors += f'\n- {input_number}? Very funny. No.'
                elif input_number > len(reminders):
                    input_errors += f'\n- {input_number} is not in the list.'
                else:
                    selections.append(input_number)
            else:
                input_errors += f'\n- `{input}` isn\'t a number.'

        if not len(input_errors):
            is_valid_input = True
        else:
            prompt = f"{input_errors}\n{instructions}"

    if not len(selections):
        return await send(":thumbsup: Stopping stopped", command_context.channel)

    reminders_to_remove = [] 
    for selection in selections:
        reminders_to_remove.append(reminders[selection - 1])

    await remove_reminders(reminders_to_remove)

    desc = format_reminders(reminders_to_remove, False)

    await send_embed(desc, command_context.channel, EmbedDesc(external_pre_text=f'Removed {len(reminders_to_remove)} reminders'))



# While it might occur to folks in the future that a good command to write would be a rip feedback-sending command, something like that
# would be way too impersonal imo.
# NOTE: (Ahmayk) yeah no this should never happen

# This thing here is for when I start attempting slash commands again. Until then, this should be unused.
# Thank you to cibere on the Digiwind server for having the patience of a saint.
#@bot.command(name='sync_commands')
#@commands.is_owner()
#async def sync(ctx):
#  cmds = await bot.tree.sync()
#  await ctx.send(f"Synced {len(cmds)} commands globally!")

# some owner-only commands to config or kill bot if necessary

@command(
    command_type=CommandType.SECRET,
    public=True,
    admin=True,
)
async def shutdown(args: list[str], command_context: CommandContext):
    await send("WARNING: This command will shut down the bot. Proceed only if I really need to be shut down right now!", command_context.channel)
    password = "".join(random.choices("abcdefghijklmnopqrstuvwxyz", k=10))
    await send(f"Type the following to confirm the action: ``{password}``", command_context.channel)
    try:
        msg = await bot.wait_for("message", check=lambda m: (m.channel.id == command_context.channel.id) and (m.author.id == command_context.user.id), timeout=60)
    except asyncio.TimeoutError:
        await send("Time out. Action aborted.", command_context.channel)
    else:
        if msg.content == password:
            await send("Goodnight!", command_context.channel)
            await bot.close()
        else:
            await send("Incorrect password, please run the command again.", command_context.channel)


@command(
    command_type=CommandType.SECRET,
    public=True,
    admin=True,
    aliases=['get_config']
)
async def current_config(args: list[str], command_context: CommandContext):
    conf = None
    if len(args):
        conf = args[0]
    if os.path.exists('config.json'):
        with open('config.json', 'r', encoding='utf-8') as file:
            configs = json.load(file)
            if conf is None:
                all_configs = ""
                for k, v in configs.items():
                    all_configs += f"{k}: {v}\n"
                await send(all_configs, command_context.channel)
            else:
                try:
                    await send(configs[conf], command_context.channel)
                except KeyError:
                    await send(f"Error: No config named {conf}.", command_context.channel)
    else:
        await send("Error: Config file not found.", command_context.channel)

@command(
    command_type=CommandType.SECRET,
    admin=True,
    aliases=['set_config'],
)
async def modify_config(args: list[str], command_context: CommandContext):
    prefix = get_config("prefix")
    if len(args) < 2:
        return await send(f"Invalid syntax. Usage: {prefix}modify_config [config] [new value]", command_context.channel)
    
    conf = args[0]
    value = args[1]
    
    cur_val = get_config(conf)
    if value == 'true':
        new_val = True
    elif value == 'false':
        new_val = False
    else:
        try:
            new_val = int(value)
        except ValueError:
            # for configs of type str. set_config will throw error if there is type mismatch.
            pass
    
    set_config(conf, new_val)
    await send(f"Modified config {conf} from {cur_val} to {new_val}.", command_context.channel)


@command(
    command_type=CommandType.SECRET,
    public=True
)
async def testsource(args: list[str], command_context: CommandContext):

    #NOTE: (Ahmayk) Don't want anything that can spam this command accessible 
    #but this is useful for testing. Uncomment this block if you need to test.
    #but don't do it too much! Don't want to spam these websites with requests
    return await send(f'no spam allowed! Only developers can use this command by removing this check in the code :)', command_context.channel) 

    rips_and_errors = await get_rips_fast_of_channel_types(["QUEUE"], command_context.channel)
    if len(rips_and_errors.error_strings):
        return send_if_errors("Nope", rips_and_errors.error_strings, command_context.channel)
    temp_rips_all = rips_and_errors.rips

    selected_rip_message_ids = []
    random.shuffle(temp_rips_all)
    clamped_count = min(max(1, 10), len(temp_rips_all))
    for i in range(clamped_count):
        selected_rip_message_ids.append(temp_rips_all[i].message_id)

    credentials_and_errors = await refresh_credentials()
    if len(credentials_and_errors.error_strings) or not credentials_and_errors.credentials:
        return await send_if_errors("Failed to connect to Google API", credentials_and_errors.error_strings, command_context.channel)

    qoc_sheet_data = await get_qoc_sheet_data(GetQoCSheetDataDesc(), credentials_and_errors.credentials)

    for rip in temp_rips_all:
        if rip.message_id in selected_rip_message_ids:
            title = get_rip_title(rip.text)
            await send(f'TESTING: {title}', command_context.channel)
            text = search_rip_sources(rip.text, qoc_sheet_data)
            await send_embed(text, command_context.channel, EmbedDesc(title="Sources"))


@command(
    command_type=CommandType.SECRET,
    admin=True,
)
async def refresh_thumbnails(args: list[str], command_context: CommandContext):

    await send("Refreshing thumbnail cache. This will take 5-10 minutes.", command_context.channel)

    async with command_context.channel.typing():
        string_and_errors = await refresh_thumbnail_cache()

    await send_and_if_errors(
        string_and_errors.string,
        "Oops, something went wrong while refreshing thumbnail cache.",
        string_and_errors.error_strings,
        command_context.channel,
    )


@command(
    command_type=CommandType.SECRET,
    admin=True,
)
async def search_frames(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Please input a game title. I'll search for a matching thumbnail for the channel.", command_context.channel)

    input_title = " ".join(args)

    async with command_context.channel.typing():
        messages_and_errors = await search_thumbnail_cache(input_title)

    return_message = ""
    if not len(messages_and_errors.messages):
        return_message = "No thumbnails found"

    for message in messages_and_errors.messages:
        return_message += f'\n{message.content}\n{message.jump_url} {message.attachments[0].url}'

    await send_and_if_errors(return_message, "Erorrs during searching frames", messages_and_errors.error_strings, command_context.channel)

 