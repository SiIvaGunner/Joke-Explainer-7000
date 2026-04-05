import requests
import re
import threading
from bs4 import BeautifulSoup, Tag
from urllib.parse import quote, quote_plus, urljoin
from urllib3.util.retry import Retry
from difflib import SequenceMatcher
from typing import Callable, Any
from collections.abc import Iterable
from itertools import chain
from enum import Enum, auto
from requests.adapters import HTTPAdapter

from hq_strings import *

DEFAULT_SIMILARITY = 0.72

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

class VGMSite(Enum):
    ZOPHAR = auto()
    KHINSIDER = auto()
    VGMRIPS = auto()
    HSC64 = auto()

class VGMSiteInfo(NamedTuple):
    name: str
    pre_href: str
    search_url_pathname: str

VGM_SITE_INFOS: dict[VGMSite, VGMSiteInfo] = {} 
VGM_SITE_INFOS[VGMSite.ZOPHAR] = VGMSiteInfo("Zophar's Domain", 'https://www.zophar.net', '/music/search?search=')
VGM_SITE_INFOS[VGMSite.KHINSIDER] = VGMSiteInfo("Kingdom Hearts Insider", 'https://downloads.khinsider.com', '/search?search=')
VGM_SITE_INFOS[VGMSite.VGMRIPS] = VGMSiteInfo("VGMRips", 'https://vgmrips.net', '/packs/search?q=')
#NOTE: (Ahmayk) no info needed for HSC64 as we access everything from a json we predownload

class ScanResultType(Enum):
    ALBUM = auto() 
    TRACK = auto() 

class ScanResult(NamedTuple):
    vgm_site: VGMSite
    type: ScanResultType 
    title: str
    url: str
    hcs_dict: None | Any #NOTE: (Ahmayk) JSON

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


#NOTE: (Ahmayk) list of json, probably
hcs_index: list[Any] = []

track_urls_reported: set[str] = set()
album_urls_reported: set[str] = set()
cached_album_results = dict()
cached_track_results = dict()

NO_RESULTS_MSG = "No results to display."

def scan_vgm_site(url: str, vgm_site: VGMSite, scan_result_type: ScanResultType, output_scan_results: List[ScanResult]):

    # print(f"SCANNING SITE: {url}")

    assert vgm_site in VGM_SITE_INFOS
    vgm_site_info = VGM_SITE_INFOS[vgm_site]

    response = None
    soup = None
    try:
        agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        headers = {'User-Agent': agent}
        response = my_session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
    except (requests.exceptions.RequestException, AssertionError) as e:
        print(f"Error fetching data from {vgm_site_info.name}: {e}")

    if response and soup:
        aborted = False
        if (response.url.startswith("https://vgmrips.net/packs/pack") and response.url != url):
            # get the string contained in the <title> tag in the page
            title_tag = soup.find('title')
            assert (title_tag and isinstance(title_tag, Tag))
            title = title_tag.string
            assert isinstance(title, str)
            clean_title = title[0:title.index(" vgm music • VGMRips")]
            output_scan_results.append(ScanResult(vgm_site, scan_result_type, clean_title, response.url, None))
        else:
            all_link_tags = soup.find_all('a', href=True)
            for link_tag in all_link_tags:
                assert isinstance(link_tag, Tag)
                href = link_tag['href']
                assert isinstance(href, str)
                parent_tag = link_tag.parent
                assert isinstance(parent_tag, Tag)

                is_valid = True

                if "/game-soundtracks/arrangements" in href and parent_tag.name == "b":
                    aborted = True
                    break

                title = link_tag.get_text(strip=True)
                found_url = urljoin(vgm_site_info.pre_href, href)
                if not len(title): 
                    is_valid = False

                if scan_result_type == ScanResultType.ALBUM:
                    if (parent_tag.has_attr('class') and "albumIconLarge" in parent_tag['class']):
                        is_valid = False

                    title_lower = title.lower()
                    remix_keywords = ["restored", "fan remaster", "recreat", "fanmade", "fan-made", "soundfont remix", "soundtrack remake"]
                    for keyword in remix_keywords:
                        if keyword in title_lower:
                            is_valid = False
                            break

                    match vgm_site:
                        case VGMSite.ZOPHAR:
                            if (
                                href.count('/') < 3
                                or "patreon" in href 
                                or "music/letter" in href
                                or "search.html" in href
                            ):
                                is_valid = False
                        case VGMSite.KHINSIDER:
                            if not "/album/" in href:
                                is_valid = False
                        case VGMSite.VGMRIPS:
                            if not "/pack/" in href:
                                is_valid = False
                        case _:
                            assert "Unimplemented VGMSite Enum"

                if scan_result_type == ScanResultType.TRACK:
                    if vgm_site == VGMSite.VGMRIPS and "DUMMY#" in found_url:
                        # I dunno why this shows up in the url on VGMrips.
                        found_url = found_url[5:]

                    if (url in track_urls_reported):
                        is_valid = False
                    if ("/company/" in url or "/developer/" in url):
                        is_valid = False
                    if ("letter/)" in url):
                        is_valid = False
                    if ("Download all files as" in title or "Download original music files" in title):
                        is_valid = False

                if is_valid:
                    result = ScanResult(vgm_site, scan_result_type, title, found_url, None)
                    output_scan_results.append(result)

        if not aborted:
            print(f"Successfully fetched and parsed {vgm_site_info.name}. [{len(output_scan_results)} result(s)] {url}")


