import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from bot_secrets import SPECIALISTS_SPREADSHEET_ID 

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

    sheet_output = {} 
    try:
        sheet_name = "Game Strict Rules" 
        service = build("sheets", "v4", credentials=creds)
        sheet = service.spreadsheets()

        col_start = 'A'
        row_start = 3
        col_end = 'D'
        row_end = row_start 

        sheet_data = (
            sheet.get(
                spreadsheetId=SPECIALISTS_SPREADSHEET_ID,
                ranges=f'{sheet_name}',
                fields='sheets.properties.gridProperties',
                includeGridData=True
            ).execute()
        )
        try:
            row_end = sheet_data['sheets'][0]['properties']['gridProperties']['rowCount']
        except (IndexError, KeyError):
            pass

        sheet_output = (
            sheet.get(
                spreadsheetId=SPECIALISTS_SPREADSHEET_ID,
                ranges=f'{sheet_name}!{col_start}{row_start}:{col_end}{row_end}',
                fields='sheets.data.rowData.values.formattedValue',
                includeGridData=True
            ).execute()
        )

    except HttpError as err:
        print(err)

    print(sheet_output)

    result = str(sheet_output.values())

    return result 