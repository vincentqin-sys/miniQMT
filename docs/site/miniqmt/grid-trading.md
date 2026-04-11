# 网格交易

## 概述

网格交易在股票价格波动中自动低吸高抛，通过在预设的价格档位之间反复买卖来累积利润。

## 核心概念

### 网格会话（GridSession）

每只股票启动一个独立的网格会话，包含以下参数：

| 参数 | 说明 |
|------|------|
| `center_price` | 网格中心价格 |
| `price_interval` | 价格档位间距（元） |
| `position_ratio` | 每档买入仓位比例 |
| `callback_ratio` | 回调触发比例（0.5%） |
| `max_investment` | 单会话最大投入金额 |
| `max_deviation` | 最大偏离中心价比例（±15%） |
| `target_profit` | 目标盈利比例（10%） |
| `stop_loss` | 网格止损比例（-10%） |

### 价格追踪器（PriceTracker）

实时追踪价格走势，检测网格信号：

- 记录最近峰值/谷值
- 检测是否穿越网格档位
- 判断回调触发条件

### 信号流程

```
价格更新 → PriceTracker.update_price()
  → 检测穿越网格档位 → crossed_level
    → 等待回调确认 → check_callback()
      → 回调达标 → 生成买入/卖出信号
        → execute_grid_trade() → 实际下单
```

---

## 退出条件

网格会话在以下条件满足时自动退出：

| 条件 | 说明 |
|------|------|
| 达到目标盈利 | `true_pnl_ratio >= target_profit` |
| 触发止损 | `true_pnl_ratio <= stop_loss` |
| 超出偏离范围 | 当前价偏离中心价 > `max_deviation` |
| 到达结束时间 | `end_time` 到期 |
| 手动停止 | 通过 Web API 或代码调用 |

---

## 通过 Web 界面使用

1. 访问 `http://localhost:5000`
2. 在股票列表中选择目标股票
3. 点击「启动网格」
4. 配置参数（或使用模板）
5. 确认启动

## 通过 API 使用

```bash
# 启动网格（使用默认参数）
curl -X POST http://localhost:5000/api/grid/start \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "000001.SZ", "center_price": 10.50}'

# 查看网格状态
curl http://localhost:5000/api/grid/status/000001.SZ

# 查看所有活跃网格
curl http://localhost:5000/api/grid/sessions

# 停止网格
curl -X POST http://localhost:5000/api/grid/stop \
  -H "Content-Type: application/json" \
  -d '{"session_id": 1}'
```

---

## 风险分级模板

系统内置三档风险模板：

| 模板 | 回调比例 | 档位间距 | 止损 | 目标盈利 |
|------|---------|---------|------|---------|
| 保守 | 0.3% | 0.03 | -5% | 5% |
| 标准 | 0.5% | 0.05 | -10% | 10% |
| 激进 | 1.0% | 0.10 | -15% | 15% |

---

## 冷却机制

防止短时间内重复交易：

| 冷却类型 | 默认值 | 说明 |
|---------|--------|------|
| `GRID_LEVEL_COOLDOWN` | 60 秒 | 同一档位两次交易间隔 |
| `GRID_BUY_COOLDOWN` | 300 秒 | 买入成功后全局冷却 |
| `GRID_SELL_COOLDOWN` | 300 秒 | 卖出成功后全局冷却 |

---

## 注意事项

- 网格交易需要先触发首次止盈（`GRID_REQUIRE_PROFIT_TRIGGERED = True`）
- 每只股票同一时间只能有一个活跃网格会话
- 网格数据持久化在 SQLite `grid_sessions` / `grid_trades` 表中
- 建议在模拟模式下充分验证策略后再切换实盘
