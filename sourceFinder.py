import requests
import re
import threading
import string
import numpy 
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

class VGMSiteInfo(NamedTuple):
    name: str
    pre_href: str
    search_url_pathname: str

VGM_SITE_INFOS: dict[VGM_SITE, VGMSiteInfo] = {} 
VGM_SITE_INFOS[VGM_SITE.ZOPHAR] = VGMSiteInfo("Zophar", 'https://www.zophar.net', '/music/search?search=')
VGM_SITE_INFOS[VGM_SITE.KHINSIDER] = VGMSiteInfo("KHInsider", 'https://downloads.khinsider.com', '/search?search=')
VGM_SITE_INFOS[VGM_SITE.VGMRIPS] = VGMSiteInfo("VGMRips", 'https://vgmrips.net', '/packs/search?q=')

class ScanResultType(Enum):
    ALBUM = auto() 
    TRACK = auto() 

class ScanResult(NamedTuple):
    vgm_site: VGM_SITE
    type: ScanResultType 
    title: str
    url: str
    platform: str

def scan_vgm_site(url: str, vgm_site: VGM_SITE, scan_result_type: ScanResultType, output_scan_results: List[ScanResult]):

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

        skip_link_parsing = False

        #NOTE: (Ahmayk) VGMRips redirects you to a page sometimes
        if (response.url.startswith("https://vgmrips.net/packs/pack") and response.url != url):
            page_element = soup.find('title')
            if page_element is not None and page_element.string is not None:
                title = page_element.string[0:page_element.string.index(" vgm music • VGMRips")]
                found_url = response.url
                skip_link_parsing = True
                output_scan_results.append(ScanResult(vgm_site, scan_result_type, title, found_url, ""))

        if not skip_link_parsing:
            all_link_tags = soup.find_all('a', href=True)

            #NOTE: (Ahmayk) This code will need to be updated if the layout of a website ever changes
            track_platform = ""
            if scan_result_type == ScanResultType.TRACK:
                match vgm_site:
                    case VGM_SITE.ZOPHAR:
                        label_tag = soup.find(string="Console:")
                        if label_tag:
                            platform_tag = label_tag.find_next()
                            if platform_tag:
                                track_platform = platform_tag.get_text(strip=True)
                        pass
                    case VGM_SITE.VGMRIPS:
                        label_tag = soup.find(string=["Systems:", "System:"])
                        if label_tag:
                            platform_tag = label_tag.find_next(class_="badge")
                            if platform_tag:
                                track_platform = platform_tag.get_text(strip=True)
                        pass
                    case VGM_SITE.KHINSIDER:
                        title_tag = soup.h2
                        if title_tag:
                            desc_tag = title_tag.find_next('p', attrs={"align": "left"})
                            if desc_tag:
                                desc = desc_tag.get_text()
                                lines = desc.splitlines()
                                if len(lines) > 1:
                                    track_platform = lines[1].replace("Platforms: ", "").strip()

            #NOTE: (Ahmayk) output dummy result so that we export the platform in the event there are no tracks found
            # (This came up with zophar)
            dummy_result = ScanResult(vgm_site, scan_result_type, "", "", track_platform)
            output_scan_results.append(dummy_result)

            for link_tag in all_link_tags:
                assert isinstance(link_tag, Tag)
                href = link_tag['href']
                assert isinstance(href, str)

                is_valid = True
                found_url = ""
                title = "" 

                #NOTE: (Ahmayk) This code will need to be updated if the layout of a website ever changes
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

                            if is_valid and href.endswith(".mp3"):
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
                                        #duplicates are filtered out later
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
                    result = ScanResult(vgm_site, scan_result_type, title, found_url, track_platform)
                    output_scan_results.append(result)


def search_sites_for_albums(game_name: str, output_scan_result_list: List[ScanResult]):
    # print(f"SEARCHING SITES FOR {game_name}")
    threads: List[threading.Thread] = []
    result_lists: List[List[ScanResult]] = []
    game_name = quote(game_name) 
    for i, vgm_site in enumerate(VGM_SITE):
        result_lists.insert(i, [])
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
    track_platform: str

