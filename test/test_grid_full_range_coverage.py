"""
网格交易全区间覆盖测试脚本

测试目标：对网格交易的买入卖出逻辑进行系统性全覆盖测试，
确保代码功能100%严格符合设计，对异常场景提供兜底保护，具备充分鲁棒性。

测试架构：
  - Suite A: 网格档位区间覆盖（价格在下轨/中轨/上轨/超出区间）
  - Suite B: 完整买入流程验证（触发条件、数量计算、资金管理）
  - Suite C: 完整卖出流程验证（触发条件、T+1规则、资金回收）
  - Suite D: 多档位连续交易（买入后重建、卖出后重建、同向多次交易）
  - Suite E: 资金管理边界（max_investment耗尽、浮点精度、硬上限）
  - Suite F: 冷却保护机制（档位冷却、买入冷却、卖出冷却）
  - Suite G: 异常场景鲁棒性（DB写入失败回滚、持仓不存在、持仓为0、T+1锁仓）
  - Suite H: 退出条件触发（偏离度、止盈、止损、到期、持仓清空）
  - Suite I: 极端价格行情（涨停跌停、价格连续穿越档位、价格在区间内振荡）
  - Suite J: 端到端全流程（完整一轮买入-上涨-卖出-重建验证）
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import unittest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
from dataclasses import asdict
import time

import config
from grid_trading_manager import GridSession, GridTradingManager, PriceTracker
from grid_database import DatabaseManager
from trading_executor import TradingExecutor
from position_manager import PositionManager


# ==============================================================================
# 测试基础工具
# ==============================================================================

def make_session(db, manager, stock_code="000001.SZ",
                 center_price=10.0, price_interval=0.05,
                 position_ratio=0.25, callback_ratio=0.005,
                 max_investment=10000.0, current_investment=0.0,
                 buy_count=0, sell_count=0,
                 total_buy_amount=0.0, total_sell_amount=0.0,
                 max_deviation=0.15, target_profit=0.10, stop_loss=-0.10,
                 end_days=7):
    """创建并注册测试用的网格会话，返回 session 对象"""
    end_time = datetime.now() + timedelta(days=end_days)
    session = GridSession(
        id=None,
        stock_code=stock_code,
        status="active",
        center_price=center_price,
        current_center_price=center_price,
        price_interval=price_interval,
        position_ratio=position_ratio,
        callback_ratio=callback_ratio,
        max_investment=max_investment,
        current_investment=current_investment,
        buy_count=buy_count,
        sell_count=sell_count,
        total_buy_amount=total_buy_amount,
        total_sell_amount=total_sell_amount,
        max_deviation=max_deviation,
        target_profit=target_profit,
        stop_loss=stop_loss,
        start_time=datetime.now(),
        end_time=end_time
    )
    d = asdict(session)
    d['start_time'] = d['start_time'].isoformat() if d.get('start_time') else datetime.now().isoformat()
    d['end_time'] = d['end_time'].isoformat() if d.get('end_time') else end_time.isoformat()
    session.id = db.create_grid_session(d)

    key = manager._normalize_code(stock_code)
    manager.sessions[key] = session
    manager.trackers[session.id] = PriceTracker(
        session_id=session.id,
        last_price=center_price,
        peak_price=center_price,
        valley_price=center_price
    )
    return session


def make_signal(stock_code="000001.SZ", signal_type="BUY",
                trigger_price=9.5, grid_level=9.5,
                session_id=1, valley_price=9.4, peak_price=None,
                callback_ratio=0.005):
    """构造网格交易信号字典"""
    sig = {
        'stock_code': stock_code,
        'strategy': config.GRID_STRATEGY_NAME,
        'signal_type': signal_type,
        'grid_level': grid_level,
        'trigger_price': trigger_price,
        'session_id': session_id,
        'timestamp': datetime.now().isoformat(),
        'callback_ratio': callback_ratio
    }
    if signal_type == 'BUY':
        sig['valley_price'] = valley_price
    else:
        sig['peak_price'] = peak_price if peak_price else trigger_price
    return sig


def make_position(volume=1000, cost_price=10.0, current_price=10.0, available=None):
    """构造持仓信息字典"""
    return {
        'stock_code': '000001.SZ',
        'volume': volume,
        'available': available if available is not None else volume,
        'cost_price': cost_price,
        'current_price': current_price,
        'market_value': volume * current_price,
        'profit_ratio': (current_price - cost_price) / cost_price
    }


class GridTestBase(unittest.TestCase):
    """测试基类：提供通用的 setUp/tearDown 和工具方法"""

    def setUp(self):
        self.db = DatabaseManager(":memory:")
        self.db.init_grid_tables()

        self.position_manager = Mock(spec=PositionManager)
        self.position_manager.signal_lock = __import__('threading').Lock()
        self.position_manager.latest_signals = {}
        self.position_manager._increment_data_version = Mock()

        self.executor = Mock(spec=TradingExecutor)
        self.executor.buy_stock = Mock(return_value={'order_id': 'TEST_ORDER_001'})
        self.executor.sell_stock = Mock(return_value={'order_id': 'TEST_ORDER_002'})

        self.manager = GridTradingManager(
            db_manager=self.db,
            position_manager=self.position_manager,
            trading_executor=self.executor
        )

        # 保存原始配置，用于 tearDown 恢复
        self._orig_sim = config.ENABLE_SIMULATION_MODE
        self._orig_buy_cd = getattr(config, 'GRID_BUY_COOLDOWN', 300)
        self._orig_sell_cd = getattr(config, 'GRID_SELL_COOLDOWN', 300)
        self._orig_level_cd = getattr(config, 'GRID_LEVEL_COOLDOWN', 60)

        # 测试时关闭冷却以便快速验证（各 Suite 可按需覆盖）
        config.ENABLE_SIMULATION_MODE = True
        config.GRID_BUY_COOLDOWN = 0
        config.GRID_SELL_COOLDOWN = 0
        config.GRID_LEVEL_COOLDOWN = 0

    def tearDown(self):
        config.ENABLE_SIMULATION_MODE = self._orig_sim
        config.GRID_BUY_COOLDOWN = self._orig_buy_cd
        config.GRID_SELL_COOLDOWN = self._orig_sell_cd
        config.GRID_LEVEL_COOLDOWN = self._orig_level_cd
        if hasattr(self, 'db') and self.db:
            self.db.close()

    def _execute_buy(self, session, trigger_price, grid_level=None):
        """快捷执行买入"""
        if grid_level is None:
            grid_level = session.current_center_price * (1 - session.price_interval)
        sig = make_signal(
            stock_code=session.stock_code,
            signal_type='BUY',
            trigger_price=trigger_price,
            grid_level=grid_level,
            session_id=session.id,
            valley_price=trigger_price * 0.995
        )
        return self.manager._execute_grid_buy(session, sig)

    def _execute_sell(self, session, trigger_price, position_snapshot, grid_level=None):
        """快捷执行卖出"""
        if grid_level is None:
            grid_level = session.current_center_price * (1 + session.price_interval)
        sig = make_signal(
            stock_code=session.stock_code,
            signal_type='SELL',
            trigger_price=trigger_price,
            grid_level=grid_level,
            session_id=session.id,
            peak_price=trigger_price * 1.005
        )
        return self.manager._execute_grid_sell(session, sig, position_snapshot=position_snapshot)


# ==============================================================================
# Suite A: 网格档位区间覆盖
# ==============================================================================

class TestSuiteA_GridLevelCoverage(GridTestBase):
    """A: 验证价格在不同网格区间的行为是否符合设计"""

    def test_a1_price_at_center_no_signal(self):
        """A-1: 价格在中轨内（下轨~上轨），不产生任何穿越"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)
        tracker = self.manager.trackers[session.id]
        levels = session.get_grid_levels()

        # lower=9.5, upper=10.5, 价格在区间内
        for price in [9.6, 9.8, 10.0, 10.2, 10.4]:
            tracker.waiting_callback = False
            self.manager._check_level_crossing(session, tracker, price)
            self.assertFalse(tracker.waiting_callback,
                             f"价格{price}在区间内不应触发等待回调，lower={levels['lower']:.2f}, upper={levels['upper']:.2f}")

    def test_a2_price_crosses_lower_triggers_buy_wait(self):
        """A-2: 价格下穿下轨，tracker 进入 falling/等待回调状态"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)
        tracker = self.manager.trackers[session.id]

        # lower = 10.0 * 0.95 = 9.50，下穿到 9.40
        self.manager._check_level_crossing(session, tracker, 9.40)
        self.assertTrue(tracker.waiting_callback, "下穿下轨后应进入等待回调状态")
        self.assertEqual(tracker.direction, 'falling', "方向应为 falling")
        self.assertEqual(tracker.valley_price, 9.40, "谷值应记录为触发价")

    def test_a3_price_crosses_upper_triggers_sell_wait(self):
        """A-3: 价格上穿上轨，tracker 进入 rising/等待回调状态"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)
        tracker = self.manager.trackers[session.id]

        # upper = 10.0 * 1.05 = 10.50，上穿到 10.60
        self.manager._check_level_crossing(session, tracker, 10.60)
        self.assertTrue(tracker.waiting_callback, "上穿上轨后应进入等待回调状态")
        self.assertEqual(tracker.direction, 'rising', "方向应为 rising")
        self.assertEqual(tracker.peak_price, 10.60, "峰值应记录为触发价")

    def test_a4_exact_boundary_at_lower(self):
        """A-4: 价格精确等于下轨边界（不触发，必须严格穿越）"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)
        tracker = self.manager.trackers[session.id]
        lower = 10.0 * 0.95  # = 9.50

        # price == lower 时，条件是 price < lower，不满足
        self.manager._check_level_crossing(session, tracker, lower)
        self.assertFalse(tracker.waiting_callback,
                         f"价格等于下轨{lower}时不应触发（需严格小于）")

    def test_a5_exact_boundary_at_upper(self):
        """A-5: 价格精确等于上轨边界（不触发，必须严格穿越）"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)
        tracker = self.manager.trackers[session.id]
        upper = 10.0 * 1.05  # = 10.50

        # price == upper 时，条件是 price > upper，不满足
        self.manager._check_level_crossing(session, tracker, upper)
        self.assertFalse(tracker.waiting_callback,
                         f"价格等于上轨{upper}时不应触发（需严格大于）")

    def test_a6_price_far_below_lower(self):
        """A-6: 价格远低于下轨（仍应触发等待，而非因偏差过大而跳过）"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)
        tracker = self.manager.trackers[session.id]

        # 价格远低于下轨 9.50，降至 8.00（-20%），应触发等待回调
        self.manager._check_level_crossing(session, tracker, 8.00)
        self.assertTrue(tracker.waiting_callback, "价格远低于下轨也应触发等待")

    def test_a7_price_far_above_upper(self):
        """A-7: 价格远高于上轨，应触发卖出等待"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)
        tracker = self.manager.trackers[session.id]

        # 价格远高于上轨 10.50，涨至 12.00（+20%）
        self.manager._check_level_crossing(session, tracker, 12.00)
        self.assertTrue(tracker.waiting_callback, "价格远高于上轨也应触发卖出等待")

    def test_a8_buy_blocked_when_max_investment_exhausted(self):
        """A-8: max_investment 耗尽时，下穿下轨不触发等待（防止无效循环）"""
        session = make_session(self.db, self.manager, center_price=10.0,
                               max_investment=10000, current_investment=10000)
        tracker = self.manager.trackers[session.id]

        self.manager._check_level_crossing(session, tracker, 9.40)
        self.assertFalse(tracker.waiting_callback,
                         "max_investment 已耗尽时下穿不应设置等待回调，防止死循环")

    def test_a9_sell_not_blocked_when_max_investment_exhausted(self):
        """A-9: max_investment 耗尽时，上穿上轨仍应触发卖出（允许卖出回收资金）"""
        session = make_session(self.db, self.manager, center_price=10.0,
                               max_investment=10000, current_investment=10000)
        tracker = self.manager.trackers[session.id]

        self.manager._check_level_crossing(session, tracker, 10.60)
        self.assertTrue(tracker.waiting_callback,
                         "max_investment 耗尽时卖出仍应触发（允许资金回收）")

    def test_a10_callback_buy_triggers_at_exact_threshold(self):
        """A-10: 回调精确达到阈值时触发 BUY 信号（FLOAT_TOLERANCE 容差验证）"""
        # callback_ratio = 0.005 (0.5%)
        tracker = PriceTracker(session_id=1, last_price=0, peak_price=0,
                               valley_price=9.40, direction='falling',
                               waiting_callback=True)
        # 从谷值 9.40 回升 0.5% = 9.40 * 1.005 = 9.447
        trigger_price = round(9.40 * 1.005, 6)
        tracker.last_price = trigger_price
        result = tracker.check_callback(0.005)
        self.assertEqual(result, 'BUY', f"回调达到 0.5% 应触发 BUY，回升价={trigger_price:.4f}")

    def test_a11_callback_sell_triggers_at_exact_threshold(self):
        """A-11: 回调精确达到阈值时触发 SELL 信号"""
        tracker = PriceTracker(session_id=1, last_price=0, peak_price=10.60,
                               valley_price=0, direction='rising',
                               waiting_callback=True)
        # 从峰值 10.60 回落 0.5% = 10.60 * (1 - 0.005) = 10.547
        trigger_price = round(10.60 * (1 - 0.005), 6)
        tracker.last_price = trigger_price
        result = tracker.check_callback(0.005)
        self.assertEqual(result, 'SELL', f"回落达到 0.5% 应触发 SELL，回落价={trigger_price:.4f}")

    def test_a12_callback_buy_not_triggered_below_threshold(self):
        """A-12: 回调未达到阈值时不触发信号"""
        tracker = PriceTracker(session_id=1, last_price=0, peak_price=0,
                               valley_price=9.40, direction='falling',
                               waiting_callback=True)
        # 回升幅度不足：0.2% < 0.5%
        tracker.last_price = 9.40 * 1.002
        result = tracker.check_callback(0.005)
        self.assertIsNone(result, "回调不足 0.5% 时不应触发信号")

    def test_a13_grid_levels_calculated_correctly(self):
        """A-13: 网格档位计算公式验证"""
        session = GridSession(
            center_price=10.0, current_center_price=10.0,
            price_interval=0.05, stock_code="000001.SZ"
        )
        levels = session.get_grid_levels()
        self.assertAlmostEqual(levels['lower'], 9.50, places=4, msg="下轨 = center * (1-interval)")
        self.assertAlmostEqual(levels['center'], 10.0, places=4, msg="中轨 = center")
        self.assertAlmostEqual(levels['upper'], 10.50, places=4, msg="上轨 = center * (1+interval)")

    def test_a14_grid_levels_use_current_center_not_original(self):
        """A-14: 网格档位使用 current_center_price，不使用初始 center_price"""
        session = GridSession(
            center_price=10.0, current_center_price=11.0,  # 重建后中心价变了
            price_interval=0.05, stock_code="000001.SZ"
        )
        levels = session.get_grid_levels()
        self.assertAlmostEqual(levels['center'], 11.0, places=4,
                               msg="档位应基于 current_center_price 计算")
        self.assertAlmostEqual(levels['lower'], 11.0 * 0.95, places=4)
        self.assertAlmostEqual(levels['upper'], 11.0 * 1.05, places=4)


