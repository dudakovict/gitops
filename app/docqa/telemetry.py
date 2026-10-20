"""OpenTelemetry setup: traces and metrics over OTLP/gRPC to the collector.

Endpoint, headers and resource attributes follow the standard OTEL_* environment
variables, so nothing here is cluster-specific. With no OTEL_EXPORTER_OTLP_ENDPOINT
set, telemetry stays local (no exporter) and the instrumentation is a no-op cost.
"""

import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from . import __version__
from .config import get_settings

_configured = False


def configure(service_name: str) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    settings = get_settings()
    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", service_name),
            "service.version": __version__,
            "deployment.environment.name": settings.env,
        }
    )
    tracer_provider = TracerProvider(resource=resource)
    readers = []
    if settings.otel_enabled and os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15000))
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=readers))


tracer = trace.get_tracer("docqa")
meter = metrics.get_meter("docqa")

documents_ingested = meter.create_counter("docqa.documents.ingested", unit="1", description="Documents fully ingested")
documents_failed = meter.create_counter("docqa.documents.failed", unit="1", description="Document ingestions that failed")
chunks_embedded = meter.create_counter("docqa.chunks.embedded", unit="1", description="Chunks embedded and stored")
questions_asked = meter.create_counter("docqa.questions", unit="1", description="Questions answered")
llm_input_tokens = meter.create_counter("docqa.llm.input_tokens", unit="1", description="LLM input tokens")
llm_output_tokens = meter.create_counter("docqa.llm.output_tokens", unit="1", description="LLM output tokens")
answer_latency = meter.create_histogram("docqa.answer.duration", unit="s", description="Time to first token of an answer")
ingest_latency = meter.create_histogram("docqa.ingest.duration", unit="s", description="Wall time to ingest one document")
