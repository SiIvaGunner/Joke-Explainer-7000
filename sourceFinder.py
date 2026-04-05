import requests
import re
import threading
from bs4 import BeautifulSoup, Tag
from urllib.parse import quote, quote_plus, urljoin
from urllib3.util.retry import Retry
from difflib import SequenceMatcher
from typing import Callable
from collections.abc import Iterable
from itertools import chain
from enum import Enum
from requests.adapters import HTTPAdapter

Zophar = True
KHInsider = True
VGMRips = True
HCS64 = True

DEFAULT_SIMILARITY = 0.72

NO_URL_MSG = "[No URL]"


my_session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS"]
)

adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=50, pool_maxsize=50)

my_session.mount("http://", adapter)
my_session.mount("https://", adapter)

class ResultType(Enum):
    ALBUM = 1
    TRACK = 2

class Result():
    #has three public variables initialized with its constructor: title, url, and source
    def __init__(self, resultType: ResultType, title: str, url: str, source: str, hcs_dict=None):
        self.resultType = resultType
        self.title = title
        self.url = url
        self.source = source
        self.hcs_dict = hcs_dict

    def __str__(self):
        return(f"{self.title} ({self.url})")
    def get_dict(self): return self.hcs_dict

def get_json(link: str) -> Iterable:
    # Load the json file at https://vgm.hcs64.com/index-clean.json
    try:
        response = my_session.get(link)
        response.raise_for_status()  # Raise an exception for HTTP errors
        json = response.json()
        return json
    except requests.exceptions.RequestException as e:
        print(f"Error fetching index from HCS64: {e}")
        return []

HCS64_INDEX_URL = "https://vgm.hcs64.com/index-clean.json"
def get_index():
    json: Iterable = get_json(HCS64_INDEX_URL)
    #json_proc: set[tuple] = set()
    #try:
    #  for element in json:
    #    assert isinstance(element, dict)
    #   json_proc.add(tuple(element.items()))


    #except AssertionError as e:
    #  print(f"2Error fetching index from HCS64: {e}")
    #  return set()
    return json


hcs_index = []

broken_sites: set[str] = set()
track_urls_reported: set[str] = set()
album_urls_reported: set[str] = set()
cached_album_results = dict()
cached_track_results = dict()

NO_RESULTS_MSG = "No results to display."

AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
headers = {'User-Agent': AGENT}

vgmRipsPackUrl = "https://vgmrips.net/packs/pack"


def parse_link_tag(link_tag, pre_href: str, target_name: str, filter: Callable, resultType: ResultType):
    href = link_tag['href']
    assert isinstance(href, str)
    if filter(href):
        full_url = urljoin(pre_href, href)
        title = link_tag.get_text(strip=True)
        if (title):
            return Result(resultType, title, full_url, target_name)
    return None


# I HATE REMIX ALBUMS ON KHINSIDER!
# I HATE REMIX ALBUMS ON KHINSIDER!
remix_keywords: set['str'] = {"restored",
                                    "fan remaster", "recreat",
                                    "fanmade", "fan-made",
                                    "soundfont remix",
                                    "soundtrack remake"}

def validate_album(album: Result):
    title: str = album.title.lower()
    for keyword in remix_keywords:
        if keyword in title:
            return False
    return True

HCS64_SET_URL = "https://vgm.hcs64.com/?set="

