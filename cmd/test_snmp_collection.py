import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import snmp
from atomic_json import write_json_atomic


class PortStatusCollectionTests(unittest.TestCase):
    def setUp(self):
        self.speed = [{"metric": {"ifIndex": "7", "ifName": "Eth7", "ifDescr": "Eth7"}, "value": [0, "100000"]}]
        self.cached = [{"ifIndex": "7", "status": 0, "ifName": "Eth7", "lldp_peer_name": "switch-b", "lldp_peer_port": "Eth8"}]

    def collect(self, oper_result):
        def query(expr):
            if expr.startswith("ifHighSpeed"):
                return self.speed, True
            if expr.startswith("ifOperStatus"):
                return oper_result
            return [], True

        with patch.object(snmp, "query_prometheus_result", side_effect=query), patch.object(snmp, "should_query_lldp_details", return_value=False):
            return snmp.get_interface_data("10.0.0.1", self.cached)

    def test_successful_up_metric_does_not_require_traffic(self):
        ports, complete = self.collect(([{"metric": {"ifIndex": "7"}, "value": [0, "1"]}], True))
        self.assertTrue(complete)
        self.assertEqual(ports[0]["status"], 0)
        self.assertEqual(ports[0]["lldp_peer_name"], "switch-b")

    def test_successful_down_metric_is_down(self):
        ports, complete = self.collect(([{"metric": {"ifIndex": "7"}, "value": [0, "2"]}], True))
        self.assertTrue(complete)
        self.assertEqual(ports[0]["status"], 1)

    def test_missing_status_metric_uses_last_known_state(self):
        ports, complete = self.collect(([], True))
        self.assertFalse(complete)
        self.assertEqual(ports, [])

    def test_status_query_failure_cannot_mark_port_down(self):
        ports, complete = self.collect(([], False))
        self.assertFalse(complete)
        self.assertEqual(ports, [])

    def test_snapshot_is_preserved_when_status_query_fails(self):
        device = {"id": "switch-a", "ip": "10.0.0.1", "status": 0}
        cached = {"switch-a": {"id": "switch-a", "status": 1, "ports": self.cached}}
        with patch.object(snmp, "get_interface_data", return_value=([], False)):
            node = snmp.collect_device_node(device, cached)
        self.assertEqual(node["ports"][0]["status"], 0)
        self.assertEqual(node["status"], 0)
        self.assertTrue(node["collection_stale"])

    def test_partial_lldp_query_keeps_cached_neighbor(self):
        def query(expr):
            if expr.startswith("ifHighSpeed"):
                return self.speed, True
            if expr.startswith("ifOperStatus"):
                return [{"metric": {"ifIndex": "7"}, "value": [0, "1"]}], True
            if expr.startswith("lldpRemPortId"):
                return [], False
            return [], True

        with patch.object(snmp, "query_prometheus_result", side_effect=query), patch.object(snmp, "should_query_lldp_details", return_value=True):
            ports, complete = snmp.get_interface_data("10.0.0.1", self.cached)
        self.assertTrue(complete)
        self.assertEqual(ports[0]["lldp_peer_name"], "switch-b")
        self.assertEqual(ports[0]["lldp_peer_port"], "Eth8")

    def test_atomic_json_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "topology.json"
            write_json_atomic(path, {"nodes": [{"id": "a"}]})
            self.assertEqual(json.loads(path.read_text())["nodes"][0]["id"], "a")
            self.assertEqual(list(Path(directory).glob(".topology.json.*")), [])


if __name__ == "__main__":
    unittest.main()
