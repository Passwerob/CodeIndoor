# SCENECODE_AUDIT.md

> PHASE 0 — Repository Audit
> 审计对象：`wangpuyi/SceneCode`（官方仓库，main 分支，最后推送 2026-06-18，64 stars）
> 论文：arXiv:2605.19587《SceneCode: Executable World Programs for Editable Indoor Scenes with Articulated Objects》，2026-05-19 提交
> 审计日期：2026-11-08
> 本地快照：`E:/3DUse/SceneCode_audit/sources/`（按仓库相对路径保存）

---

## 0. 审计方法与边界（请先读这一节）

**本报告是静态源码审计，不是运行验证。**

- 由于本机代理对 `git clone` 返回 `CONNECT tunnel failed 502`、对 codeload 源码包限速且不支持断点续传，本次改用 GitHub `git/trees` + `git/blobs` API **按需抓取真实源码**。
- 已完整读取 **37 个** 源码/配置/提示词文件（清单见附录），覆盖房间级 backbone、Code_Object 框架、策略路由、数据结构、LLM 客户端、图像生成、Blender 插件、实验编排。
- 仓库文件树：783 个条目 / 694 个 blob / 约 80.6 MB（`py` 394、`obj` 112、`yaml` 80、`bin` 27、`sdf` 13）。
- **未执行**：`uv sync`、`pytest`、`python main.py`、Blender、任何 LLM 调用、任何渲染。
- 因此本文中所有关于"能否跑通""是否可用"的表述均为**代码级推断**，凡涉及运行结果的均标注为 `[待运行验证]`。按任务纪律，不用推断冒充验证结果。

**真实性说明（避免按论文反推代码）**：报告中每条结论都标注了文件与行号。凡论文提到但代码中未找到的，明确写「未找到」。

---

## 1. 项目架构

SceneCode 是**两层叠加**的系统，不是单一架构：

| 层 | 目录 | 职责 | 来源 |
|---|---|---|---|
| A. 房间级 Agentic Backbone | `scenecode/floor_plan_agents/`、`furniture_agents/`、`wall_agents/`、`ceiling_agents/`、`manipuland_agents/`、`scenecode/agent_utils/`、`scenecode/experiments/` | 文本 → 房间布局 → 逐物体请求 → 摆放 → 渲染 → Critique → 回滚 | **继承自 SceneSmith** |
| B. 对象级 Code_Object 框架 | `scenecode/code_object/agent_framework/`（49 文件） | 单个物体 → ObjectPlan → 逐部件 Blender Python 程序 → 执行/修复 → GLB/SDF/URDF | **SceneCode 核心贡献** |

**继承关系的硬证据**（不是靠 README 致谢推断）：

- `README.md:215` 明确致谢 SceneSmith。
- `pyproject.toml:62` `packages = ["scenesmith"]`、`:76` `known_first_party = ['scenesmith']` —— 打包与 isort 配置仍指向 `scenesmith`，是 fork 后未清理干净的残留，**`uv sync` / `pip install -e .` 时可能装错包名**，属于部署风险。
- `scenecode/agent_utils/` 的 137 个文件（BlenderServer、AssetRegistry、image_generation、rendering、convex decomposition、HSSD/Objaverse 检索）与 SceneSmith 目录结构同构。
- `configs/experiment/base_experiment.yaml` 的五阶段 `floor_plan → furniture → wall_mounted → ceiling_mounted → manipuland` 与 SceneSmith 一致。

**SceneCode 自己新增的部分**：`code_object/agent_framework/`（Planner / PlannerChecker / PartConstructor / BlenderMCP）、`asset_router` 的 `code_generated` / `code_articulated` 策略、`code_articulated_pipeline.py`、`urdf_pipeline_v1.py`、`postprocess_pipeline_v1.py`。

### 1.1 主调用链

```
python main.py +name=...                    (Hydra, configs/config.yaml)
  └─ experiments/indoor_scene_generation.py
       ├─ Stage floor_plan      → StatefulFloorPlanAgent
       ├─ Stage furniture       → StatefulFurnitureAgent
       │    └─ AssetRouter.route()                     [asset_router/router.py]
       │         └─ 5 strategies → _try_code_generated_strategy / ...
       │              └─ _prepare_code_object_inputs()  ★生成参考图
       │              └─ CodeObjectRunner.generate_from_image()
       │                   └─ code_object/agent_framework/Pipeline.run()
       │                        ├─ PlannerAgent          → ObjectPlan.json
       │                        ├─ PlannerCheckerAgent   → plan_check_history.json
       │                        ├─ PartConstructorAgent  → code/<pkg>/<part>.py + <object>.py
       │                        │    └─ BlenderMCP.execute_code()  → 执行/错误修复
       │                        ├─ _export_part_mesh_assets()  → OBJ / GLTF
       │                        ├─ _render_full_object_and_save() → renders/full_object.png
       │                        └─ _export_full_object_glb()   → GLB
       ├─ Stage wall_mounted / ceiling_mounted / manipuland
       └─ save_scene_as_blend() → scene_states/<name>/scene.blend
          combined_house/ → house.blend / house.dmd.yaml / house_state.json
```

关键代码位置：
- 五阶段与恢复：`configs/experiment/base_experiment.yaml:36-41`、`scenecode/experiments/indoor_scene_generation.py:1646-1648`
- `.blend` 保存：`indoor_scene_generation.py:233-257`（`save_scene_as_blend`，输出到 `scene_states/<name>/scene.blend`）
- 场景状态：`room.py:1593 to_state_dict()` / `room.py:1619 restore_from_state_dict()`，`indoor_scene_generation.py:1128`、`:1161`
- 最终产物清单：`README.md:202-211`

**两套独立的 Blender 后端**（容易踩坑）：
1. 房间级：`scenecode/agent_utils/blender/server_manager.py` 系列，端口段 `8000–8350`（`base_furniture_agent.yaml:43`），用于场景渲染、多视角分析、标注。
2. Code_Object 级：`code_object/agent_framework/blender/`（MCP addon + headless_server + mcp_client），端口 `9676`（`agent_framework/config.yaml:64-65`）、端口段 `9900–9999`（`base_furniture_agent.yaml:127`），用于执行部件 Python 代码。

