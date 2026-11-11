# SCENECODE_ADSL_MAPPING.md — 映射设计（方向修正版）

> 版本：v2，2026-11-08
> 方向：**aDSL 主动承接 SceneCode 的既有产物；SceneCode 不改一行**
> 前置：`SCENECODE_AUDIT.md`（真实源码结论）、`SCENECODE_ADSL_ARCHITECTURE.md`（方向定稿）

---

## 1. 映射原则（与 v1 的根本区别）

| | v1（审计版隐含） | **v2（用户定稿）** |
|---|---|---|
| 动词 | 翻译 / 转换 / 接管 | **承接 / 包装 / 索引** |
| 谁改 | 隐含 SceneCode 要配合输出结构化数据 | **只有 aDSL 改** |
| 上游产物 | 被当作需规范化的中间格式 | 被当作**不可变输入事实** |
| ID | aDSL 自己生成一套 ID | **直接采用 `PartPlan.name`** |
| 程序 | 期望改成结构化参数 | **原样承接 `.py`，外面加契约** |

一句话：**不要求 SceneCode 变得更像 aDSL，而是让 aDSL 能容纳 SceneCode 本来的样子。**

---

## 2. 完整映射表

| SceneCode（真实代码位置） | 目标 aDSL | 承接方式 | 需要 SceneCode 改动？ |
|---|---|---|---|
| `AssetItem`（`asset_router/dataclasses.py:10`）<br>论文称 AssetRequest，代码中无此类 | Scene/Object 语义请求 | 摄取为 aDSL 根节点元数据：`description` → `label`，`dimensions` → AABB 提示，`object_type` → 类别标签 | ❌ |
| `ObjectPlan`（`schemas/object_plan.py:236`）<br>`category/name/style/total_dimensions/material_hints/metadata` | `Asset` 根节点属性 | 写入 aDSL 根 `Asset` 的 `component_id = ObjectPlan.name`（已是稳定字符串）+ 扩展元数据 | ❌ |
| `PartPlan`（`:168`）<br>`name/part_type/shape/position/rotation/is_symmetric/...` | `Asset` 子节点 / Component | `component_id = PartPlan.name`（= `code/<name>.py` 文件名）；`attach_part(name, ...)` | ❌ **已实现** |
| `PartShape.base_shape`（`:131`，6 种） | aDSL `Cube/Sphere/Cylinder` 等 primitive | 作为**提示性元数据**保留；实际几何以 SceneCode 生成的程序为准 | ❌ |
| `PartShape.modifiers` / `CUSTOM` | `ProgramComponent` 的程序内实现 | 不强行降级为 primitive；由程序承接 | ❌ |
| `position`（`:173`，**世界坐标**） | `xform` / Local Transform | 摄取时按父节点位姿**推导局部变换**；原始世界坐标保留在 `upstream_pose` 备用 | ❌（计算在 aDSL 侧） |
| `rotation`（`:89`，欧拉角/度） | `xform` 的 4×4 | aDSL `transforms.py` 的 `rotation_matrix`（角度单位为度，天然对齐） | ❌ |
| `PartRelation` / `RelativePosition`（`:108/:33`，**from_dict 中被注释掉**） | `RelationAssembly` 约束 | 上游未接入即不臆造；需要时由 aDSL 侧从几何反推 `attached/supported` | ❌ |
| `is_symmetric` / `symmetric_axis`（`:175-176`） | `Expr` + `align_anchors` / `distribute_along_axis` | 生成成对镜像约束，参数化表达左右对称（Test C 需要） | ❌ |
| `is_movable` / `must_be_independent`（`:177-178`） | `Joint` / `attach_joint` | 映射到独立运动学树，保留 articulated 能力；`must_be_independent` 禁止实例合并 | ❌ |
| `instances`（`:183`，同构副本） | 组件实例（aDSL 目前无显式概念） | 展开为多个带稳定 ID 的独立 Component（与 SceneCode 禁止 ARRAY/MIRROR 的约束一致） | ❌ |
| `priority`（`:180`） | 依赖图构建顺序 | 作为 `ProgramComponent` 的拓扑排序提示 | ❌ |
| `material: Dict`（`:181`） | `appearance` / Component Material | 存为组件元数据 | ❌ |
| `sub_parts`（`:182`，**仅一层**） | 嵌套 Asset 子节点 | 一层即可映射，无需扩展层级 | ❌ |
| **`code/<pkg>/<part>.py`**（`pipeline.py:434-447`） | **`ProgramComponent`** | **原样承接源文件内容 + 路径 + 参数 schema，不改写、不解析成 JSON 字段** | ❌ |
| `ActionTrace.final_code`（`:341`） | 程序版本历史 | 作为 `ProgramComponent` 的 revision | ❌ |
| `GeneratedGeometry`（`dataclasses.py:97`）<br>`object_plan_path/code_dir/pipeline_result_path/full_object_render_path` | aDSL 组件索引 | 全部作为只读 `upstream_refs` 保存，不重命名、不移动 | ❌ |
| `AssetRegistry` / `house_state.json`（`README.md:210`） | `upstream_snapshot.json`（冻结） + aDSL Registry | 快照只读；编辑期以 aDSL `component_id` 为主键 | ❌ |
| `URDF / SDF`（`code_articulated_pipeline.py:165`） | `Joint` 运动学树 | 解析为关节规格，几何仍由 ProgramComponent 提供 | ❌ |
| `scene_states/<stage>/scene_state.json` 快照 | 阶段 lineage | 只记录来源，不作为活跃状态 | ❌ |
| Blender 渲染图 `renders/full_object.png` | Visual Critic 输入 | aDSL 侧重新渲染并送 DeepSeek Flash 评审 | ❌ |

