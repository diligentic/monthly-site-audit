import argparse
import logging
from datetime import date

from dotenv import load_dotenv

from constants.sites import SITES
from services.audit_runner import run_audit
from utils.get_env import ConfigurationError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect search, analytics, sitemap, crawl, and Core Web Vitals data on a monthly/quarterly schedule."
    )
    parser.add_argument(
        "--site",
        choices=[site.name for site in SITES],
        help="Audit only this site instead of all scheduled sites.",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Anchor date; defaults to today, e.g. 2026-07-15 to simulate a July run.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    load_dotenv()
    try:
        result = run_audit(
            site_names=[args.site] if args.site else None,
            today=args.date,
        )
    except ConfigurationError as error:
        logger.error("%s", error)
        raise SystemExit(1) from error

    for site_result in result.sites:
        for stream_result in site_result.results:
            if stream_result.status == "failed":
                logger.warning(
                    "Failed to collect %s for %s: %s",
                    stream_result.label,
                    stream_result.month,
                    stream_result.error,
                )

    if result.failures:
        logger.error(
            "Audit finished with %d failure(s); see the log above.",
            result.failures,
        )
        raise SystemExit(1)
    logger.info("Audit complete in %.1fs.", result.duration_seconds)


if __name__ == "__main__":
    main()