#!/usr/bin/python3
import discord
from discord import Message, Thread, TextChannel, Reaction
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
from typing import NamedTuple
from enum import Enum, auto
import math
import json
import os

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

latest_pin_time = None # Keeps track of the last pinned message's time to distinguish between pins and unpins. To be updated on ready.
latest_scan_time = None

bot = commands.Bot(
    command_prefix='!',
    help_command = commands.DefaultHelpCommand(no_category = 'Commands'), # Change only the no_category default string
    intents = discord.Intents.all() # This was a change necessitated by an update to discord.py :/
    # https://stackoverflow.com/questions/71950432/how-to-resolve-the-following-error-in-discord-py-typeerror-init-missing
    # Also had to enable MESSAGE CONENT INTENT https://stackoverflow.com/questions/71553296/commands-dont-run-in-discord-py-2-0-no-errors-but-run-in-discord-py-1-7-3
    # 10/28/22 They changed it again!!! https://stackoverflow.com/questions/73458847/discord-py-error-message-discord-ext-commands-bot-privileged-message-content-i
)

bot.remove_command('help') # get rid of the dumb default !help command

#===============================================#
#                    EVENTS                     #
#===============================================#

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    print('#################################')

    # Update latest_pin_time in order to keep track of 
    global latest_pin_time
    latest_pin_time = datetime.now(timezone.utc)

    await write_log("Good morning!")

import traceback
@bot.event
async def on_error(event, *args, **kwargs):
    # https://stackoverflow.com/a/60031624
    await write_log('{}```py\n{}\n```'.format(event, traceback.format_exc()), embed=True)

@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    error_string = str(error)
    error_data = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    print(f"\033[91m {error_data}\033[0m")

    description = f":boom: Intriging! I have encountered an unexpected error! ```{error}```"
    # NOTE: (Ahmayk) hardcoding message limit to not risk a crash
    #as of writing, current implementation of _get_config() calls to disk
    messages = split_long_message(description, 2000)
    for message in messages:
        await ctx.channel.send(message)

    log_channel = await bot.fetch_channel(LOG_CHANNEL)
    messages = split_long_message(error_data, 2000 - len(error_string))
    for i, message in enumerate(messages):
        string = f'```py\n{message}\n```'
        if i == 0:
            string = f'**{error_string}**\n{string}'
        await log_channel.send(string)

_bot_close = bot.close
async def close_with_log(self: commands.Bot):
    await write_log("Good night!")
    return await _bot_close()

import types
bot.close = types.MethodType(close_with_log, bot)


@bot.event
async def on_guild_channel_pins_update(channel: typing.Union[GuildChannel, Thread], last_pin: datetime):
    if not channel_is_type(channel, 'ROUNDUP'):
        return
    
    global latest_pin_time
    if last_pin is None or last_pin <= latest_pin_time:
        # print("Seems to be a message being unpinned")
        pass
    else:
        latest_pin_time = last_pin
        pin_list = [message async for message in channel.pins(limit=None)]
        if len(pin_list) < 1: return
        latest_msg = pin_list[0]

        SOFT_PIN_LIMIT = _get_config('soft_pin_limit')
        if len(pin_list) > SOFT_PIN_LIMIT:
            if _get_config("pinlimit_must_die_mode"):
                await latest_msg.unpin()
                await channel.send(f"**Error**: More than {SOFT_PIN_LIMIT} rips in pins. Unpinned.")
            else:
                await channel.send(f"**Warning**: More than {SOFT_PIN_LIMIT} rips pinned - please handle them first :(")
    
        verdict, msg = await check_qoc_and_metadata(latest_msg)

        # Send msg
        if len(verdict) > 0:
            rip_title = get_rip_title(latest_msg)
            link = f"<https://discordapp.com/channels/{str(channel.guild.id)}/{str(channel.id)}/{str(latest_msg.id)}>"
            await channel.send("**Rip**: **[{}]({})**\n**Verdict**: {}\n{}-# React {} if this is resolved.".format(rip_title, link, verdict, msg, DEFAULT_CHECK))

REACTION_CACHE = dict()

class ReactionData(NamedTuple):
    name: str
    user_id: int

class ReactionCacheAction(Enum):
    REACTION_ADD = auto()
    REACTION_REMOVE = auto()
    EMOJI_CLEAR = auto()

def init_reaction_data(emoji: Emoji, user_id: int) -> typing.ReactionData:
    name = emoji
    if emoji and hasattr(emoji, "name"):
        name = emoji.name
    return ReactionData(name, user_id)

def update_reaction_cache(reaction_cache_action: ReactionCacheAction, message_id: int, user_id: int, emoji: Emoji):

    reaction_datas = []
    if message_id in REACTION_CACHE:
        reaction_datas = REACTION_CACHE.get(message_id)

    reaction_data = init_reaction_data(emoji, user_id)

    match(reaction_cache_action):
        case ReactionCacheAction.REACTION_ADD:
            reaction_datas.append(reaction_data)
            REACTION_CACHE.update({message_id: reaction_datas})

        case ReactionCacheAction.REACTION_REMOVE:
            if message_id in REACTION_CACHE and reaction_data in reaction_datas:
                reaction_datas.remove(reaction_data)
                if len(reaction_datas) == 0:
                    del REACTION_CACHE[message_id]
                else:
                    REACTION_CACHE.update({message_id: reaction_datas})

        case ReactionCacheAction.EMOJI_CLEAR:
            if message_id in REACTION_CACHE:
                for cached_reaction in reaction_datas:
                    if cached_reaction.name == reaction_data.name:
                        reaction_datas.remove(cahced_reaction)
                if len(reaction_datas) == 0:
                    del REACTION_CACHE[message_id]
                else:
                    REACTION_CACHE.update({message_id: reaction_datas})

        case _:
            write_log("WARNING: Unimplemented ReactionCacheAction: " + reaction_cache_action)


@bot.listen('on_raw_reaction_add')
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    update_reaction_cache(ReactionCacheAction.REACTION_ADD, payload.message_id, payload.user_id, payload.emoji)

@bot.listen('on_raw_reaction_remove')
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    update_reaction_cache(ReactionCacheAction.REACTION_REMOVE, payload.message_id, payload.user_id, payload.emoji)

@bot.listen('on_raw_reaction_clear_emoji')
async def on_raw_reaction_clear_emoji(payload: discord.RawReactionClearEmojiEvent):
    update_reaction_cache(ReactionCacheAction.EMOJI_CLEAR, payload.message_id, payload.user_id, payload.emoji)

@bot.listen('on_raw_reaction_clear')
async def on_raw_reaction_clear(payload: discord.RawReactionClearEvent):
    if payload.message_id in REACTION_CACHE:
        del REACTION_CACHE[payload.message_id]

@bot.listen('on_raw_message_delete')
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    if payload.message_id in REACTION_CACHE:
        del REACTION_CACHE[payload.message_id]

#===============================================#
#                   COMMANDS                    #
#===============================================#

# ============ Aggregate commands ============= #

@bot.command(name='roundup', aliases = ['down_taunt', 'qoc', 'qocparty', 'roudnup'], brief='displays all rips in QoC')
async def roundup(ctx: Context, optional_time = None):
    """
    Roundup command. Retrieve all pinned messages (except the first one) and their reactions.
    Accepts an optional argument to control embed's display time *in hours*.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("roundup", ctx.message.author.name)

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    channel = await get_roundup_channel(ctx)
    if channel is None: return

    async with ctx.channel.typing():
        all_pins = await process_pins(channel, True)
        result = ""
        for rip_id, rip_info in all_pins.items():
            result += make_markdown(rip_info, True)
        
        if result != "":
            await send_embed(ctx.channel, result, time)
        else:
            await ctx.channel.send("No rips.")


@bot.command(name='links', aliases = ['list', 'ls'], brief='roundup but quicker')
async def links(ctx: Context, optional_time = None):
    """
    Retrieve all pinned messages (except the first one) without showing reactions.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("links", ctx.message.author.name)

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    channel = await get_roundup_channel(ctx)
    if channel is None: return

    async with ctx.channel.typing():
        all_pins = await process_pins(channel, False)
        result = ""
        for rip_id, rip_info in all_pins.items():
            result += make_markdown(rip_info, False)
        
        if result != "":
            await send_embed(ctx.channel, result, time)
        else:
            await ctx.channel.send("No rips.")


