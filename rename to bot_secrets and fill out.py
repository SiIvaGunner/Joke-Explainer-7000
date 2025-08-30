TOKEN = ""
YOUTUBE_API_KEY = "" # Replace with YouTube API key to enable metadata checking
YOUTUBE_CHANNEL_NAME = ""

# Define the server's channels here.
# Each channel is represented by a dictionary entry: key is channel ID and value is a list of channel's types in str.
#
# Supported types:
# - ROUNDUP: Enables !roundup-type commands. The channel should have rips in pins.
# - QOC: QoC channel. Used in !stats.
# - SUBS: Submission channel where subs are posted in main channel. Used in !count_subs and !stats.
# - SUBS_PIN: Submission channel where subs are posted in pins. Used in !count_subs and !stats.
# - SUBS_THREAD: Submission channel where subs are posted in threads. Used in !count_subs and !stats.
# - QUEUE: Queue channel where approved rips are posted. Used in !stats.
# - PROXY_ROUNDUP: Allows running ROUNDUP commands in a different channel with longer embed time by default. Must have at least 1 ROUNDUP channel, the 1st of which is used as default.
# - DEBUG: For testing purposes.
#
# The first ROUNDUP and SUBS channels are used as default !qoc_roundup and !count_subs channels.

CHANNELS = {
    -1: ['ROUNDUP', 'QOC'],
}

LOG_CHANNEL = -1