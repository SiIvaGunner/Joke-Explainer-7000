
from hq_sheets import *

from bot_secrets import SPECIALISTS_SPREADSHEET_ID
from hq_strings import * 
from hq_discord import * 
from hq_metadata import desc_to_dict, get_music_from_desc, remove_links
from discord import Guild

from datetime import datetime, timezone

class SpecialistEntry(NamedTuple):
    specialists: str 
    notes: str
    game_title: str
    alternate_game_titles: list[str] 
    composer_name: str
    alternate_composer_names: list[str] 
    source: str
    alternate_source_names: list[str]

class SourceExclusion(NamedTuple):
    track_title: str
    game_title: str
    skip_database_search: bool
    skip_youtube_search_link: bool
    no_results_message: str
    notes: str

class QoCSheetData(NamedTuple):
    specialist_entries: list[SpecialistEntry]
    source_exclusions: list[SourceExclusion]
    error_strings: list[str]

SHEET_LAST_UPDATED: datetime = datetime.now(timezone.utc)
QOC_SHEET_DATA: QoCSheetData = QoCSheetData([], [], []) 

class GetQoCSheetDataDesc(NamedTuple):
    bypass_cache: bool = False

async def get_qoc_sheet_data(desc: GetQoCSheetDataDesc, credentials: Credentials) -> QoCSheetData: 

    global QOC_SHEET_DATA 

    qoc_sheet_data = QOC_SHEET_DATA 
    error_strings: list[str] = []

    try:
        call_sheet_api =True 
        ##NOTE: (Ahmayk) We check to see the last modified time and only fetch new info if the sheet appears to be updated.
        # Google drive doesn't seem to update this very quickly, in testing it can take up to around 5 minutes,
        # but this is 100% worth it for the speed boost since changes to the sheet don't really need to take effect immediatley
        service_drive = build("drive", "v3", credentials=credentials)
        file = await run_blocking(
            service_drive.files()
            .get(fileId=SPECIALISTS_SPREADSHEET_ID, fields="id, name, modifiedTime")
            .execute
        )
        modified_time = datetime.fromisoformat(file["modifiedTime"].replace("Z", "+00:00"))
        global SHEET_LAST_UPDATED 
        # print(f"SHEET_LAST_UPDATED: {str(SHEET_LAST_UPDATED)} modified time: {str(modified_time)}")
        ##NOTE: (Ahmayk) If we do want changes to take effect immedatley (calling !specialist directly for instance) 
        #then bypassing the skip will guarentee that we are showing updated data 
        if modified_time == SHEET_LAST_UPDATED and not desc.bypass_cache:
            call_sheet_api = False 
        SHEET_LAST_UPDATED = modified_time
    except Exception as error:
        await log_exception("Failed to get modified time from google sheet", error, [], True)

    if call_sheet_api or not QOC_SHEET_DATA:

        specialist_entries: list[SpecialistEntry] = []
        ranges = [
            f"Game Strict Rules!A3:D",
            f"Composer Strict Rules!A3:D",
            f"Source Strict Rules!A3:D",
            f"Source Exclusions!A3:F",
        ]
        batch_values_get_result = await batch_get_values_from_sheet(SPECIALISTS_SPREADSHEET_ID, ranges, credentials)
        error_strings.extend(batch_values_get_result.error_strings)
        if len(batch_values_get_result.batches) != 4:
            error_strings.append(f"Unexpected response from google drive API: {len(batch_values_get_result.batches)} batches. (Expected 4)")

        if not len(error_strings):
            for row in batch_values_get_result.batches[0]:
                if len(row) > 1:
                    game_title = row[0] 
                    specialists = row[1]
                    alternate_game_titles: list[str] = [] 
                    if len(row) > 2:
                        names = row[2].split("/")
                        for name in names:
                            alternate_game_titles.append(name.strip())
                    notes = "" 
                    if len(row) > 3:
                        notes = row[3]
                    specialist_entries.append(SpecialistEntry(specialists, notes, game_title, alternate_game_titles, "", [], "", []))

        if not len(error_strings):
            for row in batch_values_get_result.batches[1]:
                if len(row) > 1:
                    composer_string = row[0]
                    specialists = row[1]
                    alternate_composer_names = []
                    if len(row) > 2:
                        names = row[2].split("/") 
                        for name in names:
                            alternate_composer_names.append(name.strip())
                    notes = "" 
                    if len(row) > 3:
                        notes = row[3]
                    specialist_entries.append(SpecialistEntry(specialists, notes, "", [], composer_string, alternate_composer_names, "", []))

        if not len(error_strings):
            for row in batch_values_get_result.batches[2]:
                if len(row) > 1:
                    source_string = row[0]
                    specialists = row[1]
                    alternate_source_names = []
                    if len(row) > 2:
                        names = row[2].split("/") 
                        for name in names:
                            alternate_source_names.append(name.strip())
                    notes = "" 
                    if len(row) > 3:
                        notes = row[3]
                    specialist_entries.append(SpecialistEntry(specialists, notes, "", [], "", [], source_string, alternate_source_names))

        source_exclusions: list[SourceExclusion] = []
        if not len(error_strings):
            for row in batch_values_get_result.batches[3]:
                if len(row) > 1 and len(row[1]):
                    track_title = row[0] 
                    game_title = row[1]
                    skip_database_search = False
                    if len(row) > 2:
                        skip_database_search = (row[2] == 'TRUE')
                    skip_youtube_search_link = False
                    if len(row) > 3:
                        skip_youtube_search_link = (row[3] == 'TRUE')
                    no_results_message = ""
                    if len(row) > 4:
                        no_results_message = row[4]
                    notes = ""
                    if len(row) > 5:
                        notes = row[5]
                    source_exclusions.append(SourceExclusion(track_title, game_title, skip_database_search, skip_youtube_search_link, no_results_message, notes))

        if not len(error_strings):
            qoc_sheet_data = QoCSheetData(specialist_entries, source_exclusions, error_strings)
            QOC_SHEET_DATA = qoc_sheet_data 

    return qoc_sheet_data 

