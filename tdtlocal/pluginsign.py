"""Author signatures for plugins (ed25519), the same scheme as desktop/lib/plugin-sign.js.

The standard library has no ed25519, so the verification of the signature (all the server ever
needs: authors sign with ``scripts/plugin-sign.mjs``) is implemented here in pure Python,
following RFC 8032. What is signed is ``tdtplugin-v1\\n`` plus the SHA-256 of the ``.tdtplugin``
file, so a signature made for something else cannot be reused.

There is no authority that vouches for a key: the first time a key is seen its fingerprint is
shown and the user decides (trust on first use). What is remembered is, per plugin, the key it
was installed with; an update signed with another key, or unsigned when it used to be signed,
is reported as a warning, and a signature that does not verify refuses the install.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from typing import Any

SIGNATURE_VERSION = 1
CONTEXT = b"tdtplugin-v1\n"  # what is signed is this and the SHA-256, so a signature made for something else cannot be reused
B64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
SHA = re.compile(r"^[0-9a-f]{64}$")

# The SPKI DER prefix of an ed25519 public key (RFC 8410): the 32 raw bytes follow it.
SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_sha(raw: Any) -> str | None:
    """The SHA-256 a person typed or a catalog gave, lowercase, or ``None`` if it is not 64 hex digits."""
    if not isinstance(raw, str):
        return None
    text = re.sub(r"[\s:]", "", raw).lower()
    return text if SHA.match(text) else None


# ---------------------------------------------------------------------------------------------------- ed25519 (RFC 8032), verify only
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)  # square root of -1


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if (x * x - xx) % _P != 0:
        return -1  # not a square: no point
    if x % 2 != 0:
        x = _P - x
    return x


_BY = (4 * _inv(5)) % _P
_BX = _xrecover(_BY)
# extended homogeneous coordinates (X, Y, Z, T) with x = X/Z, y = Y/Z, T = XY/Z
_B = (_BX, _BY, 1, (_BX * _BY) % _P)
_IDENT = (0, 1, 1, 0)


def _edwards_add(p: tuple[int, int, int, int], q: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = ((y1 - x1) * (y2 - x2)) % _P
    b = ((y1 + x1) * (y2 + x2)) % _P
    c = (2 * t1 * t2 * _D) % _P
    d = (2 * z1 * z2) % _P
    e = b - a
    f = d - c
    g = d + c
    h = b + a
    return ((e * f) % _P, (g * h) % _P, (f * g) % _P, (e * h) % _P)


def _scalarmult(p: tuple[int, int, int, int], e: int) -> tuple[int, int, int, int]:
    q = _IDENT
    while e > 0:
        if e & 1:
            q = _edwards_add(q, p)
        p = _edwards_add(p, p)
        e >>= 1
    return q


def _encodepoint(p: tuple[int, int, int, int]) -> bytes:
    x, y, z, _t = p
    zinv = _inv(z)
    x = (x * zinv) % _P
    y = (y * zinv) % _P
    return ((y | ((x & 1) << 255)).to_bytes(32, "little"))


def _decodepoint(s: bytes) -> tuple[int, int, int, int] | None:
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little") & ((1 << 255) - 1)
    sign = s[31] >> 7
    if y >= _P:
        return None
    x = _xrecover(y)
    if x < 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    p = (x, y, 1, (x * y) % _P)
    if not _on_curve(p):
        return None
    return p


def _on_curve(p: tuple[int, int, int, int]) -> bool:
    # -x² + y² = 1 + d·x²y², in extended coordinates: -X² + Y² - Z² - d·T² = 0
    x, y, z, t = p
    return (
        (-x * x + y * y - z * z - _D * t * t) % _P == 0
        and (x * y - z * t) % _P == 0
    )


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Does ``signature`` (64 bytes) sign ``message`` with ``public_key`` (32 raw bytes)?"""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a_point = _decodepoint(public_key)
    r_point = _decodepoint(signature[:32])
    if a_point is None or r_point is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    k = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _L
    return _encodepoint(_scalarmult(_B, s)) == _encodepoint(_edwards_add(r_point, _scalarmult(a_point, k)))


# ---------------------------------------------------------------------------------------------------- the signature of a package
def fingerprint_of(public_key_b64: str) -> str:
    """The fingerprint of a public key (base64 SPKI DER): 32 hex digits in groups of four, to compare by eye."""
    der = base64.b64decode(public_key_b64)
    return re.sub(r"(.{4})", r"\1 ", hashlib.sha256(der).hexdigest()[:32]).strip()


