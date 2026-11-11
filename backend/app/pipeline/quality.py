"""Local scan-quality inspection (blur, brightness, contrast, skew, resolution metadata)."""

from __future__ import annotations

import cv2
import numpy as np

BLUR_THRESHOLD = 60.0
DARK_THRESHOLD = 60.0
BRIGHT_THRESHOLD = 235.0
CONTRAST_THRESHOLD = 25.0
SKEW_FLAG_DEGREES = 2.0
MAX_DESKEW_DEGREES = 15.0

SUGGESTIONS = {
    "BLURRY": "The image looks blurry. Hold the camera steady and make sure the text is in focus.",
    "TOO_DARK": "The image is too dark. Photograph the document in better light.",
    "TOO_BRIGHT": "The image is overexposed. Avoid glare and direct flash.",
    "LOW_CONTRAST": "The text has low contrast. Place the document on a plain, contrasting background.",
    "SKEWED": "The document appears tilted. It was straightened automatically; a straight scan reads better.",
    "LOW_RESOLUTION": "The resolution is low. Scan at 200 DPI or higher, or take a closer photo.",
    "EXIF_ROTATED": "The photo was rotated using its orientation metadata.",
}


def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    if img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_RGBA2GRAY)
    return cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)


def estimate_skew(gray: np.ndarray) -> float:
    """Estimate text skew in degrees from the minimum-area rectangle of dark pixels."""
    small = gray
    scale = 1000 / max(gray.shape)
    if scale < 1:
        small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    _, th = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # Join characters into text lines so the rectangle follows the baseline.
    th = cv2.dilate(th, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3)))
    lines = cv2.HoughLinesP(th, 1, np.pi / 720, threshold=80, minLineLength=small.shape[1] // 4, maxLineGap=20)
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) <= MAX_DESKEW_DEGREES:
            angles.append(a)
    return float(np.median(angles)) if angles else 0.0


def deskew(gray: np.ndarray, angle: float) -> np.ndarray:
    angle = max(-MAX_DESKEW_DEGREES, min(MAX_DESKEW_DEGREES, angle))
    if abs(angle) < 0.3:
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def inspect(gray: np.ndarray, *, dpi: float | None, dpi_source: str) -> dict:
    """Return measured metrics and flags. ``dpi_source`` is 'metadata', 'render' or 'missing'."""
    # Normalise size so the Laplacian variance threshold is comparable across inputs.
    norm = gray
    if max(gray.shape) > 1600:
        f = 1600 / max(gray.shape)
        norm = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    lap = float(cv2.Laplacian(norm, cv2.CV_64F).var())
    brightness = float(gray.mean())
    contrast = float(gray.std())
    skew = estimate_skew(gray)
    flags: list[str] = []
    if lap < BLUR_THRESHOLD:
        flags.append("BLURRY")
    if brightness < DARK_THRESHOLD:
        flags.append("TOO_DARK")
    ink = float(np.percentile(gray, 2))  # darkest pixels: printed text on a readable page
    if brightness > BRIGHT_THRESHOLD and ink > 150:
        flags.append("TOO_BRIGHT")  # white paper is normal; overexposure means the text itself is washed out
    if contrast < CONTRAST_THRESHOLD and ink > 90:
        flags.append("LOW_CONTRAST")
    if abs(skew) > SKEW_FLAG_DEGREES:
        flags.append("SKEWED")
    if (dpi is not None and dpi < 150) or min(gray.shape) < 500:
        flags.append("LOW_RESOLUTION")
    return {
        "blur_laplacian_var": round(lap, 1),
        "brightness": round(brightness, 1),
        "contrast": round(contrast, 1),
        "skew_degrees": round(skew, 2),
        "dpi": round(dpi, 1) if dpi else None,
        "dpi_source": dpi_source,  # 'missing' means no metadata - not a measured quality problem
        "width": int(gray.shape[1]),
        "height": int(gray.shape[0]),
        "flags": flags,
    }
