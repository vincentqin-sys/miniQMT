# 嵌入 miniQMT

不单独启动进程，由 miniQMT `main.py` 自动启动并管理 XtQuantManager。

## 修改 config.py

```python
ENABLE_XTQUANT_MANAGER = True        # 开启
XTQUANT_MANAGER_URL = "http://127.0.0.1:8888"
XTQUANT_MANAGER_TOKEN = ""           # 可选
```

## 工作原理

启动 `python main.py` 后，系统自动：

1. 在后台线程启动 HTTP 服务（127.0.0.1:8888）
2. 从 `account_config.json` 注册账号
3. 所有 xtquant 调用透明路由到 HTTP

```
ENABLE_XTQUANT_MANAGER = False   →   直接调用 easy_qmt_trader / xtquant.xtdata
ENABLE_XTQUANT_MANAGER = True    →   通过 XtQuantClient / XtDataAdapter HTTP 代理
```

!!! note "零代码改动"
    切换此开关后，miniQMT 业务代码（strategy.py、position_manager.py 等）无需任何修改，接口行为与直连模式完全一致。

## 已知差异

| 功能 | `= False`（直连） | `= True`（HTTP 代理） |
|------|------|------|
| `register_trade_callback` | 正常触发 | no-op（回调不触发） |
| `_on_trade_callback` | 实时触发 | 不触发 |
| `pending_orders` 同步 | 实时 | 下次持仓轮询（约 3 秒）自动同步 |

核心交易功能不受影响。
