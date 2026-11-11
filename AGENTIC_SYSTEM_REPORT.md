# AGENTIC_SYSTEM_REPORT.md — 改为 Agent 系统后的真实运行报告

> 2026-11-08 23:2x–00:1x
> 用户指令：「怎么又是你自己做的……我要的是 agent 系统做」「aDSL 的表征质量很好，不需要你去验证」
>
> **重要后续**：用户指出最终渲染质量极差、Critic 形同虚设、FineScene 单资产明显更好。根因诊断见 `ROOT_CAUSE_ANALYSIS.md`——本文记录的「Agent 生成 bpy 代码」路径被判定为**错误抽象层**，已标记废弃，不再作为系统方向。本文保留为真实运行证据。

## 1. 认错与改动

上一轮我手写了 `part_programs.py` / `run_fine_sofa.py`，虽然渲染好看，但那是**我写的建模脚本**，不是 Agentic Coding。已物理删除这两个文件。aDSL 核心表征（`Asset` / `component_id` / `asset_to_dict`）**没有改动**，只作为承接层使用，也没有再做「验证 aDSL」的 demo。

现在的几何**全部由 DeepSeek 生成**：

```
ObjectPlan（上游，未改）
  → Constructor Agent 生成该部件的 Blender Python
  → 静态契约检查（AST：build_part 签名、import 白名单、禁用网络/文件/清场景）
  → Blender 沙箱单独执行该程序（真实建模、校验顶点数）
  → 失败：错误 + 完整代码回灌 Agent，重写整个程序（execution-guided repair）
  → 通过：源文件注册进 aDSL ProgramComponent（文件引用 + sha256，绝不塞代码字符串）
  → Blender 组装全部 Agent 程序 → 多视角渲染
  → Visual Critic（deepseek-flash 读渲染图）→ 意见回灌 → 局部重生成 → 验收/回滚
```

新增模块（均在 `adsl_adapter/`）：

| 文件 | 职责 |
|---|---|
| `agentic_coder.py` | Constructor Agent 调用、AST 契约检查、Blender 沙箱执行、错误回灌修复、LLM 传输退避重试 |
| `part_runner.py` | Blender 内单程序沙箱，执行 `build_part`、打 `adsl_component_id`、校验网格顶点 |
| `agentic_pipeline.py` | 编排 + 对称组共享程序 + 不可变 baseline + aDSL ProgramComponent AST + Visual Critic |
| `run_agent_scene.py` | Blender 内只执行 Agent 生成的程序、布光、前后各 3 视角、局部重执行验证 |
| `critic_repair.py` | Critic 回灌重生成、成对共享程序、接受/回滚、baseline 恢复 |
| `program_component.py` | 结构化组件（加 `source_path` / `source_sha256` 溯源），纯数据无 bpy |

## 2. 最终一次运行（干净、可复现）

```
parts_total=15  parts_accepted=15  parts_failed=[]
total_agent_attempts=11   repair_attempts=0   elapsed=173.2s
immutable baseline = 15 programs
Blender 5.2.2 / EEVEE, 15 个 Agent 程序生成场景
Visual Critic score = 4
```

11 次调用而非 15 次：左右扶手共享一份程序、四条腿共享一份程序（靠各自 `params["origin"]` 定位），保证对称且省调用。

Critic 指出的问题（真实、不粉饰）：几何扭曲、座位区混入圆形茶几/环、座垫悬浮重叠、扶手变形不对称、框架与腿 placement 混乱。**这是 LLM 生成几何的质量波动，不是链路故障。**

## 3. 多轮运行中真实观察到的事实

