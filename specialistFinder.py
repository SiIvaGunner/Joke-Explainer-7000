import os.path
import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from datetime import datetime, timezone, timedelta
import traceback
import pytz

from bot_secrets import SPECIALISTS_SPREADSHEET_ID 

# If modifying these scopes, delete the file token.json.
OOB_INDICATOR = "[OUT OF BOUND]"

def get_sheet_range(sheet_id, sheet_name, col_start, row_start, col_end, row_end):

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    try:
        service = build("sheets", "v4", credentials=creds)
        # Call the Sheets API
        sheet = service.spreadsheets()

        sheet_data = (
            sheet.get(
                spreadsheetId=sheet_id,
                ranges=f'{sheet_name}',
                fields='sheets.properties.gridProperties,sheets.merges',
                includeGridData=True
            ).execute()
        )

        try:
            max_rows = sheet_data.get('sheets', [])[0].get('properties', {'gridProperties': {'rowCount': 0}}).get('gridProperties', {'rowCount': 0}).get('rowCount', 0)
            if row_start > max_rows:
                # row_start is out of bound, should search in the next sheet
                return OOB_INDICATOR, []
            row_end = max(row_start, min(max_rows, row_end))
            merges = sheet_data.get('sheets', [])[0].get('merges', [])
        except IndexError as err:
            traceback.print_exc()
            return None, []

        result = (
            sheet.get(
                spreadsheetId=sheet_id,
                ranges=f'{sheet_name}!{col_start}{row_start}:{col_end}{row_end}',
                fields='sheets.data.rowData.values.formattedValue,sheets.data.rowData.values.userEnteredFormat.backgroundColor',
                includeGridData=True
            ).execute()
        )

        return result, merges
    
    except HttpError as err:
        print(err)
        return None, []


def search_specialists(text) -> str:
    result = ""

    batch_size = 200
    sheet_name = "Game Strict Rules" 
    row_start = 2
    
    while True:
        # print(f"fetching {sheet_name} {start_row}")
        # data = get_sheet_section(SPECIALISTS_SPREADSHEET_ID, sheet_name, start_row, start_row + batch_size - 1)
        sheet_output, merges = get_sheet_range(SPECIALISTS_SPREADSHEET_ID, sheet_name, 'A', row_start, 'C', row_start + batch_size - 1)
        if sheet_output is None:
            break
        elif sheet_output == OOB_INDICATOR:
            break

        print(sheet_output)

        # data = []
        # c_merges = []
        # for merge in merges:
        #     startRowIndex = merge.get('startRowIndex', 0)
        #     endRowIndex = merge.get('endRowIndex', 0)
        #     startColumnIndex = merge.get('startColumnIndex', 0)
        #     endColumnIndex = merge.get('endColumnIndex', 0)

        #     if startColumnIndex == 2 and endColumnIndex == 3:
        #         c_merges.extend(range(startRowIndex - (row_start-1), endRowIndex - (row_start-1)))

        # index = 0
        # for row in result.get('sheets', [])[0].get('data', [])[0].get('rowData', []):
        #     values = row.get('values', [])
        #     date = values[0].get('formattedValue', None)
        #     index = index + 1

        row_start += batch_size

    result = "I'm specialist of your dick"

    return sheet_output 