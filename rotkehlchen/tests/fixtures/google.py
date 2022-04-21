import logging
import os
import warnings as test_warnings
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import pytest
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from rotkehlchen.accounting.export.csv import CSV_INDEX_OFFSET
from rotkehlchen.logging import RotkehlchenLogsAdapter

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)

DRIVE_SCOPES = [
    'https://www.googleapis.com/auth/drive.metadata.readonly',
    'https://www.googleapis.com/auth/drive.file',
]
SHEETS_SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
]

VALUE_INPUT_OPTION = 'USER_ENTERED'


def _login(service_name: str, version: str, scopes: List[str], credentials_path: Path):
    """Create a specific google service driver and loginto it with credentials
    """
    credentials = (
        Credentials.from_service_account_file(credentials_path, scopes=scopes)
    )
    return build(service_name, version, credentials=credentials)


class GoogleService:
    """This is a GoogleService class to connect to google services via api tokens.

    To use you need to create a service API key
    (https://handsondataviz.org/google-sheets-api-key.html)
    and get the credentials json file and put it somewhere in the project directory.
    Make sure to enable both google drive and google sheets api!!!

    Then:
    1. Set an environment variable GOOGLE_CREDENTIALS_FILE pointing to the path
    to the local file
    2. Set an ennvironment variable GOOGLE_EMAIL pointing to your google email.
    This is only to give you write permissions to all created sheets so you
    can view/edit/delete.
    3. Use the google_service fixture
    4. Set the upload_csv_to_google fixture variable to true

    All created sheets would appear in https://drive.google.com/drive/shared-with-me

    TODO: We could even utilize this class in production to automatically manage
    google sheets for the user via rotki.

    Also useful info:
    https://robocorp.com/docs/development-guide/google-sheets/interacting-with-google-sheets
    """
    def __init__(self, credentials_path: Path) -> None:
        self.drive_service = _login('drive', 'v3', DRIVE_SCOPES, credentials_path)
        self.sheets_service = _login('sheets', 'v4', SHEETS_SCOPES, credentials_path)
        self.user_email = os.environ.get('GOOGLE_EMAIL', None)

    def clean_drive(self) -> None:
        """Lists all files for the service account in the drive and delete them"""
        page_token = None
        while True:
            response = self.drive_service.files().list(  # pylint: disable=no-member
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageToken=page_token,
            ).execute()
            for file in response.get('files', []):
                file_id = file.get('id')
                self.drive_service.files().delete(fileId=file_id).execute()  # noqa: E501 # pylint: disable=no-member
                log.info(f'Deleted drive file: {file.get("name")} with id {file_id}')

            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break

    def create_spreadsheet(self) -> str:
        body = {
            'properties': {
                'title': 'rotki test sheet',
            },
        }
        spreadsheet = self.sheets_service.spreadsheets().create(  # pylint: disable=no-member
            body=body,
            fields='spreadsheetId',
        ).execute()
        sheet_id = spreadsheet.get('spreadsheetId')

        if self.user_email is not None:  # Share it with the given email
            permissions = {
                'type': 'user',
                'role': 'writer',
                'emailAddress': self.user_email,
            }
            self.drive_service.permissions().create(fileId=sheet_id, body=permissions).execute()  # noqa: E501 # pylint: disable=no-member
        return sheet_id

    def add_rows(self, sheet_id: str, csv_data: List[Dict[str, Any]]) -> None:
        data = [{
            'range': 'A1:L1',
            'values': [list(csv_data[0].keys())],
        }]
        for idx, row in enumerate(csv_data):
            data.append({
                'range': f'A{idx + CSV_INDEX_OFFSET}:L{idx + CSV_INDEX_OFFSET}',
                'values': [list(row.values())],
            })

        body = {
            'valueInputOption': VALUE_INPUT_OPTION,
            'data': data,
        }
        self.sheets_service.spreadsheets().values().batchUpdate(  # noqa: E501 # pylint: disable=no-member
            spreadsheetId=sheet_id,
            body=body,
        ).execute()

    def get_cell_range(self, sheet_id: str, range_name: str) -> List[List[str]]:
        result = self.sheets_service.spreadsheets().values().get(  # pylint: disable=no-member
            spreadsheetId=sheet_id,
            range=range_name,
        ).execute()
        return result.get('values', [])

    def get_cell_ranges(self, sheet_id: str, range_names: List[str]) -> List[Dict[str, Any]]:
        result = self.sheets_service.spreadsheets().values().batchGet(  # pylint: disable=no-member
            spreadsheetId=sheet_id,
            ranges=range_names,
        ).execute()
        return result.get('valueRanges', [])


@pytest.fixture(scope='session', name='session_google_service')
def fixture_session_google_service() -> Optional[Generator[GoogleService, None, None]]:
    service = None
    credentials_file_path = os.environ.get('GOOGLE_CREDENTIALS_FILE', None)
    if credentials_file_path is None:
        test_warnings.warn(UserWarning(
            'Did not find GOOGLE_CREDENTIALS_FILE env variable. Skipping google sheets uploads in tests',  # noqa: E501
        ))
    else:
        path = Path(credentials_file_path)
        if path.is_file():
            service = GoogleService(credentials_path=path)
        else:
            test_warnings.warn(UserWarning(
                f'Given google credentials file: {credentials_file_path} does not exist. Skipping google uploads in tests',  # noqa: E501
            ))
            service = None

    yield service

    # At the end of the test session cleanup the drive. If you want to inspect
    # tests afterwards then comment this out
    if service is not None:
        service.clean_drive()


@pytest.fixture(name='upload_csv_to_google')
def fixture_upload_csv_to_google() -> bool:
    return False


@pytest.fixture(scope='function', name='google_service')
def fixture_google_service(session_google_service, upload_csv_to_google) -> Optional[GoogleService]:  # noqa: E501
    if session_google_service is None:
        return None

    if upload_csv_to_google is False:
        return None

    return session_google_service