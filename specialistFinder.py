import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from bot_secrets import SPECIALISTS_SPREADSHEET_ID 
from hq_strings import * 
from simpleQoC.metadata import desc_to_dict, get_music_from_desc
from hq_core import parse_emojis_in_string
from discord import Guild

from typing import NamedTuple

def get_sheet_data(sheet_name: str, row_start: int, last_column: str, creds: Credentials) -> list[list[str]]:
    sheet_output = {} 
    try:
        service = build("sheets", "v4", credentials=creds)
        sheet = service.spreadsheets()
        sheet_output = (
            sheet.get(
                spreadsheetId=SPECIALISTS_SPREADSHEET_ID,
                ranges=f'{sheet_name}!A{row_start}:{last_column}',
                fields='sheets.data.rowData.values.formattedValue',
                includeGridData=True
            ).execute()
        )
    except HttpError as err:
        print(err)

    formatted_values = {} 
    try:
        formatted_values = sheet_output['sheets'][0]['data'][0]['rowData']
    except (KeyError, IndexError):
        pass

    result: list[list[str]] = []
    for row in formatted_values:
        if 'values' in row:
            cells = []
            for cell in row['values']:
                if 'formattedValue' in cell:
                    cells.append(cell['formattedValue'])
                else:
                    cells.append("")
            result.append(cells)

    return result

class SpecialistEntry(NamedTuple):
    source_string: str
    specialists: str 
    game_names: list[str] 
    playlist_ids: list[str]

def get_specialist_data() -> list[SpecialistEntry]:

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = None

    # NOTE: (Ahmayk) login required in web browser to access google sheets doc
    # then token.json is created and saves login info
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())


    game_sheet_data = get_sheet_data("Game Strict Rules", 3, 'D', creds)
    specialist_entries: list[SpecialistEntry] = []
    for row in game_sheet_data:

        if len(row):
            source_string = row[0] 

            specialists = "" 
            if len(source_string) > 1:
                specialists = row[1]

            game_names: list[str] = [] 
            if len(row) > 2:
                game_names = row[2].split(",")

            playlist_ids = []
            if len(row) > 3:
                for link in row[3].splitlines():
                    playlist_id = extract_playlist_id(link)
                    if len(playlist_id):
                        playlist_ids.append(playlist_id)
                    else:
                        ##TODO: (Ahmayk) report invalid data
                        playlist_ids.append("???")
                        pass

            specialist_entries.append(SpecialistEntry(source_string, specialists, game_names, playlist_ids))
            
    return specialist_entries 

def format_specialist_line(type_match: str, specialist_entry: SpecialistEntry, stop_emoji: str) -> str:
    return f'\n{stop_emoji} {specialist_entry.source_string} [{type_match}]: **{specialist_entry.specialists}**'

def search_specialists(submissionText: str, specialist_entires: list[SpecialistEntry], guild: Guild) -> str:
    result = ""

    title = get_raw_rip_title(submissionText)
    if title is None: 
        title = submissionText

    description = get_rip_description(submissionText)
    desc_dict, msgs = desc_to_dict(description, 1)
    track_string = get_music_from_desc(desc_dict)
    playlist_id = extract_playlist_id(description)
    game_and_track_pairs = parseTitle(title, ' - ', track_string)

    stop_emoji = '🛑'
    for e in guild.emojis:
        if e.name.lower() == "stop":
            stop_emoji = str(e)

    for specialist_entry in specialist_entires:

        is_match = False

        if playlist_id in specialist_entry.playlist_ids:
            is_match = True
            result += format_specialist_line('Playlist match', specialist_entry, stop_emoji)
        
        if not is_match:
            for specialist_game_name in specialist_entry.game_names:
                if len(specialist_game_name):
                    for game_and_track_pair in game_and_track_pairs:
                        if specialist_game_name in game_and_track_pair.game_name:
                            is_match = True
                            result += format_specialist_line('Game Title match', specialist_entry, stop_emoji)

    result = result.strip()

    return result