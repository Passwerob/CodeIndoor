# SCENECODE_ADSL_ARCHITECTURE.md — 架构定稿（方向修正版）

> 版本：v2，2026-11-08
> 修正依据：用户指令「把 aDSL 适应原本的 SceneCode，而不是 SceneCode 适应 aDSL」
> 前置文档：`SCENECODE_AUDIT.md`、`LOCAL_ENV_PROFILE.md`

---

## 1. 方向定稿

**aDSL 是唯一的主动适配方；SceneCode 保持原样，作为上游黑盒。**

具体含义（用可验证的条款写死，避免后面又滑回去）：

| 约束 | 含义 |
|---|---|
| C1 | **不修改 SceneCode 的任何架构/契约/业务源码**：`router.py`、`pipeline.py`、`part_constructor.py`、`image_generation.py`、`addon.py`、`base_stateful_agent.py` 全部只读 |
| C2 | 不要求 SceneCode 输出结构化参数、局部坐标、稳定 ID 或 CSG；它照常产出 `ObjectPlan.json`、`code/<part>.py`、`GLB/SDF`、`house_state.json` |
| C3 | aDSL 扩展自身去**原样承接**上述产物：ID 沿用 `PartPlan.name`，程序承接 part-wise `.py`，路径承接 `GeneratedGeometry` |
| C4 | LLM / 图像生成 / 协议差异一律在 **SceneCode 之外的桥接层**吸收 |
| C5 | 唯一允许的仓库内改动是打包元数据残留（`pyproject.toml` 的 `packages=["scenesmith"]`），不改任何业务语义 |

### 与审计版建议的差异（明确撤销）

| 审计版（v1）建议 | v2 处理 |
|---|---|
| 改 `router.py:1411 _prepare_code_object_inputs` 去掉参考图生成 | ❌ **撤销**。改为 §3 本地兼容网关接管 `images.generate` |
| 改 `router.py:1511/1552 generate_from_image → generate_from_text` | ❌ **撤销**。SceneCode 继续走 image 模式，网关返回程序化参考图 |
| 改 `fallback_backend` 配置 + 处理 `image_generator is None` | ❌ **撤销**。网关保证 `images.generate` 永不失败，fallback 不触发 |
| 「ObjectPlan → aDSL AST」隐含上游输出要被翻译/接管 | ✅ 改为「aDSL 摄取上游既有产物」，见 §4 |
| 在 SceneCode 里加 Provider Adapter 适配两套 LLM 栈 | ❌ **撤销**。统一在网关做协议翻译，见 §3 |

---

## 2. Source of Truth 冲突（必须正面解决）

任务原文要求：**aDSL 是持久化的语义化、结构化、可执行 Source of Truth**，且「Scene State Registry 必须与 aDSL 的稳定 ID 和组件层级对应，不允许维护两套互相矛盾的场景状态」。

但 C1/C2 要求 SceneCode 不改，它仍会维护自己的 `house_state.json`、`AssetRegistry`、`GeneratedGeometry`。**如果两边都活着，就是两套状态。**

### 解法：阶段隔离 + 单向投影，而不是双向同步

```
┌─ 生成期 Generation ────────────────────────────────────┐
│ SceneCode 原样运行（上游事实源）                        │
│ Brief → Planner/Designer/Critic → AssetItem → 5 策略    │
│      → ObjectPlan.json + code/<part>.py + GLB/SDF       │
│      → house_state.json                                 │
└──────────────────────┬─────────────────────────────────┘
                       │ 单向摄取（只读，不回写）
                       ▼
┌─ 摄取期 Ingestion ─────────────────────────────────────┐
│ aDSL 读上游产物 → 编译为 aDSL AST                      │
│  · component_id = PartPlan.name / code 文件名           │
│  · ProgramComponent 承接 part-wise Blender Python       │
│  · house_state.json 冻结为 upstream_snapshot.json       │
│    （immutable，带 sha256 + 时间戳 + lineage）           │
└──────────────────────┬─────────────────────────────────┘
                       │
                       ▼
┌─ 编辑期 Editing ───────────────────────────────────────┐
│ aDSL AST = 唯一活跃 Source of Truth                    │
│ Localized Edit → 脏标记 → 增量执行 → Render → Critic   │
│ SceneCode 侧状态不再更新、不再读取                      │
└─────────────────────────────────────────────────────────┘
```

