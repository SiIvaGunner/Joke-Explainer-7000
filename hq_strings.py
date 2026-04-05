
import typing
from typing import List, NamedTuple
import re

def split_long_message(a_message: str, character_limit) -> list[str]:  # avoid Discord's character limit
    """
    Split a long message to fit Discord's character limit.
    Aug 6 2025: apparently embeds have a higher character limit?
    """
    result: List[str] = []
    #TODO: (Ahmayk) While unlikely, this could split things into a group that is bigger than 2000.
    #we're not accounting for that currently.
    all_lines = a_message.splitlines()
    wall_of_text = ""
    is_in_regular_codeblock = False
    is_in_python_codeblock = False
    for i, line in enumerate(all_lines):
        line = line.replace('@', '')  # no more pings lol

        if "```py" in line:
            is_in_python_codeblock = True
        elif "```" in line:
            if is_in_python_codeblock:
                is_in_python_codeblock = False
            else:
                is_in_regular_codeblock = not is_in_regular_codeblock

        need_new_block = False 
        if len(wall_of_text) + len(line) > character_limit:
            need_new_block = True 

        if is_in_python_codeblock or is_in_regular_codeblock:
            if len(wall_of_text) + len(line) + len("\n```") > character_limit:
                need_new_block = True 
            if i != len(all_lines) - 1:
                len_current_and_next = len(wall_of_text) + len(line) + len(all_lines[i + 1])
                if len_current_and_next <= character_limit and \
                   len_current_and_next + len("\n```") > character_limit:
                    need_new_block = True 

        if need_new_block: 
            new_block = wall_of_text[:-1]
            wall_of_text = "" 
            if is_in_python_codeblock:
                new_block += '\n```' 
                wall_of_text = "```py\n" 
            if is_in_regular_codeblock:
                new_block += '\n```' 
                wall_of_text = "```" 
            result.append(new_block)

        wall_of_text += line + '\n'

    result.append(wall_of_text[:-1])  # add anything remaining
    return result


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

def clean_strings_markdown(texts: str) -> list[str]:
    result = []
    for s in texts:
        s = s.replace('*', '').replace('_', '').replace('|', '').replace('#', '').lower()
        result.append(s)
    return result

#TODO: (Ahmayk) don't return None, return empty string
def get_raw_rip_title(text: str) -> str | None:
    """
    Return the rip title line of a Discord message.
    Assumes the message follows the format where the rip title is after the first instance of ```
    """
    # Update: now use regex to find the first instance of "```[\n][text][\n]"
    rip_title = re.search(r'\`\`\`\n*.*\n', text)
    if rip_title is not None:
        rip_title = rip_title.group(0)
        rip_title = rip_title.replace('`', '')
        rip_title = rip_title.replace('\n', '')

    return rip_title


def get_rip_title(text: str) -> str:
    """
    Wrapper function to format unusual or spoiler rip titles
    """
    rip_title = get_raw_rip_title(text)
    if rip_title is None:
        return "`[Unusual Pin Format]`"
    elif '||' in text.split('```')[0]:
        # if || is detected in the message before the first ```, make the rip title into spoiler
        return "`[Rip Contains Spoiler]`"
    elif rip_title == 'ansi':
        # fairly sure we will never upload a video named "ansi". other codeblock formats exist but this one is the most common for color so
        return get_raw_rip_title(text.replace('```ansi', '```', 1))
    else:
        return rip_title


def get_raw_rip_author(text: str) -> str:
    """
    Return the rip author line of a Discord message.
    Assumes the message follows the format where the rip author is after the first instance of ```
    """
    author = text.split("```")[0]
    author = author.replace('\n', '')
    author = author.replace('||', '') # in case of spoilered rips

    return author


def get_rip_author(text: str, message_author_name: str) -> str:
    """
    Wrapper function to format author line.
    If the line contains "by me", append the message sender's name to the author line.
    """
    author = get_raw_rip_author(text)
    
    if len(re.findall(r'\bby\b', author.lower())) == 0:
        # If "by" is not found, notify that the "author line" might be unusual
        author = author + " [Unusual Pin Format]"

    elif len(re.findall(r'\bby me\b', author.lower())) > 0: 
        # Overwrite it and do something else if the rip's author and the pinner are the same
        cleaned_author = message_author_name.split('#')[0]
        author += (f' (**{cleaned_author}**)')

    return author


def get_rip_description(text: str) -> str:
    """
    Return the description of a rip, i.e. the part inside ```
    """
    # Use a regular expression to find text between two ``` markers
    match = re.search(r'```(.*?)```', text, re.DOTALL)

    if match:
        # Return the extracted text, stripping any leading/trailing whitespace
        return match.group(1).strip()
    else:
        return ""  # Return empty string if no match was found

def get_rip_joke(text: str) -> str:
    result = ""
    after_desc = text
    chunks = text.split('```')
    if len(chunks) >= 2:
        after_desc = chunks[2]
    for line in after_desc.splitlines():
        if ('oke:' in line or 'okes:' in line or 'joke' in line):
            result = (line[line.index(':')+1:]).lstrip()
            break
    return result

def format_message_link(guild_id: int, channel_id: int, message_id: int):
    return  f"<https://discord.com/channels/{str(guild_id)}/{str(channel_id)}/{str(message_id)}>"


def extract_discord_link(text: str) -> str:
    regex = r'(https://discord\.com/channels/\d+/\d+/\d+)'
    match = re.search(regex, text)
    if match:
        return match.group(1)
    else:
        return ""


def line_contains_substring(line: str, substring: str) -> bool:
    """
    Helper function to search substrings in Discord markdown-formatted line, ignoring case and formatting
    """
    # Temporary "hacky" support for regex search until/if we figure out a better system
    if substring.startswith("r\'") and substring.endswith("\'") or substring.startswith("r\"") and substring.endswith("\""):
        regex_search_key = substring[2:-1]
        return re.search(regex_search_key, line) is not None
    return substring.lower() in line.replace('*', '').replace('_', '').replace('|', '').replace('#', '').lower()


#NOTE: (Ahmayk) does not check if input is actually an emoji
def emoji_to_react_name_if_emoji(s: str) -> str:
    react_input = s 
    match = re.fullmatch(r'<a?:(\w+):\d+>', react_input)
    if match:
        react_input = match.group(1)
    return react_input



class ParsedSearchInput(NamedTuple):
    search_keys: List[str]
    is_not: bool
    containing_error_string: str

def parse_search_input(args: List[str]) -> ParsedSearchInput:
    is_not = False
    if "NOT" in args:
        is_not = True
        args.remove("NOT")
    search_keys = " ".join([str(s) for s in args]).split('|')

    prefix = 'containing'
    if is_not:
        prefix = 'NOT containing'

    containing_error_string = ''
    if len(search_keys) == 1:
        containing_error_string = f'{prefix} `{search_keys[0]}`'
    elif len(search_keys) == 2:
        containing_error_string = f'{prefix} `{search_keys[0]}` or `{search_keys[1]}`'
    elif len(search_keys) > 2:
        containing_error_string = f'{prefix} `{search_keys}`'

    return ParsedSearchInput(search_keys, is_not, containing_error_string)

def search_with_parsed_input(text: str, parsed_search_input: ParsedSearchInput) -> bool:
    is_valid = False
    for key in parsed_search_input.search_keys:
        if line_contains_substring(text, key):
            is_valid = True
            break
    if parsed_search_input.is_not:
        is_valid = not is_valid 
    return is_valid