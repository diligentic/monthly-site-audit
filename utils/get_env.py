import os

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class ConfigurationError(RuntimeError):
    """Raised when required environment configuration is not set."""


def _get_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigurationError(f"Missing required environment variable: {name}")
    return value.strip()


def env_str(name: str, default: str = "") -> str:
    """Return a stripped environment value, or ``default`` when unset."""
    return (os.getenv(name) or "").strip() or default


def env_int(name: str, default: int, *, minimum: int | None = 0) -> int:
    """Return an integer environment value, validated against ``minimum``.

    A bad value raises :class:`ConfigurationError` so a typo in a deployment
    environment is reported as the configuration mistake it is, rather than
    silently falling back to a default that quietly does the wrong thing.
    """
    raw = env_str(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer, got {raw!r}") from error
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{name} must be >= {minimum}, got {value}")
    return value


def env_bool(name: str, default: bool) -> bool:
    """Return a boolean environment value (``true/false``, ``1/0``, ...)."""
    raw = env_str(name).lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ConfigurationError(
        f"{name} must be a boolean (true/false, 1/0, yes/no), got {raw!r}"
    )