**为什么这满足「不允许两套矛盾状态」**：SceneCode 的状态被降级为**只读历史快照**（`upstream_snapshot.json`），不是并行活跃状态。任意时刻只有一个可写 SoT：生成期是 SceneCode，编辑期是 aDSL。二者靠 `phase` 字段与 `lineage_id` 显式区分，不靠实时同步。

**代价（必须接受，写进复现报告）**：
- 进入编辑期后，SceneCode 原生的 `resume_from_path`、checkpoint 回滚、房间级 critique 不可用。
- 编辑期的 `Render → Visual Critic → Repair` 闭环要在 **aDSL 侧重新接**（渲染 aDSL 执行结果 → DeepSeek Flash 视觉评审 → 生成 aDSL 局部编辑指令），不能复用 SceneCode 的 Critic。
- 「重新生成整个房间」= 显式回到生成期 → 重新摄取 → 作废旧 AST 并记 lineage。不是无缝切换。

> 如果用户要求的是「SceneCode 与 aDSL 实时双向同步、同一份状态」，那就与 C1 直接冲突，需要重新拍板——**不能靠加 Adapter 掩盖**。

---

## 3. 核心机制：本地兼容网关（让 SceneCode 零改动）

这是「aDSL/桥接层适应 SceneCode」的关键，同时解决三件事：图像生成禁用、两套 LLM 栈协议差异、API key 隔离。

### 3.1 拓扑

```
SceneCode（原样，OPENAI_API_BASE=http://127.0.0.1:<port>/v1）
   │  · openai-agents (gpt-5.4)         → /responses 或 /chat/completions
   │  · AsyncOpenAI (gemini-3.1-pro)    → /chat/completions
   │  · image_generation.py             → /images/generations, /images/edits
   ▼
┌──────────────────────────────────────────────────────────┐
│ bridge/ 本地 OpenAI-compatible 网关                       │
│  ├─ 文本请求 → 参数翻译 → DeepSeek 官方 API               │
│  │    · 剥离 reasoning_effort / verbosity / service_tier │
│  │      / vision_detail 等 OpenAI 专属字段                │
│  │    · 模型名映射 → deepseek-flash                       │
│  │    · 保留 tools / tool_calls / JSON output             │
│  │    · thinking 模式下透传 reasoning_content             │
│  │    · 剥离 Gemini-3 thought_signature 注入             │
│  │  · 图像请求 → ProceduralReferenceGenerator（本地）    │
│  │    · 绝不调用 FLUX / SAM3D / TRELLIS / OpenAI 图像    │
│  │    · 用本地 Blender/PIL 生成程序化参考图               │
│  │  · key 只在网关进程内存中，不进日志/报告/仓库          │
│  │  · 记录脱敏调用日志（模型、token、耗时，无 key）       │
└──────────────────────┬───────────────────────────────────┘
                       ▼
              https://api.deepseek.com
```

### 3.2 为什么这样能「不改 SceneCode 就禁用图像生成」

审计已确认 `router.py:1394-1420` 会无条件调 `image_generator.generate_images(...)`，最终落到 `image_generation.py:190 client.images.generate(...)`（backend 默认 `openai`）。网关把 `/images/generations` 接住并返回一个**本地程序化生成的参考图**，SceneCode 侧的 `image_path` 依然有效、`generate_from_image()` 照常执行、`fallback_backend: "flux-klein"` 永远不会触发。

参考图的生成策略（按优先级，均本地、无需 GPU 模型）：
1. 用 aDSL 已有原语 + 本地 Blender 渲染一张该物体的**中性线稿/体块示意图**（最贴合"代码驱动"的原则）；
2. 退化为 PIL 绘制的纯色/轮廓占位图 + 物体名称与尺寸标注；
3. 无论哪种，都把 `AssetItem.description` / `dimensions` 编码进图像元数据，供 Planner 读取。

