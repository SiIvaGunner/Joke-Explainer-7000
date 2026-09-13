
import discord
from discord import Message, Thread, TextChannel
from discord.abc import GuildChannel
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta, time
import pytz

import typing
from typing import List
import random 

from hq_config import *
from hq_vet import *
from hq_sheets import * 
from hq_embed import *
from hq_commands import CommandContext, CommandType, find_command_info
from hq_source import search_rip_sources 
from hq_specialist import * 
from hq_database import format_reminder

#===============================================#
#                    EVENTS                     #
#===============================================#


times = []
times.append(time(hour=6, tzinfo=timezone.utc))
@tasks.loop(time=times)
async def regular_checkup():

    try:
        string_and_errors = await validate_cache_all()
        if len(string_and_errors.string):
            await write_log(f'**Cache validation had issues:**\n{string_and_errors.string}')
        else:
            today = datetime.now()
            print(f"{today.strftime('%m/%d/%y %I:%M %p')}  Cache revalidated. No issues found.")
    except Exception as error:
        await send_crash(f'ERROR on scheduled cache revalidation', error, None)

    rips_and_errors = await get_rips_of_channel_types(['QOC', 'SUBS', 'SUBS_PIN', 'SUBS_THREAD', 'QUEUE', 'INACTIVE_QUEUE'])

    stored_urls = list(JE_DATABASE[JEDatabaseKey.RIP_LENGTH].keys())
    for rip in rips_and_errors.rips:
        urls = extract_rip_link(rip.text)
        for url in urls:
            if url in stored_urls:
                stored_urls.remove(url)
                break

    await remove_urls_from_database(stored_urls)

    await cleanup_sent_embeds_in_expire_database()

    await cleanup_expired_playlist_video_cache()
    

@tasks.loop(minutes=1)
async def cleanup_embeds_regularly():
    try: 
        await cleanup_sent_embeds_in_expire_database()
    except Exception as error:
        await send_crash(f'ERROR on scheduled embed cleanup', error, None)


@tasks.loop(seconds=1)
async def post_reminders() -> None:

    try:

        if JEDatabaseKey.REMINDER not in JE_DATABASE:
            JE_DATABASE[JEDatabaseKey.REMINDER] = {} 

        now = datetime.now(timezone.utc)
        reminders_to_send: list[Reminder] = []
        for reminders_of_channel in JE_DATABASE[JEDatabaseKey.REMINDER].values():
            for reminder in reminders_of_channel:
                if now >= reminder.remind_time:
                    reminders_to_send.append(reminder)
                elif (
                    reminder.next_countdown_delta is not None
                    and now >= reminder.remind_time - reminder.next_countdown_delta
                ):
                    reminders_to_send.append(reminder)

        new_countdowns: list[Reminder] = []
        for reminder in reminders_to_send:
            channel_and_errors = await discord_find_channel(reminder.channel_id)
            if channel_and_errors.channel:
                if reminder.is_countdown:
                    new_delta = None
                    if now < reminder.remind_time: 
                        set_delta = reminder.remind_time - now
                        for i in range(len(REMINDER_COUNTDOWN_LIST) - 1, -1, -1):
                            if set_delta > REMINDER_COUNTDOWN_LIST[i]:
                                new_delta = REMINDER_COUNTDOWN_LIST[i]
                                break
                        reminder = reminder._replace(next_countdown_delta = new_delta)
                        new_countdowns.append(reminder)
                text = format_reminder(reminder) 
                texts = split_long_message(text, 2000, True)
                for t in texts:
                    try:
                        await channel_and_errors.channel.send(t)
                    except Exception as error:
                        await log_exception(f"Failed to send reminder in {channel_and_errors.channel.jump_url}", error, [], True)

        await add_reminders_to_database(new_countdowns)
        await remove_reminders(reminders_to_send)

    except Exception as error:
        await log_exception(f"Error in post_reminders", error, [], False)


