from __future__ import annotations

import io
import json
import mimetypes
from pathlib import Path
from typing import Optional

import google.auth
from google.auth.credentials import Credentials as GoogleAuthCredentials
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from settings import (
    GOOGLE_DRIVE_AUTH_MODE,
    GOOGLE_OAUTH_CLIENT_SECRET_JSON,
    GOOGLE_OAUTH_CLIENT_SECRET,
    GOOGLE_OAUTH_TOKEN_JSON,
    GOOGLE_OAUTH_TOKEN_PATH,
    GOOGLE_SERVICE_ACCOUNT_FILE,
    GOOGLE_SERVICE_ACCOUNT_JSON,
)

SCOPES = ["https://www.googleapis.com/auth/drive"]
FOLDER_MIME = "application/vnd.google-apps.folder"


class DriveAuthError(RuntimeError):
    pass


def get_credentials(interactive: bool = False) -> Credentials:
    if GOOGLE_DRIVE_AUTH_MODE == "service_account":
        return get_service_account_credentials()

    token_path = Path(GOOGLE_OAUTH_TOKEN_PATH)
    creds: Optional[Credentials] = None
    token_from_env = False

    if GOOGLE_OAUTH_TOKEN_JSON:
        try:
            info = json.loads(GOOGLE_OAUTH_TOKEN_JSON)
            creds = Credentials.from_authorized_user_info(info, SCOPES)
            token_from_env = True
        except Exception as exc:
            raise DriveAuthError(f"GOOGLE_OAUTH_TOKEN_JSON inválido: {exc}")

    if not creds and token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        if not token_from_env:
            try:
                _save_credentials(creds, token_path)
            except Exception:
                # Em Cloud Run, o token pode estar montado como secret read-only.
                # O refresh em memória já é suficiente para a requisição atual.
                pass

    if creds and creds.valid:
        return creds

    if not interactive:
        raise DriveAuthError(
            "Credenciais OAuth ausentes/expiradas. Rode /api/drive/auth-start para autorizar."
        )

    client_secret_path: Optional[Path] = None
    tmp_client_file: Optional[Path] = None
    if GOOGLE_OAUTH_CLIENT_SECRET_JSON:
        try:
            with open("/tmp/gineco_oauth_client.json", "w", encoding="utf-8") as fh:
                fh.write(GOOGLE_OAUTH_CLIENT_SECRET_JSON)
            tmp_client_file = Path("/tmp/gineco_oauth_client.json")
            client_secret_path = tmp_client_file
        except Exception as exc:
            raise DriveAuthError(f"Falha ao preparar OAuth client JSON: {exc}")
    else:
        candidate = Path(GOOGLE_OAUTH_CLIENT_SECRET)
        if not candidate.exists():
            raise DriveAuthError(f"OAuth client JSON não encontrado: {candidate}")
        client_secret_path = candidate

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    _save_credentials(creds, token_path)
    if tmp_client_file and tmp_client_file.exists():
        try:
            tmp_client_file.unlink()
        except Exception:
            pass
    return creds


def get_service_account_credentials() -> GoogleAuthCredentials:
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
            return ServiceAccountCredentials.from_service_account_info(info, scopes=SCOPES)
        except Exception as exc:
            raise DriveAuthError(f"GOOGLE_SERVICE_ACCOUNT_JSON inválido: {exc}")

    service_account_path = Path(GOOGLE_SERVICE_ACCOUNT_FILE)
    if not service_account_path.exists():
        # Fallback recomendado em Cloud Run: usar identidade anexada ao serviço
        # sem chave JSON (ADC via metadata server).
        creds, _ = google.auth.default(scopes=SCOPES)
        return creds
    return ServiceAccountCredentials.from_service_account_file(str(service_account_path), scopes=SCOPES)


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


def upload_file_to_folder(
    service,
    local_path: Path,
    folder_id: str,
    target_name: Optional[str] = None,
    replace_existing: bool = True,
) -> dict:
    """Faz upload de um arquivo local para uma pasta do Drive.

    Quando `replace_existing=True` (default), procura por arquivo com o
    mesmo nome na pasta destino; se encontrar, faz `files.update` em vez
    de `files.create`. Isso evita acumular duplicatas a cada execucao.
    """
    name = target_name or local_path.name
    mime, _ = mimetypes.guess_type(str(local_path))
    media = MediaFileUpload(str(local_path), mimetype=mime or "application/octet-stream", resumable=False)
    fields = "id,name,mimeType,webViewLink,size,modifiedTime"

    if replace_existing:
        existing = find_file_in_folder(service, folder_id=folder_id, name=name)
        if existing:
            return (
                service.files()
                .update(
                    fileId=existing["id"],
                    media_body=media,
                    fields=fields,
                    supportsAllDrives=True,
                )
                .execute()
            )

    body = {
        "name": name,
        "parents": [folder_id],
    }
    return (
        service.files()
        .create(
            body=body,
            media_body=media,
            fields=fields,
            supportsAllDrives=True,
        )
        .execute()
    )


def find_file_in_folder(service, folder_id: str, name: str) -> Optional[dict]:
    """Procura um arquivo (nao-pasta) por nome dentro de uma pasta."""
    escaped = name.replace("'", "\\'")
    q = (
        f"name='{escaped}' and '{folder_id}' in parents and "
        f"mimeType!='{FOLDER_MIME}' and trashed=false"
    )
    resp = (
        service.files()
        .list(
            q=q,
            fields="files(id,name,mimeType,modifiedTime)",
            corpora="allDrives",
            spaces="drive",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageSize=10,
            orderBy="modifiedTime desc",
        )
        .execute()
    )
    files = resp.get("files", [])
    return files[0] if files else None


def trash_file(service, file_id: str) -> dict:
    """Move um arquivo para a lixeira do Drive (reversivel)."""
    return (
        service.files()
        .update(
            fileId=file_id,
            body={"trashed": True},
            fields="id,name,trashed",
            supportsAllDrives=True,
        )
        .execute()
    )


def download_file_to_path(service, file_id: str, target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    with target_path.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return target_path


def download_file_bytes(service, file_id: str) -> bytes:
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()
