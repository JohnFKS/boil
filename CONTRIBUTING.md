# 贡献指南

Boil 是一个本地优先的学习运行时。欢迎内容包、提示词、评测器、无障碍和测试方面的贡献。

## 开始

```bash
python3 run.py              # 零依赖运行
python3 -m unittest discover -s tests -v
make check && make check-js      # Python 编译 + 前端模块语法
python3 tools/browser_check.py   # 需要 Chromium 和已运行的服务
```

## 验收阶梯（改到哪一层，就要跑到哪一层）

| 层次 | 命令 | 它证明什么 |
| --- | --- | --- |
| 语法与单元 | `make check` | 逻辑、FSRS、编译、检索、契约 |
| 仓库体检（大文件/密钥文件） | `tests/test_zz_repo_hygiene.py` | 跟踪列表里不许出现 >2 MB 的文件、`core.*` 崩溃转储或任何密钥形态文件 |
| 测试隔离不被绕过 | `tests/test_zz_config_leak_guard.py` + `test_agent` 的守卫用例 | 跑完整套之后全局配置路径必须回到原处；覆盖 `setUp` 忘了 `super()` 也不许碰到真实 `data/llm.json` |
| HTTP 契约 | `make check`（含 `tests/test_api.py`） | 状态码、`{ok,data}` 形状、错误码 |
| 安全 | `python3 -m unittest tests.test_security` | 穿越、鉴权、请求体、跨站、密钥不外泄 |
| 韧性 | `python3 -m unittest tests.test_resilience` | 并发写、坏配置、死上游、掐流、重复重置 |
| 渲染 | `python3 tools/browser_check.py` | 真浏览器里没有控制台错误、关键控件点得着 |
| 真人节奏 | `python3 tools/human_journey.py` | 从头学一遍会不会卡住；错题本/速背/自测卷走完 |
| 无障碍与视口 | `python3 tools/audit_frontend.py` | 9 路由 × 3 视口 × 2 主题 + 键盘 + 浏览器里的 XSS/CSP |
| 性能 | `python3 tools/perf_probe.py` | 热端点 p50/p95、并发吞吐、静态体积（`reports/perf.json`） |

浏览器类工具都需要一个已运行的实例：`BOIL_APP=http://127.0.0.1:7861 BOIL_DATA_DIR=/tmp/... python3 tools/...`，
**别拿真实 `data/` 当测试目录**（历史上两次事故都是测试写坏了 `data/llm.json`）。

## 最重要的规则

1. **不静默失败。** 模型/网络/Pack 校验/导入失败必须让用户看见。
2. **降级仍然正确。** 没有模型时演示模式仍能完成闭环；没有 KaTeX 时数学文本仍可读。
3. **契约优先。** 改 API、SSE 事件、Pack 字段前，先更新文档与测试。
4. **不要替学习者思考。** 苏格拉底先答后示；费曼必须由学习者输出。
5. **不碰 K12 学科培训。** 成人高 stakes 学习是本项目的边界。

## 内容包贡献

- 读 [PACK_SPEC.md](PACK_SPEC.md)。
- 每个单元必须有 `title` / `essence` / 至少一个 recall probe。
- 必须提供 `source` 与 `license`；没有来源的内容不要提交。
- 高 stakes 内容建议包含边界/反例探针和人工复核记录。
- 用 `validate_pack` 自检；新增 Pack 应附带一个小型黄金测试。

## 代码风格

- Python 3.10+，标准库优先；不引入运行时第三方依赖。
- 前端使用原生 ES modules 与自研 CSS；不引入构建链。
- 用户内容必须转义；链接协议白名单。
- 新增 API 需补 `API.md` 与 `tests/`。
- 新增页面需通过 `tools/browser_check.py` 的渲染与控制台检查，并把该页加进 `tools/audit_frontend.py` 的路由表（视口 × 主题 × 键盘）。

## 提交

- 小步提交，说明“为什么”而不是只写“改了什么”。
- 不提交 `data/`、API Key、真实学生内容、第三方教辅原文。
- 如果修改了算法/提示词，请说明影响面与回滚方式。

## 版本与发布

网页上的版本和更新内容只来自 `CHANGELOG.md`（`GET /api/releases` → 侧边栏徽标 / 设置页分区），
**不**从 git log 生成。commit message 写给协作者，更新日志写给学习者：讲清多了什么能力、修了什么问题。

发一个小版本（1.1.0 → 1.2.0）：

```bash
# 1. 写日志：在 CHANGELOG.md 顶部加一节，格式必须严格
#    ## [1.2.0] - 2026-11-05
#    一句话：这次学习者能感觉到什么变化。
#    ### 新增 / 改进 / 修复
#    - 每条都是完整的中文句子，说人话，别写函数名
# 2. 改版本号：boil/config.py 里的 VERSION = "1.2.0"
# 3. 自检：格式 + 版本一致性，任何一条红都别发
python3 -m unittest tests.test_releases tests.test_api.ApiTest.test_27_releases_endpoint_over_http
python3 -c "from boil import releases; print(releases.all_problems())"   # 必须是 []
# 4. 全绿后提交并打标签
git add CHANGELOG.md boil/config.py && git commit -m "release: 1.2.0"
git tag -a v1.2.0 -m "Boil 1.2.0"
```

会被测试抓住的常见错误：

- 只改了 `config.VERSION` 忘了写日志 → `version_problems()` 红。
- 版本标题写成 `## 版本 1.2.0（2026年11月5日）` → 解析不出来，`unreadable_headings()` 红
  （静默少一版比报错更糟，所以必须响）。
- 上面新下面旧（`1.9.0` 排在 `1.10.0` 前面）→ 用数字元组比较，不是字符串比较。
- 只有分组标题没有条目、缺日期、缺一句话摘要 → 分别会红。

开发中攒着还没发的条目放在 `## [Unreleased]` 下，发版时把那一段改名成版本号并补日期；
`[Unreleased]` 不会出现在学习者看到的版本列表里（它走 `unreleased` 字段，只在页面标成"下一版正在攒"）。

## 报告问题

请提供：复现步骤、期望行为、实际行为、运行模式（demo/live）、浏览器/系统版本。
不要公开粘贴 API Key 或学生隐私数据。
