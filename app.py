from __future__ import annotations

import re
from typing import Optional

from flask import Flask, render_template, request

app = Flask(__name__)

TRACKING_BASE_URL = "https://orderstrack.com/"
TRACKING_PATTERN = re.compile(r"^[A-Za-z0-9]+$")


def _build_context(raw_tracking_number: str | None, *, submission_attempted: bool) -> dict[str, Optional[str]]:
    """Return template context for a potential tracking number submission."""

    tracking_number: str = raw_tracking_number.strip() if raw_tracking_number else ""
    tracking_url: Optional[str] = None
    error_message: Optional[str] = None

    if submission_attempted:
        if tracking_number:
            if TRACKING_PATTERN.fullmatch(tracking_number):
                tracking_url = f"{TRACKING_BASE_URL}{tracking_number}"
            else:
                error_message = (
                    "Tracking numbers may only contain letters and numbers. "
                    "Please try again."
                )
        else:
            error_message = "Please enter a tracking number."

    return {
        "tracking_number": tracking_number,
        "tracking_url": tracking_url,
        "error_message": error_message,
    }


@app.route("/", methods=["GET", "POST"])
def index():
    """Render the home page with an optional tracking URL."""

    if request.method == "POST":
        return render_template(
            "index.html",
            **_build_context(request.form.get("tracking_number"), submission_attempted=True),
        )

    return render_template("index.html", **_build_context(None, submission_attempted=False))


@app.route("/<tracking_number>", methods=["GET"])
def tracking_from_path(tracking_number: str):
    """Display the tracker when a tracking number is supplied in the path."""

    return render_template(
        "index.html",
        **_build_context(tracking_number, submission_attempted=True),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
