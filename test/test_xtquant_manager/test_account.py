"""
Phase 2 测试：XtQuantAccount
"""
import sys
import time
import threading
import unittest
from unittest.mock import patch, MagicMock

# 将项目根目录加入 path
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from xtquant_manager.account import AccountConfig, XtQuantAccount
from xtquant_manager.exceptions import XtQuantTimeoutError
from test.test_xtquant_manager.mocks import (
    MockXtTrader, MockXtData, MockStockAccount
)


def make_config(**kwargs) -> AccountConfig:
    """创建测试用 AccountConfig"""
    defaults = {
        "account_id": "55009640",
        "qmt_path": "mock/path",
        "account_type": "STOCK",
        "call_timeout": 3.0,
        "reconnect_base_wait": 0.1,  # 测试中缩短等待时间
    }
    defaults.update(kwargs)
    return AccountConfig(**defaults)


def make_account_with_mocks(**kwargs) -> tuple:
    """
    创建 XtQuantAccount，并注入 Mock 的 xt_trader 和 xtdata。
    返回 (account, mock_trader, mock_xtdata)
    """
    config = make_config(**kwargs)
    account = XtQuantAccount(config)

    mock_trader = MockXtTrader()
    mock_xtdata = MockXtData()

    # 直接注入 mock 对象，跳过真实 QMT 连接
    account._xt_trader = mock_trader
    account._acc = MockStockAccount(config.account_id)
    account._xtdata = mock_xtdata
    account._connected = True
    account._connected_at = time.time()
    account._last_ping_ok_time = time.time()

    return account, mock_trader, mock_xtdata


class TestAccountConfig(unittest.TestCase):
    def test_default_values(self):
        config = AccountConfig(account_id="12345", qmt_path="/path")
        self.assertEqual(config.account_type, "STOCK")
        self.assertEqual(config.call_timeout, 3.0)
        self.assertIsNone(config.session_id)

    def test_custom_values(self):
        config = AccountConfig(
            account_id="12345", qmt_path="/path",
            call_timeout=5.0, max_reconnect_attempts=10
        )
        self.assertEqual(config.call_timeout, 5.0)
        self.assertEqual(config.max_reconnect_attempts, 10)


class TestAccountHealthCheck(unittest.TestCase):
    def test_healthy_when_connected(self):
        account, _, _ = make_account_with_mocks()
        self.assertTrue(account.is_healthy())

    def test_unhealthy_when_disconnected(self):
        account, mock_trader, _ = make_account_with_mocks()
        account._connected = False
        self.assertFalse(account.is_healthy())

    def test_unhealthy_when_trader_none(self):
        account, _, _ = make_account_with_mocks()
        account._xt_trader = None
        self.assertFalse(account.is_healthy())

    def test_ping_success(self):
        account, _, mock_xtdata = make_account_with_mocks()
        result = account.ping()
        self.assertTrue(result)
        self.assertIsNotNone(account.last_ping_ok_time)

    def test_ping_failure_when_xtdata_none(self):
        account, _, _ = make_account_with_mocks()
        account._xtdata = None
        result = account.ping()
        self.assertFalse(result)

    def test_ping_failure_when_tick_empty(self):
        account, _, mock_xtdata = make_account_with_mocks()
        mock_xtdata.simulate_tick_failure()
        result = account.ping()
        self.assertFalse(result)

    def test_ping_updates_last_ping_time(self):
        account, _, _ = make_account_with_mocks()
        before = time.time()
        account.ping()
        after = time.time()
        self.assertGreaterEqual(account.last_ping_ok_time, before)
        self.assertLessEqual(account.last_ping_ok_time, after)


class TestAccountConnect(unittest.TestCase):
    def test_connect_success_mock(self):
        """通过 mock xtquant 模块测试 connect 流程"""
        config = make_config()
        account = XtQuantAccount(config)

        mock_trader = MockXtTrader()
        mock_xtdata = MockXtData()

        with patch.dict("sys.modules", {
            "xtquant": MagicMock(),
            "xtquant.xtdata": mock_xtdata,
            "xtquant.xttrader": MagicMock(),
            "xtquant.xttype": MagicMock(),
        }):
            # 模拟 XtQuantTrader 构造和连接
            mock_trader_cls = MagicMock(return_value=mock_trader)
            mock_acc_cls = MagicMock(return_value=MockStockAccount())

            with patch("xtquant_manager.account.XtQuantAccount._connect_xttrader",
                       return_value=True):
                with patch("xtquant_manager.account.XtQuantAccount._connect_xtdata",
                           return_value=True):
                    result = account.connect()

        self.assertTrue(result)
        self.assertTrue(account._connected)

    def test_already_connected_returns_true(self):
        account, _, _ = make_account_with_mocks()
        # 已连接状态再次 connect()
        result = account.connect()
        self.assertTrue(result)

    def test_disconnect_clears_state(self):
        account, _, _ = make_account_with_mocks()
        account.disconnect()
        self.assertFalse(account._connected)
        self.assertIsNone(account._xt_trader)
        self.assertIsNone(account._xtdata)


