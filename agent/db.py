"""
诊断记录持久化模块 —— SQLite 零依赖存储。

每次诊断完成后自动存入数据库，支持按时间/服务/故障类型查询和统计。

===========================================================================
为什么用 SQLite 而不是 MySQL？
===========================================================================

  这个工具每天诊断几十到几百次，单条记录 < 10KB。
  SQLite 单文件、零配置、Python 标准库自带、轻松处理百万级记录。
  不需要起一个 MySQL 服务来存几千条诊断——那是杀鸡用牛刀。

  如果将来真的需要 MySQL（比如多人协作看同一份诊断记录），
  把 sqlite3.connect() 换成 mysql-connector 的连接即可，SQL 语法不变。

===========================================================================
表结构
===========================================================================

  diagnoses
  ├── id               自增主键
  ├── diagnosis_id     诊断 UUID（可读短 ID，如 "a1b2c3d4"）
  ├── alert_text       告警原文
  ├── status           completed / error / aborted
  ├── services         涉及的服务名（JSON 数组）
  ├── root_cause       根因摘要
  ├── confidence       置信度 0.0～1.0
  ├── steps            调度轮数
  ├── elapsed_seconds  诊断耗时（秒）
  ├── report_json      完整诊断报告（JSON）
  ├── error_message    错误信息（status=error 时）
  └── created_at       创建时间（本地时间）
"""

import json
import logging
import sqlite3
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("aiops.db")

# ============================================================================
# 数据库文件路径
# ============================================================================

