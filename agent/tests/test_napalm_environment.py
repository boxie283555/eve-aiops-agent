import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "nautobot"))
from aiops_napalm.environment import top_environment


class EnvironmentTests(unittest.TestCase):
    def test_modern_mib_top_reports_real_cpu_and_memory(self):
        data = top_environment(
            """top - lab synthetic sample
%Cpu(s): 2.0 us, 1.0 sy, 0.0 ni, 97.0 id, 0.0 wa
MiB Mem : 4096.0 total, 1024.0 free, 2048.0 used, 1024.0 buff/cache
"""
        )
        self.assertEqual(data["cpu"][0]["%usage"], 3.0)
        self.assertEqual(
            data["memory"], {"available_ram": 4194304, "used_ram": 2097152}
        )
        self.assertEqual(data["power"], {})

    def test_legacy_kib_order(self):
        data = top_environment(
            """Cpu(s): 5.0%us, 2.0%sy, 0.0%ni, 93.0%id, 0.0%wa
Mem: 4096k total, 3072k used, 1024k free, 0k buffers
"""
        )
        self.assertEqual(data["cpu"][0]["%usage"], 7.0)
        self.assertEqual(data["memory"]["used_ram"], 3072)

    def test_unknown_and_invalid_data_is_not_healthy_zero(self):
        for value in [
            "",
            "%Cpu(s): 100 id\nMem: 10 total, 20 used, 0 free",
            "%Cpu(s): 101 id\nMem: 10 total, 5 used, 5 free",
        ]:
            with self.assertRaises(ValueError):
                top_environment(value)