@bot.command(name='mypins', brief='displays rips you\'ve pinned')
async def mypins(ctx: Context, optional_time = None):
    """
    Retrieve all messages pinned by the command author.
    """
    await filter_command(ctx, 'mypins', (lambda ctx, rip_info: rip_info["PinMiser"] == ctx.author.name), True, optional_time)


@bot.command(name='mypins_legacy', brief='displays rips you\'ve pinned (without reacts)')
async def mypins_legacy(ctx: Context, optional_time = None):
    """
    Retrieve all messages pinned by the command author without reacts (legacy behaviour).
    """
    await filter_command(ctx, 'mypins', (lambda ctx, rip_info: rip_info["PinMiser"] == ctx.author.name), False, optional_time)


@bot.command(name='search', brief='search for pinned rips with text in title')
async def search(ctx: Context, search_key: str, optional_time = None):
    """
    Search for pinned messages by rip title.
    Supports `|` for multiple search keys.
    """
    search_keys = search_key.split('|')
    await filter_command(ctx, 'search', 
            (lambda ctx, rip_info: any([line_contains_substring(rip_info["Title"], key) for key in search_keys])),
            True, optional_time)


@bot.command(name='search_subs', aliases = ['search_sub'], brief='search for submissions with text in title')
async def search_subs(ctx: Context, search_key: str, sub_channel_link: str = None, optional_time = None):
    """
    Search for submissions by rip title.
    Supports `|` for multiple search keys.
    """
    search_keys = search_key.split('|')
    await filter_sub_command(ctx, 'search_subs', 
            (lambda rip: any([line_contains_substring(get_raw_rip_title(rip), key) for key in search_keys])), 
            sub_channel_link, optional_time)


@bot.command(name='emails', brief='displays emails')
async def emails(ctx: Context, optional_time = None):
    """
    Retrieve all messages that are tagged as email.
    """
    await events(ctx, "email", optional_time)


@bot.command(name='events', aliases = ['event'], brief='displays event rips')
async def events(ctx: Context, event: str = None, optional_time = None):
    """
    Retrieve all messages that are tagged as for an event.
    The provided string must appear in the rip's author label (case insensitive).
    Supports `|` for multiple search keys.
    """
    if channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']) and event is None:
        await ctx.channel.send("Error: Please indicate the event name. Rips should be tagged with this name.")
        return
    
    event_keys = event.split('|')
    await filter_command(ctx, 'events', 
            (lambda ctx, rip_info: any([line_contains_substring(rip_info["Author"], e) for e in event_keys])), 
            True, optional_time)


@bot.command(name='event_subs', aliases = ['event_sub'], brief='displays event submissions from linked channel')
async def event_subs(ctx: Context, event: str = None, sub_channel_link: str = None, optional_time = None):
    """
    Retrieve all messages in a submission channel that are tagged as for an event.
    The provided string must appear in the rip's author label (case insensitive).
    Supports `|` for multiple search keys.
    """
    if channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']) and event is None:
        await ctx.channel.send("Error: Please indicate the event name. Rips should be tagged with this name.")
        return
    
    event_keys = event.split('|')
    await filter_sub_command(ctx, 'event_subs', 
            (lambda rip: any([line_contains_substring(get_raw_rip_author(rip), e) for e in event_keys])), 
            sub_channel_link, optional_time)


@bot.command(name='myfixes', brief='displays rips you\'ve wrenched')
async def myfixes(ctx: Context, user_id: str = "", optional_time = None):
    """
    Retrieve all messages with fix or alert reactions by the command author.
    """
    async def reactions_contain_fix_by_user(ID: int, reactionDatas: typing.List[ReactionData]):
        for r in reactionDatas:
            if (react_is_fix(r.name) or react_is_alert(r.name)) and ID == r.user_id:
                return True
        return False

    await react_conditional_command(ctx, 'myfixes', user_id, reactions_contain_fix_by_user, 'with wrenches', optional_time)


@bot.command(name='myfresh', brief='displays rips you\'ve not reviewed')
async def myfresh(ctx: Context, user_id: str = "", optional_time = None):
    """
    Retrieve all messages with no review reactions by the command author.
    """
    async def reactions_all_not_by_user(ID: int, reactionDatas: typing.List[ReactionData]):
        for r in reactionDatas:
            if (react_is_check(r.name) or react_is_goldcheck(r.name) or react_is_fix(r.name) or react_is_alert(r.name) or react_is_reject(r.name)) and ID == r.user_id:
                return False
        return True

    await react_conditional_command(ctx, 'myfresh', user_id, reactions_all_not_by_user, 'not reviewed', optional_time)


@bot.command(name="fresh", aliases = ['blank', 'bald', 'clean', 'noreacts'], brief='rips with no reacts yet')
async def fresh(ctx: Context, optional_time = None):
    """
    Retrieve all pinned messages (except the first one) with 0 reactions.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("fresh", ctx.message.author.name)

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    channel = await get_roundup_channel(ctx)
    if channel is None: return

    result = ""

    async with ctx.channel.typing():
        pin_list = await get_pins(channel)

        for pinned_message in pin_list:
            mesg = await channel.fetch_message(pinned_message.id)
            if len(mesg.reactions) < 1:
                title = get_rip_title(mesg)
                link = f"<https://discordapp.com/channels/{str(channel.guild.id)}/{str(channel.id)}/{str(pinned_message.id)}>"
                result = result + f'**[{title}]({link})**\n'

        if result != "":
            await send_embed(ctx.channel, result, time)
        else:
            await ctx.channel.send("No fresh rips.")


@bot.command(name='wrenches', aliases = ['fix'])
async def wrenches(ctx: Context, optional_time = None):
    """
    Retrieve all pinned messages (except the first one) with :fix: reactions.
    """
    await react_command(ctx, 'fix', react_is_fix, "No wrenches found.", optional_time)

@bot.command(name='stops')
async def stops(ctx: Context, optional_time = None):
    """
    Retrieve all pinned messages (except the first one) with :stop: reactions.
    """
    await react_command(ctx, 'stop', react_is_stop, "No octogons found.", optional_time)

@bot.command(name='checks')
async def checks(ctx: Context, optional_time = None):
    """
    Retrieve all pinned messages (except the first one) with :check: reactions.
    """
    await react_command(ctx, 'check', react_is_check, "No checks found.", optional_time)

@bot.command(name='rejects')
async def rejects(ctx: Context, optional_time = None):
    """
    Retrieve all pinned messages (except the first one) with :reject: reactions.
    """
    await react_command(ctx, 'reject', react_is_reject, "No rejected rips found.", optional_time)


@bot.command(name='overdue', brief=f'display rips that have been pinned for over X days')
async def overdue(ctx: Context, optional_time = None):
    """
    Retrieve all pinned messages (except the first one) that are overdue.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("overdue", ctx.message.author.name)

    channel = await get_roundup_channel(ctx)
    if channel is None: return

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    result = ""

    async with ctx.channel.typing():
        all_pins = await process_pins(channel, True)
        result = ""

        for rip_id, rip_info in all_pins.items():
            if rip_info["Indicator"] == OVERDUE_INDICATOR:
                result += make_markdown(rip_info, True)
        
        if result != "":
            await send_embed(ctx.channel, result, time)
        else:
            await ctx.channel.send("No overdue rips.")