---

## 2. PHASE 0 十项检查点逐项结论

### ① Planner / Designer / Critic 的实现

**存在两套、名字不同，不要混淆**：

| 角色 | 房间级（SceneSmith 继承） | 对象级（SceneCode 贡献） |
|---|---|---|
| Planner / Designer | `BaseStatefulAgent` 用 OpenAI **Agents SDK** 构造三个 `Agent`：`base_stateful_agent.py:241`、`:274`、`:306`（均 `model=self.cfg.openai.model`） | `agents/planner.py:16 PlannerAgent`（生成 ObjectPlan） |
| Critic | `max_critique_rounds: 3`、`early_finish_min_score: 9`（`base_furniture_agent.yaml:275-283`）；`CheckpointState` 双快照 N-1/N 回滚（`checkpoint_state.py:18-40`；`base_stateful_agent.py:470`、`:528`、`:871`） | `agents/planner_checker.py:54 PlannerCheckerAgent`，`check_and_fix()`（`pipeline.py:908`），`max_retry: 5`（`agent_framework/config.yaml:98-99`） |

**重要**：对象级的 "Critic" 是 **Plan Checker（检查 ObjectPlan 合理性）**，不是"渲染图 → 视觉评审 → 修复几何"的闭环。房间级有 `Render → Critique → Reset` 闭环（基于打分与 checkpoint 回滚），但那是**摆放层**的，不是部件几何层的视觉修复。任务里「Render → Critic → Repair 闭环」若指对象几何质量，代码里**未找到**该实现 —— `PartConstructorAgent` 只有 **执行错误修复**，不是视觉质量修复。

### ② AssetRequest、ObjectPlan 的完整数据结构

**`AssetRequest` 类在代码中未找到。** 全量 grep 只在 `README.md:43`（论文摘要）出现。代码里对应物是：

- `scenecode/agent_utils/asset_router/dataclasses.py:10 class AssetItem`：`description`、`short_name`、`dimensions: list[float]`、`object_type`、`strategies: list[str]`、`thin_covering_type`、`code_object_profile: str | None`。
- 同文件 `:97 GeneratedGeometry`：`geometry_path`、`item`、`asset_source`、`image_path`、`code_object_output_dir`、`object_plan_path`、`code_dir`、`pipeline_result_path`、`full_object_render_path` —— **这才是把「请求 / 程序 / 渲染 / 几何」关联起来的实际载体**。
- `:137 CodeArticulatedGeometry` 额外含 `urdf_path`、`sdf_path`。
- 场景物体：`scenecode/agent_utils/room.py` 的 `SceneObject` / `UniqueID` / `ObjectType`（被 `AssetRegistry` 使用）。

`ObjectPlan`：`scenecode/code_object/agent_framework/schemas/object_plan.py:236`，完整字段：

```
ObjectPlan: category, name, description, parts: List[PartPlan],
            total_dimensions: Dimensions, style, material_hints, metadata
PartPlan   (:168): name, part_type, shape: PartShape, position: Position(★世界坐标),
            rotation: Rotation, is_symmetric, symmetric_axis, is_movable,
            must_be_independent, description, priority, material: Dict,
            sub_parts: List[PartPlan] (★仅一层), instances: List[Dict]
PartShape  (:131): base_shape: ShapeType(cube|cylinder|sphere|cone|torus|custom),
            dimensions, modifiers: List[str], description
PartRelation (:108) / RelativePosition (:33) / BlenderAction (:311) / ActionTrace (:337)
```

**代码中发现的三处"声明了但没接上"**：
- `PartPlan.from_dict()` 的 `:221-222` 把 `parent` 和 `relations` 两行**注释掉了** —— `PartRelation` / `RelativePosition` 虽已定义，但**没有接入 ObjectPlan 的反序列化**。空间关系实际上不进入 ObjectPlan。
- `sub_parts` 在 `from_dict():212` 用 `sp_data.pop("sub_parts", None)` 强制去嵌套，层级只有一层。
- `position` 注释明确写「位置（世界坐标）」（`:173`），**不是局部坐标 / Local Transform**。

### ③ 五种 Object Code Generation Strategy

配置来源 `configs/furniture_agent/base_furniture_agent.yaml:90-120`，实现在 `asset_router/router.py`：

| # | 策略 | 默认 | 入口 | 是否 code-based | 图像生成依赖 |
|---|---|---|---|---|---|
| 1 | `code_generated` | ✅ enabled | `router.py:1303 _try_code_generated_strategy` → `:1488 _generate_code_object_geometry` | ✅ | **强制**（见 §6） |
| 2 | `code_articulated` | ✅ enabled | `code_articulated_pipeline.py:62`（`Pipeline` + `URDFPipeline` 包装） | ✅ | **强制**（同上） |
| 3 | `generated` | ❌ disabled | legacy geometry generation，`backend: sam3d`（`:76`） | ❌ | SAM3D |
| 4 | `articulated` | ❌ disabled | `router.py:579`，ArtVIP/PartNet-Mobility 检索 | ❌ | 无（需 72 GB 级数据集） |
| 5 | `thin_covering` | ✅ enabled | `router.py:883`，地毯/瑜伽垫等薄面 | ❌ | `generator.backend: "openai"` 作为材质 fallback（`:114-120`） |

**结论**：论文说的 "five code-generation strategies"，在代码里实际是 **5 种 asset acquisition strategies，其中只有 2 种是 code-based**，且 `code_articulated` 只是 `code_generated` 的 URDF 包装（`code_articulated_pipeline.py:74-86` 直接 `self.pipeline = Pipeline(...)`）。默认真正承载家具生成的是 `code_generated` 一条路径。这是"不要根据论文推断代码"的第二个实锤。

