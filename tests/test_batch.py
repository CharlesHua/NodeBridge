import threading
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nodebridge.batch import BatchResult, run_parallel, source_snapshot
from nodebridge.transfer import CopyResult
from test_transfer import DiskBackedSFTP


class BatchTests(unittest.TestCase):
    def test_nodes_run_concurrently_and_failure_does_not_hide_peer_results(self):
        barrier = threading.Barrier(2, timeout=2)
        updates = []

        def operation(alias, _node):
            barrier.wait()
            if alias == "node02":
                raise OSError("failed")
            return BatchResult(alias, copied=CopyResult(1, 0, 3))

        results = run_parallel(
            {"node02": object(), "node03": object()}, operation,
            updates.append, max_workers=2,
        )
        self.assertEqual([result.alias for result in results], ["node02", "node03"])
        self.assertEqual(len(updates), 2)
        self.assertIn("failed", results[0].error)
        self.assertTrue(results[1].complete)

    def test_skipped_or_cancelled_copy_is_not_complete_for_move(self):
        self.assertFalse(BatchResult("node02", copied=CopyResult(0, 0, 0, skipped=1)).complete)
        self.assertFalse(BatchResult("node02", copied=CopyResult(0, 0, 0, cancelled=True)).complete)

    def test_move_snapshot_detects_new_remote_or_local_files(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            folder = base / "data"
            folder.mkdir()
            (folder / "one.txt").write_text("one", encoding="utf-8")
            session = SimpleNamespace(sftp=DiskBackedSFTP(base))
            remote_before = source_snapshot(session, ["/data"])
            local_before = source_snapshot(None, [str(folder)])
            (folder / "two.txt").write_text("two", encoding="utf-8")
            self.assertNotEqual(remote_before, source_snapshot(session, ["/data"]))
            self.assertNotEqual(local_before, source_snapshot(None, [str(folder)]))
