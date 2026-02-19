
import discord
from discord import TextChannel, Thread, Guild
from datetime import datetime, timezone, timedelta

from bot_secrets import YOUTUBE_API_KEY, YOUTUBE_CHANNEL_NAME
from simpleQoC.qoc import performQoC, ffmpegExists, getFileMetadataMutagen, getFileMetadataFfprobe
from simpleQoC.metadata import countDupe, isDupe

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
    aliases=['commands', 'halp', 'test', 'helpme'],
)
async def help(args: list[str], command_context: CommandContext):

    result = "" 

    prefix = get_config("prefix")
    qoc_emote = get_qoc_emoji(command_context.channel.guild)

    if len(args):

        search_input = args[0].lower()

        command_info = find_command_info(search_input)
        if not command_info:
            return await send(f'No command named {search_input}', command_context.channel)

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
                        result += f' **{info.format}**'

                    if len(info.brief):
                        brief = parse_emojis_in_string(info.brief, command_context.channel.guild)
                        result += f': {brief}'

        qoc_channel_ids = get_channel_ids_of_types(['QOC', 'PROXY_QOC'])
        qoc_channels_strings: list[str] = [] 
        for id in qoc_channel_ids:
            channel = bot.get_channel(int(id))
            if channel:
                qoc_channels_strings.append(channel.jump_url)

        result += '\n\n__**Legend:**__'
        result += '\n**<argument>**: Required argument'
        result += '\n**[argument]**: Optional argument'
        result += f'\n{qoc_emote}: Command only accessible in QoC channels:'
        result += f'\n{" ".join(qoc_channels_strings)}'
        result += f'\n\n*To learn more about a command, use `{prefix}help <command>`*'

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
    RANDOM = auto()

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

    channel = await get_qoc_channel(command_context.channel)
    if channel is None: return

    qoc_rips = await get_qoc_rips(channel, GetRipsDesc(typing_channel=command_context.channel))

    is_spec_overdue_days = get_config('spec_overdue_days')
    is_overdue_days = get_config('overdue_days')
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

    selected_index = 0
    if roundup_desc.roundup_filter_type == RoundupFilterType.RANDOM:
        selected_index = random.randint(0, len(qoc_rips) - 1)

    result = ""

    for i, qoc_rip in enumerate(qoc_rips):

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
            case RoundupFilterType.RANDOM:
                is_valid = (i == selected_index)

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
    command_type=CommandType.ROUNDUP,
    aliases = ['down_taunt', 'qoc', 'qocparty', 'roudnup', 'links', 'list', 'ls'],
    brief="Show all Qoc rips",
)
async def roundup(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc()
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.ROUNDUP,
    brief="Show QoC rips you've pinned :pushpin:",
)
async def mypins(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYPINS,
                               message_author_id = command_context.user.id, not_found_message = "No pins are yours.")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.ROUNDUP,
    brief="Show QoC rips you've wrenched :fix: :alert:",
)
async def myfixes(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYFIXES, \
                               user_id = command_context.user.id, conditional_string = "with wrenches")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.ROUNDUP,
    brief="Show QoC rips you've not reviewed",
)
async def myfresh(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.MYFRESH, \
                               user_id = command_context.user.id, conditional_string = "not reviewed")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.ROUNDUP,
    aliases = ['blank', 'bald', 'clean', 'noreacts'],
    brief="Show QoC rips nobody's reviewed",
)
async def fresh(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.FRESH, \
            not_found_message = "No fresh rips.")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.ROUNDUP,
    brief="Show QoC rips with at least one :check: and one :reject:",
)
async def spicy(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SPICY, \
            not_found_message = "No spicy rips :(")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.ROUNDUP,
    format="<search text>",
    brief="Search QoC rips titles",
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
    command_type=CommandType.ROUNDUP,
    brief="Show QoC email rips",
    aliases=["email"]
)
async def emails(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.SEARCH_AUTHOR, \
            search_key= "email",\
            not_found_message = "No emails.")
    await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.ROUNDUP,
    format="<event name>",
    brief="Search QoC event rips",
    aliases=["event"],
    desc="This can also be used as a general purpose author line search tool. Does not need quotes.",
    examples=["christmas", "secret", "deez nuts day", "ahmayk"]
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
    command_type=CommandType.ROUNDUP,
    brief="Show QoC rips with :check:",
    desc="does not consider :goldcheck: or check number requirements such as :7check:",
)
async def checks(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactionType.CHECK, not_found_message="No checks found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.ROUNDUP,
    brief="Show QoC rips without :check:",
    aliases=["nocheck"]
)
async def nochecks(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.NOTHASREACT, \
                               reaction_type=ReactionType.CHECK, not_found_message="No non-checked rips found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.ROUNDUP,
    brief="Show QoC rips with :reject:",
)
async def rejects(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactionType.REJECT, not_found_message="No rejected rips found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.ROUNDUP,
    brief="Show QoC rips without :reject:",
    aliases=["noreject"]
)
async def norejects(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.NOTHASREACT, \
                               reaction_type=ReactionType.REJECT, not_found_message="No non-rejected rips found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.ROUNDUP,
    brief="Show QoC rips with :fix:",
    aliases=['wrenches', 'fix', 'wrench']
)
async def fixes(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactionType.FIX, not_found_message="No wrenches found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.ROUNDUP,
    brief="Show QoC rips without :fix:",
    aliases=["nowrenches", "nofix", "nowrench"]
)
async def nofixes(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.NOTHASREACT, \
                               reaction_type=ReactionType.FIX, not_found_message="No non-fix rips found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.ROUNDUP,
    brief="Show QoC rips with :stop:",
)
async def stops(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.HASREACT, \
                               reaction_type=ReactionType.STOP, not_found_message="No octogons found.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.ROUNDUP,
    brief=f'Show QoC rips pinned over %overdue_days% days'
)
async def overdue(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.OVERDUE, not_found_message="No overdue rips.")
    await send_roundup(roundup_desc, command_context)