### ④ Part-wise Blender Python 生成与执行机制

- 生成：`part_constructor.py:134 _generate_code_with_llm()` → `:106 _validate_generated_code_for_part()` → `:327 construct_part()`（单部件）
- 执行：`:232 _execute_code()` → `_call_blender("execute_code", {"code": code})`；Blender 侧 `blender/addon.py:509 execute_code`
- 错误修复：`:247 _generate_code_fix_from_error()` —— 把 Blender 报错 + 完整代码回灌 LLM，**要求输出完整修正后的全量代码**（`:264-276` "Do not output only a patch snippet"），`max_retry: 5`（`config.yaml:102-103`）
- 代码落盘：`pipeline.py:434-447` 检查 `code/<parts_pkg>/<part.name>.py`；`:453 _get_ordered_part_script_paths()`；`:501 _build_ordered_part_execution_code()`（注入 `sys.path` 后按序 import 执行）
- 主文件：`part_constructor.py:623 _generate_main_file()`，含 `clear_scene()`（`:681`）
- 产物：`pipeline.py:549 _export_part_mesh_assets()`（逐部件 OBJ/GLTF）、`:726 _export_full_object_glb()`、`:758 _render_full_object_and_save()`、`renders/full_object.png`

**注意**：部件代码是 LLM 自由生成的任意 `bpy` Python 字符串，`PartShape` 只作为**提示词约束**传入，不是可执行的结构化几何节点。没有参数表、没有数据类型、没有依赖图。

### ⑤ Scene State Registry 的数据结构与更新方式

**有两套不同粒度的状态，不要混为一谈**：

1. **物体级 `AssetRegistry`**（`agent_utils/asset_registry.py:17`）：`dict[UniqueID, SceneObject]`，`register/get/list_all/save_to_file/load_from_file`（`:149`、`:202`）。序列化字段（`:162-189`）：`object_id`、`object_type`、`name`、`description`、`transform(translation + rotation_wxyz)`、`geometry_path`、`sdf_path`、`image_path`、`metadata`、`bbox_min/bbox_max`、`scale_factor`。支持 `auto_save_path` 增量落盘（`:54-57`）。**粒度是整件物体，不含部件、不含程序引用**（程序路径在 `GeneratedGeometry` 里，不在 registry 的字段里）。
2. **房间级 `scene_state.json` / `house_state.json`**：`room.py:1593 to_state_dict()` → `indoor_scene_generation.py:1128`，恢复用 `restore_from_state_dict()`（`:1161`）。是**场景快照 dict**，配合 `scene_states/scene_after_furniture/` 等分阶段检查点（`indoor_scene_generation.py:646`、`:718`、`:780`）与 `resume_from_path`（`:1646-1648`）做阶段级续跑；`base_stateful_agent.py:470/528/871` 用 `copy.deepcopy` 的 N-1/N 快照做质量回退。

**结论**：论文所说的 "persistent scene-state registry links object requests, executable programs, rendered geometry, and simulation assets" 在代码里是**靠文件路径字段（`GeneratedGeometry`）+ 两级 JSON 快照**实现的，不是部件级带稳定 ID 的语义状态树。

### ⑥ Blender 构建、渲染、保存、导出

- 房间级：`agent_utils/blender/server_manager.py` + `renderer.py`（123 KB）+ `rendering.py`（62 KB），`configs/furniture_agent/base_furniture_agent.yaml:24-68` 配置 `top_plus_sides` 多视图、1024² 顶视 + 4×512² 侧视、EEVEE TAA 8 samples、set-of-mark 标注。
- Code_Object 级：`agent_framework/blender/addon.py:704 render_scene()`（CYCLES / EEVEE / EEVEE_NEXT，`:726-739`）、`headless_server.py`、`mcp_client.py`；`agent_framework/config.yaml:74-88` 默认 EEVEE / 32 samples / **336×336**。
- 保存 `.blend`：`indoor_scene_generation.py:241-257` → `scene_states/<name>/scene.blend`；最终 `combined_house/house.blend`（`README.md:208`）。
- 导出：GLB（`pipeline.py:726`）、逐部件 OBJ/GLTF（`pipeline.py:549-620`）、SDF / Drake `.dmd.yaml`（`README.md:209`）、URDF（`code_articulated_pipeline.py:165`）。

### ⑦ 所有 LLM API 调用位置及模型配置

见 §4 模型调用清单。

### ⑧ 图像生成、Flux、外部模型、资产检索依赖

见 §6（本报告最关键一节）。

### ⑨ 对象局部修改和场景恢复的具体实现

**已有**：
- `pipeline.py:420 _check_existing_plan()` / `:432 _check_existing_construction()` —— 复用已有 `ObjectPlan.json` 与部件 `.py`，跳过重新生成（`:940-977` "Using cached construction"）。
- `pipeline.py:721 _rebuild_scene_from_saved_code()` —— 从保存的部件代码重建完整物体（被 `:736`、`:773` 调用）。
- `code_object/agent_framework/config.yaml:112` `enable_part_construct: false` —— "部件代码已存在，仅加载并生成主文件"。
- 房间级 `resume_from_path` 阶段续跑 + checkpoint 回滚。

**没有**：
- **未找到**"定位某个部件 → 改一个尺寸参数 → 只重跑该部件"的 API。部件代码是整块 Python 字符串，错误修复也是整块重写。
- `pipeline.py:870-872` 每次 `run()` 开头 `_clear_blender_scene()`，然后按顺序重放**全部**部件脚本 —— 重建是对象级全量。
- 房间级"局部修改"是 `restore_from_state_dict()` 快照回滚 + 从某个 stage 续跑，不是保留其他物体的增量编辑。

**结论**：SceneCode 提供的是 **part-level program persistence（可复用的部件程序文件）+ 阶段级场景恢复**，不是 **semantic component-level localized edit**。任务 PHASE 5 要求的"只重新执行受影响组件、非目标几何不重新生成"在 SceneCode 原生能力里**不存在**，必须由 aDSL 侧补上。

