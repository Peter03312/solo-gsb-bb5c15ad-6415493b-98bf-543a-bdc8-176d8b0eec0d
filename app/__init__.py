"""Flask Web 应用：剧场无线话筒频点排演。"""
import hashlib
import json
import os

from pathlib import Path

from flask import Flask, jsonify, render_template, request

from . import db, scheduler, verification

BASE_DIR = Path(__file__).resolve().parent.parent
app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)


# ---------------------------------------------------------------- 工具函数

def _expire_solutions(conn):
    """输入发生变化：将所有已有方案标记为过期。"""
    conn.execute("UPDATE solutions SET expired = 1 WHERE expired = 0")


def _load_mics(conn):
    rows = conn.execute("SELECT * FROM mics ORDER BY id").fetchall()
    mics = []
    for r in rows:
        freqs = [x[0] for x in conn.execute(
            "SELECT freq FROM mic_freqs WHERE mic_id = ? ORDER BY freq",
            (r["id"],))]
        mics.append({
            "id": r["id"], "name": r["name"], "room_id": r["room_id"],
            "guard": r["guard"], "start_min": r["start_min"],
            "end_min": r["end_min"], "priority": r["priority"],
            "locked": bool(r["locked"]),
            "locked_freq": r["locked_freq"], "freqs": freqs,
        })
    return mics


def _load_interference_pairs(conn):
    return [(r["room_a_id"], r["room_b_id"])
            for r in conn.execute(
                "SELECT room_a_id, room_b_id FROM interference")]


