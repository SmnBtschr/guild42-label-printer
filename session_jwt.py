"""Verification of signed session tokens (RS256 JWT) against a published JWK Set.

WHY THIS EXISTS: the exclusive session in ``api.py`` recognises its holder by the SHA-256 of a
token — a shared secret. That works until the caller restarts. Guild42's check-in system is
redeployed daily; after a restart it no longer knows the token it was given, every print gets a
403, and the documented way out is a restart of this Pi. A caller that can *prove its identity*
instead of *remembering a secret* simply signs a new token and carries on.

WHAT IS PROVEN: that the presented token was signed by the private key belonging to a public key
published at ``SESSION_JWKS_URL``, that it has not expired, and that issuer/audience match what
this device expects. Two tokens signed by the same key with the same ``iss``/``sub`` count as the
same caller — that, and only that, is what allows a takeover after a restart.

WHY NO NEW DEPENDENCY: this project deliberately refuses libraries for small jobs (see the note on
python-dotenv in ``api.py``). ``PyJWT[crypto]`` would pull in ``cryptography``, a Rust toolchain
concern on a Raspberry Pi, for one signature check. RSA verification is a modular exponentiation
plus a byte comparison, and the standard library brings both.

WHY THE COMPARISON IS SAFE: PKCS#1 v1.5 verification is dangerous when the padding is *parsed* —
a lax parser accepts forged signatures (the classic Bleichenbacher/BERserk family of bugs). This
module never parses padding. It builds the one byte string a valid signature must produce and
compares it in full, in constant time. Anything else fails, including every malleability trick.

WHAT IS DELIBERATELY NOT SUPPORTED: algorithms other than RS256 (``none`` and HMAC are the
alg-confusion attack, so an unexpected ``alg`` is rejected outright), and encrypted JWTs.
"""

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.request

log = logging.getLogger(__name__)

# ── What a valid RS256 signature must decrypt to ────────────────────────────────────────────────
#
# DigestInfo prefix for SHA-256 (RFC 8017, 9.2 notes): the DER header in front of the 32 hash
# bytes. Hard-coded rather than assembled, because this is exactly the byte string an attacker
# would like us to be flexible about.
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")

# Signature/JWKS handling must never hang the print path. The kiosk shows a spinner while a
# request runs; a JWKS host that accepts connections and then goes quiet would freeze it.
_HTTP_TIMEOUT = 3.0

# How long a fetched key set is reused. Long enough that a busy event does not hammer the issuer,
# short enough that a key rotation is picked up within minutes without a restart here.
_CACHE_SECONDS = 300

# Refetch cooldown for an unknown ``kid``. A rotated key must be picked up immediately, but an
# attacker sending random kids must not be able to turn this into a request amplifier.
_UNKNOWN_KID_COOLDOWN = 30

_lock = threading.Lock()
_cache = {"url": None, "keys": {}, "fetched": 0.0, "last_attempt": 0.0}


def _b64url_decode(text: str) -> bytes:
    """Base64url without padding, as JWT uses it everywhere."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64url_to_int(text: str) -> int:
    return int.from_bytes(_b64url_decode(text), "big")


def _fetch_jwks(url: str) -> dict:
    """``{kid: (n, e)}`` from a JWK Set. Only RSA signing keys are taken.

    A key without ``kid`` is kept under the empty string: the JWK Set of another implementation
    may legitimately omit it when there is only one key, and refusing to talk to such a peer would
    be pedantry, not security — the signature still has to check out.
    """
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as response:  # nosec B310 - fixed https URL from .env
        document = json.loads(response.read().decode("utf-8"))
    keys = {}
    for jwk in document.get("keys") or []:
        if jwk.get("kty") != "RSA":
            continue
        if jwk.get("use") not in (None, "sig"):
            continue
        if jwk.get("alg") not in (None, "RS256"):
            continue
        try:
            keys[jwk.get("kid") or ""] = (_b64url_to_int(jwk["n"]), _b64url_to_int(jwk["e"]))
        except Exception:
            # A single malformed entry must not cost us the others.
            log.warning("session-jwt: skipping malformed JWK entry")
    return keys


def _keys_for(url: str, kid: str) -> dict:
    """Cached key set, refetched when the cache is stale or the ``kid`` is unknown."""
    now = time.time()
    with _lock:
        frisch = (_cache["url"] == url and _cache["keys"]
                  and now - _cache["fetched"] < _CACHE_SECONDS
                  and (kid in _cache["keys"] or "" in _cache["keys"]))
        if frisch:
            return dict(_cache["keys"])
        if now - _cache["last_attempt"] < _UNKNOWN_KID_COOLDOWN and _cache["url"] == url:
            # Cooldown after a recent attempt: hand back what we have rather than fetching again.
            return dict(_cache["keys"])
        _cache["last_attempt"] = now

    try:
        keys = _fetch_jwks(url)
    except Exception as problem:
        # Fail closed for NEW callers, but do not tear down a running session: api.py falls back
        # to the digest comparison, so an unreachable issuer costs the takeover feature, not the
        # ability to keep printing.
        log.warning("session-jwt: JWKS not reachable (%s): %s", url, problem)
        with _lock:
            return dict(_cache["keys"]) if _cache["url"] == url else {}

    with _lock:
        _cache.update({"url": url, "keys": keys, "fetched": time.time()})
        return dict(keys)


def _signature_ok(signing_input: bytes, signature: bytes, n: int, e: int) -> bool:
    """RSASSA-PKCS1-v1_5 verification by reconstruction (RFC 8017, 8.2.2)."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    signature_int = int.from_bytes(signature, "big")
    if signature_int >= n:
        return False

    entschluesselt = pow(signature_int, e, n).to_bytes(k, "big")

    digest_info = _SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(signing_input).digest()
    # 0x00 || 0x01 || 0xFF... || 0x00 || DigestInfo — built here, never parsed there.
    padding_laenge = k - len(digest_info) - 3
    if padding_laenge < 8:  # RFC 8017 demands at least 8 bytes of 0xFF
        return False
    erwartet = b"\x00\x01" + b"\xff" * padding_laenge + b"\x00" + digest_info

    return hmac.compare_digest(entschluesselt, erwartet)


