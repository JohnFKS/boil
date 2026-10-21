# Boil · 烧水

> **开源 AI 学习操作系统。AI 负责准备，你负责烧开。**
>
> 把任何学习材料编译成「路径 → 预消化 → 苏格拉底 → 费曼 → 间隔复习」的可验证闭环，
> 用学习证据而不是内容消费来衡量效果。

[![Python](https://img.shields.io/badge/Python-3.10%2B-0b5f53?style=flat-square&logo=python&logoColor=white)](#)
[![Dependencies](https://img.shields.io/badge/runtime%20deps-zero-ff7a45?style=flat-square)](#)
[![License](https://img.shields.io/badge/license-Apache--2.0-f2c14e?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-29%20passing-38d6bd?style=flat-square)](#测试)

![Boil Dashboard](screenshots/dashboard.png)

---

## 这是什么

大多数 AI 学习产品在帮你**生成内容**：总结、笔记、出题、课程。
Boil 在帮你**证明掌握**：30 天后还能不能提取、解释、应用、迁移。

它不是又一个 AI 家教，也不是 Anki 的皮肤。它把学习拆成五个可执行动作，并用同一个学习证据层把它们串起来：

| 闭环 | Boil 做什么 | 背后的学习科学 |
|---|---|---|
| **1. 路径** | 目标访谈 → 诊断 → 前置 DAG → 每日计划；时间不够时生成“最少必要路径”并明确暂存 | 掌握学习、最近发展区、能力边界 |
| **2. 预消化** | 一句话本质、类比、例子、步骤、边界、误解、FAQ、探针 | 认知负荷、精细化加工、双重编码 |
| **3. 苏格拉底** | 一次只问一个问题；提示阶梯；不先给答案；对话结束生成证据报告 | 苏格拉底提问、生成式学习、元认知 |
| **4. 费曼** | 讲给 10 岁小孩 / 同学 / 阅卷人；六维评分；缺口与术语清单 | 费曼技巧、自我解释、教学相长 |
| **5. 复习** | FSRS-5 四档排程；卡片只是探针的一种；保留率与到期预测 | 检索练习、间隔重复、合意困难 |

核心设计公理：

1. **先测后教**：任何新内容，先让用户尝试，再给讲解。
2. **先答后示**：苏格拉底模式在用户尝试前不直接给答案。
3. **以教代学**：费曼讲解不是“总结”，是带评分标准的公开输出。
4. **以证据代自评**：不问“懂了吗”，只看回忆、解释、应用、迁移四类证据。
5. **慢就是快**：一个单元没有烧开，不急着进入下一个。

> Boil 把《学神模型》的 1234 体系作为产品骨架：
> 慢就是快 → 掌握门禁；知识与题型 → 知识图谱 + 探针；主体/能力/时间 → 数据归用户、能力边界、10/30/60 分钟模式；
> 粉末化/导图/高频回测/极致对标 → 预消化、知识地图、苏格拉底 + FSRS、费曼评分。

---

## 快速开始

### 零依赖本地运行

只需要 **Python 3.10+**，不需要 pip install、Node、数据库服务。

```bash
python3 run.py
# 打开 http://127.0.0.1:7860
```

首次启动会创建一个本地用户 `local`，数据落在 `data/boil.db`。
不配置任何模型也能完整体验：演示模式使用本地确定性引擎（切分、追问、关键词评分、FSRS 排程），
它不会伪装成语义模型；所有页面会明确标注“演示模式”。

### 配置真实模型

打开 **设置 → 模型连接**，填入任意 OpenAI 兼容端点：

- OpenAI
- DeepSeek
- 通义千问（DashScope 兼容模式）
- Moonshot Kimi
- Ollama（本地）
- 自定义端点

也可以使用环境变量：

```bash
export BOIL_LLM_KEY=sk-xxx
export BOIL_LLM_BASE_URL=https://api.deepseek.com/v1
export BOIL_LLM_MODEL=deepseek-chat
python3 run.py
```

密钥只写入 `data/llm.json`（0600），不会返回给浏览器，也不会进入导出包。
失败会在设置页与 `/api/health` 中显式显示，不会静默降级成“假模型回答”。

### Docker

```bash
docker compose up --build
# http://127.0.0.1:7860
```

### 远程访问

默认只绑定 `127.0.0.1`。如果要暴露到局域网/公网：

```bash
export BOIL_HOST=0.0.0.0
export BOIL_TOKEN='换成一个长随机串'
python3 run.py
```

前端会提示输入访问令牌。生产环境请再加 TLS 反向代理；当前版本是**单用户本地优先**，
还不是多租户 SaaS，详见 [SECURITY.md](SECURITY.md)。

---

## 页面

| 今日 | 知识地图 |
|---|---|
| ![Dashboard](screenshots/dashboard.png) | ![Map](screenshots/map.png) |

| 苏格拉底 | 费曼挑战 |
|---|---|
| ![Socratic](screenshots/socratic.png) | ![Feynman](screenshots/feynman-result.png) |

| 复习 | 洞察 |
|---|---|
| ![Review](screenshots/review.png) | ![Insights](screenshots/insights.png) |

| 内容库 | 开始 |
|---|---|
| ![Library](screenshots/library.png) | ![Onboarding](screenshots/onboarding.png) |

---

## 功能

- **目标与路径**：截止日期、每日分钟数、前置 DAG、最少必要路径、暂存单元、动态重排。
- **内容编译**：把 Markdown/纯文本编译成知识单元、探针、卡片；有模型时做结构抽取，无模型时做确定性切分。
- **知识地图**：可缩放/平移/筛选的 SVG 图谱；节点状态：未开始、学习中、已烧开、需复习。
- **苏格拉底对话**：SSE 流式；一次一问；提示阶梯；阶段推进；结束生成四维证据报告。
- **费曼挑战**：三种讲解对象；计时；六维评分；缺口、术语、参考讲解、追问。
- **间隔重复**：FSRS-5；四档评分；每个评分显示下次间隔；复习队列；未来 7 天预测。
- **学习证据**：回忆/解释/应用/迁移四维雷达；30 天保留率；热力图；苏格拉底缺口聚合。
- **内容包（Learning Pack）**：安装/版本/幂等；更新内容不重置学习进度；JSON 导入/导出。
- **本地优先**：SQLite；数据导出；API Key 本地存储；无强制账号。
- **模型无关**：OpenAI 兼容端点；普通 + 流式 + JSON 模式；失败可见。
- **零运行时依赖**：Python 标准库后端；自研前端；KaTeX 本地化，离线可用。

---

## 三个原项目的合并方式

Boil 不是把三个仓库塞进一个目录，而是抽取能力、重建领域模型：

| 原项目 | 保留/借鉴 | 在 Boil 中的位置 |
|---|---|---|
| **mem** | FSRS 调度、复习日志、批注/速记的产品思路、快速捕获 | `boil/srs/fsrs.py` 重写为四档 FSRS-5；`review_logs` / `cards` / `evidence` |
| **examgen** | 五字段拆解、本质匹配、模板树、三道质检、内容寻址缓存 | `content/packs/math-calculus-ch5.json` + `tools/import_examgen.py`；Pack 规范 |
| **testlearnweb** | SSE 流式、skill 加载、记忆分层、引用、检索、错误可见性纪律 | 重写为 `boil/socratic.py`、`boil/feynman.py`、`boil/llm.py`、SPA 运行时 |

**没有继承**：testlearnweb 的第三方前端构建产物、zeroflow 接口契约、双 SRS、文件型 JSON 主存储、无鉴权默认。
前端从零重写；内容包只包含作者生成的模板结构，不包含 `date1/` 原始教辅。

合并后的统一契约：

- `KnowledgeUnit`：知识单元
- `Probe`：探针（回忆/解释/应用/边界）
- `Evidence` / `Mastery`：证据与四维掌握度
- `Card` / `ReviewLog`：FSRS 卡片与复习日志
- `LearningPack`：内容包
- `SourceRef`：来源与版本
- `Event`：追加式事件日志

---

## 架构

```
┌──────────────────────────────────────────────────────────────┐
│ Apps: Web / MCP / CLI（未来）                                 │
├──────────────────────────────────────────────────────────────┤
│ Coaching Skills                                              │
│ Path · Digest · Socratic · Feynman · Review · Capture        │
├──────────────────────────────────────────────────────────────┤
│ Learning Kernel（稳定）                                       │
│ Graph · Evidence · Planner · Scheduler · Session · EventLog  │
├──────────────────────────────────────────────────────────────┤
│ Content Compiler                                             │
│ Ingest → Segment → Extract → Generate → QA → Pack            │
├──────────────────────────────────────────────────────────────┤
│ Adapters: LLM Gateway · SQLite · Search · Auth · Export      │
├──────────────────────────────────────────────────────────────┤
│ Packs: Subject / Content / Skill / Evaluator                 │
└──────────────────────────────────────────────────────────────┘
```

- **内核不关心中文、数学或考研**；它只存图、记证据、算掌握、排计划、发事件。
- **可变部分全部外置成 Pack**：学科本体、内容、提示词、评分器、黄金测试都有版本、可回滚。
- **事件溯源**：每次学习行为只追加、不覆盖；未来可回放历史，用新算法重算，而不动用户数据。
- **契约优先**：JSON Schema / 数据模型 / API 在 [PACK_SPEC.md](PACK_SPEC.md) 与 [API.md](API.md) 中定义。

详见 [ARCHITECTURE.md](ARCHITECTURE.md)。

### 为什么这样设计“随时更新”

1. **Kernel 稳定**：学习单元、证据、排程的抽象不随学科变化。
2. **Pack 可热更新**：内容、提示词、rubric、评分器独立升级；不需要发布整个应用。
3. **语义化版本**：`api_version` + `pack.version`；安装幂等；旧数据可迁移。
4. **影子模式**：新算法/新 Pack 用历史数据回放对比，达标再切换（路线图）。
5. **失败可见**：Pack 校验失败整包拒绝；模型失败显式暴露；不允许半导入或静默降级。

---

## 学习证据模型

每个单元有四维证据：

| 维度 | 证据来源 |
|---|---|
| **回忆** | FSRS 复习评分、recall 探针 |
| **解释** | 苏格拉底报告、费曼评分 |
| **应用** | apply 探针、变式练习（路线图） |
| **迁移** | 边界/反例探针、费曼评分、苏格拉底综合回合 |

只有四维都过线且有一定重复，单元才会进入“已烧开”。
学习状态会随时间和到期复习变成“需复习”；系统优先建议先还卡，再学新内容。

---

## 内容包

Boil 自带两个演示包：

1. **学神模型 · 学习力**（原创内容，CC BY 4.0）：8 个单元，覆盖烧水、粉末化、对标、高频回测、费曼、能力边界、时间张弛。
2. **高等数学 · 二重积分通法**（从 examgen 模板库解析，6 条通法）：保留核心本质、步骤、边界、来源版本。

从材料创建内容包：

```bash
# 在 UI：内容库 → 从材料创建
# 或使用 API：
curl -X POST http://127.0.0.1:7860/api/packs/compile \
  -H 'Content-Type: application/json' \
  -d '{"title":"我的讲义","subject":"高等数学","text":"...","use_llm":true}'
```

重新生成数学演示包：

```bash
python3 tools/import_examgen.py \
  --input ../_work/examgen/output/模板库/第05章-二重积分-模板库-v0.14.md \
  --output content/packs/math-calculus-ch5.json --limit 6
```

Pack 规范、校验规则、版本与幂等语义见 [PACK_SPEC.md](PACK_SPEC.md)。

---

## API

所有接口返回 `{ok: true, data: ...}` 或 `{ok: false, error: {...}}`。
苏格拉底对话使用 SSE，事件为 `status` / `delta` / `done` / `error` / `close`。

完整列表见 [API.md](API.md)。几个入口：

```bash
curl http://127.0.0.1:7860/api/health
curl http://127.0.0.1:7860/api/dashboard
curl -X POST http://127.0.0.1:7860/api/onboarding \
  -H 'Content-Type: application/json' \
  -d '{"name":"你","pack_id":"boil.xueshen.learning","goal_title":"学习力","daily_minutes":40}'
```

---

## 测试

```bash
# 29 项单元 / 集成 / HTTP / SSE / LLM 网关测试（stdlib unittest）
python3 -m unittest discover -s tests -v
```

覆盖：

- FSRS 新卡/复习/遗忘/保留率
- 前置 DAG 规划与最少必要路径
- 内容编译与 Pack 校验
- Pack 重复安装不重置 SRS
- 苏格拉底流式对话、六轮收束、证据报告
- 费曼评分、复习、洞察、重排、导出
- HTTP REST + SSE + 静态资源 + 错误码
- 真实模型网关（假端点）：普通、流式、JSON 模式、失败可见
- live 服务路径：苏格拉底、费曼、编译器

浏览器验收（需要 Chromium，且服务已在 7861 运行）：

```bash
BOIL_DATA_DIR=/tmp/boil-e2e BOIL_PORT=7861 python3 run.py
python3 tools/browser_check.py
```

它会收集 JS 异常、console error、请求失败，并为所有页面生成 `screenshots/*.png`。

---

## 路线图

已完成（v1.0）：

- 目标/路径/最少必要路径
- 预消化单元与知识地图
- 苏格拉底（流式 + 报告）
- 费曼（六维 + 缺口）
- FSRS-5 复习
- 四维掌握度与洞察
- Pack 编译/导入/幂等
- 模型网关（普通/流式/JSON/失败可见）
- 本地优先 + 导出 + 演示模式
- 自研 SPA + 离线 KaTeX

下一阶段：

- **学习效果基准**：7/30 天延迟保持、迁移测试、对照实验；公开方法与局限。
- **影子模式 / A/B**：新排程器、新评分器用历史事件回放后切换。
- **语音费曼**：ASR + 评分；音频浸泡复习。
- **MCP**：把复习、会话、导入暴露给其他 agent。
- **多设备同步**：事件日志 + 冲突解决。
- **多用户 / 托管版**：真正的租户隔离、计费、团队看板、SSO。
- **更多学科包**：数学、编程、语言、职业考试（只做成人高 stakes，不碰 K12 学科培训）。
- **Pack 市场**：创作者工作台、版本、分成、质量门禁。

---

## 已知限制

- 当前是**单用户本地优先**产品，不是多租户 SaaS；不要直接暴露到公网。
- 演示模式的评分是**关键词/结构启发式**，不是语义理解；重要内容请配置真实模型并核对材料。
- 内置数学包只演示了 examgen 章节 5 的 6 条通法，不是完整课程或题库。
- 苏格拉底/费曼的提示词与评分器是 v1，仍需要更大规模的效果验证。
- 没有内置学习效果随机对照实验；路线图中有“学习效果基准”。
- 音频浸泡、移动原生 App、Pack 市场尚未实现。
- 内容版权：数学包来自作者生成的模板结构；原始教辅 OCR（`date1/`）未包含。再分发前请自行确认来源权利。

---

## 设计原则与学术依据

Boil 的设计受以下公开研究与实践影响（不声称本产品已被证明有效）：

- 检索练习（testing effect）与间隔重复（spacing effect）
- FSRS-5 / open-spaced-repetition
- 掌握学习（mastery learning）与合意困难（desirable difficulties）
- 自我解释、精细化加工、生成式学习
- 苏格拉底提问、最近发展区与提示阶梯
- 费曼技巧 / 教学相长（protégé effect）
- 自我决定理论：自主、胜任、归属
- 认知负荷理论：降低外在负荷，保留生成性负荷

产品明确反对“AI 替你把一切想完”：AI 应该降低**错误的难度**（杂乱材料、缺失前置、术语壁垒），
但必须保留**正确的难度**（提取、生成、迁移）。

---

## 贡献

欢迎贡献：

- 新的 Subject / Content Pack
- 新的苏格拉底策略、费曼 rubric、评测器
- 学习科学实验与延迟测试
- 无障碍、国际化、移动端
- 安全与隐私审计

请先读 [ARCHITECTURE.md](ARCHITECTURE.md)、[PACK_SPEC.md](PACK_SPEC.md) 与 [SECURITY.md](SECURITY.md)。
新增 Pack 必须通过 `validate_pack` 与对应的黄金测试。

---

## 致谢

- **mem**、**examgen**、**testlearnweb**：作者 JohnFKS 的三个原创项目，Boil 的能力来源与合并基础。
- **FSRS / open-spaced-repetition**：间隔重复算法与公开研究。
- **KaTeX**：本地化的数学渲染（MIT，许可证见 `static/vendor/katex/LICENSE`）。
- **学神模型解说稿**：学习力内容包的思想来源；Boil 将其重写为可教学单元与探针。

---

## 许可证

- 代码：**Apache-2.0**（见 [LICENSE](LICENSE)）。
- 内容包：各自在 `pack.json` 中声明（学习力包 CC BY 4.0；数学演示包 CC BY 4.0，来源与限制见包内 `source` / `license`）。
- 第三方组件：KaTeX MIT；详见 [NOTICE](NOTICE)。
