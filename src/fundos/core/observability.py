from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
import os
from threading import Lock
from typing import Iterator
from uuid import uuid4


HISTOGRAM_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


@dataclass(frozen=True, slots=True)
class HttpMetric:
    method: str
    path: str
    status_code: int
    count: int
    duration_seconds_sum: float
    duration_seconds_max: float
    buckets: dict[float, int]


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._values: dict[tuple[str, str, int], dict[str, object]] = defaultdict(
            lambda: {
                "count": 0,
                "sum": 0.0,
                "max": 0.0,
                "buckets": {bucket: 0 for bucket in HISTOGRAM_BUCKETS},
            }
        )

    def observe(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        if duration_seconds < 0:
            raise ValueError("request duration cannot be negative")
        key = (method.upper(), path, status_code)
        with self._lock:
            value = self._values[key]
            value["count"] = int(value["count"]) + 1
            value["sum"] = float(value["sum"]) + duration_seconds
            value["max"] = max(float(value["max"]), duration_seconds)
            buckets = value["buckets"]
            assert isinstance(buckets, dict)
            for bucket in HISTOGRAM_BUCKETS:
                if duration_seconds <= bucket:
                    buckets[bucket] = int(buckets[bucket]) + 1

    def snapshot(self) -> list[HttpMetric]:
        with self._lock:
            return [
                HttpMetric(
                    method,
                    path,
                    status_code,
                    int(value["count"]),
                    float(value["sum"]),
                    float(value["max"]),
                    dict(value["buckets"]),
                )
                for (method, path, status_code), value in sorted(self._values.items())
            ]

    def prometheus(self) -> str:
        lines = [
            "# HELP fundos_http_requests_total Total HTTP requests.",
            "# TYPE fundos_http_requests_total counter",
            "# HELP fundos_http_request_duration_seconds HTTP request latency.",
            "# TYPE fundos_http_request_duration_seconds histogram",
            "# HELP fundos_http_request_duration_seconds_max Maximum observed HTTP request latency.",
            "# TYPE fundos_http_request_duration_seconds_max gauge",
        ]
        for item in self.snapshot():
            labels = (
                f'method="{_label(item.method)}",path="{_label(item.path)}",'
                f'status="{item.status_code}"'
            )
            lines.append(f"fundos_http_requests_total{{{labels}}} {item.count}")
            for bucket, count in item.buckets.items():
                lines.append(
                    "fundos_http_request_duration_seconds_bucket"
                    f'{{{labels},le="{bucket:g}"}} {count}'
                )
            lines.append(
                "fundos_http_request_duration_seconds_bucket"
                f'{{{labels},le="+Inf"}} {item.count}'
            )
            lines.append(
                f"fundos_http_request_duration_seconds_sum{{{labels}}} "
                f"{item.duration_seconds_sum:.9f}"
            )
            lines.append(
                f"fundos_http_request_duration_seconds_count{{{labels}}} {item.count}"
            )
            lines.append(
                f"fundos_http_request_duration_seconds_max{{{labels}}} "
                f"{item.duration_seconds_max:.9f}"
            )
        return "\n".join(lines) + "\n"


def configure_opentelemetry(*, service_name: str = "fundos-api") -> bool:
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return False
    resolved_service_name = os.environ.get("OTEL_SERVICE_NAME", service_name).strip()
    provider = TracerProvider(
        resource=Resource.create({"service.name": resolved_service_name})
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter())
    )
    trace.set_tracer_provider(provider)
    return True


@contextmanager
def trace_request(
    *,
    method: str,
    path: str,
    request_id: str,
) -> Iterator[str]:
    fallback_trace_id = uuid4().hex
    try:
        from opentelemetry import trace
    except ImportError:
        yield fallback_trace_id
        return
    tracer = trace.get_tracer("fundos.api")
    with tracer.start_as_current_span(
        f"{method.upper()} {path}",
        attributes={
            "http.request.method": method.upper(),
            "url.path": path,
            "fundos.request_id": request_id,
        },
    ) as span:
        context = span.get_span_context()
        trace_id = f"{context.trace_id:032x}" if context.trace_id else fallback_trace_id
        yield trace_id


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
