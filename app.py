from __future__ import annotations

import re
from typing import Optional

from flask import Flask, render_template, request

app = Flask(__name__)

TRACKING_BASE_URL = "https://orderstrack.com/"
TRACKING_PATTERN = re.compile(r"^[A-Za-z0-9]+$")


@app.route("/", methods=["GET", "POST"])
def index():
    """Render the home page with an optional tracking URL."""
    tracking_number: str = ""
    tracking_url: Optional[str] = None
    error_message: Optional[str] = None

    if request.method == "POST":
        tracking_number = request.form.get("tracking_number", "").strip()

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

    return render_template(
        "index.html",
        tracking_number=tracking_number,
        tracking_url=tracking_url,
        error_message=error_message,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