def _input_hash(conn):
    """对当前全部排演输入计算哈希，用于标识方案对应的输入版本。"""
    rooms = [{"id": r["id"], "name": r["name"]}
             for r in conn.execute("SELECT id, name FROM rooms ORDER BY id")]
    pairs = [sorted((r["room_a_id"], r["room_b_id"]))
             for r in conn.execute(
                 "SELECT room_a_id, room_b_id FROM interference ORDER BY id")]
    mics = _load_mics(conn)
    for m in mics:
        m["freqs"] = sorted(m["freqs"])
        m["locked"] = 1 if m["locked"] else 0
        m["locked_freq"] = m["locked_freq"]
    payload = json.dumps(
        {"rooms": rooms, "pairs": sorted(pairs), "mics": mics},
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _validate_payload(data):
    """校验单个话筒的提交内容，返回规范化后的字典或错误字符串。"""
    if not isinstance(data, dict):
        return None, "请求体必须是 JSON 对象"
    name = (str(data.get("name") or "")).strip()
    if not name:
        return None, "话筒名称不能为空"

    room_id = data.get("room_id")
    room_id = int(room_id) if room_id not in (None, "", 0) else None

    try:
        guard = int(data.get("guard"))
    except (TypeError, ValueError):
        return None, f"设备「{name}」：保护间隔必须为整数(kHz)"
    try:
        start_min = int(data.get("start_min"))
        end_min = int(data.get("end_min"))
    except (TypeError, ValueError):
        return None, f"设备「{name}」：启用时段必须为整数分钟"

    priority = data.get("priority") == "high" and "high" or "normal"
    locked = bool(data.get("locked"))
    locked_freq = data.get("locked_freq")
    locked_freq = int(locked_freq) if locked_freq not in (None, "") else None

    raw_freqs = data.get("freqs")
    if isinstance(raw_freqs, str):
        parts = [p.strip() for p in raw_freqs.replace("，", ",").split(",")]
        parts = [p for p in parts if p]
    elif isinstance(raw_freqs, list):
        parts = raw_freqs
    else:
        parts = []
    try:
        freqs = sorted({int(f) for f in parts})
    except (TypeError, ValueError):
        return None, f"设备「{name}」：可用频点必须全部为整数(kHz)"

    return {
        "name": name, "room_id": room_id, "guard": guard,
        "start_min": start_min, "end_min": end_min, "priority": priority,
        "locked": locked, "locked_freq": locked_freq, "freqs": freqs,
    }, None


# ---------------------------------------------------------------- 页面

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------- 查询

@app.get("/api/state")
def get_state():
    conn = db.get_db()
    try:
        rooms = [dict(r) for r in conn.execute(
            "SELECT id, name FROM rooms ORDER BY id")]

        interference = [dict(r) for r in conn.execute("""
            SELECT i.id, i.room_a_id, i.room_b_id,
                   a.name AS room_a, b.name AS room_b
            FROM interference i
            JOIN rooms a ON a.id = i.room_a_id
            JOIN rooms b ON b.id = i.room_b_id
            ORDER BY i.id""")]

        room_names = {r["id"]: r["name"] for r in rooms}
        mics = _load_mics(conn)
        for m in mics:
            m["room_name"] = room_names.get(m["room_id"])

        sol_row = conn.execute(
            "SELECT * FROM solutions ORDER BY id DESC LIMIT 1").fetchone()
        solution = None
        if sol_row:
            assigns = [dict(r) for r in conn.execute("""
                SELECT a.mic_id, a.mic_name, a.freq, m.priority, m.locked
                FROM assignments a
                LEFT JOIN mics m ON m.id = a.mic_id
                WHERE a.solution_id = ?
                ORDER BY a.freq, a.id""", (sol_row["id"],))]
            try:
                unassigned = json.loads(sol_row["unassigned_json"] or "[]")
            except json.JSONDecodeError:
                unassigned = []
            solution = {
                "id": sol_row["id"],
                "created_at": sol_row["created_at"],
                "input_hash": sol_row["input_hash"],
                "status": sol_row["status"],
                "high_total": sol_row["high_total"],
                "total": sol_row["total"],
                "high_assigned": sol_row["high_assigned"],
                "assigned": sol_row["assigned"],
                "wall_time_s": sol_row["wall_time_s"],
                "expired": bool(sol_row["expired"]),
                "current_hash": _input_hash(conn),
                "assignments": assigns,
                "unassigned": unassigned,
            }

        verif_rows = conn.execute("""
            SELECT v.*, vs.payload_json AS snapshot_json
            FROM verifications v
            LEFT JOIN verification_snapshots vs
                   ON vs.verification_id = v.id
            ORDER BY v.id""").fetchall()
        verifications = [_verification_json(conn, r) for r in verif_rows]

        return jsonify(rooms=rooms, interference=interference,
                       mics=mics, solution=solution,
                       verifications=verifications)
    finally:
        conn.close()


# ---------------------------------------------------------------- 房间 / 互扰

@app.post("/api/rooms")
def create_room():
    name = (str((request.get_json(silent=True) or {}).get("name") or "")
            ).strip()
    if not name:
        return jsonify(error="房间名称不能为空"), 400
    conn = db.get_db()
    try:
        try:
            cur = conn.execute("INSERT INTO rooms(name) VALUES (?)", (name,))
        except Exception:
            return jsonify(error=f"房间「{name}」已存在"), 400
        _expire_solutions(conn)
        conn.commit()
        return jsonify(id=cur.lastrowid, name=name)
    finally:
        conn.close()


@app.delete("/api/rooms/<int:room_id>")
def delete_room(room_id):
    conn = db.get_db()
    try:
        used = conn.execute(
            "SELECT COUNT(*) FROM mics WHERE room_id = ?", (room_id,)).fetchone()[0]
        if used:
            names = "、".join(r[0] for r in conn.execute(
                "SELECT name FROM mics WHERE room_id = ?", (room_id,)))
            return jsonify(
                error=f"该房间下仍有话筒（{names}），请先改派或删除这些话筒"), 400
        conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        _expire_solutions(conn)
        conn.commit()
        return jsonify(ok=True)
    finally:
        conn.close()


@app.post("/api/interference")
def add_interference():
    data = request.get_json(silent=True) or {}
    try:
        a, b = int(data["room_a_id"]), int(data["room_b_id"])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="请选择两个房间"), 400
    if a == b:
        return jsonify(error="互扰关系必须选择两个不同的房间（同房间天然互扰）"), 400
    a, b = sorted((a, b))
    conn = db.get_db()
    try:
        ids = {r[0] for r in conn.execute("SELECT id FROM rooms")}
        if a not in ids or b not in ids:
            return jsonify(error="所选房间不存在"), 400
        exists = conn.execute(
            "SELECT 1 FROM interference WHERE room_a_id = ? AND room_b_id = ?",
            (a, b)).fetchone()
        if exists:
            return jsonify(error="该互扰关系已存在"), 400
        conn.execute(
            "INSERT INTO interference(room_a_id, room_b_id) VALUES (?, ?)",
            (a, b))
        _expire_solutions(conn)
        conn.commit()
        return jsonify(ok=True)
    finally:
        conn.close()


