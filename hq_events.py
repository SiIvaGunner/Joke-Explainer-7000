
import discord
from discord import Message, Thread, TextChannel
from discord.abc import GuildChannel
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta, time

import typing
from typing import List

import discord
from discord.abc import GuildChannel
from hq_config import *
from hq_core import *

#===============================================#
#                    EVENTS                     #
#===============================================#

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


async def remove_embeds_from_channel(channel_ids: List[int], expire_time_seconds: float):
    for channel_id in channel_ids:
        channel = bot.get_channel(channel_id)
        if channel:
            messages_and_errors = await discord_cleanup_embeds(200, expire_time_seconds, channel, None) 
            count = len(messages_and_errors.messages)
            if count > 0:
                await send(f"Removed {count} embed messages sent more than {expire_time_seconds // 60} minutes ago. " + \
                       f"I'm just cleaning up my embeds sent before I restarted, don't worry!", channel)


@tasks.loop(time=times)
async def cleanup_regularly():
    qoc_channel_ids = get_channel_ids_of_types(['QOC'])
    await remove_embeds_from_channel(qoc_channel_ids, get_config('embed_seconds'))

    proxy_channel_ids = get_channel_ids_of_types(['PROXY_QOC'])
    await remove_embeds_from_channel(proxy_channel_ids, get_config('proxy_embed_seconds'))


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
    cleanup_regularly.start()

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

@bot.event
async def on_guild_channel_pins_update(channel: typing.Union[GuildChannel, Thread], last_pin: datetime):

    if not channel_is_types(channel, ['QOC', 'SUBS_PIN']):
        return

    global latest_pin_time

    if last_pin is None or last_pin <= latest_pin_time:
        # print("Seems to be a message being unpinned")

        channel_info = get_channel_info(channel)

        if channel_info.rip_fetch_type == RipFetchType.PINS: 

            async with channel.typing():

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
                    new_count = len(rips_and_errors.rips)
                    soft_pin_limit = get_config('soft_pin_limit')
                    txt += f'-# Rip Count: {new_count}/{soft_pin_limit}'

                unlock_channel(channel.id)

                await send_and_if_errors(txt, "Errors during unpin.", error_strings, channel)

    else:

        async with channel.typing():

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

                new_count = len(rips_and_errors.rips) + 1
                is_pinlimit_must_die = get_channel_config(channel.id).pinlimit_must_die_mode
                SOFT_PIN_LIMIT = get_config('soft_pin_limit')

                if is_pinlimit_must_die and new_count > SOFT_PIN_LIMIT:
                    error_strings.extend(await discord_unpin_message(message))
                    error_strings.extend(await discord_add_reaction('📌', message))
                    await send(f":bangbang: **Error**: {len(rips_and_errors.rips)}/{SOFT_PIN_LIMIT} rips in pins. Unpinned.\n-# Remove the 📌 reaction when this is resolved.", channel)
                else:

                    if new_count > SOFT_PIN_LIMIT:
                        await send(f":warning: **Warning: {new_count}/{SOFT_PIN_LIMIT}** rips pinned. Please handle other rips first :(", channel)
                    elif new_count == SOFT_PIN_LIMIT:
                        if is_pinlimit_must_die:
                            await send(f"-# Warning: **Pinlimit is reached!** Pinlimit must die is **on**, so if you pin another rip, *prepare to die!*\n-# Rip Count: {new_count}/{SOFT_PIN_LIMIT}", channel)
                        else:
                            await send(f"-# Warning: **Pinlimit is reached!** Pinlimit must die is **off**, but please handle other rips first before pinning more.\n-# Rip Count: {new_count}/{SOFT_PIN_LIMIT}", channel)
                    elif new_count < SOFT_PIN_LIMIT and new_count >= max(0, SOFT_PIN_LIMIT - 10):
                        await send(f"-# Warning: **{SOFT_PIN_LIMIT - new_count} rips** until pinlimit is reached.\n-# Rip Count: {new_count}/{SOFT_PIN_LIMIT}", channel)

                    await lock_message(message.id, None)
                    #NOTE: (Ahmayk) have to fetch message to get reaction data for cache
                    message_and_errors = await discord_fetch_message(message.id, channel)
                    error_strings.extend(message_and_errors.error_strings)
                    if message_and_errors.message:
                        message = message_and_errors.message
                    rip = cache_rip_in_message(message)
                    unlock_message(message.id)

                    vet_desc = VetRipDesc(message=message, use_youtube_api=True)
                    vet_rip_result = await vet_rip_or_url(rip.text, vet_desc)
                    error_strings.extend(vet_rip_result.error_strings)

                    format_desc = FormatVetRipResultDesc(is_new_pinned_message=True)
                    return_message += format_vet_rip_result(format_desc, vet_rip_result)

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
        old_text = rip.text
        rip = rip._replace(text = payload.message.content)
        RIP_CACHE[payload.channel_id][payload.message_id] = rip
        unlock_message(payload.message_id)

        if channel_is_types(payload.message.channel, ['QOC']):
            async with payload.message.channel.typing():
                try:
                    desc = VetRipDesc(message=payload.message, use_youtube_api=True, \
                                    past_rip_message_content=old_text)
                    vet_rip_result = await vet_rip_or_url(payload.message.content, desc)

                    format_desc = FormatVetRipResultDesc(smol_update=True)
                    text = format_vet_rip_result(format_desc, vet_rip_result)
                    await send_and_if_errors(text, "Errors while re-qocing:", vet_rip_result.error_strings, payload.message.channel)
                except Exception as error:
                    await send_crash(f'ERROR on rip vet after pin:', error, payload.message.channel)


@bot.event
async def on_message(message: Message):
    if message.author == bot.user:
        return

    prefix = get_config("prefix")
    await process_suborqueue_rip_caching(message)

    if not message.content.startswith(prefix):
        return

    args = message.content.split(' ')
    command_name = args[0][len(prefix):].lower()
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