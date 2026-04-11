# 常见问题

## 止盈止损信号重复执行

**原因**：信号验证失败或未正确标记为已处理。

**解决**：

- 检查 `validate_trading_signal()` 和 `mark_signal_processed()` 调用链
- 查看日志中的信号验证详情
- 确认 `signal_timestamps` 机制正常工作

---

## 模拟交易持仓不更新

**原因**：未触发数据版本号更新。

**解决**：

```python
def simulate_buy_position(self, ...):
    # ... 执行模拟买入逻辑 ...
    self._increment_data_version()  # 必须调用
```

---

## QMT 连接断开

**检查**：

```python
# 检查连接状态
position_manager.qmt_trader.xt_trader.is_connected()

# 重新连接
position_manager.qmt_trader.connect()

# 检查路径配置
# config.py 中的 QMT_PATH 是否正确
```

---

## 持仓监控线程未运行

**排查**：

```python
# 1. 检查配置
config.ENABLE_POSITION_MONITOR  # 应为 True

# 2. 检查线程状态
import threading
print(threading.enumerate())

# 3. 查看日志
# 搜索 "启动持仓监控线程" 或 "持仓监控线程异常"
```

---

## 线程监控器未自动重启线程

**原因**：使用了错误的线程注册方式。

**正确做法**：

```python
# ✅ 正确: 使用 lambda 获取最新对象
monitor.register_thread(
    "持仓监控",
    lambda: position_manager.monitor_thread,
    restart_func,
)

# ❌ 错误: 直接传递线程对象
monitor.register_thread(
    "持仓监控",
    position_manager.monitor_thread,  # 重启后引用会变化
    restart_func,
)
```

---

## 系统退出时出现数据库错误

**原因**：关闭顺序不正确，Web 服务器在数据库关闭后仍在处理请求。

**解决**：确保 `main.py` 中的 `cleanup()` 函数按正确顺序关闭。验证时查看日志，应看到有序的关闭步骤，无 ERROR 日志。

---

## 网格交易未触发

**排查步骤**：

1. 确认 `ENABLE_GRID_TRADING = True`
2. 确认股票在股票池中（`stock_pool.json`）
3. 确认网格会话已启动（`GET /api/grid/sessions`）
4. 查看日志中的网格信号检测记录
5. 确认 `GRID_REQUIRE_PROFIT_TRIGGERED = True` 时，持仓已触发首次止盈