| 观察 | 数据 |
|---|---|
| Agent 一次通过率 | 最好 15/15；最差 10/15（受生成随机性 + 门禁影响） |
| Critic 分数波动 | **3–6 分**（同一套系统、同一 brief） |
| 执行引导修复**确实触发过** | 某轮两条腿各 2 次尝试；另一轮 8 次修复；`use_auto_smooth` 被 Blender 5.2 移除、`bmesh.faces.new` 重复建面都被沙箱抓到并回灌 |
| LLM 传输故障 | `RemoteDisconnected` 导致过部件丢失 → 已加 3 次退避重试 |
| Critic 回灌重生成 | 跑通，且出现 6→5、6→4 等**退步** |
| 验收回滚 | 分数下降时自动恢复不可变 baseline（真实触发多次） |
| 对称共享程序 | 独立生成左右会产出不同造型 → 改为共享；该修复同时减少了 4 次 LLM 调用 |
| 基准漂移 bug | 多次运行以「当时状态」为备份导致回滚目标漂移 → 改为 `generated_baseline/` 不可变基线 |

**一次明确的自我纠错**：我加过「几何 AABB 尺寸硬门禁」，本意挡住跑偏的巨大椭圆盘，结果误杀倒角/曲面薄部件，把通过率从 15/15 压到 10/15、Critic 掉到 3。已将其改为**非阻断告警**，并在代码里写明原因。随后恢复 15/15。这一段是过度迭代，如实记录。

## 4. 闭环能力清单（都真实跑过）

- ✅ LLM 生成 part-wise Blender Python（无手写几何）
- ✅ AST 静态契约 + import/API 白名单
- ✅ Blender 沙箱执行 + 顶点校验
- ✅ 执行错误回灌重写（`execution-guided repair`）
- ✅ 结构化 ProgramComponent 承接（源文件引用 + sha256，AST 不含代码字符串）
- ✅ 稳定 `component_id` 与局部重执行（某轮报告：只删/重建 2 个对象，非目标违规 0）
- ✅ 多视角渲染 + Visual Critic（图像输入）
- ✅ Critic 回灌 → 局部重生成 → 分数验收 → 退回步 → baseline 回滚
- ✅ 对称组共享程序、不可变 baseline
- ✅ 脱敏调用日志（网关侧）

## 5. 仍未解决 / 待改进（诚实）

1. **生成质量不稳定**：3–6 分波动，Agent 会凭空生成圆桌/环、悬浮线框、扭曲扶手。需要更强的生成契约、几何合理性提示、Planner/Checker 多轮、以及「保留历史最优版本」机制（现在 baseline 是最近一次运行，不是最优运行）。
2. **布局问题属于 Planner 层**：brief 是 two-seat，但上游 ObjectPlan 给了 3 个 seat_cushion / 3 个 back_cushion。Critic 反复指出「三座不是两座」。按阶段隔离 SoT，这应回到**生成期重规划**，不是编辑期局部改扶手能解决的；当前 `critic_repair.py` 只把它记为 `layout_issues_deferred_to_planner`，**没有实现 Planner 回灌**。
3. **P2 脏标记闭包 / P4 RelationAssembly 局部重解 / P5 三方一致性** 仍未实现；局部编辑目前由调用方显式指定脏组件。
4. **独立环境（uv / Python 3.11 / Blender 4.4.3）、PHASE 2 原生 SceneCode 复现、完整客厅 PHASE 5** 依旧未做。当前是本机 Blender 5.2.2 + 网关下的独立验证，不是 SceneCode 原样流水线跑通。
5. `generated/` 目录留有多轮 `_attempt*.py` 实验文件；`generated_baseline/` 是权威版本。

## 6. 交付物

```
SceneCode_audit/
├─ AGENTIC_SYSTEM_REPORT.md      (本文)
├─ AGENTIC_RESULTS.json          (最终运行：15/15, 11 次调用, Critic, 每部件 log)
├─ CRITIC_REPAIR.json            (Critic 回灌 / 验收 / 回滚记录)
├─ adsl_adapter/agentic_*.py, part_runner.py, run_agent_scene.py, critic_repair.py
└─ runtime/outputs/
    ├─ agentic/generated/*.py             (Agent 生成的 15 份程序)
    ├─ agentic/generated_baseline/*.py   (不可变基线 15 份)
    ├─ agentic/part_specs.json / program_ast.json
    └─ agent_scene/  report.json, before_*.png, after_*.png, agent_scene.blend
```

**本报告不含任何真实 API Key、prompt 正文或密钥。** key 仅存在于 `runtime/.env`（已 gitignore）。
