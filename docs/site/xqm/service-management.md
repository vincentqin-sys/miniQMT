# 服务管理脚本

[`xtquant_manager/xqm_manager.bat`](https://github.com/weihong-su/miniQMT/blob/main/xtquant_manager/xqm_manager.bat) — Windows 批处理管理工具，支持交互式菜单和命令行两种模式。

## 命令行模式

```bat
xqm_manager.bat start    # 启动服务（轮询就绪，最多等待 15s）
xqm_manager.bat stop     # 停止服务
xqm_manager.bat restart  # 重启服务
xqm_manager.bat status   # 显示状态 + 健康检查 + 最近 10 行日志
xqm_manager.bat ui       # 浏览器打开 test_ui_a.html
xqm_manager.bat logs     # 实时追踪日志（PowerShell tail，Ctrl+C 退出）
```

## 交互式菜单

双击 `xqm_manager.bat` 进入菜单界面，支持 1-7 功能选项。

## 启动流程

```
xqm_manager.bat start
    ↓
检查端口 8888 是否占用
    ↓ 已占用
    检查健康接口 → 健康：跳过启动（服务已运行）
                → 不健康：KillByPort，继续启动
    ↓ 未占用
启动后台窗口（最小化），重定向日志到 logs/xqm_manager.log
    ↓
每秒轮询 /api/v1/health，最多等待 15s
    ↓ 健康
读取 PID，保存到 .xqm_manager.pid
输出 [OK] 信息
```

## 测试界面

位于 `xtquant_manager/test_ui/`：

| 文件 | 风格 | 特色功能 |
|------|------|---------|
| `test_ui_a.html` | 终端/功能 | JSON 高亮、历史记录 50 条、cURL 导出 |
| `test_ui_b.html` | 可视化 | 持仓表格、资产卡片、指标进度条、自动刷新 |

!!! note "Git Bash 兼容性"
    `xqm_manager.bat` 请在 `cmd.exe` 或 PowerShell 中运行。在 Git Bash 中 `timeout` 命令语法不同，会报错，但服务本身功能不受影响。