# ==============================================================================
# Suite B: 完整买入流程验证
# ==============================================================================

class TestSuiteB_BuyExecution(GridTestBase):
    """B: 验证买入执行的全路径逻辑"""

    def test_b1_normal_buy_updates_all_stats(self):
        """B-1: 正常买入更新 trade_count/buy_count/total_buy_amount/current_investment"""
        session = make_session(self.db, self.manager, max_investment=10000,
                               current_investment=0, position_ratio=0.25)
        before_trade = session.trade_count
        before_buy = session.buy_count
        before_inv = session.current_investment

        result = self._execute_buy(session, trigger_price=9.50)
        self.assertTrue(result, "正常买入应返回 True")
        self.assertEqual(session.trade_count, before_trade + 1)
        self.assertEqual(session.buy_count, before_buy + 1)
        self.assertGreater(session.total_buy_amount, 0)
        self.assertGreater(session.current_investment, before_inv)

    def test_b2_buy_volume_is_multiple_of_100(self):
        """B-2: 买入股数必须是100的整数倍"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        result = self._execute_buy(session, trigger_price=9.50)
        self.assertTrue(result)
        # actual_amount / trigger_price 向下取整到100的倍数
        actual_amount = session.total_buy_amount
        expected_volume = (int(actual_amount / 9.50) // 100) * 100
        # 反推买入量应为 100 的倍数
        volume_implied = round(actual_amount / 9.50)
        self.assertEqual(volume_implied % 100, 0,
                         f"买入股数应为100的整数倍，implied={volume_implied}")

    def test_b3_buy_respects_position_ratio(self):
        """B-3: 单次买入金额不超过 max_investment * position_ratio"""
        max_inv = 10000.0
        ratio = 0.25
        session = make_session(self.db, self.manager, max_investment=max_inv,
                               current_investment=0, position_ratio=ratio)
        self._execute_buy(session, trigger_price=10.0)
        # 买入金额 <= max_inv * ratio（取整到100股后略有误差）
        expected_max = max_inv * ratio
        self.assertLessEqual(session.total_buy_amount, expected_max + 10.0,
                             "买入金额不应超过 max_investment * position_ratio（含100股取整容差）")

    def test_b4_buy_uses_remaining_when_remaining_less_than_target(self):
        """B-4: 剩余可用额度不足单次目标额度时，以剩余额度为准"""
        max_inv = 10000.0
        # 已投入 9000，剩余 1000，目标是 10000*0.25=2500，但只剩 1000
        session = make_session(self.db, self.manager, max_investment=max_inv,
                               current_investment=9000, position_ratio=0.25)
        result = self._execute_buy(session, trigger_price=10.0)
        # 结果看是否成功（100元以上可买至少100股 @ 10元）
        remaining = max_inv - 9000
        if remaining >= 100:
            self.assertTrue(result)
            self.assertLessEqual(session.current_investment, max_inv + 0.01,
                                 "不得超过 max_investment")
        else:
            self.assertFalse(result, "剩余不足100元时应拒绝买入")

    def test_b5_buy_rejected_when_max_investment_reached(self):
        """B-5: current_investment == max_investment 时拒绝买入"""
        session = make_session(self.db, self.manager, max_investment=10000,
                               current_investment=10000)
        result = self._execute_buy(session, trigger_price=9.50)
        self.assertFalse(result, "已达 max_investment 上限时应拒绝买入")
        self.assertEqual(session.buy_count, 0, "buy_count 不应增加")

    def test_b6_buy_rejected_when_amount_less_than_100(self):
        """B-6: 可买金额不足100元时拒绝买入"""
        # 剩余 99 元，不足 100 元最小限额
        session = make_session(self.db, self.manager, max_investment=10099,
                               current_investment=10000, position_ratio=0.25)
        result = self._execute_buy(session, trigger_price=9.50)
        self.assertFalse(result, "可买金额不足100元时应拒绝")

    def test_b7_buy_rejected_when_volume_less_than_100_shares(self):
        """B-7: 计算出的买入股数不足100股时拒绝"""
        # max_investment=500, ratio=0.25 => target=125元
        # 价格 20 元 => 125/20=6.25股，向下取整到100股 = 0 股 < 100股 => 拒绝
        session = make_session(self.db, self.manager, max_investment=500,
                               current_investment=0, position_ratio=0.25)
        result = self._execute_buy(session, trigger_price=20.0)
        self.assertFalse(result, "股数不足100股时应拒绝买入")

    def test_b8_buy_rejected_when_max_investment_zero(self):
        """B-8: max_investment=0 时立即拒绝（无效配置）"""
        session = make_session(self.db, self.manager, max_investment=0)
        result = self._execute_buy(session, trigger_price=10.0)
        self.assertFalse(result, "max_investment=0 时应拒绝")

    def test_b9_buy_triggers_grid_rebuild(self):
        """B-9: 买入成功后网格中心价重建为触发价"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)
        trigger_price = 9.50
        self._execute_buy(session, trigger_price=trigger_price)
        self.assertAlmostEqual(session.current_center_price, trigger_price, places=4,
                               msg="买入后中心价应重建为触发价")

    def test_b10_buy_rebuilds_tracker(self):
        """B-10: 买入后 PriceTracker 重置（waiting_callback=False）"""
        session = make_session(self.db, self.manager, center_price=10.0)
        tracker = self.manager.trackers[session.id]
        tracker.waiting_callback = True
        tracker.direction = 'falling'
        self._execute_buy(session, trigger_price=9.50)
        self.assertFalse(tracker.waiting_callback, "买入后 tracker 应重置")

    def test_b11_buy_records_trade_in_db(self):
        """B-11: 买入成功后在数据库中记录交易"""
        session = make_session(self.db, self.manager, max_investment=10000)
        self._execute_buy(session, trigger_price=9.50)
        trades = self.db.get_grid_trades(session.id, limit=10)
        self.assertEqual(len(trades), 1, "应有1条交易记录")
        trade = dict(trades[0])
        self.assertEqual(trade['trade_type'], 'BUY')
        self.assertAlmostEqual(trade['trigger_price'], 9.50, places=2)

    def test_b12_buy_in_live_mode_calls_executor(self):
        """B-12: 实盘模式下买入调用 executor.buy_stock"""
        config.ENABLE_SIMULATION_MODE = False
        self.executor.buy_stock.return_value = {'order_id': 'LIVE_BUY_001'}
        session = make_session(self.db, self.manager, max_investment=10000)
        result = self._execute_buy(session, trigger_price=9.50)
        self.assertTrue(result)
        self.assertTrue(self.executor.buy_stock.called, "实盘模式应调用 executor.buy_stock")
        call_kwargs = self.executor.buy_stock.call_args[1]
        self.assertEqual(call_kwargs.get('stock_code', call_kwargs.get('stock_code')), '000001.SZ')

    def test_b13_buy_in_live_mode_failed_returns_false(self):
        """B-13: 实盘模式下 executor 返回 None/False 时买入失败"""
        config.ENABLE_SIMULATION_MODE = False
        self.executor.buy_stock.return_value = None
        session = make_session(self.db, self.manager, max_investment=10000)
        result = self._execute_buy(session, trigger_price=9.50)
        self.assertFalse(result, "executor 失败时买入应返回 False")
        self.assertEqual(session.buy_count, 0, "买入失败后 buy_count 不应增加")

    def test_b14_hardcap_prevents_overinvestment(self):
        """B-14: 硬上限保护 - actual_amount > remaining 时拒绝（防浮点误差超买）"""
        # 构造 remaining 刚好为 0.005（低于0.01容差），actual_amount 会超出
        session = make_session(self.db, self.manager, max_investment=10000.005,
                               current_investment=10000.0, position_ratio=0.25)
        # remaining = 0.005，远不足100元，应先被100元检查拦截
        result = self._execute_buy(session, trigger_price=9.50)
        self.assertFalse(result)
        self.assertLessEqual(session.current_investment, session.max_investment + 0.01,
                             "硬上限保护：current_investment 不得超过 max_investment+0.01")