def parse_signature(raw: Any) -> dict[str, Any] | None:
    """A signature as a `.sig` file or a catalog gives it (text or object), cleaned, or ``None``."""
    value = raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(value, dict) or value.get("v") != SIGNATURE_VERSION or value.get("alg") != "ed25519":
        return None
    key, sig = value.get("key"), value.get("sig")
    if not isinstance(key, str) or not isinstance(sig, str) or not B64.match(key) or not B64.match(sig) or len(key) > 100 or len(sig) > 100:
        return None
    return {"v": SIGNATURE_VERSION, "alg": "ed25519", "key": key, "sig": sig}


def verify_package(buffer: bytes, raw_signature: Any) -> dict[str, Any]:
    """Does this signature belong to this file? ``{ok: true, fingerprint}`` or ``{ok: false, reason}``."""
    parsed = parse_signature(raw_signature)
    if not parsed:
        return {"ok": False, "reason": "la firma no tiene el formato esperado"}
    try:
        der = base64.b64decode(parsed["key"], validate=True)
        sig = base64.b64decode(parsed["sig"], validate=True)
    except (binascii.Error, ValueError):
        return {"ok": False, "reason": "la firma o la clave no son válidas"}
    if len(der) != 44 or not der.startswith(SPKI_PREFIX):
        return {"ok": False, "reason": "la clave no es ed25519"}
    message = CONTEXT + sha256_hex(buffer).encode("ascii")
    if not ed25519_verify(der[12:], message, sig):
        return {"ok": False, "reason": "la firma no corresponde a este archivo: lo han cambiado o no es de esa clave"}
    return {"ok": True, "fingerprint": fingerprint_of(parsed["key"])}


def judge(plugin_id: str, buffer: bytes, raw_signature: Any, keys: dict[str, Any]) -> dict[str, Any]:
    """The verdict about a package that is about to be installed, given the key each plugin was installed with.

    ``state``: ``signed-known``, ``signed-new``, ``signed-changed``, ``unsigned``, ``unsigned-was-signed``
    or ``invalid`` (a signature that does not verify: the install is refused).
    """
    had = keys.get(plugin_id) if isinstance(keys, dict) else None
    if raw_signature is None or raw_signature == "":
        return {"state": "unsigned-was-signed", "fingerprint": "", "previous": had["fingerprint"]} if had else {"state": "unsigned", "fingerprint": ""}
    checked = verify_package(buffer, raw_signature)
    if not checked["ok"]:
        return {"state": "invalid", "fingerprint": "", "reason": checked["reason"]}
    if not had:
        return {"state": "signed-new", "fingerprint": checked["fingerprint"]}
    if had.get("fingerprint") == checked["fingerprint"]:
        return {"state": "signed-known", "fingerprint": checked["fingerprint"]}
    return {"state": "signed-changed", "fingerprint": checked["fingerprint"], "previous": had.get("fingerprint", "")}


def describe_judgement(j: dict[str, Any]) -> str:
    """The sentence about the signature for the question to the user."""
    state = j.get("state")
    if state == "signed-known":
        return f"Firmado con la clave {j['fingerprint']}: la misma de la versión que ya tienes."
    if state == "signed-new":
        return (f"Firmado con la clave {j['fingerprint']}. Es la primera vez que ves esta clave para este plugin: "
                "si conoces a su autor, compara esta huella con la que publica.")
    if state == "signed-changed":
        return (f"⚠ LA CLAVE HA CAMBIADO. Este archivo está firmado con {j['fingerprint']} y la versión que tienes se instaló "
                f"con {j.get('previous', '')}. Puede ser el mismo autor con una clave nueva, o alguien distinto: no lo instales si no esperabas el cambio.")
    if state == "unsigned-was-signed":
        return f"⚠ SIN FIRMA. La versión que tienes venía firmada con {j.get('previous', '')} y este archivo no lleva firma. No lo instales si no esperabas el cambio."
    if state == "invalid":
        return f"⚠ La firma NO es válida ({j.get('reason', '')})."
    return "Sin firma de autor: lo único que lo respalda es quién te lo pasa y el SHA-256."


def is_warning(j: dict[str, Any]) -> bool:
    """Does this verdict deserve a warning rather than a plain question?"""
    return j.get("state") in ("signed-changed", "unsigned-was-signed", "invalid")
