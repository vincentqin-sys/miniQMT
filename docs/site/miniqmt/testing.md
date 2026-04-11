# 测试框架

## 概述

项目包含 65+ 个测试文件、约 21,000 行测试代码、1170+ 个测试用例，全部通过。位于 [test/](https://github.com/weihong-su/miniQMT/tree/main/test) 目录。

---

## 测试基础设施

| 文件 | 说明 |
|------|------|
| `test/test_base.py` | `TestBase` 基类：测试 DB 创建、持仓 fixture、线程断言、条件等待 |
| `test/test_mocks.py` | `MockQmtTrader`：完整模拟 QMT API（连接、持仓、下单），无需真实 QMT |
| `test/test_utils.py` | 通用测试辅助函数 |

---

## 运行测试

### 回归测试框架（推荐）

```bash
# 快速验证（5 分钟，17 个模块，419 个用例）
python test/run_integration_regression_tests.py --fast

# 运行全部回归测试
python test/run_integration_regression_tests.py --all

# 按组运行
python test/run_integration_regression_tests.py --group system_integration
python test/run_integration_regression_tests.py --group stop_profit
python test/run_integration_regression_tests.py --group grid_signal
python test/run_integration_regression_tests.py --group grid_session
python test/run_integration_regression_tests.py --group grid_trade
python test/run_integration_regression_tests.py --group grid_exit
python test/run_integration_regression_tests.py --group grid_validation
python test/run_integration_regression_tests.py --group grid_comprehensive
python test/run_integration_regression_tests.py --group grid_bug_regression
```

### 其他选项

```bash
# 失败重试
python test/run_integration_regression_tests.py --all --retry-failed

# 详细输出
python test/run_integration_regression_tests.py --all --verbose

# 跳过环境准备（不备份生产 DB）
python test/run_integration_regression_tests.py --all --skip-env-prep
```

### 单个测试文件

```bash
python test/run_single_test.py test.test_unattended_operation
python -m unittest test.test_system_integration -v
python test/run_all_grid_tests.py
```

---

## 测试报告

自动输出到：

- `test/integration_test_report.json` — JSON 格式
- `test/integration_test_report.md` — Markdown 格式

---

## 测试分组

| 组名 | 优先级 | 内容 |
|------|--------|------|
| `system_integration` | critical | 系统集成、无人值守、线程监控 |
| `stop_profit` | high | 动态止盈止损策略（7 个模块） |
| `grid_signal` | high | 网格信号检测与价格追踪 |
| `grid_session` | high | 网格会话生命周期管理 |
| `grid_trade` | high | 网格买卖执行与资金管理 |
| `grid_exit` | high | 网格退出条件检测 |
| `grid_comprehensive` | high | 网格综合端到端场景 |
| `grid_validation` | medium | 参数校验与边界情况 |
| `grid_bug_regression` | high | 已修复 Bug 的回归验证 |
| `web_api` | critical | RESTful API 功能测试 |
| `db_thread_safety` | critical | 数据库线程安全验证 |
| `dual_layer_storage` | critical | 内存 + SQLite 双层存储一致性 |
| `fast` | critical | 5 分钟快速验证子集 |

---

## 编写新测试

```python
from test.test_base import TestBase
from test.test_mocks import MockQmtTrader

class TestMyFeature(TestBase):
    def setUp(self):
        super().setUp()
        self.mock_trader = MockQmtTrader()
        self.mock_trader.add_mock_position("000001.SZ", volume=1000, cost_price=10.0)

    def test_something(self):
        # 测试代码...
        self.wait_for_condition(lambda: condition_met, timeout=5)
```

测试运行时自动备份生产 DB，测试完成后恢复。