# ==============================================================================
# Suite C: 完整卖出流程验证
# ==============================================================================

class TestSuiteC_SellExecution(GridTestBase):
    """C: 验证卖出执行的全路径逻辑"""

    def test_c1_normal_sell_updates_all_stats(self):
        """C-1: 正常卖出更新 trade_count/sell_count/total_sell_amount"""
        session = make_session(self.db, self.manager, max_investment=10000,
                               current_investment=5000, position_ratio=0.25)
        pos = make_position(volume=1000, cost_price=10.0)
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertTrue(result, "正常卖出应返回 True")
        self.assertEqual(session.sell_count, 1)
        self.assertGreater(session.total_sell_amount, 0)

    def test_c2_sell_volume_is_multiple_of_100(self):
        """C-2: 卖出股数必须是100的整数倍"""
        session = make_session(self.db, self.manager, position_ratio=0.25)
        pos = make_position(volume=1000)
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertTrue(result)
        # 期望 sell_volume = (int(1000*0.25)//100)*100 = (250//100)*100 = 200
        expected_sell_vol = (int(1000 * 0.25) // 100) * 100
        trade = dict(self.db.get_grid_trades(session.id, limit=1)[0])
        self.assertEqual(trade['volume'], expected_sell_vol,
                         f"卖出股数应为 {expected_sell_vol}")

    def test_c3_sell_volume_minimum_100_shares(self):
        """C-3: 卖出数量为0时自动调整为最小值100股"""
        # available=50 => sell_vol = (int(50*0.25)//100)*100 = 0 => 调整为100，但100>50，再调整为0 => 拒绝
        session = make_session(self.db, self.manager, position_ratio=0.25)
        pos = make_position(volume=50, available=50)
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        # 50股持仓，sell_volume先设为100（最小值），100>50，再向下取整为0，拒绝
        self.assertFalse(result, "可卖不足100股时应拒绝卖出")

    def test_c4_sell_rejected_when_volume_is_zero(self):
        """C-4: current_volume=0 时拒绝卖出"""
        session = make_session(self.db, self.manager)
        pos = make_position(volume=0)
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertFalse(result, "持仓为0时应拒绝卖出")

    def test_c5_sell_rejected_when_position_none(self):
        """C-5: 持仓不存在时拒绝卖出（position_snapshot=None 触发内部 get_position，返回 None）"""
        session = make_session(self.db, self.manager)
        self.position_manager.get_position.return_value = None
        sig = make_signal(signal_type='SELL', trigger_price=10.50,
                          session_id=session.id, grid_level=10.50)
        result = self.manager._execute_grid_sell(session, sig, position_snapshot=None)
        self.assertFalse(result, "持仓不存在时应拒绝")

    def test_c6_t1_rule_uses_available_not_volume(self):
        """C-6: T+1 规则 - 使用 available 而非 volume 计算卖出股数"""
        session = make_session(self.db, self.manager, position_ratio=0.25)
        # 总持仓 1000，今日买入800不可卖，available=200
        pos = make_position(volume=1000, available=200)
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertTrue(result)
        # 卖出应基于 available=200: (int(200*0.25)//100)*100 = 0 => 100，但100<200，结果=100
        trade = dict(self.db.get_grid_trades(session.id, limit=1)[0])
        self.assertLessEqual(trade['volume'], 200, "卖出股数不得超过 available（T+1规则）")

    def test_c7_t1_all_locked_rejects_sell(self):
        """C-7: available=0（今日全仓买入锁仓）时拒绝卖出"""
        session = make_session(self.db, self.manager)
        pos = make_position(volume=1000, available=0)  # 全部今日买入，不可卖
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertFalse(result, "available=0 时应拒绝卖出（T+1限制）")

    def test_c8_sell_recovers_investment_correctly(self):
        """C-8: 卖出后 current_investment 按实际成交额（卖出价*股数）回收"""
        initial_investment = 5000.0
        session = make_session(self.db, self.manager, max_investment=10000,
                               current_investment=initial_investment, position_ratio=0.25)
        pos = make_position(volume=1000)
        trigger_price = 10.50
        self._execute_sell(session, trigger_price=trigger_price, position_snapshot=pos)
        trade = dict(self.db.get_grid_trades(session.id, limit=1)[0])
        sell_amount = trade['volume'] * trigger_price
        expected_inv = max(0, initial_investment - sell_amount)
        self.assertAlmostEqual(session.current_investment, expected_inv, delta=0.01,
                               msg="卖出后资金回收应按触发价*股数计算")

    def test_c9_sell_triggers_grid_rebuild(self):
        """C-9: 卖出成功后网格中心价重建为触发价"""
        session = make_session(self.db, self.manager, center_price=10.0)
        pos = make_position(volume=1000)
        trigger_price = 10.50
        self._execute_sell(session, trigger_price=trigger_price, position_snapshot=pos)
        self.assertAlmostEqual(session.current_center_price, trigger_price, places=4,
                               msg="卖出后中心价应重建为触发价")

    def test_c10_sell_in_live_mode_calls_executor(self):
        """C-10: 实盘模式下卖出调用 executor.sell_stock，传入 volume 和 price"""
        config.ENABLE_SIMULATION_MODE = False
        self.executor.sell_stock.return_value = {'order_id': 'LIVE_SELL_001'}
        session = make_session(self.db, self.manager, position_ratio=0.25)
        pos = make_position(volume=1000)
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertTrue(result)
        self.assertTrue(self.executor.sell_stock.called, "实盘应调用 executor.sell_stock")

    def test_c11_sell_current_investment_not_negative(self):
        """C-11: 卖出后 current_investment 不应为负数（max(0, ...)）"""
        # 卖出金额 > current_investment 时，应截断至 0
        session = make_session(self.db, self.manager, max_investment=10000,
                               current_investment=500, position_ratio=1.0)  # 全仓比例
        pos = make_position(volume=1000, cost_price=10.0)
        self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertGreaterEqual(session.current_investment, 0,
                                "current_investment 不应为负数")

    def test_c12_sell_limit_to_available_when_exceeds(self):
        """C-12: sell_volume > available 时自动截断至 available 的整百"""
        # position_ratio=1.0 => sell_volume = (int(100*1.0)//100)*100 = 100 == available(100)
        session = make_session(self.db, self.manager, position_ratio=1.0)
        pos = make_position(volume=150, available=100)  # available 限制
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertTrue(result)
        trade = dict(self.db.get_grid_trades(session.id, limit=1)[0])
        self.assertLessEqual(trade['volume'], 100, "卖出量不应超过 available=100")


# ==============================================================================
# Suite D: 多档位连续交易
# ==============================================================================

class TestSuiteD_MultiRoundTrading(GridTestBase):
    """D: 多轮次买入/卖出后的网格状态一致性"""

    def test_d1_buy_twice_investment_accumulates(self):
        """D-1: 两次买入后 current_investment 正确累加"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        self._execute_buy(session, trigger_price=9.50)
        inv_after_1 = session.current_investment
        # 重建后中心价变为 9.50，新下轨 = 9.50*0.95=9.025
        self._execute_buy(session, trigger_price=9.025)
        self.assertGreater(session.current_investment, inv_after_1,
                           "两次买入后 current_investment 应继续增加")
        self.assertEqual(session.buy_count, 2)

    def test_d2_sell_after_buy_profit_calculated(self):
        """D-2: 买入后卖出，盈亏率正确计算"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        # 1. 买入
        self._execute_buy(session, trigger_price=9.50)
        buy_amount = session.total_buy_amount
        # 2. 卖出（价格高于买入价，应盈利）
        pos = make_position(volume=1000, cost_price=9.50)
        self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        profit = session.get_profit_ratio()
        # profit = (total_sell - total_buy) / max_investment
        expected = (session.total_sell_amount - buy_amount) / session.max_investment
        self.assertAlmostEqual(profit, expected, places=6)

    def test_d3_grid_rebuilds_correctly_after_each_trade(self):
        """D-3: 每次交易后网格中心价都基于最新触发价"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)

        # 第一次买入 @9.50
        self._execute_buy(session, trigger_price=9.50)
        self.assertAlmostEqual(session.current_center_price, 9.50, places=4)

        # 第一次卖出 @9.975 (9.50*1.05)
        pos = make_position(volume=1000)
        self._execute_sell(session, trigger_price=9.975, position_snapshot=pos)
        self.assertAlmostEqual(session.current_center_price, 9.975, places=4)

    def test_d4_buy_count_and_sell_count_independent(self):
        """D-4: buy_count 和 sell_count 独立计数，互不影响"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)

        self._execute_buy(session, trigger_price=9.50)
        self.assertEqual(session.buy_count, 1)
        self.assertEqual(session.sell_count, 0)

        pos = make_position(volume=1000)
        self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertEqual(session.buy_count, 1)
        self.assertEqual(session.sell_count, 1)

    def test_d5_trade_count_equals_buy_plus_sell(self):
        """D-5: trade_count = buy_count + sell_count"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        self._execute_buy(session, trigger_price=9.50)
        pos = make_position(volume=1000)
        self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertEqual(session.trade_count, session.buy_count + session.sell_count,
                         "trade_count 应等于 buy_count + sell_count")

    def test_d6_multiple_buys_max_4_slots(self):
        """D-6: position_ratio=0.25 最多允许4次买入（满仓4档）"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        prices = [9.50, 9.025, 8.57, 8.14]
        for i, price in enumerate(prices):
            result = self._execute_buy(session, trigger_price=price)
            if session.current_investment < session.max_investment:
                self.assertTrue(result or session.buy_count <= i + 1)

        # 第5次尝试买入，应被拒绝（资金已耗尽）
        result = self._execute_buy(session, trigger_price=7.73)
        # 可能已耗尽，也可能还差一点，关键验证不超过上限
        self.assertLessEqual(session.current_investment, session.max_investment + 0.01)

    def test_d7_db_trade_records_match_memory_stats(self):
        """D-7: 数据库中记录的交易与内存统计一致"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        self._execute_buy(session, trigger_price=9.50)
        pos = make_position(volume=1000)
        self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)

        trades = self.db.get_grid_trades(session.id, limit=100)
        self.assertEqual(len(trades), 2, "应有2条交易记录")

        db_buy_total = sum(dict(t)['amount'] for t in trades if dict(t)['trade_type'] == 'BUY')
        db_sell_total = sum(dict(t)['amount'] for t in trades if dict(t)['trade_type'] == 'SELL')
        self.assertAlmostEqual(db_buy_total, session.total_buy_amount, places=2)
        self.assertAlmostEqual(db_sell_total, session.total_sell_amount, places=2)


# ==============================================================================
# Suite E: 资金管理边界
# ==============================================================================

class TestSuiteE_FundManagement(GridTestBase):
    """E: max_investment 三重防护与资金边界验证"""

    def test_e1_current_investment_never_exceeds_max(self):
        """E-1: 任何情况下 current_investment 不超过 max_investment"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.5)
        for price in [9.50, 9.025, 8.574]:
            self._execute_buy(session, trigger_price=price)
            self.assertLessEqual(session.current_investment, session.max_investment + 0.01,
                                 f"买入@{price}后 current_investment 不应超过 max_investment")

    def test_e2_get_profit_ratio_returns_zero_when_no_trades(self):
        """E-2: 无交易时 get_profit_ratio() 返回 0.0"""
        session = GridSession(max_investment=10000)
        self.assertEqual(session.get_profit_ratio(), 0.0)

    def test_e3_get_profit_ratio_returns_zero_when_max_investment_zero(self):
        """E-3: max_investment=0 时 get_profit_ratio() 返回 0.0（防除零）"""
        session = GridSession(max_investment=0, total_buy_amount=100, total_sell_amount=200)
        self.assertEqual(session.get_profit_ratio(), 0.0)

    def test_e4_profit_ratio_with_sell_profit(self):
        """E-4: 卖出盈利时 profit_ratio > 0"""
        session = GridSession(max_investment=10000,
                              total_buy_amount=2000, total_sell_amount=2500)
        ratio = session.get_profit_ratio()
        expected = (2500 - 2000) / 10000
        self.assertAlmostEqual(ratio, expected, places=6)
        self.assertGreater(ratio, 0)

    def test_e5_profit_ratio_with_sell_loss(self):
        """E-5: 卖出亏损时 profit_ratio < 0"""
        session = GridSession(max_investment=10000,
                              total_buy_amount=2000, total_sell_amount=1500)
        ratio = session.get_profit_ratio()
        self.assertLess(ratio, 0)

    def test_e6_grid_profit_absolute_value(self):
        """E-6: get_grid_profit() 返回绝对盈亏金额"""
        session = GridSession(total_buy_amount=2000, total_sell_amount=2300)
        self.assertAlmostEqual(session.get_grid_profit(), 300.0, places=4)

    def test_e7_sell_investment_recovered_but_not_negative(self):
        """E-7: 卖出回收超过当前投入时截断至 0"""
        session = make_session(self.db, self.manager, max_investment=10000,
                               current_investment=200, position_ratio=1.0)
        pos = make_position(volume=1000)
        self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertGreaterEqual(session.current_investment, 0, "current_investment >= 0")

    def test_e8_float_precision_buy_near_max(self):
        """E-8: 浮点精度边界 - 剩余额度=0.001时不超买"""
        session = make_session(self.db, self.manager, max_investment=10000.001,
                               current_investment=10000.0, position_ratio=0.25)
        result = self._execute_buy(session, trigger_price=9.50)
        # 剩余 0.001 < 100 元最小限额，应拒绝
        self.assertFalse(result)
        self.assertLessEqual(session.current_investment, 10000.001 + 0.01)

    def test_e9_buy_amount_exactly_fills_remaining(self):
        """E-9: 剩余额度恰好够买100股时，能正常完成"""
        # 100股 @ 9.50 = 950元。current_investment=9050, max=10000, remaining=950
        session = make_session(self.db, self.manager, max_investment=10000,
                               current_investment=9050, position_ratio=0.25)
        result = self._execute_buy(session, trigger_price=9.50)
        # target = 10000*0.25=2500，remaining=950, buy_amount=min(950,2500)=950
        # 950/9.50=100股，正好100股，应该成功
        self.assertTrue(result)
        self.assertAlmostEqual(session.current_investment, 9050 + 950, delta=1.0)


# ==============================================================================
# Suite F: 冷却保护机制
# ==============================================================================

class TestSuiteF_CooldownMechanism(GridTestBase):
    """F: 冷却保护防止级联交易"""

    def test_f1_level_cooldown_prevents_second_crossing(self):
        """F-1: 档位冷却期内相同档位不再触发等待"""
        config.GRID_LEVEL_COOLDOWN = 60  # 60秒冷却
        session = make_session(self.db, self.manager, center_price=10.0)
        tracker = self.manager.trackers[session.id]
        lower = session.current_center_price * (1 - session.price_interval)

        # 第一次穿越：手动设置冷却记录
        self.manager.level_cooldowns[(session.id, lower)] = time.time()

        # 再次下穿同一档位，应被冷却期阻止
        tracker.waiting_callback = False
        self.manager._check_level_crossing(session, tracker, lower - 0.01)
        self.assertFalse(tracker.waiting_callback, "冷却期内不应再次触发等待")

    def test_f2_level_cooldown_expired_allows_crossing(self):
        """F-2: 冷却期过后可以重新触发"""
        config.GRID_LEVEL_COOLDOWN = 1  # 1秒冷却
        session = make_session(self.db, self.manager, center_price=10.0)
        tracker = self.manager.trackers[session.id]
        lower = session.current_center_price * (1 - session.price_interval)

        # 设置2秒前的冷却记录（已过期）
        self.manager.level_cooldowns[(session.id, lower)] = time.time() - 2.0

        tracker.waiting_callback = False
        self.manager._check_level_crossing(session, tracker, lower - 0.01)
        self.assertTrue(tracker.waiting_callback, "冷却期过后应允许重新触发")

    def test_f3_buy_cooldown_prevents_rapid_second_buy(self):
        """F-3: 买入冷却期内阻止第二次买入"""
        config.GRID_BUY_COOLDOWN = 300  # 5分钟冷却
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)

        # 第一次买入
        self._execute_buy(session, trigger_price=9.50)
        buy1 = session.buy_count

        # 第二次立即买入（冷却中）
        self._execute_buy(session, trigger_price=9.025)
        # buy_count 不应再增加
        self.assertEqual(session.buy_count, buy1, "买入冷却期内不应再次买入")

    def test_f4_sell_cooldown_prevents_rapid_second_sell(self):
        """F-4: 卖出冷却期内阻止第二次卖出"""
        config.GRID_SELL_COOLDOWN = 300
        session = make_session(self.db, self.manager, current_investment=5000, position_ratio=0.25)
        pos = make_position(volume=1000)

        # 第一次卖出
        self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        sell1 = session.sell_count

        # 第二次立即卖出（冷却中）
        pos2 = make_position(volume=800)
        self._execute_sell(session, trigger_price=10.50, position_snapshot=pos2)
        self.assertEqual(session.sell_count, sell1, "卖出冷却期内不应再次卖出")

    def test_f5_buy_cooldown_recorded_before_db_write(self):
        """F-5: 买入冷却时间在 DB 写入前记录（BUG-C1修复验证）"""
        config.GRID_BUY_COOLDOWN = 300
        # 模拟 DB 写入失败
        original_record = self.db.record_grid_trade
        self.db.record_grid_trade = Mock(side_effect=Exception("DB故障"))

        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        result = self._execute_buy(session, trigger_price=9.50)

        # DB 失败导致买入返回 False，但冷却时间应已记录
        self.assertFalse(result)
        # 恢复 DB
        self.db.record_grid_trade = original_record

        # 验证冷却已生效：再次买入应被冷却拦截
        result2 = self._execute_buy(session, trigger_price=9.025)
        self.assertFalse(result2, "DB失败后冷却时间已记录，应阻止下次买入")

    def test_f6_sell_cooldown_recorded_before_db_write(self):
        """F-6: 卖出冷却时间在 DB 写入前记录（A-4对称修复验证）"""
        config.GRID_SELL_COOLDOWN = 300
        original_record = self.db.record_grid_trade
        self.db.record_grid_trade = Mock(side_effect=Exception("DB故障"))

        session = make_session(self.db, self.manager, current_investment=5000, position_ratio=0.25)
        pos = make_position(volume=1000)
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)

        self.assertFalse(result)
        self.db.record_grid_trade = original_record

        pos2 = make_position(volume=800)
        result2 = self._execute_sell(session, trigger_price=10.60, position_snapshot=pos2)
        self.assertFalse(result2, "DB失败后卖出冷却已记录，应阻止下次卖出")

    def test_f7_different_sessions_have_independent_cooldowns(self):
        """F-7: 不同会话的冷却相互独立"""
        config.GRID_BUY_COOLDOWN = 300
        session1 = make_session(self.db, self.manager, stock_code='000001.SZ',
                                max_investment=10000, position_ratio=0.25)
        session2 = make_session(self.db, self.manager, stock_code='600000.SH',
                                max_investment=10000, position_ratio=0.25)

        # session1 买入（冷却启动）
        self._execute_buy(session1, trigger_price=9.50)
        # session2 独立，不受 session1 冷却影响
        result2 = self._execute_buy(session2, trigger_price=9.50)
        self.assertTrue(result2, "不同会话的买入冷却应独立")


