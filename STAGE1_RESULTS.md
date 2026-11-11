# STAGE1_RESULTS.md — 阶段 1 实测结果汇总

> 2026-11-08 22:0x（GMT+8）
> 指令依据：用户「测试一下不就行了，把东西全部补齐，按照预想的那样」
> 原则：全部结论来自**真实运行**，失败如实记录并定位修复，不把推断写成成功。

## 1. 本轮实测总览

| # | 项目 | 结果 | 证据 |
|---|---|---|---|
| 1 | 网关补齐后冒烟测试 | **9/9 PASS** | `bridge/test_gateway.py` |
| 2 | ObjectPlan 质量风险（程序化参考图是否导致退化） | **未退化**，两轮各 15–17 部件 | `OBJECTPLAN_PROBE_RESULT.json` |
| 3 | P3 增量执行器 + Blender 真跑 | **Test A/B/C 全 PASS** | `runtime/outputs/incremental_test/report.json` |
| 4 | Blender 产物 | `.blend` 96 KB + 渲染图 168 KB | 见 §4 |
| 5 | P6 单向摄取（真实 ObjectPlan → aDSL AST） | **2/2 成功**，ID 往返保持 | `runtime/outputs/ingested/` |
| 6 | aDSL 回归 | **10 + 14 全绿** | `test_adsl_stable_ids.py` / `scripts/test_relations.py` |
| 7 | 密钥泄漏复核 | **干净**，仅 `runtime/.env`（已 gitignore） | grep 全目录 |

## 2. 网关补齐（9/9）

在 v1 基础上补了：上游 502 退避重试（3 次）、`stream:true` SSE 逐块透传、Responses 的 `tool_choice` 对象形态处理、`/images/edits` 兜底。

| 测试 | 结果 |
|---|---|
| `GET /v1/models` | PASS，返回 `deepseek-flash` / `deepseek-v4-pro` |
| chat 带 `gpt-5.4` + 全部 OpenAI 专属参数 | PASS 200，映射为 `deepseek-flash` |
| `tool_choice:required` + thinking 强制改写 | PASS 200（上游原本 400） |
| `POST /v1/responses` | PASS 200，`object:"response"` |
| `/v1/images/generations` → 本地 PNG `b64_json` | PASS，PNG 魔数校验 |
| `/v1/images/edits` → 本地 PNG | PASS |
| 不支持路径 404 守卫 | PASS |
| **`stream:true` SSE 透传** | PASS，`text/event-stream`，4 chunks |
| **Responses `tool_choice` 对象形态** | PASS 200 |

## 3. 过程中真实失败与修复（不藏）

| 失败 | 真实原因 | 修复 |
|---|---|---|
| 协议探针 6/7 项 `-1 transport_error` | 本机代理隧道 `502 Bad Gateway`，请求未到达服务端 | 探针加退避重试；不把传输失败记为 API 结论 |
| 网关测试 `responses tool_choice` 返回 422 | DeepSeek `/responses` 的 `tool_choice.type` 枚举是 `function`/`web_search`，**不接受 OpenAI 的 `required`** | 按路径删除 OpenAI 风格 variant，让服务端用默认值；9/9 通过 |
| ObjectPlan 探针 JSON 解析失败（两次） | `max_tokens=3000` 截断了 8–13K 字符输出 | 提到 6000；两次 `finish_reason=stop` 且严格解析成功。另加正则兜底统计，防止畸形 JSON 被当成成功 |
| 网关冒烟第 7 项断言恒为 `True` | 我自己写的假测试 | 改成真请求 `/audio/transcriptions` 验证 404 |
| 摄取器 `ModuleNotFoundError: numpy` | 用系统 Python 跑，aDSL core 依赖 numpy | 改用 `E:/3DUse/aDSL/venv/Scripts/python.exe`（正好印证环境隔离必要） |

## 4. ObjectPlan 质量探针结论（回答「测试一下不就行了」）

用 SceneCode `prompts/planner.md` 的输出契约，经网关调 `deepseek-flash`，对比两种输入。**两次独立运行**（模型输出有随机性）：

| 运行 | 纯文本 | 程序化参考图 + 文本 |
|---|---|---|
| 第一次 | 16 部件 / 7 种 part_type / cube 11 + cylinder 4 + custom 1 / 6 sub_parts / 26 modifiers | 17 部件 / 6 种 / cube 10 + cylinder 7 / 2 sub_parts / 23 modifiers |
| 第二次 | 15 部件 / cone 4 + cube 10 + torus 1 / 22 modifiers | 15 部件（含 piping_seam） |
| **退化判定** | **False** | **False** |

**结论**：程序化参考图**没有**让 ObjectPlan 退化成几个立方体，部件数与形状多样性和纯文本相当。风险已用实测排除。

**限定**：这是能力探针，不是完整 SceneCode 流水线复现——真实 `PlannerAgent` 还有 20 KB 完整 prompt 和 PlannerChecker 的 `check_and_fix` 多轮修复。不能据此宣称 PHASE 2 复现完成。

## 5. P3 增量执行器（本机 Blender 5.2.2 真跑）

