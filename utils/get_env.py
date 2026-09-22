import os


class SearchConsoleConfigurationError(RuntimeError):
    """Raised when required Search Console credentials are not configured."""


def _get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SearchConsoleConfigurationError(
            f"Missing required environment variable: {name}"
        )
    return value.strip()
