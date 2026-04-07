import requests
import re
import threading
import string
from bs4 import BeautifulSoup, Tag
from urllib.parse import quote, quote_plus, urljoin
from urllib3.util.retry import Retry
from difflib import SequenceMatcher
from typing import Any
from collections.abc import Iterable
from enum import Enum, auto
from requests.adapters import HTTPAdapter

from hq_strings import *
from simpleQoC.metadata import desc_to_dict, get_music_from_desc

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

class VGM_SITE(Enum):
    ZOPHAR = auto()
    KHINSIDER = auto()
    VGMRIPS = auto()
    HSC64 = auto()

class VGMSiteInfo(NamedTuple):
    name: str
    pre_href: str
    search_url_pathname: str

VGM_SITE_INFOS: dict[VGM_SITE, VGMSiteInfo] = {} 
VGM_SITE_INFOS[VGM_SITE.ZOPHAR] = VGMSiteInfo("Zophar", 'https://www.zophar.net', '/music/search?search=')
VGM_SITE_INFOS[VGM_SITE.KHINSIDER] = VGMSiteInfo("KHInsider", 'https://downloads.khinsider.com', '/search?search=')
VGM_SITE_INFOS[VGM_SITE.VGMRIPS] = VGMSiteInfo("VGMRips", 'https://vgmrips.net', '/packs/search?q=')
#NOTE: (Ahmayk) no info needed for HSC64 as we access everything from a json we predownload

class ScanResultType(Enum):
    ALBUM = auto() 
    TRACK = auto() 

class ScanResult(NamedTuple):
    vgm_site: VGM_SITE
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

cached_album_results = dict()
cached_track_results = dict()

def scan_vgm_site(url: str, vgm_site: VGM_SITE, scan_result_type: ScanResultType, output_scan_results: List[ScanResult]):

    print(f"SCANNING SITE: {url}")

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

        skip_link_parsing = False

        #NOTE: (Ahmayk) VGMRips redirects you to a page sometimes
        if (response.url.startswith("https://vgmrips.net/packs/pack") and response.url != url):
            page_element = soup.find('title')
            if page_element is not None and page_element.string is not None:
                title = page_element.string[0:page_element.string.index(" vgm music • VGMRips")]
                found_url = response.url
                skip_link_parsing = True
                output_scan_results.append(ScanResult(vgm_site, scan_result_type, title, found_url, None))

        if not skip_link_parsing:
            all_link_tags = soup.find_all('a', href=True)
            for link_tag in all_link_tags:
                assert isinstance(link_tag, Tag)
                href = link_tag['href']
                assert isinstance(href, str)

                is_valid = True
                found_url = ""
                title = "" 

                match vgm_site:
                    case VGM_SITE.ZOPHAR:

                        found_url = urljoin(vgm_site_info.pre_href, href)

                        if scan_result_type == ScanResultType.ALBUM:
                            if href.count('/') != 3:
                                is_valid = False
                            if not 'music' in href: 
                                is_valid = False
                            if "music/letter" in href:
                                is_valid = False
                            title = link_tag.get_text(strip=True)

                        if scan_result_type == ScanResultType.TRACK:
                            if not href.startswith('https://fi.zophar.net/soundfiles/'):
                                is_valid = False

                            if is_valid:
                                parent = link_tag.parent
                                if parent is not None:
                                    row = parent.parent
                                    if row is not None:
                                        name_tag = row.find('td', class_='name')
                                        if name_tag is not None:
                                            title = name_tag.get_text(strip=True)

                    case VGM_SITE.VGMRIPS:

                        if scan_result_type == ScanResultType.ALBUM:
                            found_url = urljoin(vgm_site_info.pre_href, href)
                            if href.count('/') != 5:
                                is_valid = False
                            if not "/packs/pack" in href:
                                is_valid = False
                            if "#autoplay" in href:
                                is_valid = False
                            title = link_tag.get_text(strip=True)

                        if scan_result_type == ScanResultType.TRACK:
                            found_url = urljoin(url, href)
                            if "#" not in href:
                                is_valid = False
                            if "DUMMY#" in found_url:
                                found_url = found_url[5:]
                            title = link_tag.get_text(strip=True)

                    case VGM_SITE.KHINSIDER:

                        found_url = urljoin(vgm_site_info.pre_href, href)

                        if scan_result_type == ScanResultType.ALBUM:
                            if href.count('/') != 3:
                                is_valid = False
                            if not "/game-soundtracks/album" in href:
                                is_valid = False
                            title = link_tag.get_text(strip=True)

                        if scan_result_type == ScanResultType.TRACK:

                            #NOTE: (Ahmayk) invalid by default unless we find the title 
                            is_valid = False

                            #NOTE: (Ahmayk) search for title! 
                            if href.endswith(".mp3"):
                                play_track_tag = link_tag.find_previous(attrs={"title": "play track"})
                                if play_track_tag is not None:
                                    parent = play_track_tag.parent
                                    if parent is not None and parent.contents is not None and len(parent.contents) > 7:
                                        #NOTE: (Ahmayk) finds the first link, this should always have the title 
                                        title_tag = parent.a
                                        if title_tag is not None:
                                            title = title_tag.get_text(strip=True)
                                            is_valid = True

                if not len(title) or not len(found_url): 
                    is_valid = False

                for scan_result in output_scan_results:
                    if scan_result.url == found_url:
                        is_valid = False

                if is_valid:

                    # if scan_result_type == ScanResultType.TRACK:
                        # print(link_tag)
                        # print(f'HREF: {href}')
                        # print(f'URL: {found_url}')
                        # print(f'Title: {title}')

                    assert len(title)
                    result = ScanResult(vgm_site, scan_result_type, title, found_url, None)
                    output_scan_results.append(result)


