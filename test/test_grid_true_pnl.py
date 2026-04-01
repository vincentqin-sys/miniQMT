"""
网格交易 True P&L (真实盈亏) 验证测试

业界最佳实践:
  True P&L = Realized P&L + Unrealized P&L
           = (total_sell - total_buy) + open_grid_volume * current_price

  open_grid_volume = total_buy_volume - total_sell_volume
  ratio = true_pnl / max_investment

核心修复:
  - 单次买入后: true_pnl = 0 (现金流出 = 持仓价值), 不触发止损
  - 买入后价格下跌: true_pnl 为负但比例合理 (反映真实亏损)
  - DESIGN-4: 多次买入 + 持续下跌 -> true_pnl 持续恶化 -> 触发止损
  - 完整买卖对: true_pnl = 已实现利润

降级路径:
  旧 session 无 volume 数据时, 回退到 get_profit_ratio_by_market_value()

测试结构:
  TestGetTruePnlRatio          -- 直接测试新方法
  TestCheckExitWithTruePnl     -- 通过 _check_exit_conditions 端到端验证
"""

import sys
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from grid_trading_manager import GridTradingManager, GridSession
from grid_database import DatabaseManager

config.ENABLE_SIMULATION_MODE = False
config.ENABLE_GRID_TRADING = True
config.DEBUG_SIMU_STOCK_DATA = True


# ==================== Helper ====================

def make_session(
    total_buy: float = 0,
    total_sell: float = 0,
    buy_volume: int = 0,
    sell_volume: int = 0,
    buy_count: int = None,
    sell_count: int = None,
    max_investment: float = 50000,
    stop_loss: float = -0.10,
    target_profit: float = 0.10,
    center_price: float = 24.86,
    max_deviation: float = 0.15,
) -> GridSession:
    """Factory: create GridSession with True P&L volume tracking"""
    bc = buy_count if buy_count is not None else (1 if total_buy > 0 else 0)
    sc = sell_count if sell_count is not None else (1 if total_sell > 0 else 0)
    return GridSession(
        id=99,
        stock_code='301399.SZ',
        status='active',
        center_price=center_price,
        current_center_price=center_price,
        price_interval=0.05,
        position_ratio=0.20,
        callback_ratio=0.005,
        max_investment=max_investment,
        current_investment=total_buy,
        max_deviation=max_deviation,
        target_profit=target_profit,
        stop_loss=stop_loss,
        end_time=datetime.now() + timedelta(days=7),
        buy_count=bc,
        sell_count=sc,
        total_buy_amount=total_buy,
        total_sell_amount=total_sell,
        total_buy_volume=buy_volume,
        total_sell_volume=sell_volume,
        start_time=datetime.now(),
    )


def make_gtm():
    """Create minimal GTM for _check_exit_conditions tests"""
    mock_pm = MagicMock()
    mock_pm.get_position.return_value = None
    db_manager = DatabaseManager(':memory:')
    db_manager.init_grid_tables()
    gtm = GridTradingManager(
        db_manager=db_manager,
        position_manager=mock_pm,
        trading_executor=None,
    )
    return gtm, db_manager


# ==================== Test 1: Method ====================