def search_sites_for_albums(game_name: str) -> List[ScanResult]:
    print(f"SEARCHING SITES FOR {game_name}")
    threads: List[threading.Thread] = []
    result_lists: List[List[ScanResult]] = []
    game_name = quote(game_name) 
    for i, vgm_site in enumerate(VGMSite):
        result_lists.insert(i, [])

        if vgm_site == VGMSite.HSC64:
            ##NOTE: No API call needed, reference downloaded json lookup
            if len(game_name) > 1:
                MAX_NUM = 30
                num = 0
                for hcs_game in hcs_index:
                    #assert isinstance(hcs_game, tuple[str, str])
                    if (num > MAX_NUM):
                        #NOTE: (Ahmayk) why was this truncated?
                        pass
                        # return
                    if "nm" in hcs_game:
                        hcs_game_name_dirty = hcs_game["nm"]
                    else: 
                        continue

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
                        url = "https://vgm.hcs64.com/?set=" + str(hcs_game["id"])
                        result_lists[i].append(ScanResult(vgm_site, ScanResultType.ALBUM, hcs_game_name, url, hcs_game))
                        num += 1
        else:
            assert vgm_site in VGM_SITE_INFOS
            vgm_site_info = VGM_SITE_INFOS[vgm_site]
            search_url = vgm_site_info.pre_href + vgm_site_info.search_url_pathname + game_name
            thread = threading.Thread(target=scan_vgm_site, args=(search_url, vgm_site, ScanResultType.ALBUM, result_lists[i]))
            thread.start()
            threads.append(thread)

    for t in threads:
        t.join()

    result = []
    for l in result_lists:
        result.extend(l)
    return result

def search_album_for_track(scan_result_album: ScanResult, input_track_name: str, output_track_strings: List[str]):

    NO_URL_MSG = "[No URL]"

    if scan_result_album.vgm_site == VGMSite.HSC64:
        new_url = "https://" + quote(scan_result_album.hcs_dict["sd"]) + ".joshw.info/.filelists/" + scan_result_album.hcs_dict["nm"] + ".json"
        json = get_json(new_url)
        try:
            assert isinstance(json, dict)
        except AssertionError as e:
            print(e)
        results = []
        if (not json is None):
            for file in json["files"]:
                results.append(ScanResult(VGMSite.HSC64, ScanResultType.TRACK, file["name"], NO_URL_MSG, None))
                # print(file["name"])
    else:
        scan_result_tracks: List[ScanResult] = []
        scan_vgm_site(scan_result_album.url, scan_result_album.vgm_site, ScanResultType.TRACK, scan_result_tracks)

        for scan_result in scan_result_tracks:
            track_title = scan_result.title
            track_url = scan_result.url
            closeness_title = SequenceMatcher(None, track_title, input_track_name).ratio()

            if (closeness_title >= DEFAULT_SIMILARITY or input_track_name.strip() in track_title
                    or quote(input_track_name.strip()) in track_url):

                full_url = ""
                if track_url == NO_URL_MSG:
                    full_url = track_url
                else:
                    full_url = urljoin(scan_result_album.url, track_url)

                cool = f"{track_title} ({full_url}) exists at the album {scan_result_album.title} ({scan_result_album.url})"
                output_track_strings.append(cool)
                track_urls_reported.add(track_url)
                # print(f"\nTrack found: {cool}")


YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query="
COMMON_JOKE_DELIMITERS = ",."
NO_TRACK = "!@#$%^&*()"

def parseTitle(title: str, divider: str):
    pairs: list[list[str]] = []

    foundDivider = False
    for i in range(len(title)):

        j = i+len(divider)
        if (title[i:j] == divider):
            before = title[0:i]
            after = title[j-1:]
            foundDivider = True
            before = before.strip()
            after = after.strip()

            pairs.append([before, after])

            if (before.endswith(")") and "(" in before):
                before_no_mixname = before[0:before.rindex("(")]
                pairs.extend(parseTitle(before_no_mixname + divider + after, divider))

            if (after.endswith(")") and "(" in after):
                after_no_mixname = after[0:after.rindex("(")]
                pairs.extend(parseTitle(before + divider + after_no_mixname, divider))

    if not foundDivider:
        pairs.append([NO_TRACK, title])

    print(f'PAIRS: {pairs}')
    return pairs