`[待运行验证]`：**没有真实参考图时，PlannerAgent 生成的 ObjectPlan 部件丰富度是否下降**（是否退化成几个 cube）。这是 PHASE 2 最需要实测的风险，不能预设成功。

### 3.3 网关必须处理的协议差异（来自审计 §3）

> **2026-11-08 21:4x 已按真实实测修正**，详见 `DEEPSEEK_PROBE_REPORT.md`。初稿里几条「必须剥离/清理」的假设被推翻，下表以服务端真实响应为准。

| 来源 | 参数 / 行为 | 实测 | 网关处理 |
|---|---|---|---|
| `base_furniture_agent.yaml:296-316` | `reasoning_effort`、`verbosity` | **200，被忽略** | 默认透传；`STRICT_PARAMS=1` 才剥离 |
| `configs/config.yaml:11`、`:289` | `service_tier` | **200，被忽略** | 同上 |
| `base_furniture_agent.yaml:292` | `vision_detail: high` | **200，被忽略** | 同上 |
| `llm_client.py:193-234` | Gemini-3 `thought_signature` 注入 | **200，被忽略** | 不必强制清理 |
| `llm_client.py:265-277` | `temperature` + thinking | **200**（仅无效） | 不必剥离 |
| `llm_client.py:265-277` | `tool_choice:"required"` + thinking | **400** `Thinking mode does not support this tool_choice` | **唯一强制改写**：降为 `"auto"` |
| `llm_client.py:265-277` | `max_completion_tokens` 别名 | **200** | 透传 |
| `openai-agents>=0.6.4` | Responses API `POST /responses` | **200**，标准 `object:"response"` | **直接透传**，无需转译成 chat/completions |
| `OPENAI_TRACING_KEY` | OpenAI Agents tracing | — | 关闭 tracing，或网关返回空成功，不发外部请求 |
| `image_generation.py:141-146` | 与文本 LLM 共用 `OPENAI_API_KEY` | — | 网关按路径分流，key 只对文本请求生效 |
| `image_generation.py:1035` | 取 `response.data[0].b64_json` | — | `/images/*` 返回 `{"data":[{"b64_json":…}]}` 本地程序化 PNG |

**结论**：网关比初稿设想简单得多——文本两条路径基本是**透传 + 模型名映射**，唯一必须改写的是 `tool_choice:"required"` 与 thinking 的组合。已实现并 7/7 冒烟通过。

### 3.4 网关之外的注入点（备用）

若 `openai-agents` 的某些调用绕过 `OPENAI_API_BASE`（例如 SDK 内部硬编码），在 `bridge/entrypoint.py` 里用 `sys.modules` 注入或工厂函数替换来兜底，**仍然不改 SceneCode 仓库源码**。所有注入只在独立启动脚本里发生。

---

## 4. aDSL 侧改造路线（这是「修改到 aDSL」的主体）

### 4.1 已完成（本轮，含测试）

| 能力 | 实现 | 验证 |
|---|---|---|
| 稳定 Component ID | `Asset.component_id`（`kw_only` 追加末尾，不破坏任何现有位置参数构造）；`attach_part` 自动把 **part key** 绑定为 ID；`copy()` 保留 | 10 项新测试全绿 |
| ID 查询/依赖定位 | `component_ids()` / `find_component()` / `component_path()` / `effective_component_id`，重复 ID 早报错 | 同上 |
| 双向序列化 | `asset_to_dict` 增加 `component_id`（旧 reader 忽略即可）；核心库新增 `asset_from_dict`；`Asset.from_dict()` | 同上 |
| 向后兼容 | 旧 `outputs/*.json`（无 component_id）可还原并派生稳定 ID | 同上 |
| 回归 | `test_adsl_direct.py` 通过；`scripts/test_relations.py` 14 项 OK | 同上 |

**这一步直接服务 C3**：ID 不是 aDSL 自造的 UUID，而是 SceneCode 的 `PartPlan.name` / `code/<part>.py` 文件名。上游一个字都不用改。

### 4.2 待实现（按顺序，每项都要有测试）

