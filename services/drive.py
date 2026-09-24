import io
import logging
import os
from typing import Any

from google.api_core.exceptions import GoogleAPICallError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from utils.get_env import ConfigurationError, _get_env

logger = logging.getLogger(__name__)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]

ROOT_FOLDER_NAME = "audit_data"

CLIENT_ID_ENV_VAR = "GOOGLE_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV_VAR = "GOOGLE_OAUTH_CLIENT_SECRET"
REFRESH_TOKEN_ENV_VAR = "GOOGLE_DRIVE_REFRESH_TOKEN"
ROOT_FOLDER_ID_ENV_VAR = "GOOGLE_DRIVE_ROOT_FOLDER_ID"

TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
AUTH_PROVIDER_CERT_URL = "https://www.googleapis.com/oauth2/v1/certs"

_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


class GoogleDriveError(RuntimeError):
    """Raised when a Google Drive operation cannot be completed."""


def oauth_client_config() -> dict[str, Any]:
    """OAuth 2.0 "Desktop app" client config built from the environment."""
    return {
        "installed": {
            "client_id": _get_env(CLIENT_ID_ENV_VAR),
            "client_secret": _get_env(CLIENT_SECRET_ENV_VAR),
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "auth_provider_x509_cert_url": AUTH_PROVIDER_CERT_URL,
            "redirect_uris": ["http://localhost"],
        }
    }


def validate_drive_credentials() -> None:
    """Fail fast when the OAuth environment variables are missing."""
    _get_env(CLIENT_ID_ENV_VAR)
    _get_env(CLIENT_SECRET_ENV_VAR)
    _get_env(REFRESH_TOKEN_ENV_VAR)


def _build_credentials() -> Credentials:
    """Build and immediately validate refreshable Google OAuth credentials."""
    credentials = Credentials(
        token=None,
        refresh_token=_get_env(REFRESH_TOKEN_ENV_VAR),
        token_uri=TOKEN_URI,
        client_id=_get_env(CLIENT_ID_ENV_VAR),
        client_secret=_get_env(CLIENT_SECRET_ENV_VAR),
        scopes=DRIVE_SCOPES,
    )
    try:
        credentials.refresh(Request())
    except Exception as error:
        raise ConfigurationError(
            "Failed to refresh the Google Drive OAuth token. Re-run "
            f"`uv run python auth_drive.py` to obtain a new "
            f"{REFRESH_TOKEN_ENV_VAR}. Details: {error}"
        ) from error
    return credentials


