
import discord
from discord import TextChannel, Thread, Guild
from datetime import datetime, timezone, timedelta

from bot_secrets import YOUTUBE_API_KEY, YOUTUBE_CHANNEL_NAME
from simpleQoC.qoc import performQoC, ffmpegExists, getFileMetadataMutagen, getFileMetadataFfprobe
from simpleQoC.metadata import countDupe, isDupe
from sourceFinder import search_rip_sources 

from hq_core import *
from hq_config import *

import re
import typing
from typing import NamedTuple, List
from enum import Enum, auto
import json
import os
import random

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
    qoc_emote = get_qoc_emoji(command_context.channel.guild)

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
            desc += f'\n\n{qoc_emote} *Only accessable in QoC channels.*' 
        if command_info.admin:
            desc += f'\n\n:nerd: *Only accessable by admins of this discord server.*' 

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
                        result += f'{qoc_emote} '

                    result += f'**{prefix}{name}**'

                    if len(info.format):
                        result += f' `{info.format}`:'
                    else:
                        result += f':'

                    if len(info.brief):
                        brief = parse_emojis_in_string(info.brief, command_context.channel.guild)
                        result += f' {brief}'

        qoc_channel_ids = get_channel_ids_of_types(['QOC', 'PROXY_QOC'])
        qoc_channels_strings: list[str] = [] 
        for id in qoc_channel_ids:
            channel = bot.get_channel(int(id))
            if channel:
                qoc_channels_strings.append(channel.jump_url)

        result += '\n\n__**Legend:**__'
        result += '\n`<argument>`: Required argument'
        result += '\n`[argument]`: Optional argument'
        result += f'\n{qoc_emote}: Command only accessible in QoC channels:'
        result += f'\n{" ".join(qoc_channels_strings)}'
        result += f'\n\n*To learn more about a command, use `{prefix}help <command>`*'

        await send_embed(result, command_context.channel, EmbedDesc(seperator='\n\n', title="Commands"))

# ============ Roundup commands ============== #

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
    OVERDUE = auto()
    RANDOM = auto()
    SAVEQOC = auto()

class RoundupDesc(NamedTuple):
    roundup_filter_type: RoundupFilterType = RoundupFilterType.NULL
    message_author_id: int = 0 
    message_author_name: str = ""
    user_id: int = 0 
    conditional_string: str = ""
    parsed_search_input: ParsedSearchInput = ParsedSearchInput([], False, "") 
    react_name: str = ""
    reaction_type: ReactType = ReactType.NULL 
    not_found_message: str = ""
    random_count: int = 0

async def send_roundup(roundup_desc: RoundupDesc, command_context: CommandContext):
    """
    Sends a roundup message of all rips that the roundup channel the message was sent in points to.
    The roundup_filter_type describes how the roundup will be filtered.
    """

    channel = await get_qoc_channel(command_context.channel)
    if channel is None: return

    get_rips_desc = GetRipsDesc(typing_channel=command_context.channel)
    rips_and_errors = await get_rips(channel, get_rips_desc)
    error_strings = rips_and_errors.error_strings

    spec_overdue_days = get_config('spec_overdue_days')
    overdue_days = get_config('overdue_days')

    ##TODO: (Ahmayk) fuzzy username input (ie typing "ahmayk" and matching to their username or display name)
    user_id = command_context.user.id

    selected_rip_message_ids = [] 
    if roundup_desc.roundup_filter_type == RoundupFilterType.RANDOM:
        selected_rip_message_ids = choose_random_rips(rips_and_errors.rips, roundup_desc.random_count)

    readability_line = "━━━━━━━━━━━━━━━━━━\n"

    result = ""

    valid_count = 0
    for i, rip in enumerate(rips_and_errors.rips):

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
                title = get_rip_title(rip.text)
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
            case RoundupFilterType.SAVEQOC:
                #NOTE: (Ahmayk) does not take num checks needed into account, it could if we wanted
                #to standardize that in bot tho
                checks_count = rip_react_count([ReactType.CHECK], rip)
                rejects_count = rip_react_count([ReactType.REJECT], rip)
                has_valid_stop = rip_has_react([ReactType.STOP], rip) and not rip_has_react([ReactType.GOLDCHECK], rip)
                is_valid = checks_count - rejects_count == 2 \
                    and not rip_has_react(FIX_REACT_LIST, rip) \
                    and not has_valid_stop

        if is_valid:
            result += format_rip(rip, command_context.channel.guild, False, spec_overdue_days, overdue_days) + vet_reacts
            result += readability_line 
            valid_count += 1

    if result != "":
        footer = f'#{channel.name}   -   {valid_count} of {len(rips_and_errors.rips)} Rips'
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
                               user_id = command_context.user.id, not_found_message = "You haven't reviewed any rips yet.")
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
            not_found_message = "No rips can be saved with only one check. All is lost!")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    format="[NOT] <search text>",
    brief="Search QoC rips titles",
    desc="Does not need quotes. Include NOT to search for rips that don't inlude the searched input.",
    examples=["Deltarune", "PAL", "Mother 3"]
)
async def search(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Inclue what you want to search for! I'll search for it in the titles of QoC rips.", \
                           command_context.channel)

    parsed_search_input = parse_search_input(args)

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
    brief=f'Show QoC rips pinned over %overdue_days% days'
)
async def overdue(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.OVERDUE, not_found_message="No overdue rips.")
    await send_roundup(roundup_desc, command_context)


