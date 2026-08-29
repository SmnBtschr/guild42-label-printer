"""Simulator (PRINTER_SIM=1): captured prints, the /sim routes, and the off switch.

The contract under test: with the switch ON, printing through the kiosk route and through the
API lands in the simulator buffer and the responses are byte-compatible with the hardware path;
with the switch OFF every /sim route answers 404 and nothing is recorded.
"""

import importlib
import os

import pytest


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINTER_SIM", "1")
    # api.py answers 404 without a token file - point it at an empty temp dir so the kiosk
    # route is what we exercise; the API path gets its own fixture below.
    monkeypatch.chdir(tmp_path)
    import simulator
    import app as app_module
    importlib.reload(simulator)
    importlib.reload(app_module)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_sim_disabled_hides_routes(client, monkeypatch):
    monkeypatch.setenv("PRINTER_SIM", "")
    assert client.get("/sim").status_code == 404
    assert client.get("/sim/prints").status_code == 404
    assert client.get("/sim/label/1.png").status_code == 404
    # Der Kanal-Reset gehoert dazu: Er darf auf einem Geraet nicht einmal existieren.
    assert client.get("/sim/session").status_code == 404
    assert client.post("/sim/session/reset").status_code == 404


def test_kanal_reset_auf_der_sim_seite(client):
    """The button on /sim releases a stuck channel — the point of the whole addition.

    Why it matters: the session lives in memory and the documented way out is a restart of the
    process. On the Pi that is a deliberate hurdle. On a container in a rack it means whoever
    tests check-in needs NAS access, and printer-int sat blocked from 21. to 29.08.2026 for
    exactly that reason.
    """
    import api
    api._session = {"digest": "egal", "started": 0, "prints": 3, "identity": None}

    zustand = client.get("/sim/session").get_json()
    assert zustand["connected"] is True and zustand["prints"] == 3

    antwort = client.post("/sim/session/reset")
    assert antwort.status_code == 200
    assert antwort.get_json() == {"ok": True, "released": True, "prints": 3}
    assert client.get("/sim/session").get_json()["connected"] is False


def test_kanal_reset_ohne_sitzung_meldet_frei(client):
    import api
    api._session = None
    assert client.post("/sim/session/reset").get_json() == {"ok": True, "released": False,
                                                            "prints": 0}


