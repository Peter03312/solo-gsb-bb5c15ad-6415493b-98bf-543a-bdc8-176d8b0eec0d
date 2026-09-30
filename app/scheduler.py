"""频点排演求解器。

输入为纯数据（字典列表），与数据库解耦，便于测试。

约束:
  1. 两台话筒位于同一房间或互扰房间，且启用时段重叠时，
     已获配频点之差不得小于 max(双方保护间隔)。
  2. 锁定话筒必须使用其锁定频点。
  3. 可用频点不足时，仍返回使目标最优的最大可行方案。

目标（字典序）:
  1. 最大化获配的高优先级话筒数；
  2. 再最大化获配总数。
"""
import json

from ortools.sat.python import cp_model


class SchedulingError(Exception):
    """录入数据非法或锁定项互冲，无法编排。"""


def _overlap(a, b):
    return a["start_min"] < b["end_min"] and b["start_min"] < a["end_min"]


def validate_mics(mics, interference_pairs):
    """校验录入数据。返回错误列表 [(设备名或None, 说明), ...]。"""
    errors = []
    names = set()
    for m in mics:
        name = (m.get("name") or "").strip()
        if not name:
            errors.append((None, "存在未命名的话筒"))
            name = name or f"ID{m.get('id')}"
        if name in names:
            errors.append((name, "话筒名称重复"))
        names.add(name)

        if not isinstance(m.get("guard"), int) or m["guard"] < 0:
            errors.append((name, "保护间隔必须为不小于 0 的整数(kHz)"))

        st, en = m.get("start_min"), m.get("end_min")
        if not isinstance(st, int) or not isinstance(en, int):
            errors.append((name, "启用时段必须为整数分钟"))
        elif not (0 <= st < 1440 and 0 < en <= 1440) or st >= en:
            errors.append(
                (name, f"启用时段非法：[{m.get('start_min')}, {m.get('end_min')})，"
                       f"要求 0≤开始<结束≤1440（即 [开始,结束) 非空且不跨日）"))

        freqs = m.get("freqs") or []
        if not freqs:
            errors.append((name, "可用频点列表为空"))
        elif any((not isinstance(f, int)) for f in freqs):
            errors.append((name, "可用频点必须全部为整数(kHz)"))
        elif len(set(freqs)) != len(freqs):
            errors.append((name, "可用频点列表存在重复值"))

        if m.get("locked"):
            lf = m.get("locked_freq")
            if not isinstance(lf, int):
                errors.append((name, "已锁定但未指定锁定频点"))
            elif freqs and lf not in freqs:
                errors.append((name, f"锁定频点 {lf} 不在可用频点列表中"))
            # 未指定房间的锁定话筒不参与互扰约束，无需报错
    return errors


def _conflict_pairs(mics, interference_pairs):
    """生成所有“同房间/互扰房间且时段重叠”的话筒对。"""
    interferes = {tuple(sorted(p)) for p in interference_pairs}
    pairs = []
    n = len(mics)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = mics[i], mics[j]
            ra, rb = a.get("room_id"), b.get("room_id")
            if ra is None or rb is None:
                continue
            room_related = ra == rb or tuple(sorted((ra, rb))) in interferes
            if room_related and _overlap(a, b):
                pairs.append((i, j, max(a["guard"], b["guard"])))
    return pairs


def check_locked_conflicts(mics, interference_pairs):
    """检查锁定话筒之间是否互冲。返回冲突描述列表。"""
    conflicts = []
    interferes = {tuple(sorted(p)) for p in interference_pairs}
    n = len(mics)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = mics[i], mics[j]
            if not (a.get("locked") and b.get("locked")):
                continue
            ra, rb = a.get("room_id"), b.get("room_id")
            room_related = (
                ra is not None and rb is not None
                and (ra == rb or tuple(sorted((ra, rb))) in interferes)
            )
            if room_related and _overlap(a, b):
                need = max(a["guard"], b["guard"])
                diff = abs(a["locked_freq"] - b["locked_freq"])
                if diff < need:
                    conflicts.append(
                        f"锁定话筒「{a['name']}」({a['locked_freq']} kHz) 与"
                        f"「{b['name']}」({b['locked_freq']} kHz) 互扰且时段重叠，"
                        f"频点差 {diff} kHz < 保护间隔 {need} kHz"
                    )
    return conflicts