def format_specialist(subject_string: str, alt_subject_string: str, specialist_entry: SpecialistEntry, emoji_string: str) -> str:
    result = f'\n{emoji_string} {subject_string}: **{specialist_entry.specialists}**'
    if len(alt_subject_string) and alt_subject_string not in subject_string:
        result += f' [{alt_subject_string}]'
    return result

def format_valid_alternate(alt_names: list[str], subject_input: str, subject_string: str, specialist_entry: SpecialistEntry, stop_emoji: str) -> str:
    result = "" 
    for alt_string in alt_names: 
        if len(alt_string) and alt_string.lower() in subject_input.lower():
            result = format_specialist(subject_string, alt_string, specialist_entry, stop_emoji)
            break
    return result

def search_specialists(submissionText: str, qoc_sheet_data: QoCSheetData, guild: Guild) -> str:
    result = ""

    title = get_raw_rip_title(submissionText)
    if title is None: 
        title = submissionText

    description = get_rip_description(submissionText)
    desc_dict, msgs = desc_to_dict(description, 1)
    track_string = get_music_from_desc(desc_dict)
    game_and_track_pairs = parseTitle(title, ' - ', track_string)

    sources_input = submissionText 
    chunks = submissionText.split('```')
    if len(chunks) >= 2:
        sources_input = chunks[2]
        sources_input += get_raw_rip_author(submissionText)

    sources_input = remove_links(sources_input)
    sources_input = sources_input.lower()

    composer_inputs = [] 
    composer_matches = ["Composer", "Composer", "Arrangement"]
    for match in composer_matches:
        if match in desc_dict:
            composer_inputs.append(desc_dict[match].lower())
    if not len(composer_inputs):
        composer_inputs.append(submissionText.lower())

    stop_emoji = '🛑'
    for e in guild.emojis:
        if e.name.lower() == "stop":
            stop_emoji = str(e)

    for specialist_entry in qoc_sheet_data.specialist_entries:

        for game_and_track_pair in game_and_track_pairs:
            if len(specialist_entry.game_title) and specialist_entry.game_title.lower() == game_and_track_pair.game_name.lower():
                result += format_specialist(specialist_entry.game_title, "", specialist_entry, stop_emoji)
                break

            alt_name_match_string = format_valid_alternate(specialist_entry.alternate_game_titles, game_and_track_pair.game_name, specialist_entry.game_title, specialist_entry, stop_emoji)
            if len(alt_name_match_string):
                result += alt_name_match_string 
                break
                
        if len(sources_input):
            if len(specialist_entry.source) and specialist_entry.source.lower() in sources_input:
                result += format_specialist(specialist_entry.source, "", specialist_entry, stop_emoji)
                continue

        for alternate_source_name in specialist_entry.alternate_source_names:
            if len(alternate_source_name) and alternate_source_name.lower() in sources_input:
                result += format_specialist(specialist_entry.source, alternate_source_name, specialist_entry, stop_emoji)
                break

        if len(composer_inputs):
            for composer_input in composer_inputs:
                if len(specialist_entry.composer_name) and specialist_entry.composer_name.lower() in composer_input:
                    result += format_specialist(specialist_entry.composer_name, "", specialist_entry, stop_emoji)
                    break

                alt_name_match_string = format_valid_alternate(specialist_entry.alternate_composer_names, composer_input, specialist_entry.composer_name, specialist_entry, stop_emoji)
                if len(alt_name_match_string):
                    result += alt_name_match_string 
                    break

    result = result.strip()

    return result