import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from bot_secrets import SPECIALISTS_SPREADSHEET_ID 
from hq_sheets import * 
from hq_strings import * 
from simpleQoC.metadata import desc_to_dict, get_music_from_desc
from discord import Guild

from typing import NamedTuple
from datetime import datetime, timezone

class SpecialistEntry(NamedTuple):
    specialists: str 
    notes: str
    game_title: str
    alternate_game_titles: list[str] 
    excluded_tracks: list[str]
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

def get_raw_sheet_data(sheet_name: str, row_start: int, last_column: str, credentials: Credentials) -> list[list[str]]: 
    result: list[list[str]] = []

    try:
        service = build("sheets", "v4", credentials=credentials)
        output = (
            service.spreadsheets()
            .get(
                spreadsheetId=SPECIALISTS_SPREADSHEET_ID,
                ranges=f"{sheet_name}!A{row_start}:{last_column}",
                fields="sheets.data.rowData.values.formattedValue",
                includeGridData=True,
            )
            .execute()
        )
        rows = output["sheets"][0]["data"][0]["rowData"]
    except (HttpError, KeyError, IndexError):
        return []

    print(f"ROWS: {rows}")

    for row in rows:
        cells = []
        for cell in row.get("values", []):
            cells.append(cell.get("formattedValue", ""))
        result.append(cells)

    return result


CREDENTIALS = None
SHEET_LAST_UPDATED: datetime = datetime.now(timezone.utc)
QOC_SHEET_DATA: QoCSheetData = QoCSheetData([], []) 
def get_qoc_sheet_data() -> QoCSheetData: 

    global CREDENTIALS
    global QOC_SHEET_DATA 

    result = QOC_SHEET_DATA 

    if not CREDENTIALS:

        # NOTE: (Ahmayk) login required in web browser to access google sheets doc
        # then token.json is created and saves login info
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]

        if os.path.exists("token.json"):
            CREDENTIALS = Credentials.from_authorized_user_file("token.json", scopes)

        if not CREDENTIALS or not CREDENTIALS.valid:
            if CREDENTIALS and CREDENTIALS.expired and CREDENTIALS.refresh_token:
                CREDENTIALS.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", scopes)
                CREDENTIALS = flow.run_local_server(port=0)

            with open("token.json", "w") as token:
                token.write(CREDENTIALS.to_json())

    if CREDENTIALS:

        call_sheet_api = True 

        try:
            service_drive = build("drive", "v3", credentials=CREDENTIALS)
            file = (
                service_drive.files()
                .get(fileId=SPECIALISTS_SPREADSHEET_ID, fields="id, name, modifiedTime")
                .execute()
            )
            modified_time = datetime.fromisoformat(file["modifiedTime"].replace("Z", "+00:00"))
            global SHEET_LAST_UPDATED 
            if modified_time == SHEET_LAST_UPDATED:
                call_sheet_api = False 
            SHEET_LAST_UPDATED = modified_time

        except HttpError as error:
            print(f"An error occurred: {error}")

        if call_sheet_api or not QOC_SHEET_DATA:

            specialist_entries: list[SpecialistEntry] = []

            game_sheet_data = get_raw_sheet_data("Game Strict Rules", 3, 'E', CREDENTIALS)
            for row in game_sheet_data:
                if len(row) > 1:
                    game_title = row[0] 
                    specialists = row[1]
                    alternate_game_titles: list[str] = [] 
                    if len(row) > 2:
                        alternate_game_titles = row[2].split("/")
                    excluded_tracks: list[str] = [] 
                    if len(row) > 3:
                        excluded_tracks = row[2].split("/")
                    notes = "" 
                    if len(row) > 4:
                        notes = row[4]
                    specialist_entries.append(SpecialistEntry(specialists, notes, game_title, alternate_game_titles, excluded_tracks, "", []))

            source_sheet_data = get_raw_sheet_data("Source Strict Rules", 3, 'D', CREDENTIALS)
            for row in source_sheet_data:
                if len(row) > 1:
                    source_string = row[0]
                    specialists = row[1]
                    alternate_source_names = []
                    if len(row) > 2:
                        alternate_source_names = row[2].split("/") 
                    notes = "" 
                    if len(row) > 3:
                        notes = row[3]
                    specialist_entries.append(SpecialistEntry(specialists, notes, "", [], [], source_string, alternate_source_names))

            source_exclusions: list[SourceExclusion] = []
            source_exclusion_sheet_data = get_raw_sheet_data("Source Exclusions", 3, 'F', CREDENTIALS)
            for row in source_exclusion_sheet_data:
                if len(row) > 1:
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

            result = QoCSheetData(specialist_entries, source_exclusions)
            QOC_SHEET_DATA = result

    return result


def search_specialists(submissionText: str, guild: Guild) -> str:
    result = ""

    title = get_raw_rip_title(submissionText)
    if title is None: 
        title = submissionText

    description = get_rip_description(submissionText)
    desc_dict, msgs = desc_to_dict(description, 1)
    track_string = get_music_from_desc(desc_dict)
    game_and_track_pairs = parseTitle(title, ' - ', track_string)

    joke_input = submissionText 
    chunks = submissionText.split('```')
    if len(chunks) >= 2:
        joke_input = chunks[2]
    joke_input = joke_input.lower()

    stop_emoji = '🛑'
    for e in guild.emojis:
        if e.name.lower() == "stop":
            stop_emoji = str(e)

    print(game_and_track_pairs)

    qoc_sheet_data = get_qoc_sheet_data()

    for specialist_entry in qoc_sheet_data.specialist_entries:

        for game_and_track_pair in game_and_track_pairs:
            if len(specialist_entry.game_title) and specialist_entry.game_title == game_and_track_pair.game_name:
                result += f'\n{stop_emoji} {specialist_entry.game_title}: **{specialist_entry.specialists}**'
                break
            for alternate_title in specialist_entry.alternate_game_titles:
                if len(alternate_title) and alternate_title.lower() in game_and_track_pair.game_name.lower():
                    result += f'\n{stop_emoji} {specialist_entry.game_title}: **{specialist_entry.specialists}** [{alternate_title}]'
                    break
        
        if len(specialist_entry.source) and specialist_entry.source.lower() in joke_input:
            result += f'\n{stop_emoji} {specialist_entry.source}: **{specialist_entry.specialists}**'
        for alternate_source_name in specialist_entry.alternate_source_names:
            if len(alternate_source_name) and alternate_source_name.lower() in joke_input:
                result += f'\n{stop_emoji} {specialist_entry.source}: **{specialist_entry.specialists}** [{alternate_source_name}]'
                break

    result = result.strip()

    return result