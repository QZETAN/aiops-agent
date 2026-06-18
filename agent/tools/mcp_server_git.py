"""
MCP Server: Git 代码变更查询工具。
让 AI Agent 能查询故障时间附近的代码提交记录。

Phase 4 安全加固：repo_path 白名单校验，防止 LLM 被诱导查询敏感路径。
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("aiops.mcp-git")

mcp = FastMCP("mcp-server-git")

# ============================================================================
# 安全校验
# ============================================================================

# 禁止访问的系统路径（防止 LLM 被 prompt injection 诱导查询敏感目录）
_BLOCKED_PATHS = [
    "/etc", "/proc", "/sys", "/root", "/var/log", "/tmp",
    "C:\\Windows", "C:\\Windows\\System32", "C:\\Program Files",
]


def _validate_repo_path(repo_path: str) -> str | None:
    """
    校验 repo_path 是否安全。返回 None 表示通过，否则返回错误信息。

    安全策略：
      1. 拒绝包含 .. 的路径（路径穿越攻击）
      2. 拒绝系统敏感目录
      3. 如果配置了 GIT_REPO_PATH 环境变量，只允许该路径及其子目录
    """
    # 规则 1：禁止路径穿越
    if ".." in repo_path:
        return "拒绝访问：路径中包含 '..'（路径穿越）"

    # 规则 2：禁止系统敏感路径（同时检查原始路径和解析后路径）
    raw_lower = repo_path.lower().replace("\\", "/")
    resolved_lower = str(Path(repo_path).resolve()).lower().replace("\\", "/")
    for blocked in _BLOCKED_PATHS:
        blocked_lower = blocked.lower().replace("\\", "/")
        if raw_lower.startswith(blocked_lower) or resolved_lower.startswith(blocked_lower):
            return f"拒绝访问：{repo_path} 是受保护的系统路径"

    # 规则 3：白名单模式 —— 如果配置了 GIT_REPO_PATH，只允许该路径
    allowed = os.environ.get("GIT_REPO_PATH", "")
    if allowed:
        allowed_normalized = str(Path(allowed).resolve()).lower()
        if not normalized.startswith(allowed_normalized):
            return f"拒绝访问：{repo_path} 不在允许的路径范围内（GIT_REPO_PATH={allowed}）"

    return None  # 通过校验


def _parse_git_log(stdout_text: str) -> list[dict]:
    """将 git log --format 输出解析为 commit 列表。字段间用 \\0 分隔。"""
    commits = []
    for line in stdout_text.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\0")
        if len(parts) >= 4:
            commits.append({
                "hash": parts[0].strip(),
                "author": parts[1].strip(),
                "timestamp": parts[2].strip(),
                "message": parts[3].strip(),
            })
    return commits


@mcp.tool()
async def get_recent_commits(repo_path: str, hours: int = 24, limit: int = 20) -> str:
    """
    查询 Git 仓库最近 N 小时的提交记录。

    Args:
        repo_path: Git 仓库本地路径
        hours: 最近多少小时
        limit: 最多返回条数

    Returns:
        commits 数组 + 查询时间范围
    """
    # 安全校验
    error = _validate_repo_path(repo_path)
    if error:
        logger.warning("Git 路径校验失败: %s", error)
        return json.dumps({"error": error, "repo_path": repo_path}, ensure_ascii=False)

    logger.info("get_recent_commits: repo_path=%s, hours=%d", repo_path, hours)

    since_ts = (datetime.now() - timedelta(hours=hours)).isoformat()
    cmd = ["git", "-C", repo_path, "log", f"--since={since_ts}", f"--max-count={limit}", "--format=%H\0%an\0%ai\0%s"]

    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return json.dumps({"error": "Git 命令执行超时（>15秒）", "repo_path": repo_path}, ensure_ascii=False)

        if proc.returncode != 0:
            return json.dumps({"error": f"Git 命令失败: {stderr.decode()}", "repo_path": repo_path}, ensure_ascii=False)

        commits = _parse_git_log(stdout.decode("utf-8", errors="replace"))
        logger.info(f"get_recent_commits: {len(commits)} commits")
        return json.dumps({"commits": commits, "total": len(commits), "since": since_ts, "repo_path": repo_path}, ensure_ascii=False)

    except FileNotFoundError:
        return json.dumps({"error": "系统中未找到 git 命令", "repo_path": repo_path}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"查询异常: {type(e).__name__}: {str(e)}", "repo_path": repo_path}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