| # | 能力 | 要解决的 PHASE 5 验收项 |
|---|---|---|
| P1 | `ProgramComponent`：aDSL 新组件类型，承接 SceneCode 的 part-wise Blender Python（含 `params` schema、局部原点、输出 AABB、源文件路径） | Program Persistence |
| P2 | 依赖图 + 脏标记：基于 `component_path()` 记录参数/空间依赖，编辑时求受影响闭包 | Localized Update |
| P3 | **增量执行器**：改 `export_glb` / `blender_worker`，支持按 `component_id` 删除并重建指定子树，其余 `bpy` 对象保持句柄不动（替代 `clear_scene=True`） | Localized Update / Semantic IDs |
| P4 | 关系层局部重解：`RelationAssembly.compile()` 目前恒返回全新 scene，需支持按稳定 ID 局部重求解 | Relation Preservation / Test B |
| P5 | 场景状态层：`aDSL AST ↔ Registry ↔ Blender` 三方一致性校验（以 `component_id` 为主键） | State Consistency |
| P6 | 摄取器：读 `ObjectPlan.json` + `code/<part>.py` + `house_state.json` → aDSL AST + `upstream_snapshot.json` | Program Persistence / SoT |

> P3 是硬骨头。`SCENECODE_AUDIT.md` §8.2 已说明：现有 Executor 是 `clear_scene=True` + `runpy.run_path` 全量重建。不改它，Test A/B/C 里「非目标几何不重新生成」无法通过。这是**aDSL 核心执行模型的改造**，属于用户本轮确认的「需要修改到 aDSL」，与 C1 不冲突。

### 4.3 articulated 能力保留

aDSL 有独立 `Joint` / `attach_joint` 运动学树（`core/joints.py`），与 `_children` 几何树互不干扰。SceneCode 的 `is_movable` + `URDFPipeline` 产物可以映射到 `Joint`，**不会因引入 aDSL 而丢失 articulated object 能力**。这是审计中少数的好消息，摄取器需显式处理 `URDF/SDF → Joint` 的映射。

---

## 5. 目标执行链（修正版）

```
Natural Language Brief
   ↓  SceneCode Planner/Designer/Critic（原样，经网关走 DeepSeek）
AssetItem / AssetRequest
   ↓  AssetRouter 5 策略（原样，参考图由网关本地生成）
ObjectPlan.json + code/<part>.py + GLB/SDF + house_state.json
   ↓  ── 单向摄取（aDSL 主动适应，不回写）──
aDSL AST / Component Tree  ★唯一 SoT（编辑期）
   ↓  ProgramComponent 编译 + aDSL 增量执行器
Blender Python（执行产物，不是唯一可编辑表示）
   ↓  Blender Scene（按 component_id 管理对象）
Render → DeepSeek Flash 视觉评审
   ↓  Localized aDSL Edit（只改目标组件参数/关系/程序）
Incremental Re-execution（只重建脏组件）
   ↓
更新后的 .blend + aDSL AST + Registry（三者以 component_id 对齐）
```

---

## 6. 环境适配摘要（详见 `LOCAL_ENV_PROFILE.md`）

| 约束 | 应对 |
|---|---|
| WSL2 被禁 + `wsl.exe` 黑名单 | Windows 原生支路；不绕过安全策略 |
| 无 Python 3.11 | `uv python install 3.11` 托管，独立 venv，**不碰现有 3.13 / aDSL venv** |
| Blender 5.2.2 / 4.4.3 / `bpy 4.5.4` 三方冲突 | 三者隔离：aDSL 用 5.2.2；SceneCode 用独立 4.4.3 Windows portable；`bpy` 仅作 Python 模块依赖 |
| GPU 16 GB | 纯代码几何 + EEVEE 渲染；禁用所有生成式/检索式后端（SAM3D/FLUX/CLIP/HSSD） |
| C 盘剩 24 GB | 全部 venv/缓存/Blender/输出落 E 盘，显式设置 `UV_CACHE_DIR` 等 |
| pydrake Windows | `[待验证]`；若不可用，明确裁掉 SDF 仿真导出并记为「因环境未复现」 |
| API key | 只在网关进程内存 + `.env`（已 gitignore），不写入任何代码/日志/报告 |

