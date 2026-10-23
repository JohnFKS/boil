r"""粘贴文本清洗：把从网页 / Word / PPT / PDF / AI 对话里复制来的脏 Markdown 归一化。

材料的主要来源就是复制粘贴，而粘贴产物会直接干扰编译器切分：
全角空格让标题匹配失败，Word 的项目符号不是 `-`，`\[..\]` 里的公式不会被 KaTeX 认出来。
这里只做**确定性的形状归一**，不增删内容、不改写措辞。
"""
from __future__ import annotations

import re

FULL_WIDTH_SPACE = "\u3000\u00a0\u2007\u202f"
BULLET_RE = re.compile(r"^([ \t]*)(?:[•▪◦●○·‣⁃‐‑‒―－]|[-])([ \t]*)(?=\S)")
HEAD_RE = re.compile(r"^(#{1,6})(?!#)(?=[^\s#])")
NUMBERED_RE = re.compile(r"^([ \t]*)(\d{1,3})[.、)）](?=[^\s])")

OPTION_KEYS = (
    "normalize_newlines", "strip_trailing_space", "collapse_blank_lines",
    "full_width_space", "bullets", "heading_space", "numbered", "math_delimiters",
    "strip_zero_width", "dedupe_blank_codefence",
)
DEFAULTS = {k: True for k in OPTION_KEYS}


def _split_code_segments(text: str) -> list[tuple[str, bool]]:
    """把文本切成 (片段, 是否在代码块内)。代码块与行内代码里的内容不动。"""
    segments: list[tuple[str, bool]] = []
    pos = 0
    for m in re.finditer(r"```[\s\S]*?```|~~~[\s\S]*?~~~", text):
        if m.start() > pos:
            segments.append((text[pos:m.start()], False))
        segments.append((m.group(0), True))
        pos = m.end()
    if pos < len(text):
        segments.append((text[pos:], False))
    return segments


def _outside_inline(code_free: str) -> list[tuple[str, bool]]:
    parts: list[tuple[str, bool]] = []
    pos = 0
    for m in re.finditer(r"`[^`\n]*`", code_free):
        if m.start() > pos:
            parts.append((code_free[pos:m.start()], False))
        parts.append((m.group(0), True))
        pos = m.end()
    if pos < len(code_free):
        parts.append((code_free[pos:], False))
    return parts


def _apply_outside(text: str, fn) -> str:
    out = []
    for segment, is_code in _split_code_segments(text):
        if is_code:
            out.append(segment)
            continue
        parts = []
        for piece, is_inline in _outside_inline(segment):
            parts.append(piece if is_inline else fn(piece))
        out.append("".join(parts))
    return "".join(out)


def fix_markdown(text: str, options: dict | None = None) -> tuple[str, list[str]]:
    """返回 (清洗后的文本, 实际做了哪些事的列表)。空选项一律走默认全开。"""
    opts = {**DEFAULTS, **(options or {})}
    raw = (text or "")
    if not raw.strip():
        return "", []
    done: list[str] = []
    out = raw

    def note(key: str, label: str, before: str, after: str) -> None:
        if before != after:
            done.append(label)

    if opts["normalize_newlines"]:
        step = out.replace("\r\n", "\n").replace("\r", "\n")
        note("normalize_newlines", "换行统一为 LF", out, step)
        out = step
    if opts["strip_zero_width"]:
        step = re.sub(r"[\u200b-\u200d\ufeff\u2060]", "", out)
        note("strip_zero_width", "去掉零宽字符", out, step)
        out = step

    if opts["math_delimiters"]:
        def fix_math(chunk: str) -> str:
            before = chunk
            chunk = re.sub(r"\\\[([\s\S]+?)\\\]", lambda mm: f"$${mm.group(1).strip()}$$", chunk)
            chunk = re.sub(r"\\\(([\s\S]+?)\\\)", lambda mm: f"${mm.group(1).strip()}$", chunk)
            return chunk
        step = _apply_outside(out, fix_math)
        note("math_delimiters", "LaTeX 定界符转为 $/$$", out, step)
        out = step

    lines = out.split("\n")
    changed_any = False

    def map_lines(fn) -> None:
        nonlocal lines, changed_any
        new_lines = []
        for line in lines:
            replaced = fn(line)
            if replaced != line:
                changed_any = True
            new_lines.append(replaced)
        lines = new_lines

    if opts["full_width_space"]:
        def fix_space(line: str) -> str:
            stripped = re.sub(r"^([ \t]*)", "", line)
            indent = line[:len(line) - len(stripped)]
            body = stripped.translate({ord(ch): " " for ch in FULL_WIDTH_SPACE})
            return indent + body
        map_lines(fix_space)
        if changed_any:
            done.append("全角空格转半角")
            changed_any = False

    if opts["bullets"]:
        def fix_bullet(line: str) -> str:
            m = BULLET_RE.match(line)
            if not m:
                return line
            return f"{m.group(1)}- {line[m.end():]}"
        map_lines(fix_bullet)
        if changed_any:
            done.append("项目符号归一为 -")
            changed_any = False

    if opts["numbered"]:
        def fix_numbered(line: str) -> str:
            m = NUMBERED_RE.match(line)
            if not m:
                return line
            return f"{m.group(1)}{m.group(2)}. {line[m.end():]}"
        map_lines(fix_numbered)
        if changed_any:
            done.append("编号列表补齐空格")
            changed_any = False

    if opts["heading_space"]:
        def fix_head(line: str) -> str:
            m = HEAD_RE.match(line)
            if not m:
                return line
            return f"{m.group(1)} {line[m.end():]}"
        map_lines(fix_head)
        if changed_any:
            done.append("标题补空格")
            changed_any = False

    if opts["strip_trailing_space"]:
        map_lines(lambda l: l.rstrip())
        if changed_any:
            done.append("去掉行尾空格")
            changed_any = False

    out = "\n".join(lines)

    if opts["dedupe_blank_codefence"]:
        step = re.sub(r"^\s*```\s*$\n\s*```\s*$", "", out, flags=re.M)
        note("dedupe_blank_codefence", "删掉空代码围栏", out, step)
        out = step

    if opts["collapse_blank_lines"]:
        step = re.sub(r"\n{4,}", "\n\n\n", out)
        step = step.strip("\n")
        note("collapse_blank_lines", "合并多余空行", out, step)
        out = step

    return out, done
