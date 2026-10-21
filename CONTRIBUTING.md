# 贡献指南

Boil 是一个本地优先的学习运行时。欢迎内容包、提示词、评测器、无障碍和测试方面的贡献。

## 开始

```bash
python3 run.py              # 零依赖运行
python3 -m unittest discover -s tests -v
make check && make check-js      # Python 编译 + 前端模块语法
python3 tools/browser_check.py   # 需要 Chromium 和已运行的服务
```

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
- 新增页面需通过 `tools/browser_check.py` 的渲染与控制台检查。

## 提交

- 小步提交，说明“为什么”而不是只写“改了什么”。
- 不提交 `data/`、API Key、真实学生内容、第三方教辅原文。
- 如果修改了算法/提示词，请说明影响面与回滚方式。

## 报告问题

请提供：复现步骤、期望行为、实际行为、运行模式（demo/live）、浏览器/系统版本。
不要公开粘贴 API Key 或学生隐私数据。
