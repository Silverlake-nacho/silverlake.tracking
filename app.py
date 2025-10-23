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
    "MAXOPTRA_BASE_URL", "https://silverlake.maxoptra.com"
).rstrip("/")
MAXOPTRA_WIDGET_ENDPOINT = (
    f"{MAXOPTRA_BASE_URL}/api/v6/orders/{{reference}}/widget"
)
MAXOPTRA_POD_ENDPOINT = f"{MAXOPTRA_BASE_URL}/api/v6/orders/{{reference}}/pod"
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


def _normalise_key(key: str) -> str:
    """Return a normalised key for loose matching."""

    return re.sub(r"[^a-z0-9]", "", key.lower())


def _find_string_value(payload: Any, target_keys: set[str]) -> Optional[str]:
    """Recursively find the first string value whose key matches ``target_keys``."""

    if isinstance(payload, dict):
        for key, value in payload.items():
            if _normalise_key(key) in target_keys and isinstance(value, str):
                candidate = value.strip()
                if candidate:
                    return candidate
            candidate = _find_string_value(value, target_keys)
            if candidate:
                return candidate
    elif isinstance(payload, list):
        for item in payload:
            candidate = _find_string_value(item, target_keys)
            if candidate:
                return candidate
    return None


def _format_label(label: str) -> str:
    """Format a dictionary key for human readable display."""

    cleaned = re.sub(r"[_\-]+", " ", label).strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else label


def _coerce_data_uri(value: str) -> Optional[str]:
    """Return ``value`` as a usable data URI if it appears to be base64 encoded."""

    if value.startswith("data:"):
        return value

    compact = re.sub(r"\s+", "", value)
    if not compact:
        return None

    if re.fullmatch(r"[A-Za-z0-9+/=]+", compact) and len(compact) > 100:
        return f"data:image/png;base64,{compact}"

    return None


def _build_proof_of_delivery_context(payload: Any) -> Optional[dict[str, Any]]:
    """Extract displayable proof-of-delivery information from ``payload``."""

    if not isinstance(payload, dict):
        return None

    pod_body: Any = payload.get("pod") if isinstance(payload.get("pod"), dict) else payload

    signature_url = _find_string_value(
        pod_body,
        {
            "signatureurl",
            "signatureimageurl",
            "signaturelink",
            "signaturedownloadurl",
        },
    )
    signature_image = None

    if not signature_url or not signature_url.lower().startswith(("http://", "https://")):
        # Treat the found value as an inline image if it is not a URL.
        if signature_url:
            signature_image = _coerce_data_uri(signature_url)
            signature_url = None
        if not signature_image:
            signature_candidate = _find_string_value(
                pod_body,
                {
                    "signatureimage",
                    "signature",
                    "signaturedata",
                    "signaturepayload",
                    "proofimage",
                },
            )
            if signature_candidate:
                signature_image = _coerce_data_uri(signature_candidate) or signature_candidate

    signed_by = _find_string_value(
        pod_body,
        {
            "signedby",
            "signatory",
            "recipient",
            "recipientname",
            "receiver",
            "receivername",
        },
    )
    signed_at = _find_string_value(
        pod_body,
        {
            "signedat",
            "completedat",
            "completedtime",
            "timestamp",
            "deliveredat",
            "deliveredon",
            "datetime",
        },
    )
    status = _find_string_value(
        pod_body,
        {
            "status",
            "podstatus",
            "deliverystatus",
        },
    )

    detail_pairs: list[tuple[str, str]] = []
    seen_keys = set()

    for label, value in (
        ("Signed by", signed_by),
        ("Signed at", signed_at),
        ("Status", status),
    ):
        if value:
            detail_pairs.append((label, value))

    if isinstance(pod_body, dict):
        for key, value in pod_body.items():
            normalised_key = _normalise_key(key)
            if normalised_key in {
                "signatureurl",
                "signatureimage",
                "signature",
                "signaturedata",
                "signaturepayload",
                "proofimage",
                "signaturelink",
            }:
                continue
            if isinstance(value, (str, int, float)):
                formatted_label = _format_label(key)
                if formatted_label not in seen_keys:
                    seen_keys.add(formatted_label)
                    detail_pairs.append((formatted_label, str(value)))

    if not detail_pairs and not signature_url and not signature_image:
        return None

    return {
        "signature_url": signature_url,
        "signature_image": signature_image,
        "details": detail_pairs,
        "raw": payload,
    }


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


