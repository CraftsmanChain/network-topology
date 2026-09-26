import fcntl
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refresh_scheduler as scheduler
import multi_env_refresh as refresh


class Process:
    def __init__(self):
        self.result = None
        self.pid = 100

    def poll(self):
        return self.result


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.registry_path = self.root / 'environments.json'
        self.now = 10000
        self.launched = []

        def spawn(command, **kwargs):
            process = Process()
            self.launched.append((command[-1], process, kwargs))
            return process

        self.scheduler = scheduler.Scheduler(2, clock=lambda: self.now, spawn=spawn)

    def registry(self, *specs):
        return {'environments': [{'code': code, 'data_root': code, 'topology_refresh_sec': interval}
                                 for code, interval in specs]}

    def tick(self, registry):
        self.scheduler.tick(registry, self.registry_path)

    def test_slow_environment_does_not_block_fast_environment_next_cycle(self):
        registry = self.registry(('large', 300), ('small', 60))
        self.tick(registry)
        self.assertEqual(len(self.launched), 2)
        self.launched[1][1].result = 0
        self.now += 10
        self.tick(registry)
        self.now += 59
        self.tick(registry)
        self.assertEqual(len(self.launched), 2)
        self.now += 1
        self.tick(registry)
        self.assertEqual([job[0] for job in self.launched], ['large', 'small', 'small'])
        self.assertEqual(len(self.scheduler.jobs), 2)

    def test_concurrency_limit_and_oldest_due_first(self):
        registry = self.registry(('a', 60), ('b', 60), ('c', 60))
        self.tick(registry)
        self.assertEqual(len(self.launched), 2)
        self.launched[0][1].result = 0
        self.now += 5
        self.tick(registry)
        self.assertEqual([job[0] for job in self.launched], ['a', 'b', 'c'])

    def test_aliases_disabled_environment_and_active_jobs_cannot_duplicate(self):
        registry = self.registry(('a', 60), ('disabled', 60))
        registry['environments'][1]['refresh_enabled'] = False
        registry['environments'].append({'code': 'alias', 'data_root': 'a'})
        self.tick(registry)
        self.tick(registry)
        self.assertEqual(len(self.launched), 1)

    def test_restarts_honor_persisted_finish_time_and_configuration_changes(self):
        root = self.root / 'a'
        refresh.dump_json(root / 'refresh-schedule.json', {'finished_at': scheduler.iso(self.now-10), 'success': True})
        registry = self.registry(('a', 120))
        self.tick(registry)
        self.assertFalse(self.launched)
        self.now += 30
        registry['environments'][0]['topology_refresh_sec'] = 30
        self.tick(registry)
        self.assertEqual(len(self.launched), 1)

    def test_initial_schedule_uses_existing_snapshot_timestamp(self):
        refresh.dump_json(self.root / 'a/status.json', {'checked_at': scheduler.iso(self.now)})
        self.tick(self.registry(('a', 300)))
        self.assertFalse(self.launched)

    def test_failure_retries_after_backoff_without_touching_good_snapshot(self):
        root = self.root / 'a'
        refresh.dump_json(root / 'topology.json', {'nodes': ['existing']})
        registry = self.registry(('a', 300))
        self.tick(registry)
        self.launched[0][1].result = 1
        self.now += 10
        self.tick(registry)
        self.now += 59
        self.tick(registry)
        self.assertEqual(len(self.launched), 1)
        self.now += 1
        self.tick(registry)
        self.assertEqual(len(self.launched), 2)
        self.assertEqual(json.loads((root/'topology.json').read_text()), {'nodes': ['existing']})

    def test_timeout_frees_slot_and_records_failure(self):
        registry = self.registry(('a', 300))
        registry['environments'][0]['refresh_timeout_sec'] = 60
        self.tick(registry)
        self.now += 61
        with patch.object(scheduler, 'stop_process') as stop:
            self.tick(registry)
        stop.assert_called_once()
        self.assertFalse(self.scheduler.jobs)
        self.assertTrue(refresh.load_json(self.root/'a/refresh-schedule.json', {})['timed_out'])

    def test_failed_launch_does_not_stop_other_environments(self):
        self.scheduler.spawn = lambda *args, **kwargs: (_ for _ in ()).throw(OSError('test'))
        self.tick(self.registry(('a', 300)))
        self.assertEqual(refresh.load_json(self.root/'a/refresh-schedule.json', {})['exit_code'], 127)

    def test_shared_data_directory_lock_blocks_manual_duplicate(self):
        with (self.root / '.refresh.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(refresh, 'refresh_environment_unlocked') as collect:
                self.assertFalse(refresh.refresh_environment({'code': 'a', 'data_root': str(self.root)}, {}, self.root))
                collect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