# 从环境变量读取，默认在项目 data/ 目录下
_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "diagnoses.db"
_DB_PATH = Path(__import__("os").environ.get("AIOPS_DB_PATH", str(_DEFAULT_DB_PATH)))


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接（线程安全：每个线程独立连接）。"""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 写不阻塞读
    conn.execute("PRAGMA busy_timeout=3000")  # 3 秒超时
    return conn


# ============================================================================
# 初始化
# ============================================================================

def init_db() -> None:
    """创建表和索引（幂等，首次调用时自动执行）。"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS diagnoses (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            diagnosis_id   TEXT    UNIQUE NOT NULL,
            alert_text     TEXT    NOT NULL,
            status         TEXT    NOT NULL DEFAULT 'completed',
            services       TEXT    DEFAULT '[]',
            root_cause     TEXT    DEFAULT '',
            confidence     REAL    DEFAULT 0.0,
            steps          INTEGER DEFAULT 0,
            elapsed_seconds REAL   DEFAULT 0.0,
            report_json    TEXT    DEFAULT '{}',
            error_message  TEXT    DEFAULT '',
            created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_diag_created
        ON diagnoses(created_at DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_diag_status
        ON diagnoses(status)
    """)
    conn.commit()
    conn.close()
    logger.info("数据库就绪: %s", _DB_PATH)


# ============================================================================
# 写入
# ============================================================================

def save_diagnosis(result: dict) -> int:
    """
    保存一次诊断记录。

    result 字典结构（由 app.py 或 server.py 构造）：
      {
        "diagnosis_id": "a1b2c3d4",
        "alert_text": "...",
        "status": "completed" | "error",
        "services": ["svc-a", "svc-b"],
        "root_cause": "根因摘要",
        "confidence": 0.85,
        "steps": 5,
        "elapsed_seconds": 12.3,
        "report": {...},      # 完整报告 dict
        "error": "..."        # 仅 status=error 时有
      }

    返回新记录的 id。
    """
    init_db()  # 确保表存在

    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO diagnoses
        (diagnosis_id, alert_text, status, services, root_cause, confidence,
         steps, elapsed_seconds, report_json, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result.get("diagnosis_id", ""),
        result.get("alert_text", ""),
        result.get("status", "completed"),
        json.dumps(result.get("services", []), ensure_ascii=False),
        result.get("root_cause", ""),
        result.get("confidence", 0.0),
        result.get("steps", 0),
        result.get("elapsed_seconds", 0.0),
        json.dumps(result.get("report", {}), ensure_ascii=False),
        result.get("error", ""),
    ))
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    logger.debug("诊断记录已保存: id=%d, diag=%s, status=%s", row_id, result.get("diagnosis_id"), result.get("status"))
    return row_id


# ============================================================================
# 查询
# ============================================================================

def get_recent(limit: int = 20) -> list[dict]:
    """获取最近 N 条诊断记录。"""
    init_db()
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM diagnoses ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_by_id(diagnosis_id: str) -> Optional[dict]:
    """按 diagnosis_id 查询单条记录。"""
    init_db()
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM diagnoses WHERE diagnosis_id = ?", (diagnosis_id,)
    ).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


# ============================================================================
# 统计分析
# ============================================================================

def get_stats(days: int = 30) -> dict:
    """
    获取最近 N 天的诊断统计。

    返回结构：
      {
        "period_days": 30,
        "total": 150,             总诊断数
        "completed": 140,         成功数
        "error": 10,              失败数
        "success_rate": 0.93,     成功率
        "avg_confidence": 0.82,   平均置信度
        "avg_steps": 4.5,         平均调度轮数
        "avg_seconds": 15.2,      平均耗时
        "by_date": [...],         按日期分布
        "top_services": [...],    最多故障的服务 Top 10
        "top_root_causes": [...], 最常见根因 Top 5
      }
    """
    init_db()
    conn = _get_conn()
    since = f"-{days} days"

    # 总体指标
    row = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed,
            SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as error,
            AVG(confidence) as avg_conf,
            AVG(steps) as avg_steps,
            AVG(elapsed_seconds) as avg_elapsed
        FROM diagnoses
        WHERE created_at >= datetime('now','localtime',?)
    """, (since,)).fetchone()

    total = row["total"] or 0

    # 按日期分布
    by_date_rows = conn.execute("""
        SELECT date(created_at) as d, COUNT(*) as c, AVG(confidence) as ac
        FROM diagnoses
        WHERE created_at >= datetime('now','localtime',?)
        GROUP BY d ORDER BY d
    """, (since,)).fetchall()

    # 按状态
    status_rows = conn.execute("""
        SELECT status, COUNT(*) as c
        FROM diagnoses
        WHERE created_at >= datetime('now','localtime',?)
        GROUP BY status
    """, (since,)).fetchall()

    # 所有记录用于服务聚合和根因提取
    all_rows = conn.execute("""
        SELECT services, root_cause, confidence
        FROM diagnoses
        WHERE created_at >= datetime('now','localtime',?)
        ORDER BY created_at DESC
    """, (since,)).fetchall()

    conn.close()

    # 服务聚合
    svc_counter: Counter = Counter()
    svc_confidences: dict[str, list[float]] = {}
    for r in all_rows:
        try:
            services = json.loads(r["services"])
            for svc in services:
                svc_counter[svc] += 1
                if svc not in svc_confidences:
                    svc_confidences[svc] = []
                svc_confidences[svc].append(r["confidence"])
        except (json.JSONDecodeError, TypeError):
            pass

    top_services = [
        {
            "service": svc,
            "count": count,
            "avg_confidence": round(sum(svc_confidences.get(svc, [0])) / max(len(svc_confidences.get(svc, [0])), 1), 2),
        }
        for svc, count in svc_counter.most_common(10)
    ]

    # 根因聚合
    cause_counter: Counter = Counter()
    for r in all_rows:
        cause = (r["root_cause"] or "").strip()
        if cause and len(cause) > 3:
            # 截取前 80 字做聚合
            cause_counter[cause[:80]] += 1

    top_root_causes = [
        {"cause": cause, "count": count}
        for cause, count in cause_counter.most_common(5)
    ]

    return {
        "period_days": days,
        "total": total,
        "completed": row["completed"] or 0,
        "error": row["error"] or 0,
        "success_rate": round((row["completed"] or 0) / total, 4) if total > 0 else 0,
        "avg_confidence": round(row["avg_conf"] or 0, 2),
        "avg_steps": round(row["avg_steps"] or 0, 1),
        "avg_seconds": round(row["avg_elapsed"] or 0, 1),
        "by_date": [
            {"date": r["d"], "count": r["c"], "avg_confidence": round(r["ac"] or 0, 2)}
            for r in by_date_rows
        ],
        "top_services": top_services,
        "top_root_causes": top_root_causes,
    }


# ============================================================================
# 辅助
# ============================================================================

def _row_to_dict(row: sqlite3.Row) -> dict:
    """将 sqlite3.Row 转为普通 dict，还原 JSON 字段。"""
    d = dict(row)
    # 尝试还原 JSON 字段
    for key in ("services",):
        try:
            d[key] = json.loads(d[key])
        except (json.JSONDecodeError, TypeError):
            pass
    for key in ("report_json",):
        try:
            d["report"] = json.loads(d.pop(key, "{}"))
        except (json.JSONDecodeError, TypeError):
            d["report"] = {}
    # 删除 report_json key（已转为 report）
    d.pop("report_json", None)
    return d