@command(
    command_type=CommandType.ROUNDUP,
    brief=f'Show a random QoC rip',
    aliases=['random', 'lucky', 'letsgogambling!']
)
async def randompull(args: list[str], command_context: CommandContext):
    roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.RANDOM, not_found_message="No gambling today, sorry!")
    await send_roundup(roundup_desc, command_context)

# ============ Counting Commands ============== #

@command(
    command_type=CommandType.STATS,
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
        rips = await get_suborqueue_rips_fast(qoc_channel, GetRipsDesc(typing_channel=command_context.channel))
        pincount = len(rips)

        if (pincount < 1):
            result = "`* Determination.`"
        else:
            result = f"`* {pincount} left.`"

        result += proxy
        await send(result, command_context.channel)


@command(
    command_type=CommandType.STATS,
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
        rips = await get_suborqueue_rips_fast(qoc_channel, GetRipsDesc(typing_channel=command_context.channel))
        result = f"You can pin {get_config('soft_pin_limit') - len(rips)} more rips until I start complaining about pin space."
        result += proxy
        await send(result, command_context.channel)

@command(
    command_type=CommandType.STATS,
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
    rips = await get_suborqueue_rips_fast(channel, GetRipsDesc(typing_channel=command_context.channel))
    count = len(rips)

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

class SendSubOrQueueDesc(NamedTuple):
    suborqueue_rip_filter_type: SubOrQueueRipFilterType = SubOrQueueRipFilterType.NULL 
    reaction_type: ReactionType = ReactionType.NULL 
    channel_link: str = ""
    channel_types: List[str] = []
    search_key: str = ""
    not_found_message: str = ""

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
    count = 0

    prefix = desc.search_key.lower()
    qoc_emote = get_qoc_emoji(command_context.channel.guild)

    for channel_id in channel_ids:
        channel = bot.get_channel(channel_id)
        if channel is None: continue

        rips = await get_suborqueue_rips(channel, GetRipsDesc(typing_channel=command_context.channel))

        result += f'<#{channel_id}>:\n'

        for suborqueue_rip in rips:

            rip_title = get_rip_title(suborqueue_rip.text)
            rip_author = get_raw_rip_author(suborqueue_rip.text)

            is_valid = False
            match(desc.suborqueue_rip_filter_type):
                case SubOrQueueRipFilterType.HASREACT:
                    is_valid = suborqueue_rip_has_reaction(desc.reaction_type, suborqueue_rip)
                case SubOrQueueRipFilterType.UNSENT:
                    is_valid = line_contains_substring(rip_author, 'email') and \
                            not suborqueue_rip_has_reaction(ReactionType.EMAILSENT, suborqueue_rip)
                case SubOrQueueRipFilterType.SEARCH_TITLE:
                    is_valid = line_contains_substring(rip_title, desc.search_key)
                case SubOrQueueRipFilterType.SEARCH_AUTHOR:
                    is_valid = line_contains_substring(rip_author, desc.search_key)
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
        if len(desc.not_found_message):
            not_found_message = desc.not_found_message
        await send(not_found_message, command_context.channel)
    else:
        await send_embed(result, command_context.channel, EmbedDesc(expires=True))


@command(
    command_type=CommandType.SUBS,
    public=True,
    format="<search text>",
    brief='Search submission rip titles',
    desc="Does not need quotes.",
    aliases=['search_sub'],
)
async def search_subs(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Inclue what you want to search for! I'll search for it in the titles of submitted rips.", \
                           command_context.channel)

    input_text = "".join([str(s) for s in args])
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SEARCH_TITLE, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'], \
                              search_key = input_text, \
                              not_found_message = f'No submissions containing `{input_text}` in title found.')
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.SUBS,
    public=True,
    format="<event text>",
    brief='Search for submission event rips',
    desc="This can also be used as a general purpose author line search tool. Does not need quotes.",
    aliases=['event_sub'],
)
async def event_subs(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please include the event name tagged in submitted rips.", \
                           command_context.channel)

    input_text = "".join([str(s) for s in args])
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SEARCH_AUTHOR, \
                              channel_types = ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'], \
                              search_key = input_text, \
                              not_found_message = f'No submissions containing `{input_text}` in author line found.')
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
                              reaction_type = ReactionType.THUMBNAIL)
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    brief='Show queued rips with an alert react :alert:',
)
async def alerts(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.HASREACT, \
                              channel_types = ["QUEUE"], \
                              reaction_type = ReactionType.ALERT)
    await send_suborqueue_rips(desc, command_context)


@command(
    command_type=CommandType.QUEUE,
    public=True,
    brief='Show queued rips with a metadata react :metadata:',
)
async def metadata(args: list[str], command_context: CommandContext):
    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.HASREACT, \
                              channel_types = ["QUEUE"], \
                              reaction_type = ReactionType.METADATA)
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
    format="<prefix>",
    brief='Search queued rips startting with prefix',
    desc='The prefix can contain spaces.',
    examples=['e', 'Level', 'deez nuts']
)
async def scout(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please provide a prefix. I'll find approved rips that start with it.", \
                           command_context.channel)

    input_text = "".join([str(s) for s in args])

    desc = SendSubOrQueueDesc(suborqueue_rip_filter_type = SubOrQueueRipFilterType.SCOUT, \
                              channel_types = ['QUEUE'], \
                              search_key = input_text, \
                              not_found_message = f'No approved rips starting with {input_text} found.')
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

    suborqueue_rips = await get_suborqueue_rips_fast(channel, GetRipsDesc(typing_channel=command_context.channel))

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
        await send("No approved rips found.", command_context.channel)
    else:
        await send_embed(result, command_context.channel, EmbedDesc(expires=True))

##TODO: (Ahmayk) subs_all [channel_link]

# ============ Basic QoC commands ============== #

##TODO: (Ahmayk) vet command UX needs to be refactored it's confusing as hell 

@command(
    command_type=CommandType.STATS,
    brief='Vet all QoC rips for bitrate and clipping issues',
)
async def vet(args: list[str], command_context: CommandContext):
    prefix = get_config("prefix")
    if len(args):
        return await send(f"WARNING: ``{prefix}vet`` takes no argument. Did you mean to use ``{prefix}vet_msg`` or ``{prefix}vet_url``?", command_context.channel)
    
    if command_context.message_reference:
        return await send(f"WARNING: ``{prefix}vet`` takes no argument (nor replies). Did you mean to use ``{prefix}vet_msg`` or ``{prefix}vet_url``?", command_context.channel)
    
    await vet_from(args, command_context)


@command(
    command_type=CommandType.STATS,
    format='[message link]',
    brief='Vet rips in a queue starting from message link',
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
        rips = await get_suborqueue_rips_fast(channel, GetRipsDesc())

        for rip in rips:
            if not vet_all_pins and rip.created_at < from_timestamp:
                continue
            qcCode, qcMsg, _ = await check_qoc(rip.text, False)

            if qcCode != 0:
                rip_title = get_rip_title(rip.text)
                verdict = code_to_verdict(qcCode, qcMsg)
                link = format_message_link(channel.guild.id, channel.id, rip.message_id)
                await send("**Rip**: **[{}]({})**\n**Verdict**: {}\n{}\n-# React {} if this is resolved.".format(rip_title, link, verdict, qcMsg, DEFAULT_CHECK), command_context.channel)

        if len(rips) == 0:
            await send("No pinned rips found to QoC.", command_context.channel)
        else:
            await send("Finished QoC-ing. Please note that these are only automated detections - you should verify the issues in Audacity and react manually.", command_context.channel)

@command(
    command_type=CommandType.STATS,
    brief='Vet all QoC rips at once with summary',
)
async def vet_all(args: list[str], command_context: CommandContext):

    if not ffmpegExists():
        return await send("WARNING: ffmpeg command not found on the bot's server. Please contact the developers.", command_context.channel)

    async with command_context.channel.typing():
        roundup_desc = RoundupDesc(roundup_filter_type = RoundupFilterType.VET_ALL)
        await send_roundup(roundup_desc, command_context)


@command(
    command_type=CommandType.STATS,
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

        verdict, msg = await check_qoc_and_metadata(message.content, message.id, str(message.author), True)
        rip_title = get_rip_title(message.content)

        await send("**Rip**: **{}**\n**Verdict**: {}\n**Comments**:\n{}".format(rip_title, verdict, msg), command_context.channel)


@command(
    command_type=CommandType.STATS,
    format='<url to rip>',
    brief='Vet rip in audio url',
)
async def vet_url(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Error: Please provide an url to a rip", command_context.channel)

    urls = extract_rip_link(args[0])

    if not len(urls):
        return await send(f'Error: no url found in {args[0]}', command_context.channel)

    async with command_context.channel.typing():
        code, msg = await run_blocking(performQoC, urls[0])
        verdict = code_to_verdict(code, msg)
        await command_context.channel.send("**Verdict**: {}\n**Comments**:\n{}".format(verdict, msg))


@command(
    command_type=CommandType.STATS,
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

    q = 0
    queue_channels = get_channel_ids_of_types(['QUEUE'])
    for queue_channel_id in queue_channels:
        queue_channel = server.get_channel(queue_channel_id)
        if queue_channel:
            suborqueue_rips = await get_suborqueue_rips_fast(queue_channel, GetRipsDesc(typing_channel=command_context.channel))
            q += sum([isDupe(description, get_rip_description(r.text)) for r in suborqueue_rips if r.message_id != message.id])

    # https://codegolf.stackexchange.com/questions/4707/outputting-ordinal-numbers-1st-2nd-3rd#answer-4712 how
    ordinal = lambda n: "%d%s" % (n,"tsnrhtdd"[(n//10%10!=1)*(n%10<4)*n%10::4])

    await send(f"**Rip**: **{rip_title}**\nFound {p + q} rips of the same track ({p} on the channel, {q} in queues). This is the {ordinal(p + q + 1)} rip of this track.", command_context.channel)


##TODO: (Ahmayk) redesign this command considering YouTube api limits (as is it's too easy to start a process that bricks bot's quota)
"""
@bot.command(name='scan', brief='')
async def scan(ctx: Context, channel_link: str = None, start_index: int = None, end_index: int = None):
    # Scan through a submission or queue channel for metadata issues.
    # Channel link must be provided as argument.
    # Accepts two optional arguments to specify the range of rips to scan through, if the channel has too many rips.

    # - `start_index`: First rip to look at (inclusive). Index 1 means start from the oldest rip.
    # - `end_index`: Last rip to look at (inclusive). Index 100 means scan until and including the 100th oldest rip. If this is not provided, scan to the latest rip.
    if not channel_is_types(ctx.channel, ['QOC', 'PROXY_QOC']): return

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
"""


@command(
    command_type=CommandType.STATS,
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
            long_message = split_long_message("**Rip**: **{}**\n**File metadata**:\n{}".format(rip_title, msg), get_config('character_limit'))
            for line in long_message:
                await send(line, command_context.channel)


@command(
    command_type=CommandType.STATS,
    format='<message url>',
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
            code, msg = await run_blocking(getFileMetadataFfprobe, urls)
        else:
            code, msg = await run_blocking(getFileMetadataMutagen, urls)
        
        if code == -1:
            await send(f'Error reading URL: {msg}', command_context.channel)
        else:
            await send(f'**File metadata**:\n{msg}', command_context.channel)

@command(
    command_type=CommandType.MANAGEMENT,
    brief='Check that all rips are in bot\'s cache',
)
async def validate_cache(args: list[str], command_context: CommandContext):
    await send("Validating cache of rips in all channels...", command_context.channel)

    async with command_context.channel.typing():
        validate_result = await validate_cache_all()
        if len(validate_result):
            await send(f'{validate_result}\n**Validation complete. Issues found.**', command_context.channel)
        else:
            await send("Validation complete! No issues found.", command_context.channel)

@command(
    command_type=CommandType.MANAGEMENT,
    format='<channel link>',
    brief='Refetches all rip data from discord for a channel',
)
async def reset_cache(args: list[str], command_context: CommandContext):

    if not len(args):
        return await send("Please provide a link to the channel you want to reset the cache for.", command_context.channel)

    channel_link = args[0] 

    channel_id, msg = parse_channel_link(channel_link, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD', 'QUEUE', 'QOC'], False)
    if len(msg) > 0:
        await send(msg, command_context.channel)
    if not channel_id:
        return
    
    channel = bot.get_channel(channel_id)
    init_channel_cache(channel.id)
    channel_info = get_channel_info(channel)

    await send("Rebuilding cache...", command_context.channel)

    suborqueue_count_old = 0 
    async with CACHE_LOCK_SUBORQUEUE[channel.id]:
        suborqueue_count_old = len(RIP_CACHE_SUBORQUEUE[channel.id])
    qoc_count_old = 0
    async with CACHE_LOCK_QOC[channel.id]:
        qoc_count_old = len(RIP_CACHE_QOC[channel.id])

    return_message = f'Cache rebuilt!'

    suborqueue_rips = await get_suborqueue_rips(channel, GetRipsDesc(typing_channel=command_context.channel, rebuild_cache=True))
    return_message += f'\nSubOrQueue rip count: {suborqueue_count_old} => {len(suborqueue_rips)}' 

    if channel_info.is_cache_qoc:
        qoc_rips = await get_qoc_rips(channel, GetRipsDesc(typing_channel=command_context.channel, rebuild_cache=True))
        return_message += f'\nQoc rip count: {qoc_count_old} => {len(qoc_rips)}' 

    await send(return_message, command_context.channel)

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
                + " [pinlimit must die mode {}]".format("enabled" if channel_config.pinlimit_must_die_mode else "disabled") if 'QOC' in channel_config.types else ""
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
    
    count = 0
    async for message in command_context.channel.history(limit = search_limit):
        if message.author == bot.user and message.embeds:
            await message.delete()
            count += 1
    
    await send(f"Removed {count} embed messages.", command_context.channel)


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

@command(
    command_type=CommandType.STATS,
    public=True,
    format="[all]",
    brief="Show # of rips in channels",
    desc="include `all` or `a` to show rips in all queue channels as well",
)
async def stats(args: list[str], command_context: CommandContext):

    ret = "**QoC channels**\n"

    qoc_channels = get_channel_ids_of_types(['QOC'])
    sub_channels = get_channel_ids_of_types(['SUBS', 'SUBS_THREAD', 'SUBS_PIN'])
    for channel_id in qoc_channels:
        if channel_id not in sub_channels:
            team_count = 0
            email_count = 0
            channel = bot.get_channel(channel_id)
            if channel:
                rips = await get_suborqueue_rips_fast(channel, GetRipsDesc(typing_channel=command_context.channel))
                for rip in rips:
                    author = get_rip_author(rip.text, rip.message_author_name)
                    if 'email' in author.lower():
                        email_count += 1
                    else:
                        team_count += 1
                ret += f"- <#{channel_id}>: **{team_count + email_count}** rips\n  - {team_count} team subs\n  - {email_count} email subs\n"

    ret += "**Submission channels**\n"
    for channel_id in sub_channels:
        ret += await get_suborqueue_rip_stats_string(channel_id, command_context.channel)

    ##TODO: (Ahmayk) considering any arguemnts to show everything is kind of jank, but maybe fine since that's what ppl are used to
    if len(args):
        ret += "**Queues**\n"
        queue_channels = get_channel_ids_of_types(['QUEUE'])
        for channel_id in queue_channels:
            ret += await get_suborqueue_rip_stats_string(channel_id, command_context.channel)

    await send(ret, command_context.channel)


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
    await send("Goodnight!", command_context.channel)
    await bot.close()

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