`adsl_adapter/incremental_executor.py` 复用 aDSL 核心的 `asset_from_dict` 与 `core.export.export_glb._build_shape`（用 `importlib.import_module`，`import ... as` 会被包属性覆盖）。每个组件构建时打一个 **UUID build token**；未被重建的几何 token 保持不变，这比比较对象名更严格地证明「非目标几何没重新生成」。

`adsl_adapter/test_incremental_blender.py` 结果（17 个对象，编辑前后数量不变，component_ids 保持）：

| 测试 | 行为 | 验证 |
|---|---|---|
| A 组件级编辑（椅背降低 5cm） | 只删/重建 `backrest` 的 2 个对象 | 目标 token 改变，非目标违规 `[]` → PASS |
| B 关系级编辑 | 只移动 `seat` 的 2 个对象，不重建几何 | 目标 token **保持**、matrix 改变、非目标违规 `[]` → PASS |
| C 程序级对称编辑（扶手加宽） | 只重建 `armrest_left` + `armrest_right` | 两者 token 都改变，非目标违规 `[]` → PASS |

产物：`chair_incremental.blend`（96 KB）、`chair_after_edits.png`（168 KB，目视确认靠背/扶手/座面/四条腿可见）、`report.json`。

**限定**：这是**单把椅子的机制验证**，不是 PHASE 5 的完整客厅场景测试；Test B 目前是「移动组件」，尚未接入 `RelationAssembly` 的约束重求解（P4）。

## 6. P6 单向摄取（aDSL 主动适应 SceneCode）

`adsl_adapter/ingest_objectplan.py` 把两份**真实生成的** ObjectPlan 转成 aDSL AST：

- `component_id` 直接取 `PartPlan.name`（如 `back_cushion_1`、`leg_front_left`），不造 UUID；
- 各 15 个部件 → 16 个稳定 ID（含根），`asset_to_dict → asset_from_dict` 往返 ID 全保持；
- 产出 `scene_a2.json` / `scene_b2.json` + `.upstream.json`（上游 manifest：尺寸、材质、对称性、可动性、原始位姿）+ `.ingest_report.json`；
- `cone` / `torus` 如实记为 `unmapped_shapes`，**没有静默降级成 Cube** —— 真实几何应由 P1 的 `ProgramComponent` 承接 part-wise `code/<part>.py`，摄取器只建结构骨架。

## 7. 生成物清单

```
SceneCode_audit/
├─ STAGE1_RESULTS.md                    (本文)
├─ DEEPSEEK_PROBE_REPORT.md             (能力 + 协议实测)
├─ OBJECTPLAN_PROBE_RESULT.json         (脱敏统计)
├─ OBJECTPLAN_SAMPLE_A.json / _B.json   (真实 ObjectPlan)
├─ DEEPSEEK_PROBE_RESULT.json / DEEPSEEK_COMPAT_RESULT.json
├─ bridge/
│   ├─ gateway.py                      (本地兼容网关)
│   ├─ probe_deepseek.py / probe_compat.py / probe_objectplan.py
│   └─ test_gateway.py                 (9/9)
├─ adsl_adapter/
│   ├─ incremental_executor.py         (P3)
│   ├─ test_incremental_blender.py     (A/B/C)
│   └─ ingest_objectplan.py            (P6)
├─ tests/test_adsl_stable_ids.py       (10/10)
└─ runtime/outputs/
    ├─ incremental_test/  chair_incremental.blend, chair_after_edits.png, report.json
    ├─ ingested/          scene_a2.json, scene_b2.json, *.upstream.json, *.ingest_report.json
    └─ gateway_calls.jsonl             (脱敏)
```

## 8. 仍未完成（诚实标注，不宣称补齐）

| 项 | 状态 | 原因 |
|---|---|---|
| 独立环境（uv / Python 3.11 / Blender 4.4.3 / `uv sync`） | ⬜ 未安装 | 按用户确认的顺序，应在代码写好、要跑 SceneCode 真身时才装；本机网络不稳、`pydrake` Windows 未验证 |
| **PHASE 2 原生复现**（完整客厅、五阶段、`.blend`、多视角渲染、脱敏日志） | ⬜ 未执行 | 依赖独立环境 |
| P1 `ProgramComponent`（承接 part-wise `.py` 的参数 schema / 局部原点 / AABB / 依赖） | ⬜ 未实现 | 摄取器目前只建结构骨架 |
| P2 依赖图 + 脏标记（基于 `component_path()` 求闭包） | ⬜ 未实现 | P3 目前由调用方显式指定脏组件 |
| P4 `RelationAssembly` 局部重求解 | ⬜ 未实现 | Test B 目前只做位姿移动 |
| P5 aDSL ↔ Registry ↔ Blender 三方一致性校验 | ⬜ 未实现 | — |
| PHASE 5 完整客厅三项编辑测试 + 修改前后多视角对比 | ⬜ 未执行 | 依赖 P1–P5 + PHASE 2 场景 |
| `SCENECODE_REPRO_REPORT.md` | ⬜ 未写 | PHASE 2 未跑，不能写复现结论 |

