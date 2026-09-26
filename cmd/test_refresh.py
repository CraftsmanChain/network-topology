import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refresh


class RefreshModeTests(unittest.TestCase):
    def test_single_uses_shared_pipeline_with_direct_source(self):
        env = {"TOPOLOGY_MODE": "single", "PROM_QUERY_URL": "http://vm/api/v1/query", "VM_TARGETS_URL": "http://vm:8429/api/v1/targets", "CONFIG_DIR": "/srv/config", "DATA_ROOT": "/srv/data"}
        with patch.dict(os.environ, env, clear=True), patch.object(refresh, "refresh_environment", return_value=True) as shared:
            self.assertEqual(refresh.main(), 0)
        spec = shared.call_args.args[0]
        self.assertEqual(spec["prom_query_url"], env["PROM_QUERY_URL"])
        self.assertEqual(spec["config_dir"], "/srv/config")
        self.assertEqual(spec["data_root"], "/srv/data")

    def test_single_propagates_shared_pipeline_failure(self):
        with patch.dict(os.environ, {"TOPOLOGY_MODE": "single"}, clear=True), patch.object(refresh, "refresh_environment", return_value=False):
            self.assertEqual(refresh.main(), 1)

    def test_multi_uses_registry(self):
        with patch.dict(os.environ, {"TOPOLOGY_MODE": "multi"}, clear=True), patch.object(refresh, "run_once", return_value=True) as multi:
            self.assertEqual(refresh.main(), 0)
            multi.assert_called_once_with()

    def test_multi_scheduler_is_controlled_by_configuration(self):
        with patch.dict(os.environ, {"TOPOLOGY_MODE": "multi", "TOPOLOGY_REFRESH_LOOP": "1"}, clear=True), patch.object(refresh, "run_scheduler", return_value=0) as loop:
            self.assertEqual(refresh.main(), 0)
            loop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