---

## 3. `ProgramComponent` 契约（P1，待实现）

**任务明确禁止**：「不要直接把任意 Blender Python 字符串塞进一个 JSON 字段，然后宣称已经实现 aDSL」。

因此 `ProgramComponent` 不是「一个装代码的字符串」，而是一个**带语义、参数、坐标系、依赖和校验的可编译组件**：

```python
@dataclass
class ProgramComponent:
    component_id: str            # = SceneCode PartPlan.name，稳定、不可变
    source_path: Path            # 上游 code/<part>.py，只读引用
    source_sha256: str           # 程序版本指纹，用于增量执行的脏判定
    entrypoint: str              # SceneCode 约定的构建函数名，如 create_backrest()
    params: Dict[str, ParamSpec] # 结构化参数表（名称/类型/单位/范围/默认值）
    local_origin: Vec3           # 局部原点（由世界坐标推导，aDSL 侧完成）
    local_frame: Mat4            # 局部坐标系 4x4
    output_aabb: AABB            # 执行后实测包围盒，供 relations 求解
    depends_on: Tuple[str, ...]  # 依赖的其他 component_id
    upstream_refs: Dict[str, Path]  # ObjectPlan / pipeline_result / render 原路径
    revisions: List[ActionTrace] # SceneCode 的修复历史
```

关键约束：
- `params` 是**结构化参数表**，不是自由 dict。`Test A`（椅背 −5cm）、`Test C`（扶手 +3cm 且左右对称）必须能通过修改具名参数完成，而不是重新生成整份代码。
- `source_path` 是**引用**，不是把代码搬进 JSON。aDSL AST 存的是「哪个程序 + 什么参数 + 什么坐标系 + 依赖谁」。
- `compile()` 产出 Blender Python 执行片段，`execute()` 交给增量执行器，二者分离。
- 若上游生成的 `.py` 无法满足 `entrypoint` / `params` 契约，摄取器**明确报错并列出缺失项**，不静默降级成几个立方体。

### 3.1 SceneCode 生成的代码能否被包装？

**不能直接自动包装，但也不需要改 SceneCode。**

审计已确认 part-wise 代码是 LLM 自由生成的任意 `bpy` 字符串，`PartShape` 只是提示词输入。解决办法放在**摄取侧 + 提示词侧**，都在 aDSL/桥接层：

1. aDSL 摄取器对每份 `<part>.py` 做静态分析（AST），提取顶层 `create_*()` 函数、字面量常量、尺寸相关表达式 → 生成 `ParamSpec` 候选。
2. 无法静态提取的参数，标记为 `opaque`，并**明确记录该组件不支持参数级编辑**（Test A/C 只能选可参数化的组件，验收里如实标注）。
3. 通过网关在 **Planner/Constructor 的 prompt 里追加一段契约说明**（不改 SceneCode 源码，只在网关注入 system prompt 增补），引导 LLM 生成的程序暴露稳定的 `create_<part>(params)` 签名与具名尺寸常量。这是「适配层适应上游」而非「改造上游」。
4. `[待运行验证]` 该引导在 DeepSeek Flash 下的遵循率。

---

## 4. 稳定 ID 规则（已实现）

