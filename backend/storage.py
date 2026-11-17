import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("STUDY_DATA_DIR", str(ROOT / "data"))).resolve()


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA / "study.sqlite3", timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    from .models import StudyGuide
    from .sample import DEMO_GUIDE, DEMO_ID, DEMO_SOURCE

    with db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS materials (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, pages TEXT NOT NULL,
                warnings TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chapters (
                id TEXT PRIMARY KEY, course TEXT NOT NULL, textbook TEXT NOT NULL,
                title TEXT NOT NULL, mode TEXT NOT NULL, goal TEXT NOT NULL,
                guide TEXT NOT NULL, sources TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                notes_revision INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, stage TEXT NOT NULL,
                chapter_id TEXT, error TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                question_index INTEGER NOT NULL, choice INTEGER NOT NULL,
                correct INTEGER NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY (chapter_id, question_index)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                question TEXT NOT NULL, answer TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chapter ON messages(chapter_id, id);
            CREATE TABLE IF NOT EXISTS learning_rewards (
                event_key TEXT PRIMARY KEY, points INTEGER NOT NULL,
                kind TEXT NOT NULL, earned_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_days (
                day TEXT NOT NULL, question_key TEXT NOT NULL,
                PRIMARY KEY(day,question_key)
            );
            CREATE TABLE IF NOT EXISTS backup_imports (
                source_id TEXT NOT NULL, digest TEXT NOT NULL,
                chapter_id TEXT NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL, PRIMARY KEY (source_id, digest)
            );
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(chapters)")}
        if "notes_revision" not in columns:
            conn.execute("ALTER TABLE chapters ADD COLUMN notes_revision INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE jobs SET status='failed', stage='任务已中断', error='程序上次关闭时生成尚未完成，请重新生成。' WHERE status IN ('queued','running')")
        guide = StudyGuide.model_validate(DEMO_GUIDE).model_dump()
        conn.execute("""INSERT OR IGNORE INTO chapters
            (id,course,textbook,title,mode,goal,guide,sources,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""", (
            DEMO_ID, "计算机网络", "内置教学示例 · 非教材原文", "网络层：让数据找到方向",
            "demo", "understand", dumps(guide),
            dumps([{"id": "demo-p1", "label": "内置示例材料", "text": DEMO_SOURCE}]), now(),
        ))


def message_page(conn, chapter_id, limit, before=None):
    if limit == 0:
        return {"messages": [], "messages_has_more": False, "messages_cursor": None}
    sql = "SELECT id,question,answer,created_at FROM messages WHERE chapter_id=?"
    params = [chapter_id]
    if before is not None:
        sql += " AND id<?"
        params.append(before)
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit + 1)).fetchall()
    has_more = len(rows) > limit
    messages = [dict(row) for row in reversed(rows[:limit])]
    return {"messages": messages, "messages_has_more": has_more,
            "messages_cursor": messages[0]["id"] if has_more else None}


def get_chapter(chapter_id, message_limit=None):
    with db() as conn:
        conn.execute("BEGIN")
        row = conn.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["guide"] = json.loads(item["guide"])
        item["sources"] = json.loads(item["sources"])
        item["completed"] = bool(item["completed"])
        item["attempts"] = [dict(r) for r in conn.execute("SELECT question_index,choice,correct FROM attempts WHERE chapter_id=?", (chapter_id,))]
        if message_limit is None:
            item["messages"] = [dict(r) for r in conn.execute("SELECT id,question,answer,created_at FROM messages WHERE chapter_id=? ORDER BY id", (chapter_id,))]
        else:
            item.update(message_page(conn, chapter_id, message_limit))
        return item
