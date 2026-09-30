"""Best-effort email notifications for completed audit runs."""

import logging
import os
from collections.abc import Callable
from typing import Any

import requests

from services.audit_runner import AuditRunResult, run_audit

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_API_KEY_ENV_VAR = "BREVO_API_KEY"
AUDIT_NOTIFICATION_TO_ENV_VAR = "AUDIT_NOTIFICATION_TO_EMAIL"
AUDIT_NOTIFICATION_FROM_ENV_VAR = "AUDIT_NOTIFICATION_FROM_EMAIL"
AUDIT_NOTIFICATION_FROM_NAME_ENV_VAR = "AUDIT_NOTIFICATION_FROM_NAME"
_REQUEST_TIMEOUT_SECONDS = 15


def _notification_config() -> tuple[str, str, str, str] | None:
    """Return notification configuration, or None when email is not set up."""
    api_key = os.getenv(BREVO_API_KEY_ENV_VAR, "").strip()
    recipients = os.getenv(AUDIT_NOTIFICATION_TO_ENV_VAR, "").strip()
    sender = os.getenv(AUDIT_NOTIFICATION_FROM_ENV_VAR, "").strip()
    sender_name = os.getenv(AUDIT_NOTIFICATION_FROM_NAME_ENV_VAR, "Site Audit")
    if not (api_key or recipients or sender):
        logger.info(
            "Audit email notifications are disabled; notification env vars are unset"
        )
        return None
    missing = [
        name
        for name, value in (
            (BREVO_API_KEY_ENV_VAR, api_key),
            (AUDIT_NOTIFICATION_TO_ENV_VAR, recipients),
            (AUDIT_NOTIFICATION_FROM_ENV_VAR, sender),
        )
        if not value
    ]
    if missing:
        logger.error("Audit email notification not sent; missing %s", ", ".join(missing))
        return None
    return api_key, recipients, sender, sender_name.strip() or "Site Audit"


def _message_body(
    *,
    result: AuditRunResult | None,
    run_error: str | None,
    run_id: str | None,
    scope: str,
) -> tuple[str, str]:
    if run_error:
        subject = "Audit failed before completion"
        lines = [
            "The audit exited with an error before it could complete.",
            f"Scope: {scope}",
            f"Run ID: {run_id or 'unavailable'}",
            f"Error: {run_error}",
        ]
    else:
        assert result is not None
        if result.failures:
            subject = f"Audit completed with {result.failures} failure(s)"
            lines = [
                "The audit completed, but one or more data sources failed.",
                f"Run ID: {run_id or 'unavailable'}",
                f"Started: {result.started_at}",
                f"Finished: {result.finished_at}",
                f"Duration: {result.duration_seconds:.1f} seconds",
                f"Failed streams: {result.failures}",
            ]
            for site in result.sites:
                for item in site.results:
                    if item.status != "failed":
                        continue
                    lines.extend(
                        [
                            "",
                            f"Site: {site.name}",
                            f"Source/API: {item.label}",
                            f"Month: {item.month}",
                            f"Expected Drive file: {item.drive_path or 'unknown'}",
                            f"Error: {item.error or 'unknown error'}",
                        ]
                    )
        else:
            subject = "Audit completed successfully"
            lines = [
                "The audit completed successfully with no failed data sources.",
                f"Run ID: {run_id or 'unavailable'}",
                f"Started: {result.started_at}",
                f"Finished: {result.finished_at}",
                f"Duration: {result.duration_seconds:.1f} seconds",
            ]
    return subject, "\n".join(lines)


def send_audit_notification(
    *,
    result: AuditRunResult | None = None,
    run_error: str | None = None,
    run_id: str | None = None,
    scope: str = "all configured sites",
) -> None:
    """Send a best-effort status email through Brevo's transactional API."""
    config = _notification_config()
    if config is None:
        return
    api_key, recipient_list, sender, sender_name = config
    subject, body = _message_body(
        result=result,
        run_error=run_error,
        run_id=run_id,
        scope=scope,
    )
    payload: dict[str, Any] = {
        "sender": {"email": sender, "name": sender_name},
        "to": [
            {"email": email.strip()}
            for email in recipient_list.split(",")
            if email.strip()
        ],
        "subject": subject,
        "textContent": body,
    }
    if not payload["to"]:
        logger.error("Audit email notification not sent; no valid recipients configured")
        return
    try:
        response = requests.post(
            BREVO_API_URL,
            headers={"api-key": api_key, "accept": "application/json"},
            json=payload,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        logger.info("Audit status notification sent through Brevo")
    except requests.RequestException:
        logger.exception("Could not send audit status notification through Brevo")


def run_audit_with_notification(
    *,
    run_id: str | None = None,
    scope: str = "all configured sites",
    audit: Callable[..., AuditRunResult] = run_audit,
    **audit_options: Any,
) -> AuditRunResult:
    """Run an audit, notifying on success, partial failure, or fatal error."""
    try:
        result = audit(**audit_options)
    except Exception as error:
        send_audit_notification(
            run_error=f"{type(error).__name__}: {error}",
            run_id=run_id,
            scope=scope,
        )
        raise
    send_audit_notification(result=result, run_id=run_id, scope=scope)
    return result
