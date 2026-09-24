"""Google OAuth 2.0 credentials shared by Google API clients."""

from functools import lru_cache

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from utils.get_env import ConfigurationError, _get_env

CLIENT_ID_ENV_VAR = "GOOGLE_CLIENT_ID"
CLIENT_SECRET_ENV_VAR = "GOOGLE_CLIENT_SECRET"
REFRESH_TOKEN_ENV_VAR = "GOOGLE_REFRESH_TOKEN"
TOKEN_URI = "https://oauth2.googleapis.com/token"

GOOGLE_API_SCOPES = (
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/drive.file",
)


@lru_cache(maxsize=1)
def _credentials() -> Credentials:
    """Create refreshable credentials once per process."""
    return Credentials(
        token=None,
        refresh_token=_get_env(REFRESH_TOKEN_ENV_VAR),
        token_uri=TOKEN_URI,
        client_id=_get_env(CLIENT_ID_ENV_VAR),
        client_secret=_get_env(CLIENT_SECRET_ENV_VAR),
        scopes=GOOGLE_API_SCOPES,
    )


def get_access_token() -> str:
    """Return a valid access token, refreshing it when required."""
    credentials = _credentials()

    try:
        if not credentials.valid:
            credentials.refresh(Request())
    except Exception as error:
        raise ConfigurationError(
            "Unable to obtain a Google access token. Verify GOOGLE_CLIENT_ID, "
            "GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN and confirm that "
            "the refresh token grants the required Google API scopes."
        ) from error

    if not credentials.token:
        raise ConfigurationError("Google OAuth returned an empty access token.")

    return credentials.token


def validate_oauth_credentials() -> None:
    """Validate configuration and confirm the refresh token can be used."""
    _get_env(CLIENT_ID_ENV_VAR)
    _get_env(CLIENT_SECRET_ENV_VAR)
    _get_env(REFRESH_TOKEN_ENV_VAR)
    get_access_token()