def search_sites_for_albums(game_name: str, output_scan_result_list: List[ScanResult]):
    print(f"SEARCHING SITES FOR {game_name}")
    threads: List[threading.Thread] = []
    result_lists: List[List[ScanResult]] = []
    game_name = quote(game_name) 
    for i, vgm_site in enumerate(VGM_SITE):
        result_lists.insert(i, [])

        if vgm_site == VGM_SITE.HSC64:
            # ##NOTE: No API call needed, reference downloaded json lookup
            # if len(game_name) > 1:
            #     MAX_NUM = 30
            #     num = 0
            #     for hcs_game in hcs_index:
            #         #assert isinstance(hcs_game, tuple[str, str])
            #         if (num > MAX_NUM):
            #             #NOTE: (Ahmayk) why was this truncated?
            #             pass
            #             # return
            #         if "nm" in hcs_game:
            #             hcs_game_name_dirty = hcs_game["nm"]
            #         else: 
            #             continue

            #         hcs_game_name = ""
            #         if "/" in hcs_game_name_dirty:
            #             hcs_game_name = hcs_game_name_dirty[hcs_game_name_dirty.index("/")+1:]
            #         else:
            #             hcs_game_name = hcs_game_name_dirty
            #         if (game_name.lower()) in (hcs_game_name.lower()):
            #             # if there are are "[" or "(" in hcs_game_name, remove everything after them
            #             if "[" in hcs_game_name:
            #                 hcs_game_name = hcs_game_name[0:hcs_game_name.index("[")]
            #             if "(" in hcs_game_name:
            #                 hcs_game_name = hcs_game_name[0:hcs_game_name.index("(")]
            #             hcs_game_name = hcs_game_name.strip()
            #             url = "https://vgm.hcs64.com/?set=" + str(hcs_game["id"])
            #             result_lists[i].append(ScanResult(vgm_site, ScanResultType.ALBUM, hcs_game_name, url, hcs_game))
            #             num += 1
            pass
        else:
            assert vgm_site in VGM_SITE_INFOS
            vgm_site_info = VGM_SITE_INFOS[vgm_site]
            search_url = vgm_site_info.pre_href + vgm_site_info.search_url_pathname + game_name

            if vgm_site == VGM_SITE.KHINSIDER:
                search_url += '&album_type='
                #NOTE: (Ahmayk) 
                # album type 1 = Soundtracks
                # album type 2 = Gamerips
                # Could include Singles and Compilations maybe. Are filtering so we don't get arrangmenets and remixes
                types = ['1', '2']
                for album_type in types:
                    thread = threading.Thread(target=scan_vgm_site, args=(search_url + album_type, vgm_site, ScanResultType.ALBUM, result_lists[i]))
                    thread.start()
                    threads.append(thread)
            else:
                thread = threading.Thread(target=scan_vgm_site, args=(search_url, vgm_site, ScanResultType.ALBUM, result_lists[i]))
                thread.start()
                threads.append(thread)

    for t in threads:
        t.join()

    for l in result_lists:
        output_scan_result_list.extend(l)


