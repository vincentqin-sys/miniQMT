# test/test_xtquant_manager/test_standalone_config.py
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from xtquant_manager.standalone_config import (
    load_standalone_config,
    StandaloneConfig,
    AccountEntry,
)


class TestLoadStandaloneConfigDefaults(unittest.TestCase):
    """无配置文件时，返回全部默认值"""

    def test_returns_default_config_when_no_file(self):
        cfg = load_standalone_config("/nonexistent/path.json")
        self.assertIsInstance(cfg, StandaloneConfig)
        self.assertEqual(cfg.host, "127.0.0.1")
        self.assertEqual(cfg.port, 8888)
        self.assertEqual(cfg.api_token, "")
        self.assertEqual(cfg.accounts, [])
        self.assertEqual(cfg.heartbeat_interval, 1800.0)
        self.assertEqual(cfg.watchdog_interval, 10.0)
        self.assertEqual(cfg.watchdog_restart_cooldown, 30.0)


class TestLoadStandaloneConfigFromFile(unittest.TestCase):
    """从 JSON 文件加载配置"""

    def _write_config(self, data: dict) -> str:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(data, f)
        f.close()
        return f.name

    def tearDown(self):
        # 清理环境变量
        os.environ.pop("XTQUANT_MANAGER_CONFIG", None)

    def test_loads_basic_fields(self):
        path = self._write_config({
            "host": "0.0.0.0",
            "port": 9999,
            "api_token": "secret123",
            "rate_limit": 120,
        })
        cfg = load_standalone_config(path)
        self.assertEqual(cfg.host, "0.0.0.0")
        self.assertEqual(cfg.port, 9999)
        self.assertEqual(cfg.api_token, "secret123")
        self.assertEqual(cfg.rate_limit, 120)
        os.unlink(path)

    def test_loads_accounts(self):
        path = self._write_config({
            "accounts": [
                {
                    "account_id": "25105132",
                    "qmt_path": "C:/test/userdata_mini",
                    "account_type": "STOCK",
                    "call_timeout": 5.0,
                },
                {
                    "account_id": "25105133",
                    "qmt_path": "C:/test/userdata_mini2",
                },
            ]
        })
        cfg = load_standalone_config(path)
        self.assertEqual(len(cfg.accounts), 2)
        self.assertEqual(cfg.accounts[0].account_id, "25105132")
        self.assertEqual(cfg.accounts[0].call_timeout, 5.0)
        self.assertEqual(cfg.accounts[1].account_id, "25105133")
        self.assertEqual(cfg.accounts[1].account_type, "STOCK")  # 默认值
        os.unlink(path)

    def test_loads_watchdog_and_heartbeat_fields(self):
        path = self._write_config({
            "watchdog_interval": 15.0,
            "watchdog_restart_cooldown": 60.0,
            "heartbeat_interval": 300.0,
        })
        cfg = load_standalone_config(path)
        self.assertEqual(cfg.watchdog_interval, 15.0)
        self.assertEqual(cfg.watchdog_restart_cooldown, 60.0)
        self.assertEqual(cfg.heartbeat_interval, 300.0)
        os.unlink(path)

    def test_env_var_takes_priority(self):
        path = self._write_config({"port": 7777})
        os.environ["XTQUANT_MANAGER_CONFIG"] = path
        cfg = load_standalone_config("")  # 不传路径，依赖环境变量
        self.assertEqual(cfg.port, 7777)
        os.unlink(path)

    def test_loads_security_fields(self):
        path = self._write_config({
            "allowed_ips": ["192.168.1.1", "10.0.0.1"],
            "enable_hmac": True,
            "hmac_secret": "mysecret",
            "ssl_certfile": "/path/to/cert.pem",
            "ssl_keyfile": "/path/to/key.pem",
        })
        cfg = load_standalone_config(path)
        self.assertEqual(cfg.allowed_ips, ["192.168.1.1", "10.0.0.1"])
        self.assertTrue(cfg.enable_hmac)
        self.assertEqual(cfg.hmac_secret, "mysecret")
        self.assertEqual(cfg.ssl_certfile, "/path/to/cert.pem")
        os.unlink(path)


if __name__ == "__main__":
    unittest.main()
