"""Serialize audit rows to in-memory CSV bytes.

Nothing here touches the filesystem: every provider serializer returns
``bytes`` that can be uploaded straight to Google Drive.
"""

import csv
import io
from collections.abc import Mapping
from typing import Any

METRIC_COLUMNS = ("clicks", "impressions", "ctr", "position")


def serialize_dict_rows(
    rows: list[dict[str, Any]] | None,
    fieldnames: tuple[str, ...] | list[str],
    *,
    names: Mapping[str, str] | None = None,
) -> bytes:
    """Encode ``rows`` as UTF-8 CSV bytes.

    ``names`` optionally maps internal column names to the display headers
    that should appear in the file; unmapped keys are written as-is.
    Unknown row keys are dropped so the header controls the schema exactly.
    """
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=list(fieldnames),
        extrasaction="ignore",
    )
    writer.writeheader()
    for row in rows or []:
        if names:
            writer.writerow(
                {names.get(key, key): value for key, value in row.items()}
            )
        else:
            writer.writerow(row)
    return output.getvalue().encode("utf-8")


def serialize_metrics_rows(
    rows: list[dict[str, Any]] | None,
    *,
    key_column: str,
) -> bytes:
    """Serialize the shared ``clicks/impressions/ctr/position`` row format.

    Rows produced by Search Console, Bing, and the Canada filters all share
    the same shape: ``keys`` holds the dimension value and the metrics are
    top-level fields.
    """
    fieldnames = [key_column, *METRIC_COLUMNS]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows or []:
        keys = row.get("keys") or [""]
        writer.writerow(
            {
                key_column: keys[0],
                "clicks": row.get("clicks", ""),
                "impressions": row.get("impressions", ""),
                "ctr": row.get("ctr", ""),
                "position": row.get("position", ""),
            }
        )
    return output.getvalue().encode("utf-8")