import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from aiops_agent.collector import Collector, DeviceResult


class PublishingTests(unittest.TestCase):
    def test_each_result_published_once(self):
        devices = [SimpleNamespace(name="good"), SimpleNamespace(name="bad")]
        collector = Collector(SimpleNamespace(max_parallel_devices=1), devices)
        results = [
            DeviceResult(devices[0], True, 1),
            DeviceResult(devices[1], False, 1, error="timeout"),
        ]
        collector.collect_device = Mock(side_effect=results)
        collector._publish_metrics = Mock()
        collector.collect_all()
        self.assertEqual(collector._publish_metrics.call_count, 2)
        self.assertEqual(set(collector.latest_results), {"good", "bad"})
