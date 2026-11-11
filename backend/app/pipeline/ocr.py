"""Local text extraction with provenance.

* PDFs: embedded text via PyMuPDF when adequate; otherwise the page is rendered locally and
  OCR'd with Tesseract. Embedded text has *no* confidence value - none is invented.
* Images: EXIF orientation, grayscale, bounded deskew, upscale, then Tesseract with a timeout.
* Fallback: if text is below the alphanumeric / confidence thresholds, one bounded re-OCR
  with alternative preprocessing is attempted. Still unreadable -> NEEDS_REUPLOAD.

Raw text lives only in memory for the duration of the processing attempt.
"""

from __future__ import annotations

import io
import re
import shutil
from statistics import median
from dataclasses import dataclass, field

import cv2
import numpy as np
import pymupdf
import pytesseract
from PIL import Image, ImageOps

from app.core.config import get_settings
from app.pipeline import quality as q


class OcrUnavailable(Exception):
    """Tesseract binary missing - an environment problem, not a document problem."""


@dataclass
class Word:
    text: str
    bbox: tuple[float, float, float, float]  # normalised 0..1 (x0, y0, x1, y1)
    conf: float | None


@dataclass
class Line:
    page: int
    words: list[Word]
    method: str  # pdf_text | tesseract | tesseract_reocr

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (
            min(w.bbox[0] for w in self.words), min(w.bbox[1] for w in self.words),
            max(w.bbox[2] for w in self.words), max(w.bbox[3] for w in self.words),
        )

    def word_spans(self) -> list[tuple[int, int, Word]]:
        spans, pos = [], 0
        for w in self.words:
            spans.append((pos, pos + len(w.text), w))
            pos += len(w.text) + 1
        return spans


@dataclass
class PageText:
    page: int
    method: str
    lines: list[Line]
    mean_conf: float | None
    alnum: int
    quality: dict | None = None