def get_tracks_in_album(scan_result_album: ScanResult, output_source_tracks: List[SourceTrack]):
    scan_result_tracks: List[ScanResult] = []
    scan_vgm_site(scan_result_album.url, scan_result_album.vgm_site, ScanResultType.TRACK, scan_result_tracks)
    # print(f"Returned tracks from scan for {scan_result_album.url}: {len(scan_result_tracks)}")
    for scan_result_track in scan_result_tracks:
        assert scan_result_album.vgm_site == scan_result_track.vgm_site
        track_url = urljoin(scan_result_album.url, scan_result_track.url)
        source_track = SourceTrack(
            scan_result_album.vgm_site,
            scan_result_album.title,
            scan_result_album.url,
            scan_result_track.title,
            track_url ,
            scan_result_track.platform,
        )
        output_source_tracks.append(source_track)
        # print(f'track name in album {scan_result_album.title}: {scan_result_track.title}')


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
            after_no_mixname = "" 
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
                if len(after_no_mixname):
                    pairs.append(GameAndTrackPair(before, after_no_mixname))
            if add_after_before:
                pairs.append(GameAndTrackPair(after, before))
                if len(before_no_mixname):
                    pairs.append(GameAndTrackPair(after, before_no_mixname))
                if len(after_no_mixname):
                    pairs.append(GameAndTrackPair(after_no_mixname, before))

    return pairs

def parseTitle(title: str, divider: str, track_name: str) -> list[GameAndTrackPair]:
    pairs = [] 
    if divider in title:
        pairs = _parse_title_internal(title, divider, track_name)
        #NOTE: (Ahmayk) if trying to match to a track_name doesn't turn up anything, repeat without matching to track_name
        if not len(pairs) and len(track_name):
            pairs = _parse_title_internal(title, divider, "")
    else:
        pairs.append(GameAndTrackPair(title, title))
    return pairs


def get_cleaned_words(title: str, scan_result_type: ScanResultType) -> str:
    title = title.lower()
    title = title.translate(str.maketrans('', '', string.punctuation))
    #NOTE: (Ahmayk) remove common keywords, matching to them dilutes search
    if scan_result_type == ScanResultType.ALBUM:
        title = title.replace(" ost", " ")
        title = title.replace(" official soundtrack ", " ")
        title = title.replace(" unofficial soundtrack", " ")
        title = title.replace(" soundtrack", " ")
    if scan_result_type == ScanResultType.TRACK:
        title = title.replace(" version", " ")
        title = title.replace(" mix", " ")
    title = title.strip()
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

    submitted_title = get_cleaned_words(submitted_title, scan_result_type)
    scanned_title = get_cleaned_words(scanned_title, scan_result_type)

    # print(f'SUB: {submitted_title} SCAN: {scanned_title}')
    longest_common_substring_ratio = 0 
    if len(submitted_title):
        longest_common_substring_ratio = longest_common_substring(submitted_title, scanned_title) / len(submitted_title)
    # print(f"LCS: {longest_common_substring_ratio}")

    match scan_result_type:

        case ScanResultType.ALBUM:
            # print(f'SUB: {submitted_title} SCAN: {scanned_title}')
            # print(f'SUB: {len(submitted_title)} SCAN: {len(scanned_title)}')
            is_exact_match = submitted_title == scanned_title
            ratio_rattcliff = SequenceMatcher(None, submitted_title, scanned_title).ratio()

            score = (
                (0.5 * longest_common_substring_ratio)
                + (0.25 * is_exact_match)
                + (0.25 * ratio_rattcliff)
            )

        case ScanResultType.TRACK:

            if submitted_title == scanned_title:
                # print("Exact match!")
                score = 1.0
            else:
                is_partial_match = 0.0 
                if submitted_title in scanned_title:
                    # print(f"Included! {submitted_title} -> {scanned_title}")
                    is_partial_match = 1.0 

                ratio_rattcliff = SequenceMatcher(None, submitted_title, scanned_title).ratio()
                # print(f"ratio: {ratio_rattcliff}")

                score = (
                    (0.5 * longest_common_substring_ratio)
                    + (0.25 * ratio_rattcliff)
                    + (0.25 * is_partial_match)
                )

                #NOTE: (Ahmayk) introduces harsher cutoff, makes lower scores lower than higher scores
                score = score * score * score

    return score