async def valdate_random_count_input(args: List[str], channel: TextChannel | Thread) -> int | None:
    count = 1
    if len(args):
        if '-' in args[0]:
            await send(f'ERROR: Negative rips not implemented `(library not found: antirip)`', channel)
            return None
        if args[0].isdigit():
            count = int(args[0])
            if count == 0:
                await send_embed(f'**[Zero. - Zero 64 (Zero Mix)](<https://www.youtube.com/watch?v=UtGL5yKdSCk>)**\nby Zero Z | 🔥 🔥 🍌 😭\n------------------------------', channel, EmbedDesc())
                return None
        else:
            await send(f'**{args[0]}** is not a number. Please enter include how many rips you want to roll, or nothing if you just want one.', channel)
            return None
    return count

@command(
    command_type=CommandType.QOC,
    brief=f'Show a random QoC rip',
    format="[count]",
    desc='Insert a number to roll that many rips.',
    aliases=['random', 'randomqoc', 'random_qoc', 'lucky', 'letsgogambling!']
)
async def randompull(args: list[str], command_context: CommandContext):
    count = await valdate_random_count_input(args, command_context.channel)
    if count == None: return
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.RANDOM, random_count = count, \
                               not_found_message="No gambling today, sorry!")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.QOC,
    public=True,
    brief='Count pinned QoC rips for channel',
    desc='Only counts pinned messages with valid rips.'
)
async def count(args: list[str], command_context: CommandContext):

    ##TODO: (Ahmayk) Compress
    qoc_channel = None 
    proxy = ""
    if channel_is_type(command_context.channel, 'PROXY_QOC'):
        qoc_channel = await get_qoc_channel(command_context.channel)
        if qoc_channel:
            proxy = f"\n-# Showing results from <#{qoc_channel.id}>."
    else:
        qoc_channel = command_context.channel

    if qoc_channel:
        rips_and_errors = await get_rips_fast(qoc_channel, GetRipsDesc(typing_channel=command_context.channel))
        if len(rips_and_errors.error_strings):
            return await send_if_errors("Can't count today. :(", rips_and_errors.error_strings, command_context.channel)

        pincount = len(rips_and_errors.rips)

        if (pincount < 1):
            result = "`* Determination.`"
        else:
            result = f"`* {pincount} left.`"

        result += proxy
        await send(result, command_context.channel)


