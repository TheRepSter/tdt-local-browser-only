"""Tests of tdtlocal/pluginsign.py: the ed25519 verification against the RFC 8032 vectors, and the
verdicts about a package's signature (the same rules as desktop/lib/plugin-sign.js)."""

from __future__ import annotations

import base64
import hashlib
import json
import unittest

from tdtlocal import pluginsign as ps


# RFC 8032, section 7.1, test vectors 1 and 2 (and 3, with a one-byte message)
VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]


def _seed_to_public(seed: bytes) -> bytes:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return ps._encodepoint(ps._scalarmult(ps._B, a))


def _sign(seed: bytes, message: bytes) -> bytes:
    """ed25519 sign (only used by the tests: the authors sign with scripts/plugin-sign.mjs)."""
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    prefix = h[32:]
    public = ps._encodepoint(ps._scalarmult(ps._B, a))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % ps._L
    r_point = ps._encodepoint(ps._scalarmult(ps._B, r))
    k = int.from_bytes(hashlib.sha512(r_point + public + message).digest(), "little") % ps._L
    s = (r + k * a) % ps._L
    return r_point + s.to_bytes(32, "little")


def _sig_file(seed_hex: str, buffer: bytes) -> str:
    seed = bytes.fromhex(seed_hex)
    public = _seed_to_public(seed)
    message = ps.CONTEXT + ps.sha256_hex(buffer).encode("ascii")
    sig = _sign(seed, message)
    key = base64.b64encode(ps.SPKI_PREFIX + public).decode("ascii")
    return json.dumps({"v": 1, "alg": "ed25519", "key": key, "sig": base64.b64encode(sig).decode("ascii")})


class Ed25519Test(unittest.TestCase):
    def test_rfc8032_vectors(self) -> None:
        for seed_hex, public_hex, message_hex, signature_hex in VECTORS:
            seed = bytes.fromhex(seed_hex)
            public = bytes.fromhex(public_hex)
            self.assertEqual(_seed_to_public(seed), public, "the public key is derived from the seed")
            self.assertTrue(ps.ed25519_verify(public, bytes.fromhex(message_hex), bytes.fromhex(signature_hex)))
            bad = bytearray(bytes.fromhex(signature_hex))
            bad[0] ^= 1
            self.assertFalse(ps.ed25519_verify(public, bytes.fromhex(message_hex), bytes(bad)))

    def test_wrong_lengths(self) -> None:
        self.assertFalse(ps.ed25519_verify(b"x" * 31, b"m", b"s" * 64))
        self.assertFalse(ps.ed25519_verify(b"x" * 32, b"m", b"s" * 63))


class PackageSignatureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.buffer = b"un paquete de prueba"
        self.seed = VECTORS[0][0]
        self.sig = _sig_file(self.seed, self.buffer)

    def test_parse_signature(self) -> None:
        parsed = ps.parse_signature(self.sig)
        self.assertIsNotNone(parsed)
        self.assertIsNone(ps.parse_signature("no es json"))
        self.assertIsNone(ps.parse_signature({"v": 2, "alg": "ed25519", "key": "AAAA", "sig": "AAAA"}))
        self.assertIsNone(ps.parse_signature({"v": 1, "alg": "ed25519", "key": "!!!", "sig": "AAAA"}))

    def test_verify_package_ok(self) -> None:
        checked = ps.verify_package(self.buffer, self.sig)
        self.assertTrue(checked["ok"], checked)
        self.assertRegex(checked["fingerprint"], r"^([0-9a-f]{4} ){7}[0-9a-f]{4}$")

    def test_verify_package_changed_file(self) -> None:
        checked = ps.verify_package(self.buffer + b"x", self.sig)
        self.assertFalse(checked["ok"])
        self.assertIn("no corresponde", checked["reason"])

    def test_verify_package_bad_key(self) -> None:
        other = json.loads(self.sig)
        other["key"] = base64.b64encode(ps.SPKI_PREFIX + b"\x01" * 32).decode()
        self.assertFalse(ps.verify_package(self.buffer, other)["ok"])

    def test_judge_states(self) -> None:
        self.assertEqual(ps.judge("p", self.buffer, None, {})["state"], "unsigned")
        new = ps.judge("p", self.buffer, self.sig, {})
        self.assertEqual(new["state"], "signed-new")
        keys = {"p": {"fingerprint": new["fingerprint"], "author": "Ana"}}
        self.assertEqual(ps.judge("p", self.buffer, self.sig, keys)["state"], "signed-known")
        other_keys = {"p": {"fingerprint": "aaaa bbbb cccc dddd eeee ffff 0000 1111", "author": "Otro"}}
        self.assertEqual(ps.judge("p", self.buffer, self.sig, other_keys)["state"], "signed-changed")
        self.assertEqual(ps.judge("p", self.buffer, None, keys)["state"], "unsigned-was-signed")
        self.assertEqual(ps.judge("p", self.buffer, "basura", {})["state"], "invalid")
        self.assertTrue(ps.is_warning(ps.judge("p", self.buffer, "basura", {})))
        self.assertFalse(ps.is_warning(new))
        self.assertIn("CLAVE HA CAMBIADO", ps.describe_judgement(ps.judge("p", self.buffer, self.sig, other_keys)))

    def test_clean_sha(self) -> None:
        self.assertEqual(ps.clean_sha("AB CD:ef" + "0" * 58), "ab cdef".replace(" ", "") + "0" * 58)
        self.assertIsNone(ps.clean_sha("corto"))
        self.assertIsNone(ps.clean_sha(123))


if __name__ == "__main__":
    unittest.main()
