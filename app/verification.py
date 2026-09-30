"""现场执行核验：快照冻结、上报校验（乐观修订号）与报告生成。

本模块只处理纯数据，与 Flask / SQLite 解耦，便于直接测试。

核心概念
========
* snapshot —— 开启核验瞬间冻结的方案与输入（房间、互扰对、话筒属性、
  方案分配频点）。此后录入被修改 / 话筒被删除或改名，均不影响核验。
* report —— 操作员按话筒 ID 上报的实际开机区间与实际频点，是对上次
  上报的**完整替代**；提交时必须携带当前修订号（revision），旧号拒绝
  且不做任何部分写入。
* 报告输出四类条目：
    conflicts  —— 实际上机的两台话筒同房间 / 互扰房间、开机区间重叠
                  且频差 < 双方保护间隔较大值；
    deviations —— 实际频点偏离计划频点 / 锁定频点 / 可用列表，或计划
                  未获配却开机；
    unplanned  —— （并入 deviations，type 为 unplanned_power）
    unknown    —— 计划启用时段中已获配、但没有任何实际上报覆盖的部分；
                  不推定正常也不推定冲突。
"""
import json


class VerificationError(Exception):
    """上报内容非法（未知话筒 / 非法时段或频点 / 区间重叠等）。"""


class RevisionMismatch(Exception):
    """修订号过期，整份提交被拒绝。"""


# 偏差类型 -> 中文说明
REASON_TEXT = {
    "freq_not_planned": "实际频点偏离计划分配频点",
    "freq_not_locked": "实际频点偏离锁定频点",
    "freq_not_allowed": "实际频点不在可用频点列表内",
    "unplanned_power": "计划未获配却开机",
}


# ------------------------------------------------------------------ 快照

def build_snapshot(rooms, pairs, mics, assignments):
    """构造冻结快照（全部为可 JSON 序列化的简单类型）。

    rooms:      [{id, name}]
    pairs:      [(room_a_id, room_b_id), ...]
    mics:       _load_mics 风格的话筒字典（含 freqs）
    assignments: [{mic_id, freq}]
    """
    frozen_mics = {}
    for m in mics:
        frozen_mics[str(m["id"])] = {
            "id": m["id"],
            "name": m["name"],
            "room_id": m["room_id"],
            "guard": m["guard"],
            "start_min": m["start_min"],
            "end_min": m["end_min"],
            "priority": m["priority"],
            "locked": bool(m["locked"]),
            "locked_freq": m["locked_freq"],
            "freqs": sorted(m["freqs"]),
            "assigned_freq": None,
        }
    for a in assignments:
        key = str(a["mic_id"])
        if key in frozen_mics:
            frozen_mics[key]["assigned_freq"] = a["freq"]
    return {
        "rooms": [{"id": r["id"], "name": r["name"]} for r in rooms],
        "interference": [sorted([int(x), int(y)]) for x, y in pairs],
        "mics": frozen_mics,
    }


def dumps(snapshot):
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True)


def loads(payload):
    return json.loads(payload or "{}")


# ------------------------------------------------------------------ 校验

def _is_int(v):
    """JSON 布尔不算整数。"""
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_segments(raw, errors, label):
    """解析并校验一台话筒的区间列表，返回 [(start,end,freq), ...]（已排序）。

    区间端点必须为 0..1440 的整数且 start < end；区间可首尾相接，
    同一话筒的区间不得重叠。校验失败的区间仍返回（用于重叠判定），
    错误统一收集到 errors。
    """
    segs = []
    if not isinstance(raw, list):
        errors.append(f"话筒「{label}」：interval 必须是区间数组")
        return segs
    for k, item in enumerate(raw):
        if isinstance(item, dict):
            st, en, fq = item.get("start_min"), item.get("end_min"), item.get("freq")
        elif isinstance(item, (list, tuple)) and len(item) == 3:
            st, en, fq = item
        else:
            errors.append(f"话筒「{label}」：第 {k + 1} 个区间格式非法，"
                          "应为 [开始,结束,频点] 或 {start_min,end_min,freq}")
            continue
        ok = True
        if not (_is_int(st) and _is_int(en) and 0 <= st <= 1440
                and 0 <= en <= 1440 and st < en):
            errors.append(
                f"话筒「{label}」：第 {k + 1} 个区间 [{st},{en}) 非法，"
                "要求 0≤开始<结束≤1440 的整数分钟")
            ok = False
        if not (_is_int(fq) and fq >= 0):
            errors.append(f"话筒「{label}」：第 {k + 1} 个区间频点 {fq!r} 非法，"
                          "必须为不小于 0 的整数(kHz)")
            ok = False
        if ok:
            segs.append((st, en, fq))
    segs.sort()
    for k in range(1, len(segs)):
        if segs[k][0] < segs[k - 1][1]:
            errors.append(
                f"话筒「{label}」：区间 [{segs[k-1][0]},{segs[k-1][1]}) 与 "
                f"[{segs[k][0]},{segs[k][1]}) 重叠（区间可首尾相接但不得重叠）")
    return segs