# Generic link-collecting function
def scan_page(base_url: str,
                            target_name: str,
                            pre_href: str,
                            filter: Callable,
                            should_print: bool,
                            resultType: ResultType):

    # If this site has thrown errors before, don't even bother searching again.
    if target_name in broken_sites:
        return []

    page_results = []
    try:
            response: requests.Response = my_session.get(base_url, headers=headers, timeout=10)
            response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
            soup: BeautifulSoup = BeautifulSoup(response.text, 'html.parser')

            # get host name of website

            if (response.url.startswith(vgmRipsPackUrl) and response.url != base_url):
                # get the string contained in the <title> tag in the page
                title_tag = soup.find('title')
                assert (title_tag and isinstance(title_tag, Tag))
                title = title_tag.string
                assert isinstance(title, str)
                clean_title = title[0:title.index(" vgm music • VGMRips")]
                page_results = [Result(resultType, clean_title, response.url,target_name)]
            else:
                all_link_tags = soup.find_all('a', href=True)
                for link_tag in all_link_tags:
                    assert isinstance(link_tag, Tag)
                    href = link_tag['href']
                    assert isinstance(href, str)
                    parent_tag = link_tag.parent
                    assert isinstance(parent_tag, Tag)
                    if "/game-soundtracks/arrangements" in href and parent_tag.name == "b": return []
                    if (parent_tag.has_attr('class') and "albumIconLarge" in parent_tag['class']): continue
                    result = parse_link_tag(link_tag, pre_href, target_name, filter, resultType)
                    if (result and validate_album(result)): page_results.append(result)

            if target_name and target_name != "" and should_print:
                print(f"Successfully fetched and parsed {target_name}. [{len(page_results)} result(s)]")
    except (requests.exceptions.RequestException, AssertionError) as e:
            print(f"Error fetching data from {target_name}: {e}")
            broken_sites.add(target_name)

    return page_results


# Searches specifically for albums
def search_db(game: str,
                            base_url: str,
                            search_mod: str,
                            site_name: str,
                            pre_href: str,
                            filter: Callable,
                            should_print: bool):
    search_url = base_url + search_mod + quote_plus(game)
    return scan_page(search_url, site_name + " search page",
                                     pre_href, filter, should_print, ResultType.ALBUM)

def search_db_in_thread(game: str,
                            base_url: str,
                            search_mod: str,
                            site_name: str,
                            pre_href: str,
                            filter: Callable,
                            should_print: bool,
                            results: list,
                            index: int):
    res = search_db(game,
                            base_url,
                            search_mod,
                            site_name,
                            pre_href,
                            filter,
                            should_print)
    results[index] = res


def search_HCS(game_name: str, should_print: bool, results: list):
    if len(game_name) < 2:
        return
    my_results = []
    MAX_NUM = 30
    num = 0
    for hcs_game in hcs_index:
        #assert isinstance(hcs_game, tuple[str, str])
        if (num > MAX_NUM):
            return
        if "nm" in hcs_game:
            hcs_game_name_dirty = hcs_game["nm"]
        else: continue

        hcs_game_name = ""
        if "/" in hcs_game_name_dirty:
            hcs_game_name = hcs_game_name_dirty[hcs_game_name_dirty.index("/")+1:]
        else:
            hcs_game_name = hcs_game_name_dirty
        if (game_name.lower()) in (hcs_game_name.lower()):
            # if there are are "[" or "(" in hcs_game_name, remove everything after them
            if "[" in hcs_game_name:
                hcs_game_name = hcs_game_name[0:hcs_game_name.index("[")]
            if "(" in hcs_game_name:
                hcs_game_name = hcs_game_name[0:hcs_game_name.index("(")]
            hcs_game_name = hcs_game_name.strip()
            url = HCS64_SET_URL + str(hcs_game["id"])

            my_results.append(Result(ResultType.ALBUM, hcs_game_name, url, "HCS64", hcs_game))
            num += 1
    results.extend(my_results)


# The way these requests are called is a little bit janky but it works
# and I don't wanna mess it up

