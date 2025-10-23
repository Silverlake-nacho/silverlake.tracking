from __future__ import annotations

import os
import re
from typing import Any, Optional
from urllib.parse import quote

import requests
from flask import Flask, render_template, request
from requests import RequestException

app = Flask(__name__)

TRACKING_BASE_URL = "https://orderstrack.com/"
TRACKING_PATTERN = re.compile(r"^[A-Za-z0-9]+$")

# ``MAXOPTRA_BASE_URL`` should point at the customer's Maxoptra tenant, e.g.
# ``https://yourmaxoptraaccount.maxoptra.com``.
MAXOPTRA_BASE_URL = os.environ.get(
    "MAXOPTRA_BASE_URL", "https://widgets.maxoptra.com"
).rstrip("/")
MAXOPTRA_WIDGET_ENDPOINT = (
    f"{MAXOPTRA_BASE_URL}/api/v6/orders/{{reference}}/widget"
)
MAXOPTRA_API_KEY = os.environ.get(
    "MAXOPTRA_API_KEY", "Ua85Vj4ucIlzUa7qk5Yb6M55qfDXPHoGhUbfCQpmgr76wKntTm"
)
TRACKING_NUMBER_KEYS = (
    "trackingNumber",
    "tracking_number",
    "trackingCode",
    "tracking_code",
    "tracking",
    "consignmentNumber",
    "consignment_number",
    "trackingId",
    "tracking_id",
)


def _extract_tracking_number(payload: Any) -> Optional[str]:
    """Recursively search for a plausible tracking number within an API payload."""

    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in TRACKING_NUMBER_KEYS and isinstance(value, str):
                candidate = value.strip()
                if candidate:
                    return candidate
            candidate = _extract_tracking_number(value)
            if candidate:
                return candidate
    elif isinstance(payload, list):
        for item in payload:
            candidate = _extract_tracking_number(item)
            if candidate:
                return candidate
    return None


def _fetch_tracking_number_from_reference(order_reference: str) -> tuple[Optional[str], Optional[str]]:
    """Retrieve the tracking number associated with ``order_reference`` from Maxoptra."""

    if not MAXOPTRA_API_KEY:
        return None, "Tracking by reference is not configured."

    if not MAXOPTRA_BASE_URL:
        return None, (
            "Tracking by reference is not configured correctly. Please set the "
            "Maxoptra base URL."
        )

    encoded_reference = quote(order_reference, safe="")

    try:
        response = requests.get(
            MAXOPTRA_WIDGET_ENDPOINT.format(reference=encoded_reference),
            headers={
                "Api-Key": MAXOPTRA_API_KEY,
                "Authorization": f"Bearer {MAXOPTRA_API_KEY}",
                "Accept": "application/json",
            },
            timeout=10,
        )
    except RequestException as exc:
        app.logger.warning(
            "Error contacting Maxoptra for reference %s: %s", order_reference, exc
        )
        detail = f"Unable to contact the tracking service. Please try again later. (Technical detail: {exc})"
        return None, detail

    if response.status_code == 404:
        return None, "No delivery was found for that reference."
    if response.status_code in {401, 403}:
        body_preview = response.text[:200]
        app.logger.warning(
            "Maxoptra returned %s for reference %s: %s",
            response.status_code,
            order_reference,
            body_preview,
        )
        return (
            None,
            "The tracking service rejected the request (HTTP {code}). This can happen if "
            "the API key is invalid, the Maxoptra account URL is incorrect, or network "
            "access to Maxoptra is blocked. "
            "Please contact support.".format(code=response.status_code),
        )
    if response.status_code >= 500:
        app.logger.warning(
            "Maxoptra returned %s for reference %s", response.status_code, order_reference
        )
        return None, "The tracking service is temporarily unavailable. Please try again later."
    if not response.ok:
        app.logger.warning(
            "Unexpected Maxoptra status %s for reference %s: %s",
            response.status_code,
            order_reference,
            response.text[:200],
        )
        return None, "Unexpected response from the tracking service."

    try:
        payload = response.json()
    except ValueError:
        return None, "Received an invalid response from the tracking service."

    tracking_number = _extract_tracking_number(payload)
    if tracking_number:
        return tracking_number, None

    return None, "The tracking service did not return a tracking number for that reference."


def _build_context(
    raw_tracking_number: str | None,
    raw_order_reference: str | None,
    *,
    submission_attempted: bool,
) -> dict[str, Optional[str]]:
    """Return template context for a potential tracking number or reference submission."""

    tracking_number: str = raw_tracking_number.strip() if raw_tracking_number else ""
    order_reference: str = raw_order_reference.strip() if raw_order_reference else ""
    tracking_url: Optional[str] = None
    error_message: Optional[str] = None
    reference_error_message: Optional[str] = None
    resolved_tracking_number: Optional[str] = None

    if submission_attempted:
        if tracking_number:
            if TRACKING_PATTERN.fullmatch(tracking_number):
                tracking_url = f"{TRACKING_BASE_URL}{tracking_number}"
            else:
                error_message = (
                    "Tracking numbers may only contain letters and numbers. "
                    "Please try again."
                )
        elif order_reference:
            resolved_tracking_number, reference_error_message = (
                _fetch_tracking_number_from_reference(order_reference)
            )
            if resolved_tracking_number and TRACKING_PATTERN.fullmatch(resolved_tracking_number):
                tracking_number = resolved_tracking_number
                tracking_url = f"{TRACKING_BASE_URL}{resolved_tracking_number}"
            elif resolved_tracking_number:
                reference_error_message = (
                    "The retrieved tracking number appears to be invalid. Please contact support."
                )
                resolved_tracking_number = None
        else:
            error_message = "Please enter a tracking number or order reference."

    return {
        "tracking_number": tracking_number,
        "order_reference": order_reference,
        "tracking_url": tracking_url,
        "error_message": error_message,
        "reference_error_message": reference_error_message,
        "resolved_tracking_number": resolved_tracking_number,
    }


@app.route("/", methods=["GET", "POST"])
def index():
    """Render the home page with an optional tracking URL."""

    if request.method == "POST":
        return render_template(
            "index.html",
            **_build_context(
                request.form.get("tracking_number"),
                request.form.get("order_reference"),
                submission_attempted=True,
            ),
        )

    return render_template(
        "index.html",
        **_build_context(None, None, submission_attempted=False),
    )


@app.route("/<tracking_number>", methods=["GET"])
def tracking_from_path(tracking_number: str):
    """Display the tracker when a tracking number is supplied in the path."""

    return render_template(
        "index.html",
        **_build_context(tracking_number, None, submission_attempted=True),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
