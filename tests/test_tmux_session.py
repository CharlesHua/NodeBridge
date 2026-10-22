import os
import shlex
import unittest
from concurrent.futures import CancelledError
from types import SimpleNamespace
from unittest.mock import Mock, patch

from nodebridge.tmux_session import TmuxBackend, TmuxChannel, RemoteTask, FORMAT, TMUX


class ProtocolChannel:
    def __init__(self):
        self.data = bytearray(b"%begin 1 1 0\n%end 1 1 0\n")
        self.sent = []
        self.closed = False

    def sendall(self, data):
        self.sent.append(data)
        if data.startswith(b"capture-pane"):
            self.data.extend(b"%begin 2 2 1\nalice@node:~$ \n\n%end 2 2 1\n%output %1 late\\015\\012\n")
        elif data.startswith(b"display-message"):
            self.data.extend(b"%begin 3 3 1\n15,0,0,2\n%end 3 3 1\n")

    def recv_ready(self):
        return bool(self.data)

    def recv(self, count):
        data = bytes(self.data[:count])
        del self.data[:count]
        return data

    def exit_status_ready(self):
        return False

    def close(self):
        self.closed = True


class TmuxTests(unittest.TestCase):
    def test_cancelled_query_closes_only_its_query_channel(self):
        host = SimpleNamespace(_alias_route=(), _root_client=Mock())
        cancel = Mock()
        cancel.is_set.side_effect = [False, True]
        backend = TmuxBackend(host, cancel=cancel)
        with self.assertRaises(CancelledError):
            backend.run("tmux -V")
        channel = host._root_client.get_transport.return_value.open_session.return_value
        channel.close.assert_called_once()
        host._root_client.close.assert_not_called()

    def test_control_protocol_preserves_live_output_and_hex_encodes_input(self):
        raw = ProtocolChannel()
        channel = TmuxChannel(raw, "%1")
        channel.prime()
        screen, prompt = channel.take_initial_screen()
        self.assertIn("alice@node:~$ ", screen)
        self.assertEqual(prompt, "alice@node:~$ ")
        self.assertTrue(channel.recv_ready())
        initial = channel.recv(65536)
        self.assertNotIn(b"alice@node:~$ ", initial)
        self.assertIn(b"late\r\n", initial)
        raw.data.extend(b"%output %2 wrong\n%output %1 \\033[31mred\\033[0m\\015\\012\\134\n")
        self.assertTrue(channel.recv_ready())
        self.assertEqual(channel.recv(65536), b"\x1b[31mred\x1b[0m\r\n\\")
        channel.sendall(b"\x03\r\x1b[A; kill-server\n")
        self.assertTrue(raw.sent[-1].startswith(b"send-keys -t %1 -H 03 0d 1b 5b 41 3b"))
        self.assertNotIn(b"kill-server", raw.sent[-1])
        channel.resize_pty(120, 40)
        self.assertEqual(raw.sent[-1], b"refresh-client -C 120x40\n")
        channel.close()
        self.assertTrue(raw.closed)
        self.assertFalse(any(b"kill-session" in data or data == b"exit\n" for data in raw.sent))

    def test_fragmented_notifications_and_literal_percent_lines(self):
        raw = ProtocolChannel()
        channel = TmuxChannel(raw, "%1")
        channel.prime()
        channel.recv(65536)
        raw.data.extend(b"%output %1 a\\0")
        self.assertFalse(channel.recv_ready())
        raw.data.extend(b"15b\n%extended-output %1 0 : c\\012\n")
        self.assertTrue(channel.recv_ready())
        self.assertEqual(channel.recv(65536), b"a\rbc\n")
        raw.data.extend(b"%begin 4 4 1\n%output %1 this is command output\n%end 4 4 1\n")
        self.assertFalse(channel.recv_ready())

    def test_tmux_errors_do_not_become_shell_output(self):
        raw = ProtocolChannel()
        channel = TmuxChannel(raw, "%1")
        channel.prime()
        raw.data.extend(b"%begin 4 4 1\ninvalid target\n%error 4 4 1\n")
        with self.assertRaisesRegex(OSError, "invalid target"):
            channel.recv_ready()

    def test_scan_filters_names_and_inactive_panes(self):
        def row(name, active="1"):
            return "\t".join((name, "%1", "/work/a b", "python", "1", "123", "group", "label", "1", active, "0"))
        tasks = TmuxBackend.parse_tasks(row("nodebridge-test") + "\n" + row("personal") + "\n" + row("nodebridge-other", "0"))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].path, "/work/a b")
        self.assertEqual(tasks[0].attached, 1)
        backend = TmuxBackend(SimpleNamespace(_alias_route=()))
        with patch.object(backend, "run") as run:
            with self.assertRaises(ValueError):
                backend.kill("personal")
            run.assert_not_called()
            backend.kill("nodebridge-test")
            self.assertEqual(run.call_args.args[0], TMUX + " kill-session -t =nodebridge-test")

    def test_missing_or_old_tmux_is_not_silently_replaced(self):
        backend = TmuxBackend(SimpleNamespace(_alias_route=()))
        with patch.object(backend, "run", return_value="tmux 2.9"):
            with self.assertRaisesRegex(OSError, "3.1"):
                backend.check()

    def test_remote_route_is_strict_and_quoted(self):
        host = SimpleNamespace(_alias_route=("first",), _root_client=Mock())
        backend = TmuxBackend(host, "second")
        backend._open("printf '%s' 'a; b'")
        transport = host._root_client.get_transport.return_value
        command = transport.open_session.return_value.exec_command.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("BatchMode=yes", command)
        self.assertIn("-- first", command)
        self.assertIn("second", shlex.split(command)[-1])


