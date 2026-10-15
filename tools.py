"""
tools.py —— Agent 可调用的 5 个工具。
全部用 langchain_core.tools.tool 装饰器封装。
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool


# ---------------------------------------------------------------------------
# 工具 1：list_dir —— 列出目录内容
# ---------------------------------------------------------------------------
@tool
def list_dir(path: str = ".") -> str:
    """列出指定目录下的文件和子目录。path 默认为当前目录。"""
    print(f"[TOOL] list_dir  path={path}")
    try:
        p = Path(path).resolve()
        if not p.exists():
            return f"[错误] 路径不存在: {p}"
        if not p.is_dir():
            return f"[错误] 不是目录: {p}"
        entries = sorted(p.iterdir())
        if not entries:
            return f"目录 {p} 是空的。"
        lines = []
        for e in entries:
            tag = "[DIR ]" if e.is_dir() else "[FILE]"
            lines.append(f"{tag}  {e.name}")
        return "\n".join(lines)
    except Exception as ex:
        return f"[异常] list_dir({path}): {ex}"


# ---------------------------------------------------------------------------
# 工具 2：read_file —— 读取文件内容
# ---------------------------------------------------------------------------
@tool
def read_file(path: str) -> str:
    """读取并返回指定文件的文本内容。"""
    print(f"[TOOL] read_file  path={path}")
    try:
        p = Path(path).resolve()
        if not p.exists():
            return f"[错误] 文件不存在: {p}"
        if not p.is_file():
            return f"[错误] 不是文件: {p}"
        # 按 UTF-8 读，失败再降级 GBK（Windows 中文常见）
        for enc in ("utf-8", "gbk"):
            try:
                text = p.read_text(encoding=enc)
                return text
            except UnicodeDecodeError:
                continue
        return f"[错误] 无法解码文件: {p}"
    except Exception as ex:
        return f"[异常] read_file({path}): {ex}"


# ---------------------------------------------------------------------------
# 工具 3：search_content —— 在目录下搜索关键词
# ---------------------------------------------------------------------------
@tool
def search_content(keyword: str, directory: str = ".") -> str:
    """
    在指定目录（递归）下搜索包含 keyword 的文本文件。
    返回每个匹配文件的路径 + 命中行（带行号）。
    """
    print(f"[TOOL] search_content  keyword={keyword!r}  directory={directory}")
    try:
        root = Path(directory).resolve()
        if not root.exists():
            return f"[错误] 目录不存在: {root}"
        if not root.is_dir():
            return f"[错误] 不是目录: {root}"

        results = []
        # 简单的二进制/文本判断：跳过明显的二进制后缀
        binary_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".pdf",
                           ".zip", ".tar", ".gz", ".exe", ".pyc", ".dll"}
        for fp in root.rglob("*"):
            if not fp.is_file():
                continue
            if fp.suffix.lower() in binary_suffixes:
                continue
            try:
                for enc in ("utf-8", "gbk"):
                    try:
                        lines = fp.read_text(encoding=enc).splitlines()
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    continue
            except Exception:
                continue

            hits = []
            for i, line in enumerate(lines, 1):
                if keyword.lower() in line.lower():
                    hits.append(f"  L{i}: {line.strip()}")
            if hits:
                results.append(f"📄 {fp}:\n" + "\n".join(hits))

        if not results:
            return f"在 {root} 下未找到包含 {keyword!r} 的文件。"
        return "\n\n".join(results)
    except Exception as ex:
        return f"[异常] search_content({keyword}, {directory}): {ex}"


# ---------------------------------------------------------------------------
# 工具 4：write_file —— 写入文件
# ---------------------------------------------------------------------------
@tool
def write_file(path: str, content: str) -> str:
    """将 content 写入指定文件（覆盖写）。必要时会自动创建父目录。"""
    print(f"[TOOL] write_file  path={path}  content_len={len(content)}")
    try:
        p = Path(path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"[成功] 已写入 {len(content)} 字符到 {p}"
    except Exception as ex:
        return f"[异常] write_file({path}): {ex}"


# ---------------------------------------------------------------------------
# 工具 5：bash —— 执行 shell 命令（带安全防护）
# ---------------------------------------------------------------------------
_DANGEROUS_PATTERNS = [
    # Windows / Linux 通吃的危险操作
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\brm\s+(-[a-zA-Z]*[rRfF][a-zA-Z]*\s+)?/", re.IGNORECASE),
    re.compile(r"\bformat\s+[A-Z]:", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\binit\s+0\b"),
    re.compile(r"\bdel\s+/[sS]\s+/[qQ]", re.IGNORECASE),      # Windows del /s /q
    re.compile(r"\bRemove-Item\b.*-Recurse.*-Force", re.IGNORECASE),
    re.compile(r"\bchmod\s+[-+=]*[74][444]"),                 # chmod 777 类
    re.compile(r"\beval\s*\(\s*\$", re.IGNORECASE),           # eval($...)
    re.compile(r"\|\s*nc\b", re.IGNORECASE),                   # 反弹 shell
    re.compile(r"/dev/tcp/", re.IGNORECASE),
]


def _is_dangerous(cmd: str) -> Optional[str]:
    """返回第一个命中的危险模式描述；无则返回 None。"""
    for pat in _DANGEROUS_PATTERNS:
        if pat.search(cmd):
            return pat.pattern
    return None


@tool
def bash(command: str) -> str:
    """
    执行一条 shell / PowerShell 命令并返回 stdout+stderr。
    ⚠️ 已内置危险命令黑名单（rm -rf / shutdown / del /s /q 等），命中会被拒绝。
    """
    print(f"[TOOL] bash  command={command!r}")
    matched = _is_dangerous(command)
    if matched:
        return (f"[拒绝] 命令命中危险模式 {matched!r}，为了安全已阻止执行。"
                f"请换一条更温和的命令。")

    try:
        # Windows 用 PowerShell，其它用 bash
        if os.name == "nt":
            args = ["powershell", "-NoProfile", "-Command", command]
        else:
            args = ["bash", "-c", command]

        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if not out.strip():
            out = "(命令执行成功，但没有输出)"
        # 截断过长输出，避免撑爆 LLM context
        if len(out) > 4000:
            out = out[:4000] + "\n...(输出已截断)"
        return f"[exit={proc.returncode}]\n{out}"
    except subprocess.TimeoutExpired:
        return "[超时] 命令在 30 秒内未完成，已被终止。"
    except Exception as ex:
        return f"[异常] bash({command!r}): {ex}"


# ---------------------------------------------------------------------------
# 汇总：Agent 要用到的工具列表
# ---------------------------------------------------------------------------
ALL_TOOLS = [list_dir, read_file, search_content, write_file, bash]