class TestGetTruePnlRatio(unittest.TestCase):
    """Direct test of GridSession.get_true_pnl_ratio()"""

    # ---------- Core Fix ----------

    def test_01_single_buy_price_unchanged_pnl_zero(self):
        """
        [Core Fix] Single buy 400 shares at 23.80, price still 23.80:
          true_pnl = (0 - 9520) + 400 * 23.80 = 0
          ratio = 0 / 50000 = 0.00% -> no stop-loss
        """
        session = make_session(total_buy=9520, buy_volume=400)
        ratio = session.get_true_pnl_ratio(23.80)
        self.assertAlmostEqual(ratio, 0.0, places=6,
                               msg=f"Expected 0%, got {ratio*100:.4f}%")

    def test_02_single_buy_small_drop_no_trigger(self):
        """
        Single buy 400 shares at 23.80, price drops to 22.00:
          true_pnl = -9520 + 400*22 = -720
          ratio = -720/50000 = -1.44% > -10% -> no stop-loss
        """
        session = make_session(total_buy=9520, buy_volume=400)
        ratio = session.get_true_pnl_ratio(22.00)
        expected = (-9520 + 400 * 22.00) / 50000
        self.assertAlmostEqual(ratio, expected, places=6)
        self.assertGreater(ratio, session.stop_loss,
                           f"ratio={ratio*100:.2f}% should > -10%")

    def test_03_single_buy_large_drop_no_trigger(self):
        """
        Single buy 400 shares at 23.80, price drops to 20.00:
          true_pnl = -9520 + 400*20 = -1520
          ratio = -1520/50000 = -3.04% > -10% -> no stop-loss
          (small position relative to max_investment)
        """
        session = make_session(total_buy=9520, buy_volume=400)
        ratio = session.get_true_pnl_ratio(20.00)
        self.assertGreater(ratio, session.stop_loss,
                           f"ratio={ratio*100:.2f}% should > -10%")

    # ---------- DESIGN-4 ----------

    def test_04_design4_three_buys_price_crash_triggers(self):
        """
        [DESIGN-4] 3 buys: 1200 shares total 28560, price at 18.00:
          true_pnl = -28560 + 1200*18 = -6960
          ratio = -6960/50000 = -13.92% < -10% -> triggers
        """
        session = make_session(
            total_buy=28560, buy_volume=1200,
            buy_count=3, sell_count=0,
        )
        ratio = session.get_true_pnl_ratio(18.00)
        self.assertLess(ratio, session.stop_loss,
                        f"DESIGN-4 failed: ratio={ratio*100:.2f}% should < -10%")

    def test_05_design4_boundary_triggers(self):
        """
        3 buys: 1200 shares at 28560, price at 19.60:
          true_pnl = -28560 + 1200*19.60 = -5040
          ratio = -5040/50000 = -10.08% < -10% -> triggers
        """
        session = make_session(
            total_buy=28560, buy_volume=1200,
            buy_count=3, sell_count=0,
        )
        ratio = session.get_true_pnl_ratio(19.60)
        self.assertLess(ratio, session.stop_loss,
                        f"ratio={ratio*100:.2f}% should < -10%")

    def test_06_design4_near_boundary_no_trigger(self):
        """
        3 buys: 1200 shares at 28560, price at 19.70:
          true_pnl = -28560 + 1200*19.70 = -4920
          ratio = -4920/50000 = -9.84% > -10% -> no trigger
        """
        session = make_session(
            total_buy=28560, buy_volume=1200,
            buy_count=3, sell_count=0,
        )
        ratio = session.get_true_pnl_ratio(19.70)
        self.assertGreater(ratio, session.stop_loss,
                           f"ratio={ratio*100:.2f}% should > -10%")

    # ---------- Complete pair ----------

    def test_07_complete_pair_true_pnl_equals_realized(self):
        """
        Buy 400@23.80=9520, Sell 400@24.20=9680:
          true_pnl = (9680 - 9520) + 0*24.20 = 160
          ratio = 160/50000 = 0.32%
        """
        session = make_session(
            total_buy=9520, total_sell=9680,
            buy_volume=400, sell_volume=400,
            buy_count=1, sell_count=1,
        )
        ratio = session.get_true_pnl_ratio(24.20)
        expected = (9680 - 9520) / 50000
        self.assertAlmostEqual(ratio, expected, places=6,
                               msg=f"Expected {expected*100:.4f}%, got {ratio*100:.4f}%")

    def test_08_complete_pair_loss(self):
        """
        Buy 400@23.80=9520, Sell 400@22.00=8800:
          true_pnl = (8800 - 9520) + 0 = -720
          ratio = -720/50000 = -1.44%
        """
        session = make_session(
            total_buy=9520, total_sell=8800,
            buy_volume=400, sell_volume=400,
            buy_count=1, sell_count=1,
        )
        ratio = session.get_true_pnl_ratio(22.00)
        expected = (8800 - 9520) / 50000
        self.assertAlmostEqual(ratio, expected, places=6)

    # ---------- Partial pair (realized + unrealized) ----------

    def test_09_partial_pair_with_unrealized(self):
        """
        Buy 800@23.80=19040, Sell 400@24.20=9680:
          open_vol = 800-400 = 400
          true_pnl = (9680 - 19040) + 400*23.80 = -9360 + 9520 = 160
          ratio = 160/50000 = 0.32%
        """
        session = make_session(
            total_buy=19040, total_sell=9680,
            buy_volume=800, sell_volume=400,
            buy_count=2, sell_count=1,
        )
        ratio = session.get_true_pnl_ratio(23.80)
        expected = (9680 - 19040 + 400 * 23.80) / 50000
        self.assertAlmostEqual(ratio, expected, places=6)

    # ---------- Fallback ----------

    def test_10_fallback_no_volume_uses_market_value(self):
        """
        No volume data (old session) -> fallback to get_profit_ratio_by_market_value
        """
        session = make_session(total_buy=9520, buy_volume=0, buy_count=1)
        ratio_true = session.get_true_pnl_ratio(23.80, position_volume=4028)
        ratio_mv = session.get_profit_ratio_by_market_value(4028, 23.80)
        self.assertAlmostEqual(ratio_true, ratio_mv, places=6,
                               msg="Fallback should equal market_value method")

    def test_11_fallback_no_volume_no_position_uses_max_investment(self):
        """
        No volume + no position -> fallback to get_profit_ratio (max_investment)
        """
        session = make_session(total_buy=9520, buy_volume=0, buy_count=1)
        ratio_true = session.get_true_pnl_ratio(23.80, position_volume=0)
        ratio_old = session.get_profit_ratio()
        self.assertAlmostEqual(ratio_true, ratio_old, places=6,
                               msg="Fallback should equal original method")

    # ---------- Bug reproduction ----------

    def test_12_bug_reproduction_old_vs_true_pnl(self):
        """
        [Bug reproduction] Production scenario 2026-04-01:
          Old: -9520/50000 = -19.04% -> triggers (BUG)
          True P&L: -9520 + 400*23.80 = 0 -> 0% -> no trigger (FIXED)
        """
        session = make_session(total_buy=9520, buy_volume=400)

        # Old formula triggers stop-loss
        ratio_old = session.get_profit_ratio()
        self.assertLessEqual(ratio_old, session.stop_loss,
                             f"Old should trigger: {ratio_old*100:.2f}%")

        # True P&L does NOT trigger
        ratio_true = session.get_true_pnl_ratio(23.80)
        self.assertGreater(ratio_true, session.stop_loss,
                           f"True P&L should not trigger: {ratio_true*100:.2f}%")


