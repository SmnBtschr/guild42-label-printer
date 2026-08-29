"""Printer simulator: captures rendered labels instead of feeding a Brother QL.

WHY THIS EXISTS: the real device is a Raspberry Pi next to a physical printer that only one
person has access to. Every integration (kiosk UI, REST API, guild42 event check-in) needs a
place to be exercised without burning label rolls — and without the Pi being online at all.
With ``PRINTER_SIM=1`` the app renders labels exactly as it would for the hardware (same
``create_label_image``, same pipeline up to the raster hand-off) and stores the result here;
``/sim`` shows them coming out of an animated printer.

WHY IN MEMORY: same reasoning as the API session — the simulator is a stateless container, a
restart wiping the history is a feature. The buffer is bounded so a runaway caller cannot fill
the container's memory with PNGs.

WHAT IS DELIBERATELY NOT SIMULATED: paper jams, an empty roll, USB errors. A successful
``accepted`` here means the same as on the hardware path — the raster was handed over, nothing
more. Callers must not be able to tell the difference from the response.
"""

import io
import itertools
import os
import threading
import time

from flask import Blueprint, abort, jsonify, render_template, request, send_file

sim_bp = Blueprint("sim", __name__, url_prefix="/sim")

_lock = threading.Lock()
_prints = []          # newest last: {"id", "name", "subtitle", "source", "ts", "png": bytes}
_ids = itertools.count(1)
MAX_PRINTS = 50       # ~50 labels x ~10 KB PNG — bounded by design

# WHY A RUN TOKEN: print ids restart at 1 with the process, so ``/sim/label/2.png`` stands for a
# different label after every restart. The no-store headers below fixed what the CDN made of
# that; they cannot fix what a browser already holds from an earlier run. Measured 29.08.2026 on
# printer-int.guild42.ch: a fresh print showed up with the correct footer (#2, 15:59:41) and the
# PICTURE of a test label printed two weeks earlier under the same id. The token goes into the
# image URL, so a new run asks for genuinely new addresses instead of hoping nobody cached the
# old ones.
_RUN = os.urandom(4).hex()


def sim_enabled() -> bool:
    """``PRINTER_SIM=1`` switches the hardware path off. Read per call, like ``.env`` values
    elsewhere in this project — no restart semantics to explain."""
    return os.environ.get("PRINTER_SIM", "") == "1"


def record_print(image, name: str, subtitle: str, source: str) -> int:
    """Store a rendered label as PNG. Returns the print id."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    entry = {
        "id": next(_ids),
        "name": name,
        "subtitle": subtitle,
        "source": source,           # "kiosk" or "api" — the sim page shows where it came from
        "ts": int(time.time()),
        "png": buf.getvalue(),
    }
    with _lock:
        _prints.append(entry)
        del _prints[:-MAX_PRINTS]
    return entry["id"]


def _public(entry: dict) -> dict:
    public = {k: entry[k] for k in ("id", "name", "subtitle", "source", "ts")}
    public["run"] = _RUN
    return public


@sim_bp.get("")
@sim_bp.get("/")
def sim_page():
    if not sim_enabled():
        abort(404)
    return render_template("sim.html")


@sim_bp.get("/prints")
def sim_prints():
    """Everything newer than ``since`` (a print id), oldest first — the page polls with the
    last id it has seen and animates only what is genuinely new."""
    if not sim_enabled():
        abort(404)
    try:
        since = int(request.args.get("since", 0))
    except ValueError:
        since = 0
    with _lock:
        fresh = [_public(e) for e in _prints if e["id"] > since]
        total = _prints[-1]["id"] if _prints else 0
    return jsonify({"prints": fresh, "latest": total})


@sim_bp.get("/session")
def sim_session():
    """State of the exclusive channel, for the panel on the page.

    The same numbers ``GET /api/v1/session`` returns, but without a token: this page is already
    unauthenticated and shows the printed labels themselves. Withholding "a session is running"
    from someone who can read the names on the labels would protect nothing.
    """
    if not sim_enabled():
        abort(404)
    from api import session_status
    return jsonify(session_status())


@sim_bp.post("/session/reset")
def sim_session_reset():
    """Release the channel from the simulator page.

    WHY THIS BUTTON IS HERE AND NOT ONLY IN THE API. The stuck channel is discovered by whoever
    is testing — at the guild settings page or here — and until now their only remedy was a
    restart of this container, which needs NAS access. The people who test check-in are not the
    people with a shell on the NAS. A remedy that only the operator can apply is, for a test
    system, no remedy.

    It carries no token, like the rest of this blueprint, and that is defensible for exactly the
    same reason: it exists only under ``PRINTER_SIM=1``, where there is no device to protect. On
    hardware ``sim_enabled()`` is false and this route answers 404 like any unknown path.
    """
    if not sim_enabled():
        abort(404)
    from api import force_release
    freigegeben = force_release()
    return jsonify({"ok": True, "released": freigegeben is not None,
                    "prints": (freigegeben or {}).get("prints", 0)})


@sim_bp.get("/label/<int:print_id>.png")
def sim_label(print_id: int):
    if not sim_enabled():
        abort(404)
    with _lock:
        entry = next((e for e in _prints if e["id"] == print_id), None)
    if entry is None:
        abort(404)
    # NO CACHING, and the reason is not academic: print ids restart at 1 whenever this process
    # does, and the process is meant to restart (no volume — a wiped history is a feature). A
    # long max_age turns that into wrong data at the far end: a CDN in front of the simulator
    # keeps serving label #1 of the *previous* run. Measured on 2026-08-16 against
    # printer-int.guild42.ch — `cf-cache-status: HIT`, `age: 5426`, three labels returned bytes
    # from a run 90 minutes earlier while the container itself served the correct ones.
    #
    # That breaks the one use this endpoint has: checking, over REST, what actually came out of
    # the printer. Correctness beats a cache hit for a debugging surface.
    antwort = send_file(io.BytesIO(entry["png"]), mimetype="image/png", max_age=0)
    antwort.headers["Cache-Control"] = "no-store, max-age=0"
    return antwort
