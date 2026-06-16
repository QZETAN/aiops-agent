"""
MCP Server: Git 代码变更查询工具。
让 AI Agent 能查询故障时间附近的代码提交记录。
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("mcp-git")

mcp = FastMCP("mcp-server-git")


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
    logger.info(f"get_recent_commits: repo_path={repo_path}, hours={hours}")

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
