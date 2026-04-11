# 数据库表结构

## positions（持仓表）

核心持仓信息，双层存储（内存 + SQLite）。

| 字段 | 数据来源 | 更新频率 | 说明 |
|------|---------|---------|------|
| `stock_code` | QMT 实盘 | 首次同步 | 股票代码 |
| `volume` | QMT 实盘 | 10 秒 | 持仓数量 |
| `available` | QMT 实盘 | 10 秒 | 可用数量 |
| `cost_price` | QMT 实盘 | 10 秒 | 成本价 |
| `current_price` | data_manager | 实时 | 当前价格 |
| `market_value` | 计算 | 实时 | 市值 |
| `profit_ratio` | 计算 | 实时 | 盈亏比例 |
| `open_date` | 持久化 | 首次买入 | 开仓日期 |
| `profit_triggered` | 持久化 | 首次止盈 | 是否已触发首次止盈 |
| `highest_price` | 持久化 | 价格更新时 | 持仓期间最高价 |
| `stop_loss_price` | 持久化 | 策略触发时 | 止损价格 |

### 关键字段说明

- **`profit_triggered`**：影响后续动态止盈逻辑，首次止盈前不启用动态止盈
- **`highest_price`**：用于计算动态止盈位，持续更新
- **`stop_loss_price`**：低于此价格触发全部卖出

---

## trade_records（交易记录表）

记录所有买卖交易。

| 字段 | 说明 |
|------|------|
| `stock_code` | 股票代码 |
| `trade_type` | `BUY` / `SELL` |
| `price` | 成交价格 |
| `volume` | 成交数量 |
| `trade_id` | 订单 ID（实盘为 QMT 返回的 order_id，模拟为 `SIM{timestamp}{counter}`） |
| `strategy` | 策略标识（`simu` / `auto_partial` / `stop_loss` / `grid`） |
| `timestamp` | 交易时间 |

---

## 网格交易表

### grid_sessions（网格会话表）

| 字段 | 说明 |
|------|------|
| `id` | 会话 ID |
| `stock_code` | 股票代码 |
| `status` | `active` / `stopped` / `completed` |
| `center_price` | 网格中心价格 |
| `current_center_price` | 当前中心价格（可能调整） |
| `price_interval` | 档位间距 |
| `position_ratio` | 仓位比例 |
| `max_investment` | 最大投入金额 |
| `max_deviation` | 最大偏离比例 |
| `target_profit` | 目标盈利比例 |
| `stop_loss` | 止损比例 |
| `end_time` | 会话结束时间 |

### grid_trades（网格交易表）

| 字段 | 说明 |
|------|------|
| `session_id` | 关联的网格会话 ID |
| `stock_code` | 股票代码 |
| `trade_type` | `BUY` / `SELL` |
| `price` | 成交价格 |
| `volume` | 成交数量 |
| `grid_level` | 触发的网格档位 |
| `timestamp` | 交易时间 |

---

## 同步机制

```
内存数据库 ←→ SQLite

每 15 秒（POSITION_SYNC_INTERVAL）:
  内存 → SQLite: 持久化所有 positions 表的持久化字段
  SQLite → 内存: 恢复 open_date、profit_triggered、highest_price 等字段
```

- 内存数据库存储高频更新数据（价格、市值、盈亏比例）
- SQLite 持久化关键状态，系统重启后自动恢复
- 修改内存数据后必须调用 `_increment_data_version()` 触发前端更新
