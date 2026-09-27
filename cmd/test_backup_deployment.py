import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_deployment as backup


class BackupTests(unittest.TestCase):
    def test_urls_and_multiple_environment_assignments_are_redacted(self):
        value = backup.redact_json({'prom_query_url': 'https://user:password@vm/query?token=secret&tenant=1'})
        self.assertNotIn('password', value['prom_query_url'])
        self.assertNotIn('secret', value['prom_query_url'])
        self.assertIn('tenant=1', value['prom_query_url'])
        text = backup.redact_environment('Environment="FOO=bar" "PROM_BEARER_TOKEN=secret"\n')
        self.assertNotIn('secret', text)

    def test_export_excludes_secrets_history_and_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / 'backup.tar.gz'
            files = {
                'config/.users.local': 'admin:secret',
                'config/.env_sources.local.json': '{"token":"secret"}',
                'config/snmp_private.yml': 'community: secret',
                'config/.history/group_rules.json': '{}',
                '../config/group_rules.json': '{}',
                'icons/._switch.png': 'resource-fork',
                'config/group_rules.json': '{"groups":[]}',
                'config/environments.json': '{"secret_ref":"vm_token","token":"secret"}',
                'config/zy/environment.json': '{"environments":[{"code":"yczy","token":"secret"}]}',
                'systemd/snmp.service': '[Service]\nEnvironment=PROM_BEARER_TOKEN=secret\nEnvironment=ENV_SOURCES_FILE=/srv/secrets.json\n',
                'systemd/snmp@.timer': '[Timer]\nOnUnitInactiveSec=1min\n',
                'systemd/snmp@yczy.service.d/timeout.conf': '[Service]\nTimeoutStartSec=30min\n',
                'systemd/unit-states.txt': 'snmp@yczy.timer enabled enabled\n',
            }
            with tarfile.open(archive_path, 'w:gz') as archive:
                for name, value in files.items():
                    data = value.encode()
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            dest = root / 'public'
            exported = backup.export_archive(archive_path, dest)
            self.assertEqual(len(exported), 7)
            self.assertNotIn('"secret"', (dest / 'config/zy/environment.json').read_text())
            self.assertFalse((dest / 'config/.users.local').exists())
            registry = json.loads((dest / 'config/environments.json').read_text())
            self.assertEqual(registry['secret_ref'], 'vm_token')
            self.assertNotEqual(registry['token'], 'secret')
            service = (dest / 'systemd/snmp.service').read_text()
            self.assertNotIn('PROM_BEARER_TOKEN=secret', service)
            self.assertIn('ENV_SOURCES_FILE=/srv/secrets.json', service)


if __name__ == '__main__':
    unittest.main()
