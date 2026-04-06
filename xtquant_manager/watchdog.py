# xtquant_manager/watchdog.py
"""
ServerWatchdog — HTTP 服务线程看门狗

监控 XtQuantServer 线程是否存活，崩溃后在冷却期过后自动调用 restart_fn。

设计原则（与 HealthMonitor/thread_monitor 一致）:
- 使用 threading.Event.wait() 代替 time.sleep，支持快速 stop()
- 冷却保护防止重启风暴（默认 30s）
- restart_fn 在看门狗线程中同步调用，调用方负责不阻塞太久
"""
import threading
import time
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .server_runner import XtQuantServer

try:
    from logger import get_logger
    logger = get_logger("xqm_watchdog")
except Exception:
    import logging
    logger = logging.getLogger("xtquant_manager.watchdog")


class ServerWatchdog:
    """
    HTTP 服务看门狗。

    Usage:
        def restart():
            global server
            server = XtQuantServer(config)
            server.start(blocking=False)

        wdog = ServerWatchdog(
            get_server=lambda: server,
            restart_fn=restart,
            check_interval=10.0,
            restart_cooldown=30.0,
        )
        wdog.start()
        # ... 运行中 ...
        wdog.stop()
    """

    def __init__(
        self,
        get_server: Callable[[], Optional["XtQuantServer"]],
        restart_fn: Callable[[], None],
        check_interval: float = 10.0,
        restart_cooldown: float = 30.0,
    ):
        """
        Args:
            get_server: 返回当前 XtQuantServer 实例的 callable（lambda: server）
            restart_fn: 重启服务的 callable（负责创建并启动新 XtQuantServer）
            check_interval: 检查周期（秒）
            restart_cooldown: 两次重启的最小间隔（秒），防止重启风暴
        """
        self._get_server = get_server
        self._restart_fn = restart_fn
        self._check_interval = check_interval
        self._restart_cooldown = restart_cooldown

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_restart_time: float = 0.0
        self._restart_count: int = 0
        self._status_lock = threading.Lock()

    def start(self) -> None:
        """启动后台守护线程"""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("ServerWatchdog 已在运行")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._watch_loop,
            name="XtQuantServerWatchdog",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"ServerWatchdog 已启动 "
            f"(check_interval={self._check_interval}s, "
            f"restart_cooldown={self._restart_cooldown}s)"
        )

    def stop(self, timeout: float = 5.0) -> None:
        """停止看门狗线程"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning("ServerWatchdog 线程未在超时内退出")
            else:
                logger.info("ServerWatchdog 已停止")
        self._thread = None

    def is_running(self) -> bool:
        """返回看门狗线程是否在运行"""
        return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict:
        """返回看门狗状态快照"""
        with self._status_lock:
            restart_count = self._restart_count
            last_restart_time = self._last_restart_time
        return {
            "running": self.is_running(),
            "check_interval": self._check_interval,
            "restart_cooldown": self._restart_cooldown,
            "restart_count": restart_count,
            "last_restart_time": last_restart_time,
        }

    # ------------------------------------------------------------------
    # 内部逻辑
    # ------------------------------------------------------------------

    def _watch_loop(self) -> None:
        """主循环：wait() 支持快速响应 stop()"""
        logger.debug("ServerWatchdog 监控循环已开始")
        while not self._stop_event.wait(self._check_interval):
            self._check_server()
        logger.debug("ServerWatchdog 监控循环已结束")

    def _check_server(self) -> None:
        """检查服务是否存活，不健康时触发重启"""
        try:
            server = self._get_server()
            if server is None or not server.is_running():
                if self._can_restart():
                    self._do_restart()
                else:
                    logger.debug("ServerWatchdog: 服务未运行，冷却中，跳过重启")
        except Exception as e:
            logger.error(f"ServerWatchdog 检查时出错: {e}")

    def _can_restart(self) -> bool:
        """检查是否超过冷却期"""
        elapsed = time.time() - self._last_restart_time
        return elapsed >= self._restart_cooldown

    def _do_restart(self) -> None:
        """执行重启"""
        with self._status_lock:
            self._last_restart_time = time.time()
            self._restart_count += 1
            count = self._restart_count
        logger.warning(
            f"ServerWatchdog: 检测到服务停止，触发重启（第 {count} 次）"
        )
        try:
            self._restart_fn()
            logger.info("ServerWatchdog: 重启完成")
        except Exception as e:
            logger.error(f"ServerWatchdog: 重启时出错: {e}")
