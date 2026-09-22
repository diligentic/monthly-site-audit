import csv
import os
from pathlib import Path
from typing import Any

METRIC_COLUMNS = ("clicks", "impressions", "ctr", "position")


def write_metrics_csv(
    rows: list[dict[str, Any]],
    csv_path: Path,
    *,
    key_column: str,
) -> Path:
    fieldnames = [key_column, *METRIC_COLUMNS]
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = csv_path.with_name(f"{csv_path.name}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
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
        os.replace(temp_path, csv_path)
    finally:
        temp_path.unlink(missing_ok=True)

    return csv_path
