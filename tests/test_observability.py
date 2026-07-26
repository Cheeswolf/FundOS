import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fundos.core import MetricsRegistry, configure_opentelemetry, trace_request  # noqa: E402


class ObservabilityTests(unittest.TestCase):
    def test_records_histogram_and_prometheus_metrics(self) -> None:
        registry = MetricsRegistry()
        registry.observe(
            method="get",
            path="/products/{product_id}",
            status_code=200,
            duration_seconds=0.02,
        )
        registry.observe(
            method="GET",
            path="/products/{product_id}",
            status_code=200,
            duration_seconds=0.20,
        )
        metric = registry.snapshot()[0]
        self.assertEqual(metric.count, 2)
        self.assertAlmostEqual(metric.duration_seconds_sum, 0.22)
        self.assertAlmostEqual(metric.duration_seconds_max, 0.20)
        self.assertEqual(metric.buckets[0.01], 0)
        self.assertEqual(metric.buckets[0.25], 2)
        rendered = registry.prometheus()
        self.assertIn("fundos_http_requests_total", rendered)
        self.assertIn('path="/products/{product_id}"', rendered)
        self.assertIn('le="+Inf"} 2', rendered)

    def test_rejects_negative_duration(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            MetricsRegistry().observe(
                method="GET",
                path="/health",
                status_code=200,
                duration_seconds=-0.1,
            )

    def test_opentelemetry_is_optional_without_endpoint(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(configure_opentelemetry())

    def test_trace_request_always_provides_trace_id(self) -> None:
        with trace_request(method="GET", path="/health", request_id="request-1") as trace_id:
            self.assertEqual(len(trace_id), 32)
            int(trace_id, 16)


if __name__ == "__main__":
    unittest.main()