class SourceTrack(NamedTuple):
    vgm_site: VGM_SITE
    album_title: str
    album_url: str 
    track_title: str
    track_url: str

def add_source_track(scan_result_track: ScanResult, scan_result_album: ScanResult, output_source_tracks: List[SourceTrack]):
    assert scan_result_album.vgm_site == scan_result_track.vgm_site
    track_url = urljoin(scan_result_album.url, scan_result_track.url)
    source_track = SourceTrack(
        scan_result_album.vgm_site,
        scan_result_album.title,
        scan_result_album.url,
        scan_result_track.title,
        track_url 
    )
    output_source_tracks.append(source_track)

def get_tracks_in_album(scan_result_album: ScanResult, output_source_tracks: List[SourceTrack]):
    if scan_result_album.vgm_site == VGM_SITE.HSC64:
        # new_url = "https://" + quote(scan_result_album.hcs_dict["sd"]) + ".joshw.info/.filelists/" + scan_result_album.hcs_dict["nm"] + ".json"
        # #NOTE: (Ahmayk) very bad that we keep calling this
        # json = get_json(new_url)
        # try:
        #     assert isinstance(json, dict)
        # except AssertionError as e:
        #     print(e)
        # if (not json is None):
        #     for file in json["files"]:
        #         #TODO: (Ahmayk) really? no track URL? That doesn't seem right
        #         scan_result_track = ScanResult(VGM_SITE.HSC64, ScanResultType.TRACK, file["name"], "", None)
        #         add_source_track(scan_result_track, scan_result_album, output_source_tracks)
        pass
    else:
        scan_result_tracks: List[ScanResult] = []
        scan_vgm_site(scan_result_album.url, scan_result_album.vgm_site, ScanResultType.TRACK, scan_result_tracks)
        print(f"Returned tracks from scan for {scan_result_album.url}: {len(scan_result_tracks)}")
        for scan_result_track in scan_result_tracks:
            # print(f'track name in album {scan_result_album.title}: {scan_result_track.title}')
            add_source_track(scan_result_track, scan_result_album, output_source_tracks)


class GameAndTrackPair(NamedTuple):
    track_name: str
    game_name: str

def _parse_title_internal(title: str, divider: str, track_name: str) -> list[GameAndTrackPair]:
    pairs: list[GameAndTrackPair] = []
    for i in range(len(title)):
        j = i+len(divider)
        if (title[i:j] == divider):
            before = title[0:i]
            after = title[j-1:]
            before = before.strip()
            after = after.strip()

            before_no_mixname = "" 
            if (before.endswith(")") and "(" in before):
                before_no_mixname = before[0:before.rindex("(")].strip()
            if (after.endswith(")") and "(" in after):
                after_no_mixname = after[0:after.rindex("(")].strip()

            add_before_after = False
            add_after_before = False
            
            if len(track_name):
                if before == track_name:
                    add_before_after = True
                if after == track_name:
                    add_after_before = True
            else:
                add_before_after = True

            if add_before_after:
                pairs.append(GameAndTrackPair(before, after))
                if len(before_no_mixname):
                    pairs.append(GameAndTrackPair(before_no_mixname, after))
            if add_after_before:
                pairs.append(GameAndTrackPair(after, before))
                if len(after_no_mixname):
                    pairs.append(GameAndTrackPair(before, after_no_mixname))

    return pairs

def parseTitle(title: str, divider: str, track_name: str) -> list[GameAndTrackPair]:
    pairs = _parse_title_internal(title, divider, track_name)
    #NOTE: (Ahmayk) if trying to match to a track_name doesn't turn up anything, repeat without matching to track_name
    if not len(pairs) and len(track_name):
        pairs = _parse_title_internal(title, divider, "")
    return pairs


