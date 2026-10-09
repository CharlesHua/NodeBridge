import io
import shlex
import stat
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nodebridge.remote import NodeConfig, RemoteSession, UnknownHostKeyError, VerifyHostKey


class RemoteSessionTests(unittest.TestCase):
    def test_sftp_peer_uses_separate_channel_without_closing_browse_session(self):
        client = Mock()
        browse_sftp = Mock()
        peer_sftp = Mock()
        session = RemoteSession(NodeConfig("jump.example", 22, "alice"), client, browse_sftp)
        with patch("nodebridge.remote.paramiko.SFTPClient.from_transport", return_value=peer_sftp) as open_sftp:
            peer = session.open_sftp_peer()
        open_sftp.assert_called_once_with(client.get_transport.return_value)
        self.assertIs(peer.sftp, peer_sftp)
        self.assertIsNot(peer.sftp, browse_sftp)
        peer.close()
        peer_sftp.close.assert_called_once()
        browse_sftp.close.assert_not_called()
        client.close.assert_not_called()

    def test_alias_sftp_peer_keeps_strict_host_key_checking(self):
        root = Mock()
        channel = root.get_transport.return_value.open_session.return_value
        session = RemoteSession(NodeConfig("cft03", 22, ""), None, Mock(),
                                alias_route=("cft02", "cft03"), root_client=root)
        with patch("nodebridge.remote.paramiko.SFTPClient", return_value=Mock()):
            peer = session.open_sftp_peer()
        command = channel.exec_command.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("cft02", command)
        self.assertIn("cft03", command)
        peer.close()
        channel.close.assert_called()
        root.close.assert_not_called()

    def test_sha256_runs_on_alias_node_and_returns_only_digest(self):
        root = Mock()
        transport = root.get_transport.return_value
        transport.is_active.return_value = True
        channel = transport.open_session.return_value
        channel.recv_ready.side_effect = [True, False]
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        digest = "a" * 64
        channel.recv.return_value = (digest + "  /work/a b.txt\n").encode()
        session = RemoteSession(NodeConfig("cft02", 22, ""), None, Mock(),
                                alias_route=("cft02",), root_client=root)

        path = "/work/a b'; touch /tmp/should-not-run; #.txt"
        self.assertEqual(session.sha256_file(path), digest)
        command = channel.exec_command.call_args.args[0]
        self.assertIn("ssh -T", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("sha256sum --", command)
        remote_command = shlex.split(command)[-1]
        self.assertEqual(shlex.split(remote_command)[-1], path)
        channel.close.assert_called_once()

    def test_sha256_rejects_missing_or_invalid_output(self):
        root = Mock()
        transport = root.get_transport.return_value
        transport.is_active.return_value = True
        channel = transport.open_session.return_value
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        session = RemoteSession(NodeConfig("cft02", 22, ""), None, Mock(),
                                alias_route=("cft02",), root_client=root)
        with self.assertRaisesRegex(OSError, "未返回有效"):
            session.sha256_file("/work/a.txt")
        with self.assertRaises(ValueError):
            session.sha256_file("relative.txt")

    def test_sha256_can_use_dedicated_jump_transport(self):
        original = Mock()
        dedicated = Mock()
        channel = dedicated.get_transport.return_value.open_session.return_value
        channel.recv_ready.side_effect = [True, False]
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        channel.recv.return_value = ("b" * 64 + "  /work/a.txt\n").encode()
        session = RemoteSession(NodeConfig("cft02", 22, ""), None, Mock(),
                                alias_route=("cft02",), root_client=original)
        self.assertEqual(session.sha256_file("/work/a.txt", command_client=dedicated), "b" * 64)
        original.get_transport.assert_not_called()
        dedicated.get_transport.return_value.open_session.assert_called_once()

    def test_sha256_files_bundles_names_safely_in_one_command(self):
        root = Mock()
        channel = root.get_transport.return_value.open_session.return_value
        channel.recv_ready.side_effect = [True, False]
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        paths = ["/work/a b.txt", "/work/line\nbreak.py"]
        channel.recv.return_value = b"Cluster notice\n" + b"".join(
            digest.encode() + b"  " + path.encode() + b"\0"
            for digest, path in zip(("a" * 64, "b" * 64), paths)
        )
        session = RemoteSession(NodeConfig("cft02", 22, ""), None, Mock(),
                                alias_route=("cft02",), root_client=root)

        self.assertEqual(session.sha256_files(paths),
                         {paths[0]: "a" * 64, paths[1]: "b" * 64})
        command = channel.exec_command.call_args.args[0]
        self.assertEqual(shlex.split(shlex.split(command)[-1])[-2:], paths)
        self.assertIn("sha256sum -z --", command)
        root.get_transport.return_value.open_session.assert_called_once()

    def test_sha256_files_rejects_truncated_output(self):
        root = Mock()
        channel = root.get_transport.return_value.open_session.return_value
        channel.recv_ready.side_effect = [True, False]
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        channel.recv.return_value = ("a" * 64 + "  /work/a.txt\0").encode()
        session = RemoteSession(NodeConfig("cft02", 22, ""), None, Mock(),
                                alias_route=("cft02",), root_client=root)
        with self.assertRaisesRegex(OSError, "数量"):
            session.sha256_files(["/work/a.txt", "/work/b.txt"])

    def test_probe_alias_checks_ssh_without_accepting_unknown_keys(self):
        client = Mock()
        channel = Mock()
        channel.exit_status_ready.return_value = True
        channel.recv_stderr_ready.return_value = False
        channel.recv_exit_status.return_value = 0
        client.exec_command.return_value = (Mock(), SimpleNamespace(channel=channel), Mock())
        session = RemoteSession(NodeConfig("jump", 22, "alice"), client, Mock())

        self.assertEqual(session.probe_alias("cft02"), ("reachable", "SSH 免密连接成功"))
        command = client.exec_command.call_args.args[0]
        self.assertIn("BatchMode=yes", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("cft02 true", command)
        channel.close.assert_called_once()

    def test_probe_alias_reports_untrusted_host_key(self):
        client = Mock()
        channel = Mock()
        channel.exit_status_ready.return_value = True
        channel.recv_stderr_ready.side_effect = [True, False]
        channel.recv_stderr.return_value = b"Host key verification failed."
        channel.recv_exit_status.return_value = 255
        client.exec_command.return_value = (Mock(), SimpleNamespace(channel=channel), Mock())
        session = RemoteSession(NodeConfig("jump", 22, "alice"), client, Mock())
        state, detail = session.probe_alias("cft02")
        self.assertEqual(state, "untrusted")
        self.assertIn("Host key verification failed", detail)

    def test_discovers_all_numbered_nodes_from_hosts_including_other_prefixes(self):
        sftp = Mock()
        sftp.normalize.return_value = "/home/alice"
        hosts = b"127.0.0.1 localhost\n" + b"".join(
            f"192.0.2.{number} cft{number:02d}\n".encode() for number in range(1, 51)
        ) + b"".join(
            f"198.51.100.{number} ope{number:02d}\n".encode() for number in range(1, 10)
        )
        sftp.open.side_effect = lambda path, _mode: io.BytesIO(hosts if path == "/etc/hosts" else b"")
        session = RemoteSession(NodeConfig("jump", 22, "alice"), Mock(), sftp)
        with patch.object(session, "discover_ssh_aliases", return_value=[]), \
             patch.object(session, "_exec_text", return_value="cft01\n") as execute:
            discovered = session.discover_work_nodes()

        self.assertEqual(len(discovered.aliases), 58)
        self.assertIn("cft50", discovered.aliases)
        self.assertIn("ope01", discovered.aliases)
        self.assertIn("ope09", discovered.aliases)
        self.assertNotIn("cft01", discovered.aliases)
        self.assertNotIn("cft51", discovered.aliases)
        self.assertEqual(discovered.hostname, "cft01")
        execute.assert_called_once_with("hostname -s")

    def test_probes_name_resolution_when_hosts_has_no_numbered_nodes(self):
        sftp = Mock()
        sftp.normalize.return_value = "/home/alice"
        sftp.open.side_effect = lambda path, _mode: io.BytesIO(b"127.0.0.1 localhost\n")
        session = RemoteSession(NodeConfig("jump", 22, "alice"), Mock(), sftp)
        with patch.object(session, "discover_ssh_aliases", return_value=[]), \
             patch.object(session, "_exec_text", side_effect=["cft01\n", "cft02\ncft03\n"]) as execute:
            discovered = session.discover_work_nodes()

        self.assertEqual(discovered.aliases, ("cft02", "cft03"))
        self.assertIn("getent hosts", execute.call_args_list[1].args[0])
        self.assertIn("timeout 20s", execute.call_args_list[1].args[0])

    def test_discovers_explicit_jump_aliases_from_included_ssh_config(self):
        files = {
            "/home/alice/.ssh/config": b"Include conf.d/*.conf\nHost node02 node03 node*\n  HostName example.invalid\n",
            "/home/alice/.ssh/conf.d/extra.conf": b"Host node04\nHost *\n",
            "/etc/ssh/ssh_config": b"Include ssh_config.d/*.conf\n",
            "/etc/ssh/ssh_config.d/cluster.conf": b"Host node05\n",
        }
        sftp = Mock()
        sftp.normalize.return_value = "/home/alice"
        sftp.open.side_effect = lambda path, _mode: io.BytesIO(files[path])
        sftp.listdir.side_effect = lambda path: {
            "/home/alice/.ssh/conf.d": ["extra.conf"],
            "/etc/ssh/ssh_config.d": ["cluster.conf"],
        }[path]
        session = RemoteSession(NodeConfig("jump", 22, "alice"), Mock(), sftp)

        self.assertEqual(session.discover_ssh_aliases(), ["node02", "node03", "node04", "node05"])

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

    def test_terminal_only_jump_does_not_open_sftp_channel(self):
        client = Mock()
        config = NodeConfig("jump", 22, "alice")
        with patch("nodebridge.remote.paramiko.SSHClient", return_value=client):
            host = RemoteSession.connect_shell_host(config)
        client.open_sftp.assert_not_called()
        client.connect.assert_called_once()
        self.assertIsInstance(client.set_missing_host_key_policy.call_args[0][0], VerifyHostKey)
        with self.assertRaises(ConnectionError):
            _ = host.sftp
        host.close()
        client.close.assert_called_once_with()

    def test_authentication_banner_reads_server_notice(self):
        client = Mock()
        client.get_transport().get_banner.return_value = "Welcome before authentication\n"
        session = RemoteSession(NodeConfig("server", 22, "alice"), client, None)
        self.assertEqual(session.authentication_banner(), "Welcome before authentication\n")

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

    def test_alias_shell_opens_pty_without_worker_sftp_session(self):
        jump_client = Mock()
        transport = jump_client.get_transport.return_value
        transport.is_active.return_value = True
        channel = transport.open_session.return_value
        jump = RemoteSession(NodeConfig("cft01", 22, "alice"), jump_client, Mock())

        opened = RemoteSession.open_alias_shell("cft02", jump)

        self.assertIs(opened, channel)
        transport.open_session.assert_called_once_with(timeout=10)
        channel.get_pty.assert_called_once_with(term="xterm", width=100, height=30)
        command = channel.exec_command.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertTrue(command.endswith("cft02"))
        jump_client.open_sftp.assert_not_called()

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

    def test_shell_uses_separate_pty_channel_for_direct_and_alias_sessions(self):
        client = Mock()
        transport = client.get_transport.return_value
        transport.is_active.return_value = True
        channel = transport.open_session.return_value
        direct = RemoteSession(NodeConfig("jump", 22, "alice"), client, Mock())
        self.assertIs(direct.open_shell(), channel)
        channel.get_pty.assert_called_with(term="xterm", width=100, height=30)
        channel.invoke_shell.assert_called_once_with()

        channel.reset_mock()
        alias = RemoteSession(
            NodeConfig("cft02", 22, ""), None, Mock(),
            alias_route=("cft02",), root_client=client,
        )
        self.assertIs(alias.open_shell(), channel)
        command = channel.exec_command.call_args.args[0]
        self.assertIn("ssh -tt", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertTrue(command.endswith("cft02"))
        channel.invoke_shell.assert_not_called()

    def test_failed_shell_setup_closes_only_new_channel(self):
        client = Mock()
        transport = client.get_transport.return_value
        transport.is_active.return_value = True
        channel = transport.open_session.return_value
        channel.get_pty.side_effect = OSError("PTY unavailable")
        session = RemoteSession(NodeConfig("jump", 22, "alice"), client, Mock())
        with self.assertRaisesRegex(OSError, "PTY unavailable"):
            session.open_shell()
        channel.close.assert_called_once_with()
        client.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