class FindSongResult(NamedTuple):
    source_tracks: list[SourceTrack]
    found_exact_match: bool

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

    scored_album_dict: dict[str, ScoredAlbum] = {}
    for game_name, scan_result_list in scan_result_dict_albums.items():
        for scan_result_album in scan_result_list:
            if len(scan_result_album.title):
                score = score_title_similarity(game_name, scan_result_album.title, ScanResultType.ALBUM)
                # print(f"ALBUM URL {scan_result_album.url}")
                if scan_result_album.url not in scored_album_dict or scored_album_dict[scan_result_album.url].score < score:
                    scored_album_dict[scan_result_album.url] = ScoredAlbum(score, scan_result_album)
                    # print(f'SCORED ALBUM {game_name} {score} - {scan_result_album.title}')

    scored_albums = list(sorted(scored_album_dict.values(), key=lambda s: s.score, reverse=True))
    scored_albums = scored_albums[:10]

    scores: list[float] = []
    for scored_album in scored_albums:
        scores.append(scored_album.score)

    found_album_exact_match = 1 in scores

    album_cutoff = 0.0
    if len(scores):
        album_cutoff = numpy.minimum(numpy.max(scores), numpy.mean(scores))

    # print(f"ALBUM CUTOFF: {album_cutoff}")
    albums_to_remove = []
    for album in scored_albums:
        if album.score <= (album_cutoff - 0.0001):
            albums_to_remove.append(album)
    for album in albums_to_remove:
        scored_albums.remove(album)

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

    scored_sources_dict: dict[SourceTrack, ScoredSourceTrack] = {} 
    for pair in game_and_track_pairs:
        for source_track in output_source_tracks:
            if len(source_track.track_title):
                score_track = score_title_similarity(pair.track_name, source_track.track_title, ScanResultType.TRACK)
                # print(f'TRACK SCORE: {score_track}: {source_track.track_title}')
                if source_track not in scored_sources_dict or score_track > scored_sources_dict[source_track].score:
                    scored_sources_dict[source_track] = ScoredSourceTrack(score_track, source_track)

    scored_sources = list(sorted(scored_sources_dict.values(), key=lambda scored_source: scored_source.score, reverse=True))
    scored_sources = scored_sources[:20]
    # print(scored_sources)

    scores = []
    for scored_track in scored_sources:
        scores.append(scored_track.score)

    found_track_exact_match = 1 in scores

    #NOTE: (Ahmayk) useful for showing statistics on the score
    #Used this to experiemnt with the math to make the scoring algorythm
    # if len(scores):
    #     scores = sorted(scores, reverse=True)
    #     album_standard_deviation = numpy.std(scores)
    #     album_mean = numpy.mean(scores)
    #     album_min = numpy.min(scores)
    #     album_max = numpy.max(scores)
    #     q1 = numpy.percentile(scores, 25) 
    #     q3 = numpy.percentile(scores, 75) 
    #     iqr = q3 - q1 
    #     min_zscore = (album_min - album_mean) / album_standard_deviation
    #     max_zscore = (album_max - album_mean) / album_standard_deviation
    #     print(scores)

    #     zscores = []
    #     for score in scores:
    #         zscores.append((score - album_mean) / album_standard_deviation)
    #     print(zscores)

    #     print(f'std: {album_standard_deviation}')
    #     print(f'mean: {album_mean}')
    #     print(f'q1: {q1}')
    #     print(f'q3: {q3}')
    #     print(f'iqr: {iqr}')
    #     print(f'min: {album_min}')
    #     print(f'max: {album_max}')
    #     print(f'min zscore: {min_zscore}')
    #     print(f'max zscore: {max_zscore}')

    track_cutoff = 0 
    if len(scores):
        track_mean = numpy.mean(scores) 
        standard_deviation = numpy.maximum(0.0, numpy.std(scores))
        max_score = numpy.max(scores)
        track_cutoff = numpy.minimum(max_score, track_mean + standard_deviation)
    # print(f"TRACK CUTOFF: {track_cutoff}")

    max_things_output = 3

    tracks_to_remove: list[ScoredSourceTrack] = []
    for track in scored_sources:
        if track.score <= (track_cutoff - 0.0001):
            tracks_to_remove.append(track)
    for track in tracks_to_remove:
        scored_sources.remove(track)
    scored_sources = scored_sources[:max_things_output]

    # for s in scored_sources:
    #     print(s)

    # full_url = urljoin(scan_result_album.url, scan_result_track.url)
    # cached_track_results[(game_name, track_name)] = track_list

    # if len(game_and_track_pairs):
    #     print(f'TOTAL ALBUM COUNT: {len(scan_result_dict_albums.keys())}')
    #     print(f'TOTAL TRACK COUNT: {len(output_source_tracks)}')

    source_tracks: list[SourceTrack] = []
    for scored_track in scored_sources:
        # print(scored_track)
        source_tracks.append(scored_track.source_track)

    found_exact_match = found_album_exact_match and found_track_exact_match

    return FindSongResult(source_tracks, found_exact_match)