def get_cleaned_words(title: str) -> str:
    title = title.lower()
    title = title.translate(str.maketrans('', '', string.punctuation))
    #NOTE: (Ahmayk) remove common mixname keywords, matching to them dilutes search
    title = title.replace("version", "")
    title = title.replace("mix", "")
    return title


#https://www.geeksforgeeks.org/dsa/longest-common-substring-dp-29/
def longest_common_substring(s1, s2):
    m = len(s1)
    n = len(s2)

    # Create a 1D array to store the previous row's results
    prev = [0] * (n + 1)
    
    res = 0
    for i in range(1, m + 1):
      
        # Create a temporary array to store the current row
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1] + 1
                res = max(res, curr[j])
            else:
                curr[j] = 0
        
        # Move the current row's data to the previous row
        prev = curr
    
    return res

#NOTE: (Ahamyk) fine tuned similarity algorythm
def score_title_similarity(submitted_title: str, scanned_title: str, scan_result_type: ScanResultType) -> float:

    score = 0.0

    submitted_title = get_cleaned_words(submitted_title)
    scanned_title = get_cleaned_words(scanned_title)

    score += longest_common_substring(submitted_title, scanned_title)

    match scan_result_type:

        case ScanResultType.ALBUM:
            if submitted_title == scanned_title:
                score += 5 

        case ScanResultType.TRACK:
            if submitted_title == scanned_title:
                score += 12 
            else:
                if submitted_title in scanned_title:
                    score += 10 

                ratio = SequenceMatcher(None, submitted_title, scanned_title).ratio()
                score += ratio * 3

    return score 


class FindSongResult(NamedTuple):
    source_tracks: list[SourceTrack]
    albums: list[ScanResult]

def find_song(game_and_track_pairs: list[GameAndTrackPair]) -> FindSongResult:
    threads = []
    scan_result_dict_albums: dict[str, list[ScanResult]] = {} 
    for pair in game_and_track_pairs:
        if pair.game_name not in scan_result_dict_albums: 
            scan_result_dict_albums[pair.game_name] = []
            thread = threading.Thread(target=search_sites_for_albums, args=(pair.game_name, scan_result_dict_albums[pair.game_name]))
            thread.start()
            threads.append(thread)
    for t in threads:
        t.join()

    class ScoredAlbum(NamedTuple):
        score: float
        scan_result_album: ScanResult

    scan_result_dict_albums_scored: dict[str, list[ScoredAlbum]] = {} 
    for game_name, scan_result_list in scan_result_dict_albums.items():
        scan_result_dict_albums_scored[game_name] = []
        for scan_result_album in scan_result_list:
            total_score = score_title_similarity(game_name, scan_result_album.title, ScanResultType.ALBUM)
            print(f"ALBUM SCORE {total_score}: {scan_result_album.title}")
            if total_score > 0.0:
                scan_result_dict_albums_scored[game_name].append(ScoredAlbum(total_score, scan_result_album))
    
    scan_result_dict_albums_sorted: dict[str, list[ScoredAlbum]] = {}
    for game_name, scored_albums in scan_result_dict_albums_scored.items():
        sorted_scored = sorted(scored_albums, key=lambda s: s.score , reverse=True)
        scan_result_dict_albums_sorted[game_name] = sorted_scored[:10]

    scanned_album_dict: dict[str, ScoredAlbum] = {}
    for game_name, scored_albums in scan_result_dict_albums_sorted.items():
        for scored_album in scored_albums:
            is_valid = True 
            if scored_album.scan_result_album.url in scanned_album_dict:
                is_valid = scanned_album_dict[scored_album.scan_result_album.url].score < scored_album.score
            if is_valid:
                scanned_album_dict[scored_album.scan_result_album.url] = scored_album

    scored_albums = list(sorted(scanned_album_dict.values(), key=lambda s: s.score, reverse=True))
    # print(f'SCORED ALBUMS: {scored_albums}')

    output_source_tracks: list[SourceTrack] = []
    threads = []
    for scored_album in scored_albums: 
        thread = threading.Thread(target=get_tracks_in_album, args=(scored_album.scan_result_album, output_source_tracks))
        thread.start()
        threads.append(thread)
    for t in threads:
        t.join()

    class ScoredSourceTrack(NamedTuple):
        score: float
        source_track: SourceTrack

    scored_sources: list[ScoredSourceTrack] = []
    for pair in game_and_track_pairs:
        for source_track in output_source_tracks:
            score_track = score_title_similarity(pair.track_name, source_track.track_title, ScanResultType.TRACK)
            if score_track > 0:
                print(f'TRACK SCORE: {score_track}: {source_track.track_title}')
                score_album = score_title_similarity(pair.game_name, source_track.album_title, ScanResultType.ALBUM)
                total_score = score_track + score_album
                is_valid = True
                to_remove = None
                for s in scored_sources:
                    if s.source_track == source_track and total_score > s.score:
                        to_remove = s
                        break
                if to_remove:
                    scored_sources.remove(to_remove)
                if is_valid:
                    scored_sources.append(ScoredSourceTrack(total_score, source_track))

    scored_sources = sorted(scored_sources, key=lambda scored_source: scored_source.score, reverse=True)
    # scored_sources = scored_sources[:3]
    scored_sources = scored_sources[:20]

    for s in scored_sources:
        print(s)

    # full_url = urljoin(scan_result_album.url, scan_result_track.url)
    # cached_track_results[(game_name, track_name)] = track_list

    if len(game_and_track_pairs):
        print(f'TOTAL ALBUM COUNT: {len(scan_result_dict_albums.keys())}')
        print(f'TOTAL TRACK COUNT: {len(output_source_tracks)}')

    source_tracks: list[SourceTrack] = []
    for scored_track in scored_sources:
        source_tracks.append(scored_track.source_track)

    albums: list[ScanResult] = []
    for scored_album in scored_albums:
        albums.append(scored_album.scan_result_album)

    return FindSongResult(source_tracks, albums)



