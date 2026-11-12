from typing import NamedTuple, List
from enum import Enum, auto
import discord
from discord import Message, Guild
import re

from hq_types import Rip, React 
from hq_config import get_config 
from hq_discord import bot, UserReactDictAndErrors, log_exception, discord_add_reaction, discord_clear_reaction, discord_remove_reaction

APPROVED_INDICATOR = '🔥'
AWAITING_SPECIALIST_INDICATOR = '♨️'
SPECS_OVERDUE_INDICATOR = '🫑'
OVERDUE_INDICATOR = '🕒'

DEFAULT_CHECK = '✅'
DEFAULT_FIX = '🔧'
DEFAULT_ERROR = '💢'
DEFAULT_STOP = '🛑'
DEFAULT_GOLDCHECK = '🎉'
DEFAULT_REJECT = '❌'
DEFAULT_ALERT = '❗'
DEFAULT_QOC = '🛃'
DEFAULT_METADATA = '📝'
DEFAULT_THUMBNAIL = '🖼️'
DEFAULT_WRITEUP = '✏️' 
DEFAULT_SENDBACK = '➡️'
DEFAULT_UPDATED = '🤛'
DEFAULT_CALENDAR_1 = '📆'
DEFAULT_CALENDAR_2 = '📅'
DEFAULT_CALENDAR_3 = '🗓️' 
DEFAULT_PIN = '📌'
DEFAULT_JINGLE = '🔔' 

QOC_DEFAULT_LINKERR = '🔗'
QOC_DEFAULT_BITRATE = '🔢'
QOC_DEFAULT_CLIPPING = '📢'

#NOTE: (Ahmayk) reacts that match to a single emoji,
# either a custom emoji or a default fallback
class ReactType(Enum):
    NULL = auto()
    GOLDCHECK = auto()
    CHECK = auto()
    REGULAR_CHECK = auto()
    FIX = auto()
    REJECT = auto()
    STOP = auto()
    STOPSKIP = auto()
    ALERT = auto()
    QOC = auto()
    METADATA = auto()
    THUMBNAIL = auto()
    EMAILSENT = auto()
    ANTIMAIL = auto()
    WRITEUP = auto()
    SENDBACK = auto()
    UPDATED = auto()
    CALENDAR = auto()
    BITRATE = auto()
    CLIPPING = auto()
    JINGLE = auto()
    LINKERR = auto()
    PIN = auto()

class ReactInfo(NamedTuple):
    default_names: list[str]
    custom_names: list[str]

REACT_INFOS: dict[ReactType, ReactInfo] = {
    ReactType.GOLDCHECK: ReactInfo([DEFAULT_GOLDCHECK], ["goldcheck"]),
    ReactType.CHECK: ReactInfo([DEFAULT_CHECK], ["check"]),
    ReactType.REGULAR_CHECK: ReactInfo([DEFAULT_CHECK], []),
    ReactType.FIX: ReactInfo([DEFAULT_FIX], ["fix", "wrench"]),
    ReactType.REJECT: ReactInfo([DEFAULT_REJECT], ["reject"]),
    ReactType.STOP: ReactInfo([DEFAULT_STOP], ["stop", "octagonal"]),
    ReactType.STOPSKIP: ReactInfo([""], ["stopskip"]),
    ReactType.ALERT: ReactInfo([DEFAULT_ALERT], ["alert"]),
    ReactType.QOC: ReactInfo([DEFAULT_QOC], ["qoc"]),
    ReactType.METADATA: ReactInfo([DEFAULT_METADATA], ["metadata"]),
    ReactType.THUMBNAIL: ReactInfo([DEFAULT_THUMBNAIL], ["thumbnail"]),
    ReactType.EMAILSENT: ReactInfo([""], ["emailsent"]),
    ReactType.ANTIMAIL: ReactInfo([""], ["antimail"]),
    ReactType.WRITEUP: ReactInfo([DEFAULT_WRITEUP], ["writeup"]),
    ReactType.SENDBACK: ReactInfo([DEFAULT_SENDBACK], ["sendback"]),
    ReactType.UPDATED: ReactInfo([DEFAULT_UPDATED], ["updated"]),
    ReactType.CALENDAR: ReactInfo([DEFAULT_CALENDAR_1, DEFAULT_CALENDAR_2, DEFAULT_CALENDAR_3], ["calendar"]),
    ReactType.BITRATE: ReactInfo([QOC_DEFAULT_BITRATE], ["bitrate"]),
    ReactType.CLIPPING: ReactInfo([QOC_DEFAULT_CLIPPING], ["clipping"]),
    ReactType.JINGLE: ReactInfo([DEFAULT_JINGLE], ["jinglebell"]),
    ReactType.LINKERR: ReactInfo([QOC_DEFAULT_LINKERR], [""]),
    ReactType.PIN: ReactInfo([DEFAULT_PIN], [""]),
}

