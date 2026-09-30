"""SQLite 数据访问层。"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "theater.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS interference (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_a_id   INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    room_b_id   INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    UNIQUE(room_a_id, room_b_id)
);

CREATE TABLE IF NOT EXISTS mics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    room_id     INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
    guard       INTEGER NOT NULL,
    start_min   INTEGER NOT NULL,
    end_min     INTEGER NOT NULL,
    priority    TEXT NOT NULL DEFAULT 'normal',
    locked      INTEGER NOT NULL DEFAULT 0,
    locked_freq INTEGER
);

CREATE TABLE IF NOT EXISTS mic_freqs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mic_id  INTEGER NOT NULL REFERENCES mics(id) ON DELETE CASCADE,
    freq    INTEGER NOT NULL,
    UNIQUE(mic_id, freq)
);

CREATE TABLE IF NOT EXISTS solutions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    input_hash    TEXT NOT NULL,
    status        TEXT NOT NULL,
    high_total    INTEGER NOT NULL,
    total         INTEGER NOT NULL,
    high_assigned INTEGER NOT NULL,
    assigned      INTEGER NOT NULL,
    objective     INTEGER,
    wall_time_s   REAL,
    unassigned_json TEXT,
    expired       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    solution_id INTEGER NOT NULL REFERENCES solutions(id) ON DELETE CASCADE,
    mic_id      INTEGER REFERENCES mics(id) ON DELETE SET NULL,
    mic_name    TEXT NOT NULL,
    freq        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS verifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    solution_id   INTEGER NOT NULL REFERENCES solutions(id),
    note          TEXT,
    revision      INTEGER NOT NULL DEFAULT 1,
    report_json   TEXT
);

CREATE TABLE IF NOT EXISTS verification_snapshots (
    verification_id INTEGER PRIMARY KEY
        REFERENCES verifications(id) ON DELETE CASCADE,
    payload_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    verification_id INTEGER NOT NULL
        REFERENCES verifications(id) ON DELETE CASCADE,
    mic_id          INTEGER,
    mic_name        TEXT NOT NULL,
    start_min       INTEGER NOT NULL,
    end_min         INTEGER NOT NULL,
    freq            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verif_entries_vid
    ON verification_entries(verification_id);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 演示数据

def seed_demo():
    """首次使用时填入一套演示数据。已存在话筒或房间时不执行。"""
    conn = get_db()
    try:
        if conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]:
            return False
        if conn.execute("SELECT COUNT(*) FROM mics").fetchone()[0]:
            return False

        rooms = {}
        for name in ("主舞台", "侧舞台", "休息室"):
            cur = conn.execute("INSERT INTO rooms(name) VALUES (?)", (name,))
            rooms[name] = cur.lastrowid
        pairs = [("主舞台", "侧舞台"), ("侧舞台", "休息室")]
        for a, b in pairs:
            conn.execute(
                "INSERT INTO interference(room_a_id, room_b_id) VALUES (?, ?)",
                (rooms[a], rooms[b]),
            )

        # (名称, 房间, 保护间隔, 开始, 结束, 优先级, 是否锁定, 锁定频点, 频点列表)
        mics = [
            ("主持1号", "主舞台", 25, "19:00", "21:30", "high",   1, 600, [600, 610, 620]),
            ("主持2号", "主舞台", 25, "19:00", "21:30", "high",   0, None, [625, 640, 650]),
            ("演唱3号", "主舞台", 20, "20:00", "22:00", "high",   0, None, [580, 605, 645]),
            ("互动4号", "侧舞台", 20, "20:30", "21:00", "high",   0, None, [630, 650]),
            ("休息5号", "休息室", 10, "19:30", "20:00", "normal", 0, None, [600, 610, 620]),
            ("侧边6号", "侧舞台", 20, "19:15", "21:45", "normal", 0, None, [570, 590, 615, 645]),
            ("备用7号", "主舞台", 15, "21:30", "22:30", "normal", 0, None, [590, 600, 610]),
        ]
        for name, room, guard, st, en, prio, locked, lf, freqs in mics:
            cur = conn.execute(
                """INSERT INTO mics(name, room_id, guard, start_min, end_min,
                                    priority, locked, locked_freq)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (name, rooms[room], guard, _to_min(st), _to_min(en),
                 prio, locked, lf),
            )
            mic_id = cur.lastrowid
            for f in freqs:
                conn.execute(
                    "INSERT INTO mic_freqs(mic_id, freq) VALUES (?, ?)",
                    (mic_id, f),
                )
        conn.commit()
        return True
    finally:
        conn.close()


def _to_min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)
