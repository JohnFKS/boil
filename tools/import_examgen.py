#!/usr/bin/env python3
"""把 examgen 的模板库 Markdown 转成 Boil LearningPack。

用法：
  python3 tools/import_examgen.py \
    --input ../_work/examgen/output/模板库/第05章-二重积分-模板库-v0.17.md \
    --output content/packs/math-calculus-ch5.json --limit 6
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from boil.packs import derive_key_points, normalize_pack, validate_pack  # noqa: E402
from boil.utils import split_cn  # noqa: E402

ANALOGIES = {
    "T-HS-43-I": "把定积分看成一个数，就像方程里的未知常数；参数藏在积分号里时，先把它请到上限，再逐层求导。",
    "T-HS-122-I": "先看结构的对称性，就像整理房间先按柜子分类；能免算的，绝不动手。",
    "T-HS-131-I": "构造函数像给不等式造一座桥：移到一侧、求导、看单调性，让符号自己说话。",
    "T-HS-142-I": "切片像切吐司：绕哪根轴就垂直那根轴切片，每一片都是一个圆盘或柱壳。",
    "T-HS-155-I": "和式就是积分的‘乐高说明书’：先凑出步长与归一化下标，就能拼回定积分。",
    "T-HS-247-I": "先画区域再定序，就像先看地图再决定路线；区域的表示方式决定了计算量。",
    "T-HS-17-S": "对称性像照镜子：先判断镜子里哪一部分会反号，奇项直接归零。",
}


def latest_template(chapter_dir: Path) -> Path:
    files = sorted(chapter_dir.glob("*.md"))
    if not files:
        raise SystemExit(f"目录下没有模板文件：{chapter_dir}")
    def version(p: Path) -> float:
        m = re.search(r"-v(\d+(?:\.\d+)?)\.md$", p.name)
        return float(m.group(1)) if m else 0.0
    return max(files, key=version)


def parse_sections(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^## (.+)$", text, re.M))
    out = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        if title in ("本章概览", "本章通法清单"):
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            out.append((title, body))
    return out


def table_value(body: str, key: str) -> str:
    m = re.search(r"^\|\s*" + re.escape(key) + r"\s*\|\s*(.+?)\s*\|\s*$", body, re.M)
    return m.group(1).strip() if m else ""


def subsection(body: str, heading: str) -> str:
    m = re.search(r"^### " + re.escape(heading) + r".*$", body, re.M)
    if not m:
        return ""
    rest = body[m.end():]
    nxt = re.search(r"^###?\s+", rest, re.M)
    return (rest[:nxt.start()] if nxt else rest).strip()


def numbered_items(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if m:
            value = re.sub(r"\s+", " ", m.group(1)).strip()
            if 4 <= len(value) <= 260:
                out.append(value)
    return out


def bullet_items(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*-\s+(.+)$", line)
        if m:
            value = re.sub(r"\s+", " ", m.group(1)).strip()
            if value:
                out.append(value)
    return out


def parse_branches(text: str) -> list[dict[str, str]]:
    out = []
    lines = [l for l in text.splitlines() if l.strip().startswith("|")]
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] not in ("分支", "---"):
            out.append({"name": cells[0], "trigger": cells[1]})
    return out


def parse_examples(text: str) -> list[dict[str, str]]:
    out = []
    lines = [l for l in text.splitlines() if l.strip().startswith("|")]
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] not in ("题号", "---"):
            out.append({"id": cells[0], "type": cells[1]})
    return out


def make_unit(title: str, body: str, index: int, source_file: str) -> dict | None:
    meta_m = re.search(r"编号\s*\*\*([^*]+)\*\*\s*｜\s*版本\s*\*\*([^*]+)\*\*", body)
    if not meta_m:
        return None
    tid = meta_m.group(1).strip()
    version = meta_m.group(2).strip()
    essence = table_value(body, "核心本质")
    if not essence:
        return None
    scope = table_value(body, "适用范围")
    steps = numbered_items(subsection(body, "标准解题步骤"))
    bounds_raw = bullet_items(subsection(body, "失效边界"))
    boundaries = []
    for b in bounds_raw:
        b = re.sub(r"\*\*(适用前提|失效场景)\*\*[：:]\s*", "", b)
        boundaries.extend([x.strip() for x in re.split(r"[；;]\s*", b) if len(x.strip()) > 8][:4])
    branches = parse_branches(subsection(body, "二级分支"))
    examples = parse_examples(subsection(body, "典型例题"))
    branch_names = [b["name"] for b in branches][:8]
    example = examples[0]["type"] if examples else scope

    unit = {
        "id": f"template-{tid.lower().replace('-', '_')}",
        "title": title,
        "essence": essence,
        "analogy": ANALOGIES.get(tid, ""),
        "example": example,
        "steps": steps,
        "boundaries": boundaries[:6],
        "misconceptions": [
            "只判断表面形式，不检查适用前提与失效边界。",
            "把某一分支的局部条件当成整个通法的条件。",
            "只关注被积函数，忽略定向元素/积分区域/符号的联动。",
        ],
        "faq": [
            {
                "q": "这个通法有哪些典型分支？",
                "a": "、".join(branch_names[:6]) if branch_names else "材料未列出分支。",
            },
            {
                "q": "它适用于什么范围？",
                "a": scope or "以材料给出的适用范围为准。",
            },
        ],
        "key_points": [],
        "difficulty": 4,
        "importance": 5 if index < 3 else 4,
        "estimated_minutes": 50 if index < 3 else 45,
        "order_index": index,
        "tags": ["高等数学", "二重积分", "通法"] + branch_names[:3],
        "source_ref": {
            "system": "examgen",
            "kind": "template",
            "id": tid,
            "version": version,
            "path": source_file,
        },
        "probes": [
            {
                "kind": "recall",
                "prompt": f"不看手册，用一句话说出「{title}」的核心本质。",
                "answer": essence,
                "rubric": [],
                "hints": [essence[:80]],
            },
            {
                "kind": "explain",
                "prompt": f"为什么「{title}」能成立？它最关键的假设是什么？",
                "answer": scope or essence,
                "rubric": [],
                "hints": ["先找适用前提，再想如果不满足会怎样。"],
            },
            {
                "kind": "apply",
                "prompt": f"遇到什么样的表面特征时，你会启动「{title}」？",
                "answer": example,
                "rubric": [],
                "hints": [branch_names[0] if branch_names else "先看题目结构。"],
            },
            {
                "kind": "boundary",
                "prompt": f"举一个「{title}」会失效或不能直接套用的场景。",
                "answer": "\n".join(f"- {b}" for b in boundaries[:3]),
                "rubric": [],
                "hints": ["检查定向元素、区域表示或适用前提。"],
            },
        ],
        "cards": [
            {"front": f"「{title}」的核心本质是什么？", "back": essence},
            {"front": f"「{title}」最容易漏掉的边界是什么？", "back": "\n".join(f"- {b}" for b in boundaries[:2]) or "先检查适用前提。"},
        ],
        "relations": [],
    }
    # 关键点：本质短句 + 步骤动词 + 分支名
    kp = []
    for raw in [essence, *steps[:3], *branch_names[:2]]:
        for part in split_cn(raw, 3):
            if 3 <= len(part) <= 30 and part not in kp:
                kp.append(part)
    unit["key_points"] = (kp or derive_key_points(unit))[:8]
    for p in unit["probes"]:
        p["rubric"] = unit["key_points"][:5]
    return unit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="content/packs/math-calculus-ch5.json")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--name", default="高等数学 · 二重积分通法")
    args = parser.parse_args()
    input_path = Path(args.input)
    text = input_path.read_text(encoding="utf-8")
    units = []
    for title, body in parse_sections(text):
        unit = make_unit(title, body, len(units), input_path.name)
        if unit:
            units.append(unit)
        if len(units) >= args.limit:
            break
    if not units:
        raise SystemExit("没有解析到任何模板")
    for a, b in zip(units, units[1:]):
        a["relations"].append({"to": b["id"], "kind": "prerequisite"})
    pack = {
        "schema_version": "1.0",
        "id": "boil.examgen.calculus.ch5",
        "version": "1.0.0",
        "kind": "subject",
        "name": args.name,
        "description": f"从 examgen 模板库解析的 {len(units)} 条二重积分通法；保留核心本质、步骤、边界与来源版本。",
        "subject": "高等数学",
        "language": "zh-CN",
        "license": "CC-BY-4.0",
        "cover": "ink",
        "tags": ["考研数学", "高等数学", "二重积分", "通法"],
        "source": {
            "system": "examgen",
            "kind": "template-library",
            "path": input_path.name,
            "generated_at": "2026-10-21T00:00:00Z",
            "note": "仅导入作者生成的模板结构；原始教辅 date1/ 未包含。",
        },
        "units": units,
    }
    pack = normalize_pack(pack, source="examgen")
    errors = validate_pack(pack)
    if errors:
        raise SystemExit("校验失败：\n" + "\n".join(errors))
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写入 {out_path}：{len(units)} 个单元")


if __name__ == "__main__":
    main()