def test_kiosk_print_lands_in_simulator(client):
    r = client.post("/print", json={"name": "Martina", "subtitle": "Guild42.ch"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "name": "Martina"}  # byte-compatible with hardware

    lst = client.get("/sim/prints").get_json()
    assert lst["latest"] == 1
    assert lst["prints"][0]["name"] == "Martina"
    assert lst["prints"][0]["source"] == "kiosk"

    png = client.get("/sim/label/1.png")
    assert png.status_code == 200
    assert png.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_label_is_never_cached(client):
    """A cache in front of this endpoint serves labels from a *previous* run.

    Print ids restart at 1 with the process, and the process is meant to restart (no volume).
    With a long max_age a CDN keeps answering label #1 with bytes from an hour ago — measured
    on 2026-08-16 against printer-int.guild42.ch: `cf-cache-status: HIT`, `age: 5426`, three
    labels wrong while the container itself served the right ones. This endpoint exists to
    check what came out of the printer; caching defeats exactly that.
    """
    client.post("/print", json={"name": "Nocache"})
    antwort = client.get("/sim/label/1.png")

    assert antwort.status_code == 200
    steuerung = antwort.headers.get("Cache-Control", "")
    assert "no-store" in steuerung, f"Cache-Control ohne no-store: {steuerung!r}"
    assert "max-age=31536000" not in steuerung


def test_long_name_stays_inside_the_label(client):
    """A name may be longer than the old 15 characters — it must not run off the paper.

    guild42 makes the limit configurable per tenant (29.08.2026), so the renderer can no longer
    lean on "15 characters never overflow". It measures instead: the text is scaled down until it
    fits between the margins.
    """
    import app as kiosk
    from PIL import ImageDraw

    lang = "Wolfgang Amadeus Hörbiger"          # 25 characters, wide glyphs
    bild = kiosk.create_label_image(lang, "https://guild42.ch")
    zeichner = ImageDraw.Draw(bild)
    schrift = kiosk._fitting_font(zeichner, lang, kiosk.FONT_BOLD, 80, 34, 696 - 40)

    assert zeichner.textlength(lang, font=schrift) <= 696 - 40
    # POSITIVKONTROLLE: der ungebremste 80px-Satz waere tatsaechlich zu breit gewesen — ohne das
    # belegt die Zeile darueber nur, dass irgendetwas gemessen wurde.
    gross = kiosk.ImageFont.truetype(kiosk.FONT_BOLD, 80)
    assert zeichner.textlength(lang, font=gross) > 696 - 40
    # und ein kurzer Name behaelt die volle Groesse
    assert kiosk._fitting_font(zeichner, "Anna", kiosk.FONT_BOLD, 80, 34, 696 - 40).size == 80


def test_max_name_kommt_aus_der_umgebung(client, monkeypatch):
    """The limit is a setting now — guild42 may ask for more than 15 characters."""
    import app as kiosk

    assert kiosk.max_name() == 15                      # Vorgabe unveraendert
    monkeypatch.setenv("MAX_NAME", "25")
    assert kiosk.max_name() == 25
    monkeypatch.setenv("MAX_NAME", "999")              # ausserhalb des Rahmens -> gekappt
    assert kiosk.max_name() == 40
    monkeypatch.setenv("MAX_NAME", "keine Zahl")       # Unsinn faellt auf die Vorgabe zurueck
    assert kiosk.max_name() == 15


def test_label_url_is_unique_per_run(client):
    """The id alone is not a unique address: it restarts at 1 with the process.

    Daniel, 29.08.2026: a freshly printed label showed up in the tray with the right footer
    (#2, 15:59:41) and the picture of a test label printed two weeks earlier — the browser still
    had /sim/label/2.png from that older run. Headers alone cannot repair a cache entry that is
    already there, so every print carries a run token and the page puts it into the URL.
    """
    import simulator

    client.post("/print", json={"name": "Lauf"})
    eintrag = client.get("/sim/prints").get_json()["prints"][0]

    assert eintrag["run"] == simulator._RUN
    assert eintrag["run"], "leerer Run-Token — die Adresse waere wieder nur die id"
    # GEGENPROBE: ein neuer Prozess erzeugt einen anderen Token, sonst bringt er nichts.
    assert simulator.os.urandom(4).hex() != simulator._RUN
    # und die Seite baut die Adresse auch wirklich damit
    seite = client.get("/sim").data
    assert b"labelUrl" in seite and b"p.run" in seite


def test_label_belongs_to_its_id(client):
    """Two prints, two different images — the id must select, not the order of the request."""
    client.post("/print", json={"name": "Erste"})
    client.post("/print", json={"name": "Zweite"})

    eins = client.get("/sim/label/1.png").data
    zwei = client.get("/sim/label/2.png").data

    assert eins[:8] == b"\x89PNG\r\n\x1a\n" and zwei[:8] == b"\x89PNG\r\n\x1a\n"
    assert eins != zwei, "beide ids liefern dasselbe Bild — die Zuordnung greift nicht"


def test_prints_since_filters(client):
    client.post("/print", json={"name": "A"})
    client.post("/print", json={"name": "B"})
    lst = client.get("/sim/prints?since=1").get_json()
    assert [p["name"] for p in lst["prints"]] == ["B"]
    assert lst["latest"] == 2


def test_sim_page_and_button(client):
    assert b"Label Printer Simulator" in client.get("/sim").data
    assert b"/sim" in client.get("/").data          # kiosk shows the simulator link


def test_buffer_is_bounded(client):
    import simulator
    for i in range(simulator.MAX_PRINTS + 5):
        client.post("/print", json={"name": f"N{i}"})
    lst = client.get("/sim/prints").get_json()
    assert len(lst["prints"]) == simulator.MAX_PRINTS
    # the oldest entries are gone, their PNGs answer 404
    assert client.get("/sim/label/1.png").status_code == 404
