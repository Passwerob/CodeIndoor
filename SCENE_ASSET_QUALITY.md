# 单件资产质量根因与修复

针对「单个资产太烂，FineScene 比这好多了」的诊断与修复记录。

## 1. 根因：不是材质 bug，是取错了几何源

分三层，逐层验证过：

**第一层 — 借错了源。**
`assemble_scene.py` 从 `FineScene/deliveries/reception_lounge_adsl_20261104/build_reception_lounge.py`
取几何。那个脚本的存在目的是**验证 aDSL AST 可编辑性**：每个 part 一个命名
primitive，方便 `attach_part` 和局部编辑测试有东西可咬。它的形体被刻意压到最简，
从来不是质量目标。用它组装客厅，等于把 aDSL 的编辑能力当成了建模能力。

**第二层 — aDSL 真正的单件生成能力没上场。**
`aDSL/outputs/medieval_armchair_20261017`（The Oakwarden）是 200 原语 → 156 mesh
+ 10 曲线，车木腿、菱形软包扣、铜铆钉、程序化木纹与皮革 PBR。这条链路在客厅组装
里一次都没调用。

**第三层 — 材质退化成单色。**
`build_blend.py::material_for()` 只给纯色。而 FineScene 有
`realistic_refiner.py::surface()`（diff/rough/arm/normal 全套 2k PBR + BOX 投影）
和 `assets/external/` 里 19 个 CC0 扫描资产，全部没用上。

## 2. 修复后的单件供给（三级，按保真度降序）

| 级别 | 来源 | 本场景用量 |
|---|---|---|
| retrieved | Poly Haven CC0 扫描资产 + 原生 2k PBR | 7 件（茶几、电视柜、扶手椅×2、边几、盆栽、挂画） |
| generated | Agent 作者程序 → FineScene Builder DSL → 双门禁 | 4 件（沙发、落地灯、吊灯、书架） |
| procedural | 剖面 + 程序化 PBR（无体量可言的软装） | 2 件（地毯、窗帘） |

`asset_supply.py` 是解析器，每条映射都带理由，可审计。
`furniture_forge.py` + `forge_runner.py` 是生成链路。

## 3. 关键发现：FineScene 的家具不是四个工厂做的

`adsl_components.FACTORIES` 只有 bookshelf / shelf / desk / chair 四种，
每种编译成几个盒子 —— 这是"积木感"的直接来源。

FineScene 自己的好家具来自 **`baroque_lib.Builder` + 装饰件生成器**：
`volute`（涡卷）、`cabriole_leg`（卡布里腿）、`acanthus_leaf`（茛苕叶）、
`torus`、`fluted_column`、`corinthian_capital`、`dentil_row`，叠在真实 2k PBR 上。

所以 `forge_runner.py` 把这套 DSL 直接交给 Agent，由 Agent 自己写部件表：

```
def build(B, mats, P):
    B.box("plinth", (w, d, 0.09), (0, 0, 0.15), "oak_joinery", bevel=0.010)
    cabriole_leg(B, "leg_w_front", (-0.9, -0.35, 0), 0.14, "oak_joinery")
    ...
```

`forge_runner.py` 不含任何家具几何。

## 4. 门禁：两道，且补了一条确定性检查

**component 级（FineScene 原装 `general_vision.review_images`, scope='component'）**
4/4 通过：沙发 2 轮、落地灯 2 轮、吊灯 1 轮、书架 1 轮。

**确定性检查（新增，因为视觉门禁漏过了一次严重错误）**
一张全白、零材质的沙发被视觉门禁判 `passed`。原因：Agent 把
`mats["oak_joinery"]`（材质对象）直接传给 `B.box`，而 `Builder._register` 只认
字符串 key，静默跳过 → 所有 mesh 无材质，渲染成纸白。

现在 `forge_runner` 报 `unshaded_count`，`furniture_forge` 在进视觉门禁**之前**
就拒绝：无材质部件、超出包络 >10%、部件数 < 12。

**scene 级**：`needs_revision`（未通过）。具体项：
- 电视柜与沙发朝向关系不清（剖切视图导致的空间误读）
- 挂画在多数视角不可见（在西墙，被书架遮挡）
- 部分视角下盆栽贴墙关系可疑

这些是摆放/取景层，不是资产质量层。如实记录，不标为通过。

## 5. 门的另一侧：间距求解器

`validate_layout` 只拒绝 XY **重叠**（3 cm 容差）。据此
`coffee_table` 后缘正好压在沙发前缘：过得了重叠测试，但作为一个房间是错的
—— 人站不起来。

`clearance_solver.py` 补功能间距（座位前 38 cm、椅子到边几 6 cm、落地灯到书架
12 cm），用局部搜索而非逐轴推挤（逐轴推挤会在被夹住的物件上来回振荡）。
它是求解器不是改数字：规则是语义化的，对任何 layout 生效。

求解结果：3 处调整，7 条规则全过。

## 6. 实测产物

| 产物 | 路径 |
|---|---|
| 场景 blend（可编辑层级 + 相机 + 灯光） | `runtime/outputs/living_room/assets/living_room_assets.blend` |
| 最终 4 视图 | `runtime/outputs/living_room/assets/v2_view_00{0..3}.png` |
| Agent 程序（可读、可复跑） | `runtime/outputs/forge/<id>/program.py` |
| 单件 blend | `runtime/outputs/forge/<id>/<id>.blend` |
| 组装报告 | `runtime/outputs/living_room/assets/ASSET_SCENE_REPORT.json` |
| 供给计划 | `runtime/outputs/scene_layout/supply_plan.json` |
| 间距报告 | `runtime/outputs/scene_layout/clearance_report.json` |

组装：11 件（7 retrieved + 4 forged）+ 2 件 procedural，0 错误。

## 7. 尚未解决

- `ceiling_light` 挂点在 z=2.90 由常量决定，没有从 shell 实测天花板高度。
- 沙发靠背偏薄，坐垫厚度是靠视觉门禁拉上来的，不是几何规则保证的。
- `tv_cabinet` 复用 `WoodenTable_01` 压扁，勉强读作矮柜；资产库缺真正的媒体柜。
- 挂画的可见性没有纳入取景决策。
- 场景级门禁仍未通过。
