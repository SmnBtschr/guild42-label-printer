"""Tests for the optional REST API (api.py).

No printer is involved: ``send()`` is mocked and the device path points at a temporary file.
Nothing here talks to hardware, so the suite runs on any machine — including CI.

The four things worth proving are the ones that would be expensive to get wrong:
  * without a token file the API does not exist at all (the promise that makes this addition safe
    for installations that do not want it),
  * a wrong or missing token gets in nowhere,
  * a revoked token stops working immediately, without a restart,
  * a failing printer never leaks internals into the response.
"""

import hashlib
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOKEN = "test-token-not-a-real-secret"
TOKEN_HASH = hashlib.sha256(TOKEN.encode()).hexdigest()


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    """A client plus the paths the API reads, all inside tmp_path."""
    import api
    import app as kiosk

    tokens = tmp_path / "api_tokens.txt"
    drucker = tmp_path / "lp0"
    drucker.write_bytes(b"")

    monkeypatch.setattr(api, "token_file", lambda: str(tokens))
    monkeypatch.setattr(kiosk, "PRINTER_URI", str(drucker))
    kiosk.app.config.update(TESTING=True)
    return kiosk.app.test_client(), tokens, drucker


def _mit_token(tokens, label="test"):
    tokens.write_text(f"# comment line\n{label}:{TOKEN_HASH}\n", encoding="utf-8")


def test_ohne_tokendatei_gibt_es_die_api_nicht(umgebung):
    """The central promise: no token file, no API — not even a 401 that hints it exists."""
    client, tokens, _ = umgebung
    assert not tokens.exists()
    antwort = client.post("/api/v1/print", json={"name": "Anna"})
    assert antwort.status_code == 404


def test_ohne_token_kein_zugang(umgebung):
    client, tokens, _ = umgebung
    _mit_token(tokens)
    assert client.post("/api/v1/print", json={"name": "Anna"}).status_code == 401


def test_falscher_token_kein_zugang(umgebung):
    client, tokens, _ = umgebung
    _mit_token(tokens)
    antwort = client.post("/api/v1/print", json={"name": "Anna"},
                          headers={"Authorization": "Bearer wrong"})
    assert antwort.status_code == 401
    # The answer must not say WHY it failed.
    assert antwort.get_json() == {"error": "unauthorized"}


def test_gueltiger_token_druckt(umgebung):
    client, tokens, _ = umgebung
    _mit_token(tokens)
    with mock.patch("brother_ql.backends.helpers.send") as send:
        antwort = client.post("/api/v1/print", json={"name": "Anna"},
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert antwort.status_code == 200
    assert send.called
    daten = antwort.get_json()
    # "accepted", not "printed": we know the device took the data, not that a label came out.
    assert daten["accepted"] is True
    assert daten["name"] == "Anna"


def test_widerruf_wirkt_sofort_ohne_neustart(umgebung):
    """Deleting the line must be enough — this is what makes the file approach acceptable."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    kopf = {"Authorization": f"Bearer {TOKEN}"}
    with mock.patch("brother_ql.backends.helpers.send"):
        assert client.post("/api/v1/print", json={"name": "Anna"}, headers=kopf).status_code == 200
    tokens.write_text("# revoked\n", encoding="utf-8")
    assert client.post("/api/v1/print", json={"name": "Anna"}, headers=kopf).status_code == 401


def test_name_ist_pflicht(umgebung):
    client, tokens, _ = umgebung
    _mit_token(tokens)
    antwort = client.post("/api/v1/print", json={"name": "   "},
                          headers={"Authorization": f"Bearer {TOKEN}"})
    assert antwort.status_code == 400
    assert antwort.get_json()["error"] == "name_required"


def test_name_wird_wie_im_kiosk_gekuerzt(umgebung):
    """Both paths must produce the same label — otherwise they drift apart."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    with mock.patch("brother_ql.backends.helpers.send"):
        antwort = client.post("/api/v1/print", json={"name": "A" * 100},
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert len(antwort.get_json()["name"]) == 40


def test_fehlendes_geraet_gibt_503(umgebung):
    client, tokens, drucker = umgebung
    _mit_token(tokens)
    drucker.unlink()
    antwort = client.post("/api/v1/print", json={"name": "Anna"},
                          headers={"Authorization": f"Bearer {TOKEN}"})
    assert antwort.status_code == 503
    assert antwort.get_json()["error"] == "printer_unavailable"


def test_druckfehler_verraet_keine_interna(umgebung):
    """A failing backend must not hand its message to the caller."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    with mock.patch("brother_ql.backends.helpers.send",
                    side_effect=RuntimeError("/dev/usb/lp0: secret internal detail")):
        antwort = client.post("/api/v1/print", json={"name": "Anna"},
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert antwort.status_code == 500
    assert antwort.get_json() == {"error": "print_failed"}
    assert "secret internal detail" not in antwort.get_data(as_text=True)


def test_health_meldet_den_druckerzustand(umgebung):
    client, tokens, drucker = umgebung
    _mit_token(tokens)
    kopf = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/v1/health", headers=kopf).get_json()["printer"] == "ready"
    drucker.unlink()
    antwort = client.get("/api/v1/health", headers=kopf)
    assert antwort.status_code == 503
    assert antwort.get_json()["printer"] == "unavailable"


def test_der_bestehende_kiosk_weg_bleibt_unberuehrt(umgebung):
    """The whole point of the blueprint: /print keeps working exactly as before, no token."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    with mock.patch("brother_ql.backends.helpers.send"):
        antwort = client.post("/print", json={"name": "Anna"})
    assert antwort.status_code == 200
    assert antwort.get_json()["ok"] is True