@bot.event
async def on_ready():
    # this should ensure a check for config.json on start up. if the file doesnt exist, the bot should close with error.
    get_config("prefix")

    print(f'Logged in as {bot.user.name}')
    print('#################################')

    await write_log("Good morning! Connecting to Google Sheets API...")

    cleanup_embeds_regularly.start()
    post_reminders.start()

    #NOTE: (Ahmayk) fetch sheet data on init to initialize credentials info and make sure that works
    credentials_and_errors = await refresh_credentials()
    if credentials_and_errors.credentials:
        await get_qoc_sheet_data(GetQoCSheetDataDesc(), credentials_and_errors.credentials)

    await write_log("Caching rips...")

    await rebuild_cache()

    await write_log('Validating cache...')
    string_and_errors = await validate_cache_all()
    validate_result = f'**Startup caching complete.**{string_and_errors.string}'
    await write_log(validate_result)

    regular_checkup.start()

@bot.event
async def on_error(event, *args, **kwargs):
    # https://stackoverflow.com/a/60031624
    await write_log('{}```py\n{}\n```'.format(event, traceback.format_exc()))


_bot_close = bot.close
async def close_with_log(self: commands.Bot):
    JE_DATABASE.close()
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
        try:
            channel_info = get_channel_info(channel)

            if channel_info.rip_fetch_type == RipFetchType.PINS: 

                async with channel.typing():

                    txt = "" 
                    error_strings: list[str] = []

                    async with lock_channel(channel.id, error_strings, channel):

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

                                string_and_errors = await get_formatted_rip_length(rip.text, False, False, channel.guild)
                                ##NOTE: (Ahmayk) No one cares if length processing errors out here, don't say anything
                                #perhaps dangerous but whatever
                                # error_strings.extend(string_and_errors.error_strings)

                                formatted_rip = format_rip(rip, string_and_errors.string, channel.guild, True, spec_overdue_days, overdue_days)
                                txt += f'\n{UNPIN_START_STRING} **{user_string}** unpinned\n{formatted_rip}'

                            rips_and_errors = await get_rips_fast(channel, GetRipsDesc())
                            error_strings.extend(rips_and_errors.error_strings)
                            new_count = len(rips_and_errors.rips)
                            soft_pin_limit = get_config('soft_pin_limit')
                            txt += f'-# Rip Count: {new_count}/{soft_pin_limit}'


                    await send_and_if_errors(txt, "Errors during unpin.", error_strings, channel)

        except Exception as error:
            await send_crash(f'ERROR on QOC Unpin:', error, channel)

    else:

        try: 

            async with channel.typing():

                messages_and_errors = await discord_get_channel_pins(1, channel)
                if len(messages_and_errors.error_strings):
                    return await send_if_errors("Error: Failed to vet pinned message.", messages_and_errors.error_strings, channel)
                
                assert(len(messages_and_errors.messages))
                message = messages_and_errors.messages[0]

                error_strings: List[str] = []

                return_message = ""
                delete_afterwards_time = 0

                if is_message_rip(message):

                    latest_pin_time = last_pin

                    rips_and_errors = await get_rips_fast(channel, GetRipsDesc())
                    error_strings.extend(rips_and_errors.error_strings)

                    new_count = len(rips_and_errors.rips) + 1
                    is_pinlimit_must_die = get_channel_config(channel.id).pinlimit_must_die_mode
                    SOFT_PIN_LIMIT = get_config('soft_pin_limit')

                    if is_pinlimit_must_die and new_count > SOFT_PIN_LIMIT:
                        error_strings.extend(await discord_add_reaction(DEFAULT_PIN, message))
                        error_strings.extend(await discord_unpin_message(message))
                        await send(f":bangbang: **Error**: {len(rips_and_errors.rips)}/{SOFT_PIN_LIMIT} rips in pins. Unpinned.", channel)
                    else:

                        if new_count > SOFT_PIN_LIMIT:
                            msg = f":warning: **Warning: {new_count}/{SOFT_PIN_LIMIT}** rips pinned. Please handle other rips first :("
                            special_msg_end = f"(**{new_count}/{SOFT_PIN_LIMIT}** rips pinned)" 
                            if new_count >= SOFT_PIN_LIMIT * 1.5:
                                msg = f":warning: **Warning: That's a lot of rips! {special_msg_end}**"
                            if new_count >= SOFT_PIN_LIMIT * 2:
                                msg = f"**Yum, I'm so full of rips {special_msg_end}**"
                            if new_count >= SOFT_PIN_LIMIT * 2.5:
                                msg = f"Good lord, what is happening in here? {special_msg_end}"
                            if new_count >= SOFT_PIN_LIMIT * 3:
                                msg = f"What have you done. {special_msg_end}"
                            if new_count >= SOFT_PIN_LIMIT * 3.5:
                                msg = f"Help me! {special_msg_end}"
                            if new_count >= SOFT_PIN_LIMIT * 4:
                                msg = f"Help. {special_msg_end}"
                            if new_count >= SOFT_PIN_LIMIT * 4.5:
                                msg = f"Help me. {special_msg_end}"
                            await send(msg, channel)
                        elif new_count == SOFT_PIN_LIMIT:
                            if is_pinlimit_must_die:
                                await send(f"-# Warning: **Pinlimit is reached!** Pinlimit must die is **on**, so if you pin another rip, *prepare to die!*\n-# Rip Count: {new_count}/{SOFT_PIN_LIMIT}", channel)
                            else:
                                await send(f"-# Warning: **Pinlimit is reached!** Pinlimit must die is **off**, but please handle other rips first before pinning more.\n-# Rip Count: {new_count}/{SOFT_PIN_LIMIT}", channel)
                        elif new_count < SOFT_PIN_LIMIT and new_count >= max(0, SOFT_PIN_LIMIT - 10):
                            await send(f"-# Warning: **{SOFT_PIN_LIMIT - new_count} rips** until pinlimit is reached.\n-# Rip Count: {new_count}/{SOFT_PIN_LIMIT}", channel)

                        async with lock_channel(channel.id, error_strings, None):
                            #NOTE: (Ahmayk) have to fetch message to get reaction data for cache
                            message_and_errors = await discord_fetch_message(message.id, channel)
                            error_strings.extend(message_and_errors.error_strings)
                            if message_and_errors.message:
                                message = message_and_errors.message
                            rip = cache_rip_in_message(message)

                        credentials_and_errors = await refresh_credentials()
                        error_strings.extend(credentials_and_errors.error_strings)

                        #NOTE (Ahmayk) only lookup source info if rip has been posted recently
                        #this is unwanted if repinning an old rip
                        auto_source_on_pin = get_config('auto_source_on_pin')
                        if auto_source_on_pin and datetime.now(timezone.utc) - message.created_at < timedelta(minutes=30) and credentials_and_errors.credentials:
                            qoc_sheet_data = await get_qoc_sheet_data(GetQoCSheetDataDesc(), credentials_and_errors.credentials)
                            source_text = search_rip_sources(message.content, qoc_sheet_data)
                            specialists_text = search_specialists(message.content, qoc_sheet_data, message.channel.guild)
                            await send_embed(f'{source_text}\n\n{specialists_text}', channel, EmbedDesc(title="Sources"))

                        vet_desc = VetRipDesc(message=message, is_new_pinned_message=True)
                        vet_report_and_errors = await vet_rip_or_url(rip.text, vet_desc, channel.guild)
                        error_strings.extend(vet_report_and_errors.error_strings)

                        if not len(vet_report_and_errors.string):
                            delete_afterwards_time = 2 
                            confirm_emoji = random.choice(['👍', '😚', '😀 ', '🙃 ', '😎 ', '👌 ', '❤️ ', '🔥 ', '✅ ', '🥰'])
                            if random.random() < (1.0 / 20.0):
                                confirm_emoji = random.choice(['🐟 ', '😂 ', '😳 ', '🐒 ', '💯 ', '🌈 ', '🍔 ', '🫶', '👁️👄👁️ ', '🏳️‍⚧️ ', '🏳️‍🌈 ', '🐴 ', '🇧🇷'])
                            return_message = confirm_emoji 

                await send_and_if_errors(return_message, "Warning: Pinning QoC rip returned errors.", error_strings, channel, delete_afterwards_time)

        except Exception as error:
            await send_crash(f'ERROR on QOC Pin:', error, channel)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):

    error_strings: list[str] = []

    channel_and_errors = await discord_find_channel(payload.channel_id)
    channel = channel_and_errors.channel
    if channel:
        is_qoc_channel = channel_is_types(channel, ['QOC'])
        is_suborqueue_channel = channel_is_types(channel, ['QUEUE', 'SUBS', 'SUBS_THREAD', 'SUBS_PIN'])

        message = None 
        in_cache = False
        if is_qoc_channel or is_suborqueue_channel:

            async with lock_channel(payload.channel_id, error_strings, None):
                in_cache = payload.channel_id in RIP_CACHE and payload.message_id in RIP_CACHE[payload.channel_id]
                if in_cache: 

                    async with lock_message(payload.message_id, error_strings, None):

                        ##NOTE: (Ahmayk) could skip this if we wanted to, but
                        ##we don't really lose much from doing a fetch here.
                        ##It guarentees that all reactions are accurate 
                        message_and_errors = await discord_fetch_message(payload.message_id, channel)
                        message = message_and_errors.message

                        if message:
                            rip = cache_rip_in_message(message)
                            if is_qoc_channel and message.pinned:

                                if rip.message_id not in USER_REACT_CACHE: 
                                    USER_REACT_CACHE[rip.message_id] = {} 

                                react = React(payload.emoji.id or 0, payload.emoji.name, str(payload.emoji))
                                if react not in USER_REACT_CACHE[rip.message_id]: 
                                    USER_REACT_CACHE[rip.message_id][react] = [] 

                                USER_REACT_CACHE[rip.message_id][react].append(payload.user_id) 



