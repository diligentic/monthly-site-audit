import os


class ConfigurationError(RuntimeError):
    """Raised when required environment configuration is not set."""


def _get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigurationError(f"Missing required environment variable: {name}")
    return value.strip()
