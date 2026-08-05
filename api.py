"""Optional REST API for triggering label prints from other systems.

WHY A SEPARATE FILE: the kiosk web UI in ``app.py`` stays byte-identical. This module is a Flask
blueprint that ``app.py`` registers in two lines. If no token file exists the API answers 404 on
every route, so an installation that does not want it behaves exactly like before.

WHY TOKENS IN A FILE: this project runs on a Raspberry Pi behind a tunnel, next to a printer.
A database would be a heavier dependency than the whole application. The token file sits beside
``.env`` and follows the same "read it when you need it" style the app already uses for
``DEFAULT_SUBTITLE`` — which also makes revocation immediate: delete the line, no restart.

WHAT IS DELIBERATELY NOT PROMISED: a successful response means the raster was handed to the
printer, not that a label came out. The Brother backend returns once the device accepted the
data; paper jams, an empty roll or a label peeling off happen afterwards and are invisible from
here. The response says ``accepted`` for exactly that reason — a caller that reports "printed"
to a user would be lying on our behalf.
"""

import hashlib
import hmac
import logging
import os

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

# Same limits as the web UI in app.py — the API must not be able to produce labels the kiosk
# cannot, otherwise the two paths drift apart.
MAX_NAME = 40
MAX_SUBTITLE = 50


def _env(key: str, default: str = "") -> str:
    """Read a value from .env, falling back to the process environment.

    Mirrors ``get_default_subtitle()`` in app.py rather than adding python-dotenv: one more
    dependency for eight lines is a poor trade in a project this size.
    """
    try:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        with open(env_path) as handle:
            for line in handle:
                line = line.strip()
                if line.startswith(f"{key}=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get(key, default)


def token_file() -> str:
    """Path of the token file. Override with ``API_TOKENS_FILE`` in .env."""
    configured = _env("API_TOKENS_FILE")
    if configured:
        return configured
    return os.path.join(os.path.dirname(__file__), "api_tokens.txt")


def api_enabled() -> bool:
    """The API exists only if someone created a token file. No file, no API."""
    return os.path.isfile(token_file())


def _known_hashes() -> dict:
    """``{sha256hex: label}`` from the token file.

    Read per request on purpose: deleting a line revokes a token immediately, without a restart.
    At kiosk load this costs nothing, and "revocation needs a service restart" is the kind of
    footnote that turns into a stale token six months later.
    """
    hashes = {}
    try:
        with open(token_file()) as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                label, _, digest = line.partition(":")
                digest = digest.strip().lower()
                if len(digest) == 64:
                    hashes[digest] = label.strip() or "unnamed"
    except Exception as exc:  # unreadable file must not authenticate anyone
        log.warning("api: cannot read token file: %s", exc)
    return hashes


def authenticate() -> str:
    """Return the label of the presenting token, or None.

    The comparison runs over every known hash with ``hmac.compare_digest`` instead of a dict
    lookup: a plain lookup leaks through timing which prefix was right. That is close to
    irrelevant for 256-bit tokens, but it costs nothing here.
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    presented = hashlib.sha256(header[7:].strip().encode()).hexdigest()
    for digest, label in _known_hashes().items():
        if hmac.compare_digest(presented, digest):
            return label
    return None


def _limits() -> tuple:
    """Rate limits, overridable in .env. Material costs money — this is not academic."""
    return _env("API_RATE_LIMIT", "10/minute"), _env("API_RATE_LIMIT_GLOBAL", "30/minute")


def register_limits(limiter):
    """Attach rate limits. Called by app.py when Flask-Limiter is available."""
    per_token, overall = _limits()
    limiter.limit(per_token, key_func=lambda: request.headers.get("Authorization", "anon"))(api_bp)
    limiter.limit(overall)(api_bp)


@api_bp.before_request
def _guard():
    """One place for "is the API on" and "is the caller allowed" — every route is protected."""
    if not api_enabled():
        # Not 401: an installation without a token file should look like the API never existed.
        return jsonify({"error": "not_found"}), 404
    label = authenticate()
    if label is None:
        # No detail on WHY it failed (missing, malformed, unknown) — that only helps an attacker.
        # The token itself is never logged.
        log.info("api: rejected request from %s", request.remote_addr)
        return jsonify({"error": "unauthorized"}), 401
    request.environ["api.token_label"] = label
    return None


@api_bp.get("/health")
def health():
    """Can we reach the printer device at all? Lets a caller check before promising anything."""
    from app import PRINTER_URI

    ready = False
    try:
        # os.open without O_CREAT, not open(..., "wb"): the builtin CREATES the file when it is
        # missing, so the check would silently produce its own device node and always report
        # "ready". Caught by the test on the first run.
        handle = os.open(PRINTER_URI, os.O_WRONLY)
        os.close(handle)
        ready = True
    except Exception as exc:
        log.info("api: printer not available: %s", exc)
    return jsonify({"printer": "ready" if ready else "unavailable"}), (200 if ready else 503)


@api_bp.post("/print")
def print_label():
    """Render a label and hand it to the printer.

    Returns ``accepted``, never ``printed`` — see the module docstring.
    """
    from app import LABEL_SIZE, PRINTER_MODEL, PRINTER_URI, create_label_image, get_default_subtitle

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:MAX_NAME]
    subtitle = (data.get("subtitle") or get_default_subtitle()).strip()[:MAX_SUBTITLE]
    if not name:
        return jsonify({"error": "name_required"}), 400

    if not os.path.exists(PRINTER_URI):
        # Fail fast instead of letting the backend block on a device that is not there.
        log.warning("api: printer device %s missing", PRINTER_URI)
        return jsonify({"error": "printer_unavailable"}), 503

    try:
        from brother_ql.backends.helpers import send
        from brother_ql.conversion import convert
        from brother_ql.raster import BrotherQLRaster

        image = create_label_image(name, subtitle)
        raster = BrotherQLRaster(PRINTER_MODEL)
        raster.exception_on_warning = False
        convert(qlr=raster, images=[image], label=LABEL_SIZE, rotate="auto", dpi_600=False)
        send(
            instructions=raster.data,
            printer_identifier=PRINTER_URI,
            backend_identifier="linux_kernel",
        )
    except Exception as exc:
        # Details go to the log, never into the response: the message can carry device paths and
        # internals. Same reasoning as the fix applied to /print in app.py.
        log.exception("api: print failed for token '%s': %s",
                      request.environ.get("api.token_label", "?"), exc)
        return jsonify({"error": "print_failed"}), 500

    log.info("api: accepted print for '%s' via token '%s'",
             name, request.environ.get("api.token_label", "?"))
    return jsonify({"ok": True, "accepted": True, "name": name}), 200