def _fetch_proof_of_delivery(order_reference: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Retrieve proof-of-delivery data for ``order_reference`` from Maxoptra."""

    if not MAXOPTRA_API_KEY:
        return None, "Proof of delivery is not available because the API key is missing."

    if not MAXOPTRA_BASE_URL:
        return None, (
            "Proof of delivery is not configured correctly. Please set the Maxoptra base URL."
        )

    encoded_reference = quote(order_reference, safe="")

    try:
        response = requests.get(
            MAXOPTRA_POD_ENDPOINT.format(reference=encoded_reference),
            headers={
                "Api-Key": MAXOPTRA_API_KEY,
                "Authorization": f"Bearer {MAXOPTRA_API_KEY}",
                "Accept": "application/json",
            },
            timeout=10,
        )
    except RequestException as exc:
        app.logger.warning(
            "Error fetching proof of delivery for %s: %s", order_reference, exc
        )
        detail = (
            "Unable to retrieve proof of delivery at this time. "
            f"(Technical detail: {exc})"
        )
        return None, detail

    if response.status_code == 404:
        return None, "No proof of delivery was found for this order yet."
    if response.status_code in {401, 403}:
        body_preview = response.text[:200]
        app.logger.warning(
            "Maxoptra returned %s for proof of delivery %s: %s",
            response.status_code,
            order_reference,
            body_preview,
        )
        return (
            None,
            "The tracking service rejected the proof-of-delivery request. Please contact support.",
        )
    if response.status_code >= 500:
        app.logger.warning(
            "Maxoptra returned %s for proof of delivery %s",
            response.status_code,
            order_reference,
        )
        return None, "The proof-of-delivery service is temporarily unavailable."
    if not response.ok:
        app.logger.warning(
            "Unexpected Maxoptra status %s for proof of delivery %s: %s",
            response.status_code,
            order_reference,
            response.text[:200],
        )
        return None, "Unexpected response from the proof-of-delivery service."

    try:
        payload = response.json()
    except ValueError:
        return None, "Received an invalid proof-of-delivery response from the tracking service."

    pod_context = _build_proof_of_delivery_context(payload)
    if pod_context:
        return pod_context, None

    return None, "Proof-of-delivery information is not currently available for this order."


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
    proof_of_delivery: Optional[dict[str, Any]] = None
    proof_of_delivery_error: Optional[str] = None
    
    if submission_attempted:
        if tracking_number:
            if TRACKING_PATTERN.fullmatch(tracking_number):
                tracking_url = f"{TRACKING_BASE_URL}{tracking_number}"
            else:
                error_message = (
                    "Tracking numbers may only contain letters and numbers. "
                    "Please try again."
                )
            if order_reference:
                proof_of_delivery, proof_of_delivery_error = _fetch_proof_of_delivery(
                    order_reference
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
            proof_of_delivery, proof_of_delivery_error = _fetch_proof_of_delivery(
                order_reference
            )
        else:
            error_message = "Please enter a tracking number or order reference."

    return {
        "tracking_number": tracking_number,
        "order_reference": order_reference,
        "tracking_url": tracking_url,
        "error_message": error_message,
        "reference_error_message": reference_error_message,
        "resolved_tracking_number": resolved_tracking_number,
        "proof_of_delivery": proof_of_delivery,
        "proof_of_delivery_error": proof_of_delivery_error,
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
