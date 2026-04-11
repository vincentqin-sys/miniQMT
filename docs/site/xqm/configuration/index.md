# 配置指南

XtQuantManager 通过 `xtquant_manager_config.json`（放项目根目录）进行配置，也支持环境变量和 Python 代码配置。

## 配置文件发现规则

启动时按以下顺序查找配置文件，找到即使用：

1. 命令行参数 `--config /path/to/config.json`
2. 环境变量 `XQM_CONFIG=/path/to/config.json`
3. 当前工作目录 `./xtquant_manager_config.json`
4. 项目根目录（`xtquant_manager/` 的父目录）

---

## 选择场景

| 你的情况 | 推荐场景 |
|---------|---------|
| 本机开发/测试，单个 QMT 账号 | [单账号](single-account.md) |
| 本机运行两个 QMT 账号 | [双账号](dual-account.md) |
| 交易机 + 分析机，局域网部署 | [局域网共享](lan.md) |
| miniQMT 主程序自动管理 | [嵌入 miniQMT](embedded.md) |
| 无人值守，长期运行 | [无人值守](unattended.md) |
| 查询所有配置参数 | [全量参数参考](reference.md) |
