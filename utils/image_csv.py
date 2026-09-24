from datetime import date
from typing import Any

from constants.crawl import IMAGE_COLUMNS
from utils.csv_serializer import serialize_dict_rows
from utils.dates import month_range

_IMAGE_HEADER_NAMES = {
    "image_url": "Image URL",
    "status": "Status Code",
    "content_type": "Content Type",
    "file_size": "File Size",
    "width": "Width",
    "height": "Height",
    "error": "Error",
}


def image_csv_name(
    today: date | None = None, months_back: int = 1
) -> str:
    month = month_range(today, months_back=months_back)[0]
    return f"images_{month:%Y-%m}.csv"


def serialize_image_rows(rows: list[dict[str, Any]] | None) -> bytes:
    return serialize_dict_rows(
        rows, IMAGE_COLUMNS, names=_IMAGE_HEADER_NAMES
    )