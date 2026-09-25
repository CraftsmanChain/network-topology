import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
