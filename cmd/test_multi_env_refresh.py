import sys
import tempfile
import unittest
import os
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import multi_env_refresh as refresh


class LLDPRefreshTests(unittest.TestCase):
    def test_gateway_lldp_refresh_interval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = {"lldp_refresh_sec": 3600}
            self.assertTrue(refresh.lldp_refresh_due(spec, root))
            refresh.dump_json(root / "lldp-refresh.json", {"checked_at": refresh.iso_now()})
            self.assertFalse(refresh.lldp_refresh_due(spec, root))
            self.assertTrue(refresh.lldp_refresh_due({"lldp_refresh_sec": 0}, root))

    def test_both_sources_derive_links_after_collection(self):
        for source in ("http://vm/api/v1/query", "https://proxy/query-gateway/source"):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                spec = {"code": "test", "config_dir": str(root), "data_root": str(root), "prom_query_url": source}
                with patch.object(refresh, "run_step", return_value=True) as step, patch.object(refresh, "ensure_links", return_value=True) as links:
                    self.assertTrue(refresh.refresh_environment(spec, {}, root))
                self.assertEqual([Path(call.args[1][1]).name for call in step.call_args_list], ["devices.py", "snmp.py", "flapping.py"])
                self.assertTrue(links.call_args.kwargs["force"])

    def test_failed_link_derivation_does_not_publish_old_raw_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            refresh.dump_json(root / "topology.json", {"nodes": []})
            refresh.dump_json(root / "links.json", [{"old": True}])
            refresh.dump_json(root / "links-alias.json", [{"stale": True}])
            with patch.object(refresh, "run_step", return_value=False):
                self.assertFalse(refresh.ensure_links(root, {}, force=True))
            self.assertEqual(refresh.load_json(root / "links.json", None), [{"old": True}])

    def test_environment_credentials_and_targets_are_isolated(self):
        with patch.dict(os.environ, {"PROM_BEARER_TOKEN": "other-environment", "VM_TARGETS_URL": "http://wrong-vm", "PROM_SKIP_TLS_VERIFY": "1"}):
            env = refresh.build_env_vars({"prom_query_url": "http://vm"}, "", Path("/tmp/config"))
        self.assertNotIn("PROM_BEARER_TOKEN", env)
        self.assertEqual(env["VM_TARGETS_URL"], "")
        self.assertEqual(env["PROM_SKIP_TLS_VERIFY"], "0")


if __name__ == "__main__":
    unittest.main()