```
component_id := PartPlan.name                    # 优先，上游已有
             := stem(code/<part>.py)            # 兜底
             := attach_part 的 key              # 纯 aDSL 构造时
```

- **不生成 UUID、不加前缀、不重命名**，保证 aDSL 与 `house_state.json` / `code/` 目录可以互相索引。
- `Asset.attach_part()` 自动绑定；`Asset.copy()` 保留；`asset_to_dict/asset_from_dict` 往返保持。
- 重复 ID 在 `component_ids()` 处报错（增量执行器用 ID 作 Blender 对象键，不能静默覆盖）。
- 旧 JSON 无 `component_id` 时按 label 派生，保证历史 `outputs/*.json` 仍可加载。

已验证：`SceneCode_audit/tests/test_adsl_stable_ids.py` 10 项 + 既有 14 项回归全绿。

---

## 5. 空间关系映射（Test B ）

SceneCode 的 `PartRelation` 在 `from_dict()` 中被注释掉（`object_plan.py:221-222`），所以**上游没有可用的关系数据**。aDSL 不能凭论文假设它存在。

Test B「让茶几与沙发保持居中对齐，同时保证合理的使用距离」的落点：

| 需求 | aDSL 机制 | 说明 |
|---|---|---|
| 居中对齐 | `align_centers`（`layout.py:326`，注意是复数）、`align_anchors`（`:92`） | 修改**关系约束**，不直接改绝对坐标 |
| 使用距离 | `RelationAssembly.bound/within/supported`（`relations.py:83/128/120`） | 以参数化距离约束表达，求解器重算 |
| 不盲目改所有坐标 | `Expr` / `parameter` / `datum`（`relations.py:23/71/77`） | 只重解受影响对象 |
| 局部重求解 | **P4 待实现** | `RelationAssembly.compile()` 目前恒返回全新 scene（`:61` 注释），需支持按稳定 ID 局部重解 |

---

## 6. aDSL 缺失操作与最小扩展清单

| 缺口 | 是否可用现有能力表达 | 最小扩展 | 验证方式 |
|---|---|---|---|
| 承接任意 part-wise 程序 | ❌ 无对应类型 | `ProgramComponent`（§3） | 摄取一份真实 `<part>.py`，能编译并执行出相同几何 |
| 组件级参数编辑 | 🟡 `Expr`/`parameter` 有基础，但绑定不到程序 | `ParamSpec` + `bindings` | Test A / Test C |
| 稳定 ID | ✅ **本轮已补齐** | — | 往返 + copy + 旧 JSON 测试 |
| 双向序列化 | ✅ **本轮已补齐**（核心库 `asset_from_dict`） | — | 同上 |
| 依赖图 / 脏标记 | ❌ 无 | `DependencyGraph`，基于 `component_path()` | 编辑单组件后脏集合 == 预期闭包 |
| 增量 Blender 执行 | ❌ `clear_scene=True` 全量重建 | 按 `component_id` 删除并重建子树，其余 `bpy` 对象保持句柄 | Test A/B/C 的「非目标几何不重新生成」 |
| 关系局部重解 | ❌ compile 恒返回新 scene | `compile(changed_ids=...)` | Test B |
| aDSL ↔ Registry ↔ Blender 一致性 | ❌ 无校验 | 以 `component_id` 为主键的三方 diff | 验收表 State Consistency |
| CSG | ✅ `boolean.py` 已有 | 不需要扩展；但 SceneCode 侧无 CSG，不得凭空映射 | — |
| ExternalMesh | ✅ 已有 | 仅作已有资产引用，不升为主要几何方式 | — |

**扩展纪律**：每项扩展都要写清语义、参数、输入/输出、验证方式（任务 PHASE 4 要求）。上表即初版清单，实现时逐项补规格。

---

## 7. 明确不做的事

- ❌ 不把 SceneCode 的 `ObjectPlan` 改造成 aDSL AST 的同义词（任务原文已警告「不要假设 ObjectPlan 等价于 aDSL」）。
- ❌ 不把复杂对象降级成少量 Cube/Sphere/Cylinder。
- ❌ 不把所有部件合并成不可编辑的单一 Mesh。
- ❌ 不因为引入 aDSL 丢掉 articulated（`Joint` 树已可承接）。
- ❌ 不让局部编辑重新生成整个房间（这正是 P3 必须解决的）。
- ❌ 不把任意 Blender Python 字符串塞进 JSON 字段当作「已实现 aDSL」。
- ❌ 不修改 SceneCode 业务源码来满足上述任何一条。