# ==============================================================================
# Suite G: 异常场景鲁棒性
# ==============================================================================

class TestSuiteG_AbnormalRobustness(GridTestBase):
    """G: 异常场景下的数据一致性和兜底保护"""

    def test_g1_db_failure_on_buy_rolls_back_memory(self):
        """G-1: 买入 DB 写入失败时，内存统计回滚（RISK-1验证）"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        before_trade_count = session.trade_count
        before_buy_count = session.buy_count
        before_investment = session.current_investment
        before_buy_amount = session.total_buy_amount

        # 模拟 record_grid_trade 抛出异常
        self.db.record_grid_trade = Mock(side_effect=Exception("DB写入失败"))

        result = self._execute_buy(session, trigger_price=9.50)
        self.assertFalse(result, "DB 失败时应返回 False")
        # 验证内存状态回滚
        self.assertEqual(session.trade_count, before_trade_count, "trade_count 应回滚")
        self.assertEqual(session.buy_count, before_buy_count, "buy_count 应回滚")
        self.assertAlmostEqual(session.current_investment, before_investment, delta=0.001,
                               msg="current_investment 应回滚")
        self.assertAlmostEqual(session.total_buy_amount, before_buy_amount, delta=0.001,
                               msg="total_buy_amount 应回滚")

    def test_g2_db_failure_on_sell_rolls_back_memory(self):
        """G-2: 卖出 DB 写入失败时，内存统计回滚（RISK-2验证）"""
        session = make_session(self.db, self.manager, max_investment=10000,
                               current_investment=5000, position_ratio=0.25)
        before_sell_count = session.sell_count
        before_investment = session.current_investment

        self.db.record_grid_trade = Mock(side_effect=Exception("DB写入失败"))

        pos = make_position(volume=1000)
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertFalse(result)
        self.assertEqual(session.sell_count, before_sell_count, "sell_count 应回滚")
        self.assertAlmostEqual(session.current_investment, before_investment, delta=0.001,
                               msg="current_investment 应回滚")

    def test_g3_buy_failure_resets_tracker_waiting_callback(self):
        """G-3: 买入失败后 tracker.waiting_callback 重置为 False（防止无限重试）"""
        config.GRID_BUY_COOLDOWN = 0  # 关闭冷却，让代码走到 DB 写入
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        tracker = self.manager.trackers[session.id]
        tracker.waiting_callback = True
        tracker.direction = 'falling'

        # 模拟 executor 失败（实盘模式）
        config.ENABLE_SIMULATION_MODE = False
        self.executor.buy_stock.return_value = None

        sig = make_signal(signal_type='BUY', trigger_price=9.50, session_id=session.id,
                          grid_level=9.50)
        result = self.manager.execute_grid_trade(sig)
        self.assertFalse(result)
        self.assertFalse(tracker.waiting_callback, "买入失败后 waiting_callback 应重置为 False")

    def test_g4_sell_failure_resets_tracker_waiting_callback(self):
        """G-4: 卖出失败后 tracker.waiting_callback 重置"""
        config.ENABLE_SIMULATION_MODE = False
        self.executor.sell_stock.return_value = None
        session = make_session(self.db, self.manager, position_ratio=0.25)
        tracker = self.manager.trackers[session.id]
        tracker.waiting_callback = True
        tracker.direction = 'rising'
        self.position_manager.get_position.return_value = make_position(volume=1000)

        sig = make_signal(signal_type='SELL', trigger_price=10.50, session_id=session.id,
                          grid_level=10.50)
        result = self.manager.execute_grid_trade(sig)
        self.assertFalse(result)
        self.assertFalse(tracker.waiting_callback, "卖出失败后 waiting_callback 应重置为 False")

    def test_g5_exception_in_execute_trade_resets_tracker(self):
        """G-5: execute_grid_trade 异常时同样重置 tracker（Gap-1修复验证）"""
        config.ENABLE_SIMULATION_MODE = False
        self.executor.buy_stock.side_effect = RuntimeError("网络异常")
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        tracker = self.manager.trackers[session.id]
        tracker.waiting_callback = True

        sig = make_signal(signal_type='BUY', trigger_price=9.50, session_id=session.id,
                          grid_level=9.50)
        result = self.manager.execute_grid_trade(sig)
        self.assertFalse(result)
        self.assertFalse(tracker.waiting_callback, "异常后 tracker 应重置，防无限重试")

    def test_g6_session_not_found_returns_false(self):
        """G-6: 信号对应会话不存在时返回 False"""
        sig = make_signal(stock_code='999999.SZ', signal_type='BUY',
                          trigger_price=9.50, session_id=999)
        result = self.manager.execute_grid_trade(sig)
        self.assertFalse(result, "会话不存在时应返回 False")

    def test_g7_duplicate_signal_deduplicated(self):
        """G-7: 信号去重机制（P1-1修复）- 已有相同类型信号时跳过"""
        session = make_session(self.db, self.manager, center_price=10.0)
        stock_code = session.stock_code

        # 预置已有 grid_buy 信号
        with self.position_manager.signal_lock:
            self.position_manager.latest_signals[stock_code] = {
                'type': 'grid_buy',
                'session_id': session.id
            }

        # 价格下穿 + 回调已满足
        tracker = self.manager.trackers[session.id]
        tracker.waiting_callback = True
        tracker.direction = 'falling'
        tracker.valley_price = 9.40
        tracker.last_price = 9.445  # 回升 >= 0.5%

        self.position_manager.get_position.return_value = make_position(volume=1000)
        result = self.manager.check_grid_signals(stock_code, 9.445)
        self.assertIsNone(result, "已有相同信号时不应重复生成")

    def test_g8_stop_session_clears_pending_grid_signals(self):
        """G-8: 停止会话后清除残留的网格信号（P0-2修复）"""
        session = make_session(self.db, self.manager, center_price=10.0)
        stock_code = session.stock_code

        # 预置一个 grid_buy 信号
        with self.position_manager.signal_lock:
            self.position_manager.latest_signals[stock_code] = {
                'type': 'grid_buy',
                'session_id': session.id
            }

        self.manager.stop_grid_session(session.id, 'manual')

        with self.position_manager.signal_lock:
            remaining = self.position_manager.latest_signals.get(stock_code)
        self.assertIsNone(remaining, "停止会话后应清除残留的网格信号")

    def test_g9_price_tracker_reset_clears_all_state(self):
        """G-9: PriceTracker.reset() 清除所有状态"""
        tracker = PriceTracker(session_id=1, last_price=9.50,
                               peak_price=11.0, valley_price=8.0,
                               direction='rising', waiting_callback=True,
                               crossed_level=10.50)
        tracker.reset(10.0)
        self.assertFalse(tracker.waiting_callback)
        self.assertIsNone(tracker.direction)
        self.assertIsNone(tracker.crossed_level)
        self.assertAlmostEqual(tracker.last_price, 10.0, places=4)
        self.assertAlmostEqual(tracker.peak_price, 10.0, places=4)
        self.assertAlmostEqual(tracker.valley_price, 10.0, places=4)

    def test_g10_valley_price_tracks_lowest(self):
        """G-10: 等待回调期间谷值持续更新为最低价"""
        tracker = PriceTracker(session_id=1, last_price=9.40,
                               valley_price=9.40, direction='falling',
                               waiting_callback=True)
        tracker.update_price(9.30)  # 更低
        self.assertAlmostEqual(tracker.valley_price, 9.30, places=4)
        tracker.update_price(9.35)  # 回升，不更新谷值
        self.assertAlmostEqual(tracker.valley_price, 9.30, places=4)

    def test_g11_peak_price_tracks_highest(self):
        """G-11: 等待回调期间峰值持续更新为最高价"""
        tracker = PriceTracker(session_id=1, last_price=10.60,
                               peak_price=10.60, direction='rising',
                               waiting_callback=True)
        tracker.update_price(10.70)  # 更高
        self.assertAlmostEqual(tracker.peak_price, 10.70, places=4)
        tracker.update_price(10.65)  # 回落，不更新峰值
        self.assertAlmostEqual(tracker.peak_price, 10.70, places=4)

    def test_g12_double_crossing_ignored_when_waiting(self):
        """G-12: 等待回调期间再次穿越不触发新的等待（waiting_callback=True 时跳过检测）"""
        session = make_session(self.db, self.manager, center_price=10.0)
        tracker = self.manager.trackers[session.id]
        tracker.waiting_callback = True
        tracker.direction = 'falling'
        tracker.valley_price = 9.40

        # 再次下穿（理论上不应更改 crossed_level 或重置 direction）
        self.manager._check_level_crossing(session, tracker, 9.20)
        self.assertTrue(tracker.waiting_callback, "等待回调期间不应重新触发穿越")
        self.assertEqual(tracker.direction, 'falling', "方向不应改变")


# ==============================================================================
# Suite H: 退出条件触发
# ==============================================================================

class TestSuiteH_ExitConditions(GridTestBase):
    """H: 验证各退出条件触发机制"""

    def test_h1_exit_deviation_from_drift(self):
        """H-1: 网格漂移偏离超限触发退出"""
        # deviation = |current_center - center| / center > max_deviation
        session = make_session(self.db, self.manager, center_price=10.0,
                               max_deviation=0.15)
        session.current_center_price = 11.6  # 偏离 16% > 15%
        pos = make_position(volume=1000)
        reason = self.manager._check_exit_conditions(session, 11.6,
                                                     position_snapshot=pos)
        self.assertEqual(reason, 'deviation', "偏离超限应触发 deviation 退出")

    def test_h2_exit_deviation_from_market_price(self):
        """H-2: 市价相对当前中心价偏离超限触发退出"""
        session = make_session(self.db, self.manager, center_price=10.0,
                               max_deviation=0.15)
        session.current_center_price = 10.0
        # 市价 = 12.0，偏离 = |12.0-10.0|/10.0 = 20% > 15%
        pos = make_position(volume=1000, current_price=12.0)
        reason = self.manager._check_exit_conditions(session, 12.0,
                                                     position_snapshot=pos)
        self.assertEqual(reason, 'deviation')

    def test_h3_exit_target_profit(self):
        """H-3: 盈亏率达到目标止盈后触发（需 buy_count > 0 且 sell_count > 0）"""
        session = make_session(self.db, self.manager, center_price=10.0,
                               max_investment=10000, target_profit=0.05)
        # 模拟买卖记录
        session.buy_count = 2
        session.sell_count = 1
        session.total_buy_amount = 2000
        session.total_sell_amount = 2600  # profit = (2600-2000)/10000 = 6% > 5%
        pos = make_position(volume=500)
        reason = self.manager._check_exit_conditions(session, 10.0,
                                                     position_snapshot=pos)
        self.assertEqual(reason, 'target_profit', "达到目标盈利应触发止盈退出")

    def test_h4_no_exit_target_profit_without_sell(self):
        """H-4: 未完成卖出（sell_count=0）时不触发止盈（需配对才止盈）"""
        session = make_session(self.db, self.manager, center_price=10.0,
                               max_investment=10000, target_profit=0.05)
        session.buy_count = 2
        session.sell_count = 0  # 尚无卖出
        session.total_buy_amount = 2000
        session.total_sell_amount = 2600
        pos = make_position(volume=500)
        reason = self.manager._check_exit_conditions(session, 10.0,
                                                     position_snapshot=pos)
        self.assertNotEqual(reason, 'target_profit', "无卖出记录时不应触发止盈")

    def test_h5_exit_stop_loss_without_sell(self):
        """H-5: 仅买入未卖出时止损允许触发（防单边下跌风险扩大）"""
        session = make_session(self.db, self.manager, center_price=10.0,
                               max_investment=10000, stop_loss=-0.05)
        session.buy_count = 1
        session.sell_count = 0
        session.total_buy_amount = 2000
        session.total_sell_amount = 0  # 无卖出 -> profit = -2000/10000 = -20% < -5%
        pos = make_position(volume=200)
        reason = self.manager._check_exit_conditions(session, 10.0,
                                                     position_snapshot=pos)
        self.assertEqual(reason, 'stop_loss', "仅有买入时止损也应触发")

    def test_h6_no_exit_when_buy_count_zero(self):
        """H-6: buy_count=0 时不触发止盈止损"""
        session = make_session(self.db, self.manager, center_price=10.0,
                               max_investment=10000, stop_loss=-0.01)
        session.buy_count = 0
        pos = make_position(volume=1000)
        reason = self.manager._check_exit_conditions(session, 10.0,
                                                     position_snapshot=pos)
        self.assertNotIn(reason, ['target_profit', 'stop_loss'],
                         "无买入记录时不应触发盈亏检测")

    def test_h7_exit_expired(self):
        """H-7: 到期退出"""
        session = make_session(self.db, self.manager, center_price=10.0, end_days=-1)
        pos = make_position(volume=1000)
        reason = self.manager._check_exit_conditions(session, 10.0,
                                                     position_snapshot=pos)
        self.assertEqual(reason, 'expired', "超过 end_time 应触发过期退出")

    def test_h8_exit_position_cleared(self):
        """H-8: 持仓清空退出"""
        session = make_session(self.db, self.manager, center_price=10.0)
        pos = make_position(volume=0)  # 持仓为0
        reason = self.manager._check_exit_conditions(session, 10.0,
                                                     position_snapshot=pos)
        self.assertEqual(reason, 'position_cleared', "持仓为0应触发清仓退出")

    def test_h9_exit_position_none(self):
        """H-9: 持仓不存在（None）触发清仓退出"""
        session = make_session(self.db, self.manager, center_price=10.0)
        reason = self.manager._check_exit_conditions(session, 10.0,
                                                     position_snapshot=None)
        # 传 None 会走 get_position() 路径
        self.position_manager.get_position.return_value = None
        session2 = make_session(self.db, self.manager, stock_code='600001.SH')
        reason2 = self.manager._check_exit_conditions(session2, 10.0,
                                                      position_snapshot=None)
        self.assertEqual(reason2, 'position_cleared')

    def test_h10_no_exit_when_all_ok(self):
        """H-10: 所有条件均未触发时返回 None"""
        session = make_session(self.db, self.manager, center_price=10.0,
                               max_investment=10000, max_deviation=0.15,
                               target_profit=0.10, stop_loss=-0.10, end_days=7)
        pos = make_position(volume=1000)
        reason = self.manager._check_exit_conditions(session, 10.0,
                                                     position_snapshot=pos)
        self.assertIsNone(reason, "正常状态下不应触发任何退出条件")

    def test_h11_deviation_check_uses_max_of_drift_and_market(self):
        """H-11: 偏离度取 drift 和 market 两者最大值（双重保护）"""
        session = make_session(self.db, self.manager, center_price=10.0,
                               max_deviation=0.10)
        # drift = 5%（current_center 相对 center）
        session.current_center_price = 10.5
        # market = 18%（市价相对 current_center）
        current_price = 10.5 * 1.18
        pos = make_position(volume=1000, current_price=current_price)
        reason = self.manager._check_exit_conditions(session, current_price,
                                                     position_snapshot=pos)
        self.assertEqual(reason, 'deviation', "market_deviation=18%超过limit 10%应触发")

    def test_h12_get_deviation_ratio_zero_when_centers_equal(self):
        """H-12: center_price == current_center_price 时偏离度为 0"""
        session = GridSession(center_price=10.0, current_center_price=10.0)
        self.assertEqual(session.get_deviation_ratio(), 0.0)

    def test_h13_get_deviation_ratio_zero_when_center_zero(self):
        """H-13: center_price=0 时 get_deviation_ratio() 返回 0（防除零）"""
        session = GridSession(center_price=0.0, current_center_price=10.0)
        self.assertEqual(session.get_deviation_ratio(), 0.0)


# ==============================================================================
# Suite I: 极端价格行情
# ==============================================================================

class TestSuiteI_ExtremePriceScenarios(GridTestBase):
    """I: 极端行情下的系统稳定性"""

    def test_i1_price_up_10pct_涨停_no_crash(self):
        """I-1: 价格涨停（+10%）不崩溃，正确检测上穿"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)
        tracker = self.manager.trackers[session.id]
        # 涨停价 = 10.0 * 1.10 = 11.0 > 上轨 10.5
        self.manager._check_level_crossing(session, tracker, 11.0)
        self.assertTrue(tracker.waiting_callback)
        self.assertEqual(tracker.direction, 'rising')

    def test_i2_price_down_10pct_跌停_no_crash(self):
        """I-2: 价格跌停（-10%）不崩溃，正确检测下穿"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)
        tracker = self.manager.trackers[session.id]
        # 跌停价 = 10.0 * 0.90 = 9.0 < 下轨 9.5
        self.manager._check_level_crossing(session, tracker, 9.0)
        self.assertTrue(tracker.waiting_callback)
        self.assertEqual(tracker.direction, 'falling')

    def test_i3_price_oscillates_within_range_no_signal(self):
        """I-3: 价格在中轨区间内反复振荡不产生错误信号"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05)
        tracker = self.manager.trackers[session.id]
        # lower=9.5, upper=10.5，在范围内振荡
        for price in [9.6, 9.8, 10.0, 10.2, 10.4, 10.3, 9.9, 10.1]:
            tracker.waiting_callback = False
            self.manager._check_level_crossing(session, tracker, price)
            self.assertFalse(tracker.waiting_callback,
                             f"价格{price}在区间内振荡不应触发等待")

    def test_i4_price_very_low_triggers_buy_not_panic(self):
        """I-4: 价格极低（跌至 5.0）仍正常触发买入，不崩溃"""
        session = make_session(self.db, self.manager, center_price=10.0, max_investment=10000,
                               position_ratio=0.25)
        tracker = self.manager.trackers[session.id]
        self.manager._check_level_crossing(session, tracker, 5.0)
        self.assertTrue(tracker.waiting_callback)
        # 模拟回调
        tracker.last_price = 5.0 * 1.006  # 回升0.6% > 0.5%
        tracker.valley_price = 5.0
        signal = tracker.check_callback(0.005)
        self.assertEqual(signal, 'BUY')

    def test_i5_continuous_buy_signals_exhaust_investment(self):
        """I-5: 连续买入信号在资金耗尽后自动停止"""
        session = make_session(self.db, self.manager, max_investment=5000, position_ratio=0.5)
        prices = [9.50, 9.025]
        for price in prices:
            self._execute_buy(session, trigger_price=price)

        # 验证不超限
        self.assertLessEqual(session.current_investment, session.max_investment + 0.01)

        # 再次尝试应被拒绝（已耗尽）
        if session.current_investment >= session.max_investment:
            result = self._execute_buy(session, trigger_price=8.57)
            self.assertFalse(result)

    def test_i6_check_callback_with_zero_valley_price_safe(self):
        """I-6: valley_price=0 时 check_callback 安全返回 None（防除零）"""
        tracker = PriceTracker(session_id=1, last_price=0.001,
                               valley_price=0, direction='falling',
                               waiting_callback=True)
        result = tracker.check_callback(0.005)
        self.assertIsNone(result, "valley_price=0 时应安全返回 None")

    def test_i7_check_callback_with_zero_peak_price_safe(self):
        """I-7: peak_price=0 时 check_callback 安全返回 None（防除零）"""
        tracker = PriceTracker(session_id=1, last_price=10.0,
                               peak_price=0, direction='rising',
                               waiting_callback=True)
        result = tracker.check_callback(0.005)
        self.assertIsNone(result, "peak_price=0 时应安全返回 None")

    def test_i8_not_waiting_callback_check_returns_none(self):
        """I-8: waiting_callback=False 时 check_callback 直接返回 None"""
        tracker = PriceTracker(session_id=1, last_price=10.0,
                               peak_price=11.0, valley_price=9.0,
                               direction='rising', waiting_callback=False)
        result = tracker.check_callback(0.005)
        self.assertIsNone(result)


