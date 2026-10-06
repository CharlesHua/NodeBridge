import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from nodebridge.site_dialog import SiteManagerDialog
from nodebridge.sites import LockedSiteStore, Site, SiteStore


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
            self.assertEqual(data["version"], 2)
            self.assertEqual(data["password_mode"], "plain")
            self.assertEqual(data["sites"][0]["password"], "example-only")

    def test_master_password_encrypts_and_reopens(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SiteStore(Path(temporary) / "sites.json")
            site = Site("site-id", "集群", "example.invalid", 22, "user", "", "example-only")
            store.save([site], mode="master", master_password="test master phrase")
            raw = store.path.read_text(encoding="utf-8")
            self.assertNotIn("example-only", raw)
            self.assertEqual(store.load(), [site])
            reopened = SiteStore(store.path)
            with self.assertRaises(LockedSiteStore):
                reopened.load()
            with self.assertRaises(ValueError):
                reopened.unlock("wrong phrase")
            reopened.unlock("test master phrase")
            self.assertEqual(reopened.load(), [site])
            reopened.save(reopened.load(), mode="none")
            self.assertNotIn("password", json.loads(store.path.read_text(encoding="utf-8"))["sites"][0])
            self.assertEqual(reopened.load()[0].password, "")

    def test_legacy_file_migrates_on_save(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SiteStore(Path(temporary) / "sites.json")
            legacy = {"version": 1, "sites": [{"id": "site-id", "name": "旧站点", "host": "example.invalid",
                       "port": 22, "username": "user", "password": "old-secret"}]}
            store.path.write_text(json.dumps(legacy), encoding="utf-8")
            sites = store.load()
            store.save(sites, mode="master", master_password="test master phrase")
            self.assertNotIn("old-secret", store.path.read_text(encoding="utf-8"))
            self.assertEqual(store.load(), sites)

    def test_frozen_build_uses_data_beside_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = str(Path(temporary) / "NodeBridge.exe")
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", executable):
                self.assertEqual(SiteStore().path, Path(executable).resolve().parent / "data" / "sites.json")

    def test_forgotten_master_can_reset_passwords_but_keep_sites(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SiteStore(Path(temporary) / "sites.json")
            site = Site("site-id", "集群", "example.invalid", 22, "user", "", "example-only")
            store.save([site], mode="master", master_password="forgotten phrase")
            reopened = SiteStore(store.path)
            reopened.reset_encrypted_passwords()
            self.assertEqual(reopened.mode, "none")
            self.assertEqual(reopened.load()[0], Site("site-id", "集群", "example.invalid", 22, "user"))
            self.assertNotIn("example-only", store.path.read_text(encoding="utf-8"))

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

    def test_dialog_does_not_save_password_in_none_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SiteStore(Path(temporary) / "sites.json")
            dialog = SiteManagerDialog(store)
            dialog._new_site()
            dialog.host_edit.setText("example.invalid")
            dialog.user_edit.setText("user")
            dialog.password_edit.setText("one-time-secret")
            dialog.password_mode.setCurrentIndex(dialog.password_mode.findData("none"))
            dialog._connect_site()
            self.assertEqual(dialog.connection_password, "one-time-secret")
            self.assertEqual(store.load()[0].password, "")
            self.assertNotIn("one-time-secret", store.path.read_text(encoding="utf-8"))

    def test_dialog_master_mode_and_unlock(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = SiteStore(Path(temporary) / "sites.json")
            dialog = SiteManagerDialog(store)
            dialog._new_site()
            dialog.host_edit.setText("example.invalid")
            dialog.user_edit.setText("user")
            dialog.password_edit.setText("example-secret")
            dialog.password_mode.setCurrentIndex(dialog.password_mode.findData("master"))
            with patch("nodebridge.site_dialog.QInputDialog.getText", side_effect=[
                ("test master phrase", True), ("test master phrase", True),
            ]):
                self.assertTrue(dialog._persist())
            self.assertNotIn("example-secret", store.path.read_text(encoding="utf-8"))
            with patch("nodebridge.site_dialog.QInputDialog.getText", return_value=("test master phrase", True)):
                reopened = SiteManagerDialog(SiteStore(store.path))
            self.assertEqual(reopened.password_edit.text(), "example-secret")


if __name__ == "__main__":
    unittest.main()
