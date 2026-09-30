"""Best-effort email notifications for completed audit runs."""

import logging
import os
from datetime import UTC, datetime
from html import escape
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
            f"Error: {run_error}",
        ]
    else:
        assert result is not None
        if result.failures:
            subject = f"Audit completed with {result.failures} failure(s)"
            lines = [
                "The audit completed, but one or more data sources failed.",
                f"Started: {_format_timestamp(result.started_at)}",
                f"Finished: {_format_timestamp(result.finished_at)}",
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
                f"Started: {_format_timestamp(result.started_at)}",
                f"Finished: {_format_timestamp(result.finished_at)}",
                f"Duration: {result.duration_seconds:.1f} seconds",
            ]
    return subject, "\n".join(lines)


def _format_timestamp(value: str) -> str:
    """Format audit timestamps consistently, preserving invalid input verbatim."""
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return value
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).strftime("%b %d, %Y at %I:%M:%S %p UTC")


def _html_content(subject: str, body: str) -> str:
    """Render a readable, email-client-friendly HTML version of the message."""
    paragraphs = body.split("\n\n", 1)
    if len(paragraphs) > 1:
        introduction = escape(paragraphs[0])
        details = paragraphs[1].splitlines()
    else:
        lines = body.splitlines()
        introduction = escape(lines[0]) if lines else ""
        details = lines[1:]
    summary_labels = {"Scope", "Started", "Finished", "Duration", "Failed streams"}
    summary_rows = []
    failures = []
    for line in details:
        label, separator, value = line.partition(": ")
        if not separator:
            continue
        if label in summary_labels:
            summary_rows.append(
                "<tr>"
                f'<th align="left" style="padding:8px 12px;color:#475569;">{escape(label)}</th>'
                f'<td style="padding:8px 12px;color:#0f172a;">{escape(value)}</td>'
                "</tr>"
            )
        elif label == "Site":
            failures.append({"Site": value})
        elif failures and label in {"Source/API", "Month", "Expected Drive file", "Error"}:
            failures[-1][label] = value
        elif label == "Error":
            failures.append({"Error": value})

    summary = ""
    if summary_rows:
        summary = (
            '<table role="presentation" style="width:100%;border-collapse:collapse;'
            'background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;">'
            + "".join(summary_rows)
            + "</table>"
        )
    failure_cards = []
    for failure in failures:
        rows = []
        for label in ("Site", "Source/API", "Month", "Expected Drive file", "Error"):
            if label in failure:
                rows.append(
                    "<tr>"
                    f'<th align="left" valign="top" style="width:150px;padding:7px 10px;color:#475569;">{escape(label)}</th>'
                    f'<td style="padding:7px 10px;color:#0f172a;overflow-wrap:anywhere;">{escape(failure[label])}</td>'
                    "</tr>"
                )
        failure_cards.append(
            '<table role="presentation" style="width:100%;border-collapse:collapse;'
            'margin-top:12px;border:1px solid #e2e8f0;border-left:4px solid #dc2626;'
            'background:#fff;">' + "".join(rows) + "</table>"
        )
    failures_html = "".join(failure_cards)
    other_details = ""
    if not summary_rows and not failure_cards and details:
        other_details = (
            '<div style="padding:14px 16px;background:#f8fafc;'
            'border:1px solid #e2e8f0;border-radius:8px;line-height:1.6;">'
            + "<br>".join(escape(line) for line in details)
            + "</div>"
        )
    return (
        '<!doctype html><html><body style="margin:0;background:#f1f5f9;'
        'font-family:Arial,Helvetica,sans-serif;color:#0f172a;">'
        '<div style="max-width:720px;margin:24px auto;padding:0 16px;">'
        '<div style="background:#fff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">'
        '<div style="padding:22px 26px;background:#0f172a;color:#fff;">'
        f'<h1 style="margin:0;font-size:22px;">{escape(subject)}</h1></div>'
        '<div style="padding:24px 26px;">'
        f'<p style="margin:0 0 20px;line-height:1.6;">{introduction}</p>'
        f'{summary}{failures_html}{other_details}'
        '</div></div><p style="margin:14px 0;text-align:center;color:#64748b;font-size:12px;">Site Audit notification</p>'
        '</div></body></html>'
    )


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
        "htmlContent": _html_content(subject, body),
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
    except requests.RequestException as error:
        response = error.response
        if response is not None:
            detail = response.text[:2000]
            logger.error(
                "Could not send audit status notification through Brevo "
                "(HTTP %s): %s",
                response.status_code,
                detail or error,
            )
        else:
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
