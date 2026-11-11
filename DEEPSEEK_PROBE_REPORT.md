# DEEPSEEK_PROBE_REPORT.md — 官方 API 实测报告

> 实测时间：2026-11-08 21:4x（GMT+8）
> 脚本：`bridge/probe_deepseek.py`（能力）、`bridge/probe_compat.py`（协议边界）、`bridge/test_gateway.py`（网关冒烟）
> 原始脱敏结果：`DEEPSEEK_PROBE_RESULT.json`、`DEEPSEEK_COMPAT_RESULT.json`
> **本报告与所有结果文件均不含真实 API Key、prompt 正文或图像字节。**

## 1. `/models` 实测

| 端点 | HTTP | 返回模型 |
|---|---|---|
| `GET https://api.deepseek.com/models` | 200 | `deepseek-flash`, `deepseek-v4-pro` |
| `GET https://api.deepseek.com/v1/models` | 200 | 同上 |

账户可用模型**只有这两个**。任务配置里的 `SCENECODE_TEXT_MODEL=deepseek-flash` / `SCENECODE_VISION_MODEL=deepseek-flash` 与实际一致。`deepseek-chat` / `deepseek-reasoner` 已弃用，未在列表中出现。

## 2. 八项能力实测（全部 200）

| # | 能力 | 结果 | 证据 |
|---|---|---|---|
| 1 | 纯文本 chat | 200，返回 `OK` | `message_keys=[content, role]`，10 tokens |
| 2 | JSON 输出 | 200，返回 `{"probe":"ok"}` | `response_format:{"type":"json_object"}` 生效 |
| 3 | Tool calls | 200，返回 `get_weather` | `message.tool_calls` 存在 |
| 4 | Thinking 模式 | 200，返回 `reasoning_content` + `content` | `thinking:{"type":"enabled"}` |
| 5 | 长文本输出 | 200，303 completion tokens | `max_tokens=700` |
| 6 | **图像输入** | **200，1×1 PNG → `Pink`** | `image_url` + data URI 内容块被接受 |
| 7 | `/models` | 200 | 见 §1 |
| 8 | `/v1/models` | 200 | 见 §1 |

**结论**：`deepseek-flash` 可承担 Planner / Designer / Critic 的文本推理、JSON 结构化输出、tool calls，以及**渲染图的视觉评审**。这满足任务「DeepSeek Flash 可以接收渲染图片用于视觉评审」的前提。任务同时明确「不能把它当作图像生成模型」——实测确认账户没有图像生成端点，网关因此本地生成参考图。

## 3. 七项协议边界实测（含一次真实故障）

**第一次运行**遇到本机代理隧道 `Tunnel connection failed: 502`，7 项中 6 项未到达服务端。这**不是 API 行为**，按「先定位真实原因再解决」给探针加了退避重试后重跑，7 项全部得到真实响应。未把传输失败记录成协议结论。

| # | 探针 | HTTP | 结论 |
|---|---|---|---|
| 1 | OpenAI 专属参数 `reasoning_effort` / `verbosity` / `service_tier` / `vision_detail` | **200** | **被忽略，不报错** → 网关无需强制剥离 |
| 2 | `POST /responses` | **200** | 返回标准 OpenAI Responses 对象（`object:"response"`、`output`、`usage`…）→ `openai-agents` **可直接透传** |
| 3 | `tool_choice:"required"` + thinking enabled | **400** | `Thinking mode does not support this tool_choice` → **唯一必须改写的规则** |
| 4 | `temperature` + thinking enabled | 200 | 不报错（仅无效），无需剥离 |
| 5 | `max_completion_tokens` 别名 | 200 | 可直接透传 |
| 6 | Gemini-3 `extra_content.google.thought_signature` 残留 | 200 | 被忽略，无需强制清理 |
| 7 | `response_format:json_object` + thinking enabled | 200，返回 `{"a":1}` | 兼容 |

## 4. 被实测推翻的架构假设（已在架构文档修正）

| `SCENECODE_ADSL_ARCHITECTURE.md` v2 初稿 | 实测 | 修正 |
|---|---|---|
| 「网关剥离 `reasoning_effort` / `verbosity` / `service_tier` / `vision_detail`」 | 200，服务端忽略 | 改为**可选**（`STRICT_PARAMS=1` 才剥离），默认透传 |
| 「清理 Gemini-3 `thought_signature` 避免 400」 | 200，服务端忽略 | 改为不必强制清理 |
| 「`openai-agents` 可能走 `/responses`，需验证并可能转译成 chat/completions」 | `/responses` 200 且格式标准 | 改为**直接透传**，网关大幅简化 |
| 「`temperature` 与 `tool_choice` 需要网关改写」 | `temperature` 200；只有 `tool_choice:"required"+thinking` 400 | 只保留一条改写规则 |