def _build_service() -> Any:
    credentials = _build_credentials()
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _escape_query(value: str) -> str:
    """Escape a string for a single-quoted literal in a Drive ``q`` filter."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _error_message(error: Exception) -> str:
    try:
        return error.reason  # type: ignore[attr-defined]
    except AttributeError:
        return str(error)


class GoogleDriveStorage:
    """Idempotent CSV storage under an ``audit_data`` folder on Google Drive.

    Folder discovery is cached for the lifetime of the instance so one audit
    run (many streams and months) only pays for each folder lookup once.
    """

    def __init__(self, service: Any | None = None) -> None:
        self._service = service or _build_service()
        self._audit_root_id: str | None = None
        self._folder_ids: dict[str, str] = {}
        self._file_cache: dict[tuple[str, str], str | None] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def file_exists(self, relative_path: str) -> bool:
        """Return whether a CSV already exists at ``relative_path``."""
        return self._resolve_file(relative_path) is not None

    def upload_csv(self, relative_path: str, content: bytes) -> None:
        """Create or update a CSV at ``relative_path`` on Google Drive.

        ``relative_path`` is relative to the audit root, e.g.
        ``Diligentic/GSC/queries_2026-09.csv``. Uploads are idempotent: if
        the file already exists in the target folder it is updated in place;
        otherwise it is created.
        """
        folder_path, _, filename = relative_path.rpartition("/")
        folder_id = self._ensure_folder_path(folder_path)

        media = MediaIoBaseUpload(
            io.BytesIO(content), mimetype="text/csv", resumable=False
        )
        try:
            existing = self._file_in_folder(filename, folder_id)
            if existing is not None:
                self._service.files().update(
                    fileId=existing["id"],
                    media_body=media,
                    fields="id,name",
                ).execute()
                logger.info("Updated Google Drive file %s", relative_path)
            else:
                body = {
                    "name": filename,
                    "parents": [folder_id],
                    "mimeType": "text/csv",
                }
                created = (
                    self._service.files()
                    .create(
                        body=body,
                        media_body=media,
                        fields="id,name",
                    )
                    .execute()
                )
                self._file_cache[(folder_id, filename)] = created["id"]
                logger.info("Uploaded Google Drive file %s", relative_path)
        except GoogleAPICallError as error:
            raise GoogleDriveError(
                f"Failed to upload {relative_path!r} to Google Drive: "
                f"{_error_message(error)}"
            ) from error
        except OSError as error:
            raise GoogleDriveError(
                f"Failed to upload {relative_path!r} to Google Drive: {error}"
            ) from error

    # ------------------------------------------------------------------
    # Folder resolution
    # ------------------------------------------------------------------
    @property
    def audit_root_id(self) -> str:
        """ID of the ``audit_data`` folder.

        It lives at the top of My Drive unless ``GOOGLE_DRIVE_ROOT_FOLDER_ID``
        points at a specific folder to place it in.
        """
        if self._audit_root_id is None:
            base_id = os.getenv(ROOT_FOLDER_ID_ENV_VAR, "").strip() or "root"
            self._audit_root_id = self._get_or_create_folder(ROOT_FOLDER_NAME, base_id)
        return self._audit_root_id

    def _ensure_folder_path(self, relative_folder: str) -> str:
        """Walk ``relative_folder`` (e.g. ``Diligentic/GSC``) from the audit root."""
        folder_id = self.audit_root_id
        prefix = ""
        for segment in relative_folder.split("/"):
            if not segment:
                continue
            prefix = f"{prefix}/{segment}" if prefix else segment
            cached = self._folder_ids.get(prefix)
            if cached is not None:
                folder_id = cached
            else:
                folder_id = self._get_or_create_folder(segment, folder_id)
                self._folder_ids[prefix] = folder_id
        return folder_id

    def _get_or_create_folder(self, name: str, parent_id: str) -> str:
        query = (
            f"name = '{_escape_query(name)}' "
            f"and '{parent_id}' in parents "
            f"and mimeType = '{_FOLDER_MIME_TYPE}' "
            "and trashed = false"
        )
        existing = self._query_one(query)
        if existing is not None:
            return existing["id"]
        try:
            created = (
                self._service.files()
                .create(
                    body={
                        "name": name,
                        "parents": [parent_id],
                        "mimeType": _FOLDER_MIME_TYPE,
                    },
                    fields="id",
                )
                .execute()
            )
        except GoogleAPICallError as error:
            raise GoogleDriveError(
                f"Failed to create Google Drive folder {name!r}: "
                f"{_error_message(error)}"
            ) from error
        return created["id"]

    # ------------------------------------------------------------------
    # File lookup
    # ------------------------------------------------------------------
    def _resolve_file(self, relative_path: str) -> dict[str, Any] | None:
        folder_path, _, filename = relative_path.rpartition("/")
        folder_id = self._ensure_folder_path(folder_path)
        return self._file_in_folder(filename, folder_id)

    def _file_in_folder(self, name: str, folder_id: str) -> dict[str, Any] | None:
        cache_key = (folder_id, name)
        if cache_key in self._file_cache:
            cached = self._file_cache[cache_key]
            return {"id": cached} if cached else None
        query = (
            f"name = '{_escape_query(name)}' "
            f"and '{folder_id}' in parents "
            "and trashed = false"
        )
        file_id = None
        try:
            response = (
                self._service.files()
                .list(
                    q=query,
                    fields="files(id)",
                    pageSize=1,
                )
                .execute()
            )
            matched = response.get("files") or []
            if matched:
                file_id = matched[0]["id"]
        except GoogleAPICallError as error:
            raise GoogleDriveError(
                f"Failed to query Google Drive for {name!r}: {_error_message(error)}"
            ) from error
        self._file_cache[cache_key] = file_id
        return {"id": file_id} if file_id else None

    def _query_one(self, query: str) -> dict[str, Any] | None:
        try:
            response = (
                self._service.files()
                .list(
                    q=query,
                    fields="files(id)",
                    pageSize=1,
                )
                .execute()
            )
        except GoogleAPICallError as error:
            raise GoogleDriveError(
                f"Google Drive query failed: {_error_message(error)}"
            ) from error
        matched = response.get("files") or []
        return matched[0] if matched else None
