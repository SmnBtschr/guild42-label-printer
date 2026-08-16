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
import secrets
import threading
import time

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

# ── Exclusive session ────────────────────────────────────────────────────────────────────────
#
# WHY IN MEMORY AND NOT IN A FILE: the session is meant to die with the process. A restart of the
# Pi is the only way out of a forgotten session - that is the deliberate design, not an oversight
# (no admin token, no expiry). Writing it to a file would keep a stale session alive across
# exactly the restart that is supposed to clear it.
#
# WHY ONLY THE HASH: same reasoning as the token file. The value is handed out once, on
# onboarding; from then on we only ever need to recognise it.
#
# WHY A LOCK: two onboarding requests arriving together must not both win. Without it the second
# caller could overwrite the first - which is precisely the hijack this mechanism exists to
# prevent, only through the back door of a race.
#
# WHY AN OPTIONAL IDENTITY: recognising the holder by the hash of a value means the holder has to
# REMEMBER that value. A caller that is redeployed daily cannot (guild42 loses it on every
# restart, and the documented way out is a restart of this Pi). If the presented token is a JWT
# signed by a key published at SESSION_JWKS_URL, we therefore remember WHO opened the session
# (iss+sub) instead of only WHAT was handed over - and the same WHO may take it over with a fresh
# token. Without SESSION_JWKS_URL nothing changes: identity stays None and only the digest counts.
_session_lock = threading.Lock()
_session = None  # {"digest": str, "started": float, "prints": int, "identity": dict | None}


def session_status() -> dict:
    """What the kiosk UI shows. Never contains the token."""
    with _session_lock:
        if _session is None:
            return {"connected": False, "prints": 0}
        return {"connected": True, "prints": _session["prints"],
                "since": int(_session["started"])}


def _signed_session_config() -> dict:
    """Where to fetch the caller's public keys, and what to expect in the token.

    Empty ``jwks_url`` switches the whole signed-token path off — the API then behaves exactly as
    before, which is the same "not configured, not there" rule the token file follows.
    """
    return {"jwks_url": _env("SESSION_JWKS_URL"),
            "issuer": _env("SESSION_JWT_ISSUER"),
            "audience": _env("SESSION_JWT_AUDIENCE", "guild42-label-printer")}


def _ausweis(token: str):
    """Identity proven by a signed token, or ``None``. Never raises — a broken issuer must not
    turn into a 500 on the print path."""
    config = _signed_session_config()
    if not config["jwks_url"] or not token:
        return None
    try:
        import session_jwt
        return session_jwt.verify(token, config["jwks_url"], config["issuer"], config["audience"])
    except Exception as problem:  # pragma: no cover - defence in depth
        log.warning("api: signed session token could not be checked: %s", problem)
        return None


def _selbe_identitaet(a, b) -> bool:
    """Same caller? Only iss+sub count — the token value and its jti change on every restart."""
    return bool(a) and bool(b) and a["iss"] == b["iss"] and a["sub"] == b["sub"]


def _session_matches() -> bool:
    """Does the caller present the token of the running session?

    Two ways in, and the second exists only for the restart case: either the presented value
    hashes to the stored digest (unchanged behaviour), or it is a valid signed token from the same
    identity that opened the session. A session opened with a static token has no identity, so no
    signature can take it over.
    """
    presented = request.headers.get("X-Session-Token", "").strip()
    if not presented:
        return False
    digest = hashlib.sha256(presented.encode()).hexdigest()
    with _session_lock:
        if _session is None:
            return False
        if hmac.compare_digest(digest, _session["digest"]):
            return True
        gespeichert = _session.get("identity")
    if not gespeichert:
        return False
    return _selbe_identitaet(_ausweis(presented), gespeichert)

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
    """Attach rate limits. Called by app.py when Flask-Limiter is available.

    ``exempt_when`` is not decoration: the limiter runs as an app-wide ``before_request``, so it
    fires BEFORE the blueprint guard below that answers 404 when there is no token file. Without
    the exemption an installation that never enabled the API would start answering 429 on the
    eleventh probe - and a 429 is an admission that something is listening. That would quietly
    break the promise this module is built on ("no token file, no API"). Skipping the limit costs
    nothing there: without a token file every request is refused anyway.
    """
    per_token, overall = _limits()
    aus = lambda: not api_enabled()  # noqa: E731 - one expression, named for the reader
    limiter.limit(per_token, key_func=lambda: request.headers.get("Authorization", "anon"),
                  exempt_when=aus)(api_bp)
    limiter.limit(overall, exempt_when=aus)(api_bp)


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


