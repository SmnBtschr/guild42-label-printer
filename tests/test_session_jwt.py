"""Tests for signed session tokens (session_jwt.py plus the takeover path in api.py).

WHY A SECOND TEST FILE: ``test_api.py`` proves the static-token behaviour and must keep proving it
unchanged. Everything here is about the case that static tokens cannot cover — the caller was
restarted and no longer knows its token.

NO NETWORK, NO CRYPTO LIBRARY: the JWK Set is served by a stub in place of ``urllib.request``, and
tokens are signed with the two RSA test key pairs below using ``pow()``. That is the same modular
exponentiation the verifier performs, so signing needs no dependency either.

THE TEST KEYS ARE PUBLIC ON PURPOSE. They are generated once for this file and used nowhere else;
a private exponent in a test file is only dangerous if someone mistakes it for a real one, so:
these two are worthless, and nothing in the project reads them.
"""

import base64
import hashlib
import json
import os
import sys
import time
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOKEN = "test-token-not-a-real-secret"
TOKEN_HASH = hashlib.sha256(TOKEN.encode()).hexdigest()

# Test key pair 1 (the legitimate caller) and 2 (a stranger holding a different key).
N1 = 17834363664169554561351691383411807185346161862328034655313117782455450499954713489413120247294598807718109495767051496954338944169661306447336034801592263171323517839342596953546171472083548079616536685499426736203686754735188904427767571982552094578286470131618715017877196807666824194181842621567231223162271007958139754514380128789996743585223175666581925282262339388858684498378395026876635205387097404780473867548310186887537372885731166025791469341007370502243686794013043128115891657788047193838365269482935947090409843532673181554970174474073465313256058382980882721486633224121928878008779285221561491211833
D1 = 4535126533793394747123100122835184287461837748395987938845549298922918508735451388647853204774221225109879210243157250446198875048590727415018671711947996732863757672025940301346166603109821482839455454662109896275425204251633286338113744463054792440826299158925365749583173221205905955538665154183570989663951247318824336385344038509184255863822696485947164328736043131263748489847529829388363369771250012722740328483295606023573371402418422725600190769070990437256763222218568750437490039031387359947168557466494432315487453184622997899752822540480605633505434601063109653991479932865371596736330504004272082117263
N2 = 29128955211516752745470012471183263090517095274718134596465993075933371170269076976893086308081698212312823735678867715263307690021468383652529426321378681159764968511945354202252976638775759056689636618439174911930786131567990049335137161355847671337156701515058463909392366175937156028197565071804773122037338919601793392061916213029088162122625729348277972593032409509312921248109530388528578769481034203181138603751499090323284886341485502455303975706989747506086654667737428391725589448384942247658028178493670430338190289240854396100784267706474302966309313914282116259025151739373183968242780221416129014398053
D2 = 208920091589761266337034254807026485196289880203434851124966716476798134984255392511036408329917918452273694245334408129587365194798659292449207512163851435232907332013404708013313549816901334287053698536423333061345056194870385668718680120167359015495480779418054720997017349358214710522909388752253300380393773909839627911900468619453559343780320773781551689677573392146480567950985929719722551637158114139818265146925716829827365348064561356522395339843498672868924922779311995418700280425820795850271260541083262775829969887335583369982211887177918405771104529676268895775239531193723026857422519733795624888613
E = 65537

