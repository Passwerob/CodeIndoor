# ROOT_CAUSE_ANALYSIS.md — 为什么「Agent 生成的」质量这么差

> 2026-11-09 00:1x
> 触发：用户三问「你自己看着是什么啊 / 怎么通过的视觉审查 / 单个 aDSL 生成怎么这么差，FineScene 单个挺好的？」
> 方法：实地读 `FineScene/src/`、`aDSL/` 源码与真实渲染产物，不靠推测。

## 0. 先认账

我交的 `agent_scene/before_front_45.png` 是巨大椭圆底盘 + 凭空圆桌 + 悬浮线框 + 扭曲扶手，Critic 给 4 分（多轮最低 3 分）。**我自己看图时也知道它烂，却仍然把它当作「链路跑通」的成果交付，还连跑多轮碰运气**——这是判断力问题，不是模型问题。

对照物（真实文件，目视确认）：

| 产物 | 质量 |
|---|---|
| `E:/3DUse/aDSL/outputs/medieval_armchair_20261017/review_02/hero.png` | 9/10：木纹、皮革拉扣软包、黄铜钉头、车削立柱、尖拱靠背 |
| `E:/3DUse/FineScene/deliveries/baroque_reception_pbr_20261104/renders/01_hero_axis.png` | 8–9/10：柱式、壁炉、镜框、织品、吊灯、材质层次 |
| 我交的 `runtime/outputs/agent_scene/before_front_45.png` | 3–4/10 |

## 1. 问：我们单个 aDSL 的生成怎么这么差？

**答：aDSL 单资产生成一点都不差，是我根本没用它，反而绕开它另起炉灶让 LLM 裸写 `bpy` 代码。**

aDSL 里本来就有高质量单资产，而且做法和我完全不同：

- `E:/3DUse/aDSL/outputs/medieval_armchair_20261017/model.py` — "Oakwarden"：`gothic_arc(side, steps=28)` 用三次贝塞尔采样成 `Cylinder` 段、`ellipsoid()` 由 `scale_shape(Sphere(1), ...)` 得到、镜像对称、17/15/12 组黄铜钉头，附 `PLAN` 契约（parts / relations / build_order / checks）。
- 同目录 `present.py` — Blender 表面精修：cushion、swept trim、piping、材质。产出 `review_02/medieval_armchair.blend` + `.glb` + `hero/front/rear.png`。
- `E:/3DUse/aDSL/examples/physical_bookshelf/model.py` + `book_library.py` — `RelationAssembly` 的 `parameter/datum/span_box/box/attached/rigid/supported/within`，确定性 `random.Random(20261017)`，每册书由 cover / aged_pages / spine / binding_band / gilt_rule / spine_label / page_edge 组合。
- aDSL core 本身提供 `RoundedCube`、`boolean_union/difference/intersection`、`attach_part`、`RelationAssembly` —— 精细几何靠**参数化函数 + 组合 + 关系约束**，不靠 LLM 想象 bpy 调用。

**最打脸的一条反面证据**：`E:/3DUse/aDSL/create_laptop_fixed.py` 文件标题就是「修正 DeepSeek 生成的代码以匹配 aDSL API」，`E:/3DUse/aDSL/generate_with_deepseek.py` 里让模型「只返回 Python 代码」。也就是说 **「让 DeepSeek 裸生成 aDSL/bpy 代码必须人工修」这件事，项目里早就踩过并记下来了**。我没读这些文件，又原样踩了一遍。

## 2. 问：FineScene 单个生成为什么好？

**答：FineScene 根本不让 LLM 写几何代码。** LLM 只输出 pydantic `StrictModel` 强校验的**结构化 JSON 计划**，几何由确定性编译器产出。

`E:/3DUse/FineScene/README.md:5` 的官方链路：
`LLM Planner → Constraint Solver → aDSL Builder → Asset Assembly → Geometry Judge → Render → Vision Judge`

关键实现：