@command(
    command_type=CommandType.QOC,
    public=True,
    brief='Report proximity to pinlimit for channel',
    desc='Only counts pinned messages with valid rips.',
    aliases=['pinlimit'],
)
async def limitcheck(args: list[str], command_context: CommandContext):

    ##TODO: (Ahmayk) Compress
    qoc_channel = None 
    proxy = ""
    if channel_is_type(command_context.channel, 'PROXY_QOC'):
        qoc_channel = await get_qoc_channel(command_context.channel)
        if qoc_channel:
            proxy = f"\n-# Showing results from <#{qoc_channel.id}>."
    else:
        qoc_channel = command_context.channel

    if qoc_channel:
        rips_and_errors = await get_rips_fast(qoc_channel, GetRipsDesc(typing_channel=command_context.channel))
        if len(rips_and_errors.error_strings):
            return await send_if_errors("idk how to count rn sorry", rips_and_errors.error_strings, command_context.channel)
        count = len(rips_and_errors.rips)
        result = f"You can pin {get_config('soft_pin_limit') - count} more rips until I start complaining about pin space."
        result += proxy
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

    sub_channel_id, msg = parse_channel_link(sub_channel_link, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'])
    if len(msg) > 0:
        await send(msg, command_context.channel)
    if sub_channel_id == -1:
        return

    channel = bot.get_channel(sub_channel_id)
    rips_and_errors = await get_rips_fast(channel, GetRipsDesc(typing_channel=command_context.channel))
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
    UNSENT = auto()
    SEARCH_TITLE = auto()
    SEARCH_AUTHOR = auto()
    SCOUT = auto()
    ALL = auto()
    RANDOM = auto()

class SendSubOrQueueDesc(NamedTuple):
    suborqueue_rip_filter_type: SubOrQueueRipFilterType = SubOrQueueRipFilterType.NULL 
    reaction_type: ReactType = ReactType.NULL 
    channel_link: str = ""
    channel_types: List[str] = []
    parsed_search_input: ParsedSearchInput = ParsedSearchInput([], False, "") 
    not_found_message: str = ""
    random_count: int = 0

async def send_suborqueue_rips(desc: SendSubOrQueueDesc, command_context: CommandContext):
    """
    Sends a list of rips from either all submission or queue channels according to a filter.
    """
    channel_id, msg = parse_channel_link(desc.channel_link, desc.channel_types)
    if len(msg) > 0 or not len(desc.channel_link):
        if desc.channel_link is not None and len(desc.channel_link):
            await command_context.channel.send("Warning: something went wrong parsing channel link. Defaulting to showing from all known queues.")
        channel_ids = get_channel_ids_of_types(desc.channel_types)
    else:
        channel_ids = [channel_id]

    result = ""
    error_strings = []
    total_count = 0
    valid_count = 0

    qoc_emote = get_qoc_emoji(command_context.channel.guild)

    selected_rip_message_ids = [] 
    if desc.suborqueue_rip_filter_type == SubOrQueueRipFilterType.RANDOM: 
        temp_rips_all: List[Rip] = []
        for channel_id in channel_ids:
            channel = bot.get_channel(channel_id)
            if channel:
                temp_rips_and_errors = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
                error_strings.extend(temp_rips_and_errors.error_strings)
                temp_rips_all.extend(temp_rips_and_errors.rips)
        selected_rip_message_ids = choose_random_rips(temp_rips_all, desc.random_count)

    for channel_id in channel_ids:
        channel = bot.get_channel(channel_id)
        if channel:

            result += f'<#{channel_id}>:\n'

            rips_and_errors = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
            error_strings.extend(rips_and_errors.error_strings)

            for rip in rips_and_errors.rips:

                total_count += 1

                rip_title = get_rip_title(rip.text)
                rip_author = get_raw_rip_author(rip.text)

                is_valid = False
                match(desc.suborqueue_rip_filter_type):
                    case SubOrQueueRipFilterType.HASREACT:
                        is_valid = rip_has_react([desc.reaction_type], rip)
                    case SubOrQueueRipFilterType.UNSENT:
                        is_valid = line_contains_substring(rip_author, 'email') and \
                                not rip_has_react([ReactType.EMAILSENT, ReactType.ANTIMAIL], rip)
                    case SubOrQueueRipFilterType.SEARCH_TITLE:
                        is_valid = search_with_parsed_input(rip_title, desc.parsed_search_input) 
                    case SubOrQueueRipFilterType.SEARCH_AUTHOR:
                        is_valid = search_with_parsed_input(rip_author, desc.parsed_search_input) 
                    case SubOrQueueRipFilterType.SCOUT:
                        is_valid = False
                        for key in desc.parsed_search_input.search_keys:
                            if rip_title.lower().startswith(key.lower()):
                                is_valid = True
                                break
                    case SubOrQueueRipFilterType.ALL:
                        is_valid = True
                    case SubOrQueueRipFilterType.RANDOM:
                        is_valid = rip.message_id in selected_rip_message_ids
                    case _:
                        assert "Unimplemented SubOrQueueRipFilterType"

                if is_valid:
                    if rip_has_react([ReactType.QOC], rip):
                        result += f"{qoc_emote} "
                    rip_link = format_message_link(channel.guild.id, rip.channel_id, rip.message_id)
                    result += f'**[{rip_title}]({rip_link})**\n'
                    valid_count += 1

            result += '------------------------------\n'

    if valid_count == 0:
        not_found_message = "No rips found."
        if len(desc.not_found_message):
            not_found_message = desc.not_found_message
        await send_and_if_errors(not_found_message, "Errors during getting rips:", error_strings, command_context.channel)
    else:
        footer = f'{valid_count} of {total_count} Rips'
        await send_embed(result, command_context.channel, EmbedDesc(expires=True, footer=footer))
        await send_if_errors("Errors during getting rips:", error_strings, command_context.channel)


@command(
    command_type=CommandType.SUBS,
    public=True,
    format="[NOT] <search text>",
    brief='Search submission rip titles',
    desc="Does not need quotes. Include NOT to search for rips that don't inlude the searched input.",
    aliases=['search_sub'],
)
async def search_subs(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Inclue what you want to search for! I'll search for it in the titles of submitted rips.", \
                           command_context.channel)

    parsed_search_input = parse_search_input(args)

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

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SEARCH_AUTHOR, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'], \
                              parsed_search_input = parsed_search_input, \
                              not_found_message = f'No submissions {parsed_search_input.containing_error_string} in author line found.')
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.SUBS,
    public=True,
    format="[count]",
    brief='Show a random submitted rip',
    desc='Insert a number to roll that many rips.',
    aliases=['randomsub', 'randomqoc', 'luckysub', 'letsgogambling!sub!']
)
async def random_sub(args: list[str], command_context: CommandContext):

    count = await valdate_random_count_input(args, command_context.channel)
    if count == None: return

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.RANDOM, \
                              random_count = count, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'])
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
    format="[NOT] <search text>",
    brief='Search queued rip titles',
    desc="Does not need quotes. Include NOT to search for rips that don't inlude the searched input.",
    aliases=['search_queue', 'search_queues', 'search_qs'],
)
async def search_q(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Inclue what you want to search for! I'll search for it in the titles of accepted queued rips.", \
                           command_context.channel)

    parsed_search_input = parse_search_input(args)
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
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SEARCH_AUTHOR, \
                              channel_types = ['QUEUE'], \
                              parsed_search_input= parsed_search_input, \
                              not_found_message = f'No submissions {parsed_search_input.containing_error_string} in author line found.')
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

    channel_id, msg = parse_channel_link(channel_link, ['QUEUE'])
    if len(msg) > 0:
        return await command_context.channel.send(msg)

    channel = bot.get_channel(channel_id)
    if channel is None: 
        return await send("Error: Invalid channel found. Contact bot developers to update list of channels.", command_context.channel)

    rips_and_errors = await get_rips_fast(channel, GetRipsDesc(typing_channel=command_context.channel))
    if len(rips_and_errors.error_strings):
        return await send_if_errors("Scout died.", rips_and_errors.error_strings, command_context.channel)

    count = {}
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ': # could have done string.ascii_uppercase but i dont think the alphabet is getting any updates
        count[letter] = 0

    for rip in rips_and_errors.rips:
        rip_title = get_raw_rip_title(rip.text)
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

    channel = await get_qoc_channel(command_context.channel)
    if channel is None: return

    rips_and_errors = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
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

    queue_rips = []
    for channel_id in get_channel_ids_of_types(['QUEUE']):
        channel = bot.get_channel(channel_id)
        if channel:
            rips_and_errors  = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
            if len(rips_and_errors.error_strings):
                return await send_if_errors("The vibes are bad!", rips_and_errors.error_strings, command_context.channel)
            queue_rips.extend(rips_and_errors.rips)

    await send_vibes(queue_rips, max, command_context)


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

    subbed_rips = []
    for channel_id in get_channel_ids_of_types(['SUBS', 'SUBS_THREAD', 'SUBS_PIN']):
        channel = bot.get_channel(channel_id)
        if channel:
            rips_and_errors = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
            if len(rips_and_errors.error_strings):
                return await send_if_errors("vibes are kind of not in today. come back later", rips_and_errors.error_strings, command_context.channel)
            subbed_rips.extend(rips_and_errors.rips)

    await send_vibes(subbed_rips, max, command_context)


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

        #NOTE: (Ahmayk) assumes all rips come from the same server 
        channel = bot.get_channel(rip.channel_id)
        guild_id = 0 
        if channel:
            channel = await bot.fetch_channel(rip.channel_id)
        if channel:
            guild_id = channel.guild.id 

        emoji_count_list_most = emoji_count_list 
        if max_rips > 0:
            emoji_count_list_most = emoji_count_list_most[:max_rips]
        if len(emoji_count_list_most) < len(emoji_count_list):
            result += f'*Showing top **{len(emoji_count_list_most)}** rips with {react_name}.*\n\n'

        emoji_stirng = reaction_name_to_emoji_string(react_name, command_context.channel.guild)
        for emoji_count in emoji_count_list_most:
            rip_title = get_rip_title(emoji_count.rip.text)
            rip_link = format_message_link(guild_id, emoji_count.rip.channel_id, emoji_count.rip.message_id)
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

    channel = await get_qoc_channel(command_context.channel)
    if channel is None: return

    react_input = emoji_to_react_name_if_emoji(args[0])

    max = 30
    if len(args) > 1:
        max = 0 

    rips_and_errors = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
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

    queue_rips = []
    for channel_id in get_channel_ids_of_types(['QUEUE']):
        channel = bot.get_channel(channel_id)
        if channel:
            rips_and_errors  = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
            if len(rips_and_errors.error_strings):
                return await send_if_errors("The vibes are bad!", rips_and_errors.error_strings, command_context.channel)
            queue_rips.extend(rips_and_errors.rips)

    await send_viberank(queue_rips, react_input, max, command_context)


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

    subbed_rips = []
    for channel_id in get_channel_ids_of_types(['SUBS', 'SUBS_THREAD', 'SUBS_PIN']):
        channel = bot.get_channel(channel_id)
        if channel:
            rips_and_errors = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
            if len(rips_and_errors.error_strings):
                return await send_if_errors("vibes are kind of not in today. come back later", rips_and_errors.error_strings, command_context.channel)
            subbed_rips.extend(rips_and_errors.rips)

    await send_viberank(subbed_rips, react_input, max, command_context)

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
    format="[count]",
    brief=f'Show a random submitted rip',
    desc='Insert a number to roll that many rips.',
    aliases=['randomq', 'randomqueue', 'randomaccepted', 'luckyqueue', 'letsgogambling!queue!']
)
async def random_q(args: list[str], command_context: CommandContext):
    count = await valdate_random_count_input(args, command_context.channel)
    if count == None: return
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.RANDOM, \
                              random_count=count, \
                              channel_types = ['QUEUE'])
    await send_suborqueue_rips(desc, command_context)


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
                    result += ' \ '
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

    channel_ids = get_channel_ids_of_types(['LIMBO'])
    if not len(channel_ids):
        return await send("No limbo channels are defined. This needs to be set up in the bot! Contact a bot maintainer.", command_context.channel)

    error_strings = []

    ripdates: list[RipDate] = []
    total_rip_count = 0

    for channel_id in channel_ids:
        channel = bot.get_channel(channel_id)
        if channel:
            rips_and_errors = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
            error_strings.extend(rips_and_errors.error_strings)
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