JWKS_URL = "https://guild.example.invalid/.well-known/jwks.json"
SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def int_b64u(wert: int) -> str:
    return b64u(wert.to_bytes((wert.bit_length() + 7) // 8, "big"))


def jwk_thumbprint(n: int, e: int) -> str:
    """kid as RFC 7638 thumbprint — the same value plaintext-root publishes."""
    kanonisch = '{"e":"%s","kty":"RSA","n":"%s"}' % (int_b64u(e), int_b64u(n))
    return b64u(hashlib.sha256(kanonisch.encode()).digest())


def sign(header: dict, claims: dict, n: int = N1, d: int = D1) -> str:
    """A JWS compact token, signed with pow() — RSASSA-PKCS1-v1_5 over SHA-256."""
    signing_input = (b64u(json.dumps(header).encode()) + "." + b64u(json.dumps(claims).encode()))
    k = (n.bit_length() + 7) // 8
    digest_info = SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(signing_input.encode()).digest()
    em = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    signature = pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")
    return signing_input + "." + b64u(signature)


def ausweis(sub: str = "guild-checkin-desk", iss: str = "https://guild.example.invalid",
            aud: str = "guild42-label-printer", gueltig_sekunden: int = 900,
            n: int = N1, d: int = D1, alg: str = "RS256", jti: str = None) -> str:
    """A token as plaintext-root's signServiceToken() issues it."""
    jetzt = int(time.time())
    claims = {"sub": sub, "iss": iss, "aud": aud, "iat": jetzt,
              "exp": jetzt + gueltig_sekunden, "jti": jti or ("jti-%d" % jetzt),
              "token_use": "service"}
    return sign({"alg": alg, "typ": "JWT"}, claims, n, d)


def jwks_document(*schluessel) -> bytes:
    keys = [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": jwk_thumbprint(n, E),
             "n": int_b64u(n), "e": int_b64u(E)} for n in (schluessel or (N1,))]
    return json.dumps({"keys": keys}).encode()


class StubAntwort:
    """Minimal stand-in for the object urlopen() returns."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    """Client, token file, fake printer, and a JWK Set served without network."""
    import api
    import app as kiosk
    import session_jwt

    tokens = tmp_path / "api_tokens.txt"
    tokens.write_text("guild-desk:%s\n" % TOKEN_HASH)
    drucker = tmp_path / "lp0"
    drucker.write_bytes(b"")

    monkeypatch.setattr(api, "token_file", lambda: str(tokens))
    monkeypatch.setattr(kiosk, "PRINTER_URI", str(drucker))
    kiosk.app.config.update(TESTING=True)
    if kiosk.limiter is not None:
        kiosk.limiter.reset()
    api._session = None
    session_jwt.reset_cache()

    # Configuration normally read from .env.
    einstellungen = {"SESSION_JWKS_URL": JWKS_URL,
                     "SESSION_JWT_ISSUER": "https://guild.example.invalid",
                     "SESSION_JWT_AUDIENCE": "guild42-label-printer"}
    monkeypatch.setattr(api, "_env", lambda key, default="": einstellungen.get(key, default))

    veroeffentlicht = {"keys": jwks_document(N1)}
    monkeypatch.setattr(session_jwt.urllib.request, "urlopen",
                        lambda url, timeout=None: StubAntwort(veroeffentlicht["keys"]))

    with mock.patch("app.send"):
        yield {"client": kiosk.app.test_client(), "api": api, "session_jwt": session_jwt,
               "veroeffentlicht": veroeffentlicht, "einstellungen": einstellungen}
    api._session = None
    session_jwt.reset_cache()


def kopf():
    return {"Authorization": "Bearer " + TOKEN}


def onboard(client, token=None):
    return client.post("/api/v1/session", headers=kopf(), json={"token": token} if token else {})


def drucke(client, session_token):
    return client.post("/api/v1/print", headers={**kopf(), "X-Session-Token": session_token},
                       json={"name": "Anna Beispiel"})


# ── The case this whole change exists for ───────────────────────────────────────────────────────

def test_nach_einem_neustart_uebernimmt_derselbe_aussteller_die_session(umgebung):
    client = umgebung["client"]
    erster = ausweis(jti="vor-dem-neustart")
    assert onboard(client, erster).status_code == 201
    assert drucke(client, erster).status_code == 200

    # guild is redeployed: new process, new token, same key and same identity.
    zweiter = ausweis(jti="nach-dem-neustart")
    assert zweiter != erster
    antwort = onboard(client, zweiter)

    assert antwort.status_code == 200, "Derselbe Aussteller muss seine Session uebernehmen koennen"
    assert antwort.get_json()["resumed"] is True
    assert antwort.get_json()["prints"] == 1, "Der Druckzaehler der laufenden Session bleibt erhalten"
    assert drucke(client, zweiter).status_code == 200, "Mit dem neuen Ausweis muss gedruckt werden koennen"


def test_erneuerter_ausweis_druckt_auch_ohne_erneutes_onboarding(umgebung):
    """Ein neu signierter Ausweis derselben Identitaet wird beim Druck sofort anerkannt."""
    client = umgebung["client"]
    assert onboard(client, ausweis(jti="alt")).status_code == 201

    assert drucke(client, ausweis(jti="neu")).status_code == 200


def test_offboarding_gelingt_mit_erneuertem_ausweis(umgebung):
    """Genau der Fall, der bisher nur mit einem Neustart des Pi zu loesen war."""
    client = umgebung["client"]
    onboard(client, ausweis(jti="alt"))

    antwort = client.delete("/api/v1/session",
                            headers={**kopf(), "X-Session-Token": ausweis(jti="neu")})

    assert antwort.status_code == 200
    assert client.get("/api/v1/session", headers=kopf()).get_json()["connected"] is False


# ── Everything that must NOT work ───────────────────────────────────────────────────────────────

def test_fremde_identitaet_wird_weiterhin_abgewiesen(umgebung):
    client = umgebung["client"]
    onboard(client, ausweis(sub="guild-checkin-desk"))

    antwort = onboard(client, ausweis(sub="ein-anderes-desk"))

    assert antwort.status_code == 409, "Ein anderer sub ist ein anderer Aufrufer — kein Uebernahmerecht"


def test_fremder_schluessel_wird_abgewiesen(umgebung):
    """Gueltig aufgebautes Token, aber mit einem Schluessel signiert, der nicht veroeffentlicht ist."""
    client = umgebung["client"]
    onboard(client, ausweis())

    antwort = onboard(client, ausweis(n=N2, d=D2))

    assert antwort.status_code == 409


def test_manipulierte_signatur_wird_abgewiesen(umgebung):
    client = umgebung["client"]
    echt = ausweis()
    onboard(client, echt)

    teile = echt.split(".")
    gefaelscht = teile[0] + "." + teile[1] + "." + teile[2][:-4] + ("AAAA" if not teile[2].endswith("AAAA") else "BBBB")
    assert onboard(client, gefaelscht).status_code == 409


def test_veraenderte_claims_werden_abgewiesen(umgebung):
    """Wer den sub im Rumpf austauscht, aendert damit die signierte Nachricht."""
    client = umgebung["client"]
    onboard(client, ausweis(sub="guild-checkin-desk"))

    echt = ausweis(sub="guild-checkin-desk")
    teile = echt.split(".")
    manipuliert = json.dumps({"sub": "guild-checkin-desk", "iss": "https://guild.example.invalid",
                              "aud": "guild42-label-printer", "exp": int(time.time()) + 900})
    gefaelscht = teile[0] + "." + b64u(manipuliert.encode()) + "." + teile[2]

    assert onboard(client, gefaelscht).status_code == 409


def test_alg_none_wird_abgewiesen(umgebung):
    """alg=none wuerde die Signaturpruefung ueberspringen — der Klassiker."""
    client = umgebung["client"]
    onboard(client, ausweis())

    jetzt = int(time.time())
    claims = {"sub": "guild-checkin-desk", "iss": "https://guild.example.invalid",
              "aud": "guild42-label-printer", "exp": jetzt + 900}
    ohne_signatur = b64u(json.dumps({"alg": "none", "typ": "JWT"}).encode()) + "." \
        + b64u(json.dumps(claims).encode()) + "."

    assert onboard(client, ohne_signatur + "x").status_code == 409


def test_hs256_mit_dem_oeffentlichen_schluessel_wird_abgewiesen(umgebung):
    """alg-confusion: der veroeffentlichte Schluessel darf nie als HMAC-Geheimnis dienen."""
    import hmac as hmac_modul
    client = umgebung["client"]
    onboard(client, ausweis())

    jetzt = int(time.time())
    header = b64u(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    rumpf = b64u(json.dumps({"sub": "guild-checkin-desk", "iss": "https://guild.example.invalid",
                             "aud": "guild42-label-printer", "exp": jetzt + 900}).encode())
    geheimnis = int_b64u(N1).encode()
    signatur = b64u(hmac_modul.new(geheimnis, (header + "." + rumpf).encode(), hashlib.sha256).digest())

    assert onboard(client, header + "." + rumpf + "." + signatur).status_code == 409


@pytest.mark.parametrize("behauptet", ["none", "HS256", "RS512"])
def test_header_mit_anderer_signaturart_wird_abgewiesen_trotz_gueltiger_rsa_signatur(umgebung, behauptet):
    """Die Zusage "nur RS256" wird eingehalten, auch wenn die RSA-Signatur stimmt.

    Die beiden Tests darueber (alg=none, HS256) scheitern in Wahrheit schon an der Signaturlaenge —
    sie belegen die alg-Pruefung also NICHT. Eine Mutationsprobe hat das gezeigt: mit entfernter
    alg-Pruefung blieben alle 41 Tests gruen. Hier steht deshalb der Fall, den nur sie faengt: ein
    Token, dessen Header eine andere Signaturart behauptet, dessen RSA-Signatur aber gueltig ist.
    Wer dem Header spaeter einmal folgt, faellt sonst still in die alg-confusion.
    """
    client = umgebung["client"]
    onboard(client, ausweis())

    jetzt = int(time.time())
    getarnt = sign({"alg": behauptet, "typ": "JWT"},
                   {"sub": "guild-checkin-desk", "iss": "https://guild.example.invalid",
                    "aud": "guild42-label-printer", "exp": jetzt + 900})

    assert onboard(client, getarnt).status_code == 409


def test_abgelaufener_ausweis_wird_abgewiesen(umgebung):
    client = umgebung["client"]
    onboard(client, ausweis(jti="alt"))

    # Auch die Kulanz (60 s) ist ueberschritten.
    assert onboard(client, ausweis(jti="abgelaufen", gueltig_sekunden=-120)).status_code == 409


def test_ausweis_ohne_ablauf_wird_abgewiesen(umgebung):
    client = umgebung["client"]
    onboard(client, ausweis())

    ohne_exp = sign({"alg": "RS256", "typ": "JWT"},
                    {"sub": "guild-checkin-desk", "iss": "https://guild.example.invalid",
                     "aud": "guild42-label-printer"})
    assert onboard(client, ohne_exp).status_code == 409, "Ohne exp waere der Ausweis unbegrenzt gueltig"


def test_ausweis_fuer_ein_anderes_geraet_wird_abgewiesen(umgebung):
    client = umgebung["client"]
    onboard(client, ausweis())

    assert onboard(client, ausweis(aud="ein-anderer-drucker")).status_code == 409


def test_ausweis_eines_anderen_ausstellers_wird_abgewiesen(umgebung):
    client = umgebung["client"]
    onboard(client, ausweis())

    assert onboard(client, ausweis(iss="https://boese.example.invalid")).status_code == 409


def test_statische_session_wird_von_keiner_signatur_uebernommen(umgebung):
    """Eine Session ohne bewiesene Identitaet gehoert dem, der ihren Wert kennt — niemandem sonst."""
    client = umgebung["client"]
    statisch = "ein-statischer-token-mit-genug-laenge"
    assert onboard(client, statisch).status_code == 201

    assert onboard(client, ausweis()).status_code == 409
    assert drucke(client, ausweis()).status_code == 403


# ── The promise to installations that do not use this ───────────────────────────────────────────

def test_ohne_konfigurierte_jwks_url_bleibt_alles_wie_vorher(umgebung):
    """Kein SESSION_JWKS_URL => die Signatur interessiert niemanden, der Digest entscheidet."""
    client = umgebung["client"]
    umgebung["einstellungen"]["SESSION_JWKS_URL"] = ""

    erster = ausweis(jti="eins")
    assert onboard(client, erster).status_code == 201
    assert onboard(client, ausweis(jti="zwei")).status_code == 409, \
        "Ohne Konfiguration ist ein Ausweis nur eine lange Zeichenkette"
    assert drucke(client, erster).status_code == 200, "Der mitgebrachte Wert druckt wie bisher"


def test_unerreichbares_jwks_laesst_die_laufende_session_drucken(umgebung):
    """Ein toter Aussteller kostet die Uebernahme, nicht den Betrieb."""
    client = umgebung["client"]
    session_jwt = umgebung["session_jwt"]
    erster = ausweis(jti="eins")
    onboard(client, erster)

    session_jwt.reset_cache()

    def kaputt(url, timeout=None):
        raise OSError("issuer unreachable")

    with mock.patch.object(session_jwt.urllib.request, "urlopen", kaputt):
        assert drucke(client, erster).status_code == 200, "Der bekannte Token druckt weiter"
        assert onboard(client, ausweis(jti="zwei")).status_code == 409, "Uebernahme ist nicht beweisbar"


def test_schluesselwechsel_wird_ohne_neustart_bemerkt(umgebung):
    """Nach einer Rotation veroeffentlicht der Aussteller einen neuen Schluessel."""
    client = umgebung["client"]
    session_jwt = umgebung["session_jwt"]
    onboard(client, ausweis(jti="alter-schluessel"))

    umgebung["veroeffentlicht"]["keys"] = jwks_document(N1, N2)
    session_jwt.reset_cache()

    antwort = onboard(client, ausweis(jti="neuer-schluessel", n=N2, d=D2))
    assert antwort.status_code == 200, "Ein zweiter veroeffentlichter Schluessel derselben Instanz gilt auch"
