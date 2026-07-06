"""OpenTelemetry bootstrap.

Exports to an OTLP collector (e.g. Jaeger) when OTEL_EXPORTER_OTLP_ENDPOINT is set,
otherwise falls back to printing spans to the console so tracing is visible even
without any observability stack running.
"""

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