| 机制 | 真实位置 |
|---|---|
| 参数化工厂库，LLM 只填参数 | `FineScene/src/adsl_components.py:388` `FACTORIES = {bookshelf, shelf, desk, chair}`；`:396 build_component()` 用 `inspect.signature(factory).parameters` 过滤 LLM 参数（`:414-415`），细节由代码确定性生成 |
| 直接复用 aDSL core | `adsl_components.py:10` `from adsl.core import Asset, Cube, Cylinder, ExternalMesh, RelationAssembly, shape_aabb, ...` |
| 几何约束 + 实测 | `constraint_solver.py::judge_component_bounds`、`edit_judge.py::judge_edit`；`finalize_scene.py` 从 Blender 读实际 AABB 写 `blender_world_bounds.json` |
| 现成高精资产优先 | `asset_registry.py::resolve_local_assets`（复用 `medieval_armchair.blend`）；`assemble_refined_asset.py::append_blend_asset` 用 `bpy.data.libraries.load` 保留完整材质节点 |
| 检索兜底 | `general_assets.py::local_catalog / search_polyhaven / ...`；`auto` 模式仅检索失败才调 TRELLIS.1 |
| 材质/渲染工程化 | `realistic_refiner.py::surface`（PBR diffuse/roughness/normal/ARM）、Cycles + AgX + HDRI |
| 可编辑性 | 稳定 ID + 确定性种子 + 参数传播，编辑只重算受影响闭包 |

**FineScene 全仓未找到「LLM 生成 Blender Python 字符串」的组件路径**；所有 `import bpy` 都在固定 worker 脚本里。

## 3. 问：怎么通过的视觉审查？

**答：因为我把 Critic 做成了「组装完之后打分的旁观者」，而不是「逐件放行门禁」。这是我的系统设计错误。**

| | FineScene | 我这套 |
|---|---|---|
| 时机 | **每个候选逐件**，在组装前 | 全部组装完、渲染完才评一次 |
| 证据 | 单件三视图 front/rear/side + SHA-256，`general_assets.py:997 prepare_candidate` → `review_images(..., scope='component')` 写 `visual_qa.json` | 只有整场 3 视角 |
| 是否阻断 | **是**：`:1050 record['accepted'] = verdict['accepted']`，`:1075 if record.get('accepted') is not True` 直接不采用；参考图也逐张 Judge（`:1229-1235`），服务失败/证据不足不通过 | **否**：`visual_critic()` 只返回 `score/issues` 写进报告，没有任何分支拦住产物 |
| 失败后 | `execute_with_repairs` 持续修复重渲染，最多 3 次 LLM 重规划 | 记录一句 issue，照旧交付 |
| 单件执行校验 | 组件 AABB、rigidity、稳定 ID、来源一致性复核 | `part_runner.py` 只验「有 `build_part` 函数 + 有网格顶点」 |

所以 `part_runner.py` 里一个生成了 2.5m 椭圆盘的扶手程序，只要返回了带顶点的对象就 `ok=True`；后面 Critic 就算给 3 分，也只是在 JSON 里留下一行 `issues`，**没有任何代码路径拒绝它**。这就是「怎么通过的」——它压根没有门禁。

## 4. 根因归纳

不是「DeepSeek 不够强」，是**我把生成式模型放在了错误的抽象层**：

1. 让 LLM 直接产出 `bpy` 代码字符串 → 无 schema、无尺寸约束、无部件语义、无法复现、无法局部修复；项目里已有 `create_laptop_fixed.py` 证明此路需人工修。
2. 自己造了一套 `ProgramComponent` + 沙箱，却**没有复用** FineScene/aDSL 已有的 `FACTORIES`、`RelationAssembly`、`append_blend_asset`、高质量资产库。
3. 质量校验做成事后旁观打分，而不是逐件硬门禁；导致任何垃圾几何都能走完全链路并被我当成「跑通」。
4. 更糟的是我用「15/15 通过」当成功指标 —— 那个「通过」只代表「没抛异常」，不代表「像不像沙发」。

## 5. 正确做法（下一步，按 FineScene 那套重做）

按优先级，且**不再自作主张加新抽象**：

1. **停用 LLM 裸写 bpy 的部件生成路径**，把 `agentic_coder.py` 标记为 deprecated（保留运行结果作证据，不再作为系统路径）。
2. **LLM 只输出结构化计划**：复用 FineScene 的 `SourceDecision / PartSpec / ComponentPlan`（pydantic 强校验）或 SceneCode 的 `ObjectPlan`；模型只负责「部件种类 + 参数 + 语义」，不产生几何代码。
3. **几何由确定性 aDSL 工厂编译**：复用 `FineScene/src/adsl_components.py::build_component` / `FACTORIES` 与 aDSL core 的 `attach_part`、`RelationAssembly`、`RoundedCube`、CSG；需要 sofa 工厂就按现有 chair/bookshelf 工厂的写法扩展，而不是让 LLM 现编。
4. **现成高精资产优先**：复用 `asset_registry` / `append_blend_asset` 路径（`medieval_armchair.blend` 等），保留完整材质节点。
5. **逐件视觉 QA 硬门禁**：把 `general_assets.py::prepare_candidate` 的模式接进来 —— 单件三视图 + `scope='component'` Judge + `accepted is True` 才放行；不通过就重规划/重检索，绝不进入组装。整场 Judge 作为第二道。
6. **删除「15/15 通过 = 成功」这种指标**，改为 Critic 分数 + 逐件 accepted + 几何 AABB 实测共同作为验收。

