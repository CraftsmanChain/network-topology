import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flapping
import snmp


class FlappingTests(unittest.TestCase):
    def setUp(self):
        self.topology = {"nodes": [{"id": "switch-a", "ip": "10.12.1.3", "ports": [
            {"ifIndex": "30", "ifName": "25GE1/0/18", "status": 1}]}]}

    def series(self, count=17, ip="10.12.1.3", index="30"):
        return {"metric": {"instance": ip, "ifIndex": index}, "value": [1790403358, str(count)]}

    def test_threshold_inventory_join_and_duplicate_series(self):
        series = [self.series(5), self.series(6), self.series(17), self.series(9),
                  self.series(18, ip="10.99.0.1"), self.series(20, index="999")]
        result = flapping.build_snapshot(series, self.topology, 1790403358)
        self.assertEqual(len(result["ports"]), 1)
        self.assertEqual(result["ports"][0]["changes"], 17)
        self.assertEqual(result["ports"][0]["device_id"], "switch-a")
        self.assertEqual(result["ports"][0]["port_name"], "25GE1/0/18")
        self.assertEqual(result["unmatched_series"], 2)
        self.assertEqual(result["window_start"], flapping.timestamp(1790403358 - 1200))
        self.assertEqual(result["window_end"], result["updated_at"])

    def test_ambiguous_inventory_does_not_guess(self):
        self.topology["nodes"].append({**self.topology["nodes"][0], "id": "other"})
        self.assertEqual(flapping.build_snapshot([self.series()], self.topology, 1790403358)["ports"], [])

    def test_failure_retains_snapshot_and_successful_empty_clears(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "topology.json").write_text(json.dumps(self.topology))
            with patch.object(flapping, "query_prometheus_result", return_value=([self.series()], True)) as query:
                self.assertTrue(flapping.collect(root))
                query.assert_called_once_with("changes(ifOperStatus[20m]) > 5", require_complete=True)
            original = flapping.load_json(root / "flapping.json", {})
            with patch.object(flapping, "query_prometheus_result", return_value=([], False)):
                self.assertFalse(flapping.collect(root))
            failed = flapping.load_json(root / "flapping.json", {})
            self.assertTrue(failed["stale"])
            self.assertEqual(failed["ports"], original["ports"])
            self.assertEqual(failed["updated_at"], original["updated_at"])
            with patch.object(flapping, "query_prometheus_result", return_value=([], True)):
                self.assertTrue(flapping.collect(root))
            cleared = flapping.load_json(root / "flapping.json", {})
            self.assertFalse(cleared["stale"])
            self.assertEqual(cleared["ports"], [])

    def test_first_failure_has_no_success_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(flapping, "query_prometheus_result", return_value=([], False)):
                self.assertFalse(flapping.collect(Path(directory)))
            self.assertIsNone(flapping.load_json(Path(directory) / "flapping.json", {}).get("updated_at"))

    def test_partial_query_is_not_a_successful_empty_result(self):
        response = Mock()
        response.json.return_value = {"status": "success", "isPartial": True, "data": {"result": []}}
        with patch.object(snmp, "http_get", return_value=response), patch.object(snmp, "request_retries", return_value=1):
            self.assertEqual(snmp.query_prometheus_result(flapping.RULE, require_complete=True), ([], False))


if __name__ == "__main__":
    unittest.main()