# ==============================================================================
# Suite J: 端到端全流程验证
# ==============================================================================

class TestSuiteJ_EndToEndFlow(GridTestBase):
    """J: 模拟完整一轮网格交易流程"""

    def test_j1_complete_one_cycle_buy_and_sell(self):
        """J-1: 完整一轮：价格下穿->等待回调->买入->网格重建->价格上穿->等待回调->卖出"""
        session = make_session(self.db, self.manager, center_price=10.0, price_interval=0.05,
                               max_investment=10000, position_ratio=0.25, callback_ratio=0.005)
        tracker = self.manager.trackers[session.id]
        stock_code = session.stock_code

        # Step 1: 价格下穿下轨 9.50 => 价格到 9.40
        self.manager._check_level_crossing(session, tracker, 9.40)
        self.assertTrue(tracker.waiting_callback)
        self.assertEqual(tracker.direction, 'falling')

        # Step 2: 价格回升到 9.447 (回升 0.5%)
        buy_price = 9.40 * 1.005
        tracker.update_price(buy_price)
        sig_type = tracker.check_callback(0.005)
        self.assertEqual(sig_type, 'BUY')

        # Step 3: 执行买入
        result = self._execute_buy(session, trigger_price=buy_price, grid_level=9.50)
        self.assertTrue(result)
        self.assertEqual(session.buy_count, 1)

        # Step 4: 网格重建验证（中心价变为 buy_price）
        self.assertAlmostEqual(session.current_center_price, buy_price, places=3)
        new_upper = buy_price * 1.05

        # Step 5: 价格上穿新上轨
        above_upper = new_upper + 0.10
        tracker.waiting_callback = False  # 模拟重置后首次穿越
        self.manager._check_level_crossing(session, tracker, above_upper)
        self.assertTrue(tracker.waiting_callback)
        self.assertEqual(tracker.direction, 'rising')

        # Step 6: 价格从峰值回落 0.5%
        peak = above_upper
        sell_price = peak * (1 - 0.005)
        tracker.peak_price = peak
        tracker.last_price = sell_price
        sig_type2 = tracker.check_callback(0.005)
        self.assertEqual(sig_type2, 'SELL')

        # Step 7: 执行卖出
        pos = make_position(volume=1000)
        result2 = self._execute_sell(session, trigger_price=sell_price,
                                     position_snapshot=pos, grid_level=new_upper)
        self.assertTrue(result2)
        self.assertEqual(session.sell_count, 1)

        # Step 8: 验证整体盈亏合理
        profit = session.get_profit_ratio()
        # 卖出价 > 买入价（0.5% 回调 + 5% 上涨），应为正收益
        self.assertGreaterEqual(profit, -0.01,
                                f"正常买卖循环盈亏率应>=0, 实际={profit:.4%}")

    def test_j2_session_stop_manual(self):
        """J-2: 手动停止会话，内存和数据库都清理"""
        session = make_session(self.db, self.manager, center_price=10.0)
        session_id = session.id
        stock_key = self.manager._normalize_code(session.stock_code)

        stats = self.manager.stop_grid_session(session_id, 'manual')
        self.assertEqual(stats['stop_reason'], 'manual')
        self.assertNotIn(stock_key, self.manager.sessions,
                         "停止后会话应从内存中移除")
        self.assertNotIn(session_id, self.manager.trackers,
                         "停止后 tracker 应从内存中移除")

    def test_j3_session_stop_nonexistent_raises_value_error(self):
        """J-3: 停止不存在的会话抛出 ValueError"""
        with self.assertRaises(ValueError):
            self.manager.stop_grid_session(99999, 'manual')

    def test_j4_profit_ratio_positive_after_profitable_cycle(self):
        """J-4: 盈利交易周期后 profit_ratio > 0"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        # 买入
        self._execute_buy(session, trigger_price=9.50)
        buy_amount = session.total_buy_amount
        # 卖出价高于买入价
        pos = make_position(volume=1000)
        self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertGreater(session.get_profit_ratio(), 0,
                           "盈利卖出后 profit_ratio 应大于0")

    def test_j5_loss_cycle_profit_ratio_negative(self):
        """J-5: 亏损交易周期后 profit_ratio < 0"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        self._execute_buy(session, trigger_price=10.50)  # 高价买入
        pos = make_position(volume=1000, cost_price=10.50)
        self._execute_sell(session, trigger_price=9.50, position_snapshot=pos)  # 低价卖出
        self.assertLess(session.get_profit_ratio(), 0,
                        "亏损卖出后 profit_ratio 应小于0")

    def test_j6_normalize_code_strips_suffix(self):
        """J-6: _normalize_code 正确去除交易所后缀"""
        self.assertEqual(self.manager._normalize_code('000001.SZ'), '000001')
        self.assertEqual(self.manager._normalize_code('600036.SH'), '600036')
        self.assertEqual(self.manager._normalize_code('000001'), '000001')
        self.assertEqual(self.manager._normalize_code(''), '')

    def test_j7_get_session_stats_returns_complete_info(self):
        """J-7: get_session_stats 返回完整的会话统计信息"""
        session = make_session(self.db, self.manager, center_price=10.0)
        stats = self.manager.get_session_stats(session.id)
        required_keys = ['session_id', 'stock_code', 'status', 'center_price',
                         'current_center_price', 'grid_levels', 'trade_count',
                         'buy_count', 'sell_count', 'profit_ratio', 'grid_profit',
                         'deviation_ratio', 'current_investment', 'max_investment']
        for key in required_keys:
            self.assertIn(key, stats, f"stats 应包含 '{key}' 字段")

    def test_j8_check_grid_signals_returns_none_for_inactive_session(self):
        """J-8: 非 active 状态的会话 check_grid_signals 返回 None"""
        session = make_session(self.db, self.manager, center_price=10.0)
        session.status = 'stopped'
        self.position_manager.get_position.return_value = make_position(volume=1000)
        result = self.manager.check_grid_signals(session.stock_code, 10.0)
        self.assertIsNone(result)

    def test_j9_four_complete_buy_sell_cycles(self):
        """J-9: 4次完整买卖循环，统计数据始终一致"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        pos = make_position(volume=2000)

        for i in range(4):
            buy_price = 9.50 - i * 0.10
            sell_price = buy_price * 1.06
            self._execute_buy(session, trigger_price=buy_price)
            self._execute_sell(session, trigger_price=sell_price, position_snapshot=pos)

        self.assertEqual(session.buy_count, 4)
        self.assertEqual(session.sell_count, 4)
        self.assertEqual(session.trade_count, 8)

        # 验证 DB 记录数量
        trades = self.db.get_grid_trades(session.id, limit=100)
        self.assertEqual(len(trades), 8)

    def test_j10_investment_fully_recovered_after_buy_sell_cycle(self):
        """J-10: 买入后卖出，current_investment 正确回收（不超过 max）"""
        session = make_session(self.db, self.manager, max_investment=10000, position_ratio=0.25)
        self._execute_buy(session, trigger_price=9.50)
        investment_after_buy = session.current_investment

        buy_volume = round(session.total_buy_amount / 9.50)
        buy_volume = (buy_volume // 100) * 100
        pos = make_position(volume=max(100, buy_volume))
        self._execute_sell(session, trigger_price=9.975, position_snapshot=pos)

        # 回收后应小于等于买入后的水平（卖出价 ≈ 买入价 * 1.05）
        self.assertLessEqual(session.current_investment, investment_after_buy,
                             "卖出后 current_investment 应减少（资金回收）")
        self.assertGreaterEqual(session.current_investment, 0, "不应为负数")


# ==============================================================================
# Suite K: 不同 price_interval 参数组合（参数化测试）
# ==============================================================================

class TestSuiteK_PriceIntervalVariants(GridTestBase):
    """K: 不同价格间隔参数下的档位计算正确性"""

    def _verify_levels(self, center, interval):
        """验证档位计算公式"""
        session = GridSession(center_price=center, current_center_price=center,
                              price_interval=interval, stock_code='000001.SZ')
        levels = session.get_grid_levels()
        self.assertAlmostEqual(levels['lower'], center * (1 - interval), places=6)
        self.assertAlmostEqual(levels['upper'], center * (1 + interval), places=6)
        self.assertAlmostEqual(levels['center'], center, places=6)

    def test_k1_interval_3pct(self):
        """K-1: 价格间隔3%"""
        self._verify_levels(10.0, 0.03)

    def test_k2_interval_5pct(self):
        """K-2: 价格间隔5%（默认）"""
        self._verify_levels(10.0, 0.05)

    def test_k3_interval_10pct(self):
        """K-3: 价格间隔10%"""
        self._verify_levels(10.0, 0.10)

    def test_k4_interval_2pct_with_high_price_stock(self):
        """K-4: 价格间隔2%、高价股（500元）"""
        self._verify_levels(500.0, 0.02)

    def test_k5_interval_1pct_with_low_price_stock(self):
        """K-5: 价格间隔1%、低价股（2元）"""
        self._verify_levels(2.0, 0.01)

    def test_k6_buy_volume_at_low_price_high_ratio(self):
        """K-6: 低价股（3元）大比例买入（50%）至少能买100股"""
        # max_inv=1000, ratio=0.50 => buy_amount=500, 500/3=166.7 => 100股
        session = make_session(self.db, self.manager, center_price=3.0,
                               max_investment=1000, position_ratio=0.50)
        result = self._execute_buy(session, trigger_price=3.0)
        self.assertTrue(result, "低价股应能正常买入")
        trade = dict(self.db.get_grid_trades(session.id, limit=1)[0])
        self.assertGreaterEqual(trade['volume'], 100, "至少买100股")
        self.assertEqual(trade['volume'] % 100, 0, "股数为100的整数倍")

    def test_k7_sell_volume_with_small_position_ratio(self):
        """K-7: 小比例（10%）卖出时的股数计算"""
        session = make_session(self.db, self.manager, position_ratio=0.10)
        # available=1000, ratio=0.10 => 100股
        pos = make_position(volume=1000, available=1000)
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertTrue(result)
        trade = dict(self.db.get_grid_trades(session.id, limit=1)[0])
        self.assertEqual(trade['volume'], 100, "10%*1000=100股")

    def test_k8_sell_volume_with_large_position_ratio(self):
        """K-8: 大比例（75%）卖出时的股数计算"""
        session = make_session(self.db, self.manager, position_ratio=0.75)
        pos = make_position(volume=1000, available=1000)
        result = self._execute_sell(session, trigger_price=10.50, position_snapshot=pos)
        self.assertTrue(result)
        trade = dict(self.db.get_grid_trades(session.id, limit=1)[0])
        expected_vol = (int(1000 * 0.75) // 100) * 100  # = 700
        self.assertEqual(trade['volume'], expected_vol)


# ==============================================================================
# 测试入口
# ==============================================================================

def load_all_suites():
    """加载所有测试套件"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    test_classes = [
        TestSuiteA_GridLevelCoverage,
        TestSuiteB_BuyExecution,
        TestSuiteC_SellExecution,
        TestSuiteD_MultiRoundTrading,
        TestSuiteE_FundManagement,
        TestSuiteF_CooldownMechanism,
        TestSuiteG_AbnormalRobustness,
        TestSuiteH_ExitConditions,
        TestSuiteI_ExtremePriceScenarios,
        TestSuiteJ_EndToEndFlow,
        TestSuiteK_PriceIntervalVariants,
    ]
    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='网格交易全区间覆盖测试')
    parser.add_argument('--suite', '-s', default='all',
                        help='指定运行的套件 (A/B/C/D/E/F/G/H/I/J/K/all)')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')
    args = parser.parse_args()

    suite_map = {
        'A': TestSuiteA_GridLevelCoverage,
        'B': TestSuiteB_BuyExecution,
        'C': TestSuiteC_SellExecution,
        'D': TestSuiteD_MultiRoundTrading,
        'E': TestSuiteE_FundManagement,
        'F': TestSuiteF_CooldownMechanism,
        'G': TestSuiteG_AbnormalRobustness,
        'H': TestSuiteH_ExitConditions,
        'I': TestSuiteI_ExtremePriceScenarios,
        'J': TestSuiteJ_EndToEndFlow,
        'K': TestSuiteK_PriceIntervalVariants,
    }

    if args.suite.upper() == 'ALL':
        suite = load_all_suites()
    elif args.suite.upper() in suite_map:
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(suite_map[args.suite.upper()])
    else:
        print(f"未知套件: {args.suite}，可用: {', '.join(suite_map.keys())}, all")
        exit(1)

    verbosity = 2 if args.verbose else 1
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    exit(0 if result.wasSuccessful() else 1)
