
from discord import Message, Thread, TextChannel

from hq_core import *

async def discord_fetch_message(message_id: int, channel: TextChannel | Thread) -> Message | None:
    message = None
    try:
        message = await channel.fetch_message(message_id)
    except Exception as error:
        error_string = f"{type(error).__name__}: {error}"
        await write_log(f'Discord API call failed: Fetch Message {message_id} {error_string}')
    return message