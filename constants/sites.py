from dataclasses import dataclass
from enum import Enum
from os import getenv
from pathlib import Path

from constants.ga4 import (
    GA4_PROPERTY_ID_AJAYKUMAR,
    GA4_PROPERTY_ID_DILIGENTIC,
)
from constants.sources import Provider

DATA_ROOT_ENV_VAR = "DATA_ROOT"


def _default_data_root() -> Path:
    return Path(__file__).resolve().parent.parent / "data"


DATA_ROOT = Path(getenv(DATA_ROOT_ENV_VAR, str(_default_data_root())))

QUARTERLY_MONTHS = frozenset({1, 4, 7, 10})


class Schedule(Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


@dataclass(frozen=True)
class Site:
    name: str
    gsc_site_url: str
    bing_site_url: str
    sitemap_url: str
    schedule: Schedule
    history_months: int
    always_fetch_months: int
    ga4_property_id_env_var: str

    @property
    def data_dir(self) -> Path:
        return DATA_ROOT / self.name

    def provider_dir(self, provider: Provider) -> Path:
        return self.data_dir / provider.value


DILIGENTIC = Site(
    name="Diligentic",
    gsc_site_url="sc-domain:diligentic.ca",
    bing_site_url="https://diligentic.ca/",
    sitemap_url="https://diligentic.ca/sitemap.xml",
    schedule=Schedule.MONTHLY,
    history_months=2,
    always_fetch_months=1,
    ga4_property_id_env_var=GA4_PROPERTY_ID_DILIGENTIC,
)

AJAYKUMAR = Site(
    name="AjayKumar",
    gsc_site_url="sc-domain:ajaykumar.ca",
    bing_site_url="https://ajaykumar.ca/",
    sitemap_url="https://ajaykumar.ca/sitemap.xml",
    schedule=Schedule.QUARTERLY,
    history_months=6,
    always_fetch_months=3,
    ga4_property_id_env_var=GA4_PROPERTY_ID_AJAYKUMAR,
)

SITES = (DILIGENTIC, AJAYKUMAR)
