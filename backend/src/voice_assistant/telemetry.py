"""Observability bootstrap — tracing and application logging.

Tracing exports to an OTLP collector (e.g. Jaeger) when
OTEL_EXPORTER_OTLP_ENDPOINT is set, otherwise falls back to printing spans to
the console so it's visible even without any observability stack running.

Logging attaches a formatted handler to the app's ``voice_assistant`` logger
namespace and stamps every line with the active trace/span id, so a log line
and the Jaeger span for the same turn can be lined up by trace id.
"""

import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from voice_assistant.config import settings

_configured = False


def configure_telemetry() -> None:
    global _configured
    if _configured:
        return

    resource = Resource.create({SERVICE_NAME: settings.otel_service_name})
    provider = TracerProvider(resource=resource)

    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
    else:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _configured = True


def get_tracer(name: str = "voice_assistant"):
    return trace.get_tracer(name)


class _TraceContextFilter(logging.Filter):
    """Stamp each record with the active OTel trace/span id (or ``-`` when
    there's no span in context, e.g. connection-level logs), so a log line
    correlates with the Jaeger span for the same turn."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = trace.get_current_span().get_span_context()
        record.trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else "-"
        record.span_id = format(ctx.span_id, "016x") if ctx.is_valid else "-"
        return True


_logging_configured = False


def configure_logging() -> None:
    """Give the app's ``voice_assistant.*`` loggers a formatted stderr handler
    at the configured level. Scoped to the app's own namespace so uvicorn's
    access/error logging is left untouched; ``propagate`` stays on so log
    capture in tests still sees the records."""
    global _logging_configured
    if _logging_configured:
        return

    handler = logging.StreamHandler()
    handler.addFilter(_TraceContextFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s "
            "[trace=%(trace_id)s span=%(span_id)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    app_logger = logging.getLogger("voice_assistant")
    app_logger.setLevel(settings.log_level.upper())
    app_logger.addHandler(handler)
    _logging_configured = True
