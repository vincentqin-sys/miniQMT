# test/test_xtquant_manager/test_watchdog.py
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from xtquant_manager.watchdog import ServerWatchdog


class FakeServer:
    """最小化 fake server，可受控模拟 is_running() 返回值"""
    def __init__(self, running: bool = True):
        self._running = running

    def is_running(self) -> bool:
        return self._running

    def crash(self):
        self._running = False


class TestServerWatchdogInit(unittest.TestCase):
    def test_not_running_before_start(self):
        server = FakeServer(running=True)
        wdog = ServerWatchdog(
            get_server=lambda: server,
            restart_fn=lambda: None,
        )
        self.assertFalse(wdog.is_running())

    def test_get_status_before_start(self):
        server = FakeServer()
        wdog = ServerWatchdog(get_server=lambda: server, restart_fn=lambda: None)
        status = wdog.get_status()
        self.assertFalse(status["running"])
        self.assertEqual(status["restart_count"], 0)


class TestServerWatchdogLifecycle(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer(running=True)
        self.restart_calls = []

    def _make_watchdog(self, check_interval=0.05, restart_cooldown=0.0):
        def restart_fn():
            self.restart_calls.append(time.time())
            self.server._running = True  # 模拟重启成功

        return ServerWatchdog(
            get_server=lambda: self.server,
            restart_fn=restart_fn,
            check_interval=check_interval,
            restart_cooldown=restart_cooldown,
        )

    def test_starts_and_is_running(self):
        wdog = self._make_watchdog()
        wdog.start()
        try:
            time.sleep(0.1)
            self.assertTrue(wdog.is_running())
        finally:
            wdog.stop()

    def test_stop_terminates_thread(self):
        wdog = self._make_watchdog()
        wdog.start()
        wdog.stop(timeout=2.0)
        self.assertFalse(wdog.is_running())

    def test_no_restart_when_server_healthy(self):
        """服务正常时，看门狗不触发重启"""
        wdog = self._make_watchdog(check_interval=0.05)
        wdog.start()
        time.sleep(0.2)
        wdog.stop()
        self.assertEqual(len(self.restart_calls), 0)

    def test_triggers_restart_when_server_down(self):
        """服务崩溃时，看门狗应触发 restart_fn"""
        self.server.crash()  # 服务已停止
        wdog = self._make_watchdog(check_interval=0.05, restart_cooldown=0.0)
        wdog.start()
        # 等待至少 2 次检查周期
        deadline = time.time() + 1.0
        while time.time() < deadline and len(self.restart_calls) == 0:
            time.sleep(0.02)
        wdog.stop()
        self.assertGreater(len(self.restart_calls), 0)

    def test_restart_count_increments(self):
        """每次重启后 restart_count 递增"""
        self.server.crash()
        wdog = self._make_watchdog(check_interval=0.05, restart_cooldown=0.0)
        wdog.start()
        deadline = time.time() + 1.0
        while time.time() < deadline and wdog.get_status()["restart_count"] == 0:
            time.sleep(0.02)
        wdog.stop()
        self.assertGreater(wdog.get_status()["restart_count"], 0)

    def test_cooldown_prevents_rapid_restart(self):
        """冷却期内不重复重启"""
        self.server.crash()
        wdog = self._make_watchdog(check_interval=0.02, restart_cooldown=10.0)
        wdog.start()
        time.sleep(0.3)
        wdog.stop()
        # 冷却 10s，应该只重启一次
        self.assertLessEqual(len(self.restart_calls), 1)

    def test_get_server_returns_none(self):
        """get_server() 返回 None 时也触发重启"""
        restart_called = threading.Event()

        def restart_fn():
            restart_called.set()

        wdog = ServerWatchdog(
            get_server=lambda: None,
            restart_fn=restart_fn,
            check_interval=0.05,
            restart_cooldown=0.0,
        )
        wdog.start()
        triggered = restart_called.wait(timeout=1.0)
        wdog.stop()
        self.assertTrue(triggered)


if __name__ == "__main__":
    unittest.main()