@unittest.skipUnless(os.environ.get("NODEBRIDGE_TMUX_LIVE") == "1", "opt-in Linux tmux integration")
class LiveTmuxTests(unittest.TestCase):
    def test_detach_recover_ansi_input_and_interrupt(self):
        import select
        import subprocess
        import time
        import uuid

        class LocalChannel:
            def __init__(self):
                self.proc = None
                self.closed = False
                self.out_eof = self.err_eof = False
            def exec_command(self, command):
                self.proc = subprocess.Popen(["sh", "-c", command], stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            def settimeout(self, _value):
                pass
            def recv_ready(self):
                return not self.out_eof and bool(select.select([self.proc.stdout], [], [], 0)[0])
            def recv_stderr_ready(self):
                return not self.err_eof and bool(select.select([self.proc.stderr], [], [], 0)[0])
            def recv(self, count):
                data = os.read(self.proc.stdout.fileno(), count)
                self.out_eof = not data
                return data
            def recv_stderr(self, count):
                data = os.read(self.proc.stderr.fileno(), count)
                self.err_eof = not data
                return data
            def exit_status_ready(self):
                return self.proc.poll() is not None
            def recv_exit_status(self):
                return self.proc.wait()
            def sendall(self, data):
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            def close(self):
                self.closed = True
                if self.proc:
                    self.proc.stdin.close()
                    try:
                        self.proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.proc.terminate()
                        self.proc.wait(timeout=2)
                    self.proc.stdout.close()
                    self.proc.stderr.close()

        transport = SimpleNamespace(is_active=lambda: True, open_session=lambda **_kw: LocalChannel())
        host = SimpleNamespace(_alias_route=(), _root_client=SimpleNamespace(get_transport=lambda: transport))
        socket_name = "nodebridge-test-" + uuid.uuid4().hex
        self.addCleanup(lambda: subprocess.run(["tmux", "-L", socket_name, "kill-server"],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        backend = TmuxBackend(host)
        with patch("nodebridge.tmux_session.TMUX", f"tmux -L {socket_name} -f /dev/null"):
            task = backend.create("live-group", "test group")
            try:
                channel = backend.attach(task)
                def read_until(marker, timeout=8):
                    output = bytearray()
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        if channel.recv_ready():
                            output.extend(channel.recv(65536))
                            if marker in output:
                                return bytes(output)
                        time.sleep(0.01)
                    self.fail(f"missing {marker!r}: {bytes(output)!r}")
                channel.resize_pty(100, 30)
                channel.sendall(b"export NB_TEST=retained; cd /tmp; printf '\\033[31mNB_RED\\033[0m\\n'\r")
                colored = read_until(b"\x1b[31mNB_RED")
                self.assertIn(b"\x1b[31m", colored)
                channel.sendall(b" printf '\\033]9;NodeBridgePwd:live:%s\\007' \"$(pwd -P)\"\r")
                self.assertIn(b"\x1b]9;NodeBridgePwd:live:/tmp\x07", read_until(b"\x1b]9;NodeBridgePwd:live:/tmp\x07"))
                # The file is created after disconnecting the control client.
                channel.sendall(b"sleep 1; NB_AFTER=finished; sleep 30\r")
                time.sleep(0.15)
                channel.close()
                time.sleep(1.2)
                found = next(t for t in backend.scan() if t.name == task.name)
                self.assertEqual(found.path, "/tmp")
                channel = backend.attach(found)
                channel.sendall(b"\x03")
                time.sleep(0.15)
                channel.sendall(b"printf 'NB_STATE:%s:%s:%s\\n' \"$NB_TEST\" \"$NB_AFTER\" \"$PWD\"\r")
                output = read_until(b"NB_STATE:retained:finished:/tmp")
                self.assertIn(b"NB_STATE:retained:finished:/tmp", output)
                # A full-screen program's alternate-screen bytes reach xterm unchanged.
                channel.sendall(b"printf '\\033[?1049hNB_ALT\\033[?1049l\\n'\r")
                self.assertIn(b"\x1b[?1049hNB_ALT\x1b[?1049l", read_until(b"\x1b[?1049l"))
                channel.close()
            finally:
                backend.kill(task.name)
            self.assertEqual(backend.scan(), [])