#NOTE: (Ahmayk) react categories where multiple emojis are valid
class ReactCategory(Enum):
    CHECKREQ = auto()
    NUMBER = auto()

REVIEW_REACT_LIST = [ReactType.CHECK, ReactType.GOLDCHECK, ReactType.FIX, ReactType.ALERT, ReactType.REJECT]
FIX_REACT_LIST = [ReactType.FIX, ReactType.ALERT]

KEYCAP_EMOJIS = {'2️⃣': 2, '3️⃣': 3, '4️⃣': 4, '5️⃣': 5, '6️⃣': 6, '7️⃣': 7, '8️⃣': 8, '9️⃣': 9, '🔟': 10}

#===============================================#
#                    REACTS
#===============================================#

discord.Emoji

def init_react(reaction: discord.Reaction) -> React:
    name = ""
    id = 0 
    string = ""
    if isinstance(reaction.emoji, str):
        name = reaction.emoji
        string = name
    elif isinstance(reaction.emoji, discord.Emoji) or isinstance(reaction.emoji, discord.PartialEmoji):
        name = reaction.emoji.name
        string = str(reaction.emoji) 
        if reaction.emoji.id:
            id = reaction.emoji.id
    else:
        assert False, "Unrecognized reaction type" # This shouldn't happen
    return React(id, name, string) 

def react_is(react_type: ReactType, name: str) -> bool:
    result = False
    name_lower = name.lower()
    if react_type in REACT_INFOS:
        result = (name_lower in REACT_INFOS[react_type].default_names) \
                  or (name_lower in REACT_INFOS[react_type].custom_names)
    else:
        assert f"Unimplemented ReactionType {react_type}"
    return result

def react_name_to_react_type(name: str) -> ReactType:
    result = ReactType.NULL
    name_lower = name.lower()
    for react_type, react_info in REACT_INFOS.items():
        if (
            name_lower in react_info.default_names 
            or name_lower in react_info.custom_names
        ):
            result = react_type
            break
    return result

def react_is_category(react_category: ReactCategory, name: str) -> bool:
    result = False
    name_lower = name.lower()
    match (react_category):
        case ReactCategory.CHECKREQ:
            result = name_lower.endswith("check") and name_lower[0].isdigit()
            if (result):
                print(f"{name} is checkreq!" )
        case ReactCategory.NUMBER:
            result = name in KEYCAP_EMOJIS
        case _:
            assert f"Unimplemented ReactionCategory {react_category}"
    return result

def react_type_to_react(react_type: ReactType, guild: Guild) -> React:
    result = React(0, "", "") 
    if react_type in REACT_INFOS:
        if len(REACT_INFOS[react_type].default_names):
            name = REACT_INFOS[react_type].default_names[0]
            result = React(0, name, name) 
        if guild:
            for custom_name in REACT_INFOS[react_type].custom_names:
                for e in guild.emojis:
                    if e.name.lower() == custom_name:
                        result = React(e.id, e.name, str(e))
                        break
    return result 

def react_is_one(reaction_type_list: List[ReactType], name: str) -> bool:
    for reaction_type in reaction_type_list:
        if react_is(reaction_type, name):
            return True
    return False

