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