async def process_suborqueue_rip_caching(message: Message):
    is_queue_channel = channel_is_types(message.channel, ['QUEUE']) 
    is_sub_channel = channel_is_types(message.channel, ['SUBS', 'SUBS_THREAD'])
    if (is_queue_channel or is_sub_channel) and is_message_rip(message): 
        cache_rip_in_message(message)
        await process_jingle_status(message)


async def remove_reaction_from_cache(channel_id: int, message_id: int, emoji: discord.PartialEmoji, user_id: int | None, remove_all: bool):
    in_rip_cache = channel_id in RIP_CACHE and message_id in RIP_CACHE[channel_id]
    in_user_react_cache = message_id in USER_REACT_CACHE
    if in_rip_cache or in_user_react_cache:
        async with lock_message(message_id, [], None):

            react = React(emoji.id or 0, emoji.name, str(emoji))

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
        async with lock_message(payload.message_id, [], None):
            if in_rip_cache: 
                RIP_CACHE[payload.channel_id][payload.message_id].reacts.clear()
            if in_user_react_cache: 
                USER_REACT_CACHE.pop(payload.message_id)


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    await remove_rip_from_cache(payload.message_id, payload.channel_id)


@bot.event
async def on_raw_message_edit(payload: discord.RawMessageUpdateEvent):
    if payload.channel_id in RIP_CACHE and payload.message_id in RIP_CACHE[payload.channel_id]: 
        error_strings: list[str] = []
        async with lock_message(payload.message_id, error_strings, None):
            rip = RIP_CACHE[payload.channel_id][payload.message_id]
            old_text = rip.text
            rip = rip._replace(text = payload.message.content)
            RIP_CACHE[payload.channel_id][payload.message_id] = rip
        if len(error_strings):
            await send_if_errors("Error on updating edited rip:", error_strings, payload.message.channel)

        if channel_is_types(payload.message.channel, ['QOC']) and payload.message.pinned:
            async with payload.message.channel.typing():
                try:
                    desc = VetRipDesc(message=payload.message, past_rip_message_content=old_text)
                    vet_report = await vet_rip_or_url(payload.message.content, desc, payload.message.guild)
                    await send_if_errors("Errors while vetting:", vet_report.error_strings, payload.message.channel)
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
    
    if not type(message.author) is discord.Member:
        if command_info.command_type != CommandType.SECRET:
            return await send("Error: Working commands can only be executed within servers.", message.channel)
    elif command_info.admin:
        if not message.author.guild_permissions.administrator:
            return await send("Error: Only users with admin access in this server can use this command.", message.channel)

    command_context = CommandContext(message.channel, message.author, message.reference)

    today = datetime.now() # Technically not useful, but it looks gorgeous on my CRT monitor
    print(f"{today.strftime('%m/%d/%y %I:%M %p')}  ~~~  Heard {command_name} command from {message.author.name}!")

    try:
        await command_info.func(args[1:], command_context)
    except Exception as error:
        await send_crash(f'ERROR on command {prefix}{command_name}:', error, message.channel)