@app.delete("/api/interference/<int:pair_id>")
def delete_interference(pair_id):
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM interference WHERE id = ?", (pair_id,))
        _expire_solutions(conn)
        conn.commit()
        return jsonify(ok=True)
    finally:
        conn.close()


# ---------------------------------------------------------------- 话筒

@app.post("/api/mics")
def upsert_mic():
    data = request.get_json(silent=True) or {}
    mic, err = _validate_payload(data)
    if err:
        return jsonify(error=err), 400

    mic_id = data.get("id")
    mic_id = int(mic_id) if mic_id not in (None, "") else None
    conn = db.get_db()
    try:
        # 唯一性 + 内容校验（借助求解器的统一校验逻辑）
        dup = conn.execute(
            "SELECT id FROM mics WHERE name = ? AND id IS NOT ?",
            (mic["name"], mic_id)).fetchone()
        if dup:
            return jsonify(error=f"话筒「{mic['name']}」名称已存在"), 400
        if mic["room_id"] is not None and not conn.execute(
                "SELECT 1 FROM rooms WHERE id = ?", (mic["room_id"],)).fetchone():
            return jsonify(error="所选房间不存在"), 400

        check = dict(mic, id=mic_id or -1)
        errs = scheduler.validate_mics([check], [])
        if errs:
            return jsonify(error="；".join(
                (f"设备「{w}」：{e}" if w else e) for w, e in errs)), 400

        if mic_id is None:
            cur = conn.execute("""
                INSERT INTO mics(name, room_id, guard, start_min, end_min,
                                 priority, locked, locked_freq)
                VALUES (:name, :room_id, :guard, :start_min, :end_min,
                        :priority, :locked, :locked_freq)""",
                {"name": mic["name"], "room_id": mic["room_id"],
                 "guard": mic["guard"], "start_min": mic["start_min"],
                 "end_min": mic["end_min"], "priority": mic["priority"],
                 "locked": 1 if mic["locked"] else 0,
                 "locked_freq": mic["locked_freq"] if mic["locked"] else None})
            mic_id = cur.lastrowid
        else:
            row = conn.execute("SELECT id FROM mics WHERE id = ?",
                               (mic_id,)).fetchone()
            if not row:
                return jsonify(error="要修改的话筒不存在"), 404
            conn.execute("""
                UPDATE mics SET name=:name, room_id=:room_id, guard=:guard,
                    start_min=:start_min, end_min=:end_min, priority=:priority,
                    locked=:locked, locked_freq=:locked_freq
                WHERE id=:id""",
                {"id": mic_id, "name": mic["name"], "room_id": mic["room_id"],
                 "guard": mic["guard"], "start_min": mic["start_min"],
                 "end_min": mic["end_min"], "priority": mic["priority"],
                 "locked": 1 if mic["locked"] else 0,
                 "locked_freq": mic["locked_freq"] if mic["locked"] else None})
            conn.execute("DELETE FROM mic_freqs WHERE mic_id = ?", (mic_id,))

        conn.executemany(
            "INSERT INTO mic_freqs(mic_id, freq) VALUES (?, ?)",
            [(mic_id, f) for f in mic["freqs"]])
        _expire_solutions(conn)
        conn.commit()
        return jsonify(id=mic_id, ok=True)
    finally:
        conn.close()


@app.delete("/api/mics/<int:mic_id>")
def delete_mic(mic_id):
    conn = db.get_db()
    try:
        conn.execute("DELETE FROM mics WHERE id = ?", (mic_id,))
        _expire_solutions(conn)
        conn.commit()
        return jsonify(ok=True)
    finally:
        conn.close()


# ---------------------------------------------------------------- 编排

