import threading
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import CancelledError

from nodebridge.resource_monitor import Sample, parse_sample, cpu_percent, query_sample, ResourceMonitor


TEXT = "cpu 100 0 30 800 20 5 5 40 99 0\nMemTotal: 1000 kB\nMemAvailable: 400 kB\nboot-1\n"


class ResourceMonitorTests(unittest.TestCase):
    def test_cpu_delta_memory_and_reboot(self):
        first = parse_sample(TEXT)
        self.assertEqual(first.total, 1000)  # Guest time already belongs to user/nice.
        self.assertEqual(first.memory_used, 600 * 1024)
        self.assertIsNone(cpu_percent(None, first))
        second = Sample(1100, 870, first.memory_total, first.memory_used, "boot-1")
        self.assertEqual(cpu_percent(first, second), 50)
        self.assertIsNone(cpu_percent(second, first))
        self.assertIsNone(cpu_percent(first, Sample(1100, 870, 1, 1, "new-boot")))
        with self.assertRaises(ValueError):
            parse_sample(TEXT.replace("400 kB", "1400 kB"))

    def test_query_is_strict_bounded_and_closes_channel(self):
        host, channel = Mock(), Mock()
        host._client.get_transport.return_value.open_session.return_value = channel
        channel.recv_ready.side_effect = [True, False]
        channel.recv.return_value = TEXT.encode()
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        result = query_sample(host, "cft02", threading.Event())
        command = channel.exec_command.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("BatchMode=yes", command)
        self.assertIn("-- cft02", command)
        self.assertEqual(result.memory_total, 1024000)
        channel.close.assert_called_once()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            query_sample(host, None, cancel)

    def test_monitor_reuses_dedicated_host_and_closes_on_cancel(self):
        monitor = ResourceMonitor(Mock(), "secret")
        monitor.set_targets({"primary": None, "worker:cft02": "cft02"})
        host = Mock()
        sample = parse_sample(TEXT)
        # Stop at the end of this first round, without a real five-second wait.
        with patch("nodebridge.resource_monitor.RemoteSession.connect_shell_host", return_value=host) as connect, \
                patch("nodebridge.resource_monitor.query_sample", return_value=sample) as query, \
                patch.object(monitor.cancel, "wait", side_effect=lambda _: monitor.cancel.set()):
            monitor.run()
        connect.assert_called_once()
        self.assertEqual(query.call_count, 2)
        self.assertTrue(all(call.args[0] is host for call in query.call_args_list))
        host.close.assert_called_once()
        self.assertIsNone(monitor.password)
