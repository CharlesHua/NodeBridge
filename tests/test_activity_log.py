"""Persistent log storage, including bounded rotation."""

import json
import tempfile
import unittest
from pathlib import Path

from nodebridge.activity_log import ActivityLog


class ActivityLogTests(unittest.TestCase):
    def test_utf8_record_and_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nodebridge.log"
            log = ActivityLog(path, max_bytes=280, backups=2)
            for index in range(6):
                log.write(event_id=str(index), category="file", action="复制文件",
                          state="成功", context={"node": "cft02", "sources": ["/中文路径"]})
            self.assertTrue(path.exists())
            self.assertTrue(path.with_name("nodebridge.log.1").exists())
            self.assertFalse(path.with_name("nodebridge.log.3").exists())
            latest = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(latest["event_id"], "5")
            self.assertEqual(latest["action"], "复制文件")
            self.assertEqual(latest["context"]["sources"], ["/中文路径"])


if __name__ == "__main__":
    unittest.main()