def validate_submission(data, snapshot):
    """校验完整替代上报。

    成功返回 {mic_id: [(start,end,freq), ...]}（保持请求顺序的话筒映射）。
    非法时抛出 VerificationError（中文，含全部错误，不做任何写入）。
    """
    if not isinstance(data, dict):
        raise VerificationError("请求体必须是 JSON 对象，"
                                "形如 {\"revision\":1,\"report\":[...]}")
    revision = data.get("revision")
    if not _is_int(revision) or revision < 1:
        raise VerificationError("revision 必须为不小于 1 的整数（当前核验修订号）")

    report = data.get("report")
    if isinstance(report, dict) and "mics" in report:
        report = report["mics"]
    if report is None:
        report = []
    if not isinstance(report, list):
        raise VerificationError("report 必须是按话筒上报的数组")

    mics = snapshot["mics"]
    errors = []
    seen = set()
    entries = []
    for idx, item in enumerate(report):
        if not isinstance(item, dict):
            errors.append(f"第 {idx + 1} 条上报必须是对象 "
                          "{mic_id, intervals:[[开始,结束,频点],...]}")
            continue
        mic_id = item.get("mic_id")
        if not _is_int(mic_id):
            errors.append(f"第 {idx + 1} 条上报：mic_id 必须为整数")
            continue
        key = str(mic_id)
        if key not in mics:
            errors.append(f"话筒 ID {mic_id} 不在冻结方案中（未知话筒，"
                          "可能从未录入；已删除/改名话筒仍应使用原 ID）")
            continue
        if key in seen:
            errors.append(f"话筒「{mics[key]['name']}」(ID {mic_id}) 重复上报，"
                          "同一话筒只能出现一次（其区间合并到 intervals 中）")
            continue
        seen.add(key)
        raw_intervals = item.get("intervals")
        if raw_intervals is None:
            # 兼容 {mic_id, intervals:[{start_min,end_min,freq}]} 之外的写法
            raw_intervals = [item] if ("start_min" in item or "start" in item) else []
        segs = _parse_segments(raw_intervals, errors, mics[key]["name"])
        entries.append((key, mic_id, mics[key]["name"], segs))

    if errors:
        raise VerificationError("上报内容非法，已整体拒绝（未写入任何数据）：\n"
                                + "\n".join(f"· {e}" for e in errors))
    return revision, [(mid, mname, segs) for _, mid, mname, segs in entries]


# ------------------------------------------------------------------ 区间

def _merge_runs(runs):
    """合并排序后的 [start,end] 段（端点相接也合并）。"""
    if not runs:
        return []
    runs = sorted(([s, e] for s, e in runs))
    out = [runs[0]]
    for s, e in runs[1:]:
        if s <= out[-1][1]:
            if e > out[-1][1]:
                out[-1][1] = e
        else:
            out.append([s, e])
    return out


def _merge_keyed(runs):
    """按 key（如频点对）合并 [start,end] 段；不同 key 的段保持独立。"""
    order = []
    buckets = {}
    for key, s, e in runs:
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append((s, e))
    out = []
    for key in order:
        for s, e in _merge_runs(buckets[key]):
            out.append((key, s, e))
    return out


def _subtract(window, cover):
    """从 window=(s,e) 中扣除若干已合并覆盖段，返回剩余段。"""
    s, e = window
    parts = []
    cur = s
    for cs, ce in cover:
        if cs <= cur:
            cur = max(cur, ce)
        elif cs < e:
            parts.append([cur, cs])
            cur = ce
        if cur >= e:
            break
    if cur < e:
        parts.append([cur, e])
    return parts


