import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from bot_secrets import SPECIALISTS_SPREADSHEET_ID 
from hq_strings import extract_playlist_id 

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

def search_specialists(text: str) -> str:

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

    class SpecialistEntry(NamedTuple):
        source_string: str
        specialists: str 
        game_names: list[str] 
        playlist_ids: list[str]

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
            
    result = str(specialist_entries) 

    return result 