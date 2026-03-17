from hq_core import *
from hq_config import *

@command(
    command_type=CommandType.SECRET,
    public=True,
)
async def op(args: list[str], command_context: CommandContext):
    await send("op", command_context.channel)

@command(
    command_type=CommandType.SECRET,
    public=True,
    aliases=["cat"],
)
async def meow(args: list[str], command_context: CommandContext):
    await send("meow!", command_context.channel)
