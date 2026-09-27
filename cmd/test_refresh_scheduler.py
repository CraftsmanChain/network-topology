import fcntl
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refresh_scheduler as policy
import multi_env_refresh as refresh


class TimerPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.spec = {'code': 'a', 'data_root': str(self.root), 'topology_refresh_sec': 60}

    def save(self, **values):
        refresh.dump_json(self.root / 'refresh-schedule.json', {'finished_at': policy.iso(10000), **values})

    def test_minimum_interval_even_after_failure(self):
        for success in (True, False):
            self.save(success=success, duration_sec=30)
            self.assertEqual(policy.due_at(self.spec, self.root), 10600)

    def test_slow_collection_waits_fifteen_minutes_after_finishing(self):
        self.save(duration_sec=300)
        self.assertEqual(policy.due_at(self.spec, self.root), 10600)
        self.save(duration_sec=300.01)
        self.assertEqual(policy.due_at(self.spec, self.root), 10900)
        self.save(duration_sec=1200)
        self.assertEqual(policy.due_at(self.spec, self.root), 10900)

    def test_larger_configured_interval_preserved(self):
        self.save(duration_sec=700)
        self.spec['topology_refresh_sec'] = 1800
        self.assertEqual(policy.due_at(self.spec, self.root), 11800)

    def test_first_install_uses_snapshot_time(self):
        refresh.dump_json(self.root / 'status.json', {'checked_at': policy.iso(10000)})
        self.assertEqual(policy.due_at(self.spec, self.root), 10600)

    def test_not_due_does_not_touch_schedule_or_call_collector(self):
        self.save(duration_sec=700)
        before = (self.root / 'refresh-schedule.json').read_bytes()
        with patch.object(refresh.time, 'time', return_value=10899), patch.object(refresh, 'refresh_environment_unlocked') as collect:
            self.assertTrue(refresh.refresh_environment(self.spec, {}, self.root, scheduled=True))
            collect.assert_not_called()
        self.assertEqual((self.root / 'refresh-schedule.json').read_bytes(), before)

    def test_due_runs_and_records_real_completion(self):
        self.save(duration_sec=700)
        with patch.object(refresh.time, 'time', side_effect=[10900, 10900, 11600]), patch.object(refresh.time, 'monotonic', side_effect=[0, 700]), patch.object(refresh, 'refresh_environment_unlocked', return_value=True) as collect:
            self.assertTrue(refresh.refresh_environment(self.spec, {}, self.root, scheduled=True))
            collect.assert_called_once()
        record = refresh.load_json(self.root / 'refresh-schedule.json', {})
        self.assertEqual(record['duration_sec'], 700)
        self.assertTrue(record['success'])
        self.assertEqual(policy.due_at(self.spec, self.root), 12500)

    def test_shared_directory_lock_blocks_timer_and_manual_duplicate(self):
        with (self.root / '.refresh.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(refresh, 'refresh_environment_unlocked') as collect:
                self.assertFalse(refresh.refresh_environment(self.spec, {}, self.root))
                self.assertTrue(refresh.refresh_environment(self.spec, {}, self.root, scheduled=True))
                collect.assert_not_called()

    def test_global_slots_skip_without_changing_timestamps(self):
        with (self.root / '.refresh-slot-0.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.dict(refresh.os.environ, {'TOPOLOGY_REFRESH_WORKERS': '1'}), patch.object(refresh, 'refresh_environment_unlocked') as collect:
                self.assertTrue(refresh.refresh_environment(self.spec, {}, self.root, scheduled=True, limited=True))
                collect.assert_not_called()
        self.assertFalse((self.root / 'refresh-schedule.json').exists())

    def test_exception_records_failure_and_releases_locks(self):
        with patch.object(refresh, 'refresh_environment_unlocked', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                refresh.refresh_environment(self.spec, {}, self.root, limited=True)
        self.assertFalse(refresh.load_json(self.root / 'refresh-schedule.json', {})['success'])
        self.assertEqual(refresh.COLLECTION_LOCK_FDS.get(), ())
        with patch.object(refresh, 'refresh_environment_unlocked', return_value=True):
            self.assertTrue(refresh.refresh_environment(self.spec, {}, self.root, limited=True))

    def test_alias_and_disabled_environment_do_not_duplicate(self):
        registry = {'environments': [self.spec, {**self.spec, 'code': 'alias'}, {**self.spec, 'code': 'disabled', 'refresh_enabled': False}]}
        with patch.object(refresh, 'read_registry', return_value=(self.root / 'environments.json', registry)), patch.object(refresh, 'read_secrets', return_value={}), patch.object(refresh, 'refresh_environment', return_value=True) as collect:
            self.assertTrue(refresh.run_once(scheduled=True))
            self.assertEqual(collect.call_count, 1)
            self.assertFalse(refresh.run_once(['unknown'], scheduled=True))

    def test_child_inherits_locks(self):
        def collect(*args):
            self.assertEqual(len(refresh.COLLECTION_LOCK_FDS.get()), 2)
            self.assertTrue(refresh.run_step('test', [sys.executable, '-c', 'import os, sys; [os.fstat(int(fd)) for fd in sys.argv[1:]]', *map(str, refresh.COLLECTION_LOCK_FDS.get())], self.root, refresh.os.environ.copy()))
            return True
        with patch.object(refresh, 'refresh_environment_unlocked', side_effect=collect):
            self.assertTrue(refresh.refresh_environment(self.spec, {}, self.root, limited=True))


if __name__ == '__main__':
    unittest.main()
