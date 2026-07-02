# benchgate TODO

项目级待办（Agent / 维护者）。已完成里程碑见 `docs/RFC_LOCAL_SIM_MODEL_PROVIDER.md`。

## Backlog

- [ ] **`watch loop`** — 持续监听 `design/` + `models/blocks.yaml` / `blocks/*`，变更时自动跑 `watch_once` 流水线（pipeline → mapping → sim → gate）；CLI `benchgate watch loop`，Agent 工具 `watch_loop`；可选 `--interval`、debounce、Git hook 集成