def solve(mics, interference_pairs):
    """执行编排。

    返回 dict:
      assignments: [{id, name, freq, locked, priority, room_id,
                     start_min, end_min, guard}]
      unassigned: [同上结构，附 reason]
      high_assigned, assigned, high_total, total, objective, wall_time_s
    数据非法 / 锁定互冲时抛出 SchedulingError。
    """
    errors = validate_mics(mics, interference_pairs)
    if errors:
        msgs = [f"设备「{who}」：{msg}" if who else msg for who, msg in errors]
        raise SchedulingError("录入数据非法，已拒绝编排：\n" + "\n".join(msgs))

    locked_conflicts = check_locked_conflicts(mics, interference_pairs)
    if locked_conflicts:
        raise SchedulingError(
            "锁定频点互冲，已拒绝编排：\n" + "\n".join(locked_conflicts))

    n = len(mics)
    pairs = _conflict_pairs(mics, interference_pairs)

    model = cp_model.CpModel()
    assigned = [model.new_bool_var(f"assigned_{i}") for i in range(n)]
    freq_vars = []
    for i, m in enumerate(mics):
        if m["locked"]:
            f = model.new_constant(m["locked_freq"])
            model.add(assigned[i] == 1)
        else:
            f = model.new_int_var_from_domain(
                cp_model.Domain.from_values(sorted(set(m["freqs"]))),
                f"freq_{i}")
        freq_vars.append(f)

    # 互扰对：只要双方都获配，频点差必须 >= 保护间隔（双向或的蕴含）
    for i, j, need in pairs:
        if mics[i]["locked"] and mics[j]["locked"]:
            continue  # 已在预检中处理
        # assigned_i => (f_i - f_j >= need) or (f_j - f_i >= need)
        pos = model.new_bool_var(f"pos_{i}_{j}")
        neg = model.new_bool_var(f"neg_{i}_{j}")
        model.add(freq_vars[i] - freq_vars[j] >= need).only_enforce_if(
            assigned[i], assigned[j], pos)
        model.add(freq_vars[j] - freq_vars[i] >= need).only_enforce_if(
            assigned[i], assigned[j], neg)
        model.add_bool_or(pos, neg).only_enforce_if(assigned[i], assigned[j])

    high = [i for i, m in enumerate(mics) if m["priority"] == "high"]
    high_count = model.new_int_var(0, len(high), "high_count")
    total_count = model.new_int_var(0, n, "total_count")
    model.add(high_count == sum(assigned[i] for i in high))
    model.add(total_count == sum(assigned))

    # 字典序目标：高优先级权重必须严格大于普通项的最大可能贡献
    weight = n + 1
    model.maximize(weight * high_count + total_count)

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 8
    solver.parameters.max_time_in_seconds = 30
    solver.parameters.random_seed = 20260930
    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SchedulingError(
            "求解器未能找到可行方案（可能锁定项与其他设备存在不可调和的冲突）")

    result_assigned, result_unassigned = [], []
    assign_vals = [solver.value(v) for v in assigned]
    for i, m in enumerate(mics):
        info = {
            "id": m["id"], "name": m["name"], "freq": solver.value(freq_vars[i]),
            "locked": bool(m["locked"]), "priority": m["priority"],
            "room_id": m.get("room_id"), "start_min": m["start_min"],
            "end_min": m["end_min"], "guard": m["guard"],
        }
        if assign_vals[i]:
            result_assigned.append(info)
        else:
            blockers = _blocker_reasons(
                i, mics, pairs, solver, freq_vars, assign_vals)
            info["reason"] = blockers
            result_unassigned.append(info)

    return {
        "assignments": result_assigned,
        "unassigned": result_unassigned,
        "high_assigned": solver.value(high_count),
        "assigned": solver.value(total_count),
        "high_total": len(high),
        "total": n,
        "objective": int(solver.objective_value),
        "optimal": status == cp_model.OPTIMAL,
        "wall_time_s": round(solver.wall_time, 4),
        "unassigned_json": json.dumps(
            [{"id": x["id"], "name": x["name"], "reason": x["reason"]}
             for x in result_unassigned], ensure_ascii=False),
    }


def _blocker_reasons(i, mics, pairs, solver, freq_vars, assign_vals):
    """解释某台话筒为何在最优方案中无法获配：列出各频点被谁占用。"""
    m = mics[i]
    related = []
    for a, b, need in pairs:
        other = None
        if a == i:
            other = b
        elif b == i:
            other = a
        if other is None:
            continue
        related.append((other, need))

    blocking = []
    for f in sorted(set(m["freqs"])):
        holders = []
        for other, need in related:
            if not assign_vals[other]:
                continue  # 对方未获配，不构成频点占用
            of = solver.value(freq_vars[other])
            if abs(of - f) < need:
                holders.append(f"{mics[other]['name']}({of} kHz)")
        if holders:
            blocking.append(f"频点 {f} kHz 与 {'、'.join(holders)} 冲突")
    if not blocking:
        return "无可用的兼容频点（受互扰关系与保护间隔限制）"
    return ("最优方案中所有可用频点均被占用："
            + "；".join(blocking[:6]) + (" 等" if len(blocking) > 6 else ""))