@api_bp.post("/session")
def onboard():
    """Onboarding: claim the printer for one caller.

    Deliberately NOT exempt from the guard above. An unauthenticated onboarding endpoint would
    mean "whoever reaches the address gets the session" - and this device is reachable from any
    network by design (README: "Accessible from any network via https://printer.guild42.ch").
    A single call from outside would then lock out the very system the session is for, with a
    trip to the Pi as the only remedy. Keeping it behind the existing token adds no second auth
    model: the static token from api_tokens.txt is the entry ticket, nothing more.

    A second onboarding is REFUSED, never silently overwritten - overwriting is the hijack.

    THE ONE EXCEPTION, and why it is not a hijack: if the running session was opened with a signed
    token and the new request presents a valid signature from the SAME identity (iss+sub), the
    session is handed over to the new token. That is not a second caller taking the printer - it
    is the same caller after a restart, proving it with a key we fetched from its own published
    JWK Set. A different identity, an invalid signature, or a session opened with a static token
    still gets 409.
    """
    data = request.get_json(silent=True) or {}
    gewuenscht = (data.get("token") or "").strip()
    if gewuenscht and len(gewuenscht) < 16:
        # A caller may bring its own token, but not a guessable one.
        return jsonify({"error": "token_too_short"}), 400
    token = gewuenscht or secrets.token_urlsafe(32)
    identitaet = _ausweis(gewuenscht) if gewuenscht else None
    digest = hashlib.sha256(token.encode()).hexdigest()

    global _session
    with _session_lock:
        if _session is not None:
            if not _selbe_identitaet(identitaet, _session.get("identity")):
                log.info("api: onboarding refused, session already running")
                return jsonify({"error": "session_active"}), 409
            # Same proven identity: keep the running session (counter and start time), swap the
            # token it is recognised by. Nothing is lost, and no restart of this Pi is needed.
            _session["digest"] = digest
            gedruckt = _session["prints"]
            log.info("api: session resumed by '%s' (same signed identity, %d prints so far)",
                     identitaet["sub"], gedruckt)
            return jsonify({"ok": True, "token": token, "resumed": True, "prints": gedruckt}), 200
        _session = {"digest": digest, "started": time.time(), "prints": 0,
                    "identity": identitaet}
    log.info("api: session onboarded via token '%s'%s",
             request.environ.get("api.token_label", "?"),
             " (signed identity %s)" % identitaet["sub"] if identitaet else "")
    # The only moment the value leaves this process.
    return jsonify({"ok": True, "token": token}), 201


@api_bp.delete("/session")
def offboard():
    """Offboarding: release the printer. Only the holder of the session token may do this.

    There is no admin override on purpose (decision 07.08.2026): an override without
    authentication would bypass the whole mechanism, and one with authentication would be the
    second auth model this design avoids. The way out of a forgotten session is a restart of the
    Pi - which is also why the session lives in memory.
    """
    global _session
    with _session_lock:
        if _session is None:
            return jsonify({"error": "no_session"}), 404
    if not _session_matches():
        # Same reticence as the guard: no hint whether the token was missing or wrong.
        log.info("api: offboarding rejected from %s", request.remote_addr)
        return jsonify({"error": "session_token_invalid"}), 403
    with _session_lock:
        gedruckt = _session["prints"]
        _session = None
    log.info("api: session offboarded after %d prints", gedruckt)
    return jsonify({"ok": True, "prints": gedruckt}), 200


@api_bp.get("/session")
def session_info():
    """Is a session running, and how much has it printed? No token value is ever returned."""
    return jsonify(session_status()), 200


@api_bp.get("/health")
def health():
    """Can we reach the printer device at all? Lets a caller check before promising anything."""
    from app import PRINTER_URI

    try:
        from simulator import sim_enabled
        if sim_enabled():
            # The simulator is always "ready" - there is no device that could be missing. The
            # distinct value lets a caller see it is talking to the simulation, without changing
            # the contract (200 = you may print).
            return jsonify({"printer": "simulated"}), 200
    except ImportError:
        pass

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

    # While a session is running the printer belongs to it: the session token has to come along,
    # in X-Session-Token. Without a session nothing changes - callers with a static token print
    # as before. That keeps the addition backwards compatible and still delivers what the
    # exclusivity is for: once someone has claimed the printer, nobody else prints on it.
    with _session_lock:
        session_laeuft = _session is not None
    if session_laeuft and not _session_matches():
        log.info("api: print rejected, session token missing or wrong (from %s)",
                 request.remote_addr)
        return jsonify({"error": "session_token_invalid"}), 403

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:MAX_NAME]
    subtitle = (data.get("subtitle") or get_default_subtitle()).strip()[:MAX_SUBTITLE]
    if not name:
        return jsonify({"error": "name_required"}), 400

    sim = False
    try:
        from simulator import sim_enabled, record_print
        sim = sim_enabled()
    except ImportError:
        pass

    if not sim and not os.path.exists(PRINTER_URI):
        # Fail fast instead of letting the backend block on a device that is not there.
        log.warning("api: printer device %s missing", PRINTER_URI)
        return jsonify({"error": "printer_unavailable"}), 503

    try:
        image = create_label_image(name, subtitle)
        if sim:
            # Same pipeline, different sink (see simulator.py). Session accounting and the
            # response below stay identical - a caller cannot tell simulation from hardware.
            record_print(image, name, subtitle, "api")
        else:
            from brother_ql.backends.helpers import send
            from brother_ql.conversion import convert
            from brother_ql.raster import BrotherQLRaster

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

    # Counted only after the printer took the data - the kiosk display should show labels, not
    # attempts.
    with _session_lock:
        if _session is not None:
            _session["prints"] += 1

    log.info("api: accepted print for '%s' via token '%s'",
             name, request.environ.get("api.token_label", "?"))
    return jsonify({"ok": True, "accepted": True, "name": name}), 200