# ============ Pin count commands ============== #

@bot.command(name='count', brief="counts all pinned rips")
async def count(ctx: Context):
    """
    Count the number of pinned messages containing rip links.
    """
    heard_command("count", ctx.message.author.name)

    if channel_is_type(ctx.channel, 'PROXY_ROUNDUP'):
        channel = await get_roundup_channel(ctx)
        if channel is None: return
        else: proxy = f"\n-# Showing results from <#{channel.id}>."
    else:
        channel = ctx.channel
        proxy = ""

    async with ctx.channel.typing():
        pin_list = await get_pins(channel)
        pincount = len(pin_list)

        if (pincount < 1):
            result = "`* Determination.`"
        else:
            result = f"`* {pincount} left.`"

        result += proxy
        await ctx.channel.send(result)


@bot.command(name='limitcheck', aliases=['pinlimit'], brief="pin limit checker")
async def limitcheck(ctx: Context):
    """
    Count the number of available pin slots.
    """
    heard_command("limitcheck", ctx.message.author.name)

    if channel_is_type(ctx.channel, 'PROXY_ROUNDUP'):
        channel = await get_roundup_channel(ctx)
        if channel is None: return
        else: proxy = f"\n-# Showing results from <#{channel.id}>."
    else:
        channel = ctx.channel
        proxy = ""

    async with ctx.channel.typing():
        pin_list = [message async for message in channel.pins(limit=None)]
        result = f"You can pin {_get_config('soft_pin_limit') - len(pin_list)} more rips until I start complaining about pin space."

        result += proxy
        await ctx.channel.send(result)


@bot.command(name='scout', brief='find approved rips with specific title prefix')
async def scout(ctx: Context, prefix: str = None, channel_link: str = None, optional_time = None):
    """
    Search queue channel for rips starting with the specific prefix (e.g. letter E).
    The prefix is case insensitive.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("scout", ctx.message.author.name)

    if prefix is None:
        await ctx.channel.send("Error: Please provide a prefix string.")
        return

    channel_id, msg = parse_channel_link(channel_link, ['QUEUE'])
    if len(msg) > 0:
        await ctx.channel.send(msg)
        return

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)
    
    channel = bot.get_channel(channel_id)
    if channel is None: await ctx.channel.send("Error: Invalid channel found. Contact bot developers to update list of channels.")

    async with ctx.channel.typing():
        rips: typing.List[Message] = []
        for t in ['msg', 'thread']:
            t_rips = await get_rips(channel, t)
            for k, v in t_rips.items():
                rips.extend(v)
        
        result = ""
        for rip in rips:
            rip_title = get_rip_title(rip)
            rip_link = f"<https://discordapp.com/channels/{str(ctx.guild.id)}/{str(channel_id)}/{str(rip.id)}>"
            if rip_title.lower().startswith(prefix.lower()):
                result += f'**[{rip_title}]({rip_link})**\n'

        if len(result) == 0:
            await ctx.channel.send(f"No approved rips starting with {prefix} found.")
        else:
            await send_embed(ctx.channel, result, time)


@bot.command(name='scout_stats', brief='summarize approved rips with specific letter prefix')
async def scout_stats(ctx: Context, channel_link: str = None, optional_time = None):
    """
    Display count of rips starting with each letter.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("scout_stats", ctx.message.author.name)

    channel_id, msg = parse_channel_link(channel_link, ['QUEUE'])
    if len(msg) > 0:
        await ctx.channel.send(msg)
        return

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    channel = bot.get_channel(channel_id)
    if channel is None: await ctx.channel.send("Error: Invalid channel found. Contact bot developers to update list of channels.")

    async with ctx.channel.typing():
        rips: typing.List[Message] = []
        for t in ['msg', 'thread']:
            t_rips = await get_rips(channel, t)
            for k, v in t_rips.items():
                rips.extend(v)
        
        count = {}
        for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ': # could have done string.ascii_uppercase but i dont think the alphabet is getting any updates
            count[letter] = 0

        for rip in rips:
            rip_title = get_raw_rip_title(rip)
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

        if len(rips) == 0:
            await ctx.channel.send("No approved rips found.")
        else:
            await send_embed(ctx.channel, result, time)


@bot.command(name='frames', brief='find approved rips with thumbnail reacts')
async def frames(ctx: Context, channel_link: str = None, optional_time = None):
    """
    Search queue channel for rips with "thumbnail needed" react.
    """
    heard_command("frames", ctx.message.author.name)
    await fetch_reaction_command(ctx, react_is_thumbnail, channel_link, optional_time)


@bot.command(name='alerts', brief='find approved rips with alert reacts')
async def alerts(ctx: Context, channel_link: str = None, optional_time = None):
    """
    Search queue channel for rips with alert react.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("alerts", ctx.message.author.name)
    await fetch_reaction_command(ctx, react_is_alert, channel_link, optional_time)


@bot.command(name='metadata', brief='find approved rips with metadata reacts')
async def metadata(ctx: Context, channel_link: str = None, optional_time = None):
    """
    Search queue channel for rips with metadata react.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("metadata", ctx.message.author.name)
    await fetch_reaction_command(ctx, react_is_metadata, channel_link, optional_time)


@bot.command(name='unsent', brief='find approved emails with no emailsent reacts')
async def unsent(ctx: Context, channel_link: str = None, optional_time = None):
    """
    Search queue channel for rips tagged email with no "approval email sent" react.
    """
    heard_command("unsent", ctx.message.author.name)
    await fetch_command(ctx, 
            lambda rip: line_contains_substring(get_raw_rip_author(rip), 'email') and not any(react_is_emailsent(r) for r in rip.reactions),
            channel_link, optional_time)


# ============ Basic QoC commands ============== #

@bot.command(name='vet', brief='scan pinned messages for bitrate and clipping issues')
async def vet(ctx: Context, optional_arg = None):
    """
    Find rips in pinned messages with bitrate/clipping issues and show their details
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return

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
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("vet_from", ctx.message.author.name)

    vet_all_pins = from_msg is None
    if not vet_all_pins:
        _, _, from_message, status = await parse_message_link(from_msg)
        if from_message is None:
            await ctx.channel.send(status)
            return
        from_timestamp = from_message.created_at

    channel = await get_roundup_channel(ctx)
    if channel is None: return

    if not ffmpegExists():
        await ctx.channel.send("WARNING: ffmpeg command not found on the bot's server. Please contact the developers.")
        return

    async with ctx.channel.typing():
        pin_list = await get_pins(channel)

        for pinned_message in pin_list:
            if not vet_all_pins and pinned_message.created_at < from_timestamp:
                continue
            qcCode, qcMsg, _ = await check_qoc(pinned_message, False)
            rip_title = get_rip_title(pinned_message)
            verdict = code_to_verdict(qcCode, qcMsg)

            if qcCode != 0:
                link = f"<https://discordapp.com/channels/{str(channel.guild.id)}/{str(channel.id)}/{str(pinned_message.id)}>"
                await ctx.channel.send("**Rip**: **[{}]({})**\n**Verdict**: {}\n{}\n-# React {} if this is resolved.".format(rip_title, link, verdict, qcMsg, DEFAULT_CHECK))

        if len(pin_list) == 0:
            await ctx.channel.send("No pinned rips found to QoC.")
        else:
            await ctx.channel.send("Finished QoC-ing. Please note that these are only automated detections - you should verify the issues in Audacity and react manually.")


@bot.command(name='vet_all', brief='vet all pinned messages and show summary')
async def vet_all(ctx: Context, optional_time = None):
    """
    Retrieve all pinned messages (except the first one) and perform basic QoC, giving emoji labels.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("vet_all", ctx.message.author.name)

    channel = await get_roundup_channel(ctx)
    if channel is None: return

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    if not ffmpegExists():
        await ctx.channel.send("WARNING: ffmpeg command not found on the bot's server. Please contact the developers.")
        return

    async with ctx.channel.typing():
        all_pins = await vet_pins(channel)
        result = ""
        for rip_id, rip_info in all_pins.items():
            result += make_markdown(rip_info, True)
        result += f"```\nLEGEND:\n{QOC_DEFAULT_LINKERR}: Link cannot be parsed\n{DEFAULT_CHECK}: Rip is OK\n{DEFAULT_FIX}: Rip has potential issues, see below\n{QOC_DEFAULT_BITRATE}: Bitrate is not 320kbps\n{QOC_DEFAULT_CLIPPING}: Clipping```"
        await send_embed(ctx.channel, result, time)