def search_sites(game: str, doZophar: bool, doKHInsider: bool,
                                 doVGMRips: bool, doHCS64: bool, should_print: bool):
    list_of_results = [[] for _ in range(4)]


    threads = []
    if doZophar:
        zopharThread = threading.Thread(target=search_db_in_thread,
                                args=(game,
                                "https://www.zophar.net/music",
                                "/search?search=",
                                "Zophar's Domain",
                                "https://www.zophar.net",
                                lambda href: ((href.count('/') >= 3)
                                                            and not ("patreon") in href
                                                            and not ("music/letter") in href
                                                            and not 'search.html' in href),
                                should_print,
                                list_of_results, 0))
        threads.append(zopharThread)
        zopharThread.start()
    if doKHInsider:
        khThread = threading.Thread(target=search_db_in_thread,
                            args=(game,
                            "https://downloads.khinsider.com",
                            "/search?search=",
                            "Kingdom Hearts Insider",
                            "https://downloads.khinsider.com",
                            lambda href: ("/album/" in href),
                            should_print,
                                list_of_results, 1))
        threads.append(khThread)
        khThread.start()
    if doVGMRips:
        vgmripsThread = threading.Thread(target=search_db_in_thread,
                            args=(game,
                            "https://vgmrips.net/packs",
                            "/search?q=",
                            "VGMRips",
                            "https://vgmrips.net",
                            lambda href: ("/pack/" in href),
                            should_print,
                                list_of_results, 2))
        threads.append(vgmripsThread)
        vgmripsThread.start()
    if doHCS64:
        hcs64Thread = threading.Thread(target=search_HCS, args=(game, should_print,list_of_results[3]))
        threads.append(hcs64Thread)
        hcs64Thread.start()
    for t in threads:
        t.join()

    return list_of_results


def print_subresults(theList: list[Result], name):
    print(name)
    if theList:
            for result in theList:
                print("  ", result)
    else:
            print(NO_RESULTS_MSG)

def print_results(all_results: Iterable, show_all: bool, game: str):
    exact_matches = []
    other_results = []
    for result in all_results:
        if result.title == game:
            exact_matches.append(result)
        else:
            other_results.append(result)

    sorted_other_results = list(reversed(sorted(
            other_results,
            key=lambda result:
                SequenceMatcher(None, result.title, game).ratio())))

    if exact_matches:
        plural = ""
        if len(exact_matches) > 1: plural = "es"
        print(f"\nExact title match{plural} for {game} found:")
        for result in exact_matches:
            print(f"  {result.url}")

        if show_all:
            if (sorted_other_results):
                print_subresults(sorted_other_results,
                    "\nOther search results were:")
            else:
                print("No other results to display.")
    else:
        print_subresults(sorted_other_results,
                                         f"\nNo exact title match found for \"{game}\". Search results were:")


def validate_track(title: str, url: str):
    if (url in track_urls_reported):
        return False
    if ("/company/" in url or "/developer/" in url):
        return False
    if ("letter/)" in url):
        return False

    if ("Download all files as" in title or "Download original music files" in title):
        return False
    #rint(url)
    return True

def _find_track_for_album_thread(album, track, similarity, game, should_print, thread_results):
    track_results = []
    if "vgm.hcs" in album.url:
        track_results = hcs_album_track_finder(album, track, similarity, game, should_print)
    else:
        track_results = scan_page(
            album.url,
            album.source,
            album.title,
            lambda x: True,
            should_print,
            ResultType.TRACK)

    if (track_results is None): return

    for myTrack in track_results:
        track_title = myTrack.title
        track_url = myTrack.url
        if ("DUMMY#" in track_url):
            # I dunno why this shows up in the url on VGMrips.
            track_url = track_url[5:]
        if (not validate_track(track_title, track_url)): continue
        closeness_title = SequenceMatcher(None, track_title, track).ratio()
        if (closeness_title >= similarity or track.strip() in track_title
                or quote(track.strip()) in track_url):

            full_url = ""
            if track_url == NO_URL_MSG:
                full_url = track_url
            else:
                full_url = urljoin(album.url, track_url)

            cool = f"{track_title} ({full_url}) exists at the album {album.title} ({album.url})"
            thread_results.append(cool)
            track_urls_reported.add(track_url)
            if should_print:
                print(f"\nTrack found: {cool}")

