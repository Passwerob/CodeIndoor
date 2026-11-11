# CodeIndoor — SceneCode × ADSL 室内场景闭环布局

本项目是对 [SceneCode](https://github.com/) 室内场景生成流程的审计与改造（ADSL adapter），核心目标是实现**真正的闭环**：由 agent 决定家具位姿并自修复，而不是人工逐个改坐标。

## 这个项目做了什么

- 在 SceneCode 的 5 段式契约（floor_plan → furniture → wall_mounted → ceiling_mounted → manipuland）之上，增加一套**可被测试约束**的派生逻辑与门禁。
- 关键修复：**靠墙家具（书架、电视柜等）朝向由房间几何派生**——背板永远朝墙、开口朝房间，杜绝"书架放反"。
  - `adsl_adapter/layout_finalize.py` 在写出权威布局时按墙内法线重算 yaw，并在写出前用 `validate_layout` 门禁拦截反装。
  - `adsl_adapter/build_asset_scene.py` 消费权威布局并对真实房间几何做兜底重算。
  - `tests/test_back_to_wall.py` 锁死该行为（书架→−90°、电视柜保持 180°、反装被断言捕获）。

## 目录结构

| 目录 | 说明 |
| --- | --- |
| `adsl_adapter/` | 核心 agent 流程：布局规划、派生、门禁、Blender 构建 |
| `bridge/` | 本地 LLM 网关与兼容性探针 |
| `tests/` | 回归测试 |
| `docs/` | 项目总览与场景说明（含中文《项目总览》） |
| `sources/` | SceneCode 上游基座代码 |
| `env/prepare_local.py` | 本地环境初始化脚本（不写任何密钥） |

> `runtime/`（生成产物、打包 blend、密钥）与 `cache_tmp/` 默认被 `.gitignore` 排除，不计入版本库。

## 快速开始

```bash
# 1. 准备本地环境
python env/prepare_local.py

# 2. 配置密钥（从 .env.example 复制，填入你自己的 DeepSeek key）
cp .env.example runtime/.env
#   然后编辑 runtime/.env

# 3. 运行完整流水线（详见 docs/项目总览.md）
python adsl_adapter/layout_finalize.py
#   再在 Blender 内执行 build_asset_scene.py 生成场景
```

## 文档

- `docs/项目总览.md` — 项目目的、闭环方法论、模块架构、复现步骤、已知限制。
- 根目录的 `*_AUDIT.md` / `*_REPORT.md` / `*_ANALYSIS.md` 记录了完整的审计与改造过程。

## License

待补充。
