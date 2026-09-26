import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snmp


class LinkResolutionTests(unittest.TestCase):
    def links(self, source, targets, aliases=None):
        topology = {"nodes": [{"id": "a", "ports": [source]}, {"id": "b", "ports": targets}]}
        before = copy.deepcopy(topology)
        _, links = snmp.build_links_from_topology(topology, aliases)
        self.assertEqual(topology, before)
        return [link for link in links if link["source"] == "a"]

    def source(self, target_port):
        return {"ifName": "FH0/18", "ifDescr": "FHGigabitEthernet 0/18", "ifAlias": "Link-to-b-" + target_port, "status": 1}

    def test_speed_abbreviation_resolves_to_inventory_name(self):
        links = self.links(self.source("TwentyFiveGigE1/0/42"), [{"ifName": "25GE1/0/42", "status": 0}])
        self.assertEqual(links[0]["targetPort"], "25GE1/0/42")

    def test_exact_inventory_wins_over_conflicting_reverse_description(self):
        ports = [{"ifName": "FourHundredGigE1/0/63"}, {"ifName": "FH0/63", "ifAlias": "Link-to-a-FH0/18"}]
        self.assertEqual(self.links(self.source("400GE1/0/63"), ports)[0]["targetPort"], "FourHundredGigE1/0/63")

    def test_unique_exact_reverse_reference_resolves_vendor_name(self):
        links = self.links(self.source("400GE1/0/63"), [{"ifName": "FH0/63", "ifDescr": "FHGigabitEthernet 0/63", "ifAlias": "Link-to-a-FH0/18"}])
        self.assertEqual(links[0]["targetPort"], "FHGigabitEthernet 0/63")
        self.assertEqual(links[0]["targetPortResolution"], "reciprocal")

    def test_ambiguous_reverse_reference_is_not_guessed(self):
        ports = [{"ifName": name, "ifAlias": "Link-to-a-FH0/18"} for name in ("FH0/63", "FH1/63")]
        self.assertEqual(self.links(self.source("400GE1/0/63"), ports)[0]["targetPort"], "FourHundredGigE1/0/63")

    def test_slot_and_breakout_numbers_are_never_dropped(self):
        for actual in ("FourHundredGigE2/0/63", "FourHundredGigE1/0/63/1", "FH0/63"):
            with self.subTest(actual=actual):
                self.assertEqual(self.links(self.source("400GE1/0/63"), [{"ifName": actual}])[0]["targetPort"], "FourHundredGigE1/0/63")

    def test_explicit_alias_is_device_scoped_and_requires_real_port(self):
        source = self.source("400GE1/0/63")
        aliases = {"b": {"400GE1/0/63": "FH0/63"}}
        self.assertEqual(self.links(source, [{"ifName": "FH0/63", "status": 1}], aliases)[0]["targetPort"], "FH0/63")
        self.assertEqual(self.links(source, [{"ifName": "FH1/63"}], aliases)[0]["targetPort"], "FourHundredGigE1/0/63")
        self.assertEqual(self.links(source, [{"ifName": "FH0/63"}], {"c": aliases["b"]})[0]["targetPort"], "FourHundredGigE1/0/63")

    def test_duplicate_interface_names_are_not_resolved(self):
        ports = [{"ifName": "25GE1/0/42", "ifDescr": name} for name in ("first", "second")]
        self.assertEqual(self.links(self.source("TwentyFiveGigE1/0/42"), ports)[0]["targetPort"], "TwentyFiveGigE1/0/42")

    def test_only_unique_description_is_resolved(self):
        source = {"ifName": "FH0/18", "lldp_peer_name": "b", "lldp_peer_port": "uplink"}
        ports = [{"ifName": "FH0/63", "ifAlias": "uplink"}]
        self.assertEqual(self.links(source, ports)[0]["targetPort"], "FH0/63")
        ports.append({"ifName": "FH1/63", "ifAlias": "uplink"})
        self.assertEqual(self.links(source, ports)[0]["targetPort"], "uplink")

    def test_lldp_port_id_precedes_free_text_description(self):
        self.assertEqual(snmp.select_lldp_peer_port("shared-uplink", "XGigabitEthernet0/0/1"), "XGigabitEthernet0/0/1")
        self.assertEqual(snmp.select_lldp_peer_port("25GE1/0/42", "00:11:22:33:44:55"), "25GE1/0/42")


if __name__ == "__main__":
    unittest.main()
