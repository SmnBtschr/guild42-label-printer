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
    # The rate limit counter lives in memory and survives between tests, so without this reset
    # the suite would spend its own budget and later tests would fail with 429 for no reason.
    if kiosk.limiter is not None:
        kiosk.limiter.reset()
    # Same for the session: it is module state on purpose (it must die with the process), which
    # means one test's session would leak into the next.
    api._session = None
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
    """Both paths must produce the same label — otherwise they drift apart.

    Deliberately measured against ``app.MAX_NAME`` and not against a number written here: this
    test held the number 40 while the kiosk moved to 12 and then to 15, and it stayed green the
    whole time. A test that carries its own copy of the constraint proves the copy, not the
    constraint.
    """
    import app as kiosk

    client, tokens, _ = umgebung
    _mit_token(tokens)
    with mock.patch("brother_ql.backends.helpers.send"):
        antwort = client.post("/api/v1/print", json={"name": "A" * 100},
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert len(antwort.get_json()["name"]) == kiosk.MAX_NAME

    # Gegenprobe ueber den Kiosk-Weg: beide kuerzen gleich, nicht nur beide irgendwie.
    with mock.patch("brother_ql.backends.helpers.send"):
        ueber_kiosk = client.post("/print", json={"name": "A" * 100})
    assert ueber_kiosk.get_json()["name"] == antwort.get_json()["name"]


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


def test_rate_limit_greift_beim_elften_druck(umgebung):
    """README.md promises "10/minute per token" and a 429 - this proves the promise is kept.

    Until now register_limits() had no caller: Flask-Limiter was installed, the limits were
    defined, and nothing attached them. The endpoint was uncapped while the documentation said
    otherwise.
    """
    client, tokens, _ = umgebung
    _mit_token(tokens)
    kopf = {"Authorization": f"Bearer {TOKEN}"}
    with mock.patch("brother_ql.backends.helpers.send"):
        codes = [client.post("/api/v1/print", json={"name": "Anna"}, headers=kopf).status_code
                 for _ in range(11)]
    assert codes[:10] == [200] * 10, codes
    assert codes[10] == 429, codes


def test_ohne_tokendatei_bleibt_es_404_auch_unter_last(umgebung):
    """The limiter must not betray an API that is supposed to look absent.

    Flask-Limiter hooks into the app-wide before_request and therefore runs BEFORE the blueprint
    guard that returns 404. Without ``exempt_when`` in register_limits() the eleventh probe would
    answer 429 - and that is an admission that something is there. Fifteen calls, all 404.
    """
    client, tokens, _ = umgebung
    assert not tokens.exists()
    codes = {client.post("/api/v1/print", json={"name": "Anna"},
                         headers={"Authorization": f"Bearer {TOKEN}"}).status_code
             for _ in range(15)}
    assert codes == {404}, codes


def test_der_bestehende_kiosk_weg_bleibt_unberuehrt(umgebung):
    """The whole point of the blueprint: /print keeps working exactly as before, no token."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    with mock.patch("brother_ql.backends.helpers.send"):
        antwort = client.post("/print", json={"name": "Anna"})
    assert antwort.status_code == 200
    assert antwort.get_json()["ok"] is True


# ── Exclusive session (onboarding / offboarding) ─────────────────────────────────────────────

KOPF = {"Authorization": f"Bearer {TOKEN}"}


def _onboard(client):
    antwort = client.post("/api/v1/session", headers=KOPF)
    return antwort, (antwort.get_json() or {}).get("token")


def test_onboarding_liefert_ein_token(umgebung):
    client, tokens, _ = umgebung
    _mit_token(tokens)
    antwort, session_token = _onboard(client)
    assert antwort.status_code == 201
    assert session_token and len(session_token) >= 32
    # The status endpoint must never hand the value back out.
    zustand = client.get("/api/v1/session", headers=KOPF).get_json()
    assert zustand["connected"] is True and zustand["prints"] == 0
    assert session_token not in client.get("/api/v1/session", headers=KOPF).get_data(as_text=True)


def test_zweites_onboarding_wird_abgewiesen(umgebung):
    """The core of the mechanism: refused, not silently overwritten - overwriting IS the hijack."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    _, erstes = _onboard(client)
    zweite_antwort, _ = _onboard(client)
    assert zweite_antwort.status_code == 409
    assert zweite_antwort.get_json()["error"] == "session_active"
    # And the first session is untouched - it still prints.
    with mock.patch("brother_ql.backends.helpers.send"):
        weiter = client.post("/api/v1/print", json={"name": "Anna"},
                             headers={**KOPF, "X-Session-Token": erstes})
    assert weiter.status_code == 200


def test_onboarding_braucht_das_statische_token(umgebung):
    """Onboarding stays behind the guard - the device is reachable from any network."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    assert client.post("/api/v1/session").status_code == 401
    assert client.post("/api/v1/session",
                       headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_drucken_ohne_session_token_wird_abgewiesen(umgebung):
    client, tokens, _ = umgebung
    _mit_token(tokens)
    _onboard(client)
    antwort = client.post("/api/v1/print", json={"name": "Anna"}, headers=KOPF)
    assert antwort.status_code == 403
    assert antwort.get_json()["error"] == "session_token_invalid"


def test_drucken_mit_falschem_session_token_wird_abgewiesen(umgebung):
    client, tokens, _ = umgebung
    _mit_token(tokens)
    _onboard(client)
    antwort = client.post("/api/v1/print", json={"name": "Anna"},
                          headers={**KOPF, "X-Session-Token": "nicht-das-richtige-token"})
    assert antwort.status_code == 403


def test_nach_offboarding_ist_onboarding_wieder_moeglich(umgebung):
    client, tokens, _ = umgebung
    _mit_token(tokens)
    _, erstes = _onboard(client)
    weg = client.delete("/api/v1/session", headers={**KOPF, "X-Session-Token": erstes})
    assert weg.status_code == 200
    assert client.get("/api/v1/session", headers=KOPF).get_json()["connected"] is False
    zweite_antwort, zweites = _onboard(client)
    assert zweite_antwort.status_code == 201
    assert zweites and zweites != erstes


def test_offboarding_nur_mit_dem_session_token(umgebung):
    """No admin override on purpose (decision 07.08.2026) - otherwise it bypasses the mechanism."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    _onboard(client)
    assert client.delete("/api/v1/session", headers=KOPF).status_code == 403
    assert client.delete("/api/v1/session",
                         headers={**KOPF, "X-Session-Token": "falsch"}).status_code == 403
    assert client.get("/api/v1/session", headers=KOPF).get_json()["connected"] is True


def test_reset_gibt_es_auf_hardware_nicht(umgebung):
    """The rejected admin override must not sneak in through the back door.

    Without PRINTER_SIM the route answers 404 like an unknown path — not 403, which would admit
    it exists — and the running session is untouched afterwards.
    """
    client, tokens, _ = umgebung
    _mit_token(tokens)
    _onboard(client)
    antwort = client.post("/api/v1/session/reset", headers=KOPF)
    assert antwort.status_code == 404
    assert antwort.get_json() == {"error": "not_found"}
    assert client.get("/api/v1/session", headers=KOPF).get_json()["connected"] is True


def test_reset_gibt_den_kanal_in_der_simulation_frei(umgebung, monkeypatch):
    client, tokens, _ = umgebung
    _mit_token(tokens)
    _onboard(client)
    monkeypatch.setenv("PRINTER_SIM", "1")

    antwort = client.post("/api/v1/session/reset", headers=KOPF)

    assert antwort.status_code == 200
    assert antwort.get_json()["released"] is True
    assert client.get("/api/v1/session", headers=KOPF).get_json()["connected"] is False
    # Und danach ist der Kanal wirklich frei, nicht nur laut Auskunft.
    zweite, _ = _onboard(client)
    assert zweite.status_code == 201


def test_reset_ohne_laufende_sitzung_ist_kein_fehler(umgebung, monkeypatch):
    """"The channel is free" is what the caller wanted — a 404 would send them hunting."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    monkeypatch.setenv("PRINTER_SIM", "1")

    antwort = client.post("/api/v1/session/reset", headers=KOPF)

    assert antwort.status_code == 200
    assert antwort.get_json() == {"ok": True, "released": False}


def test_reset_braucht_trotz_simulation_einen_token(umgebung, monkeypatch):
    """Simulation is not the same as public: the blueprint guard still applies."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    _onboard(client)
    monkeypatch.setenv("PRINTER_SIM", "1")

    assert client.post("/api/v1/session/reset").status_code == 401
    assert client.get("/api/v1/session", headers=KOPF).get_json()["connected"] is True


def test_druckzaehler_zaehlt_nur_angenommene_drucke(umgebung):
    client, tokens, drucker = umgebung
    _mit_token(tokens)
    _, session_token = _onboard(client)
    kopf = {**KOPF, "X-Session-Token": session_token}
    with mock.patch("brother_ql.backends.helpers.send"):
        client.post("/api/v1/print", json={"name": "Anna"}, headers=kopf)
        client.post("/api/v1/print", json={"name": "Bea"}, headers=kopf)
    drucker.unlink()  # printer gone -> 503, must NOT count
    assert client.post("/api/v1/print", json={"name": "Cem"}, headers=kopf).status_code == 503
    assert client.get("/api/v1/session", headers=KOPF).get_json()["prints"] == 2


def test_ohne_session_druckt_der_statische_token_wie_bisher(umgebung):
    """Backwards compatible: the exclusivity starts with the first onboarding, not before."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    with mock.patch("brother_ql.backends.helpers.send"):
        assert client.post("/api/v1/print", json={"name": "Anna"},
                           headers=KOPF).status_code == 200


def test_kiosk_statuszeile_zeigt_die_session_ohne_token(umgebung):
    """The kiosk page has no token, so its status endpoint sits outside the API guard."""
    client, tokens, _ = umgebung
    _mit_token(tokens)
    aus = client.get("/session-status").get_json()
    assert aus == {"enabled": True, "connected": False, "prints": 0}
    _, session_token = _onboard(client)
    ein = client.get("/session-status").get_json()
    assert ein["connected"] is True
    assert session_token not in client.get("/session-status").get_data(as_text=True)


def test_ohne_tokendatei_meldet_die_statuszeile_nichts(umgebung):
    """No token file -> no API -> the kiosk shows no status line at all."""
    client, tokens, _ = umgebung
    assert not tokens.exists()
    assert client.get("/session-status").get_json() == {"enabled": False}