@dataclass
class TextResult:
    pages: list[PageText] = field(default_factory=list)
    pages_total: int = 1

    @property
    def lines(self) -> list[Line]:
        return [ln for p in self.pages for ln in p.lines]

    @property
    def pages_processed(self) -> list[int]:
        return [p.page for p in self.pages]

    @property
    def method(self) -> str:
        methods = {p.method for p in self.pages}
        if methods == {"pdf_text"}:
            return "pdf_text"
        if "pdf_text" in methods:
            return "pdf_text+ocr"
        return "tesseract_reocr" if "tesseract_reocr" in methods else "tesseract"

    @property
    def mean_conf(self) -> float | None:
        vals = [p.mean_conf for p in self.pages if p.mean_conf is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    @property
    def readable(self) -> bool:
        return all(_page_ok(p) for p in self.pages) and bool(self.pages)


def _alnum(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]", text))


def _page_ok(p: PageText) -> bool:
    s = get_settings()
    if p.alnum < s.ocr_min_alnum_chars:
        return False
    return p.mean_conf is None or p.mean_conf >= s.ocr_min_mean_confidence


def tesseract_available() -> tuple[bool, str]:
    s = get_settings()
    cmd = s.tesseract_cmd or shutil.which("tesseract")
    if not cmd:
        return False, "tesseract executable not found on PATH (set TESSERACT_CMD)"
    pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        return True, str(pytesseract.get_tesseract_version())
    except Exception as exc:  # noqa: BLE001
        return False, f"tesseract not runnable: {type(exc).__name__}"


def _configure_tesseract() -> None:
    ok, msg = tesseract_available()
    if not ok:
        raise OcrUnavailable(msg)


# --------------------------------------------------------------------------- OCR core
def split_text_columns(words: list[Word], page: int, method: str) -> list[Line]:
    """Keep spatially distant text/QR artefacts out of the same field line."""
    if not words:
        return []
    words = sorted(words, key=lambda word: word.bbox[0])
    character_width = median((word.bbox[2] - word.bbox[0]) / max(len(word.text), 1) for word in words)
    threshold = max(0.08, 6 * character_width)
    groups = [[words[0]]]
    for word in words[1:]:
        if word.bbox[0] - groups[-1][-1].bbox[2] > threshold:
            groups.append([])
        groups[-1].append(word)
    return [Line(page, group, method) for group in groups]


def _ocr_gray(gray: np.ndarray, page: int, method: str, psm: int) -> PageText:
    s = get_settings()
    h, w = gray.shape
    try:
        data = pytesseract.image_to_data(
            Image.fromarray(gray), lang=s.tesseract_lang, config=f"--oem 1 --psm {psm}",
            output_type=pytesseract.Output.DICT, timeout=s.ocr_timeout_seconds,
        )
    except RuntimeError as exc:  # pytesseract raises RuntimeError on timeout
        raise TimeoutError("OCR timed out") from exc
    groups: dict[tuple[int, int, int], list[Word]] = {}
    confs: list[float] = []
    for i, txt in enumerate(data["text"]):
        txt = (txt or "").strip()
        conf = float(data["conf"][i])
        if not txt or conf < 0:
            continue
        x, y, bw, bh = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append(Word(txt, (x / w, y / h, (x + bw) / w, (y + bh) / h), conf))
        if _alnum(txt):
            confs.append(conf)
    lines = [line for _, ws in sorted(groups.items()) for line in split_text_columns(ws, page, method)]
    lines.sort(key=lambda ln: (round(ln.bbox[1], 2), ln.bbox[0]))
    text = "\n".join(ln.text for ln in lines)
    return PageText(page, method, lines, round(sum(confs) / len(confs), 1) if confs else 0.0, _alnum(text))


def _prepare(gray: np.ndarray) -> tuple[np.ndarray, float]:
    angle = q.estimate_skew(gray)
    g = q.deskew(gray, angle)
    if max(g.shape) < 1800:
        f = 1800 / max(g.shape)
        g = cv2.resize(g, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    return g, angle


def _reocr_variant(gray: np.ndarray) -> np.ndarray:
    g = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    g = cv2.medianBlur(g, 3)
    g = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return th


def ocr_image_array(gray: np.ndarray, page: int) -> PageText:
    _configure_tesseract()
    prepared, _ = _prepare(gray)
    first = _ocr_gray(prepared, page, "tesseract", psm=3)
    if _page_ok(first):
        return first
    second = _ocr_gray(_reocr_variant(prepared), page, "tesseract_reocr", psm=6)
    # keep whichever read more reliably
    def score(p: PageText) -> float:
        return p.alnum * ((p.mean_conf or 0) / 100)

    return second if score(second) > score(first) else first


# --------------------------------------------------------------------------- entry points
def load_image(data: bytes) -> tuple[np.ndarray, dict]:
    """Decode an image applying EXIF orientation. Returns (grayscale array, metadata)."""
    meta: dict = {"exif_rotated": False}
    with Image.open(io.BytesIO(data)) as im:
        try:
            orientation = im.getexif().get(0x0112, 1)
        except Exception:  # noqa: BLE001
            orientation = 1
        dpi = im.info.get("dpi")
        fixed = ImageOps.exif_transpose(im)
        meta["exif_rotated"] = orientation not in (1, None)
        meta["dpi"] = float(dpi[0]) if dpi and dpi[0] and float(dpi[0]) > 1 else None
        rgb = fixed.convert("RGB")
    gray = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2GRAY)
    return gray, meta


def render_pdf_page(doc: pymupdf.Document, index: int, dpi: int) -> np.ndarray:
    pix = doc[index].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def extract_text(data: bytes, file_format: str, *, first_page_only: bool = False) -> TextResult:
    s = get_settings()
    result = TextResult()
    if file_format == "pdf":
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            result.pages_total = doc.page_count
            indices = [0] if first_page_only else list(range(doc.page_count))
            for i in indices:
                page = doc[i]
                pw, ph = page.rect.width, page.rect.height
                words = page.get_text("words", sort=True)
                groups: dict[tuple[int, int], list[Word]] = {}
                for x0, y0, x1, y1, txt, block, line, _wn in words:
                    groups.setdefault((block, line), []).append(Word(txt, (x0 / pw, y0 / ph, x1 / pw, y1 / ph), None))
                lines = [line for ws in groups.values() for line in split_text_columns(ws, i + 1, "pdf_text")]
                lines.sort(key=lambda ln: (round(ln.bbox[1], 3), ln.bbox[0]))
                text = "\n".join(ln.text for ln in lines)
                if _alnum(text) >= s.ocr_min_alnum_chars:
                    pt = PageText(i + 1, "pdf_text", lines, None, _alnum(text))
                else:  # scanned page -> local render + Tesseract
                    pt = ocr_image_array(render_pdf_page(doc, i, s.pdf_render_dpi), i + 1)
                result.pages.append(pt)
        finally:
            doc.close()
    else:
        gray, _meta = load_image(data)
        result.pages.append(ocr_image_array(gray, 1))
    return result


def inspect_document(data: bytes, file_format: str, *, first_page_only: bool = False) -> dict:
    """Quality inspection stage. For PDFs with an embedded text layer, blur/contrast of the render
    says little about readability, so those flags are only raised for pages without text."""
    s = get_settings()
    pages: list[dict] = []
    if file_format == "pdf":
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            indices = [0] if first_page_only else list(range(doc.page_count))
            for i in indices:
                has_text = _alnum(doc[i].get_text("text")) >= s.ocr_min_alnum_chars
                info = q.inspect(render_pdf_page(doc, i, 100), dpi=None, dpi_source="vector_pdf" if has_text else "render")
                if has_text:
                    info["flags"] = [f for f in info["flags"] if f not in ("BLURRY", "LOW_CONTRAST", "LOW_RESOLUTION", "TOO_BRIGHT", "TOO_DARK")]
                info["page"] = i + 1
                info["text_layer"] = has_text
                pages.append(info)
        finally:
            doc.close()
    else:
        gray, meta = load_image(data)
        info = q.inspect(gray, dpi=meta["dpi"], dpi_source="metadata" if meta["dpi"] else "missing")
        if meta["exif_rotated"]:
            info["flags"].append("EXIF_ROTATED")
        info["page"] = 1
        info["text_layer"] = False
        pages.append(info)
    flags: list[str] = []
    for p in pages:
        flags += [f for f in p["flags"] if f not in flags]
    return {"pages": pages, "flags": flags, "suggestions": [q.SUGGESTIONS[f] for f in flags if f in q.SUGGESTIONS]}
