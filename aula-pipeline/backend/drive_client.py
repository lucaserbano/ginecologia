from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from settings import GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_TOKEN_PATH

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveAuthError(RuntimeError):
    pass


def get_credentials(interactive: bool = False) -> Credentials:
    token_path = Path(GOOGLE_OAUTH_TOKEN_PATH)
    creds: Optional[Credentials] = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_credentials(creds, token_path)

    if creds and creds.valid:
        return creds

    if not interactive:
        raise DriveAuthError(
            "Credenciais OAuth ausentes/expiradas. Rode /api/drive/auth-start para autorizar."
        )

    client_secret = Path(GOOGLE_OAUTH_CLIENT_SECRET)
    if not client_secret.exists():
        raise DriveAuthError(f"OAuth client JSON não encontrado: {client_secret}")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    _save_credentials(creds, token_path)
    return creds


def build_drive(interactive: bool = False):
    creds = get_credentials(interactive=interactive)
    return build("drive", "v3", credentials=creds)


def _save_credentials(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")


def list_children(service, folder_id: str) -> list[dict]:
    q = f"'{folder_id}' in parents and trashed=false"
    fields = "files(id,name,mimeType,modifiedTime,size,webViewLink),nextPageToken"
    items: list[dict] = []
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=q,
                fields=fields,
                pageToken=page_token,
                corpora="allDrives",
                spaces="drive",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
            .execute()
        )
        items.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def find_folder(service, parent_id: str, name: str) -> Optional[dict]:
    escaped = name.replace("'", "\\'")
    q = (
        f"name='{escaped}' and '{parent_id}' in parents and "
        f"mimeType='{FOLDER_MIME}' and trashed=false"
    )
    resp = (
        service.files()
        .list(
            q=q,
            fields="files(id,name,mimeType)",
            corpora="allDrives",
            spaces="drive",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageSize=50,
        )
        .execute()
    )
    files = resp.get("files", [])
    # Se houver duplicatas antigas, usar a primeira e evitar criar novas.
    return files[0] if files else None


def create_folder(service, parent_id: str, name: str) -> dict:
    body = {
        "name": name,
        "mimeType": FOLDER_MIME,
        "parents": [parent_id],
    }
    return (
        service.files()
        .create(body=body, fields="id,name,mimeType", supportsAllDrives=True)
        .execute()
    )


def ensure_folder(service, parent_id: str, name: str) -> dict:
    found = find_folder(service, parent_id=parent_id, name=name)
    if found:
        return found
    return create_folder(service, parent_id=parent_id, name=name)


def get_file_by_id(service, file_id: str) -> Optional[dict]:
    try:
        return (
            service.files()
            .get(
                fileId=file_id,
                fields="id,name,mimeType,parents",
                supportsAllDrives=True,
            )
            .execute()
        )
    except Exception:
        return None


def upload_file_to_folder(service, local_path: Path, folder_id: str, target_name: Optional[str] = None) -> dict:
    name = target_name or local_path.name
    mime, _ = mimetypes.guess_type(str(local_path))
    media = MediaFileUpload(str(local_path), mimetype=mime or "application/octet-stream", resumable=False)
    body = {
        "name": name,
        "parents": [folder_id],
    }
    return (
        service.files()
        .create(
            body=body,
            media_body=media,
            fields="id,name,mimeType,webViewLink,size,modifiedTime",
            supportsAllDrives=True,
        )
        .execute()
    )
