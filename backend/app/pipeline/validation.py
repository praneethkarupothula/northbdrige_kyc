"""Upload validation: magic bytes + real parsing. Extensions and client MIME types are not trusted."""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

import pymupdf
from PIL import Image

from app.core.config import get_settings


class FileRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


@dataclass
class ValidatedFile:
    file_format: str  # pdf | jpeg | png
    page_count: int
    width: int | None = None
    height: int | None = None


def sniff_format(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    return None


def validate_upload(data: bytes, *, max_bytes: int | None = None) -> ValidatedFile:
    s = get_settings()
    limit = max_bytes or s.max_file_bytes
    if len(data) == 0:
        raise FileRejected("EMPTY_FILE", "The file is empty.")
    if len(data) > limit:
        raise FileRejected("FILE_TOO_LARGE", f"Files must be {limit // (1024 * 1024)} MiB or smaller.")
    fmt = sniff_format(data)
    if fmt is None:
        raise FileRejected(
            "UNSUPPORTED_TYPE", "Only PDF, JPG/JPEG and PNG files are accepted (file content did not match)."
        )
    if fmt == "pdf":
        return _validate_pdf(data, s.max_pdf_pages)
    return _validate_image(data, fmt, s.max_image_pixels)


def _validate_pdf(data: bytes, max_pages: int) -> ValidatedFile:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise FileRejected("CORRUPT_FILE", "This PDF could not be opened. It may be damaged.") from exc
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise FileRejected(
                "ENCRYPTED_PDF", "This PDF is password-protected. Please upload an unprotected copy."
            )
        if doc.page_count < 1:
            raise FileRejected("CORRUPT_FILE", "This PDF has no pages.")
        if doc.page_count > max_pages:
            raise FileRejected("TOO_MANY_PAGES", f"PDFs may have at most {max_pages} pages.")
        if doc.is_repaired:
            raise FileRejected("CORRUPT_FILE", "This PDF is damaged and had to be repaired. Please re-export it.")
        for page in doc:
            page.get_text("text")  # forces content-stream parsing
            r = page.rect
            if r.width <= 0 or r.height <= 0 or r.width > 14400 or r.height > 14400:
                raise FileRejected("CORRUPT_FILE", "This PDF has an invalid page size.")
        return ValidatedFile("pdf", doc.page_count)
    except FileRejected:
        raise
    except Exception as exc:  # noqa: BLE001
        raise FileRejected("CORRUPT_FILE", "This PDF could not be read. It may be damaged.") from exc
    finally:
        doc.close()


def _validate_image(data: bytes, fmt: str, max_pixels: int) -> ValidatedFile:
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                actual = (im.format or "").lower()
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
                im.load()  # full decode catches truncated files
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise FileRejected("IMAGE_TOO_LARGE", "The image has too many pixels.") from exc
    except Exception as exc:  # noqa: BLE001
        raise FileRejected("CORRUPT_FILE", "This image could not be read. It may be damaged.") from exc
    if actual not in ("jpeg", "png") or actual != fmt:
        raise FileRejected("UNSUPPORTED_TYPE", "The image content does not match a JPG or PNG file.")
    if w * h > max_pixels:
        raise FileRejected("IMAGE_TOO_LARGE", "The image has too many pixels.")
    if min(w, h) < 200:
        raise FileRejected("IMAGE_TOO_SMALL", "The image is too small to read. Please upload a clearer photo or scan.")
    return ValidatedFile(fmt, 1, w, h)