# ============ Basic QoC commands ============== #

##TODO: (Ahmayk) vet command UX needs to be refactored it's confusing as hell 

@command(
    command_type=CommandType.ANALYZE,
    brief='Vet all QoC rips for issues (No YouTube API)',
    aliases=['vet_all']
)
async def vet(args: list[str], command_context: CommandContext):
    prefix = get_config("prefix")
    if len(args):
        return await send(f"WARNING: ``{prefix}vet`` takes no argument. Did you mean to use ``{prefix}vet_msg`` or ``{prefix}vet_url``?", command_context.channel)
    
    if command_context.message_reference:
        return await send(f"WARNING: ``{prefix}vet`` takes no argument (nor replies). Did you mean to use ``{prefix}vet_msg`` or ``{prefix}vet_url``?", command_context.channel)
    
    await vet_from(args, command_context)


@command(
    command_type=CommandType.ANALYZE,
    format='[message link]',
    brief='Vet rips from any channel starting from message link (No YouTube API)',
    desc='Find rips in pinned messages with bitrate/clipping issues and show their details, only counting messages not older than linked message'
)
async def vet_from(args: list[str], command_context: CommandContext):

    from_msg = None
    if len(args):
        from_msg = args[0]

    vet_all_pins = from_msg is None
    if not vet_all_pins and from_msg:
        _, _, from_message, status = await parse_message_link(from_msg)
        if from_message is None:
            await send(status, command_context.channel)
            return
        from_timestamp = from_message.created_at

    channel = await get_qoc_channel(command_context.channel)
    if channel is None: return

    if not ffmpegExists():
        return await send("WARNING: ffmpeg command not found on the bot's server. Please contact the developers.", command_context.channel)

    async with command_context.channel.typing():
        rips_and_errors = await get_rips_fast(channel, GetRipsDesc())
        error_strings = rips_and_errors.error_strings 

        for rip in rips_and_errors.rips:
            if not vet_all_pins and rip.created_at < from_timestamp:
                continue

            vet_desc = VetRipDesc(rip=rip)
            vet_report = await vet_rip_or_url(rip.text, vet_desc)
            error_strings.extend(vet_report.error_strings)
            await send(vet_report.string, command_context.channel)

        if len(rips_and_errors.rips) == 0:
            await send_and_if_errors("No pinned rips found to QoC.", "There were errors though.", error_strings, command_context.channel)
        else:
            txt = "Finished QoC-ing. Please note that these are only automated detections - you should verify the issues in Audacity and react manually." 
            await send_and_if_errors(txt, "Errors during vetting:", error_strings, command_context.channel)


