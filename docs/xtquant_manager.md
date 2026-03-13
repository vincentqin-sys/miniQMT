# XtQuantManager 产品说明

> **版本**: 1.0.0 | **最后更新**: 2026-03-13

---

## 目录

1. [产品概述](#1-产品概述)
2. [核心特性](#2-核心特性)
3. [架构说明](#3-架构说明)
4. [快速开始](#4-快速开始)
5. [配置参考](#5-配置参考)
6. [HTTP API 参考](#6-http-api-参考)
7. [Python SDK 参考](#7-python-sdk-参考)
8. [安全配置](#8-安全配置)
9. [可观测性](#9-可观测性)
10. [与 miniQMT 集成](#10-与-miniqmt-集成)
11. [常见问题](#11-常见问题)

---

## 1. 产品概述

XtQuantManager 是 miniQMT 的 **xtquant 接口统一管理层**，通过 HTTP 服务将迅投 QMT 的交易接口（xttrader）和行情接口（xtdata）封装为 RESTful API，解决以下痛点：

| 痛点 | XtQuantManager 解决方案 |
|------|------------------------|
| 无法管理多账号 | 多账号注册表，支持同时管理任意数量 QMT 账号 |
| 断线无法自动重连 | 三级健康监控 + 指数退避自动重连 |
| 超时保护不统一 | 全接口统一超时保护（默认 3 秒） |
| 无可观测性 | 实时指标（延迟、错误率、P95）+ HTTP 查询 |
| xtquant 硬耦合 | HTTP 抽象层 + 工厂函数开关，零侵入切换 |

### 设计原则

- **兼容优先**: `XtQuantClient` 暴露与 `easy_qmt_trader` 完全相同的方法签名，`XtDataAdapter` 兼容 `xtquant.xtdata` 接口，现有代码零改动即可切换
- **失败静默**: 所有接口在失败时返回空 DataFrame/dict，不抛异常
- **零停机切换**: 通过 `config.ENABLE_XTQUANT_MANAGER` 开关一键切换，不影响现有功能

---

## 2. 核心特性

### 多账号管理

- 支持同时注册和管理任意数量的 QMT 账号
- 每个账号独立的连接生命周期、超时配置、健康监控
- 支持运行时动态注册/注销账号

### 健康监控与自动重连（三级策略）

```
Level 0  每 30s  is_healthy()  ← 纯内存检查，无 I/O
              │ 不健康
Level 1        ping()          ← 真实探测（get_full_tick，3s 超时）
              │ 失败
Level 2        reconnect()     ← 指数退避重连（60s → 3600s）
                               ← 60s 冷却时间，防重连风暴
```

### 统一超时保护

所有 xtquant API 调用均通过独立线程包装，默认 3 秒超时。超时后主线程立即返回，xtquant 阻塞线程后台继续等待直至 API 返回。

### 多层安全防护

- API Token 认证（本机访问免认证）
- IP 白名单过滤
- 速率限制（令牌桶算法，每 IP 独立计数）
- HMAC 请求签名（可选，适用于公网）
- HTTPS / TLS 传输加密（可选，适用于局域网）

### 实时可观测指标

每账号独立统计，实时可查：

- 调用总量、成功数、错误数、超时数
- 平均延迟、P50、P95 延迟（最近 1000 次滑动窗口）
- 整体错误率（最近 100 次滑动窗口）
- 按操作类型分组统计

---

## 3. 架构说明

### 系统层级

```
┌──────────────────────────────────────────────────────────┐
│  miniQMT 主体代码                                         │
│  position_manager.py / data_manager.py                   │
├──────────────────────────────────────────────────────────┤
│  XtQuantClient (HTTP 客户端)                              │
│  · 与 easy_qmt_trader 接口兼容                            │
│  XtDataAdapter (行情适配器)                               │
│  · 与 xtquant.xtdata 接口兼容                             │
├──────────────────────────────────────────────────────────┤
│           HTTP (REST API / JSON)                         │
├──────────────────────────────────────────────────────────┤
│  XtQuantServer (FastAPI + uvicorn)                       │
│  SecurityMiddleware  │  API 路由  │  HealthMonitor        │
├──────────────────────────────────────────────────────────┤
│  XtQuantManager (单例，多账号注册表)                       │
├──────────────────────────────────────────────────────────┤
│  XtQuantAccount × N (每账号独立实例)                      │
│  · 连接管理  · 超时保护  · 指标收集  · 重连策略            │
├──────────────────────────────────────────────────────────┤
│  xtquant API (xttrader + xtdata，本机 QMT 客户端)         │
└──────────────────────────────────────────────────────────┘
```

### 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| 公共入口 | `__init__.py` | 统一导出所有公共 API |
| 服务启停 | `server_runner.py` | uvicorn 后台线程 / 阻塞模式封装 |
| HTTP 路由 | `server.py` | FastAPI 路由定义（约 30 个端点） |
| 数据模型 | `models.py` | Pydantic 请求/响应模型 |
| 客户端 | `client.py` | HTTP 客户端 + xtdata 适配器 |
| 多账号管理 | `manager.py` | 全局单例，账号注册表，请求分发 |
| 单账号 | `account.py` | 单账号生命周期，所有 xtquant 调用 |
| 健康监控 | `health_monitor.py` | 后台三级健康检查线程 |
| 安全层 | `security.py` | IP 白名单、API Key、速率限制、HMAC |
| 超时工具 | `timeout.py` | 统一超时包装（ThreadPoolExecutor） |
| 指标统计 | `metrics.py` | 滑动窗口调用指标收集 |
| 异常定义 | `exceptions.py` | 自定义异常类层级 |
| 证书工具 | `utils/gen_cert.py` | 自签 SSL 证书生成（SAN 扩展） |

---

## 4. 快速开始

### 4.1 依赖安装

```bash
pip install fastapi>=0.110.0 uvicorn[standard]>=0.29.0 httpx>=0.27.0 pydantic>=2.0.0
```

### 4.2 模式一：嵌入 miniQMT（推荐）

在 `config.py` 中开启开关：

```python
ENABLE_XTQUANT_MANAGER = True
XTQUANT_MANAGER_URL = "http://127.0.0.1:8888"
XTQUANT_MANAGER_TOKEN = ""   # 可选，局域网部署时建议设置
```

启动 `main.py`，系统会自动：
1. 启动 XtQuantManager HTTP 服务（127.0.0.1:8888）
2. 注册 `account_config.json` 中的账号
3. 所有 xtquant 调用通过 HTTP 路由

### 4.3 模式二：独立进程运行

```python
from xtquant_manager import XtQuantServer, XtQuantServerConfig, XtQuantManager, AccountConfig

# 启动服务器
server = XtQuantServer(XtQuantServerConfig(
    host="127.0.0.1",
    port=8888,
    api_token="my_secret_token",
))
server.start(blocking=False)

# 注册账号
manager = XtQuantManager.get_instance()
manager.register_account(AccountConfig(
    account_id="88888888",
    qmt_path="C:/QMT/userdata_mini",
    account_type="STOCK",
))

# 阻塞运行
server.start(blocking=True)
```

### 4.4 模式三：通过 HTTP API（Python 客户端）

```python
from xtquant_manager.client import XtQuantClient, ClientConfig

client = XtQuantClient(config=ClientConfig(
    base_url="http://127.0.0.1:8888",
    account_id="88888888",
    api_token="my_secret_token",
))

# 查询持仓（与 easy_qmt_trader.position() 返回格式相同）
positions = client.position()
print(positions)

# 查询资产
balance = client.balance()
print(balance)

# 获取实时行情
from xtquant_manager.client import XtDataAdapter
xtdata = XtDataAdapter(client)
tick = xtdata.get_full_tick(["000001.SZ", "600036.SH"])
print(tick)
```

### 4.5 通过 HTTP API（curl）

```bash
# 健康检查
curl http://localhost:8888/api/v1/health

# 注册账号
curl -X POST http://localhost:8888/api/v1/accounts \
  -H "Content-Type: application/json" \
  -d '{"account_id":"88888888","qmt_path":"C:/QMT/userdata_mini","account_type":"STOCK"}'

# 查询持仓
curl "http://localhost:8888/api/v1/accounts/88888888/positions"

# 查询资产
curl "http://localhost:8888/api/v1/accounts/88888888/asset"

# 查询指标
curl "http://localhost:8888/api/v1/metrics/88888888"
```

---

## 5. 配置参考

### 5.1 `config.py` — miniQMT 集成开关

```python
# 是否启用 XtQuantManager（False=原始 xtquant 接口，True=HTTP 路由）
ENABLE_XTQUANT_MANAGER = False

# XtQuantManager 服务地址
XTQUANT_MANAGER_URL = "http://127.0.0.1:8888"

# API Token（空字符串=本机访问不验证）
XTQUANT_MANAGER_TOKEN = ""
```

### 5.2 `XtQuantServerConfig` — HTTP 服务配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `host` | str | `"127.0.0.1"` | 绑定地址（局域网用 `"0.0.0.0"` 或具体 IP） |
| `port` | int | `8888` | 监听端口 |
| `api_token` | str | `""` | API 认证令牌（空=不验证） |
| `allowed_ips` | list | `[]` | IP 白名单，空表示不限制 |
| `rate_limit` | int | `60` | 每分钟每 IP 最大请求数（0=不限制） |
| `enable_hmac` | bool | `False` | 启用 HMAC 请求签名 |
| `hmac_secret` | str | `""` | HMAC 密钥 |
| `ssl_certfile` | str | `""` | SSL 证书路径（有值则启用 HTTPS） |
| `ssl_keyfile` | str | `""` | SSL 密钥路径 |
| `health_check_interval` | float | `30.0` | 健康检查间隔（秒） |
| `reconnect_cooldown` | float | `60.0` | 重连冷却时间（秒） |

### 5.3 `AccountConfig` — 账号配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `account_id` | str | 必需 | QMT 交易账号 ID |
| `qmt_path` | str | 必需 | QMT `userdata_mini` 目录路径 |
| `account_type` | str | `"STOCK"` | 账户类型（`STOCK` / `FUTURE`） |
| `session_id` | int\|None | `None` | 会话 ID（None=随机生成） |
| `call_timeout` | float | `3.0` | 普通 API 调用超时（秒） |
| `download_timeout` | float | `30.0` | 历史数据下载超时（秒） |
| `reconnect_base_wait` | float | `60.0` | 重连基础等待时间（指数退避起点，秒） |
| `max_reconnect_attempts` | int | `5` | 触发指数退避的最大尝试计数 |
| `ping_stock` | str | `"000001.SZ"` | 心跳探测使用的股票代码 |

### 5.4 `ClientConfig` — 客户端配置

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `base_url` | str | `"http://127.0.0.1:8888"` | XtQuantManager 服务地址 |
| `account_id` | str | `""` | 目标账号 ID |
| `api_token` | str | `""` | API 认证令牌 |
| `timeout` | float | `5.0` | HTTP 请求超时（秒） |
| `max_retries` | int | `2` | 连接错误最大重试次数 |
| `retry_delay` | float | `0.5` | 重试间隔（秒） |
| `verify_ssl` | bool | `False` | 是否验证 SSL 证书 |
| `ca_cert` | str | `""` | 自签证书的 CA 文件路径 |

---

## 6. HTTP API 参考

**Base URL**: `http://{host}:{port}/api/v1`

**认证**: 请求头 `X-API-Token: <token>`（本机访问或未配置 token 时可省略）

**响应格式**:
```json
{
  "success": true,
  "data": { ... },
  "error": ""
}
```

**错误码**:
| HTTP 状态码 | 含义 |
|------------|------|
| 200 | 成功 |
| 201 | 创建成功（注册账号） |
| 401 | 认证失败（Token 错误） |
| 403 | IP 未授权 |
| 404 | 账号不存在 |
| 429 | 请求过于频繁（速率限制） |
| 504 | 操作超时 |
| 502 | xtquant API 调用失败 |

### 账号管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/accounts` | 注册并连接账号 |
| `DELETE` | `/accounts/{id}` | 注销账号 |
| `GET` | `/accounts` | 列出所有账号 ID |
| `GET` | `/accounts/{id}/status` | 获取账号连接状态 |

**注册账号请求体**:
```json
{
  "account_id": "88888888",
  "account_type": "STOCK",
  "qmt_path": "C:/QMT/userdata_mini",
  "call_timeout": 3.0,
  "reconnect_interval": 60.0,
  "max_reconnect_attempts": 5
}
```

### 交易操作

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/accounts/{id}/orders` | 下单 |
| `DELETE` | `/accounts/{id}/orders/{order_id}` | 撤单 |
| `GET` | `/accounts/{id}/positions` | 查询持仓 |
| `GET` | `/accounts/{id}/asset` | 查询账户资产 |
| `GET` | `/accounts/{id}/orders` | 查询当日委托 |
| `GET` | `/accounts/{id}/trades` | 查询当日成交 |

**下单请求体**:
```json
{
  "stock_code": "000001.SZ",
  "order_type": 23,
  "order_volume": 100,
  "price_type": 11,
  "price": 10.50,
  "strategy_name": "grid",
  "order_remark": "网格买入"
}
```

> 常用 `order_type`：23=限价买入，24=限价卖出

### 行情操作

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/market/tick` | 获取全推行情 |
| `GET` | `/market/history` | 获取历史行情 |
| `POST` | `/market/download` | 下载历史数据 |

**行情查询参数**:
```
GET /api/v1/market/tick?stock_codes=000001.SZ,600036.SH&account_id=88888888
GET /api/v1/market/history?stock_code=000001.SZ&account_id=88888888&period=1d&start_time=20250101&end_time=20260313
```

### 可观测性

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 所有账号健康状态 |
| `GET` | `/health/{id}` | 单账号健康状态 |
| `GET` | `/metrics` | 所有账号指标 |
| `GET` | `/metrics/{id}` | 单账号指标 |

---

## 7. Python SDK 参考

### `XtQuantClient` 方法列表

#### 连接管理
```python
client.connect()
# 返回: (self, self) 成功 | None 失败
# 说明: 验证服务器可达（GET /health）

client.close()
# 说明: 关闭 HTTP 会话，释放连接资源
```

#### 持仓与资产（兼容 easy_qmt_trader）
```python
client.position() -> pd.DataFrame
# 列: 账号类型, 资金账号, 证券代码, 股票余额, 可用余额,
#     成本价, 参考成本价, 市值, 证券名称, 冻结数量,
#     选择, 持股天数, 交易状态, 明细, 市价, 盈亏, 盈亏比(%), 当日买入, 当日卖出
# 失败返回含列名的空 DataFrame

client.balance() -> pd.DataFrame
# 列: 账号类型, 资金账户, 可用金额, 冻结金额, 持仓市值, 总资产
# 失败返回空 DataFrame

client.query_stock_asset() -> dict
# 返回: {账号类型, 资金账户, 可用金额, 冻结金额, 持仓市值, 总资产}
# 失败返回 {}
```

#### 交易操作
```python
client.order_stock(
    stock_code: str,         # 如 "000001.SZ"
    order_type: int,         # 23=限价买, 24=限价卖
    order_volume: int,       # 数量（手 × 100）
    price_type: int = 11,    # 11=限价
    price: float = 0.0,      # 委托价格
    strategy_name: str = "", # 策略名称（日志用）
    order_remark: str = "",  # 备注
) -> int
# 返回: 订单 ID（≥0） | -1 失败

client.buy(security, order_type, amount, price=0.0) -> int
client.sell(security, order_type, amount, price=0.0) -> int
# 简化接口，amount 为数量（手×100）

client.cancel_order_stock(order_id: int) -> int
# 返回: 0=成功 | 非0=失败

client.query_stock_orders() -> pd.DataFrame
client.query_stock_trades() -> pd.DataFrame
```

#### 行情操作
```python
client.get_full_tick(stock_codes: list) -> dict
# 返回: {code: {lastPrice, open, high, low, volume, ...}}
# 失败返回 {}

client.get_market_data_ex(
    fields: list,           # 字段列表，空列表=获取全部
    stock_list: list,       # 股票代码列表
    period: str = "1d",     # 周期：1m, 5m, 15m, 30m, 60m, 1d
    start_time: str = "20200101",
    end_time: str = "",     # 空=当前日期
) -> dict
# 返回: {code: {"open": {...}, "close": {...}, ...}}（dict-of-dicts）
# 与 xtquant.xtdata.get_market_data_ex 格式兼容

client.download_history_data(
    stock_code: str,
    period: str = "1d",
    start_time: str = "20200101",
    end_time: str = "",
) -> bool
```

#### 可观测性
```python
client.is_connected() -> bool
client.health() -> dict
client.get_account_status() -> dict
client.get_metrics() -> dict
```

### `XtDataAdapter` — xtdata 兼容接口

```python
from xtquant_manager.client import XtQuantClient, ClientConfig, XtDataAdapter

client = XtQuantClient(config=ClientConfig(base_url="...", account_id="..."))
xtdata = XtDataAdapter(client)

# 与 xtquant.xtdata 接口完全兼容
xtdata.connect() -> bool
xtdata.get_full_tick(stock_codes: list) -> dict
xtdata.get_market_data_ex(fields, stock_list, period, start_time, end_time) -> dict
xtdata.download_history_data(stock_code, period, start_time, end_time)
```

---

## 8. 安全配置

### 本机使用（开发环境）

```python
XtQuantServerConfig(
    host="127.0.0.1",  # 仅本机可访问
    port=8888,
    api_token="",      # 无需认证
)
```

### 局域网使用（推荐）

```python
XtQuantServerConfig(
    host="192.168.1.100",       # 绑定局域网 IP
    port=8888,
    api_token="my-secret-key",  # 设置 Token
    allowed_ips=["192.168.1.0/24"],  # 仅允许局域网
    ssl_certfile="certs/server.crt",  # 启用 HTTPS
    ssl_keyfile="certs/server.key",
)
```

**生成自签证书**：
```bash
python xtquant_manager/utils/gen_cert.py --ip 192.168.1.100 --out certs/
```

**客户端连接 HTTPS**：
```python
client = XtQuantClient(config=ClientConfig(
    base_url="https://192.168.1.100:8888",
    api_token="my-secret-key",
    verify_ssl=False,   # 自签证书
    # 或: ca_cert="certs/ca.crt"  # 指定 CA 文件验证
))
```

### HMAC 请求签名（公网）

```python
# 服务端启用 HMAC
XtQuantServerConfig(enable_hmac=True, hmac_secret="very-long-secret-key")

# 客户端发送带签名请求
from xtquant_manager.security import generate_hmac_headers
headers = generate_hmac_headers("GET", "/api/v1/health", secret="very-long-secret-key")
```

---

## 9. 可观测性

### 健康检查响应示例

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "accounts": {
      "88888888": {
        "healthy": true,
        "connected": true,
        "reconnecting": false,
        "reconnect_attempts": 0
      }
    },
    "total": 1,
    "healthy": 1
  }
}
```

### 指标响应示例

```json
{
  "success": true,
  "data": {
    "88888888": {
      "total_calls": 42,
      "success_calls": 42,
      "error_calls": 0,
      "timeout_calls": 0,
      "error_rate": 0.0,
      "avg_latency_ms": 8.5,
      "p50_latency_ms": 5.0,
      "p95_latency_ms": 25.0,
      "last_error_time": null,
      "last_error_msg": "",
      "uptime_seconds": 3600.0,
      "ops": {
        "query_positions": {"total": 20, "success": 20, "error": 0, "timeout": 0},
        "get_full_tick":   {"total": 15, "success": 15, "error": 0, "timeout": 0},
        "order_stock":     {"total": 7,  "success": 7,  "error": 0, "timeout": 0}
      }
    }
  }
}
```

---

## 10. 与 miniQMT 集成

### 开关行为对比

| 场景 | `ENABLE_XTQUANT_MANAGER = False`（默认）| `ENABLE_XTQUANT_MANAGER = True` |
|------|----------------------------------------|--------------------------------|
| `position_manager` 交易接口 | 直接调用 `easy_qmt_trader` | 通过 `XtQuantClient` HTTP 调用 |
| `data_manager` 行情接口 | 直接调用 `xtquant.xtdata` | 通过 `XtDataAdapter` HTTP 调用 |
| `main.py` 启动 | 无额外服务 | 自动启动 HTTP 服务并注册账号 |
| QMT 依赖位置 | 本机（miniQMT 进程内） | XtQuantManager 服务进程内 |

### 注意事项

**成交回调**：`True` 模式下 `register_trade_callback` 为 no-op，`position_manager` 的 `_on_trade_callback` 不会被触发。此回调用于实时清理 `pending_orders`，不影响核心交易功能，但订单追踪可能有轻微延迟（下次持仓轮询时自动同步）。

**`data_manager` 行情验证**：`True` 模式下 `_verify_connection()` 调用 `get_full_tick`，若服务器无注册账号则返回空（仅打 warning），不影响功能。

---

## 11. 常见问题

**Q: 如何确认服务已正常启动？**
```bash
curl http://127.0.0.1:8888/api/v1/health
# 期望: {"success": true, "data": {"status": "ok", ...}}
```

**Q: 账号注册后显示 `connected: false`？**
- 检查 `qmt_path` 是否正确（应为 `userdata_mini` 目录）
- 确认 QMT 客户端已登录
- 查看日志中的 xttrader 错误信息
- 等待几秒后查询 `/accounts/{id}/status`，HealthMonitor 会自动重连

**Q: `position()` 返回空 DataFrame？**
- 确认账号已成功连接（`connected: true`）
- 确认账号下确实有持仓
- 检查 `/metrics/{id}` 中 `query_positions` 的 error_calls 计数

**Q: 如何在不重启 miniQMT 的情况下切换模式？**
目前需要重启 miniQMT 主程序。`config.ENABLE_XTQUANT_MANAGER` 在程序启动时读取一次，运行时修改不生效。

**Q: `get_market_data_ex` 返回数据格式与原始 xtquant 有何不同？**
原始 xtquant 返回 `dict[str, pd.DataFrame]`，XtQuantClient 返回 `dict[str, dict]`（DataFrame 已转为 dict-of-dicts）。`data_manager.py` 中的 `pd.DataFrame(stock_data)` 对两种格式均兼容。

**Q: 如何运行功能测试？**
```bash
# 确保 QMT 已启动，account_config.json 配置正确
python test/test_functional_xtquant_manager.py
```

---

*文档由 miniQMT 开发团队维护。如有问题请提交 Issue。*
