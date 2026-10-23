# Boil · 烧水

> **开源 AI 学习操作系统。AI 负责准备，你负责烧开。**
>
> 把任何学习材料编译成「路径 → 预消化 → 苏格拉底 → 费曼 → 间隔复习」的可验证闭环，
> 用学习证据而不是内容消费来衡量效果。

[![Python](https://img.shields.io/badge/Python-3.10%2B-0b5f53?style=flat-square&logo=python&logoColor=white)](#)
[![Dependencies](https://img.shields.io/badge/runtime%20deps-zero-ff7a45?style=flat-square)](#)
[![License](https://img.shields.io/badge/license-Apache--2.0-f2c14e?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-184%20passing-38d6bd?style=flat-square)](#测试)
[![CI](https://github.com/JohnFKS/boil/actions/workflows/ci.yml/badge.svg)](https://github.com/JohnFKS/boil/actions/workflows/ci.yml)

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
没有学习目标时会进入 `/#/onboarding`，完成一次开始流程后进入“今日”主页。
不配置任何模型也能完整体验：演示模式使用本地确定性引擎（切分、追问、关键词评分、FSRS 排程），
它不会伪装成语义模型；所有页面会明确标注“演示模式”。

如果浏览器一直停在“BOIL · 正在烧水”，先确认 `python3 run.py` 仍在运行，再按 `Ctrl+Shift+R`（Mac：`Cmd+Shift+R`）强制刷新。前端静态资源使用 ETag 协商缓存，正常刷新会立即拿到最新模块；加载失败时页面也会给出可操作的提示。

### 配置真实模型

打开 **设置 → 模型连接**，填入任意 OpenAI 兼容端点：

- **Dots（小红书）**：`https://note3-prev-api.askdiandian.com/v1`，模型 `dots3-note-prev`，使用 `api-key` 请求头
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

#### Dots 接入

在设置中选择 **Dots（小红书）** 预设，或在自定义端点中填写：

```text
Base URL:   https://note3-prev-api.askdiandian.com/v1
Model:      dots3-note-prev
认证头:     api-key
认证前缀:   （留空）
高级参数:   {"chat_template_kwargs":{"enable_thinking":false}}
```

Dots 默认开启深度思考；预设为了让苏格拉底对话保持低延迟，关闭了 thinking。
需要更强的推理时，把高级参数改为 `{"chat_template_kwargs":{"enable_thinking":true}}` 并保存。
Key 只保存在本机 `data/llm.json`，不会进入 Git、导出包或前端代码。

#### 硅基流动 Embedding

设置 → **语义检索（Embedding）**：

```text
Base URL: https://api.siliconflow.cn/v1
Model:    BAAI/bge-m3
```

填写 Key 后保存并点“测一次语义检索”。也可以使用环境变量：
`BOIL_EMBEDDING_KEY` / `BOIL_EMBEDDING_BASE_URL` / `BOIL_EMBEDDING_MODEL`。
不配置时使用本地 `local-hash-256`；云端失败会进入 60 秒冷却并降级本地，设置页会显示最后一次错误。
Embedding Key 单独保存在 `data/embedding.json`（0600），不会进入 Git/导出包。

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

## Markdown、Embedding 与数据备份

- **长材料分批**：超过 6,000 字的材料先出全篇大纲，再按 7k 预算分批编译；装不下的小节按段落无损切开，模型没读到的字会写进 warning，不静默丢。
- **多文件材料**：开始页支持一次拖入/选择多个 `.md`、`.json`、`.txt`，JSON 会按键值铺平成可读文本，所有文件合并到同一个主题后编译；文件清单可逐项移除，单文件 2 MB、合并 180,000 字符以内。
- **导入即分析**：材料一交给编译器，点「AI 生成信息」就能先拿到标题、学科、目标名与一句话描述，不必等编译；没配置模型时按本地规则给回退结果。
- **编译时生成大纲与计划**：模型读完整份材料后，除了单元还会输出「学习大纲」（为什么这样排序）、「阶段计划」（2–4 个阶段与每阶段重点、分钟数）和「材料总览」。AI 只决定顺序与叙事，**日期与时间预算仍由代码计算，前置依赖由拓扑排序强制保证**。
- **AI 呈现 + 诚实标签**：仪表盘的材料总览、单元页的「怎么学」导读、洞察页的本周解读，都是模型读完全部材料与证据之后的产物；每一条都带「AI 分析」或「本地规则」标签，规则回退绝不冒充模型输出，失败原因显示在警告里。
- **划词灵感笔记**：在任意学习内容上划选文字（或右键选中内容），可立即「存为灵感」或复制。单元、对话、费曼报告、复习卡、洞察、地图、内容库都支持；灵感带引用原文快照与所属单元，可在「灵感库」页搜索、筛选、删除、导出 Markdown，并随完整备份一起导出/恢复。
- **Markdown 解析**：标题、列表、段落是结构真相源；有模型时可做更细的语义抽取。
- **Embedding**：可接**硅基流动**或任意 OpenAI 兼容 `/embeddings`（推荐 `BAAI/bge-m3`，1024 维）；没有标题的材料用它做语义分组，语义搜索与“相关单元”也走这一层。批量请求、磁盘缓存、失败冷却、自动回退本地 `local-hash-256`，错误在设置页可见。
- **Embedding 隐私**：配置云端后，参与分组/检索的文本会发往该端点；Key 只保存在本地 `data/embedding.json`（0600）。不配置就完全本地、离线。
- **数学公式**：`$...$`、`$$...$$`、`\(...\)`、`\[...\]` 由本地 KaTeX 渲染，浅色/深色主题均已验证。
- **数据备份**：设置 → 数据 → 导出全部数据 / 导入备份；支持合并与替换；导出包含目标、单元、卡片、复习日志、对话、解释、火候记录、速记和灵感笔记，API Key 永不进入备份。
- **Anki 导出**：同一处可导出制表符分隔的卡片文本，正面、背面、标签齐全，导入 Anki 时选“制表符分隔”即可，不锁定你的复习数据。
- **速记**：来自 mem 的小功能；单元页可写口诀/类比/笔记，复习卡可显示可选速记线索。
- **AI 编口诀**：单元页「AI 编口诀」按严格的任务边界提示词生成记忆钩子（口诀/谐音/类比/画面/反差），**禁止复述内容或新增知识**；结果先显示为「AI 建议」，你点「填进速记」才会进入输入框，再点「保存速记」才会写库——绝不静默保存。材料由服务端读取，不接受前端传来的原文。
- **先回忆再翻面**：复习卡正面多了一个回忆框，写下自己的话再翻面；翻面后你的回忆排在标准答案上方做对照，并随评分一起存进 `attempts`（写不出来正好说明这里还没懂）。
- **灵感引文重现**：存在灵感库里的引用，会在对应单元正文里被自动高亮（按文字快照匹配所有出现位置，跳过代码与公式），看见灵感再次撞上知识本体。
- **记忆健康度**：洞察页新增一项——所有已学卡片此刻平均还能提取多少（按 FSRS 遗忘曲线计算）。它和「30 天保留率」不同：后者是历史答对率，前者是当下状态。
- **结构化调用缓存**：编译、大纲、速记、洞察解读这类结构化调用按「模型 + 提示词 + 参数」做内容寻址缓存，同样材料重跑零 token 且结果不漂移（实测 1.15s → 0ms）；流式对话不缓存，可在设置页关闭或一键清空。
- **错因标签**：答「忘了/模糊」时要求标一下错在哪（概念、边界、步骤、记混、粗心、没印象 6 类），只拦一次、可以跳过；标签写进 `review_logs` 与卡片，洞察页的「错因分布」卡片按标签出条形，并把「多少复习标了错因 / 样本多大」写在下面，不把没标的算进来。复习时写的回忆文字另记为 `attempts(kind=review_recall)`，不占探测题的作答计数。
- **撤销评分**：评完分的卡片旁边会出现「刚才这次」——几分钟后再见、掌握度从多少到多少——点一下就能整条撤销：卡片状态、掌握度、复习日志、回忆、火候记录一起回到评分前。只允许撤该单元最近一次（后来的复习会让快照失效），同一次不能撤两遍。
- **专注（番茄钟）**：顶栏「专注」可选 15 / 25 / 45 分钟，倒计时刷新后也不会丢；走完自动记账，提前结束明确「不计入学习时长」——完成才进热力图。
- **触摸端划词**：手指拖选结束后浮条自己出现在选区上方（不再跟着拖选柄乱跳），窄屏会换行而不是溢出屏幕；用 `-webkit-touch-callout` 让系统长按菜单让位给自己的工具条。
- **跨公式的引文**：划选里带公式时，引文会存成可读的 `$TeX$`（不是 KaTeX 的 MathML 乱码），并额外记一个「去公式可见文本」锚点；重新打开单元页，公式两侧的文字照样点亮（公式本身不动），追问的上下文也换成同一套可读文本。
- **回忆记录**：复习时写的回忆会存成 `attempts(kind=review_recall)`，单元页的「我的回忆记录」按时间倒序列出最近几条原文与得分，方便对照自己前后的说法；它和探测题作答分开计数。
- **复习队列侧栏**：本轮队列可视、点一行就跳到那张卡、勾选后「只练选中」、还能「提前加卡」把未到期的卡加进来练；已完成的划线变灰。
- **键盘优先**：空格翻面、1–4 评分、`/` 或 ⌘K 搜索；输入框里打字与浮层打开时不抢键。
- **粘贴即清洗**：从网页 / Word / PDF 粘贴材料时自动归一（项目符号、全角空格、`\[..\]` 公式定界符、编号、零宽字符、CRLF），并告诉你改了什么；代码块与行内代码里的内容一律不动。
- **导入前快照**：任何导入（合并或替换）前先把当前库用 SQLite 备份 API 快照到 `data/backups/pre_import_*.db`（保留 10 份），toast 会告诉你快照文件名，出错能回到导入前。
- **长材料分批编译**：超过 24k 字符的材料不再被截断——先送全篇骨架出大纲，再逐批只针对本批出单元，每一节都真的进过模型；单批失败只跳过该批并留 warning。
- **编译质检**：确定性优先（空本质、重复标题、步骤里混具体数字、缺探针类型、正反面一样的卡片、近似重复单元对），只有需要判断力的残差才交给模型；安全补丁自动打上，**补丁等于原文时跳过**以免无意义地翻动版本与缓存键。
- **合并不是删除**：疑似重复由**两路信号**提出——字面近乎逐字，或硅基流动 `bge-m3` 的语义相似度进入候选带（只看标题+本质：实测真重复 0.870，而「提取练习 / 测试即学习」这种相关但不同的能到 0.797，只差 0.07 分不开）。**语义分不单独授权合并**：地图页写明「字面 x% · 语义 y%」，字面过硬才由代码放行，其余要模型签字、你再点一次确认（模型对同一对两次能给出相反答案），被拒还可以写明理由署名合并——理由进合并记录，日后分得清是谁的决定。被吸收的单元从地图与「下一步」里消失，但内容仍可访问、复习卡照常到期、前置关系自动重连，链式合并指向最终归宿并拒绝成环；**合错了可以在被吸收单元的页面上「撤销这次合并」**——关系边会按当时记下的改动回滚（归宿又被并走、或这个单元之后又吸收了别人时会拒绝并说明原因）；没配嵌入服务时语义一栏直接显示为不可用，不拿本地哈希向量冒充。
- **选中即可追问 / 做成卡片**：划词菜单新增「做卡片」（预览→确认才进复习队列，带 AI/规则标签）与「问 AI」（带选中位置前后各 800 字上下文，单轮回答，答案带「AI 回答」与可见范围说明）。
- **模型诊断**：失败时设置页给出分类（DNS/拒绝连接/超时/TLS/代理/网络）、对应状态码建议、掩码 Key 与可一键复制的诊断文本；上游一律按 `stream:true` 接收再拼回，避免思考型模型长推理被空闲网关掐成 504。
- **学习风格测试**：仪表盘「花 3 分钟测一下带法」→ 5 道情境题 → 小步快跑 / 对比辨析 / 先例子后定义 / 被追问 / 动手构建 五种人设之一，写入目标并注入苏格拉底与费曼的提示词；不测也能正常学习。
- **AI 不可用时的姿态**：未配置模型时 AI 入口完全不渲染（不是置灰）；连续 2 次失败即熔断并隐藏入口，同时提示已切回本地模式。复习、苏格拉底、费曼、洞察这些主流程永远不依赖模型。

## 火候：不只给工具，给“下一档”

Boil v2 的核心不是功能菜单，而是**强度阶梯 + 推荐器**。学习金字塔从听讲、阅读、讨论、演练到教别人，强度逐级上升；火候只负责告诉你：现在在哪一档、下一档该做什么、为什么。

| 档位 | 模式 | 对应功能 | 负责什么 |
|---|---|---|---|
| 0 | 听讲 | 音频浸泡 | 建立熟悉感，不指望记住 |
| 1 | 阅读/视听 | 预消化 | 粉末化、知识地图、类比、FAQ |
| 2 | 讨论 | 苏格拉底对话 | AI 问、你答，把石头打散 |
| 3 | 实际演练 | 高频回测 + FSRS | 同一档重复的时刻表 |
| 4 | 教别人 | 费曼对话 | 你讲、AI 装不懂 |
| — | 非档位 | 极致对标 | 完整复现标准步骤，过了才算烧开 |

### T（火候）与 PN（必要步骤）

- **T**：由四维掌握度、距上次证据的天数、到期卡数量压缩成 0–100 的温度。它不是科学测量值，是产品状态量；阈值用于推荐，而不是宣称“精确预测”。
- **PN**：每个单元的标准步骤就是必要步骤；最近一次苏格拉底/费曼报告里的缺口会显示为“还缺这一步”。T 升高但 PN 仍有缺口时，推荐费曼；T 很高且缺口补齐时，推荐极致对标。
- **BoilRecord**：每次苏格拉底、费曼、回测都记录 `heat_before → heat_after`、模式、档位、结果与 PN 差异；这些记录是未来策略学习和效果评估的原料。

### Advisor v0

输入：当前 T、降温/到期列表、PN 缺口、能力边界、本次可用时间。
输出：**一个**下一步建议 + 人话理由 + 预计成本，并支持“今天先不用”（否决会记账）。

规则示例：

- 有到期卡 → 先回测；时间不足就只还一部分，不硬塞新内容。
- 新单元 T=0 → 预消化；时间 ≤8 分钟 → 音频建立熟悉感。
- T<40 → 苏格拉底；T 40–79 且 PN 有缺口 → 费曼；T≥65 且无缺口 → 极致对标。
- 前置未烧开 → 先补前置，不硬上目标单元。

推荐理由、时间预算、PN 缺口和“今天先不用”都在 [今日仪表盘](screenshots/dashboard.png) 的火候建议卡里。

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

| 多文件材料 | 编译预览 |
|---|---|
| ![Multi-file upload](screenshots/onboarding-files.png) | ![Compiled units](screenshots/onboarding-compiled.png) |

| 第 0 档音频 | 极致对标 |
|---|---|
| ![Audio](screenshots/audio.png) | ![Benchmark](screenshots/benchmark.png) |

| 设置 | 数据与迁移 |
|---|---|
| ![Settings](screenshots/settings.png) | ![Backup and Anki](screenshots/settings-data.png) |

| 划词菜单 | 灵感库 |
|---|---|
| ![Selection menu](screenshots/selection-menu.png) | ![Notes](screenshots/notes.png) |

| AI 生成标题 | 大纲与阶段计划 |
|---|---|
| ![AI suggest](screenshots/onboarding-ai-suggest.png) | ![Outline and plan](screenshots/onboarding-outline-plan.png) |

| AI 洞察解读 | 保存灵感 |
|---|---|
| ![Insights AI](screenshots/insights-ai-summary.png) | ![Note popover](screenshots/note-popover.png) |

| 风格测试 | 复习队列与错因 |
|---|---|
| ![Style test](screenshots/style-test.png) | ![Review queue](screenshots/review.png) |

| 引文在单元页重现 | AI 编口诀 |
|---|---|
| ![Note highlight](screenshots/unit-note-highlight.png) | ![Mnemonic AI](screenshots/unit-mnemonic-ai.png) |

### 数学公式

![Math formula](screenshots/math-formula.png)

数学包中的圆盘法/柱壳法公式用 KaTeX 渲染；浏览器实测 `.katex` 节点为 4 个，浅色与深色主题都验证过对比度。

---

## 功能

- **目标与路径**：截止日期、每日分钟数、前置 DAG、最少必要路径、暂存单元、动态重排。
- **内容编译**：把 Markdown/JSON/纯文本（支持多文件合并）编译成知识单元、探针、卡片与大纲/阶段计划；有标题时按结构切分，无标题时用本地向量做语义分组；有模型时做更细的结构抽取。
- **知识地图**：可缩放/平移/筛选的 SVG 图谱；节点状态：未开始、学习中、已烧开、需复习。
- **苏格拉底对话**：SSE 流式；一次一问；提示阶梯；阶段推进；结束生成四维证据报告。
- **费曼挑战**：三种讲解对象；计时；六维评分；缺口、术语、参考讲解、追问。
- **间隔重复**：FSRS-5；四档评分；每个评分显示下次间隔；复习队列；未来 7 天预测。
- **学习证据**：回忆/解释/应用/迁移四维雷达；30 天保留率；热力图；苏格拉底缺口聚合。
- **火候状态**：每个单元的 T 温度、PN 缺口、BoilRecord 历史；降温与到期会自动影响推荐。
- **Advisor v0**：沿强度阶梯只推进一步；理由可解释；支持时间预算、接受与否决记账。
- **强度模式**：第 0 档音频浸泡、第 1 档预消化、第 2 档苏格拉底、第 3 档回测、第 4 档费曼、非档位极致对标。
- **Pack 质量门禁**：原子率、四类探针覆盖率、DAG 无环，在内容库直接可见。
- **数据备份**：一键导出/导入全部学习数据；支持合并或替换，API Key 永不进入备份；可另存为 Anki 制表符文本。
- **MCP 接入**：`python3 -m boil.mcp_server` 通过 stdio 暴露 13 个工具，Claude Desktop 等 agent 可直接查询推荐、复习队列、会话与速记。
- **速记与笔记**：来自 mem 的速记思路；单元页可写口诀/类比/笔记，复习卡按需显示速记线索。
- **语义层**：可选硅基流动 BAAI/bge-m3；无标题材料语义分组、语义搜索、相关单元；失败自动降级本地哈希向量。
- **数学公式**：Markdown 中的 `$...$` / `$$...$$` 由本地 KaTeX 渲染，已用真实浏览器验证。
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

## Agent 接入（MCP）

Boil 自带一个零依赖的 MCP stdio 服务，让 Claude Desktop、Cursor 等 agent 直接使用同一份本地学习数据：

```bash
python3 -m boil.mcp_server
```

在 MCP 客户端中配置（把路径换成你的仓库位置）：

```json
{
  "mcpServers": {
    "boil": {
      "command": "python3",
      "args": ["-m", "boil.mcp_server"],
      "cwd": "/path/to/boil",
      "env": {"BOIL_DATA_DIR": "/path/to/boil/data"}
    }
  }
}
```

提供 13 个工具：`health`、`advice`、`list_units`、`get_unit`、`search`、`review_queue`、`submit_review`、`save_note`、`start_socratic`、`socratic_reply`、`finish_socratic`、`feynman_evaluate`、`insights`。
评分、费曼等会写入学习证据的工具在描述中要求 agent 先获得用户明确请求；返回结果不包含任何 API Key。

---

## 测试

```bash
# 184 项单元 / 集成 / HTTP / SSE / LLM 网关 / MCP / 质检 / 风格测试（stdlib unittest）
python3 -m unittest discover -s tests -v
```

覆盖：

- FSRS 新卡/复习/遗忘/保留率
- 前置 DAG 规划与最少必要路径
- 内容编译与 Pack 校验、多文件合并与来源记录
- Pack 重复安装不重置 SRS
- 苏格拉底流式对话、六轮收束、证据报告
- 费曼评分、复习、洞察、重排、导出
- 火候 T 衰减、到期惩罚、PN 缺口
- Advisor v0：新单元/苏格拉底/费曼/对标/回测优先/低时间音频/前置边界
- BoilRecord 与 Pack 质量门禁
- HTTP REST + SSE + 静态资源 + 错误码
- 真实模型网关（假端点）：普通、流式、JSON 模式、自定义认证头、失败可见
- live 服务路径：苏格拉底、费曼、编译器
- 数据备份导出/合并导入、Anki 导出、速记笔记、复习卡速记线索
- 安全响应头（CSP / X-Frame-Options / Referrer-Policy / Permissions-Policy）
- 本地/硅基流动 Embedding 语义分组、语义搜索、相关单元
- MCP stdio 协议：初始化、工具发现、学习查询、复习、会话、费曼与速记
- GitHub Actions：Python 3.10/3.12 测试 + 前端模块语法检查
- 远端 Embedding 批量、缓存、失败冷却、降级与错误可见
- 数学包公式内容与 KaTeX 资源检查

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
- 火候 T / PN 缺口 / BoilRecord
- Advisor v0（规则版 + 时间预算 + 否决记账）
- 第 0 档音频、第 4 档费曼、非档位极致对标
- Pack 质量门禁（原子率/探针覆盖/DAG）
- Pack 编译/导入/幂等
- 模型网关（普通/流式/JSON/失败可见）
- 本地优先 + JSON 备份 + Anki 导出 + 演示模式
- MCP stdio 服务（13 个工具）
- 安全响应头 + GitHub Actions CI
- 自研 SPA + 离线 KaTeX

下一阶段：

- **学习效果基准**：7/30 天延迟保持、迁移测试、对照实验；公开方法与局限。
- **影子模式 / A/B**：新排程器、新评分器用历史事件回放后切换。
- **语音费曼**：ASR + 评分；音频浸泡复习。
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
- T 与 Advisor 是规则版产品启发式，不是学习科学定论；需要在真实数据上校准阈值与成本模型。
- BoilRecord 已落库，但尚未做策略学习；“建议采纳/否决”数据需要积累后再训练。
- 云端 Embedding 会把参检文本发往所配端点；不配置时使用本地哈希向量。
- 数据导入是单用户备份恢复，不是多租户迁移；替换导入会清空当前数据。
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
