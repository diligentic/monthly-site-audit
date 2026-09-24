import logging
import os
import secrets
import threading
import uuid
from datetime import date as date_type

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from constants.sites import SITES
from services.audit_runner import run_audit

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

AUDIT_API_KEY_ENV_VAR = "AUDIT_API_KEY"

app = FastAPI(
    title="Monthly Site Audit API",
    description=(
        "Triggers the monthly/quarterly site audit. Collected CSVs are "
        "uploaded to Google Drive under the 'audit_data' folder; nothing is "
        "stored on local disk. Data is only collected when a run is triggered."
    ),
)


class RunRequest(BaseModel):
    """Optional parameters for an audit run."""

    site: str | None = Field(
        default=None,
        description="Restrict the run to a single site name (e.g. 'Diligentic').",
        examples=["Diligentic"],
    )
    date: date_type | None = Field(
        default=None,
        description="Anchor date (YYYY-MM-DD); defaults to today. Useful for testing.",
        examples=["2026-09-23"],
    )


class RunResponse(BaseModel):
    run_id: str
    status: str


def _require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> None:
    """Enforce ``X-Api-Key`` when ``AUDIT_API_KEY`` is configured."""
    expected = os.getenv(AUDIT_API_KEY_ENV_VAR)
    if not expected:
        return  # key not configured: open (local development)
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
        )


class RunInProgressError(RuntimeError):
    """Raised when a run is requested while another is still running."""


class RunManager:
    """Starts audits on a background thread so the trigger returns instantly.

    Only the in-progress run id is kept in memory; it guards against two
    overlapping audits (which would double-crawl the sites). The manager is
    intentionally stateless otherwise — results live on Google Drive and in
    the application logs.
    """

    def __init__(self) -> None:
        self._active: str | None = None
        self._lock = threading.Lock()

    def start(self, *, site: str | None, requested_date: date_type | None) -> str:
        with self._lock:
            if self._active is not None:
                raise RunInProgressError(self._active)
            run_id = uuid.uuid4().hex
            self._active = run_id
        thread = threading.Thread(
            target=self._execute,
            args=(run_id, site, requested_date),
            name=f"audit-{run_id}",
            daemon=True,
        )
        thread.start()
        return run_id

    def _execute(
        self,
        run_id: str,
        site: str | None,
        requested_date: date_type | None,
    ) -> None:
        logger.info("Audit run %s started", run_id)
        try:
            run_audit(site_names=[site] if site else None, today=requested_date)
            logger.info("Audit run %s finished", run_id)
        except Exception:
            logger.exception("Audit run %s failed", run_id)
        finally:
            with self._lock:
                if self._active == run_id:
                    self._active = None


manager = RunManager()


@app.get("/healthz", tags=["infra"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/audit/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunResponse,
    tags=["audit"],
    dependencies=[Depends(_require_api_key)],
)
def create_run(payload: RunRequest | None = None) -> RunResponse:
    if payload is None:
        payload = RunRequest()
    if payload.site is not None:
        choices = {site.name for site in SITES}
        if payload.site not in choices:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown site {payload.site!r}. Choices: {', '.join(sorted(choices))}.",
            )
    try:
        run_id = manager.start(site=payload.site, requested_date=payload.date)
    except RunInProgressError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An audit run is already in progress (run {error}).",
        ) from error
    return RunResponse(run_id=run_id, status="running")
