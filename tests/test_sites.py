import json
import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from nodebridge.site_dialog import SiteManagerDialog
from nodebridge.sites import Site, SiteStore


class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_profile_round_trip_with_password(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SiteStore(Path(temporary) / "sites.json")
            site = Site.create("集群 A")
            site = Site(site.id, site.name, "example.invalid", 22, "user", "", "example-only")
            store.save([site])

            self.assertEqual(store.load(), [site])
            data = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 1)
            self.assertEqual(data["sites"][0]["password"], "example-only")

    def test_dialog_saves_password_for_next_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SiteStore(Path(temporary) / "sites.json")
            dialog = SiteManagerDialog(store)
            dialog._new_site()
            dialog.name_edit.setText("计算节点")
            dialog.host_edit.setText("example.invalid")
            dialog.user_edit.setText("user")
            dialog.password_edit.setText("temporary-secret")

            dialog._connect_site()

            self.assertEqual(dialog.selected_site.name, "计算节点")
            self.assertEqual(dialog.connection_password, "temporary-secret")
            self.assertEqual(store.load()[0].host, "example.invalid")
            self.assertEqual(store.load()[0].password, "temporary-secret")
            reopened = SiteManagerDialog(store)
            self.assertEqual(reopened.password_edit.text(), "temporary-secret")


if __name__ == "__main__":
    unittest.main()