def rip_has_react(reaction_type_list: List[ReactType], rip: Rip) -> bool:
    for react in rip.reacts:
        if react_is_one(reaction_type_list, react.name):
            return True
    return False

def rip_react_count(reaction_type_list: List[ReactType], rip: Rip) -> int:
    result = 0
    for react in rip.reacts:
        if react_is_one(reaction_type_list, react.name):
            result += 1
    return result 

def rip_is_one_check_away_from_accept(rip) -> bool:
    #NOTE: (Ahmayk) does not take num checks needed into account, it could if we wanted
    #to standardize that in bot tho
    checks_count = rip_react_count([ReactType.CHECK], rip)
    rejects_count = rip_react_count([ReactType.REJECT], rip)
    has_valid_stop = rip_has_react([ReactType.STOP], rip) \
        and not rip_has_react([ReactType.GOLDCHECK], rip) \
        and not rip_has_react([ReactType.STOPSKIP], rip)
    is_valid = checks_count - rejects_count == 2 \
        and not rip_has_react(FIX_REACT_LIST, rip) \
        and not has_valid_stop
    return is_valid


#NOTE: (Ahmayk) This is an enum so we can iterate 
# all possible user react checks for caching on startup and validation
class UserReactCheckType(Enum):
    REVIEW = auto()
    FIX = auto()
    CHECK = auto()
    REJECT = auto()

def user_react_check_types_to_react_list(user_react_check_types: list[UserReactCheckType]) -> List[ReactType]:
    react_list = []
    for user_react_check_type in user_react_check_types:
        match(user_react_check_type):
            case UserReactCheckType.REVIEW: 
                react_list.extend(REVIEW_REACT_LIST)
            case UserReactCheckType.FIX:
                react_list.extend(FIX_REACT_LIST)
            case UserReactCheckType.CHECK:
                react_list.append(ReactType.CHECK)
            case UserReactCheckType.REJECT:
                react_list.append(ReactType.REJECT)
            case _:
                assert f'Unimplemented UserReactCheckType {user_react_check_type}'
    return react_list


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


def reaction_name_to_emoji_string(name: str, guild: Guild | None) -> str:
    result = "" 
    if len(name):
        if guild:
            for emoji in guild.emojis:
                if emoji.name == name:
                    result = str(emoji)
                    break
        if not len(result):
            for react_info in REACT_INFOS.values():
                if name in react_info.custom_names:
                    name = react_info.default_names[0]
                    break
        if not len(result):
            result = name

    return result

def parse_emojis_in_string(string: str, guild: Guild):

    def emoji_match_filter(match):
        name = match.group(1)
        result = reaction_name_to_emoji_string(name, guild)
        return result

    result = re.sub(r':(\w+):', emoji_match_filter, string) 

    def config_match_filter(match):
        result = get_config(match.group(1))
        return str(result)

    result = re.sub(r'%(\w+)%', config_match_filter, result) 

    return result


def message_has_react(react: React, message: Message) -> bool:
    result = False
    for reaction in message.reactions:
        message_react = init_react(reaction)
        result = react.name == message_react.name
        if result:
            break
    return result


async def update_rip_status_reacts(message: Message, react_types_add: list[ReactType], 
                                   react_types_remove: list[ReactType], guild: Guild) -> list[str]:
    error_strings = []
    for react_type in react_types_add:
        react = react_type_to_react(react_type, guild)
        if not message_has_react(react, message):
            errors = await discord_add_reaction(react.string, message)
            error_strings.extend(errors)
    for react_type in react_types_remove:
        react = react_type_to_react(react_type, guild)
        if message_has_react(react, message):
            #NOTE: (Ahmayk) this clears other users reacts too! Too far?
            #if we only removed our react, well we would continuously call this
            #becasue we don't check if our own react is there, and this is a performance hit
            errors = await discord_clear_reaction(react.string, message)
            error_strings.extend(errors)
    return error_strings