class TestAccountReconnect(unittest.TestCase):
    def test_reconnect_exponential_backoff(self):
        """验证重连时的指数退避等待时间"""
        config = make_config(reconnect_base_wait=0.05)  # 极短等待
        account = XtQuantAccount(config)

        sleep_calls = []
        original_sleep = time.sleep

        def mock_sleep(seconds):
            sleep_calls.append(seconds)
            original_sleep(min(seconds, 0.01))  # 实际只等极短时间

        with patch("xtquant_manager.account.time.sleep", side_effect=mock_sleep):
            with patch.object(account, "_do_connect", return_value=False):
                account.reconnect()  # 第 1 次失败
                account.reconnect()  # 第 2 次失败

        # 第 1 次: 0.05 * 2^0 = 0.05
        # 第 2 次: 0.05 * 2^1 = 0.10
        self.assertEqual(len(sleep_calls), 2)
        self.assertAlmostEqual(sleep_calls[0], 0.05, places=4)
        self.assertAlmostEqual(sleep_calls[1], 0.10, places=4)

    def test_reconnect_resets_attempts_on_success(self):
        config = make_config(reconnect_base_wait=0.01)
        account = XtQuantAccount(config)
        account._reconnect_attempts = 3

        with patch("xtquant_manager.account.time.sleep"):
            with patch.object(account, "_do_connect", return_value=True):
                result = account.reconnect()

        self.assertTrue(result)
        self.assertEqual(account._reconnect_attempts, 0)

    def test_reconnect_increments_attempts_on_failure(self):
        config = make_config(reconnect_base_wait=0.01)
        account = XtQuantAccount(config)

        with patch("xtquant_manager.account.time.sleep"):
            with patch.object(account, "_do_connect", return_value=False):
                account.reconnect()

        self.assertEqual(account._reconnect_attempts, 1)

    def test_reconnect_max_wait_capped(self):
        """指数退避不超过 3600s"""
        config = make_config(reconnect_base_wait=60.0)
        account = XtQuantAccount(config)
        account._reconnect_attempts = 100  # 很大的次数

        sleep_calls = []
        with patch("xtquant_manager.account.time.sleep",
                   side_effect=lambda s: sleep_calls.append(s)):
            with patch.object(account, "_do_connect", return_value=False):
                account.reconnect()

        self.assertEqual(sleep_calls[0], 3600)  # 上限 3600s


class TestAccountMarketData(unittest.TestCase):
    def test_get_full_tick_success(self):
        account, _, mock_xtdata = make_account_with_mocks()
        result = account.get_full_tick(["000001.SZ"])
        self.assertIn("000001.SZ", result)
        self.assertIn("lastPrice", result["000001.SZ"])

    def test_get_full_tick_returns_empty_when_xtdata_none(self):
        account, _, _ = make_account_with_mocks()
        account._xtdata = None
        result = account.get_full_tick(["000001.SZ"])
        self.assertEqual(result, {})

    def test_get_full_tick_timeout_returns_empty(self):
        account, _, mock_xtdata = make_account_with_mocks(call_timeout=0.1)
        mock_xtdata.simulate_timeout()
        result = account.get_full_tick(["000001.SZ"])
        self.assertEqual(result, {})

    def test_get_full_tick_timeout_recorded_in_metrics(self):
        account, _, mock_xtdata = make_account_with_mocks(call_timeout=0.1)
        mock_xtdata.simulate_timeout()
        account.get_full_tick(["000001.SZ"])
        snap = account.metrics.snapshot()
        self.assertEqual(snap["timeout_calls"], 1)

    def test_get_market_data_ex(self):
        account, _, mock_xtdata = make_account_with_mocks()
        result = account.get_market_data_ex(
            [], ["000001.SZ"], period="1d",
            start_time="20240101", end_time="20241231"
        )
        self.assertIn("000001.SZ", result)


