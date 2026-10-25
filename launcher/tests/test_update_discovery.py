from pathlib import Path
import sys
import threading
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from update_discovery import (BUSY_RETRY_MS, CHECK_INTERVAL_MS, STARTUP_DELAY_MS,
                              UpdateDiscovery, apply_verified_release)


class Root:
    def __init__(self):
        self.tasks = {}
        self.sequence = 0

    def after(self, delay, callback):
        self.sequence += 1
        self.tasks[self.sequence] = (delay, callback)
        return self.sequence

    def after_cancel(self, key):
        self.tasks.pop(key, None)

    def next(self):
        key = min(self.tasks)
        delay, callback = self.tasks.pop(key)
        callback()
        return delay


class DiscoveryTests(unittest.TestCase):
    def test_background_result_waits_until_transaction_is_idle(self):
        root, busy, applied, jobs = Root(), [True], [], []
        discovery = UpdateDiscovery(root, lambda: busy[0], lambda _cancel: {"version": "fixture"},
                                    applied.append, self.fail, worker=jobs.append)
        discovery.start()
        self.assertEqual(root.next(), STARTUP_DELAY_MS)
        self.assertFalse(jobs)
        busy[0] = False
        self.assertEqual(root.next(), BUSY_RETRY_MS)
        self.assertEqual(len(jobs), 1)
        jobs.pop()()
        busy[0] = True
        root.next()
        self.assertFalse(applied)
        busy[0] = False
        root.next()
        self.assertEqual(applied, [{"version": "fixture"}])
        self.assertEqual(next(iter(root.tasks.values()))[0], CHECK_INTERVAL_MS)
        discovery.close()
        self.assertFalse(root.tasks)

    def test_manual_check_supersedes_stale_automatic_result(self):
        root, applied, jobs = Root(), [], []
        discovery = UpdateDiscovery(root, lambda: False, lambda _cancel: {"version": "stale"},
                                    applied.append, self.fail, worker=jobs.append)
        discovery.start()
        root.next()
        jobs.pop()()
        discovery.supersede()
        root.next()
        self.assertEqual(applied, [])
        discovery.close()

    def test_failed_background_probe_logs_once_without_install_or_dialog(self):
        root, logs = Root(), []
        def fail(_cancel):
            raise FileNotFoundError(2, "No such file or directory")
        discovery = UpdateDiscovery(root, lambda: False, fail, self.fail, logs.append,
                                    worker=lambda work: work())
        discovery.start()
        root.next()
        root.next()
        self.assertEqual(len(logs), 1)
        self.assertIn("Automatic update discovery unavailable", logs[0])
        self.assertEqual(next(iter(root.tasks.values()))[0], CHECK_INTERVAL_MS)
        discovery.close()

    def test_verified_metadata_reuses_original_callback_without_network(self):
        def fetch_release():
            self.fail("Automatic result must not fetch the feed a second time")
        backend = types.SimpleNamespace(fetch_release=fetch_release)
        app = types.SimpleNamespace(_r4_ui_thread=threading.get_ident(), busy=False)
        received = []
        def original_check(target):
            target._run("checking", fetch_release, received.append)
        release = {"version": "fixture"}
        apply_verified_release(app, release, original_check, backend)
        self.assertEqual(received, [release])
        self.assertFalse(hasattr(app, "_run"))
        app.busy = True
        with self.assertRaisesRegex(RuntimeError, "transaction"):
            apply_verified_release(app, release, original_check, backend)


if __name__ == "__main__":
    unittest.main()