@command(
    command_type=CommandType.ANALYZE,
    format='<message link>',
    brief='Vet rip in message link',
    desc='The first non-YouTube link found in the message is treated as the rip URL.'
)
async def vet_msg(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please provide a link to message.", command_context.channel)

    async with command_context.channel.typing():
        server, channel, message, status = await parse_message_link(args[0])
        if message is None:
            return await send(status, command_context.channel)

        vet_desc = VetRipDesc(message=message, use_youtube_api=True, full_feedback=True)
        vet_report = await vet_rip_or_url(message.content, vet_desc)
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
        vet_report = await vet_rip_or_url(urls[0], vet_desc)
        await send_and_if_errors(vet_report.string, "Errors during vetting:", vet_report.error_strings, command_context.channel)


@command(
    command_type=CommandType.ANALYZE,
    format='<message url>',
    brief='Count # of dupes on YouTube and rip queues',
)
async def count_dupe(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please provide a link to message.", command_context.channel)

    server, channel, message, status = await parse_message_link(args[0])
    if message is None:
        return await send(status, command_context.channel)

    playlistId = extract_playlist_id('\n'.join(message.content.splitlines()[1:])) # ignore author line
    description = get_rip_description(message.content)
    rip_title = get_rip_title(message.content)

    p, msg = await run_blocking(countDupe, description, YOUTUBE_CHANNEL_NAME, playlistId, YOUTUBE_API_KEY)
    if len(msg) > 0:
        await send(msg, command_context.channel)

    error_strings = []

    q = 0
    queue_channels = get_channel_ids_of_types(['QUEUE'])
    for queue_channel_id in queue_channels:
        queue_channel = server.get_channel(queue_channel_id)
        if queue_channel:
            rips_and_errors = await get_rips_fast(queue_channel, GetRipsDesc(typing_channel=command_context.channel))
            error_strings.extend(rips_and_errors.error_strings)
            #TODO: (Ahmayk) return errors
            q += sum([isDupe(description, get_rip_description(r.text)) for r in rips_and_errors.rips if r.message_id != message.id])

    # https://codegolf.stackexchange.com/questions/4707/outputting-ordinal-numbers-1st-2nd-3rd#answer-4712 how
    ordinal = lambda n: "%d%s" % (n,"tsnrhtdd"[(n//10%10!=1)*(n%10<4)*n%10::4])

    txt = f"**Rip**: **{rip_title}**\nFound {p + q} rips of the same track ({p} on the channel, {q} in queues). This is the {ordinal(p + q + 1)} rip of this track." 
    await send_and_if_errors(txt, "Errors during processing dupes.", error_strings, command_context.channel)


@command(
    command_type=CommandType.ANALYZE,
    format='<message url>',
    brief='Get rip audio metadata from message url',
    desc='The first non-YouTube link found in the message is treated as the rip URL.'
)
async def peek_msg(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please provide a link to message.", command_context.channel)

    async with command_context.channel.typing():
        server, channel, message, status = await parse_message_link(args[0])
        if message is None:
            return await send(status, command_context.channel)
        
        rip_title = get_rip_title(message.content)

        #TODO: (Ahmayk) making a new command that uses ffprob instead
        use_ffprobe = len(args) > 1
        
        urls = extract_rip_link(message.content)
        errs = []
        for url in urls:
            if use_ffprobe:
                if not ffmpegExists():
                    return await send("ffmpeg not found on remote. Please contact developers, or run this command without the extra argument.", command_context.channel)
                code, msg = await run_blocking(getFileMetadataFfprobe, url)
            else:
                code, msg = await run_blocking(getFileMetadataMutagen, url)
            
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
        return await send("Error: Please provide a link to message.", command_context.channel)

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
            code, msg = await run_blocking(getFileMetadataFfprobe, url)
        else:
            code, msg = await run_blocking(getFileMetadataMutagen, url)
        
        if code == -1:
            await send(f'Error reading URL: {msg}', command_context.channel)
        else:
            await send(f'**File metadata**:\n{msg}', command_context.channel)


@command(
    command_type=CommandType.SOURCE,
    format='<message link | text>',
    brief='Search for sources in rip msg or text',
    public=True
)
async def source(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please provide a link to message.", command_context.channel)

    async with command_context.channel.typing():

        text = " ".join(args)
        message_link = extract_discord_link(args[0])
        if len(message_link):
            server, channel, message, status = await parse_message_link(message_link)
            if message is None:
                return await send(status, command_context.channel)
            text = message.content

        text = search_rip_sources(text)
        await send_embed(text, command_context.channel, EmbedDesc(title="Sources"))


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
    brief='Refetches rip data from discord',
    desc='Insert a channel to rebuild the cache for just that channel.',
    aliases=['refresh_cache', 'rebuild_cache']
)
async def reset_cache(args: list[str], command_context: CommandContext):

    string_and_errors = StringAndErrors("None", [])
    prefix = get_config('prefix')
    if len(args):
        channel_link = args[0] 
        channel_id, msg = parse_channel_link(channel_link, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD', 'QUEUE', 'QOC'], False)
        if len(msg) > 0:
            return await send(msg, command_context.channel)
        await send(f'Rebuilding cache for <#{channel_id}>. This will take a few minutes...', command_context.channel)
        async with command_context.channel.typing():
            await write_log(f'`{prefix}rebuild_cache` run by {command_context.user.name} for <#{channel_id}> in {command_context.channel.jump_url}')
            string_and_errors = await rebuild_cache_for_channel(channel_id)
    else:
        await send(f'Rebuilding cache for all channels. This may take a few minutes...', command_context.channel)
        async with command_context.channel.typing():
            await write_log(f'`{prefix}rebuild_cache` run by {command_context.user.name} in {command_context.channel.jump_url}')
            string_and_errors = await rebuild_cache_all()

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
            "_**Channels**_",
        ]
        for channel in get_channel_ids_all():
            channel_config = get_channel_config(channel)
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


async def get_suborqueue_rip_stats_string(channel_id: int, typing_channel: TextChannel | Thread) -> StringAndErrors:
    ret = ""
    error_strings = []
    channel = bot.get_channel(channel_id)
    if channel:

        rips_and_errors = await get_rips_fast(channel, GetRipsDesc(typing_channel=typing_channel))
        error_strings.extend(rips_and_errors.error_strings)

        if channel_is_types(channel, ['SUBS', 'SUBS_PIN']):
            ret += f"- <#{channel_id}>: **{len(rips_and_errors.rips)}** rips\n"

        elif channel_is_types(channel, ['QUEUE', 'SUBS_THREAD']):

            thread_count_dict: dict[int, int] = {}
            for rip in rips_and_errors.rips:
                if rip.channel_id:
                    if rip.channel_id not in thread_count_dict:
                        thread_count_dict[rip.channel_id] = 0
                    thread_count_dict[rip.channel_id] += 1

            if len(rips_and_errors.rips) > 0 and (channel_is_type(channel, 'SUBS_THREAD') or len(thread_count_dict) > 1):
                ret += f"- <#{channel_id}>:\n"
                for channel_id, count in thread_count_dict.items():
                    if count > 0:
                        ret += f"  - <#{channel_id}>: **{count}** rips\n"
            else:
                ret += f"- <#{channel_id}>: **{len(rips_and_errors.rips)}** rips\n"

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

    qoc_channels = get_channel_ids_of_types(['QOC'])
    sub_channels = get_channel_ids_of_types(['SUBS', 'SUBS_THREAD', 'SUBS_PIN'])
    for channel_id in qoc_channels:
        if channel_id not in sub_channels:
            team_count = 0
            email_count = 0
            channel = bot.get_channel(channel_id)
            if channel:
                rips_and_errors = await get_rips_fast(channel, GetRipsDesc(typing_channel=command_context.channel))
                error_strings.extend(rips_and_errors.error_strings)
                for rip in rips_and_errors.rips:
                    author = get_rip_author(rip.text, rip.message_author_name)
                    if 'email' in author.lower():
                        email_count += 1
                    else:
                        team_count += 1
                ret += f"- <#{channel_id}>: **{team_count + email_count}** rips\n  - {team_count} team subs\n  - {email_count} email subs\n"

    ret += "**Submission channels**\n"
    for channel_id in sub_channels:
        string_and_errors = await get_suborqueue_rip_stats_string(channel_id, command_context.channel)
        ret += string_and_errors.string
        error_strings.extend(string_and_errors.error_strings)

    ##TODO: (Ahmayk) considering any arguemnts to show everything is kind of jank, but maybe fine since that's what ppl are used to
    if len(args):
        ret += "**Queues**\n"
        queue_channels = get_channel_ids_of_types(['QUEUE'])
        for channel_id in queue_channels:
            string_and_errors = await get_suborqueue_rip_stats_string(channel_id, command_context.channel)
            ret += string_and_errors.string
            error_strings.extend(string_and_errors.error_strings)

    await send_and_if_errors(ret, "Stats may be inaccurate, errors during counting.", error_strings, command_context.channel)


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

    channel_ids = get_channel_ids_of_types(["QUEUE"])

    temp_rips_all: List[Rip] = []
    for channel_id in channel_ids:
        channel = bot.get_channel(channel_id)
        if channel:
            temp_rips_and_errors = await get_rips(channel, GetRipsDesc(typing_channel=command_context.channel))
            # error_strings.extend(temp_rips_and_errors.error_strings)
            temp_rips_all.extend(temp_rips_and_errors.rips)

    selected_rip_message_ids = choose_random_rips(temp_rips_all, 10)
    for rip in temp_rips_all:
        if rip.message_id in selected_rip_message_ids:
            title = get_rip_title(rip.text)
            await send(f'TESTING: {title}', command_context.channel)
            text = search_rip_sources(rip.text)
            await send_embed(text, command_context.channel, EmbedDesc(title="Sources"))