def _related(ma, mb, interferes):
    """两台冻结话筒是否同房间 / 互扰房间（任一未配房间则否）。"""
    ra, rb = ma["room_id"], mb["room_id"]
    if ra is None or rb is None:
        return False
    return ra == rb or tuple(sorted((ra, rb))) in interferes


# ------------------------------------------------------------------ 报告

def build_report(snapshot, entries):
    """根据冻结快照与已校验的上报生成报告。

    entries: [(mic_id, mic_name, [(start,end,freq), ...]), ...]
    返回 {conflicts, deviations, unknown, counts}。
    """
    mics = snapshot["mics"]
    interferes = {tuple(p) for p in snapshot["interference"]}
    room_names = {r["id"]: r["name"] for r in snapshot["rooms"]}

    # 实际上报（仅保留非空区间话筒）
    reported = []
    for mic_id, mic_name, segs in entries:
        if segs:
            reported.append((str(mic_id), mic_name, segs))
    by_id = {k: mics[k] for k, _, _ in reported}

    # ------------------------------------------------------------ 冲突
    conflict_runs = []
    for ai in range(len(reported)):
        for bi in range(ai + 1, len(reported)):
            ka, _, sa = reported[ai]
            kb, _, sb = reported[bi]
            ma, mb = by_id[ka], by_id[kb]
            if not _related(ma, mb, interferes):
                continue
            need = max(ma["guard"], mb["guard"])
            # 双指针扫描所有区间对的重叠部分
            for as_, ae, af in sa:
                for bs, be, bf in sb:
                    s, e = max(as_, bs), min(ae, be)
                    if s < e and abs(af - bf) < need:
                        conflict_runs.append(
                            ((ka, kb, af, bf, need), s, e))

    conflicts = []
    for (ka, kb, af, bf, need), s, e in _merge_keyed(conflict_runs):
        ma, mb = by_id[ka], by_id[kb]
        conflicts.append({
            "start_min": s,
            "end_min": e,
            "mic_a_id": int(ka),
            "mic_a": ma["name"],
            "freq_a": af,
            "mic_b_id": int(kb),
            "mic_b": mb["name"],
            "freq_b": bf,
            "guard": need,
            "freq_diff": abs(af - bf),
            "room_a": room_names.get(ma["room_id"]),
            "room_b": room_names.get(mb["room_id"]),
        })
    conflicts.sort(key=lambda c: (c["start_min"], c["end_min"],
                                  c["mic_a"], c["mic_b"]))

    # ------------------------------------------------------------ 偏差
    deviations = []
    for key, name, segs in reported:
        m = mics[key]
        planned = m["assigned_freq"]
        for s, e, f in segs:
            if planned is None:
                reasons = ["unplanned_power"]
            else:
                reasons = []
                if f != planned:
                    reasons.append(
                        "freq_not_locked" if m["locked"] else "freq_not_planned")
                if f not in m["freqs"]:
                    reasons.append("freq_not_allowed")
            for r in reasons:
                deviations.append({
                    "start_min": s, "end_min": e,
                    "mic_id": int(key), "mic": name, "freq": f,
                    "planned_freq": planned,
                    "type": r, "reason": REASON_TEXT[r],
                })
    deviations.sort(key=lambda d: (d["start_min"], d["end_min"],
                                   d["mic"], d["type"]))

    # ------------------------------------------------------------ 未知
    unknown = []
    cover = {k: _merge_runs([(s, e) for s, e, _ in segs])
             for k, _, segs in reported}
    for key, m in mics.items():
        if m["assigned_freq"] is None:
            continue  # 计划未获配设备没有“应当启用”的义务
        gaps = _subtract((m["start_min"], m["end_min"]), cover.get(key, []))
        for s, e in gaps:
            unknown.append({
                "start_min": s, "end_min": e,
                "mic_id": int(key), "mic": m["name"],
                "planned_freq": m["assigned_freq"],
            })
    unknown.sort(key=lambda u: (u["start_min"], u["end_min"], u["mic"]))

    return {
        "conflicts": conflicts,
        "deviations": deviations,
        "unknown": unknown,
        "counts": {
            "reported_mics": len(reported),
            "conflict_segments": len(conflicts),
            "deviation_segments": len(deviations),
            "unknown_segments": len(unknown),
        },
    }