**「全部补齐」目前补到的是阶段 1 的全部可验证部分**（网关、协议实测、风险实测、P3 机制、P6 摄取骨架），不是 PHASE 1–6 全部完成。按任务纪律，不把机制验证冒充完整复现。

## 9. 下一步优先级

1. **P1 ProgramComponent** —— 让摄取器挂上真实 part-wise 程序，AST 才有几何。
2. **P2 + P4** —— 脏标记闭包 + 关系局部重求解，让 Test B 从「手动移动」升级为「改约束后自动求解」。
3. 装独立环境 → PHASE 2 原生复现 → 用真实客厅跑 PHASE 5 三项编辑测试。

---

## 10. 追加（22:5x）：把「一坨」补成真正的精细家具

用户指出 `chair_after_edits.png` 就是几个方块拼的，与任务「不要把精细家具简化为几个立方体」冲突。**认账**：那是 P3 机制验证的占位几何，只用来证明增量执行，不代表最终质量。

### 补的做法：P1 ProgramComponent，不是给方块加倒角

| 新文件 | 作用 |
|---|---|
| `adsl_adapter/program_component.py` | 结构化组件：`component_id` / `program`（**程序引用名，不是代码字符串**）/ 带类型与范围的 `ParamSpec` schema / `values` / `local_origin` / `depends_on` / `upstream_refs` / `revisions`。纯 Python，无 bpy，可脱离 Blender 单测 |
| `adsl_adapter/part_programs.py` | 4 个参数化 Blender 构建器：`rounded_panel`（bevel + weighted normals）、`upholstered_cushion`（subdiv + 法线鼓起 + 曲线缝线 + 程序化织物微纹理）、`tapered_leg`（锥形木腿 + 金属脚套）、`piping_seam` |
| `adsl_adapter/run_fine_sofa.py` | 读真实 `scene_a2.upstream.json` 的 15 个部件 → 生成 ProgramComponent → 挂到 P3 执行器 → 布光/地面/相机 → 前后各 3 视角渲染 → 局部编辑 → 保存 `.blend` + `program_ast.json` |

`IncrementalBlenderExecutor` 加了 `build_fn` 回调与 `build_initial_from_specs()`，因此**复用 P3 的增量机制**，几何来源换成参数化程序，原来的 `test_incremental_blender.py` 不受影响。

### 视觉调优过程（都真跑、都看过图）

1. 首版：能看出沙发结构但**过曝**，布料被冲成白色、缝线看不见 → 降低世界光/三盏灯能量、加深布料与框架配色、`Standard` + 中高对比、加粗缝线。
2. 二版：层次清楚但布料偏浅 → 再压一档曝光、给布料加程序化 Noise→Bump 微纹理、压暗地面。
3. 定稿：蓝灰布料 / 深灰框架 / 木色底座 / 四条锥形腿 / 三块靠背软垫 / 三块座垫 / 圆角扶手 / 缝线均可辨。

### 最终结果（Blender 5.2.2, EEVEE）

```
component_count = 15      object_count_initial = 25      ids_preserved = True
test_a (靠背 -5cm)   PASS  violations=0  target_rebuilt=True   (只删/重建 2 个对象)
test_b (关系编辑)     PASS  violations=0  token_kept=True matrix_changed=True
test_c (扶手 +3cm)    PASS  violations=0  targets_rebuilt=True  (只重建左右扶手)
renders = 6 (before/after × front_45 / side / top_3q)
```

产物：`runtime/outputs/fine_sofa/fine_sofa.blend`、6 张 PNG、`program_ast.json`（结构化 AST，**不含任何代码字符串**）、`report.json`。前后渲染目视确认中间靠背变矮、扶手变宽，座垫/木底座/腿未变。

### 诚实限定（不能不说的部分）

- 这批 part programs 是**我手写的参数化参考实现**，用于证明「aDSL 组件能承载结构化参数化程序 + 增量编辑 + 精细几何」这条链路。
- 它们**不是 DeepSeek 生成的 part-wise Blender Python**。任务核心的 Agentic Coding（LLM 生成程序 → 执行 → 报错回灌修复）**仍未实现**，这是 P1 剩下的真正工作，需要接网关 + `execution-guided repair`。
- 视觉上仍有提升空间：没有真实织物贴图/法线、软垫表面偏光滑、地面偏亮；这是程序化参考实现，不是论文级资产质量。
- 仍是在**双人或单件家具**上验证，不是完整客厅场景；PHASE 2 复现、P2/P4/P5、独立环境依旧未做（见 §8）。

> **§10 已作废（2026-11-08 晚）**：用户指出「几何必须 Agent 生成，不是我自己写」。手写几何模块 `part_programs.py` 与组装脚本 `run_fine_sofa.py` 已物理删除，改为 `agentic_coder.py` / `agentic_pipeline.py` / `part_runner.py` / `run_agent_scene.py` / `critic_repair.py` 的 Agent 闭环。当时那批好看的渲染图仅作历史对比，**不代表系统路径**。现行结果见 `AGENTIC_SYSTEM_REPORT.md`。