保留的强制改写：`tool_choice == "required"` 且 thinking enabled → 改为 `"auto"`。

## 5. 已实现的网关（`bridge/gateway.py`）

纯 Python 标准库，**零新增依赖**（本机 Python 3.13 无 PIL/numpy/flask）。

| 路由 | 行为 |
|---|---|
| `GET /v1/models` `/models` | 透传 DeepSeek |
| `POST /v1/chat/completions` | 模型名映射 + 一条强制改写 + 透传 |
| `POST /v1/responses` | 同上，直接透传 |
| `POST /v1/images/generations` `/images/edits` | **本地程序化 PNG**，返回 `{"created":…, "data":[{"b64_json":…}]}` |
| 其它 | 404 JSON 错误 |

关键依据：SceneCode 在 `image_generation.py:1035` 取 `response.data[0].b64_json`，因此返回 `b64_json` 就能被**原样消费**，`router.py:1411` 的 `generate_images()` 不会失败、`fallback_backend: flux-klein` 不会触发。PNG 由标准库 `zlib`/`struct` 编码（中性背景 + 家具体块轮廓，按 prompt 哈希确定性着色），**不调用任何外部图像/3D 生成服务**。

模型映射：`gpt-5.4`、`gpt-5.2`、`gemini-3.1-pro-preview` 等 → `deepseek-flash`；已是 `deepseek-v4-pro` 的保留。

脱敏日志 `runtime/outputs/gateway_calls.jsonl` 只记 `ts / method / path / model / status / seconds / note`，不记 key、prompt、图像。key 只从 `runtime/.env`（已 gitignore）读入进程内存。

## 6. 网关冒烟测试（实跑，7/7）

`bridge/test_gateway.py`：

| 测试 | 结果 |
|---|---|
| `GET /v1/models` | PASS，返回两个真实模型 |
| chat 带 `gpt-5.4` + 全部 OpenAI 专属参数 | PASS 200，映射为 `deepseek-flash`，返回 `OK` |
| 强制改写 `tool_choice:required` + thinking（带 `gemini-3.1-pro-preview`） | PASS 200（上游原本 400） |
| `POST /v1/responses`（openai-agents 路径） | PASS 200，`object:"response"` |
| `POST /v1/images/generations` → 本地 PNG `b64_json` | PASS，1334 bytes，PNG 魔数校验通过 |
| `POST /v1/images/edits` → 本地 PNG | PASS |
| 不支持路径返回 404 | PASS |

> 过程中发现我自己写的第 7 项断言恒为 `True`（假测试），已改成真正请求 `/audio/transcriptions` 验证 404 并重跑。按任务纪律「不伪造成功日志」，不留假通过。

## 7. 待验证 / 风险（不写成已完成）

- `[待运行验证]` **程序化参考图对 ObjectPlan 质量的影响**：没有真实参考图时，Planner 是否仍生成多部件精细家具，还是退化成几个立方体。这是 PHASE 2 最关键的实测风险。
- `[待验证]` `openai-agents` 实际发的是 `/responses` 还是 `/chat/completions`、以及 Responses 格式下的 `tool_choice` 对象形态（网关目前只处理字符串 `"required"`）。要等真正跑 SceneCode 才能确认。
- `[待实现]` `stream:true` 的 SSE 逐块转发（当前网关一次性读完上游再返回，短响应可用，长流式未验证）。
- `[待实现]` `/v1/images/edits` 的 multipart 上传解析（当前忽略上传文件，直接返回程序化图，够用但不还原编辑意图）。
- `[待验证]` 真实 SceneCode 全流程下的 token 消耗、超时、`reasoning_content` 多轮回传是否正确。
- 本机代理隧道会间歇性 502，网关目前**没有**对上游传输失败做重试；长时间运行前建议补上。

## 8. 复现命令

```bash
# 能力探测
python E:/3DUse/SceneCode_audit/bridge/probe_deepseek.py
python E:/3DUse/SceneCode_audit/bridge/probe_compat.py

# 启动网关（SceneCode 的 OPENAI_API_BASE 指向 http://127.0.0.1:8765/v1）
python E:/3DUse/SceneCode_audit/bridge/gateway.py --port 8765

# 冒烟测试（另开终端）
python E:/3DUse/SceneCode_audit/bridge/test_gateway.py
```