---

## 7. 取舍清单（需要用户知晓，不藏在实现里）

1. **aDSL 不是与 SceneCode 实时同步的镜像**，而是编辑期的唯一 SoT；SceneCode 状态冻结为只读上游快照。
2. **进入编辑期后放弃 SceneCode 原生回滚/Critic**，视觉评审闭环在 aDSL 侧重接。
3. **参考图是程序化生成的，不是真实图像**；这可能影响 ObjectPlan 部件丰富度，`[待运行验证]`，不能预设与论文同等质量。
4. **SDF/Drake 仿真导出在 Windows 上可能不可用**，纯代码几何 + `.blend` 优先。
5. **P3 增量执行器要改 aDSL 核心执行模型**，与任务里「不大规模重写现有代码」的约束存在张力；本轮按用户确认的「需要修改到 aDSL」执行，但会保持改动局部化 + 全量回归测试。
6. 上述任何未实测项都不会写成「已完成」。

---

## 8. 当前状态（诚实标注）

| 项 | 状态 |
|---|---|
| PHASE 0 审计 | ✅ 完成（`SCENECODE_AUDIT.md`） |
| 本机环境画像 | ✅ 完成（`LOCAL_ENV_PROFILE.md`） |
| 架构方向定稿 | ✅ 本文档 |
| aDSL 稳定 ID + 双向序列化 + 测试 | ✅ 已落地并跑通（10 新 + 14 既有回归） |
| 映射表 | ✅ `SCENECODE_ADSL_MAPPING.md` |
| 本地兼容网关 | ✅ 已实现并实跑 9/9（`bridge/gateway.py`，含 SSE / 重试 / Responses 形态） |
| DeepSeek `/models` 查询与协议实测 | ✅ 完成（8 能力 + 7 边界，见 `DEEPSEEK_PROBE_REPORT.md`） |
| 程序化参考图是否导致 ObjectPlan 退化 | ✅ 实测排除（两轮 15–17 部件，均未退化） |
| P3 增量执行器 | ✅ 已实现，本机 Blender 5.2.2 真跑 Test A/B/C 全 PASS，产出 `.blend` + 渲染图 |
| P6 单向摄取（ObjectPlan → aDSL AST） | ✅ 结构骨架已实现，2 份真实 ObjectPlan 摄取成功、ID 往返保持 |
| P1 ProgramComponent / 单件几何 | ⚠️ **方向已修正**：裸 `bpy` 生成路径（`agentic_coder.py` / `agentic_pipeline.py`）标记 DEPRECATED，仅作运行证据保留。根本原因见 `ROOT_CAUSE_ANALYSIS.md`：绕开 aDSL 参数化工厂 + 非阻断的旁观 Critic。现改为 **LLM 只出结构化计划 → FineScene `FACTORIES`/现成 aDSL 资产确定性编译 → 逐件/整场 `review_images` 硬门禁** |
| **SceneCode 场景组织 × aDSL 单件生成** | ✅ **已打通并真跑**：14 对象五阶段布局（校验 0 违规 / Critic 2 轮 / 最优版本保留）→ 位姿盖章到 13 个现成 aDSL 对象 + 1 个 `build_desk` 工厂件 → 14 对象 / 137 独立部件 / 4 视角室内渲染 / `.blend`。见 `SCENECODE_LAYOUT_ADSL_REPORT.md` |
| 局部编辑（aDSL 作为编辑期 SoT） | ✅ **已验证**：按整场 QA 的 blocking finding 只改 2 把扶手椅 yaw，其余 12 个组件 AST SHA-256 全不变；重渲染后 `needs_revision → passed / accepted=True`。FineScene 内置 `isolated_part_edit` / `grouped_object_edit` 亦全绿 |
| P2 / P4 / P5 | 🟡 部分：哈希级非目标保持已验证；脏标记闭包、关系约束自动重求解、三方状态自动对账仍未实现 |
| 独立环境（uv / Python 3.11 / Blender 4.4.3） | ⬜ 未安装（按用户确认的顺序，留到要跑 SceneCode 真身时） |
| PHASE 2 原生复现 | ⬜ 未执行（本轮是「按 SceneCode 真实契约复刻规划层」，非原生流水线跑通） |
| 完整客厅 `.blend` / 多视角渲染 | ✅ 已产出（`runtime/outputs/living_room/interior{,_edited}/`），但几何是 FineScene 现成简化参数化资产，非 PBR 精修 |
| PHASE 5 完整客厅三项编辑测试 | 🟡 已验证组件级局部编辑（A 类）；关系级/程序级在完整客厅上仍未单独跑 |

