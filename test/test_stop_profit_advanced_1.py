"""
动态止盈止损高级测试 - 突破和回撤机制
测试两阶段止盈逻辑：阶段1突破监控 -> 阶段2回撤触发

测试用例：
- test_11_profit_breakout_mechanism: 验证首次突破阈值时只标记突破状态、不立即产生交易信号
- test_12_pullback_take_profit_trigger: 验证突破后价格回撤达阈值时触发 take_profit_half 信号
"""

import unittest
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from test.test_base import TestBase
from position_manager import PositionManager
from logger import get_logger

logger = get_logger("test_stop_profit_advanced_1")


class TestStopProfitAdvanced1(TestBase):
    """测试两阶段止盈机制：突破监控和回撤触发"""

    def setUp(self):
        super().setUp()
        self.pm = PositionManager()
        self.pm.stop_sync_thread()
        self._ensure_memory_schema()
        # 清理内存持仓，保证用例隔离
        cursor = self.pm.memory_conn.cursor()
        cursor.execute("DELETE FROM positions")
        self.pm.memory_conn.commit()
        logger.info(f"测试环境初始化完成: {self._testMethodName}")

    def tearDown(self):
        try:
            self.pm.stop_sync_thread()
        finally:
            super().tearDown()

    def _ensure_memory_schema(self):
        """确保内存 positions 表包含止盈突破相关字段"""
        cursor = self.pm.memory_conn.cursor()
        cursor.execute("PRAGMA table_info(positions)")
        cols = {row[1] for row in cursor.fetchall()}
        if "profit_breakout_triggered" not in cols:
            cursor.execute(
                "ALTER TABLE positions ADD COLUMN profit_breakout_triggered BOOLEAN DEFAULT FALSE"
            )
        if "breakout_highest_price" not in cols:
            cursor.execute(
                "ALTER TABLE positions ADD COLUMN breakout_highest_price REAL"
            )
        self.pm.memory_conn.commit()

    def _insert_position(self, **kw):
        """向内存持仓表插入测试数据"""
        stock_code = kw.get("stock_code", "000001.SZ")
        cost_price = kw.get("cost_price", 10.0)
        cursor = self.pm.memory_conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO positions
            (stock_code, volume, available, cost_price, current_price,
             open_date, profit_triggered, highest_price, stop_loss_price,
             profit_breakout_triggered, breakout_highest_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            stock_code,
            kw.get("volume", 1000),
            kw.get("available", kw.get("volume", 1000)),
            cost_price,
            kw.get("current_price", cost_price),
            kw.get("open_date", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            kw.get("profit_triggered", 0),
            kw.get("highest_price", cost_price),
            kw.get("stop_loss_price", cost_price * (1 + config.STOP_LOSS_RATIO)),
            kw.get("profit_breakout_triggered", 0),
            kw.get("breakout_highest_price", 0.0),
        ))
        self.pm.memory_conn.commit()
        return stock_code

    def test_11_profit_breakout_mechanism(self):
        """
        测试11：首次突破止盈阈值时，check_trading_signals 只标记突破状态，不立即产生交易信号

        验证点：
        1. 信号返回 (None, None) —— 不立即下单
        2. profit_breakout_triggered 被置为 True
        3. breakout_highest_price 被记录为当前突破价格
        """
        logger.info("=== 测试11：首次突破止盈阈值的突破标记验证 ===")

        cost_price = 10.0
        # 达到 6% 盈利阈值：恰好触发突破标记逻辑
        breakout_price = cost_price * (1 + config.INITIAL_TAKE_PROFIT_RATIO)  # 10.6

        stock_code = self._insert_position(
            cost_price=cost_price,
            current_price=cost_price,
            profit_triggered=0,
            profit_breakout_triggered=0,
            breakout_highest_price=0.0,
        )
        logger.info(f"持仓已插入: cost={cost_price}, breakout_price={breakout_price:.2f}")

        # 以突破价格调用信号检测
        signal_type, signal_info = self.pm.check_trading_signals(
            stock_code, current_price=breakout_price
        )

        # 断言1: 不立即产生交易信号
        self.assertIsNone(signal_type,
                          "首次突破止盈阈值时应返回 None，不立即触发交易")

        # 断言2: profit_breakout_triggered 已被置为 True
        pos = self.pm.get_position(stock_code)
        self.assertIsNotNone(pos, "内存持仓应存在")
        self.assertEqual(int(pos.get('profit_breakout_triggered', 0)), 1,
                         "突破后 profit_breakout_triggered 应置为 True")

        # 断言3: breakout_highest_price 已被记录
        self.assertAlmostEqual(float(pos.get('breakout_highest_price', 0)),
                               breakout_price, places=2,
                               msg="breakout_highest_price 应等于突破时的当前价格")

        logger.info(f"断言通过: signal=None, "
                    f"profit_breakout_triggered={pos.get('profit_breakout_triggered')}, "
                    f"breakout_highest_price={pos.get('breakout_highest_price'):.2f}")
        logger.info("=== 测试11完成：突破标记逻辑验证通过 ===")

    def test_12_pullback_take_profit_trigger(self):
        """
        测试12：突破后价格回撤达阈值时触发 take_profit_half 信号

        场景：
        - 已突破（profit_breakout_triggered=1，突破后最高价=10.6）
        - 当前价从突破最高价回撤超过 INITIAL_TAKE_PROFIT_PULLBACK_RATIO (0.5%)
        - 应触发 take_profit_half 信号，sell_ratio = INITIAL_TAKE_PROFIT_RATIO_PERCENTAGE (0.6)
        """
        logger.info("=== 测试12：回撤止盈触发 take_profit_half 信号 ===")

        cost_price = 10.0
        breakout_highest = 10.6
        pullback_threshold = config.INITIAL_TAKE_PROFIT_PULLBACK_RATIO  # 0.005
        # 回撤幅度超过阈值：0.5% + 0.1% = 0.6% 回撤
        current_price = round(breakout_highest * (1 - pullback_threshold - 0.001), 4)

        logger.info(f"测试参数: cost={cost_price}, 突破最高={breakout_highest}, "
                    f"当前价={current_price:.4f}, 回撤={((breakout_highest - current_price) / breakout_highest):.4f}")

        stock_code = self._insert_position(
            cost_price=cost_price,
            current_price=current_price,
            profit_triggered=0,
            highest_price=breakout_highest,
            profit_breakout_triggered=1,
            breakout_highest_price=breakout_highest,
        )

        signal_type, signal_info = self.pm.check_trading_signals(
            stock_code, current_price=current_price
        )

        # 断言1: 触发首次止盈信号
        self.assertEqual(signal_type, "take_profit_half",
                         f"回撤超过阈值应触发 take_profit_half，实际: {signal_type}")

        # 断言2: signal_info 包含必要字段
        self.assertIn("sell_ratio", signal_info, "signal_info 应含 sell_ratio")
        self.assertIn("pullback_ratio", signal_info, "signal_info 应含 pullback_ratio")
        self.assertIn("breakout_highest_price", signal_info, "signal_info 应含 breakout_highest_price")

        # 断言3: 卖出比例正确
        self.assertAlmostEqual(signal_info["sell_ratio"],
                               config.INITIAL_TAKE_PROFIT_RATIO_PERCENTAGE, places=4,
                               msg="卖出比例应等于 INITIAL_TAKE_PROFIT_RATIO_PERCENTAGE")

        # 断言4: 回撤比例满足阈值
        self.assertGreaterEqual(signal_info["pullback_ratio"],
                                config.INITIAL_TAKE_PROFIT_PULLBACK_RATIO,
                                "signal_info 中的回撤比例应 >= INITIAL_TAKE_PROFIT_PULLBACK_RATIO")

        logger.info(f"断言通过: signal=take_profit_half, "
                    f"sell_ratio={signal_info['sell_ratio']:.0%}, "
                    f"pullback_ratio={signal_info['pullback_ratio']:.4f}")
        logger.info("=== 测试12完成：回撤止盈触发验证通过 ===")


if __name__ == '__main__':
    unittest.main(verbosity=2)
