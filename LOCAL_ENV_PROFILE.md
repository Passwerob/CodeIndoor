# LOCAL_ENV_PROFILE.md — 本机环境画像与适配决策

> 实测时间：2026-11-08 21:1x（GMT+8）
> 实测方式：只读命令，未安装/修改任何环境
> 目的：PHASE 1「针对本地环境做适配」的事实基础

## 1. 实测底数

| 项 | 实测值 | 证据 |
|---|---|---|
| OS | Windows 11 专业版，Version 10.0.26200，Build 26200 | `Win32_OperatingSystem` |
| WSL 功能 | `Microsoft-Windows-Subsystem-Linux = Disabled`；`VirtualMachinePlatform = Enabled` | `Get-WindowsOptionalFeature` |
| WSL 可执行 | `wsl.exe` **拒绝访问**，被安全中心「程序黑名单」拦截 | 命令返回 `PROGRAM BLOCKED BY SECURITY POLICY` |
| 系统 Python | **3.13.14**（WorkBuddy 3.13.12）、3.13.12、3.10；**无 3.11** | `python --version` / `py -0p` |
| aDSL venv | `E:/3DUse/aDSL/venv/Scripts/python.exe` = **Python 3.13.4** | 直接执行 |
| uv | **未安装** | `uv --version` → command not found |
| conda | 不在 PATH；仅有 `E:/3DUse/conda_pkgs` 缓存目录 | `where conda` |
| Blender | `E:/Blender/blender.exe` = **Blender 5.2.2 LTS**（2026-09-15），**不在 PATH** | `blender.exe --version` |
| GPU | NVIDIA GeForce RTX 5060 Ti，**16311 MiB ≈ 16 GB**，驱动 610.88 | `nvidia-smi` |
| 内存 | 31.8 GB | `Win32_ComputerSystem` |
| 磁盘 | **C: 剩 24 GB / 93% 已用**；**E: 剩 295 GB** | `df -h` |

## 2. 硬阻断与直接后果

### 2.1 WSL2 不可用（双重阻断）

功能本身是 `Disabled`，且 `wsl.exe` 被安全策略黑名单拦截。**该拦截明确不可绕过**，不能换个 shell 或脚本重试。

按任务原文「如果 WSL2 不可用，再评估 Windows 原生部署」→ **必须走 Windows 原生支路**。

> 若后续确实要用 WSL2，唯一合规路径是用户自己在 **Security Center → Command Security → Program Blacklist** 移除 `wsl.exe`，并启用 WSL 功能（需管理员 + 重启）。这属于用户侧操作，我不代为绕过。

### 2.2 Windows 原生的真实可行性

| 依赖 | Windows 原生风险 | 判断 |
|---|---|---|
| `drake>=1.47.0`（pydrake） | Drake 官方主推 Ubuntu/macOS，Windows wheel 覆盖极有限 | 🔴 **最高风险**。需实测 `uv` 能否解析到 wheel；失败则必须裁剪掉 Drake 依赖路径（只保留代码几何 + Blender 导出，不跑 `dmd.yaml` 仿真） |
| `bpy==4.5.4` | 需 `download.blender.org/pypi` 专属 index | 🟡 可装，但与本机 5.2.2 并存，两套 Python API |
| `torch 2.5 / open-clip-torch / diffusers` | 有 Windows CUDA wheel，但 16 GB 显存不足以跑生成式后端 | 🟡 装得上，但**不启用** |
| `coacd / vhacdx / python-fcl / pymeshlab / manifold3d / xatlas` | 多为需编译的 C 扩展，Windows wheel 覆盖不全 | 🟡 需逐个验证；可按需裁剪 |
| `pyvirtualdisplay` | 依赖 Linux Xvfb | 🟡 装得上但无用；headless Blender 在 Windows 可直接跑 |
| `rtree / shapely / opencv` | 有 Windows wheel | 🟢 |
| Blender 启动 | `blender.exe -b --python ...` 在 Windows 可用 | 🟢 |

**结论**：Windows 原生**可跑通「纯代码几何 → Blender 执行 → 渲染」这条主线**；`drake/SDF 仿真导出`这条支线大概率跑不通，需要在配置里显式关闭，并在复现报告里记为「因环境限制未复现」，不能含糊带过。

### 2.3 版本三方不一致

| 来源 | Blender 版本 |
|---|---|
| 本机已有（aDSL 用） | **5.2.2 LTS** |
| SceneCode README 要求 | **4.4.3 Linux**（`blender-4.4.3-linux-x64`） |
| SceneCode `pyproject.toml` | **`bpy==4.5.4`** |

处理原则：**三者都不互相覆盖**。
- aDSL 继续用本机 5.2.2，不动。
- SceneCode 独立目录里放一份 **Blender 4.4.3 Windows portable zip**（官方 `download.blender.org/release/Blender4.4/` 有 Windows 版），通过 `PATH` + `SCENECODE_BLENDER_GLOBAL_LOCK` 指向它。
- `bpy==4.5.4` 只作为 Python 侧 `mathutils/bpy` 模块依赖，不用于启动 Blender 进程；若与 4.4.3 的 Python 3.11 冲突，用 `uv` 的 override 或改为可选依赖。

### 2.4 显存与磁盘约束