@bot.command(name='vet_msg', brief='vet a single message link')
async def vet_msg(ctx: Context, msg_link: str = None):
    """
    Perform basic QoC on a linked message.
    The first non-YouTube link found in the message is treated as the rip URL.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("vet_msg", ctx.message.author.name)

    if msg_link is None:
        await ctx.channel.send("Error: Please provide a link to message.")
        return

    async with ctx.channel.typing():
        server, channel, message, status = await parse_message_link(msg_link)
        if message is None:
            await ctx.channel.send(status)
            return

        verdict, msg = await check_qoc_and_metadata(message, True)
        rip_title = get_rip_title(message)

        await ctx.channel.send("**Rip**: **{}**\n**Verdict**: {}\n**Comments**:\n{}".format(rip_title, verdict, msg))


@bot.command(name='vet_url', brief='vet a single url')
async def vet_url(ctx: Context, url: str = None):
    """
    Perform basic QoC on an URL.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
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
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("count_dupe", ctx.message.author.name)

    if msg_link is None:
        await ctx.channel.send("Error: Please provide a link to message.")
        return

    async with ctx.channel.typing():
        server, channel, message, status = await parse_message_link(msg_link)
        if message is None:
            await ctx.channel.send(status)
            return

        playlistId = extract_playlist_id('\n'.join(message.content.splitlines()[1:])) # ignore author line
        description = get_rip_description(message)
        rip_title = get_rip_title(message)

        p, msg = await run_blocking(countDupe, description, YOUTUBE_CHANNEL_NAME, playlistId, YOUTUBE_API_KEY)
        if len(msg) > 0:
            await ctx.channel.send(msg)
            if check_queues is None: return

        if check_queues is not None:
            q = 0
            queue_channels = [k for k, v in CHANNELS.items() if 'QUEUE' in v]
            for queue_channel_id in queue_channels:
                queue_channel = server.get_channel(queue_channel_id)
                if queue_channel is None: continue
                queue_rips = await get_rips(queue_channel, 'msg')
                q += sum([isDupe(description, get_rip_description(r)) for r in queue_rips[queue_channel_id] if r.id != message.id])

                queue_thread_rips = await get_rips(queue_channel, 'thread')
                for thread, rips in queue_thread_rips.items():
                    q += sum([isDupe(description, get_rip_description(r)) for r in rips if r.id != message.id])

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
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
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

    if channel_is_type(channel, 'SUBS'): types = ['msg']
    elif channel_is_type(channel, 'SUBS_PIN'): types = ['pin']
    elif channel_is_type(channel, 'SUBS_THREAD'): types = ['thread']
    elif channel_is_type(channel, 'QUEUE'): types = ['msg', 'thread']

    rips = []
    for t in types:
        t_rips = await get_rips(channel, t)
        for k, v in t_rips.items():
            rips.extend(v)

    rips = rips.reverse()
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
        for message in rips:
            index += 1
            if (index < sInd):
                continue
            if (index > eInd):
                break
            
            rip_title = get_rip_title(message)

            mtCode, mtMsg = await check_metadata(message)
            if mtCode == -1:
                await write_log("Warning: cannot check metadata of message\nRip: {}\n{}".format(rip_title, mtMsg))

            if mtCode == 1:
                link = f"<https://discordapp.com/channels/{str(ctx.guild.id)}/{str(channel_id)}/{str(message.id)}>"
                await ctx.channel.send("**Rip**: **[{}]({})**\n**Verdict**: {}\n{}".format(rip_title, link, DEFAULT_METADATA, mtMsg))
        
        latest_scan_time = datetime.now(timezone.utc)
        await ctx.channel.send("Finished checking metadata of {} rips. Wait for ~30 minutes and contact bot developers if you wish to use this command again today.".format(eInd - sInd))