def hcs_album_track_finder(album, track, similarity, game, should_print):
    hcs_album_dict = album.get_dict()
    new_url = "https://" + quote(hcs_album_dict["sd"]) + ".joshw.info/.filelists/" + hcs_album_dict["nm"] + ".json"
    json = get_json(new_url)
    try:
        assert isinstance(json, dict)
    except AssertionError as e:
        print(e)
        return []
    results = []
    if (not json is None):
        for file in json["files"]:
            results.append(Result(ResultType.TRACK, file["name"], NO_URL_MSG, "HCS64"))
            #print(file["name"])
    return results


def find_track(track: str,
                                     results: Iterable,
                                     similarity: float,
                                     depth: int,
                                     should_print: bool,
                                     game: str):
    tracks_found = []

    sorted_all_results = list(reversed(sorted(
        results,
        key=lambda album: SequenceMatcher(None, album.title, game).ratio())))

    threads = []
    thread_results_list = [[] for _ in range(len(sorted_all_results[:depth]))]

    for i, album in enumerate(sorted_all_results[:depth]):
        thread = None

        thread = threading.Thread(target=_find_track_for_album_thread, args=(album, track, similarity, game, should_print, thread_results_list[i]))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    for tr in thread_results_list:
        for result in tr:
            if result not in tracks_found:
                tracks_found.append(result)


    if (len(tracks_found) == 0 and should_print):
        print(f"\nUnable to find Track '{track}' – it may not be on any of these albums, or it may have a different title.")
    return tracks_found


def search_for_game(game: str, should_print=True, show_all=True, should_search_vgmrips=True):
    results = chain.from_iterable(
            search_sites(game,
                                     Zophar,
                                     KHInsider,
                                     VGMRips and should_search_vgmrips,
                                     HCS64,
                                     should_print))

    if(results):
        print_results(results, show_all, game)
        return results
    else:
        print(NO_RESULTS_MSG)
        return results


def clean_title(title: str):
    #if title ends with "Super Smash Bros. UItimate", replace that ending with "Super Smash Bros. Ultimate"
    if title.endswith("Super Smash Bros. UItimate"):
        title = title.replace("Super Smash Bros. UItimate", "Super Smash Bros. Ultimate")
    return title


YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query="
COMMON_JOKE_DELIMITERS = ",."
def parse_joke(joke_line: str):
    jokes = re.split(f'[{COMMON_JOKE_DELIMITERS}]', joke_line)

    for(joke) in jokes:
            my_url = YOUTUBE_SEARCH_URL + quote_plus(joke)
            print(f"Joke search results on YouTube: {my_url}")

    joke_results = []

    dividers = [' - ', ' from ']
    for divider in dividers:
        for joke in jokes:
            temp_results = find_song(joke, divider, DEFAULT_SIMILARITY)[0]
            joke_results.extend(temp_results)

    if (len(joke_results) > 0):
        print(joke_results)


def remove_last_mixname(name):
    return name[0:name.rindex("(")]

def parseTitle(title: str, divider: str):
    pairs: list[list[str]] = []

    foundDivider = False
    for i in range(len(title)):

        j = i+len(divider)
        if (title[i:j] == divider):
            before = title[0:i]
            after = title[j-1:]
            foundDivider = True
            beforeStrip = before.strip()
            afterStrip = after.strip()


            pairs.append([beforeStrip, afterStrip])
            if (beforeStrip.endswith(")") and "(" in beforeStrip):
                pairs.extend(parseTitle(
                        remove_last_mixname(before) + divider + after, divider))
            if (afterStrip.endswith(")") and "(" in afterStrip):
                pairs.extend(parseTitle(
                        before + divider + remove_last_mixname(afterStrip), divider))
    if not foundDivider:
        pairs.append([NO_TRACK, title])
    return pairs