def verify(token: str, jwks_url: str, expected_issuer: str = "", expected_audience: str = "",
           leeway_seconds: int = 60):
    """Verify a signed session token.

    :returns: ``{"iss": ..., "sub": ..., "jti": ...}`` identifying the caller, or ``None`` if the
        token is not a valid signed token for this device. ``None`` covers every failure —
        malformed, wrong key, expired, wrong audience — on purpose: the caller turns it into one
        indistinguishable rejection, exactly like the static-token path in ``api.py``.
    """
    if not token or not jwks_url:
        return None
    teile = token.split(".")
    if len(teile) != 3:
        return None  # Not a JWS at all — most likely a plain static token.

    try:
        header = json.loads(_b64url_decode(teile[0]).decode("utf-8"))
        claims = json.loads(_b64url_decode(teile[1]).decode("utf-8"))
        signature = _b64url_decode(teile[2])
    except Exception:
        return None

    # alg confusion is the reason this check comes before anything else: "none" would skip
    # verification, and an HMAC alg would let the PUBLIC key double as the signing secret.
    if header.get("alg") != "RS256":
        log.info("session-jwt: rejected token with alg=%r", header.get("alg"))
        return None
    if header.get("typ") not in (None, "JWT"):
        return None

    signing_input = (teile[0] + "." + teile[1]).encode("ascii")
    kid = header.get("kid") or ""
    keys = _keys_for(jwks_url, kid)
    if not keys:
        return None

    kandidaten = [keys[kid]] if kid in keys else list(keys.values())
    if not any(_signature_ok(signing_input, signature, n, e) for n, e in kandidaten):
        log.info("session-jwt: signature did not verify against %d published key(s)", len(kandidaten))
        return None

    jetzt = time.time()
    ablauf = claims.get("exp")
    if not isinstance(ablauf, (int, float)) or jetzt > ablauf + leeway_seconds:
        # An ausweis without exp is refused rather than treated as eternal: the expiry is what
        # limits the damage of a leaked token, and this one travels as an HTTP header.
        log.info("session-jwt: token expired or without exp")
        return None
    beginn = claims.get("nbf")
    if isinstance(beginn, (int, float)) and jetzt + leeway_seconds < beginn:
        return None

    if expected_issuer and claims.get("iss") != expected_issuer:
        log.info("session-jwt: unexpected iss=%r", claims.get("iss"))
        return None
    if expected_audience:
        audience = claims.get("aud")
        zulaessig = audience if isinstance(audience, list) else [audience]
        if expected_audience not in zulaessig:
            log.info("session-jwt: token not meant for this device (aud=%r)", audience)
            return None
    if not claims.get("sub"):
        # The identity IS iss+sub. Without sub there is nothing to recognise after a restart, and
        # accepting it would mean any key holder inherits any session.
        return None

    return {"iss": claims.get("iss") or "", "sub": claims["sub"], "jti": claims.get("jti") or ""}


def reset_cache():
    """Forget cached keys. For tests and for a manual rotation without a restart."""
    with _lock:
        _cache.update({"url": None, "keys": {}, "fetched": 0.0, "last_attempt": 0.0})