@bot.command(name='peek_msg', brief='print file metadata from message link')
async def peek_msg(ctx: Context, msg_link: str = None, use_ffprobe = None):
    """
    Prints the file metadata of the rip at linked message.
    The first non-YouTube link found in the message is treated as the rip URL.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("peek_msg", ctx.message.author.name)

    if msg_link is None:
        await ctx.channel.send("Error: Please provide a link to message.")
        return

    async with ctx.channel.typing():
        server, channel, message, status = await parse_message_link(msg_link)
        if message is None:
            await ctx.channel.send(status)
            return
        
        rip_title = get_rip_title(message)
        
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
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
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

@bot.command(name='help', aliases = ['commands', 'halp', 'test'])
async def help(ctx: Context):    
    async with ctx.channel.typing():
        result = "_**YOU ARE NOW QoCING:**_\n`!roundup [embed_minutes: float]`" + roundup.brief \
            + "\n`!links` " + links.brief \
            + "\n_**Special lists:**_\n`!mypins` " + mypins.brief \
            + "\n`!myfixes <user_id: str>` " + myfixes.brief + "\n`!myfresh <user_id: str>` " + myfresh.brief\
            + "\n`!search <arg1: str|arg2: str|...>` " + search.brief \
            + "\n`!emails` " + emails.brief + "\n`!events <arg1: str|arg2: str|...>` " + events.brief \
            + "\n`!checks`, `!rejects`, `!wrenches`, `!stops`" \
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
        await send_embed(ctx.channel, result, delete_after=None)


@bot.command(name='channel_list', brief='show channels and their supported commands')
async def channel_list(ctx: Context):
    async with ctx.channel.typing():
        channels = [f"<#{channel_id}>: " + ", ".join(types) for channel_id, types in CHANNELS.items()]
        message = [
            "_**Command channel types**_",
            "`ROUNDUP`: QoC-type channels with rips pinned. All QoC tools are available here.",
            "`PROXY_ROUNDUP`: Allows running ROUNDUP commands in a different channel (and embed last longer by default).",
            "`DEBUG`: For developer testing purposes.",
            "_**Stats channel types**_",
            "`QOC`: QoC channel. Rips are pinned.",
            "`SUBS`: Submission channel. Rips are posted as messages in main channel.",
            "`SUBS_PIN`: Submission channel. Rips are pinned.",
            "`SUBS_THREAD`: Submission channel. Rips are posted in threads.",
            "`QUEUE`: Queue channel. Rips are posted as messages in main channel or threads.",
            "_**Channels**_",
        ]
        message.extend(channels)
        result = "\n".join(message)
        
        await send_embed(ctx.channel, result, delete_after=None)


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


@bot.command(name='count_subs', brief='count number of remaining submissions')
async def count_subs(ctx: Context, sub_channel_link: str = None):
    """
    Count number of messages in a channel (e.g. submissions).
    Retrieve the entire history of a channel and count the number of messages not in threads.
    Accepts an optional link argument to the subs-type channel to view - if not, first valid channel in config is used.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("count_subs", ctx.message.author.name)

    sub_channel, msg = parse_channel_link(sub_channel_link, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'])
    if len(msg) > 0:
        await ctx.channel.send(msg)
        if sub_channel == -1: return

    async with ctx.channel.typing():
        server = ctx.guild
        channel = server.get_channel(sub_channel)

        if channel_is_type(channel, 'SUBS'): t = 'msg'
        elif channel_is_type(channel, 'SUBS_PIN'): t = 'pin'
        elif channel_is_type(channel, 'SUBS_THREAD'): t = 'thread'

        count = await count_rips(channel, t)
        if t == 'thread':
            count = sum(count.values())

        if (count < 1):
            result = "```ansi\n\u001b[0;31m* Determination.\u001b[0;0m```"
        else:
            result = f"```ansi\n\u001b[0;31m* {count} left.\u001b[0;0m```"

        await ctx.channel.send(result)

@bot.command(name='stats', brief='display remaining number of rips across channels')
async def stats(ctx: Context, optional_arg = None):
    """
    Display the number of rips in the QoC and submission channels.
    Accepts an optional argument to show queue channels too.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command("stats", ctx.message.author.name)

    server = ctx.guild

    async with ctx.channel.typing():
        ret = "**QoC channels**\n"
        qoc_channels = [k for k, v in CHANNELS.items() if 'QOC' in v]
        for channel_id in qoc_channels:
            team_count = 0
            email_count = 0

            channel = server.get_channel(channel_id)
            if channel is None: continue
            pin_list = await get_pins(channel)

            for pinned_message in pin_list:
                author = get_rip_author(pinned_message)
                if 'email' in author.lower():
                    email_count += 1
                else:
                    team_count += 1
            
            ret += f"- <#{channel_id}>: **{team_count + email_count}** rips\n  - {team_count} team subs\n  - {email_count} email subs\n"

        ret += "**Submission channels**\n"
        sub_channels = [k for k, v in CHANNELS.items() if any(t in v for t in ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'])]
        for channel_id in sub_channels:
            channel = server.get_channel(channel_id)
            if channel is None: continue

            if channel_is_type(channel, 'SUBS'): t = 'msg'
            elif channel_is_type(channel, 'SUBS_PIN'): t = 'pin'
            elif channel_is_type(channel, 'SUBS_THREAD'): t = 'thread'

            sub_count = await count_rips(channel, t)
            if t =='thread':
                ret += f"- <#{channel_id}>:\n"
                for thread, count in sub_count.items():
                    if count > 0:
                        ret += f"  - <#{thread}>: **{count}** rips\n"
            else:
                ret += f"- <#{channel_id}>: **{sub_count}** rips\n"

        if optional_arg is not None:
            ret += "**Queues**\n"
            queue_channels = [k for k, v in CHANNELS.items() if 'QUEUE' in v]
            for channel_id in queue_channels:
                channel = server.get_channel(channel_id)
                if channel is None: continue
                rip_count = await count_rips(channel, 'msg')
                ret += f"- <#{channel_id}>: **{rip_count}** rips\n"

                rip_thread_counts = await count_rips(channel, 'thread')
                for thread, count in rip_thread_counts.items():
                    if count > 0:
                        ret += f"  - <#{thread}>: **{count}** rips\n"

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

def make_markdown(rip_info: dict, display_reacts: bool) -> str:
    """
    Convert a dictionary of rip information to a markdown message
    """
    base_message = f'**[{rip_info["Title"]}]({rip_info["Link"]})**\n{rip_info["Author"]}'
    result = ""

    if len(rip_info["Indicator"]) > 0:
        base_message = f'{rip_info["Indicator"]} **[{rip_info["Title"]}]({rip_info["Link"]})** {rip_info["Indicator"]}\n{rip_info["Author"]}'

    if display_reacts:
        result = base_message + f' | {rip_info["Reacts"]}\n'
    else:
        result = base_message + "\n"

    result += "━━━━━━━━━━━━━━━━━━\n" # a line for readability!
    return result


async def react_command(ctx: Context, cmd_name: str, check_func: typing.Callable, not_found_message: str, optional_time = None): # I've been meaning to simplify this for AGES (7/7/24)
    """
    Unified command to roundup messages with specific reactions in QoC channels.
    Uses the react_is_ABC helper functions to filter reacts.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command(cmd_name, ctx.message.author.name)

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    channel = await get_roundup_channel(ctx)
    if channel is None: return

    async with ctx.channel.typing():
        async def filter_reacts(c: TextChannel, m: Message):
            if any([check_func(r) for r in m.reactions]):
                return await get_reactions(c, m)
            # if not, return an indication string to skip from markdown
            return "FILTERED", ""

        filtered_pins = await get_pinned_msgs_and_react(channel, filter_reacts)
        
        result = ""
        for rip_id, rip_info in filtered_pins.items():
            if rip_info["Reacts"] != "FILTERED":
                result += make_markdown(rip_info, True)

        if result == "":
            await ctx.channel.send(not_found_message)
        else:
            await send_embed(ctx.channel, result, time)

async def get_reaction_data(message_id: int, channel: TextChannel) -> typing.List[ReactionData]:
    """
    Gets reaction data from a message. If not in cache, fetches and caches it.
    Fast if cached, slow if not.
    """
    reactionDatas = []
    if message_id in REACTION_CACHE:
        reactionDatas = REACTION_CACHE.get(message_id)
    else:
        message = await channel.fetch_message(message_id)
        for reaction in message.reactions:
            user_ids = [user.id async for user in reaction.users()]
            for user_id in user_ids:
                reactData = init_reaction_data(reaction.emoji, user_id)
                reactionDatas.append(reactData)
        REACTION_CACHE.update({message_id: reactionDatas})
    return reactionDatas

async def react_conditional_command(ctx: Context, cmd_name: str, user_id: str, valid_func: typing.Callable, conditional_message: str, optional_time = None):
    """
    Unified command to roundup messages with user-dependent reactions in QoC channels.
    Uses the react_is_ABC helper functions to filter reacts.
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command(cmd_name, ctx.message.author.name)

    match = re.search(r'\d+', user_id)
    if match:
        ID = int(match.group(0))
        search_author = ctx.guild.get_member(ID)
        if search_author:
            await ctx.channel.send(f"Searching for rips {conditional_message} by {search_author.name}")
    else:
        ID = ctx.author.id
    
    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    channel = await get_roundup_channel(ctx)
    if channel is None: return

    async with ctx.channel.typing():
        pin_list = await get_pins(channel)

        dict_index = 1
        pins_in_message = {}  # make a dict for everything

        for pinned_message in pin_list:
            rip_title = get_rip_title(pinned_message)
            author = get_rip_author(pinned_message)        
            reactionDatas = get_reaction_data(message.id, channel)

            valid = await valid_func(ID, reactionDatas)
            if not valid:
                continue
            
            reacts, indicator = await get_reactions(channel, message)
            author = author.replace('*', '').replace('_', '')
            pins_in_message[dict_index] = {
                'Title': rip_title,
                'Author': author,
                'Reacts': reacts,
                'PinMiser': pinned_message.author.name,  # im mister rip christmas, im mister qoc
                'Indicator': indicator,
                'Link': f"<https://discordapp.com/channels/{str(channel.guild.id)}/{str(channel.id)}/{str(pinned_message.id)}>"
            }
            dict_index += 1

        result = ""
        for rip_id, rip_info in pins_in_message.items():
            result += make_markdown(rip_info, True) # a match!
        if result == "":
            await ctx.channel.send('No rips found.')
        else:
            await send_embed(ctx.channel, result, time)


NO_RIP_DESCRIPTOR = {
    'search': 'pinned rips containing indicated text in title',
    'search_subs': 'submissions containing indicated text in title',
    'events': 'pinned rips containing indicated text in author line',
    'event_subs': 'submissions containing indicated text in author line',
}

async def filter_command(ctx: Context, cmd_name: str, filter_func: typing.Callable, display_reacts: bool, optional_time = None):
    """
    Unified command to roundup messages according to a predicate in QoC channels.
    `filter_func` is a function accepting 2 parameters: `ctx`, `rip_info`
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command(cmd_name, ctx.message.author.name)

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    channel = await get_roundup_channel(ctx)
    if channel is None: return

    async with ctx.channel.typing():
        all_pins = await process_pins(channel, display_reacts)
        result = ""
        for rip_id, rip_info in all_pins.items():
            if filter_func(ctx, rip_info):
                result += make_markdown(rip_info, display_reacts) # a match!
        if result == "":
            await ctx.channel.send("No {} found.".format(NO_RIP_DESCRIPTOR[cmd_name] if cmd_name in NO_RIP_DESCRIPTOR.keys() else 'rips'))
        else:
            await send_embed(ctx.channel, result, time)


async def filter_sub_command(ctx: Context, cmd_name: str, filter_sub_func: typing.Callable, sub_channel_link: str = None, optional_time = None):
    """
    Unified command to roundup submissions according to a predicate.
    `filter_sub_func` is a function accepting 1 parameter: `rip` (type Message)
    """
    if not channel_is_types(ctx.channel, ['ROUNDUP', 'PROXY_ROUNDUP']): return
    heard_command(cmd_name, ctx.message.author.name)

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    sub_channel, msg = parse_channel_link(sub_channel_link, ['SUBS', 'SUBS_PIN', 'SUBS_THREAD'])
    if len(msg) > 0:
        await ctx.channel.send(msg)
        if sub_channel == -1: return

    async with ctx.channel.typing():
        server = ctx.guild
        channel = server.get_channel(sub_channel)

        qoc_emote = DEFAULT_QOC
        for e in server.emojis:
            if e.name.lower() == "qoc":
                qoc_emote = e

        if channel_is_type(channel, 'SUBS'): t = 'msg'
        elif channel_is_type(channel, 'SUBS_PIN'): t = 'pin'
        elif channel_is_type(channel, 'SUBS_THREAD'): t = 'thread'

        rips: typing.List[Message] = []
        t_rips = await get_rips(channel, t)
        for k, v in t_rips.items():
            rips.extend(v)
        
        result = ""
        for rip in rips:
            rip_title = get_rip_title(rip)
            rip_link = f"<https://discordapp.com/channels/{str(ctx.guild.id)}/{str(sub_channel)}/{str(rip.id)}>"
            if filter_sub_func(rip):
                if any([react_is_qoc(r) for r in rip.reactions]):
                    result += f"{qoc_emote} "
                author = str(rip.author).split('#')[0].replace('_', '\_')
                result += f'**[{rip_title}]({rip_link})** (by {author})\n'

        if len(result) == 0:
            await ctx.channel.send("No {} found.".format(NO_RIP_DESCRIPTOR[cmd_name] if cmd_name in NO_RIP_DESCRIPTOR.keys() else 'rips'))
        else:
            await send_embed(ctx.channel, result, time)


async def fetch_command(ctx: Context, valid_func: typing.Callable, channel_link = None, optional_time = None):
    """
    Unified command to roundup messages satisfying condition in queues.
    Uses the react_is_ABC helper functions to filter reacts.
    """
    channel_id, msg = parse_channel_link(channel_link, ['QUEUE'])
    if len(msg) > 0 or channel_link is None:
        if channel_link is not None:
            await ctx.channel.send("Warning: something went wrong parsing channel link. Defaulting to showing from all known queues.")
        queue_channel_ids = [k for k, v in CHANNELS.items() if 'QUEUE' in v]
    else:
        queue_channel_ids = [channel_id]

    time, msg = parse_optional_time(ctx.channel, optional_time)
    if msg is not None: await ctx.channel.send(msg)

    async with ctx.channel.typing():
        result = ""
        count = 0

        for queue_channel_id in queue_channel_ids:
            rips: typing.List[Message] = []
            for t in ['msg', 'thread']:
                channel = bot.get_channel(queue_channel_id)
                if channel is None: continue
                t_rips = await get_rips(channel, t)
                for k, v in t_rips.items():
                    rips.extend(v)
        
            result += f'<#{queue_channel_id}>:\n'
            count += len(rips)

            for rip in rips:
                rip_title = get_rip_title(rip)
                rip_link = f"<https://discordapp.com/channels/{str(ctx.guild.id)}/{str(rip.channel.id)}/{str(rip.id)}>"
                if valid_func(rip):
                    result += f'**[{rip_title}]({rip_link})**\n'
            
            result += '------------------------------\n'

        if count == 0:
            await ctx.channel.send("No rips found.")
        else:
            await send_embed(ctx.channel, result, time)


async def fetch_reaction_command(ctx: Context, react_func: typing.Callable, channel_link = None, optional_time = None):
    await fetch_command(ctx, lambda rip: any(react_func(r) for r in rip.reactions), channel_link, optional_time)


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


async def send_embed(channel: TextChannel, message: str, delete_after: float = None):
    """
    Send a long message as embed.

    - message: Text to send as embed
    - delete_after: Number of seconds to automatically remove the message. Defaults to constant at the beginning of file. If set to None, message will not delete.
    """
    long_message = split_long_message(message, _get_config('embed_character_limit'))
    for line in long_message:
        fancy_message = discord.Embed(description=line, color=_get_config('embed_color'))
        await channel.send(embed=fancy_message, delete_after=delete_after)

def channel_is_type(channel: TextChannel | Thread, type: str):
    return channel.id in CHANNELS.keys() and type in CHANNELS[channel.id] or hasattr(channel, "parent") and channel_is_type(channel.parent, type)

def channel_is_types(channel: TextChannel | Thread, types: typing.List[str]):
    return channel.id in CHANNELS.keys() and any([t in CHANNELS[channel.id] for t in types]) or hasattr(channel, "parent") and channel_is_types(channel.parent, types)

async def get_roundup_channel(ctx: Context):
    if channel_is_type(ctx.channel, 'PROXY_ROUNDUP'):
        qoc_channel, msg = parse_channel_link(None, ["ROUNDUP"])
        if len(msg) > 0:
            await ctx.channel.send(msg)
            if qoc_channel == -1: return None
        channel = bot.get_channel(qoc_channel)
    else:
        channel = ctx.channel
    return channel

def heard_command(command_name: str, user: str):
    today = datetime.now() # Technically not useful, but it looks gorgeous on my CRT monitor
    print(f"{today.strftime('%m/%d/%y %I:%M %p')}  ~~~  Heard {command_name} command from {user}!")


def parse_optional_time(channel: TextChannel, optional_time):
    """
    Get the number of houminutesrs from user input for roundup embed commands.
    """
    time = _get_config('proxy_embed_seconds') if channel_is_type(channel, 'PROXY_ROUNDUP') else _get_config('embed_seconds')
    msg = None
    if optional_time is not None:
        try:
            new_time = float(optional_time) * 60
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


def get_raw_rip_title(message: Message) -> str:
    """
    Return the rip title line of a Discord message.
    Assumes the message follows the format where the rip title is after the first instance of ```
    """
    # Update: now use regex to find the first instance of "```[\n][text][\n]"
    pattern = r'\`\`\`\n*.*\n'
    rip_title = re.search(pattern, message.content)
    if rip_title is not None:
        rip_title = rip_title.group(0)
        rip_title = rip_title.replace('`', '')
        rip_title = rip_title.replace('\n', '')

    return rip_title


def get_rip_title(message: Message) -> str:
    """
    Wrapper function to format unusual or spoiler rip titles
    """
    rip_title = get_raw_rip_title(message)
    if rip_title is None:
        return "`[Unusual Pin Format]`"
    elif '||' in message.content.split('```')[0]:
        # if || is detected in the message before the first ```, make the rip title into spoiler
        return "`[Rip Contains Spoiler]`"
    else:
        return rip_title


def get_raw_rip_author(message: Message) -> str:
    """
    Return the rip author line of a Discord message.
    Assumes the message follows the format where the rip author is after the first instance of ```
    """
    author = message.content.split("```")[0]
    author = author.replace('\n', '')
    author = author.replace('||', '') # in case of spoilered rips

    return author


def get_rip_author(message: Message) -> str:
    """
    Wrapper function to format author line.
    If the line contains "by me", append the message sender's name to the author line.
    """
    author = get_raw_rip_author(message)
    
    if len(re.findall(r'\bby\b', author.lower())) == 0:
        # If "by" is not found, notify that the "author line" might be unusual
        author = author + " [Unusual Pin Format]"

    elif len(re.findall(r'\bby me\b', author.lower())) > 0: 
        # Overwrite it and do something else if the rip's author and the pinner are the same
        cleaned_author = str(message.author).split('#')[0]
        author += (f' (**{cleaned_author}**)')

    return author


def get_rip_description(message: Message) -> str:
    """
    Return the description of a rip, i.e. the part inside ```
    """
    # Use a regular expression to find text between two ``` markers
    match = re.search(r'```(.*?)```', message.content, re.DOTALL)

    if match:
        # Return the extracted text, stripping any leading/trailing whitespace
        return match.group(1).strip()
    else:
        return ""  # Return empty string if no match was found


async def get_pinned_msgs_and_react(channel: TextChannel, react_func: typing.Callable | None = None) -> dict:
    """
    Unified function to retrieve all pinned messages (except the first one) from a channel and give corresponding emojis.
    - react_func: A function in the form of fn(TextChannel, Message) that returns some emojis for a message. If None, show no emojis.
    
    Returns a dictionary of pinned messages.
    """
    pin_list = await get_pins(channel)

    dict_index = 1
    pins_in_message = {}  # make a dict for everything

    for pinned_message in pin_list:
        # Get the rip title
        rip_title = get_rip_title(pinned_message)

        # Find the rip's author
        author = get_rip_author(pinned_message)        

        # Get reactions
        if react_func is not None:
            #TODO: (Marc) rearchitect to not pass in a function pointer
            reacts, indicator = await react_func(channel, pinned_message)
        else:
            reacts, indicator = "", ""

        #get rid of all asterisks and underscores in the author so an odd number of them doesn't mess up the rest of the message
        author = author.replace('*', '').replace('_', '')

        # Put all this information in the dict
        pins_in_message[dict_index] = {
            'Title': rip_title,
            'Author': author,
            'Reacts': reacts,
            'PinMiser': pinned_message.author.name,  # im mister rip christmas, im mister qoc
            'Indicator': indicator,
            'Link': f"<https://discordapp.com/channels/{str(channel.guild.id)}/{str(channel.id)}/{str(pinned_message.id)}>"
        }
        dict_index += 1

    return pins_in_message


# A bunch of functions to check if react is of specific types

def react_is_goldcheck(name: str) -> bool:
    return any([r in name.lower() for r in ["goldcheck", DEFAULT_GOLDCHECK]])

def react_is_checkreq(name: str) -> bool:
    return name.lower().endswith("check") and name.lower()[0].isdigit()

def react_is_check(name: str) -> bool:
    return not react_is_goldcheck(name) and not react_is_checkreq(name) \
            and any([r in name.lower() for r in ["check", DEFAULT_CHECK]])

def react_is_fix(name: str) -> bool:
    return any([r in name.lower() for r in ["fix", "wrench", DEFAULT_FIX]])

def react_is_reject(name: str) -> bool:
    return any([r in name.lower() for r in ["reject", DEFAULT_REJECT]])

def react_is_stop(name: str) -> bool:
    return any([r in name.lower() for r in ["stop", "octagonal", DEFAULT_STOP]])

def react_is_alert(name: str) -> bool:
    return any([r in name.lower() for r in ["alert", DEFAULT_ALERT]])

def react_is_qoc(name: str) -> bool:
    return any([r in name.lower() for r in ["qoc", DEFAULT_QOC]])

def react_is_metadata(name: str) -> bool:
    return any([r in name.lower() for r in ["metadata", DEFAULT_METADATA]])

def react_is_thumbnail(name: str) -> bool:
    return any([r in name.lower() for r in ["thumbnail", DEFAULT_THUMBNAIL]])

def react_is_emailsent(name: str) -> bool:
    return any([r in name.lower() for r in ["emailsent"]])


KEYCAP_EMOJIS = {'2️⃣': 2, '3️⃣': 3, '4️⃣': 4, '5️⃣': 5, '6️⃣': 6, '7️⃣': 7, '8️⃣': 8, '9️⃣': 9, '🔟': 10}
def react_is_number(name: str) -> bool:
    return name in KEYCAP_EMOJIS


def rip_is_specs_overdue(message: Message) -> bool:
    """
    Returns true if the message is older than SPECS_OVERDUE_DAYS
    """
    return datetime.now(timezone.utc) - message.created_at > timedelta(days=_get_config('spec_overdue_days'))

def rip_is_overdue(message: Message) -> bool:
    """
    Returns true if the message is older than OVERDUE_DAYS
    """
    return datetime.now(timezone.utc) - message.created_at > timedelta(days=_get_config('overdue_days'))

async def get_reactions(channel: TextChannel, message: Message) -> typing.Tuple[str, str]:
    """
    Return the reactions of a message.
    The message should contain the full reactions information.
    Returns an additional emoji as special indicator for the message.
    Requirements for approval:
    - At least 3 more checks than rejects
    - No fixes or alerts
    - If stop is present, number of goldchecks must be at least the numerical react (if any), or 1 (default)
    - If checkreq is present, change 3 to the corresponding value
    """
    reacts = ""
    indicator = ""

    num_checks = 0
    num_rejects = 0
    num_goldchecks = 0
    specs_required = 1
    checks_required = 3
    specs_needed = False
    fix_or_alert = False
    
    emote_names = [e.name for e in channel.guild.emojis]

    reactionDatas = await get_reaction_data(message.id, channel)

    for reactData in reactionDatas:
        if react_is_goldcheck(reactData.name): num_goldchecks += 1
        elif react_is_checkreq(reactData.name):
            try:
                checks_required = int(reactData.name.split("check")[0])
            except ValueError:
                print("Error parsing checkreq react: {}".format(reactData.name))
        elif react_is_check(reactData.name): num_checks += 1
        elif react_is_reject(reactData.name): num_rejects += 1
        elif react_is_fix(reactData.name) or react_is_alert(reactData.name): fix_or_alert = True
        elif react_is_stop(reactData.name): specs_needed = True
        elif react_is_number(reactData.name):
            specs_required = KEYCAP_EMOJIS[reactData.name]
        
        if reactData.name in emote_names:
            for e in channel.guild.emojis:
                if e.name == reactData.name:
                    reacts += f"{e} "
                    break
        else:
            reacts += f"{reactData.name} "
    
    check_passed = (num_checks - num_rejects >= checks_required) and not fix_or_alert
    specs_passed = (not specs_needed or num_goldchecks >= specs_required)

    if check_passed:
        indicator = APPROVED_INDICATOR if specs_passed else AWAITING_SPECIALIST_INDICATOR
    elif specs_needed and not specs_passed and rip_is_specs_overdue(message):
        indicator = SPECS_OVERDUE_INDICATOR
    elif rip_is_overdue(message):
        indicator = OVERDUE_INDICATOR

    return reacts, indicator


async def process_pins(channel: TextChannel, get_reacts: bool):
    """
    Retrieve all pinned messages (except the first one) from a channel.
    - get_reacts: Whether to show messages' reactions as emojis
    """
    return await get_pinned_msgs_and_react(channel, get_reactions if get_reacts else None)


async def vet_message(channel: TextChannel, message: Message) -> typing.Tuple[str, str]:
    """
    Return the QoC verdict of a message as emoji reactions.
    """
    urls = extract_rip_link(message.content)
    reacts = ""
    for url in urls:
        code, msg = await run_blocking(performQoC, url)
        reacts = code_to_verdict(code, msg)
        
        # debug
        if code == -1:
            await write_log("Message: {}\n\nURL: {}\n\nError: {}".format(message.content, url, msg))
        else:
            break

    return reacts, ""

async def vet_pins(channel: TextChannel):
    """
    Retrieve all pinned messages (except the first one) from a channel and perform basic QoC, showing verdicts as emojis.
    """
    return await get_pinned_msgs_and_react(channel, vet_message)


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


async def check_qoc(message: Message, fullFeedback: bool = False) -> typing.Tuple[str, str, str]:
    """
    Perform simpleQoC on a message.
    """
    urls = extract_rip_link(message.content)
    qcCode, qcMsg = -1, "No links detected."
    detectedUrl = None
    for url in urls:
        qcCode, qcMsg = await run_blocking(performQoC, url, fullFeedback)
        if qcCode != -1:
            detectedUrl = url
            break
    return qcCode, qcMsg, detectedUrl


async def check_metadata(message: Message, fullFeedback: bool = False) -> typing.Tuple[str, str]:
    """
    Perform metadata checking on a message.
    If message contains the phrase "unusual metadata", skip most checks
    """
    playlistId = extract_playlist_id('\n'.join(message.content.splitlines()[1:])) # ignore author line
    description = get_rip_description(message)
    advancedCheck = _get_config('metadata')
    skipCheck = "unusual metadata" in message.content.lower()
    if not skipCheck and len(description) > 0:
        mtCode, mtMsgs = await run_blocking(checkMetadata, description, YOUTUBE_CHANNEL_NAME, playlistId, YOUTUBE_API_KEY, advancedCheck)
    else:
        mtCode, mtMsgs = 0, []

    if mtCode != -1 and "[Unusual Pin Format]" in get_rip_author(message):
        mtCode = 1
        mtMsgs.append("Rip author is missing.")

    if mtCode != -1 and not skipCheck:
        server = message.guild
        title = get_raw_rip_title(message)
        desc = get_rip_description(message)

        def checkDupes(queue, rips):
            msgs = []
            if any([title == get_raw_rip_title(r) for r in rips if r.id != message.id]):
                msgs.append(f"Video title already exists in <#{queue}>.")
            elif any([isDupe(desc, get_rip_description(r), True) for r in rips if r.id != message.id]):
                msgs.append(f"Main mix detected in <#{queue}>. Add something on the author line to avoid uploading this early.")
            return msgs

        queue_channels = [k for k, v in CHANNELS.items() if 'QUEUE' in v]
        for queue_channel_id in queue_channels:
            queue_channel = server.get_channel(queue_channel_id)
            if queue_channel is None: continue
            queue_rips = await get_rips(queue_channel, 'msg')
            for m in checkDupes(queue_channel_id, queue_rips[queue_channel_id]):
                if "Video" in m: mtCode = 1
                mtMsgs.append(m)

            queue_thread_rips = await get_rips(queue_channel, 'thread')
            for thread, rips in queue_thread_rips.items():
                for m in checkDupes(thread, rips):
                    if "Video" in m: mtCode = 1
                    mtMsgs.append(m)
        
        qoc_channels = [k for k, v in CHANNELS.items() if 'QOC' in v]
        for qoc_channel_id in qoc_channels:
            qoc_channel = server.get_channel(qoc_channel_id)
            if qoc_channel is None: continue
            qoc_rips = await get_rips(qoc_channel, 'pin')
            for m in checkDupes(qoc_channel_id, qoc_rips[qoc_channel_id]):
                if "Video" in m: mtCode = 1
                mtMsgs.append(m)

    mtMsg = '\n'.join(["- " + m for m in mtMsgs]) if len(mtMsgs) > 0 else ("- Metadata is OK." if fullFeedback else "")
    
    return mtCode, mtMsg


async def check_qoc_and_metadata(message: Message, fullFeedback: bool = False) -> typing.Tuple[str, str]:
    """
    Perform simpleQoC and metadata checking on a message.

    - **message**: Message to check
    - **fullFeedback**: If True, display "OK" messages. Otherwise, display only issues.
    """
    verdict = ""
    msg = ""
    rip_title = get_rip_title(message)
    
    # QoC
    qcCode, qcMsg, detectedUrl = await check_qoc(message, fullFeedback)
    if qcCode == -1:
        await write_log("Warning: cannot QoC message\nRip: {}\n{}".format(rip_title, qcMsg))
    elif (qcCode == 1) or fullFeedback:
        verdict += code_to_verdict(qcCode, qcMsg)
        msg += qcMsg + "\n"

    # Metadata
    mtCode, mtMsg = await check_metadata(message, fullFeedback)
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
            for line in message.content.split('```', 2)[2].splitlines():
                line = "".join(c for c in line if c.isprintable())
                if detectedUrl in line:
                    break
                elif len(line) > 0 and not line.startswith('Joke') and not line == '||':
                    msg += "- Line not starting with ``Joke`` detected between description and rip URL. Recommend putting the URL directly under description to avoid accidentally uploading joke lines.\n"
                    break
        except IndexError:
            pass

    return verdict, msg


async def count_rips(channel: TextChannel, type: typing.Literal['pin', 'msg', 'thread']) -> int | dict:
    """
    Returns the number of rips in a channel.
    `type` argument specifies what type of messages to retrieve (see get_rips).
    """
    rips = await get_rips(channel, type)
    if len(rips) == 1:
        return len(rips[channel.id])
    else:
        count = {}
        for k, v in rips.items():
            count[k] = len(v)
        return count


async def get_pins(channel: TextChannel) -> typing.List[Message]:
    """
    Raw pin retrieval helper function
    """
    pins = [message async for message in channel.pins(limit=None)]
    return pins[:-1] if _get_config('qoc_contains_pinned_rule') else pins


async def get_rips(channel: TextChannel, type: typing.Literal['pin', 'msg', 'thread']) -> dict[int, typing.List[Message]]:
    """
    Retrieve all rips in a channel, depending on the type: rips are in pins, messages or threads.
    `type` argument specifies what type of messages to retrieve:
    - 'pin': Pinned messages. Assumes first pinned message is not a rip for simplicity.
    - 'msg': Messages in channel, ignoring threads. Only count messages with ```.
    - 'thread': Messages in threads.

    Return value is a dictionary of channel IDs as key and list of messages as values.

    Notes:
    - `msg` might take a long time for big channels. Limit this to submissions or queue channels.
    """
    rips = {
        channel.id: []
    }
    if type == 'pin':
        rips[channel.id] = await get_pins(channel)
    elif type == 'msg':
        async for message in channel.history(limit = None):
            if channel is Thread or not (message.channel is Thread):
                if '```' in message.content and len(extract_rip_link(message.content)) > 0:
                    rips[channel.id].append(message)
    elif type == 'thread':
        rips = {}
        async for message in channel.history(limit = None):
            if message.thread is not None:
                thread_rips = await get_rips(message.thread, 'msg')
                rips[message.thread.id] = thread_rips[message.thread.id]
    
    return rips


def parse_channel_link(link: str | None, types: typing.List[str]) -> typing.Tuple[int, str]:
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
    else:
        return default_id, f"Warning: Link is not a valid roundup channel, defaulting to <#{default_id}>."


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
        log_channel = await bot.fetch_channel(LOG_CHANNEL)
        if embed:
            await send_embed(log_channel, msg)
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
