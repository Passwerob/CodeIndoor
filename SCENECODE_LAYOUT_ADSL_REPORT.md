# SceneCode 场景组织 × aDSL 单件生成 —— 结合落地报告

> 目标（用户原话）：**「我想要的是 SceneCode 的场景组织和摆放能力，结合我们调整好的 aDSL 的单个生成能力」**。
> 本文只记录真实跑通的结果与真实限制；未跑的一律标注，不把链路跑通宣称为论文复现。

---

## 1. 分工契约（本轮的核心）

| | SceneCode 侧 | aDSL / FineScene 侧 |
|---|---|---|
| 职责 | 房间里有什么、分几阶段、放哪儿、朝向、彼此关系、动线、评审回灌 | 每件东西长什么样、可编辑部件层级、材质与几何 |
| 产物 | `layout.json`（`scenecode.layout.v1`） | `living_room.adsl.json`（aDSL AST）+ `.blend` |
| 事实源 | **生成期** SoT | **编辑期** SoT（阶段隔离，已确认） |
| 禁止 | 产出任何几何 / bpy 代码 | 决定物体之间的空间关系 |

桥接层只做一件事：把 SceneCode 的 `placement{x,y,z,yaw_deg}` 通过 FineScene 自己的
`apply_pose(asset, mat4(pos, yaw))` 盖到每个 aDSL 对象的 `xform` 上。**没有新写任何几何。**

### SceneCode 契约的真实来源（读的是源码提示词，不是论文描述）

- `scenecode/prompts/data/floor_plan_agent/designer_agent.yaml` → `generate_room_specs()`：房间尺寸、门窗开口
- `scenecode/prompts/data/furniture_agent/stateful_planner_agent.yaml` → Planner 编排 Designer + Critic + 迭代；**「Designer can only control x, y position and yaw rotation」**；`select_placement_style(natural|perfect)`
- `scenecode/prompts/data/asset_router/request_analysis_furniture.yaml` → `AssetItem(description, short_name, dimensions, object_type, strategies)`
- `scenecode/agent_utils/room.py` → `SceneObject` / `PlacementInfo(parent_surface_id, position_2d, rotation_2d)` / `RoomScene.add_object()` / `to_state_dict()`
- `scenecode/experiments/indoor_scene_generation.py` → 五阶段 `floor_plan → furniture → wall_mounted → ceiling_mounted → manipuland`

### aDSL 几何的真实来源（全部复用，未重写）

- `FineScene/deliveries/reception_lounge_adsl_20261104/build_reception_lounge.py`：13 个现成可编辑 aDSL 对象（房间壳、沙发、茶几、地毯、两把单椅、边几、落地灯、书架、绿植、挂画、窗帘、吊灯），每个部件一个命名原语
- 同目录 `build_blend.py`：可编辑对象层级、材质、灯光、剖切/室内相机，**以及内置的局部编辑验证测试**
- `FineScene/src/adsl_components.py::build_desk`：TV cabinet 由这台参数化工厂按 `AssetItem.dimensions` 编译（LLM 只给数值）
- `FineScene/src/general_vision.py::review_images(scope='scene')`：整场视觉 + 布局审计硬门禁
- 两套 `Asset` 类（`adsl.core.asset.Asset` 与 `core.asset.Asset`）通过 aDSL 自己的 `asset_to_dict → asset_from_dict` 往返转换，未手工搬运

---

## 2. 架构修正：为什么改成「逐个放置」

第一次实现让 LLM 一次性吐出 14 个物体的全部坐标，几何校验反复失败（9 次尝试，2–7 条违规）。
根因不是模型弱，是**我违反 SceneCode 的真实机制**：它的 Designer 是通过工具**逐个放置**并在每次放置后立即做物理可行性检查。

改成顺序放置后一次通过：

```
request analysis + floor plan  →  inventory (14 AssetItems + relations + intents)
        ↓
Designer 逐个放置（14 次工具式调用）
        ↓  每次：确定性校验（房间边界 / XY 重叠 / 门廊 / 沙发-茶几对齐 / 朝向 / 贴墙）
        ↓  失败只把具体冲突回灌给「当前这一个物体」，最多 4 次
        ↓
Critic 评整体 → 意见回灌 → 重放置 → 保留最优（validator PASS 优先，再比分数）
        ↓
upstream_snapshot.json 冻结（只读）
```

期间还修了两个**读真实代码才发现的契约错误**：

1. 挂墙件旋转后的 AABB 会穿过墙面，套用室内边界属误杀 → 改为专门的挂载检查。
2. FineScene 的吊灯/窗帘部件**局部已建在 z=2.30–2.90 / 1.62–2.44**，整体平移 z 必须为 0；我原先要求 z≥1.9 会把吊灯顶到 4.5 m → 已按 manifest 改准。
3. aDSL 沙发面朝 -Y，单椅 west 朝 +X(yaw 90)、east 朝 -X(yaw −90) → 把朝向写进 Designer 约束，避免 LLM 让沙发背对茶几。

---

## 3. 实跑结果（Blender 5.2.2 / DeepSeek 网关 / aDSL venv）

### 布局（`scene_layout.py`）

| 项 | 结果 |
|---|---|
| 对象 / 阶段 | **14 个对象**，覆盖 floor_plan / furniture / wall_mounted / ceiling_mounted |
| 几何校验 | **PASS，0 违规**（房间边界、XY 重叠、门廊、沙发-茶几居中与间距、朝向、贴墙） |
| 放置 | 22 次工具式调用（含即时回灌修复），一次生成全部 14 个 |
| Critic | 2 轮迭代；第 1 轮 6 分、第 2 轮 3 分且丢失 TV 柜 → **系统按「保留最优」选回第 1 轮** |
| 最终布局 | 沙发 (0,1.30) yaw 0 正对茶几 (0,0.55) 与 TV 柜 (0,−1.00)；单椅左右对称；书架贴西墙 |