- **16 GB 显存** → 论文流水线推荐 45 GB、SAM3D 需 32 GB、Hunyuan3D 需 24 GB，**全部不满足**。这反而与「不使用任何图像/3D 生成模型」的要求一致：必须只走 **Blender Python 程序化几何 + EEVEE 渲染 + DeepSeek 视觉评审**，禁用 `generated` / `articulated` 检索后端、SAM3D、FLUX、CLIP embedding 检索。
- **C 盘只剩 24 GB** → 所有 venv、uv 缓存、pip 缓存、Blender portable、模型/数据、输出**一律落 E 盘**。必须显式设置 `UV_CACHE_DIR`、`UV_PYTHON_INSTALL_DIR`、`PIP_CACHE_DIR`、`HF_HOME`、`TMP`/`TEMP`，否则默认写 C 盘会撑爆。

## 3. 适配方案（隔离、不改现有环境）

### 3.1 目录约定（全部在 E 盘）

```
E:/3DUse/SceneCode_audit/            # 独立工作目录（已存在）
  ├─ sources/                       # SceneCode 源码快照（审计用）
  ├─ tests/                         # aDSL 改造的回归测试（已存在）
  ├─ env/                           # 环境脚本与配置
  ├─ bridge/                        # 本地兼容网关 + aDSL 适配层（待实现）
  └─ runtime/                       # 独立 venv / Blender portable / 输出
       ├─ .venv-scenecode/          # Python 3.11，uv 创建
       ├─ blender-4.4.3-windows/    # 独立 Blender
       ├─ cache/                    # uv / pip / HF 缓存
       └─ outputs/                  # 运行产物
```

### 3.2 Python 3.11 的获取方式

**不安装系统 Python、不碰 3.13 / 3.10 / aDSL venv**。用 `uv` 的托管 Python：

```bash
uv python install 3.11            # 装到 UV_PYTHON_INSTALL_DIR（E 盘）
uv venv --python 3.11 runtime/.venv-scenecode
```

`uv` 本身也不改系统：下载独立二进制到 `E:/3DUse/SceneCode_audit/runtime/uv/`，只用绝对路径调用。

### 3.3 必须修掉的两个仓库级问题（在独立副本里改，不动上游语义）

1. `pyproject.toml:62` `packages = ["scenesmith"]` → `["scenecode"]`（否则 `uv sync` 装错包名）。
2. `pyproject.toml:76` `known_first_party = ['scenesmith']`（isort 配置，仅影响格式检查）。

这两处属于**打包元数据残留**，不是 SceneCode 的架构/契约改动，与「aDSL 适应 SceneCode、不改 SceneCode」不冲突。

### 3.4 环境变量（全部走 `.env`，加入 `.gitignore`，不含真实 key）

```
OPENAI_API_KEY=<从 .env 注入，禁止写入代码/日志/报告>
OPENAI_API_BASE=http://127.0.0.1:<bridge_port>/v1      # 指向本地兼容网关，而非直连 DeepSeek
SCENECODE_TEXT_MODEL=deepseek-flash
SCENECODE_VISION_MODEL=deepseek-flash
PATH=<E:/.../blender-4.4.3-windows>;%PATH%
SCENECODE_BLENDER_GLOBAL_LOCK=E:/.../runtime/blender.lock
UV_CACHE_DIR / UV_PYTHON_INSTALL_DIR / PIP_CACHE_DIR / HF_HOME = E:/.../runtime/cache/*
SCENECODE_OUTPUT_DIR / SCENECODE_MATERIALS_DIR = E:/.../runtime/outputs/*
SAM3_CHECKPOINT / SAM3D_PIPELINE_CONFIG / FLUX_PYTHON / FLUX_MODEL_PATH / GOOGLE_API_KEY = 空（全部禁用）
```

**关键**：`OPENAI_API_BASE` 指向本地网关而不是 `https://api.deepseek.com`。原因见 `SCENECODE_ADSL_ARCHITECTURE.md` §3 —— 这样 SceneCode 两套 LLM 栈、图像生成调用、`images.generate` 全部不用改一行源码就能被适配层接管。

### 3.5 尚未执行的操作（避免误伤环境）

本轮**只做了只读探测和 aDSL 核心代码改造 + 测试**，以下均未执行，等确认后再跑：

- 安装 uv / 下载 Python 3.11 / 创建 `.venv-scenecode`
- 下载 Blender 4.4.3 Windows portable
- `uv sync`（78 MB 依赖 + 多个需编译扩展，网络不稳，可能长时间失败）
- 任何 `pytest` / `main.py` / Blender / DeepSeek 调用

`[待运行验证]`：pydrake 在 Windows + Python 3.11 是否有可用 wheel；这是决定「能否保留 SDF 仿真导出」的分叉点。

## 4. 基线记录（aDSL 改造前后对比）

| 测试 | 改造前 | 改造后 |
|---|---|---|
| `test_adsl_direct.py` | 通过 | 通过 |
| `scripts/test_relations.py` | 14 tests OK | 14 tests OK |
| `SceneCode_audit/tests/test_adsl_stable_ids.py` | 不存在 | **10 tests OK** |

改造文件（均在 `E:/3DUse/aDSL/adsl-core/core/`）：
- `asset.py`：新增 `component_id`（`kw_only` 追加末尾）、`attach_part` 绑定 part key、`copy()` 保留 ID、`component_ids/find_component/component_path/effective_component_id/from_dict`
- `serialize.py`：`asset_to_dict` 增加 `component_id`（附加字段，旧 reader 忽略）；新增 `asset_from_dict`
- `__init__.py`：导出 `asset_to_dict` / `asset_from_dict`