def search_for_game_and_track(game: str, track: str, sensitivity: float):

    my_results = None
    if game in cached_album_results:
        my_results = cached_album_results[game]
    else:
        my_results = list(chain.from_iterable(
                search_sites(game, Zophar, KHInsider, VGMRips, HCS64, False)))
        cached_album_results[game] = my_results


    track_record = None
    if (game, track) in cached_track_results:
        track_record = cached_track_results[(game, track)]
    else:
        track_record = find_track(
                track, my_results, sensitivity, 10, False, game)
        cached_track_results[(game, track)] = track_record
    return(track_record)

def search_for_game_and_track_in_thread(game: str, track: str, sensitivity: float, track_results: list, index: int):
    track_results[index] = search_for_game_and_track(game, track, sensitivity)

NO_TRACK = "!@#$%^&*()"


def find_song(title: str, divider: str, similarity: float):
    pairs: list[list[str]] = parseTitle(title, divider)
    track_results = []
    threads = []
    bag = [list()] * (len(pairs) * 2)
    index = 0
    for pair in pairs:
        if(pair[0] == NO_TRACK or pair[1] == NO_TRACK):
            continue
        secondThread = threading.Thread(target=search_for_game_and_track_in_thread, args=(pair[1], pair[0], similarity,bag,index,))
        threads.append(secondThread)
        secondThread.start()
        index += 1
        firstThread = threading.Thread(target=search_for_game_and_track_in_thread, args=(pair[0], pair[1], similarity,bag,index,))
        threads.append(firstThread)
        firstThread.start()
        index += 1


# Wait for all threads to finish
    for t in threads:
            t.join()
    for thing in bag:
        for result in thing:
            if not result in track_results:
                track_results.append(result)
                print(result)

    return(track_results, pairs)


def _search_for_game_in_thread(game_name: str, should_print: bool, show_all: bool, should_search_vgmrips: bool, results_list: list, index: int):
    results = search_for_game(game_name, should_print, show_all, should_search_vgmrips)
    results_list[index] = list(results)


def handle_title(title: str, should_search_vgmrips=True):
    track_results, pairs = find_song(title, ' - ', DEFAULT_SIMILARITY)
    #For each hyphen in title, return pairs of all the text before and after it
    if (len(track_results) > 0):
        print("Done finding reference!")
    else:
        print("Unable to find track. Searching for game album:")
        album_results = []
        games_to_search = set()
        for pair in pairs:
                games_to_search.add(pair[1])

        threads = []
        thread_results_list = [[] for _ in range(len(games_to_search))]

        for i, game_name in enumerate(list(games_to_search)):
                thread = threading.Thread(target=_search_for_game_in_thread,
                                                                args=(game_name, False, True, should_search_vgmrips, thread_results_list, i))
                threads.append(thread)
                thread.start()

        for t in threads:
                t.join()

        for result_list in thread_results_list:
                album_results.extend(result_list)

    my_url = YOUTUBE_SEARCH_URL + quote_plus(title)
    print(f"Game search results: {my_url}")


def parse_rip(submissionText: str):
    global hcs_index
    broken_sites = set()
    track_urls_reported = set()
    album_urls_reported = set()
    cached_album_results = dict()
    cached_track_results = dict()
    if (len(hcs_index) == 0):
        hcs_index = get_index()

    chunks = submissionText.split('```')
    if len(chunks) > 1:
        ripData = chunks[1]
    else:
        ripData = submissionText

    ripDataToLines = ripData.splitlines()
    if (ripDataToLines[0] == ""):
        title = ripDataToLines[1]
    else:
        title= ripDataToLines[0]

    print("Searching...")
    handle_title(title, True)

    found_joke = False
    if len(chunks) > 2:
        after_code = chunks[2].splitlines()

        for partition in after_code:
            if ('oke:' in partition or 'okes:' in partition or 'joke' in partition):
                joke_line = (partition[partition.index(':')+1:]).lstrip()
                parse_joke(joke_line)
                found_joke = True
                break
    if not found_joke:
        print("No joke line found.")

    print("All done!")

import sys
parse_rip(sys.argv[1])