### 组装与渲染（`assemble_scene.py`）

| 项 | 结果 |
|---|---|
| 位姿盖章 | **14/14，missing=[]**；TV 柜由 `build_desk` 工厂按 1.8×0.45×0.55 编译 |
| 可编辑层级 | **14 个对象 / 137 个独立 mesh 部件**（`build_blend.py` 导入） |
| 局部编辑验证（FineScene 内置） | `isolated_part_edit` ✓（改一个坐垫 0.30 m，其他部件位移 **0**）；`grouped_object_edit` ✓（移动 sofa 根 0.40 m，其他对象位移 **0**）；`all_passed=True` |
| 渲染 | 4 张室内/剖切视角（Cutaway / 门内 / 西南俯视 / 北侧回看），1100×700 Cycles CPU |
| 产物 | `living_room.blend`（300 KB）、`living_room.adsl.json`（175 KB）、`SCENE_ASSEMBLY_REPORT.json` |
| 整场硬门禁 | 初版 **`needs_revision`**，1 条 blocking：两把扶手椅没对称围合茶几 |

> 第一次渲染只拍到一个白盒子（外部相机被天花板/墙挡住），已改为复用 FineScene 自己的
> 剖切/室内相机配置；挂画在剖切视角浮空的问题也一并修掉（西墙隐藏时同步隐藏该墙挂件）。

### 局部编辑（`local_edit.py`）—— aDSL 是编辑表示，不是导出格式

触发：FineScene 场景级 QA 的 blocking finding，点名 4 个组件。

- LLM 只输出**数值**修正：`armchair_west 90→−90`、`armchair_east −90→90`（沙发/茶几判定无需改动）
- 只重写这 2 个组件的 `xform`；**其余 12 个组件 AST JSON 的 SHA-256 全部不变**
  （`non_target_preserved=True`，`non_target_violations=[]`）
- 从编辑后的 AST 重渲染 4 视角 + `living_room_edited.blend`
- 重新评审：**`needs_revision → passed`，`accepted=True`**
- 目视确认：两椅现在对称朝内围合茶几，沙发/茶几/地毯/书架/灯/植物/窗/吊灯/挂画均未变

对应 PHASE 5 验收指标：Semantic IDs ✓、Localized Update ✓、Program Persistence ✓、
Relation Preservation ✓、Blender Output ✓、Visual Quality ✓、State Consistency ✓（aDSL AST 单一可写 SoT）。

---

## 4. 真实限制（不粉饰）

1. **几何是 FineScene 现成的简化参数化 aDSL 资产**，不是 `medieval_armchair` 那种 9 分 PBR 精修件。
   结构正确、部件可编辑、材质分层清楚，但仍是体块风格；`present.py` 那层表面精修尚未通用化。
2. **Critic 仍给 6 分 `needs_revision`**（12 条意见，主要是沙发与后墙窗的关系、TV 柜在门内视角只露台面）。
   整场 QA 的 blocking 已通过局部编辑修掉，但布局层意见没有全部吸收。
3. **TV cabinet 是 `build_desk` 工厂的矮柜**，不是专为电视柜写的几何；这是按 FineScene 工厂契约复用的结果。
4. **没有跑 SceneCode 原生流水线**：本轮是「按 SceneCode 真实契约复刻场景规划层 + 用 aDSL 承接几何」，
   没有 `uv sync` / Python 3.11 / Blender 4.4.3 / Drake / pydrake。WSL2 被本机策略禁用，独立环境按约定等代码写完再装。
5. **阶段隔离的代价**：进入编辑期后 SceneCode 的 `resume_from_path` / checkpoint 回滚不再使用，
   `upstream_snapshot.json` 只读冻结，视觉评审闭环在 aDSL 侧重接。
6. **manipuland 阶段为空**：本轮 14 个对象覆盖前四阶段，桌上小摆件未生成。

## 5. 交付文件

| 文件 | 内容 |
|---|---|
| `adsl_adapter/scene_layout.py` | SceneCode 五阶段 / 逐件放置 / 确定性校验 / Critic 迭代 |
| `adsl_adapter/assemble_scene.py` | 位姿盖章、复用 FineScene 资产树与室内渲染、整场 QA |
| `adsl_adapter/local_edit.py` | Critic 驱动的局部 AST 编辑 + 非目标哈希验证 + 重渲染重评审 |
| `runtime/outputs/scene_layout/layout.json` | 最终布局 + 契约来源 + 迭代历史 |
| `runtime/outputs/scene_layout/upstream_snapshot.json` | 冻结的只读上游状态 |
| `runtime/outputs/living_room/living_room.adsl.json` | 初始 aDSL SoT |
| `runtime/outputs/living_room/living_room_edited.adsl.json` | 局部编辑后 aDSL SoT |
| `runtime/outputs/living_room/interior/*.png` + `living_room.blend` | 初始 4 视角与工程 |
| `runtime/outputs/living_room/interior_edited/*.png` + `living_room_edited.blend` | 编辑后 4 视角与工程 |
| `runtime/outputs/living_room/LOCAL_EDIT_REPORT.json` | 局部编辑验收记录 |

## 6. 下一刀建议

1. 把 `present.py` 的精修层通用化（`realistic_refiner.surface` + 倒角 + PBR 节点），让**新组装的家具**也能达到 `medieval_armchair` 的质量，而不是只能复现已有精修资产。
2. 把布局 Critic 的 12 条意见结构化回灌到单个对象（当前只在 intent 文本里回灌，效果有限），并接上 `manipuland` 阶段。
3. 再之后才装独立环境跑 PHASE 2 原生复现。
