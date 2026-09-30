import stat
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nodebridge.remote import NodeConfig, RemoteSession, UnknownHostKeyError, VerifyHostKey


class RemoteSessionTests(unittest.TestCase):
    def test_listing_sorts_directories_and_keeps_remote_metadata(self):
        sftp = Mock()
        sftp.normalize.return_value = "/home/alice"
        sftp.listdir_attr.return_value = [
            SimpleNamespace(filename="z.txt", st_mode=stat.S_IFREG, st_size=12, st_mtime=1700000000),
            SimpleNamespace(filename="data", st_mode=stat.S_IFDIR, st_size=4096, st_mtime=1700000001),
            SimpleNamespace(filename="link", st_mode=stat.S_IFLNK, st_size=4, st_mtime=None),
        ]
        session = RemoteSession(NodeConfig("server", 22, "alice"), Mock(), sftp)

        listing = session.list_directory("/home/alice")

        self.assertEqual(listing.path, "/home/alice")
        self.assertEqual([item.name for item in listing.entries], ["data", "link", "z.txt"])
        self.assertTrue(listing.entries[0].is_dir)
        self.assertTrue(listing.entries[1].is_symlink)
        self.assertEqual(listing.entries[2].path, "/home/alice/z.txt")

    def test_connection_rejects_unknown_hosts_and_closes_on_failure(self):
        client = Mock()
        client.connect.side_effect = OSError("connection failed")
        with patch("nodebridge.remote.paramiko.SSHClient", return_value=client):
            with self.assertRaises(OSError):
                RemoteSession.connect(NodeConfig("server", 22, "alice"))

        client.load_system_host_keys.assert_called_once_with()
        self.assertIsInstance(client.set_missing_host_key_policy.call_args[0][0], VerifyHostKey)
        client.close.assert_called_once_with()

    def test_unknown_host_key_requires_out_of_band_verification(self):
        key = Mock()
        key.asbytes.return_value = b"example-key"
        key.get_name.return_value = "ssh-ed25519"
        with self.assertRaisesRegex(UnknownHostKeyError, "SHA256:"):
            VerifyHostKey().missing_host_key(None, "server", key)

    def test_jump_connection_uses_current_transport_and_verifies_target(self):
        jump_client = Mock()
        jump_client.get_transport.return_value.is_active.return_value = True
        channel = jump_client.get_transport.return_value.open_channel.return_value
        jump = RemoteSession(NodeConfig("jump", 22, "alice"), jump_client, Mock())
        target_client = Mock()
        config = NodeConfig("target", 2222, "bob")

        with patch("nodebridge.remote.paramiko.SSHClient", return_value=target_client):
            target = RemoteSession.connect_via(config, jump, "secret")

        jump_client.get_transport.return_value.open_channel.assert_called_once_with(
            "direct-tcpip", ("target", 2222), ("127.0.0.1", 0), timeout=10
        )
        self.assertEqual(target_client.connect.call_args.kwargs["sock"], channel)
        self.assertEqual(target_client.connect.call_args.kwargs["hostname"], "target")
        self.assertIsInstance(target_client.set_missing_host_key_policy.call_args[0][0], VerifyHostKey)
        target.close()
        channel.close.assert_called()

    def test_failed_jump_connection_closes_tunnel_but_keeps_jump(self):
        jump_client = Mock()
        jump_client.get_transport.return_value.is_active.return_value = True
        channel = jump_client.get_transport.return_value.open_channel.return_value
        jump = RemoteSession(NodeConfig("jump", 22, "alice"), jump_client, Mock())
        target_client = Mock()
        target_client.connect.side_effect = OSError("target unavailable")

        with patch("nodebridge.remote.paramiko.SSHClient", return_value=target_client):
            with self.assertRaises(OSError):
                RemoteSession.connect_via(NodeConfig("target", 22, "bob"), jump)

        channel.close.assert_called_once_with()
        jump_client.close.assert_not_called()

    def test_ssh_alias_uses_jump_hosts_openssh_and_keeps_it_open(self):
        jump_client = Mock()
        jump_client.get_transport.return_value.is_active.return_value = True
        channel = jump_client.get_transport.return_value.open_session.return_value
        jump = RemoteSession(NodeConfig("jump", 22, "alice"), jump_client, Mock())
        with patch("nodebridge.remote.paramiko.SFTPClient", return_value=Mock()) as sftp_type:
            target = RemoteSession.connect_alias("cft02", jump)
            second = RemoteSession.connect_alias("cft03", target)

        first_command = channel.exec_command.call_args_list[0].args[0]
        second_command = channel.exec_command.call_args_list[1].args[0]
        self.assertIn("BatchMode=yes", first_command)
        self.assertIn("StrictHostKeyChecking=yes", first_command)
        self.assertTrue(first_command.endswith("-s cft02 sftp"))
        self.assertIn("cft02", second_command)
        self.assertIn("cft03 sftp", second_command)
        self.assertEqual(sftp_type.call_count, 2)
        target.close()
        second.close()
        jump_client.close.assert_not_called()

    def test_alias_rejects_shell_syntax_and_failure_closes_channel(self):
        jump_client = Mock()
        jump_client.get_transport.return_value.is_active.return_value = True
        jump = RemoteSession(NodeConfig("jump", 22, "alice"), jump_client, Mock())
        with self.assertRaises(ValueError):
            RemoteSession.connect_alias("cft02;touch /tmp/x", jump)
        channel = jump_client.get_transport.return_value.open_session.return_value
        channel.recv_stderr_ready.return_value = True
        channel.recv_stderr.return_value = b"Permission denied (publickey)."
        with patch("nodebridge.remote.paramiko.SFTPClient", side_effect=OSError("EOF")):
            with self.assertRaisesRegex(ConnectionError, "Permission denied"):
                RemoteSession.connect_alias("cft02", jump)
        channel.close.assert_called_once_with()
        jump_client.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
