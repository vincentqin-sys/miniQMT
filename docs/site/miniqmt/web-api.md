# Web API

miniQMT 提供 RESTful API（Flask），默认监听 `http://0.0.0.0:5000`。

**认证**：需要 Token 的接口通过 `QMT_API_TOKEN` 环境变量设置。

---

## 系统状态

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/connection/status` | QMT 连接状态 |
| GET | `/api/status` | 系统运行状态总览 |
| GET | `/api/debug/status` | 详细调试状态 |

---

## 持仓与交易记录

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/positions` | 当前持仓列表 |
| GET | `/api/positions-all` | 全部持仓详情 |
| GET | `/api/trade-records` | 交易记录 |
| POST | `/api/initialize_positions` | 初始化持仓数据 |
| POST | `/api/holdings/init` | 初始化持股配置 |

---

## 交易操作

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/actions/execute_buy` | 执行买入 |
| POST | `/api/actions/execute_sell` | 执行卖出 |
| POST | `/api/actions/execute_trading_signal` | 执行指定交易信号 |

**买入参数**：

```json
{
  "stock_code": "000001.SZ",
  "amount": 100,
  "strategy": "manual"
}
```

---

## 网格交易 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/grid/start` | 启动网格会话 |
| POST | `/api/grid/stop/<session_id>` | 停止指定网格 |
| POST | `/api/grid/stop` | 停止所有网格 |
| GET | `/api/grid/session/<stock_code>` | 按股票查网格状态 |
| GET | `/api/grid/session/<session_id>` | 按会话 ID 查详情 |
| GET | `/api/grid/sessions` | 所有网格会话 |
| GET | `/api/grid/trades/<session_id>` | 网格交易记录 |
| GET | `/api/grid/status/<stock_code>` | 网格快速状态 |
| GET | `/api/grid/config` | 网格配置 |
| GET | `/api/grid/templates` | 网格模板列表 |
| POST | `/api/grid/template/save` | 保存网格模板 |
| DELETE | `/api/grid/template/<name>` | 删除模板 |
| POST | `/api/grid/template/use` | 使用模板 |
| GET | `/api/grid/template/default` | 获取默认模板 |
| PUT | `/api/grid/template/<name>/default` | 设为默认模板 |
| GET | `/api/grid/risk-templates` | 风险分级模板 |

---

## 配置管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取系统配置 |
| POST | `/api/config/save` | 保存配置（需 Token） |

---

## 监控控制

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/monitor/start` | 启动持仓监控 |
| POST | `/api/monitor/stop` | 停止持仓监控 |

---

## 股票池

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/stock_pool/list` | 获取股票池列表 |

---

## 实时推送

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sse` | Server-Sent Events 实时更新 |
| GET | `/api/positions/stream` | 持仓数据流 |

---

## 数据管理（需 Token）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/logs/clear` | 清空日志 |
| POST | `/api/data/clear_buysell` | 清除买卖数据 |
| POST | `/api/data/import` | 导入数据 |
