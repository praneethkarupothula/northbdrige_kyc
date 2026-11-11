"""Private document storage with application-level encryption.

Files are addressed by generated opaque keys - never by original filenames. Nothing in
storage is ever served by a static web server; access goes through authorised routes.
"""

from __future__ import annotations

import os
import re
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings
from app.core.crypto import decrypt_bytes, encrypt_bytes

_KEY_RE = re.compile(r"^doc_[0-9a-f]{32}$")
_PURPOSE = "document-blob"


class BlobStore(Protocol):
    def put(self, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


def new_key() -> str:
    return f"doc_{uuid.uuid4().hex}"


def _check(key: str) -> str:
    if not _KEY_RE.match(key):
        raise ValueError("invalid storage key")
    return key


class LocalEncryptedStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        k = _check(key)
        return self.root / k[4:6] / f"{k}.bin"

    def put(self, data: bytes) -> str:
        key = new_key()
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(encrypt_bytes(data, _PURPOSE))
        os.replace(tmp, p)
        return key

    def get(self, key: str) -> bytes:
        return decrypt_bytes(self._path(key).read_bytes(), _PURPOSE)

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3EncryptedStore:  # pragma: no cover - requires an object store; not exercised by the test suite
    """Private S3-compatible bucket (AWS S3, Cloudflare R2, MinIO). Requires `pip install boto3`.

    Objects are encrypted client-side before upload; bucket must block public access.
    """

    def __init__(self, bucket: str, endpoint_url: str | None):
        import boto3  # optional dependency

        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url or None)

    def put(self, data: bytes) -> str:
        key = new_key()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=encrypt_bytes(data, _PURPOSE))
        return key

    def get(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=_check(key))
        return decrypt_bytes(obj["Body"].read(), _PURPOSE)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_check(key))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_check(key))
            return True
        except Exception:  # noqa: BLE001
            return False


@lru_cache
def get_store() -> BlobStore:
    s = get_settings()
    if s.storage_backend == "s3":
        return S3EncryptedStore(s.s3_bucket, s.s3_endpoint_url)
    return LocalEncryptedStore(s.storage_dir)