### ⑩ 官方 examples / configs / tests 能否直接运行

- `tests/` 296 文件：`unit/` 101、`integration/` 25、`test_data/` 166。`pyproject.toml:78-80` 有 pytest 配置（`asyncio_mode = "auto"`）。
- `tests/unit/test_default_asset_strategy_config.py` 是纯配置 smoke test（mock 掉四个 `_try_*_strategy`），**不需要 GPU/Blender/LLM**，是本机最容易先跑通的一项。
- 但 `tests/unit/test_flux-*.py`、`test_image_generation.py`、`test_blender*.py`（37 KB）等需要外部模型或 Blender。
- `examples/` 只有 1 个文件（`examples/prompts.csv`，`README.md:198` 引用），**没有可直接运行的 demo 场景**。
- `configs/` 14 个 yaml；`README.md:157` 的 quick start 是 `python main.py +name=my_experiment`，但完整运行需要 Blender + Drake + 一堆 checkpoint + LLM/图像 API。
- `[待运行验证]` 全部测试。静态判断：**在 Windows 无 GPU、无外部图像生成服务条件下，全套测试不可能直接通过**。

---

## 3. 模型调用清单

**关键结构性事实：SceneCode 有两套互相独立的 LLM 调用栈，改一处不够。**

| # | 调用栈 | SDK / 端点 | 默认模型 | 配置位置 | 用途 |
|---|---|---|---|---|---|
| A | 房间级 backbone | **OpenAI Agents SDK**（`openai-agents>=0.6.4`），`base_stateful_agent.py:241/274/306` 构造 `Agent(model=cfg.openai.model)` | `gpt-5.4` | `configs/furniture_agent/base_furniture_agent.yaml:287` | Planner / Designer / Critic 三人组 |
| A' | 房间级会话摘要 | 同一栈 | `gpt-5.2` | `base_furniture_agent.yaml:13` | session memory summarization |
| A'' | 房间级 VLM | `scenecode/utils/openai.py` 的 `OpenAI/AsyncOpenAI`（`:41-79`），`base_url = ${oc.env:OPENAI_API_BASE,https://api.openai.com/v1}`（`configs/config.yaml:5`） | 同 `gpt-5.4` | `openai.py:15-16` | mesh physics analysis / asset router analysis / validation（`:302-304`） |
| B | Code_Object 框架 | `AsyncOpenAI` + **`chat.completions.create`**（`utils/llm_client.py:278`） | **`gemini-3.1-pro-preview`** | `code_object/agent_framework/config.yaml:13/21/30/38` | Planner、PartConstructor、PlannerChecker、Vision 四个位置全部同一模型 |
| B' | 备用 provider | `llm_client.py:313 AnthropicClient`（默认 `claude-3-opus-20240229`，`:327`） | — | `config.yaml:8` `provider: openai\|anthropic\|local` | 未启用 |

**协议级耦合点（DeepSeek 适配必须逐个处理，仅换 URL + 模型名不够）**：

1. `base_furniture_agent.yaml:296-316` 传 `reasoning_effort`（`minimal/low/medium/high/xhigh`）与 `verbosity`（`low/medium/high`）；`:289` 传 `service_tier`；`:292` 传 `vision_detail: "high"`；`configs/config.yaml:11` 也是 `service_tier`。这些都是 OpenAI 特有参数，DeepSeek 官方 API 不识别。
2. `utils/openai.py:72-79` 有 `call_model_input_filter` 与按 `base_url` 缓存的 `AsyncOpenAI` 单例；`image_generation.py` 与 agent 共用 `OPENAI_API_KEY`，**把 key 指向 DeepSeek 会让 `images.generate` 打到 DeepSeek 而报 404**。
3. `llm_client.py:193` `need_thought_signature = "gemini-3" in model_lc`，`:205-234` 给 tool_calls 注入 `extra_content.google.thought_signature` —— 这是 **Gemini-3 专属兼容逻辑**，换模型名后会走不同分支，需要验证不会误伤。
4. `llm_client.py:265-277` 固定传 `temperature` 与 `max_tokens`，并转发 `tools` / `tool_choice`；返回 `tool_calls` 时封装成 `{"content", "tool_calls"}`（`:298-308`）。DeepSeek 在 thinking 模式下 `temperature` 无效、`tool_choice:"required"` 会返回 400、且要求把 `reasoning_content` 原样回传，否则 400。
5. `llm_client.py:243-261` 处理 `content` 为 list 的**多模态输入块**（`image_url`），`:312-317` 在 `part_constructor.py` 里把参考图编码成 `data:image/...;base64` 传入。DeepSeek Flash 的视觉能力需要单独实测；`deepseek-v4-pro` 不支持图像输入。
6. `OPENAI_TRACING_KEY`（`.env.example:3`）用于 OpenAI Agents tracing，DeepSeek 无对应服务，需关闭 tracing 而非留空。

**DeepSeek 官方 API 现状（2026-11-08 查证）**：`deepseek-flash`（支持图像输入、tool calls、thinking/非 thinking、JSON output）与 `deepseek-v4-pro`（纯文本）。`base_url = https://api.deepseek.com`，兼容 OpenAI / Anthropic 格式。`deepseek-chat` / `deepseek-reasoner` 已于 2026-07-24 弃用。**没有图像生成接口**。

---

## 4. 外部依赖清单

### Python 依赖（`pyproject.toml:6-48`，`requires-python >=3.11,<3.12`）

