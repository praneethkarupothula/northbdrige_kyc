"""Application-level encryption and keyed fingerprints.

* AES-256-GCM with a random 96-bit nonce per message; purpose strings are bound as
  associated data so ciphertext from one column cannot be replayed into another.
* Identity fingerprints are HMAC-SHA-256(server secret, "<ID_TYPE>:<canonical value>").
  Plaintext identifiers are never persisted for lookup.

Note: Python cannot guarantee that plaintext bytes are zeroised from memory after use.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings

_VERSION = b"\x01"


def _subkey(purpose: str) -> bytes:
    master = get_settings().encryption_key_bytes()
    return HKDF(algorithm=SHA256(), length=32, salt=None, info=b"nbkyc:" + purpose.encode()).derive(master)


def encrypt_bytes(data: bytes, purpose: str) -> bytes:
    nonce = os.urandom(12)
    return _VERSION + nonce + AESGCM(_subkey(purpose)).encrypt(nonce, data, purpose.encode())


def decrypt_bytes(blob: bytes, purpose: str) -> bytes:
    if not blob or blob[:1] != _VERSION:
        raise ValueError("unsupported ciphertext version")
    nonce, ct = blob[1:13], blob[13:]
    return AESGCM(_subkey(purpose)).decrypt(nonce, ct, purpose.encode())


def encrypt_json(obj: Any, purpose: str) -> bytes:
    return encrypt_bytes(json.dumps(obj, default=str).encode(), purpose)


def decrypt_json(blob: bytes | None, purpose: str) -> Any:
    if blob is None:
        return None
    return json.loads(decrypt_bytes(blob, purpose))


def encrypt_str(value: str | None, purpose: str) -> bytes | None:
    return None if value is None else encrypt_bytes(value.encode(), purpose)


def decrypt_str(blob: bytes | None, purpose: str) -> str | None:
    return None if blob is None else decrypt_bytes(blob, purpose).decode()


def identity_fingerprint(id_type: str, canonical_value: str) -> str:
    msg = f"{id_type.upper()}:{canonical_value}".encode()
    return hmac.new(get_settings().hmac_key_bytes(), msg, hashlib.sha256).hexdigest()


def content_fingerprint(data: bytes) -> str:
    """Keyed hash of uploaded bytes, used to detect repeated uploads without an unkeyed hash."""
    return hmac.new(get_settings().hmac_key_bytes(), b"CONTENT:" + data, hashlib.sha256).hexdigest()


def keyed_lookup_hash(value: str) -> str:
    return hmac.new(get_settings().hmac_key_bytes(), b"LOOKUP:" + value.encode(), hashlib.sha256).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