class TestAccountTrading(unittest.TestCase):
    def test_order_stock_buy(self):
        account, mock_trader, _ = make_account_with_mocks()
        order_id = account.order_stock(
            stock_code="000001.SZ",
            order_type=23,  # 买入
            order_volume=100,
            price_type=11,
            price=10.5,
        )
        self.assertGreater(order_id, 0)

    def test_order_stock_sell(self):
        account, mock_trader, _ = make_account_with_mocks()
        mock_trader.add_mock_position("000001.SZ", volume=100, cost_price=10.0)
        order_id = account.order_stock(
            stock_code="000001.SZ",
            order_type=24,  # 卖出
            order_volume=100,
            price_type=11,
            price=11.0,
        )
        self.assertGreater(order_id, 0)

    def test_order_stock_returns_minus1_when_disconnected(self):
        account, mock_trader, _ = make_account_with_mocks()
        account._xt_trader = None
        result = account.order_stock("000001.SZ", 23, 100, 11, 10.5)
        self.assertEqual(result, -1)

    def test_order_stock_timeout_returns_minus1(self):
        account, mock_trader, _ = make_account_with_mocks(call_timeout=0.1)
        mock_trader.simulate_timeout()
        result = account.order_stock("000001.SZ", 23, 100, 11, 10.5)
        self.assertEqual(result, -1)

    def test_cancel_order_success(self):
        account, mock_trader, _ = make_account_with_mocks()
        order_id = account.order_stock("000001.SZ", 23, 100, 11, 10.5)
        result = account.cancel_order(order_id)
        self.assertEqual(result, 0)

    def test_cancel_nonexistent_order(self):
        account, _, _ = make_account_with_mocks()
        result = account.cancel_order(99999)
        self.assertNotEqual(result, 0)

    def test_query_positions_empty(self):
        account, _, _ = make_account_with_mocks()
        result = account.query_positions()
        self.assertEqual(result, [])

    def test_query_positions_with_holdings(self):
        account, mock_trader, _ = make_account_with_mocks()
        mock_trader.add_mock_position("000001.SZ", 100, 10.0)
        mock_trader.add_mock_position("600036.SH", 200, 20.0)
        result = account.query_positions()
        self.assertEqual(len(result), 2)
        codes = [p["证券代码"] for p in result]
        self.assertIn("000001", codes)
        self.assertIn("600036", codes)

    def test_query_positions_columns_compatible(self):
        """返回的字段名与 easy_qmt_trader.position() 一致"""
        account, mock_trader, _ = make_account_with_mocks()
        mock_trader.add_mock_position("000001.SZ", 100, 10.0)
        result = account.query_positions()
        pos = result[0]
        for col in XtQuantAccount.POSITION_COLUMNS:
            self.assertIn(col, pos, f"缺少字段: {col}")

    def test_query_asset(self):
        account, _, _ = make_account_with_mocks()
        result = account.query_asset()
        self.assertIn("可用金额", result)
        self.assertIn("总资产", result)
        self.assertGreater(result["总资产"], 0)

    def test_query_asset_returns_empty_when_disconnected(self):
        account, _, _ = make_account_with_mocks()
        account._xt_trader = None
        result = account.query_asset()
        self.assertEqual(result, {})

    def test_query_orders(self):
        account, mock_trader, _ = make_account_with_mocks()
        account.order_stock("000001.SZ", 23, 100, 11, 10.5)
        result = account.query_orders()
        self.assertEqual(len(result), 1)
        self.assertIn("订单编号", result[0])

    def test_query_trades(self):
        account, _, _ = make_account_with_mocks()
        account.order_stock("000001.SZ", 23, 100, 11, 10.5)
        result = account.query_trades()
        self.assertEqual(len(result), 1)
        self.assertIn("成交编号", result[0])


class TestAccountMetrics(unittest.TestCase):
    def test_successful_calls_recorded(self):
        account, _, _ = make_account_with_mocks()
        account.query_positions()
        account.query_asset()
        snap = account.metrics.snapshot()
        self.assertEqual(snap["total_calls"], 2)
        self.assertEqual(snap["success_calls"], 2)

    def test_failed_calls_recorded(self):
        account, _, mock_xtdata = make_account_with_mocks(call_timeout=0.1)
        mock_xtdata.simulate_timeout()
        account.get_full_tick(["000001.SZ"])
        snap = account.metrics.snapshot()
        self.assertEqual(snap["error_calls"], 1)
        self.assertEqual(snap["timeout_calls"], 1)

    def test_get_state(self):
        account, _, _ = make_account_with_mocks()
        state = account.get_state()
        self.assertTrue(state["connected"])
        self.assertIn("account_id", state)
        self.assertIn("last_ping_ok_time", state)

    def test_id_partial_masking(self):
        """账号 ID 脱敏"""
        account, _, _ = make_account_with_mocks(account_id="55009640")
        masked = account._id()
        self.assertNotEqual(masked, "55009640")
        self.assertIn("***", masked)

    def test_id_short_account(self):
        """短账号 ID 不脱敏"""
        account, _, _ = make_account_with_mocks(account_id="1234")
        masked = account._id()
        self.assertEqual(masked, "1234")


class TestAccountTradeCallback(unittest.TestCase):
    def test_register_callback(self):
        account, _, _ = make_account_with_mocks()
        called = []
        account.register_trade_callback(lambda trade: called.append(trade))
        self.assertEqual(len(account._trade_callbacks), 1)

    def test_multiple_callbacks(self):
        account, _, _ = make_account_with_mocks()
        account.register_trade_callback(lambda t: None)
        account.register_trade_callback(lambda t: None)
        self.assertEqual(len(account._trade_callbacks), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