> 详见 `STAGE1_RESULTS.md`。**机制验证不等于完整复现**，不混淆。

---

## 9. 实施顺序修正（用户确认，2026-11-08 21:31）

用户指出：**独立环境应该在代码写好、进入测试阶段时才安装**。采纳，并据此修正顺序（替代 §8 里把"装不装环境"列为待确认项的做法）。

### 阶段 1 — 不依赖独立环境（现在就能做，用现有 aDSL venv / Python 3.13.4）

1. **DeepSeek `/models` 查询 + 协议实测**（JSON 输出 / tool calls / 长文本 / 图像输入）——这一项要**提前**：网关必须按账户真实可用的模型与端点形态来写，不能靠文档猜测。
2. `bridge/` 本地兼容网关（纯 Python，独立进程）。
3. P1–P6 的 aDSL 改造（含 **P3 增量执行器**）+ 单元测试。
4. P3 的 Blender 执行验证**直接用本机已有的 Blender 5.2.2**，不必等 4.4.3 —— aDSL 侧执行器只依赖 `bpy` 基本建模 API。

### 阶段 2 — 装独立环境（仅在此刻，且只在要跑 SceneCode 真身时）

`uv` → 托管 Python 3.11 → 独立 `.venv-scenecode` → Blender 4.4.3 Windows portable → `uv sync`。

### 阶段 3 — PHASE 2 复现

跑 SceneCode 原样流水线，产出 `.blend`、多视角渲染、脱敏日志、ObjectPlan、部件程序、状态文件。

### 阶段 4 — 摄取 + 集成测试 → PHASE 5 三项编辑测试

**理由**：本机网络不稳（codeload 限速 ~25 KB/s、git 502）、`uv sync` 含多个需编译扩展、`pydrake` 在 Windows 的可用性未验证。过早安装只会留下一个半装的环境，还可能因为依赖解析失败卡住主线。

**区分两件事**：`runtime/` 目录骨架、`.env.template`、`env.sh`、`.gitignore` 属于**配置契约**（写代码时就要对齐路径），提前生成是对的；它们不是"安装环境"，与阶段 2 不冲突。

### SoT 方案说明（用户问询，2026-11-08 21:31）

「阶段隔离 / 单向投影」与「实时双向同步」的区别，用 Test A（椅背 −5cm）说明：

| | 阶段隔离（推荐） | 实时双向同步 |
|---|---|---|
| 任意时刻可写状态 | **1 份** | 2 份，必须时刻一致 |
| 编辑后是否回写上游 | 不回写，快照只读 | 必须同步改 `ObjectPlan.json`、`code/backrest.py`、`house_state.json`、GLB/SDF 等 4+ 处 |
| 是否要改 SceneCode | 不用 | **要**（它没有组件级参数/稳定 ID/局部坐标，必须扩状态结构 + 冲突处理） |
| 翻车方式 | 重新生成整个房间需显式回到生成期 | 任一处漏同步，`resume_from_path` / critique 回滚就会把旧椅背读回来覆盖编辑 |
| 与「SceneCode 零改动」约束 | ✅ 兼容 | ❌ 直接冲突 |

阶段隔离额外好处：冻结的 `upstream_snapshot.json` 正好是 PHASE 5「改动前后对比 / 非目标组件 ID 不变」的现成基线。

**用户已确认（2026-11-08 21:35）：采用阶段隔离 / 单向投影。** C1（SceneCode 业务源码零改动）保持不变；实时双向同步方案作废，不再作为备选。所有后续实现以「生成期 SceneCode 事实源 → 摄取期冻结快照 → 编辑期 aDSL 唯一 SoT」为准。
