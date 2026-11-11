"""Privacy-safe telemetry.

Logs and traces pass through an *allowlist*: only known keys survive, and each value
must satisfy a per-key validator (opaque IDs, enum-like labels, numbers). Anything else
is dropped and replaced by a counter of dropped fields, so a developer accidentally
logging ``name=...`` or ``text=...`` leaks nothing.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import structlog

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_.:\-]{0,63}$")
_REQ = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
_ROUTE = re.compile(r"^/[A-Za-z0-9_/{}\-.]{0,120}$")


def _is_uuid(v: Any) -> bool:
    return isinstance(v, str) and bool(_UUID.match(v))


def _is_label(v: Any) -> bool:
    return isinstance(v, str) and bool(_LABEL.match(v))


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _is_count_map(v: Any) -> bool:
    return isinstance(v, dict) and all(_is_label(k) and _is_num(n) for k, n in v.items())


def _is_req(v: Any) -> bool:
    return isinstance(v, str) and bool(_REQ.match(v))


def _is_route(v: Any) -> bool:
    return isinstance(v, str) and bool(_ROUTE.match(v))


ALLOWED_FIELDS: dict[str, Callable[[Any], bool]] = {
    # opaque identifiers
    "request_id": _is_req,
    "job_id": _is_uuid,
    "application_id": _is_uuid,
    "version_id": _is_uuid,
    "document_id": _is_uuid,
    "user_id": _is_uuid,
    "worker_id": _is_label,
    # labels
    "stage": _is_label,
    "status": _is_label,
    "job_kind": _is_label,
    "file_format": _is_label,
    "document_type": _is_label,
    "category": _is_label,
    "error_class": _is_label,
    "check_status": _is_label,
    "review_status": _is_label,
    "extraction_method": _is_label,
    "model": _is_label,
    "provider": _is_label,
    "method": _is_label,
    "route": _is_route,
    "env": _is_label,
    "milestone": _is_label,
    "role": _is_label,
    # numbers
    "ocr_confidence": _is_num,
    "duration_ms": _is_num,
    "latency_ms": _is_num,
    "retry_count": _is_num,
    "attempt": _is_num,
    "page_count": _is_num,
    "status_code": _is_num,
    "input_tokens": _is_num,
    "output_tokens": _is_num,
    "entity_counts": _is_count_map,
    "count": _is_num,
    "ok": _is_bool,
}

# Structlog bookkeeping keys that are always safe.
_INTERNAL = {"event", "level", "timestamp", "logger"}


def allowlist_processor(_logger, _method, event_dict: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    dropped = 0
    for key, value in event_dict.items():
        if key == "event":
            out[key] = value if _is_label(value) else "unlabelled_event"
        elif key in _INTERNAL:
            out[key] = value
        elif key in ALLOWED_FIELDS and ALLOWED_FIELDS[key](value):
            out[key] = value
        else:
            dropped += 1
    if dropped:
        out["dropped_fields"] = dropped
    return out


def filter_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    """Same allowlist for OpenTelemetry span attributes / SSE metadata."""
    return {k: v for k, v in attrs.items() if k in ALLOWED_FIELDS and ALLOWED_FIELDS[k](v) and not isinstance(v, dict)}


_configured = False


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(stream=sys.stdout, level=level.upper(), format="%(message)s")
    # Third-party libraries can log document content at DEBUG; keep them quiet.
    for noisy in ("presidio-analyzer", "presidio-anonymizer", "httpx", "google_genai", "urllib3", "pymupdf"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Uvicorn access logs include full query strings; we emit our own allowlisted request log instead.
    logging.getLogger("uvicorn.access").disabled = True
    renderer = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer(colors=False)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            allowlist_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=False,
    )
    _configured = True


def get_logger():
    return structlog.get_logger("nbkyc")


# --------------------------------------------------------------------------- tracing
_tracer = None


def configure_tracing(service_name: str, endpoint: str) -> None:
    """Metadata-only tracing. Exporter is enabled only when an OTLP endpoint is configured."""
    global _tracer
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("nbkyc")


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    from opentelemetry import trace

    tracer = _tracer or trace.get_tracer("nbkyc")
    with tracer.start_as_current_span(name) as s:
        for k, v in filter_attributes(attrs).items():
            s.set_attribute(k, v)
        yield s


def safe_error_class(exc: BaseException) -> str:
    """Sanitised error class: the exception type name only, never its message."""
    name = type(exc).__name__
    return name if _is_label(name) else "Exception"