@app.post("/api/solve")
def run_solve():
    conn = db.get_db()
    try:
        mics = _load_mics(conn)
        pairs = _load_interference_pairs(conn)
        try:
            result = scheduler.solve(mics, pairs)
        except scheduler.SchedulingError as e:
            return jsonify(error=str(e)), 409

        _expire_solutions(conn)
        cur = conn.execute("""
            INSERT INTO solutions(created_at, input_hash, status, high_total,
                                  total, high_assigned, assigned, objective,
                                  wall_time_s, unassigned_json, expired)
            VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
            (db.now_iso(), _input_hash(conn),
             "optimal" if result["optimal"] else "feasible",
             result["high_total"], result["total"],
             result["high_assigned"], result["assigned"],
             result["objective"], result["wall_time_s"],
             result["unassigned_json"]))
        sol_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO assignments(solution_id, mic_id, mic_name, freq)"
            " VALUES (?,?,?,?)",
            [(sol_id, a["id"], a["name"], a["freq"])
             for a in result["assignments"]])
        conn.commit()
        return jsonify(id=sol_id, ok=True,
                       assigned=result["assigned"], total=result["total"],
                       high_assigned=result["high_assigned"],
                       high_total=result["high_total"])
    finally:
        conn.close()


# ---------------------------------------------------------------- 现场核验

def _load_snapshot_inputs(conn, sol_row):
    """按方案冻结时刻重建核验快照所需的全部输入。

    快照直接使用方案自身的分配结果；话筒属性 / 房间 / 互扰读取当前表，
    因此「开启核验」必须以最新且未过期的方案为前提，开启后再被修改也
    不会影响已冻结的快照。
    """
    sol_id = sol_row["id"]
    rooms = [dict(r) for r in conn.execute(
        "SELECT id, name FROM rooms ORDER BY id")]
    pairs = _load_interference_pairs(conn)
    mics = _load_mics(conn)
    assignments = [{"mic_id": r["mic_id"], "freq": r["freq"]}
                   for r in conn.execute(
                       "SELECT mic_id, freq FROM assignments WHERE solution_id = ?",
                       (sol_id,))]
    return rooms, pairs, mics, assignments


def _verification_json(conn, row, with_detail=True):
    """序列化一条核验记录（含快照、上报区间与报告）。"""
    snap = verification.loads(row["snapshot_json"])
    entries = [dict(r) for r in conn.execute("""
        SELECT mic_id, mic_name, start_min, end_min, freq
        FROM verification_entries
        WHERE verification_id = ?
        ORDER BY start_min, end_min, id""", (row["id"],))]
    report = None
    if row["report_json"]:
        report = json.loads(row["report_json"])
    item = {
        "id": row["id"],
        "created_at": row["created_at"],
        "solution_id": row["solution_id"],
        "note": row["note"],
        "revision": row["revision"],
        "submitted": report is not None,
        "entries": entries,
    }
    if with_detail:
        item["snapshot"] = snap
        item["report"] = report
    return item


@app.post("/api/verifications")
def start_verification():
    """从最新且未过期的方案开启一次现场执行核验，冻结方案与相关输入。"""
    conn = db.get_db()
    try:
        sol_row = conn.execute(
            "SELECT * FROM solutions ORDER BY id DESC LIMIT 1").fetchone()
        if sol_row is None:
            return jsonify(error="尚无编排方案，无法开启核验，请先执行编排"), 409
        if sol_row["expired"] or sol_row["input_hash"] != _input_hash(conn):
            return jsonify(
                error="最新方案已过期（录入在编排后发生过变更），"
                      "请重新编排后再开启核验"), 409

        rooms, pairs, mics, assignments = _load_snapshot_inputs(conn, sol_row)
        snap = verification.build_snapshot(rooms, pairs, mics, assignments)
        data = request.get_json(silent=True) or {}
        note = str(data.get("note") or "").strip() or None

        cur = conn.execute(
            "INSERT INTO verifications(created_at, solution_id, note,"
            " revision, report_json) VALUES (?,?,?,1,NULL)",
            (db.now_iso(), sol_row["id"], note))
        vid = cur.lastrowid
        conn.execute(
            "INSERT INTO verification_snapshots(verification_id, payload_json)"
            " VALUES (?,?)", (vid, verification.dumps(snap)))
        conn.commit()
        return jsonify(id=vid, solution_id=sol_row["id"], revision=1, ok=True)
    finally:
        conn.close()


@app.get("/api/verifications")
def list_verifications():
    conn = db.get_db()
    try:
        rows = conn.execute("""
            SELECT v.*, vs.payload_json AS snapshot_json
            FROM verifications v
            LEFT JOIN verification_snapshots vs
                   ON vs.verification_id = v.id
            ORDER BY v.id""").fetchall()
        return jsonify(verifications=[_verification_json(conn, r) for r in rows])
    finally:
        conn.close()


@app.get("/api/verifications/<int:vid>")
def get_verification(vid):
    conn = db.get_db()
    try:
        row = conn.execute("""
            SELECT v.*, vs.payload_json AS snapshot_json
            FROM verifications v
            LEFT JOIN verification_snapshots vs
                   ON vs.verification_id = v.id
            WHERE v.id = ?""", (vid,)).fetchone()
        if row is None:
            return jsonify(error="核验记录不存在"), 404
        return jsonify(_verification_json(conn, row))
    finally:
        conn.close()


@app.put("/api/verifications/<int:vid>")
def submit_verification(vid):
    """携核验修订号提交完整替代区间集。旧修订号拒绝且不部分写入。"""
    data = request.get_json(silent=True)
    if data is None:
        return jsonify(error="请求体必须为 JSON"), 400
    conn = db.get_db()
    try:
        row = conn.execute("""
            SELECT v.*, vs.payload_json AS snapshot_json
            FROM verifications v
            JOIN verification_snapshots vs ON vs.verification_id = v.id
            WHERE v.id = ?""", (vid,)).fetchone()
        if row is None:
            return jsonify(error="核验记录不存在"), 404
        snap = verification.loads(row["snapshot_json"])

        try:
            revision, entries = verification.validate_submission(data, snap)
        except verification.RevisionMismatch as e:
            return jsonify(error=str(e), current_revision=row["revision"]), 409
        except verification.VerificationError as e:
            return jsonify(error=str(e)), 400

        if revision != row["revision"]:
            return jsonify(
                error=f"修订号 {revision} 已过期：核验 #{vid} 当前修订号为 "
                      f"{row['revision']}，整份提交已拒绝（未写入任何区间）",
                current_revision=row["revision"]), 409

        report = verification.build_report(snap, entries)
        # 全量替代：同一事务内先清空旧区间再写入，失败回滚不产生部分写入
        conn.execute(
            "DELETE FROM verification_entries WHERE verification_id = ?", (vid,))
        conn.executemany("""
            INSERT INTO verification_entries(verification_id, mic_id, mic_name,
                                             start_min, end_min, freq)
            VALUES (?,?,?,?,?,?)""",
            [(vid, mic_id, mic_name, s, e, f)
             for mic_id, mic_name, segs in entries
             for s, e, f in segs])
        conn.execute(
            "UPDATE verifications SET revision = revision + 1, report_json = ?"
            " WHERE id = ?", (json.dumps(report, ensure_ascii=False), vid))
        conn.commit()
        return jsonify(ok=True, id=vid, revision=revision + 1,
                       counts=report["counts"])
    finally:
        conn.close()


# ---------------------------------------------------------------- 演示 / 重置

@app.post("/api/demo")
def load_demo():
    if db.seed_demo():
        return jsonify(ok=True)
    return jsonify(error="当前已有房间或话筒数据，为避免覆盖请先清空"), 409


@app.post("/api/reset")
def reset_all():
    conn = db.get_db()
    try:
        for table in ("verification_entries", "verification_snapshots",
                      "verifications",
                      "assignments", "solutions", "mic_freqs",
                      "mics", "interference", "rooms"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        return jsonify(ok=True)
    finally:
        conn.close()


# ---------------------------------------------------------------- 入口

def main():
    db.init_db()
    if os.environ.get("SEED_DEMO", "1") == "1":
        db.seed_demo()
    port = int(os.environ.get("PORT", "8000"))
    if os.environ.get("FLASK_DEBUG") == "1":
        app.run(host="0.0.0.0", port=port, debug=True)
    else:
        from waitress import serve
        print(f"剧场话筒频点排演系统已启动: http://0.0.0.0:{port}")
        serve(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