def search_for_game_and_track(game_name: str, track_name: str, output_track_results: list[str]):

    print(f'SEARCHING FOR GAME {game_name} AND TRACK {track_name}')

    scan_results = None
    if game_name in cached_album_results:
        scan_results = cached_album_results[game_name]
    else:
        scan_results = search_sites_for_albums(game_name)
        cached_album_results[game_name] = scan_results

    track_list = []
    if (game_name, track_name) in cached_track_results:
        track_list = cached_track_results[(game_name, track_name)]
    else:
        sorted_all_results = list(reversed(sorted(scan_results, key=lambda album: SequenceMatcher(None, album.title, game_name).ratio())))
        threads = []
        depth = 10
        thread_results_list: List[List[str]] = [] 

        for i, scan_result in enumerate(sorted_all_results[:depth]):
            thread_results_list.insert(i, [])
            thread = None
            thread = threading.Thread(target=search_album_for_track, args=(scan_result, track_name, thread_results_list[i]))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        for results_list in thread_results_list:
            for result in results_list:
                if result not in track_list:
                    track_list.append(result)

        if len(track_list) == 0:
            print(f"Unable to find TRACK '{track_name}' for GAME '{game_name}' – it may not be on any of these albums, or it may have a different title.")

        cached_track_results[(game_name, track_name)] = track_list

    output_track_results.extend(track_list)


def find_song(title: str, divider: str):
    pairs: list[list[str]] = parseTitle(title, divider)
    threads = []
    track_string_lists: list[list[str]] = []
    for i, pair in enumerate(pairs):
        track_string_lists.insert(i, [])
        if(pair[0] == NO_TRACK or pair[1] == NO_TRACK):
            continue
        secondThread = threading.Thread(target=search_for_game_and_track, args=(pair[1], pair[0], track_string_lists[i]))
        threads.append(secondThread)
        secondThread.start()
        firstThread = threading.Thread(target=search_for_game_and_track, args=(pair[0], pair[1], track_string_lists[i]))
        threads.append(firstThread)
        firstThread.start()

    # Wait for all threads to finish
    for t in threads:
        t.join()

    track_results = []
    for track_string_list in track_string_lists:
        for track_result in track_string_list:
            if not track_result in track_results:
                track_results.append(track_result)
                print(f'---REFERENCE: {track_result}')

    return(track_results, pairs)



def parse_rip(submissionText: str):
    global hcs_index
    broken_sites = set()
    track_urls_reported = set()
    album_urls_reported = set()
    cached_album_results = dict()
    cached_track_results = dict()
    if (len(hcs_index) == 0):
        hcs_index = get_index()

    title = get_raw_rip_title(submissionText)
    if title is None: 
        title = submissionText

    print(f'TITLE: {title}')

    print("Searching...")

    track_results, pairs = find_song(title, ' - ')
    #For each hyphen in title, return pairs of all the text before and after it
    if (len(track_results) > 0):
        print("Done finding reference!")
        pass
    else:
        print("Unable to find track. Searching for game album:")
        games_to_search = set()
        for pair in pairs:
            games_to_search.add(pair[1])

        threads = []
        thread_results_list = [[] for _ in range(len(games_to_search))]

        def _search_for_game_in_thread(game_name: str, should_print: bool, show_all: bool, should_search_vgmrips: bool, results_list: list, index: int):
            results = search_sites_for_albums(game_name)
            if(results):
                exact_matches = []
                other_results = []
                for result in results:
                    if result.title == game_name:
                        exact_matches.append(result)
                    else:
                        other_results.append(result)

                if len(exact_matches):
                    plural = ""
                    if len(exact_matches) > 1: plural = "es"
                    print(f"\nExact title match{plural} for {game_name} found:")
                    for result in exact_matches:
                        print(f"  {result.url}")
                else:
                    print(f"\nNo exact title match found for \"{game_name}\".")

                # if len(other_results):
                    # sorted_other_results = list(reversed(sorted(other_results, key=lambda result: SequenceMatcher(None, result.title, game_name).ratio())))
                    # print("\nOther search results were:")
                    # for result in sorted_other_results:
                    #     print("  ", result)
                # else:
                    # print("No other results to display.")
                    
                if not exact_matches and not other_results:
                    print(NO_RESULTS_MSG)

            else:
                print(NO_RESULTS_MSG)
            results_list[index] = list(results)

        for i, game_name in enumerate(list(games_to_search)):
            thread = threading.Thread(target=_search_for_game_in_thread, args=(game_name, False, True, True, thread_results_list, i))
            threads.append(thread)
            thread.start()

        for t in threads:
            t.join()

    my_url = YOUTUBE_SEARCH_URL + quote_plus(title)
    print(f"Game search results: {my_url}")

    joke = get_rip_joke(submissionText)
    print(f'JOKE: {joke}')
    if len(joke):
        jokes = re.split(f'[{COMMON_JOKE_DELIMITERS}]', joke)

        for(joke) in jokes:
            my_url = YOUTUBE_SEARCH_URL + quote_plus(joke)
            print(f"Joke search results on YouTube: {my_url}")

        joke_results = []

        dividers = [' - ', ' from ']
        for divider in dividers:
            for joke in jokes:
                temp_results = find_song(joke, divider)[0]
                joke_results.extend(temp_results)

        if (len(joke_results) > 0):
            print(joke_results)

    else:
        print("Joke not found.")
        pass

    print("All done!")




import sys
parse_rip(sys.argv[1])