def search_rip_sources(submissionText: str):

    title = get_raw_rip_title(submissionText)
    if title is None: 
        title = submissionText

    # print(f'TITLE: {title}')

    description = get_rip_description(submissionText)
    desc_dict, msgs = desc_to_dict(description, 1)
    track_string = get_music_from_desc(desc_dict)

    # print(f'TRACK STRING: {track_string}')

    game_and_track_pairs = parseTitle(title, ' - ', track_string)

    # print(f'PAIRS: {game_and_track_pairs}')

    result = ""

    skip_search = False
    for pair in game_and_track_pairs:
        if pair.game_name == "Undertale" or pair.game_name == "Deltarune":
            skip_search = True
            break

    found_exact_match = False
    if not skip_search:
        find_song_result = find_song(game_and_track_pairs)
        found_exact_match = find_song_result.found_exact_match

        display_platforms = False
        test_platform = ""
        for source_track in find_song_result.source_tracks: 
            if len(source_track.track_platform):
                test_platform = source_track.track_platform
        for source_track in find_song_result.source_tracks: 
            if len(source_track.track_platform) and test_platform != source_track.track_platform:
                display_platforms = True
                break

        if len(find_song_result.source_tracks):
            for source_track in find_song_result.source_tracks:
                album_title = source_track.album_title
                if display_platforms and len(source_track.track_platform):
                    album_title += f" ({source_track.track_platform})"
                result += f"\n- **[{source_track.track_title}]({source_track.track_url})** - [{album_title}]({source_track.album_url})"
                result += f" [{VGM_SITE_INFOS[source_track.vgm_site].name}]"


    YOUTUBE_SEARCH_URL = "https://www.youtube.com/results?search_query="

    if not found_exact_match:
        youtube_title_url = YOUTUBE_SEARCH_URL + quote_plus(title)
        result += f"\nYouTube Search: [{title}]({youtube_title_url})"

    joke = get_rip_joke(submissionText)
    #NOTE: (Ahmayk) only parse this if the joke line is short. otherwise it clogs up chat
    if len(joke) < 50:
        #NOTE: (Ahmayk) removes formatted links
        joke = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', joke)
        #NOTE: (Ahmayk) removes regular links 
        joke = re.sub(r'<?https?://\S+|www\.\S+>?', "", joke)
        jokes = re.split(r'[\,\.\;]', joke)
        joke_links = []
        for(joke) in jokes:
            youtube_title_url = YOUTUBE_SEARCH_URL + quote_plus(joke)
            joke = joke.strip()
            if len(joke):
                joke_links.append(f"[{joke}](<{youtube_title_url}>)")
        if len(joke_links):
            joke_string = ", ".join(joke_links)
            result += f"\nYouTube Search: {joke_string}"

    return result