| 类别 | 包 | 风险 |
|---|---|---|
| LLM | `openai>=2.8.0`、`openai-agents>=0.6.4`、`google-genai>=1.0.0` | Agents SDK 与 DeepSeek 协议差异（§3） |
| Blender / 几何 | `bpy==4.5.4`（专属 index `download.blender.org/pypi`）、`mathutils`、`trimesh`、`manifold3d`、`pymeshlab==2023.12.post3`、`coacd`、`vhacdx`、`xatlas`、`python-fcl`、`shapely`、`rtree`、`pygltflib` | 多个需编译扩展；Windows wheel 覆盖不全 |
| 仿真 | `drake>=1.47.0` | Windows 上 pydrake 支持有限，官方按 Linux 设计 |
| ML / GPU | `torch>=2.5,<2.6`、`torchvision`、`diffusers>=0.35.1`、`transformers>=4.55.4`、`open-clip-torch>=2.20.0`、`einops`、`opencv-python` | 无 GPU 时 CLIP 检索 / SAM3D / Flux 均不可用 |
| 配置 / 其它 | `hydra-core`、`omegaconf`、`numpy<2.0`、`scipy`、`flask`、`pyvirtualdisplay`（Linux Xvfb）、`wandb`、`ninja`、`pybind11` | `pyvirtualdisplay` 是 Linux 依赖 |

### 外部服务 / checkpoint / 环境变量（`.env.example` 全量）

```
OPENAI_API_KEY          # 房间级 agent + 图像生成共用
OPENAI_API_BASE         # 默认 https://api.openai.com/v1
OPENAI_TRACING_KEY      # tracing
BLENDER_PATH            # .env.example 写 blender；README 用 PATH + SCENECODE_BLENDER_GLOBAL_LOCK
SCENECODE_OUTPUT_DIR / SCENECODE_MATERIALS_DIR / SCENECODE_MATERIAL_EMBEDDINGS_DIR
SAM3_CHECKPOINT / SAM3D_PIPELINE_CONFIG     # SAM3D backend + wall_art 裁剪
FLUX_PYTHON / FLUX_MODEL_PATH               # 独立 conda 环境 + FLUX.2-klein-9B checkpoint
GOOGLE_API_KEY                             # Gemini 图像生成
```

- README 额外要求 `SCENECODE_BLENDER_GLOBAL_LOCK`（`README.md:128`）跨进程文件锁；`.env.example` 里**没写这个变量**，属于文档不一致。
- `materials/` 目录含 7 个文件（Plaster001 / Wood094 等 1K-JPG 贴图），仓库内已带少量材质。
- 资产检索：`HSSD`（`data/hssd-models`）、`Objaverse/ObjectThor`（`data/objathor-assets`）、`ArtVIP`（`data/artvip_sdf`，默认 enabled）、`PartNet-Mobility`（默认 disabled）、`AmbientCG` 材质的 CLIP embeddings。这些都是**数十 GB 级**外部数据。

---

## 5. Image Generation 依赖位置（★ 重点）

任务里特意提醒「不要假设关闭 Flux 就代表所有图像生成依赖都已关闭」——源码确认这个提醒是对的。**至少 7 个独立位置**：

| # | 位置 | 行为 | 禁用难度 |
|---|---|---|---|
| 1 | `agent_utils/image_generation.py:190` `client.images.generate(...)` | **OpenAI 图像生成**，`backend: "openai"` 是**默认值** | 高：默认主路径 |
| 2 | `image_generation.py:351-376` `GeminiImageGenerator`（`gemini-3-pro-image-preview`）、`:520` | Gemini 图像生成 / 编辑 | 中：需 `GOOGLE_API_KEY` |
| 3 | `image_generation.py:588-815` `FluxKleinImageGenerator` + `flux_klein_worker.py` | 本地 **FLUX.2-klein-9B** 子进程 | 中：独立 conda + checkpoint |
| 4 | `base_furniture_agent.yaml:189-190` `backend: "openai"` / **`fallback_backend: "flux-klein"`** | 主 backend 失败时**自动回落到 Flux** | ⚠️ 改主 backend 不够，必须同时改 fallback |
| 5 | `router.py:1394-1420 _prepare_code_object_inputs()` | docstring 明写 "Generate the reference image"，`:1411` **无条件** `image_generator.generate_images(...)`，失败即 `return None` 使策略整体失败 | ⚠️ **最致命**：`code_generated` / `code_articulated` 主策略在房间流水线里是 **image-conditioned** 的，随后 `generate_from_image()`（`:1511`、`:1552`） |
| 6 | `router.py:2112-2121`、`:2151` | 另一处 `image_generator.generate_images(...)` + `image_path` 传递 | 需逐一核对 |
| 7 | `base_furniture_agent.yaml:114-120` thin_covering `generator.backend: "openai"` | 材质检索全部失败时的 AI 材质生成 fallback | 中 |

**另外两类容易漏掉的外部生成/检索服务**（都在 `blender/addon.py`，即 BlenderMCP 插件，由开关控制但代码确实存在）：

- `addon.py:242/1484-1763` **Hyper3D / Rodin** 付费 text/image-to-3D（`blendermcp_hyper3d_api_key`）
- `addon.py:251/2253-2510` **Hunyuan3D**（`blendermcp_hunyuan3d_secret_id/key`、`hunyuan3d_api_url`，`:2464`、`:2508` 调 `/generate`）
- `addon.py:232` **Poly Haven** 材质、`addon.py:251/1820-2037` **Sketchfab** 资产检索
- `router.py:1446-1461` **wall_art profile 用 SAM3 checkpoint 做画框裁剪** —— 即使完全不生成图像，墙画路径仍会碰 SAM3

**还有两个"图像输入"依赖（不是生成，但同样需要外部模型）**：
- `stateful_furniture_agent.py:249`、`base_furniture_agent.yaml:399-400 context_image_generation`（默认 `enabled: false`，关闭是安全的）
- `router.py:335-365`、`:754-793`、`:1178-1189` 的 **VLM 多视角网格验证**（把 Blender 渲染图 base64 送 LLM）—— 这属于允许保留的「DeepSeek 视觉评审」，但要求模型能接图像输入。

### 结论与可行替代路径

`Code_Object` 的 `Pipeline.run()` **支持纯文本模式**：`pipeline.py:369-378 _validate_generation_input()` 要求 `image_path` 与 `text_input` **二选一**，`run():835-849` 有 `input_mode == "text"` 分支，`part_constructor.py:301-305` 也会把 `text_input` 拼进 prompt，`prompts/planner.md` / `constructor.md` 有文本模式的提示词。