# ==================== Test 2: End-to-end ====================

class TestCheckExitWithTruePnl(unittest.TestCase):
    """End-to-end via _check_exit_conditions"""

    def setUp(self):
        self.gtm, self.db = make_gtm()

    def tearDown(self):
        self.db.close()

    def _exit(self, session, current_price, position_snapshot=None):
        return self.gtm._check_exit_conditions(
            session, current_price, position_snapshot=position_snapshot
        )

    # ---------- Core Fix ----------

    def test_A_single_buy_no_trigger(self):
        """[Core] Single buy + True P&L -> no exit"""
        session = make_session(total_buy=9520, buy_volume=400)
        pos = {'volume': 4028, 'current_price': 23.80}
        result = self._exit(session, 23.80, pos)
        self.assertIsNone(result, f"Expected no exit, got '{result}'")

    def test_B_old_session_fallback_triggers(self):
        """Old session (no volume) + no snapshot -> fallback -> stop-loss"""
        session = make_session(total_buy=9520, buy_volume=0, buy_count=1)
        result = self._exit(session, 23.80, position_snapshot=None)
        self.assertEqual(result, 'stop_loss', "Fallback should keep old behavior")

    def test_C_old_session_with_position_no_trigger(self):
        """
        Old session (no volume) + position_snapshot -> fallback to market_value
        -9520/(4028*23.80) = -9.93% > -10% -> no trigger
        """
        session = make_session(total_buy=9520, buy_volume=0, buy_count=1)
        pos = {'volume': 4028, 'current_price': 23.80}
        result = self._exit(session, 23.80, pos)
        self.assertIsNone(result, "Fallback market_value should not trigger")

    # ---------- DESIGN-4 ----------

    def test_D_design4_three_buys_triggers(self):
        """[DESIGN-4] 3 buys + price crash -> stop_loss"""
        session = make_session(
            total_buy=28560, buy_volume=1200,
            buy_count=3, sell_count=0,
            center_price=24.86,
            max_deviation=0.30,  # wider to avoid deviation trigger
        )
        session.current_center_price = 21.48  # 3 rebuilds
        pos = {'volume': 4000, 'current_price': 18.00}
        result = self._exit(session, 18.00, pos)
        self.assertEqual(result, 'stop_loss',
                         f"DESIGN-4 should trigger, got '{result}'")

    # ---------- Target Profit ----------

    def test_E_target_profit_with_large_gain(self):
        """Complete pair + large profit -> target_profit"""
        # buy 400@23.80=9520, sell 400@38.00=15200
        # true_pnl = (15200-9520) + 0 = 5680
        # ratio = 5680/50000 = 11.36% > 10%
        session = make_session(
            total_buy=9520, total_sell=15200,
            buy_volume=400, sell_volume=400,
            buy_count=1, sell_count=1,
            max_deviation=0.60,  # allow large price move
        )
        pos = {'volume': 4000, 'current_price': 38.00}
        result = self._exit(session, 38.00, pos)
        self.assertEqual(result, 'target_profit',
                         f"Large gain -> target_profit, got '{result}'")

    # ---------- Other exit conditions ----------

    def test_F_deviation_priority(self):
        """Deviation takes priority over profit check"""
        session = make_session(total_buy=9520, buy_volume=400,
                              center_price=25.00, max_deviation=0.15)
        session.current_center_price = 25.00
        pos = {'volume': 4000, 'current_price': 20.00}
        result = self._exit(session, 20.00, pos)
        self.assertEqual(result, 'deviation')

    def test_G_no_buy_no_check(self):
        """buy_count=0 -> skip profit check"""
        session = make_session(buy_count=0, sell_count=0)
        pos = {'volume': 4000, 'current_price': 23.80}
        result = self._exit(session, 23.80, pos)
        self.assertIsNone(result)

    def test_H_position_cleared(self):
        """volume=0 -> position_cleared"""
        session = make_session(
            total_buy=9520, total_sell=9000,
            buy_volume=400, sell_volume=400,
            buy_count=1, sell_count=1,
        )
        pos = {'volume': 0, 'current_price': 23.80}
        result = self._exit(session, 23.80, pos)
        self.assertEqual(result, 'position_cleared')


if __name__ == '__main__':
    unittest.main(verbosity=2)