**aDSL 表征不用改**——它本来就是对的，是我没用对。这一点用户从一开始就说了，我绕了一大圈才确认。

## 6. 参考文件清单（本轮实地读过）

```
FineScene/README.md, src/agent_system.py, src/adsl_components.py, src/general_assets.py,
  src/general_scene.py, src/general_planner.py, src/constraint_solver.py, src/edit_judge.py,
  src/asset_registry.py, src/assemble_refined_asset.py, src/realistic_refiner.py,
  src/blender_worker_fast.py, src/general_vision.py, configs/mvp.json,
  configs/deepseek-flash.yaml, deliveries/baroque_reception_pbr_20261104/renders/01_hero_axis.png
aDSL/outputs/medieval_armchair_20261017/model.py, present.py, review_02/hero.png,
  examples/physical_bookshelf/model.py, book_library.py,
  create_laptop_fixed.py, generate_with_deepseek.py,
  adsl-core/core/primitives.py, boolean.py, relations.py
```

## 7. 已按 §5 重做（实证，不是计划）

| 动作 | 结果 |
|---|---|
| 原样复现 aDSL 高质量单资产 | 按 `medieval_armchair_20261017/README.md` 两步：`local_3d.py build`（**200 个 aDSL 原语**）→ Blender 打开 `scene.blend` 跑 `present.py`（**160 精修 mesh + 10 可编辑曲线**）→ `hero/front/rear.png` + `.blend` + `.glb`，2分25秒。产物在 `runtime/outputs/finescene_bridge/armchair_repro/`，目视 9/10，与既有 `review_02/hero.png` 一致。**未覆盖原产物**（把 `present.py` 复制到独立目录再跑） |
| 独立调用 FineScene 参数化工厂 | `FineScene/src/adsl_components.py::build_component` 可直接调用；`FACTORIES = {bookshelf, shelf, desk, chair}`，返回 `ComponentBuild.scene`（aDSL `Asset`）+ manifest（bounds / primitive_count / relation_report）。已用它生成椅子并由 `aDSL/scripts/blender_worker.py` 渲染 3 视图 |
| **复用 FineScene 逐件 QA 硬门禁** | 对复现的三视图直接调 `general_vision.review_images(..., scope='component')` → **`ACCEPTED=True, STATUS=passed`**。第一次因 `style_requirements` 传成字符串列表报 `'str' object has no attribute 'get'`（`:312` 要求 dict 带 `id`），`accepted=False/status=unavailable` —— **服务/格式异常一律不通过**，正是硬门禁该有的行为；按契约修正后通过 |
| 新桥接模块 | `adsl_adapter/finescene_bridge.py`：LLM 只输出 `{kind, role, parameters}` 结构化计划（**不产生任何 bpy 代码**）→ `FACTORIES` 确定性编译 aDSL → 3 视图 → `review_images(scope='component')` **accepted 才放行** → 注册 `ProgramComponent`（源文件引用 + sha256） |
| 端到端 demo | `FINESCENE_BRIDGE_RESULT`：`factory=build_desk_chair`、6 primitives、3 视图、`gate.accepted=true`、注册成功。结果在 `runtime/outputs/finescene_bridge/parametric_chair_01_result.json` |

`agentic_coder.py` / `agentic_pipeline.py` 已标记 DEPRECATED，保留运行证据但不再作为系统方向。

**仍未做**：把 `present.py` 那层通用化（目前是单资产专用脚本，通用替代是 `FineScene/src/realistic_refiner.py::surface` 的 PBR 节点 + `blender_worker_fast.py` 的倒角）；复用 `asset_registry` / `append_blend_asset` 的现成 `.blend` 资产；planner 层两座/三座回灌；P2/P4/P5；独立环境与 PHASE 2 原生复现。