所以「禁用图像生成」在技术上**可行**，但必须做代码级改造，不是配置开关：

1. 新增 `CodeObjectRunner.generate_from_text(text_input=...)` 路径（或让 `_prepare_code_object_inputs` 在禁用图像时返回 `text_input`）；
2. 修改 `router.py:1511` / `:1552` 的 `generate_from_image(...)` 调用；
3. 把 `fallback_backend` 改成不触发任何生成的值，并处理 `image_generator is None` 的分支；
4. 关闭 thin_covering 的 `generator.enabled`；
5. 关闭 addon 里的 Hyper3D / Hunyuan3D / Sketchfab / PolyHaven 开关；
6. 保留 VLM 验证，但改走 DeepSeek 视觉输入。

`[待运行验证]` 纯文本模式下 `PlannerAgent` 生成的 ObjectPlan 质量（部件丰富度、是否退化为 cube 堆叠）——这是 PHASE 2 必须实测的核心风险。

---

## 6. 对象级代码生成与编辑机制（对接 aDSL 的关键判断）

**可映射的部分**：
- `ObjectPlan.parts` → 语义部件列表，每个 part 有稳定字符串 `name`、独立的 `.py` 程序文件、`is_symmetric`/`symmetric_axis`、`is_movable`、`material`、`priority`。
- `GeneratedGeometry` 已把 `object_plan_path` / `code_dir` / `pipeline_result_path` 与几何产物绑定 —— 这是 registry ↔ 程序链接的现成锚点。
- `ActionTrace.final_code`（`object_plan.py:341`）保存了每个部件的最终代码与迭代次数。
- `_get_ordered_part_script_paths()` + `_build_ordered_part_execution_code()` 提供了**部件级有序执行**的现成机制，`_check_existing_construction()` 提供了部件级缓存复用。

**不能直接映射的部分**：
- 部件代码是**自由格式的 `bpy` Python 字符串**，`PartShape` 只是提示词输入，没有结构化参数表 / 类型 / 依赖图 / 局部坐标系。
- `PartPlan.position` 是**世界坐标**，不是 Local Transform；`PartRelation` 未接入反序列化（§2②）。
- 没有 CSG 概念（`ShapeType` 只有 6 种基础形）。
- 没有稳定 Component ID（`name` 是 LLM 生成的字符串，跨次运行不保证一致）。
- `_generate_code_fix_from_error()` 重写整份部件代码；`run()` 开头清空场景并全量重放。

**判断**：把 part-wise Blender Python 包装成「带结构化参数、语义部件、局部坐标系和依赖关系的 aDSL Parametric Component」**不能靠自动转换完成**。需要一层显式的代码契约（每个部件程序暴露 `build(params) -> Asset`、参数 schema、局部原点、输出 AABB），并要求 LLM 按该契约生成。这是 PHASE 3/4 的主要工作量，不是 JSON 字段搬运。

---

## 7. 部署风险汇总

| 风险 | 证据 | 影响 |
|---|---|---|
| **环境版本自相矛盾** | `README.md:95` 要求 Blender **4.4.3 Linux**；`pyproject.toml:10` 却锁 `bpy==4.5.4` | 两个 Blender 版本并存，`bpy` wheel 与独立 Blender 的 API 可能不一致 |
| **打包配置残留** | `pyproject.toml:62` `packages=["scenesmith"]`、`:76` `known_first_party=['scenesmith']` | `uv sync` / 可编辑安装可能失败或装错包名 |
| **Linux 专用依赖** | `pyvirtualdisplay`、`drake`、README 的 `blender-4.4.3-linux-x64.tar.xz`、bubblewrap 类隔离、`/path/to` 风格路径 | Windows 原生部署基本不可行；WSL2 是必需项 |
| **GPU / 数据体量** | torch 2.5 + CLIP + SAM3D + FLUX.2-klein-9B + V-HACD + ArtVIP/HSSD（72 GB 级） | 与「不租用 GPU 服务器、只用 Windows 本机」的资源约束冲突；只能走纯代码生成 + 禁用检索/生成后端 |
| **两套 LLM 栈** | §3 | DeepSeek 适配需两处 Provider Adapter，不是一个 `.env` |
| **图像生成是主策略硬依赖** | §5 #5 | 不做代码改造则 `code_generated` 直接失败，整个家具阶段跑不起来 |
| **`OPENAI_API_KEY` 语义重载** | `image_generation.py:141-146` 与 agent 共用同一变量 | 指向 DeepSeek 后 `images.generate` 必然 404；必须把文本 LLM 与图像生成彻底拆开 |
| **网络** | 本次审计中 git/codeload/raw/jsdelivr 均不稳定 | `uv sync` 拉取 78 MB 依赖、Blender 4.4.3 下载、HuggingFace checkpoint 都会受影响 |
| **无现成 demo / 完整测试** | `examples/` 仅 1 个 CSV | 「跑通」需要自己构造最小入口，不能照抄示例 |
| **API key 管理** | `.env.example` 明文占位；`README.md:123` 直接 `export` | 必须 `.env` + `.gitignore`，禁止写入代码/日志/报告（本次审计未写入任何真实 key） |

---

## 8. 与现有 aDSL 的初步差异

### 8.1 本地 aDSL 的真实接口（已逐文件核对 `E:/3DUse/aDSL/`）