def search_rip_sources(submissionText: str):
    # global hcs_index
    # cached_album_results = dict()
    # cached_track_results = dict()
    # if (len(hcs_index) == 0):
    #     hcs_index = get_index()

    title = get_raw_rip_title(submissionText)
    if title is None: 
        title = submissionText

    print(f'TITLE: {title}')

    description = get_rip_description(submissionText)
    desc_dict, msgs = desc_to_dict(description, 1)
    track_string = get_music_from_desc(desc_dict)

    print(f'TRACK STRING: {track_string}')

    game_and_track_pairs = parseTitle(title, ' - ', track_string)

    print(f'PAIRS: {game_and_track_pairs}')

    result = ""

    find_song_result = find_song(game_and_track_pairs)
    for source_track in find_song_result.source_tracks:
        result += f"\n**[{source_track.track_title}]({source_track.track_url})** - [{source_track.album_title}]({source_track.album_url}) ({VGM_SITE_INFOS[source_track.vgm_site].name})"

    for scan_result_album in find_song_result.albums: 
        exists_in_tracks = False
        for source_track in find_song_result.source_tracks:
            if scan_result_album.url == source_track.album_url:
                exists_in_tracks = True
                break
        if not exists_in_tracks:
            result += f"\nAlbum: [{scan_result_album.title}](<{scan_result_album.url}>) ({VGM_SITE_INFOS[scan_result_album.vgm_site].name})"

    YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query="

    youtube_title_url = YOUTUBE_SEARCH_URL + quote_plus(title)
    result += f"\n\nYouTube Search: [{title}]({youtube_title_url})"

    joke = get_rip_joke(submissionText)
    print(f'\nJOKE: {joke}')
    if len(joke):
        jokes = re.split(r'[\,\.]', joke)

        for(joke) in jokes:
            youtube_title_url = YOUTUBE_SEARCH_URL + quote_plus(joke)
            result += f"\nYouTube Search: [{joke}](<{youtube_title_url}>)"

        # joke_name_pairs: list[GameAndTrackPair] = []
        # dividers = [' - ', ' from ']
        # for divider in dividers:
        #     for joke in jokes:
        #         game_and_track_pairs = parseTitle(joke, divider, "")
        #         for pair in game_and_track_pairs:
        #             if pair not in joke_name_pairs:
        #                 joke_name_pairs.append(pair)

        # find_song_result = find_song(joke_name_pairs)
        #TODO: (Ahmayk) display this somehow in a way that makes sense?
        # (or cut it lol, youtube might be more useful)

    else:
        print("Joke not found.")
        pass

    print("All done!")

    return result