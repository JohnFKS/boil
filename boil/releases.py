"""版本与更新日志。

CHANGELOG.md 是「用户能看到更新了什么」的唯一事实源：网页徽标、设置页的「版本与更新」
分区、`GET /api/releases` 都从这里读，不去解析 git 历史（Docker 镜像里没有 .git，
而且 commit message 是写给协作者的，不是写给学习者的）。

解析器故意只认一种极简结构：

    ## [1.1.0] - 2026-10-24
    一句话摘要。
    ### 分组标题
    - 条目

坏掉的标题会进 `changelog_problems()`，版本号与 `config.VERSION` 不一致会进 `version_problems()`；
宁可不显示，也不显示错的东西；
文件缺失或整篇解析不出来时 `load()` 返回空列表、不抛异常 —— 版本信息不该让应用起不来。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import config

CHANGELOG_PATH = config.ROOT / "CHANGELOG.md"
UNRELEASED_KEY = "unreleased"

_H2 = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_H3 = re.compile(r"^###\s+(?P<title>.+?)\s*$")
_ITEM = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")
_HEADER = re.compile(r"^\s*\[?\s*([^\]\s]+)\s*\]?\s*(?:[-–]\s*(\d{4}-\d{2}-\d{2}))?\s*$")
_VERSIONISH = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.\-]+)?$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def _lines(text: str) -> list[str]:
    """丢掉代码块里的内容：示例里写的 `## [9.9.9] - ...` 不该被当成一次发布。"""
    out: list[str] = []
    fenced = False
    for line in (text or "").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return out


def _split_header(raw: str) -> tuple[str, str] | None:
    match = _HEADER.match(raw)
    if not match:
        return None
    version, date = match.group(1), match.group(2) or ""
    if version.lower() == UNRELEASED_KEY:
        return UNRELEASED_KEY, ""
    if not _VERSIONISH.match(version):
        return None
    return version, date


def version_key(version: str) -> tuple[int, ...]:
    core = re.split(r"[-+]", version)[0]
    nums = tuple(int(p) for p in core.split(".")[:3] if p.isdigit())
    return nums + (0,) * (3 - len(nums))


def parse(text: str) -> list[dict[str, Any]]:
    """把 CHANGELOG 正文解析成结构化 release 列表，保持文件里的先后顺序（从新到旧）。"""
    releases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    group: dict[str, Any] | None = None
    for line in _lines(text):
        if line.lstrip().startswith("#"):
            heading = _H2.match(line)
            if heading:
                parsed = _split_header(heading.group("title"))
                if not parsed:
                    current = group = None
                    continue
                version, date = parsed
                current = {
                    "version": version,
                    "date": date,
                    "summary": "",
                    "groups": [],
                    "items": [],
                    "unreleased": version == UNRELEASED_KEY,
                }
                releases.append(current)
                group = None
                continue
            subheading = _H3.match(line)
            if subheading and current is not None:
                group = {"title": subheading.group("title"), "items": []}
                current["groups"].append(group)
                continue
            current = group = None
            continue
        if current is None:
            continue
        item = _ITEM.match(line)
        if item:
            if group is None:
                group = {"title": "更新", "items": []}
                current["groups"].append(group)
            group["items"].append(item.group("text"))
            current["items"].append(item.group("text"))
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith(("|", ">", "![")):
            continue
        if current["items"] and (line.startswith("  ") or line.startswith("\t")):
            last = current["items"][-1]
            merged = f"{last} {stripped}"
            current["items"][-1] = merged
            for g in current["groups"]:
                if g["items"] and g["items"][-1] == last:
                    g["items"][-1] = merged
                    break
        elif not current["items"] and not current["summary"]:
            current["summary"] = stripped
    return releases


def unreadable_headings(text: str) -> list[str]:
    """版本号标题写了但解析不出来 —— 这类静默失败必须被测试抓住。"""
    bad = []
    for line in _lines(text):
        heading = _H2.match(line)
        if not heading:
            continue
        title = heading.group("title")
        if title.lower() == UNRELEASED_KEY or _split_header(title):
            continue
        if any(ch.isdigit() for ch in title):
            bad.append(f"无法解析的版本标题：`{line.strip()}`（应写成 `## [x.y.z] - YYYY-MM-DD`）")
    return bad


def load(path: Path | None = None) -> list[dict[str, Any]]:
    return parse(config.read_text(path or CHANGELOG_PATH))


def released(releases: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if releases is None:
        releases = load()
    return [r for r in releases if not r["unreleased"]]


def pending(releases: list[dict[str, Any]] | None = None) -> list[str]:
    """[Unreleased] 里攒着的条目：发版前给协作者看，不算正式版本。"""
    if releases is None:
        releases = load()
    for r in releases:
        if r["unreleased"]:
            return r["items"]
    return []


def current(releases: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    entries = released(releases)
    return entries[0] if entries else None


def changelog_problems(releases: list[dict[str, Any]] | None = None,
                      text: str | None = None) -> list[str]:
    """日志本身的自检：日期、条目、分组、重复、顺序。不掺入 config.VERSION。"""
    if text is None:
        text = config.read_text(CHANGELOG_PATH)
    if releases is None:
        releases = parse(text)
    problems = unreadable_headings(text)
    entries = [r for r in releases if not r["unreleased"]]
    if not entries:
        if not problems:
            problems.append(f"{CHANGELOG_PATH.name} 里没解析出任何版本；应写成 `## [1.1.0] - 2026-10-24`")
        return problems
    seen: dict[str, int] = {}
    for index, r in enumerate(entries):
        where = f"版本 {r['version']}"
        seen[r["version"]] = seen.get(r["version"], 0) + 1
        if not r["date"]:
            problems.append(f"{where}: 缺少日期（应为 ` - YYYY-MM-DD`）")
        if not r["items"]:
            problems.append(f"{where}: 没有任何条目")
        if not r["summary"]:
            problems.append(f"{where}: 缺少一句话摘要")
        for g in r["groups"]:
            if not g["items"]:
                problems.append(f"{where} · 分组「{g['title']}」是空的")
        if index and version_key(r["version"]) > version_key(entries[index - 1]["version"]):
            problems.append(f"{where}: 排在 {entries[index - 1]['version']} 之后，版本号却更大（应从上到下由新到旧）")
    for version, count in seen.items():
        if count > 1:
            problems.append(f"版本 {version}: 重复出现了 {count} 次")
    return problems


def version_problems(releases: list[dict[str, Any]] | None = None) -> list[str]:
    """config.VERSION 必须就是日志里最新的一条 —— 发版漏一步就会红。"""
    entries = released(releases)
    if not entries:
        return []  # 没有可用日志：changelog_problems 已经说过了，不重复报
    if entries[0]["version"] != config.VERSION:
        return [f"config.VERSION 是 {config.VERSION}，但 {CHANGELOG_PATH.name} 最新的版本是 {entries[0]['version']}"]
    return []


def all_problems() -> list[str]:
    return changelog_problems() + version_problems()


def build_identity(root: Path | None = None) -> dict[str, Any]:
    """直接读 .git 拿构建号；没有 .git（容器、发布包）时诚实返回空，不猜。"""
    root = root or config.ROOT
    out: dict[str, Any] = {"commit": "", "ref": "", "source": False}
    git = root / ".git"
    if git.is_file():  # worktree / submodule：.git 是个指针文件
        target = ""
        for line in config.read_text(git).splitlines():
            if line.startswith("gitdir:"):
                target = line.split(":", 1)[1].strip()
        git = Path(target) if target else git
        if target and not git.is_absolute():
            git = root / git
    if not git.is_dir():
        return out
    out["source"] = True
    head = config.read_text(git / "HEAD").strip()
    sha = ""
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        if ".." not in ref and not ref.startswith("/"):
            out["ref"] = ref
            sha = config.read_text(git / ref).strip()
            if not sha:
                for line in config.read_text(git / "packed-refs").splitlines():
                    if line.endswith(f" {ref}") and not line.startswith(("#", "^")):
                        sha = line.split(" ", 1)[0].strip()
                        break
    else:
        sha = head  # detached HEAD
    if _SHA.match(sha):
        out["commit"] = sha[:7]
    return out


def _schema_version() -> int:
    from .db import SCHEMA_VERSION
    return SCHEMA_VERSION


def payload() -> dict[str, Any]:
    """给前端和 API 用的完整版本信息。"""
    releases = load()
    build = build_identity()
    entry = current(releases=releases) or {}
    return {
        "app": config.APP_NAME,
        "app_cn": config.APP_CN,
        "version": config.VERSION,
        "released_at": entry.get("date", ""),
        "summary": entry.get("summary", ""),
        "build": build["commit"],
        "ref": build["ref"],
        "source_checkout": build["source"],
        "schema_version": _schema_version(),
        "changelog_source": CHANGELOG_PATH.name,
        "releases": releases,
        "unreleased": pending(releases),
        "changelog_issues": changelog_problems(releases=releases) + version_problems(releases),
    }