| 任务描述 | 代码实际情况 |
|---|---|
| `Scene → Asset/Object → Component → Primitive` 四级类 | **核心库只有 `Asset` 一个类**（`adsl-core/core/asset.py:43`），Scene 是约定变量名 `scene`、Component 是 `Asset` 子节点、Primitive 是 **dict**（`_primitives: List[Dict]`，`asset.py:49`）。`Scene/Component/Primitive` 独立类**未找到** |
| `attach_part` | ✅ 存在：`asset.py:62 attach_part(name, shape) -> Asset`；配套 `detach_part(:78)` |
| Cube / Sphere / Cylinder | ✅ `primitives.py:66/40/95`，另有 `RoundedCube(:187)`、`ExternalMesh(:151)` |
| CSG Union/Difference/Intersection | ✅ `boolean.py:27/47/37`（函数，生成 `{"type":"boolean"}` primitive，Blender 侧 `export_glb.py:193 _apply_boolean` 执行） |
| 稳定 Component ID | ⚠️ **核心 `Asset` 上没有**。标识只是 `_children` 的字符串键。`component_id` 只存在于规划层 `adsl-agents/models.py:20 ComponentContract` |
| Local Transform / Geometry Metadata / Collision AABB | ⚠️ **核心 `Asset` 上没有这些字段**。局部变换存在 primitive dict 的 `xform`(4×4) 与 `Joint.origin`；AABB 是运行时计算（`bounds.py:315 shape_aabb`），不是持久字段。规划层 `ComponentContract` 有 `local_bounds` |
| `align_center` | 实际是 **`align_centers`**（复数，`layout.py:326`）。另有 `align_anchors(:92)`、`shape_anchor(:81)`、`distribute_along_axis(:179)`、`stack_shapes/grid_shapes/radial_shapes/place_on_axis/offset_from` |
| 组件间约束 | ✅ `relations.py:56 RelationAssembly`（`box/span_box/rigid/attached/supported/within/bound/parameter/datum/compile`），`Expr`(:23) 表达式 + Z-up AABB 约束求解器。`compile()` **总是返回全新 scene**（`:61` 注释） |
| Parametric Code | ⚠️ **没有 `ParametricCode` 类**。`parametric_code` 只是 `models.py:22 generation_mode` 的字符串枚举值。真正的「参数化几何程序」是 **aDSL Python 源文件 `source.py`**，由 `runpy.run_path` 执行 |
| `asset_to_dict` | ✅ `serialize.py:6 asset_to_dict(asset) -> dict`，返回 `{label, primitives, children, joints}`；`Asset.to_dict()`(`asset.py:519`) 即调它。另有更完整的 `export_manifest.py:44/93` |
| 反序列化 | ⚠️ **核心库没有**。全项目仅 `scripts/blender_worker.py:39 asset_from_json` 一处 |
| ExternalMesh | ✅ 仅作外部资产引用 + 显式 AABB 代理（`primitives.py:151-184`），自身不生成网格，符合"不能变成主要几何方式"的要求 |
| Blender Executor | 不是「编译成 Blender Python 文本」，而是 `export_glb.py:455 export_glb(shape, ...)` 直接调 `bpy.ops.mesh.primitive_*_add` 建对象；入口链 `adsl-agents/utils/execution.py:25 → asset_executor.py:25 (runpy.run_path) → export_glb` |
| **增量 / 局部重执行** | ❌ **未找到**。`export_glb.py:459 clear_scene=True` + `:468 read_factory_settings(use_empty=True)`；`blender_worker.py:190/193` 同样清空；`execution.py:47 output.mkdir(exist_ok=False)`；`asset_executor.py:29` 每轮整份 `runpy.run_path`。`EditPlan.patch_scope` 只是规划文本，`service.py` 的 "minimal patch" 改的是 `source.py` 文本，执行仍全量重跑 |

### 8.2 结构性阻断项（对 PHASE 3–5 的影响）

> **这是本次审计最重要的发现，建议在进入 PHASE 3 之前先决策。**

任务 PHASE 5 的验收表要求：`Localized Update — 非目标几何不重新生成`、`Semantic IDs — 非目标组件 ID 保持不变`、`不要仅通过修改 JSON 并重新生成整个场景来通过测试`。

**但现有 aDSL Executor 的执行模型就是全量重建**（`clear_scene=True` + `runpy.run_path` 整份 source）。SceneCode 侧同样在 `run()` 开头 `_clear_blender_scene()` 后重放全部部件脚本。**两边都不具备增量重执行能力**。

因此 `adsl_adapter/` 单靠「ObjectPlan → aDSL AST 转换」无法通过 Test A/B/C。必须先解决以下最小扩展：

1. **稳定 ID**：给核心 `Asset` 增加持久 `component_id`（现在只有 `_children` 键名），并保证 `asset_to_dict` / `asset_from_json` 往返不丢。
2. **依赖图与脏标记**：记录 Component 间的参数/空间依赖，编辑时计算受影响闭包。
3. **增量执行器**：在 `export_glb` / `blender_worker` 里支持「按 ID 删除并重建指定子树，其余对象保持 `bpy` 句柄不动」，替代 `clear_scene=True`。
4. **序列化双向化**：把 `asset_from_json` 从 `scripts/` 提升到核心库，作为 Source of Truth 的读回入口。
5. **关系层与执行层打通**：`RelationAssembly.compile()` 目前每次返回全新 scene，需要支持基于稳定 ID 的局部重求解（对应 Test B）。

这 5 项属于 **aDSL 核心执行模型的改造**，不是「Adapter」层的活，也不是「不大规模重写现有代码」能覆盖的。PHASE 4 的约束与 PHASE 5 的验收标准之间存在张力，需要先和用户确认取舍。

### 8.3 SceneCode → aDSL 初步映射可行性

