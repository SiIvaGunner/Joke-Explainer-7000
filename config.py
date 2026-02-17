import os
import json
from typing import NamedTuple, List, Callable

# ============ Channels ============== #

class ChannelConfig(NamedTuple):
    name: str
    id: int
    types: List[str]
    pinlimit_must_die_mode: bool

CHANNEL_KEY = "channels"
LOG_CHANNEL_KEY = "log_channel"

def get_channel_ids(type_filter: Callable[[List[str]], bool] = lambda _: True):
    _channels = get_config(CHANNEL_KEY)
    return [channel["id"] for channel in _channels if "id" in channel.keys() and type_filter(channel.get("types", []))]

def get_channel_config(id: int):
    _channels = get_config(CHANNEL_KEY)
    for channel in _channels:
        if channel.get("id", -1) == id:
            return ChannelConfig(
                channel.get("name", "`[Missing Channel Name]`"),
                id,
                channel.get("types", []),
                channel.get("pinlimit_must_die_mode", False)
            )
    # dummy value
    return ChannelConfig("`[Channel Not Found]`", id, [], False)

def get_log_channel():
    configs = _read_config_file()
    return configs[LOG_CHANNEL_KEY] if LOG_CHANNEL_KEY in configs.keys() else -1

def add_channel(name: str, id: int, types: List[str]):
    configs = _read_config_file()
    assert CHANNEL_KEY in configs.keys(), "Channel list missing from config.json. Please contact bot owner."
    assert id != -1, "Invalid channel ID."
    new_channel = {
        "name": name,
        "id": id,
        "types": types,
    }
    if "QOC" in types:
        new_channel["pinlimit_must_die_mode"] = False
    configs[CHANNEL_KEY].append(new_channel)
    _write_config_file(configs)

def remove_channel(id: int):
    configs = _read_config_file()
    assert CHANNEL_KEY in configs.keys(), "Channel list missing from config.json. Please contact bot owner."
    assert id != -1, "Invalid channel ID."
    index_to_remove = -1
    for i in range(len(configs[CHANNEL_KEY])):
        if configs[CHANNEL_KEY][i].get("id", -1) == id:
            index_to_remove = i
            break
    assert index_to_remove != -1, "Channel ID doesn't exist in list."
    configs[CHANNEL_KEY].pop(index_to_remove)
    _write_config_file(configs)

def set_channel_pinlimit_mode(id: int, enabled: bool):
    configs = _read_config_file()
    assert CHANNEL_KEY in configs.keys(), "Channel list missing from config.json. Please contact bot owner."
    assert id != -1, "Invalid channel ID."
    for i in range(len(configs[CHANNEL_KEY])):
        if configs[CHANNEL_KEY][i].get("id", -1) == id:
            if "QOC" in configs[CHANNEL_KEY][i].get("types", []):
                configs[CHANNEL_KEY][i]["pinlimit_must_die_mode"] = enabled
    _write_config_file(configs)

# ============ Other configs ============== #

def get_config(config: str):
    configs = _read_config_file()
    assert config in configs.keys(), f"Config `{config}` does not exist. Please contact bot owner."
    return configs[config]

def set_config(config: str, value):
    assert config != CHANNEL_KEY, "Please use channel-related functions to modify the list of channels."

    configs = _read_config_file()
    assert config in configs.keys(), f"Config `{config}` does not exist. Please contact bot owner."
    assert type(configs[config]) == type(value), "Type of config value does not match. Expected {}, got {} instead.".format(type(configs[config]).__name__, type(value).__name__)
    
    configs[config] = value
    _write_config_file(configs)

# ============ Local functions ============== #
# hey i should probably use locks on these

def _read_config_file():
    if os.path.exists('config.json'):
        with open('config.json', 'r', encoding='utf-8') as file:
            configs = json.load(file)
            return configs
    else:
        raise FileNotFoundError("config.json not found. Please contact bot owner to create the file.")

def _write_config_file(configs):
    with open('config.json', 'w', encoding='utf-8') as file:
        json.dump(configs, file, indent=4)


if __name__ == "__main__":
    pass