| SceneCode | 目标 aDSL | 可行性 | 缺口 |
|---|---|---|---|
| `AssetItem` (论文 AssetRequest) | Scene/Object 语义请求 | 🟡 | 无 `AssetRequest` 类；`AssetItem` 只有 description/dimensions/strategies |
| `ObjectPlan` | Asset 层级 | 🟡 | 世界坐标、无稳定 ID、无 CSG、`PartRelation` 未接 |
| `PartPlan` | Asset 子节点 / Component | 🟢 结构可映射 | 需补稳定 ID 与参数 schema |
| `PartShape` | aDSL Primitive | 🟡 | 6 种形状可映射，但 `modifiers`/`custom` 无对应语义 |
| `position/rotation` | `xform` / Local Transform | 🔴 | 世界坐标 → 局部坐标需要显式变换推导 |
| `is_symmetric` / `symmetric_axis` | `distribute` / `align_anchors` / `Expr` | 🟡 | 需定义对称约束语义，现有 `relations.py` 可承载 |
| `material` | Component Material | 🟡 | aDSL 的 `appearance` 模块需核对字段 |
| `instances` | `obj.copy()` 风格组件实例 | 🟡 | aDSL 无显式 instance 概念 |
| `is_movable` + URDF | `Joint` / `attach_joint` | 🟢 | aDSL 有独立关节树（`joints.py`），**articulated 能力可保留**，这是好消息 |
| part-wise `.py` | Parametric Code | 🔴 | 自由格式字符串 ≠ 结构化参数化程序；需契约化改造（§6） |
| `GeneratedGeometry` 路径字段 | Component ID + Scene State | 🟡 | 需把路径关联升级为 ID 关联 |
| 阶段级 `resume_from_path` / 缓存复用 | 局部编辑 | 🔴 | 粒度不同，不能等价替代 |

---

## 9. 审计结论

**已在代码层面确认的事实**：
1. 仓库与论文真实存在，SceneCode 是 SceneSmith 的 fork + 新增 `code_object/agent_framework/`。
2. 存在**两套独立 LLM 调用栈**（OpenAI Agents SDK `gpt-5.4` / AsyncOpenAI `gemini-3.1-pro-preview`），DeepSeek 适配必须分别处理，且有多处 OpenAI/Gemini 专属协议参数。
3. **图像生成不是可关即关的可选插件**：主策略 `code_generated` 在 router 层强制先生成参考图；fallback 还会回落到 Flux；addon 里另有 Hyper3D / Hunyuan3D / Sketchfab / Poly Haven；wall_art 路径还依赖 SAM3。
4. 但 `Code_Object Pipeline` 本身支持 `text_input` 纯文本模式 —— **纯代码几何路线在技术上成立**，代价是要改 router 与 runner 的调用链。
5. `ObjectPlan` 不等于 aDSL：世界坐标、无稳定 ID、无 CSG、`PartRelation` 未接入、`sub_parts` 单层。
6. SceneCode 有 part-level 程序持久化与阶段级场景恢复，**没有组件级参数化局部编辑**。
7. 本地 aDSL 与任务描述有 10 处口径差异，最要命的是**没有增量重执行**，这会直接卡住 PHASE 5 的三项测试。

**明确未验证、不得当作已完成的事项**：
- 任何安装、任何测试、任何 `main.py` 运行、任何 Blender 执行、任何渲染、`house.blend`、多视角图、DeepSeek 实际调用与 `/models` 查询。
- 纯文本模式下家具质量（是否有靠背/扶手/坐垫/底座、是否退化为 cube 堆叠）。
- examples / configs / tests 的真实可运行性。

**建议的下一步顺序**（遵守「先复现、后适配」）：
1. 先解决 PHASE 1 的环境可行性：WSL2 + Python 3.11 + `uv` + Blender 4.4.3（注意与 `bpy==4.5.4` 的冲突），并修掉 `pyproject.toml` 的 `packages=["scenesmith"]`。
2. 在 PHASE 2 之前，**先跑 `tests/unit/test_default_asset_strategy_config.py`** 这个无 GPU/无 LLM 的 smoke test，作为环境可用的最小证据。
3. 在实现 aDSL Adapter 之前，先就 §8.2 的增量执行器改造做架构决策 —— 否则 PHASE 5 无法按验收表通过。
4. DeepSeek Provider Adapter 需要覆盖两套栈，并对 JSON 输出、tool calls、长文本、图像输入分别做实测（任务 PHASE 1.2 要求）。

---

## 附录 A：本次实际读取的源码文件（37 个）

```
.env.example, pyproject.toml, README.md, main.py(下载),
configs/config.yaml, configs/experiment/base_experiment.yaml,
configs/furniture_agent/base_furniture_agent.yaml,
scenecode/code_object/agent_framework/config.yaml,
  schemas/object_plan.py,
  agents/base_agent.py, agents/planner.py, agents/part_constructor.py, agents/planner_checker.py,
  pipeline.py, code_articulated_pipeline.py,
  utils/llm_client.py,
  blender/backend_manager.py, blender/headless_server.py, blender/addon.py,
  prompts/planner.md, prompts/constructor.md, prompts/checker.md, prompts/urdf.md,
scenecode/utils/openai.py,
scenecode/agent_utils/image_generation.py, flux_klein_worker.py,
  asset_registry.py, base_stateful_agent.py, checkpoint_state.py,
  asset_router/router.py, asset_router/dataclasses.py, room.py,
scenecode/furniture_agents/stateful_furniture_agent.py,
scenecode/experiments/__init__.py, base_experiment.py, indoor_scene_generation.py,
tests/unit/test_default_asset_strategy_config.py
```

## 附录 B：本地 aDSL 核对时读取的关键文件

```
E:/3DUse/aDSL/adsl-core/core/asset.py, primitives.py, boolean.py, layout.py,
  bounds.py, transforms.py, relations.py, serialize.py, joints.py,
  export/export_glb.py, export/export_urdf.py, export/export_manifest.py,
E:/3DUse/aDSL/adsl-agents/models.py, utils/execution.py, utils/asset_executor.py,
  service.py, prompt/planner.md,
E:/3DUse/aDSL/scripts/blender_worker.py, scripts/test_relations.py,
E:/3DUse/aDSL/test_adsl_direct.py, test_blender_integration.py,
E:/3DUse/aDSL/outputs/chair.json, examples/physical_bookshelf/model.py
```

---

> 本报告不包含任何真实 API Key，也不包含任何运行结果或日志。所有"成功/可用"类判断均为静态代码推断，已标注 `[待运行验证]`。
