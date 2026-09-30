/* 剧场无线话筒频点排演 —— 前端逻辑（原生 JS） */
"use strict";

const state = { rooms: [], interference: [], mics: [], solution: null,
                verifications: [] };
let selectedVerifId = null;
// 每台核验中话筒的上报区间草稿：{ [verifId]: { [micId]: [[start,end,freq], ...] } }
const verifDrafts = {};
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------ API

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let body = null;
  try { body = await resp.json(); } catch (e) { /* 非 JSON */ }
  if (!resp.ok) {
    const err = new Error((body && body.error) || `请求失败 (${resp.status})`);
    err.status = resp.status;
    throw err;
  }
  return body;
}

// ------------------------------------------------------------------ 提示

function showAlert(message, kind = "error", timeout = 12000) {
  const box = $("alert-box");
  const div = document.createElement("div");
  div.className = `alert alert-${kind}`;
  const close = document.createElement("button");
  close.className = "alert-close";
  close.textContent = "✕";
  close.onclick = () => div.remove();
  div.append(close, document.createTextNode(message));
  box.appendChild(div);
  if (timeout) setTimeout(() => div.remove(), timeout);
}

// ------------------------------------------------------------------ 工具

function toMin(t) {
  if (!t) return NaN;
  const [h, m] = t.split(":").map(Number);
  return h * 60 + m;
}
function toHHMM(min) {
  const h = String(Math.floor(min / 60)).padStart(2, "0");
  const m = String(min % 60).padStart(2, "0");
  return `${h}:${m}`;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
             '"': "&quot;", "'": "&#39;" }[c]));
}
function roomName(id) {
  const r = state.rooms.find((x) => x.id === id);
  return r ? r.name : "（未分配房间）";
}

// ------------------------------------------------------------------ 渲染

function renderRooms() {
  const list = $("room-list");
  list.innerHTML = "";
  if (!state.rooms.length) {
    list.innerHTML = '<li class="hint" style="padding:8px">暂无房间</li>';
  }
  for (const r of state.rooms) {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "item-name";
    span.textContent = r.name;
    const del = document.createElement("button");
    del.className = "btn-link danger";
    del.textContent = "删除";
    del.onclick = async () => {
      if (!confirm(`确定删除房间「${r.name}」？`)) return;
      try { await api(`/api/rooms/${r.id}`, { method: "DELETE" }); await refresh(); }
      catch (e) { showAlert(e.message); }
    };
    li.append(span, del);
    list.appendChild(li);
  }

  // 互扰表单下拉
  for (const sel of [$("interf-a"), $("interf-b"), $("mic-room")]) {
    const prev = sel.id === "mic-room" ? sel.value : null;
    sel.innerHTML = "";
    if (sel.id === "mic-room") {
      const opt = document.createElement("option");
      opt.value = ""; opt.textContent = "（不分配）";
      sel.appendChild(opt);
    }
    for (const r of state.rooms) {
      const o = document.createElement("option");
      o.value = r.id; o.textContent = r.name;
      sel.appendChild(o);
    }
    if (prev !== null && [...sel.options].some((o) => o.value === prev)) {
      sel.value = prev;
    }
  }
  if (state.rooms.length >= 2) {
    if ($("interf-a").value === $("interf-b").value && state.rooms[1]) {
      $("interf-b").value = state.rooms[1].id;
    }
  }

  const il = $("interf-list");
  il.innerHTML = "";
  if (!state.interference.length) {
    il.innerHTML = '<li class="hint" style="padding:8px">暂无互扰关系</li>';
  }
  for (const p of state.interference) {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.className = "item-name";
    span.textContent = `${p.room_a}  ↔  ${p.room_b}`;
    const del = document.createElement("button");
    del.className = "btn-link danger";
    del.textContent = "删除";
    del.onclick = async () => {
      try { await api(`/api/interference/${p.id}`, { method: "DELETE" }); await refresh(); }
      catch (e) { showAlert(e.message); }
    };
    li.append(span, del);
    il.appendChild(li);
  }
}

function renderMics() {
  const tbody = $("mic-table").querySelector("tbody");
  tbody.innerHTML = "";
  if (!state.mics.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="hint">暂无话筒，请在下方表单录入</td></tr>';
    return;
  }
  for (const m of state.mics) {
    const tr = document.createElement("tr");
    const prio = m.priority === "high"
      ? '<span class="tag tag-high">高</span>'
      : '<span class="tag tag-normal">普通</span>';
    const lock = m.locked
      ? `<span class="tag tag-lock">锁定 ${esc(m.locked_freq)}</span>` : "";
    tr.innerHTML = `
      <td>${esc(m.name)}${lock}</td>
      <td>${esc(roomName(m.room_id))}</td>
      <td>${m.guard}</td>
      <td style="white-space:nowrap">${toHHMM(m.start_min)}–${toHHMM(m.end_min)}</td>
      <td>${prio}</td>
      <td>${m.freqs.map((f) => `<span class="freq-chip">${f}</span>`).join("")}</td>
      <td>${m.locked ? "🔒" : ""}</td>
      <td style="white-space:nowrap"></td>`;
    const act = tr.lastElementChild;
    const edit = document.createElement("button");
    edit.className = "btn-link"; edit.textContent = "编辑";
    edit.onclick = () => fillMicForm(m);
    const del = document.createElement("button");
    del.className = "btn-link danger"; del.textContent = "删除";
    del.onclick = async () => {
      if (!confirm(`确定删除话筒「${m.name}」？`)) return;
      try { await api(`/api/mics/${m.id}`, { method: "DELETE" }); await refresh(); }
      catch (e) { showAlert(e.message); }
    };
    act.append(edit, del);
    tbody.appendChild(tr);
  }
}

function renderSolution() {
  const area = $("solution-area");
  const s = state.solution;
  if (!s) {
    area.innerHTML = '<p class="hint">尚未编排。录入房间与话筒后点击右上角「开始编排」。</p>';
    return;
  }
  const expired = s.expired || s.input_hash !== s.current_hash;
  const bannerClass = expired ? "expired" : "ok";
  const flag = expired
    ? '<span class="expired-flag">已过期：输入已被修改，请重新编排</span>' : "";
  const ratio = `高优先级 <span class="sol-stat">${s.high_assigned}/${s.high_total}</span>`
              + `　获配总数 <span class="sol-stat">${s.assigned}/${s.total}</span>`;

  const rowsAssigned = s.assignments.map((a) => `
    <tr>
      <td>${esc(a.mic_name)}</td>
      <td class="assign-freq">${a.freq} kHz</td>
      <td>${a.priority === "high" ? '<span class="tag tag-high">高</span>' : '<span class="tag tag-normal">普通</span>'}</td>
      <td>${a.locked ? "🔒 锁定保留" : ""}</td>
    </tr>`).join("");

  const rowsUn = s.unassigned.length
    ? s.unassigned.map((u) => `
      <tr class="unassign-row">
        <td>${esc(u.name)}</td>
        <td class="reason-text">${esc(u.reason || "可用频点不足，当前最优方案无法容纳")}</td>
      </tr>`).join("")
    : '<tr><td colspan="2" class="hint">全部话筒均已获配 🎉</td></tr>';

  area.innerHTML = `
    <div class="sol-banner ${bannerClass}">
      ${flag}
      <span>${ratio}</span>
      <span class="sol-meta">方案 #${s.id} · 生成于 ${esc(s.created_at)} · 求解耗时 ${s.wall_time_s ?? "-"}s
      · 输入版本 ${esc(s.input_hash)}</span>
    </div>
    <div class="result-cols">
      <div>
        <h3>✅ 获配设备（${s.assignments.length}）</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>话筒</th><th>分配频点</th><th>优先级</th><th></th></tr></thead>
          <tbody>${rowsAssigned}</tbody>
        </table></div>
      </div>
      <div>
        <h3>⚠️ 未获配设备（${s.unassigned.length}）</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>话筒</th><th>原因</th></tr></thead>
          <tbody>${rowsUn}</tbody>
        </table></div>
      </div>
    </div>`;
}

async function refresh() {
  const data = await api("/api/state");
  state.rooms = data.rooms;
  state.interference = data.interference;
  state.mics = data.mics;
  state.solution = data.solution;
  state.verifications = data.verifications || [];
  renderRooms();
  renderMics();
  renderSolution();
  renderVerifications();
}

// ------------------------------------------------------------------ 房间表单

$("room-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("room-name");
  const name = input.value.trim();
  if (!name) return;
  try {
    await api("/api/rooms", { method: "POST", body: JSON.stringify({ name }) });
    input.value = "";
    await refresh();
  } catch (err) { showAlert(err.message); }
});

$("interf-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (state.rooms.length < 2) { showAlert("请先添加至少两个房间"); return; }
  const room_a_id = Number($("interf-a").value);
  const room_b_id = Number($("interf-b").value);
  try {
    await api("/api/interference", {
      method: "POST",
      body: JSON.stringify({ room_a_id, room_b_id }),
    });
    await refresh();
  } catch (err) { showAlert(err.message); }
});

// ------------------------------------------------------------------ 话筒表单

function rebuildLockedFreqOptions() {
  const sel = $("mic-locked-freq");
  const current = sel.value;
  sel.innerHTML = "";
  const parts = $("mic-freqs").value.split(/[,，]/).map((x) => x.trim()).filter(Boolean);
  const freqs = [...new Set(parts.map(Number).filter((x) => Number.isInteger(x)))]
    .sort((a, b) => a - b);
  for (const f of freqs) {
    const o = document.createElement("option");
    o.value = f; o.textContent = `${f} kHz`;
    sel.appendChild(o);
  }
  if (current && [...sel.options].some((o) => o.value === current)) {
    sel.value = current;
  }
}
$("mic-freqs").addEventListener("input", rebuildLockedFreqOptions);

$("mic-locked").addEventListener("change", () => {
  $("locked-freq-wrap").classList.toggle("hidden", !$("mic-locked").checked);
  rebuildLockedFreqOptions();
});

function resetMicForm() {
  $("mic-form").reset();
  $("mic-id").value = "";
  $("mic-submit").textContent = "添加话筒";
  $("mic-cancel").classList.add("hidden");
  $("locked-freq-wrap").classList.add("hidden");
  rebuildLockedFreqOptions();
}

function fillMicForm(m) {
  $("mic-id").value = m.id;
  $("mic-name").value = m.name;
  $("mic-room").value = m.room_id ?? "";
  $("mic-guard").value = m.guard;
  $("mic-start").value = toHHMM(m.start_min);
  $("mic-end").value = toHHMM(m.end_min);
  $("mic-priority").value = m.priority;
  $("mic-freqs").value = m.freqs.join(", ");
  $("mic-locked").checked = !!m.locked;
  rebuildLockedFreqOptions();
  if (m.locked) {
    $("locked-freq-wrap").classList.remove("hidden");
    $("mic-locked-freq").value = m.locked_freq;
  } else {
    $("locked-freq-wrap").classList.add("hidden");
  }
  $("mic-submit").textContent = "保存修改";
  $("mic-cancel").classList.remove("hidden");
  $("mic-form").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

$("mic-cancel").addEventListener("click", resetMicForm);

$("mic-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const locked = $("mic-locked").checked;
  const payload = {
    id: $("mic-id").value || null,
    name: $("mic-name").value.trim(),
    room_id: $("mic-room").value || null,
    guard: Number($("mic-guard").value),
    start_min: toMin($("mic-start").value),
    end_min: toMin($("mic-end").value),
    priority: $("mic-priority").value,
    freqs: $("mic-freqs").value,
    locked,
    locked_freq: locked ? Number($("mic-locked-freq").value) : null,
  };
  try {
    const r = await api("/api/mics", { method: "POST", body: JSON.stringify(payload) });
    showAlert(payload.id ? `话筒「${payload.name}」已更新（旧方案已标记过期）`
                         : `话筒「${payload.name}」已添加（旧方案已标记过期）`,
              "success", 4000);
    resetMicForm();
    await refresh();
  } catch (err) { showAlert(err.message); }
});

// ------------------------------------------------------------------ 顶部动作

$("btn-solve").addEventListener("click", async () => {
  if (!state.mics.length) { showAlert("请先录入至少一台话筒"); return; }
  const btn = $("btn-solve");
  btn.disabled = true; btn.textContent = "编排中…";
  try {
    const r = await api("/api/solve", { method: "POST" });
    await refresh();
    let msg = `编排完成：高优先级 ${r.high_assigned}/${r.high_total}，`
            + `获配总数 ${r.assigned}/${r.total}`;
    if (r.assigned < r.total) {
      msg += `。有 ${r.total - r.assigned} 台设备因可用频点不足未获配，已给出最大可行方案。`;
    }
    showAlert(msg, r.assigned < r.total ? "info" : "success", 6000);
  } catch (err) {
    if (err.status === 409) showAlert(err.message, "error", 30000);
    else showAlert(err.message);
  } finally {
    btn.disabled = false; btn.textContent = "▶ 开始编排";
  }
});

$("btn-demo").addEventListener("click", async () => {
  try {
    await api("/api/demo", { method: "POST" });
    await refresh();
    showAlert("演示数据已载入，可直接点击「开始编排」", "success", 5000);
  } catch (err) { showAlert(err.message); }
});

$("btn-reset").addEventListener("click", async () => {
  if (!confirm("将清空全部房间、互扰关系、话筒与历史方案，确定继续？")) return;
  try {
    await api("/api/reset", { method: "POST" });
    resetMicForm();
    await refresh();
    showAlert("已清空全部数据", "info", 3000);
  } catch (err) { showAlert(err.message); }
});

// ------------------------------------------------------------------ 现场核验

function renderVerifications() {
  const list = $("verif-list");
  list.innerHTML = "";
  if (!state.verifications.length) {
    list.innerHTML = '<p class="hint" style="margin:0">暂无核验记录。编排方案未过期时点击右上角按钮开启。</p>';
  } else {
    for (const v of state.verifications) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "verif-chip" + (v.id === selectedVerifId ? " active" : "");
      chip.textContent = `核验 #${v.id} · 方案 #${v.solution_id} · 修订 ${v.revision}`
        + (v.submitted ? " · 已上报" : " · 待上报");
      chip.onclick = () => { selectedVerifId = v.id; renderVerifications(); };
      list.appendChild(chip);
    }
  }
  renderVerifDetail();
}

function currentVerif() {
  return state.verifications.find((v) => v.id === selectedVerifId) || null;
}

function _range(s, e) { return `${toHHMM(s)}–${toHHMM(e)}`; }

function renderVerifDetail() {
  const box = $("verif-detail");
  box.innerHTML = "";
  const v = currentVerif();
  if (!v) return;
  const snap = v.snapshot || { rooms: [], interference: [], mics: {} };
  const roomNames = Object.fromEntries(snap.rooms.map((r) => [r.id, r.name]));
  const drafts = verifDrafts[v.id] || {};

  // ---- 头部信息 ----
  const head = document.createElement("div");
  head.className = "verif-detail-head";
  const deleted = state.mics.length
    ? Object.values(snap.mics).filter((m) => !state.mics.some((x) => x.id === m.id))
    : [];
  const renamed = state.mics.length
    ? Object.values(snap.mics).filter((m) => {
        const cur = state.mics.find((x) => x.id === m.id);
        return cur && cur.name !== m.name;
      })
    : [];
  let extra = "";
  if (deleted.length) extra += `已删除话筒按冻结身份核验：${deleted.map((m) => m.name).join("、")}。`;
  if (renamed.length) extra += `已改名：${renamed.map((m) => `${m.name}→${state.mics.find((x) => x.id === m.id).name}`).join("、")}。`;
  head.innerHTML = `
    <div>核验 <strong>#${v.id}</strong> · 基于方案 <strong>#${v.solution_id}</strong>
      · 开启于 ${esc(v.created_at)}${v.note ? ` · ${esc(v.note)}` : ""}</div>
    <div class="hint">当前修订号 <strong id="verif-rev">${v.revision}</strong>
      （提交须携带该号码，旧号拒绝且不部分写入）${extra ? "<br>" + esc(extra) : ""}</div>`;
  box.appendChild(head);

  // ---- 冻结计划 + 录入 ----
  const micIds = Object.values(snap.mics).sort((a, b) => a.id - b.id).map((m) => m.id);
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");
  table.innerHTML = `
    <thead><tr>
      <th>话筒(冻结ID)</th><th>房间</th><th>计划启用时段</th><th>计划频点</th>
      <th>保护/可用(冻结)</th><th style="min-width:300px">实际上报区间 [开始,结束) + 频点</th>
    </tr></thead><tbody></tbody>`;
  const tbody = table.querySelector("tbody");
  for (const m of micIds) {
    const tr = document.createElement("tr");
    const frozenName = snap.mics[m].name;
    const cur = state.mics.find((x) => x.id === m);
    const identity = cur && cur.name !== frozenName
      ? `${esc(frozenName)} <span class="tag tag-gone">已改名→${esc(cur.name)}</span>`
      : !cur ? `${esc(frozenName)} <span class="tag tag-gone">已删除</span>`
             : esc(frozenName);
    const plan = snap.mics[m].assigned_freq != null
      ? `<span class="assign-freq">${snap.mics[m].assigned_freq} kHz</span>`
        + (snap.mics[m].locked ? ' <span class="tag tag-lock">锁定</span>' : "")
      : '<span class="tag tag-unassign">计划未获配</span>';
    tr.innerHTML = `
      <td>${identity}<br><span class="hint">ID ${m}</span></td>
      <td>${esc(roomNames[snap.mics[m].room_id] ?? "（未分配房间）")}</td>
      <td style="white-space:nowrap">${_range(snap.mics[m].start_min, snap.mics[m].end_min)}</td>
      <td>${plan}</td>
      <td>保护 ${snap.mics[m].guard} kHz<br>
        <span class="hint">${snap.mics[m].freqs.map((f) => `<span class="freq-chip">${f}</span>`).join("")}</span></td>
      <td><div class="seg-editor" data-mic="${m}"></div></td>`;
    tbody.appendChild(tr);
    renderSegEditor(tr.querySelector(".seg-editor"), v, m,
                    drafts[m] || existingDraft(v, m));
  }
  wrap.appendChild(table);
  box.appendChild(wrap);

  // ---- 操作条 ----
  const bar = document.createElement("div");
  bar.className = "verif-actions";
  bar.innerHTML = `
    <button class="btn btn-primary btn-small" type="button" id="verif-submit">提交完整替代区间集（修订 ${v.revision}）</button>
    <button class="btn btn-small btn-ghost-dark" type="button" id="verif-load">载入已上报区间</button>
    <button class="btn btn-small btn-ghost-dark" type="button" id="verif-clear">清空草稿</button>
    <span class="hint">区间为 0–1440 的整数分钟，可首尾相接，同一话筒不得重叠；分钟输入 1260 = 21:00。</span>`;
  box.appendChild(bar);
  bar.querySelector("#verif-submit").onclick = () => submitVerif(v);
  bar.querySelector("#verif-load").onclick = () => {
    verifDrafts[v.id] = Object.fromEntries(
      Object.values(snap.mics).map((m) => [m, []]));
    for (const e of v.entries) {
      (verifDrafts[v.id][e.mic_id] ||= []).push([e.start_min, e.end_min, e.freq]);
    }
    renderVerifDetail();
  };
  bar.querySelector("#verif-clear").onclick = () => {
    verifDrafts[v.id] = Object.fromEntries(Object.values(snap.mics).map((m) => [m, []]));
    renderVerifDetail();
  };

  // ---- 报告 ----
  box.appendChild(renderReport(v));
}

function existingDraft(v, micId) {
  // 首次打开且尚无草稿时，以已上报区间作为草稿
  if (!verifDrafts[v.id]) {
    verifDrafts[v.id] = {};
  }
  if (verifDrafts[v.id][micId] === undefined) {
    verifDrafts[v.id][micId] = v.entries
      .filter((e) => e.mic_id === micId)
      .map((e) => [e.start_min, e.end_min, e.freq]);
  }
  return verifDrafts[v.id][micId];
}

function renderSegEditor(host, v, micId, segs) {
  host.innerHTML = "";
  segs.forEach((seg, idx) => {
    const row = document.createElement("div");
    row.className = "seg-row";
    row.innerHTML = `
      <input type="number" min="0" max="1440" step="1" value="${seg[0]}" title="开始（分钟）">
      <span>–</span>
      <input type="number" min="0" max="1440" step="1" value="${seg[1]}" title="结束（分钟）">
      <input type="number" min="0" step="1" value="${seg[2]}" title="频点 kHz" class="seg-freq">
      <span class="hint seg-hhmm"></span>
      <button type="button" class="btn-link danger seg-del">删除</button>`;
    const [sI, eI, fI] = row.querySelectorAll("input");
    const hhmm = row.querySelector(".seg-hhmm");
    const sync = () => {
      const s = Number(sI.value), e = Number(eI.value);
      hhmm.textContent = (Number.isInteger(s) && Number.isInteger(e))
        ? _range(s, e) : "";
      verifDrafts[v.id][micId][idx] = [s, e, Number(fI.value)];
    };
    [sI, eI, fI].forEach((i) => i.addEventListener("input", sync));
    sync();
    row.querySelector(".seg-del").onclick = () => {
      verifDrafts[v.id][micId].splice(idx, 1);
      renderVerifDetail();
    };
    host.appendChild(row);
  });
  const add = document.createElement("button");
  add.type = "button";
  add.className = "btn-link";
  add.textContent = "＋ 添加区间";
  add.onclick = () => {
    const m = v.snapshot.mics[micId];
    verifDrafts[v.id][micId].push([m.start_min, m.end_min,
                                   m.assigned_freq ?? m.freqs[0] ?? 0]);
    renderVerifDetail();
  };
  host.appendChild(add);
}

async function submitVerif(v) {
  const report = [];
  for (const micId of Object.keys(verifDrafts[v.id] || {})) {
    const segs = verifDrafts[v.id][micId].filter(
      (s) => s[0] !== "" && s[1] !== "" && s[2] !== "");
    if (segs.length) {
      report.push({ mic_id: Number(micId),
                    intervals: segs.map(([s, e, f]) => [s, e, f]) });
    }
  }
  const btn = $("verif-submit");
  btn.disabled = true;
  try {
    const r = await api(`/api/verifications/${v.id}`, {
      method: "PUT",
      body: JSON.stringify({ revision: v.revision, report }),
    });
    selectedVerifId = v.id;
    delete verifDrafts[v.id]; // 服务端已成为新基线
    await refresh();
    const c = r.counts;
    showAlert(`核验 #${v.id} 第 ${r.revision - 1} 版上报已接收（新修订号 ${r.revision}）：`
      + `冲突 ${c.conflict_segments} 段、频点偏离/计划外开机 ${c.deviation_segments} 段、`
      + `未知 ${c.unknown_segments} 段`,
      (c.conflict_segments + c.deviation_segments) ? "info" : "success", 9000);
  } catch (err) {
    if (err.status === 409) showAlert(err.message, "error", 15000);
    else showAlert(err.message, "error", 20000);
  } finally {
    btn.disabled = false;
  }
}

function renderReport(v) {
  const sec = document.createElement("div");
  sec.className = "report-sec";
  if (!v.report) {
    sec.innerHTML = '<h3>📋 核验报告</h3><p class="hint">尚未接收上报。提交区间后在此生成报告。</p>';
    return sec;
  }
  const r = v.report;
  const c = r.counts;
  sec.innerHTML = `<h3>📋 核验报告（修订 ${v.revision}）
      <span class="hint">冲突 ${c.conflict_segments} · 偏离/计划外 ${c.deviation_segments}
      · 未知 ${c.unknown_segments} · 上报话筒 ${c.reported_mics}</span></h3>`;

  // 冲突
  sec.appendChild(reportTable(`⚡ 实际冲突区间（${r.conflicts.length}）`,
    r.conflicts, ["时间", "话筒A", "话筒B", "频差/保护"],
    (x) => [`<strong>${_range(x.start_min, x.end_min)}</strong>`,
      `${esc(x.mic_a)} <span class="freq-chip">${x.freq_a}</span>`
        + `<br><span class="hint">${esc(x.room_a || "")}</span>`,
      `${esc(x.mic_b)} <span class="freq-chip">${x.freq_b}</span>`
        + `<br><span class="hint">${esc(x.room_b || "")}</span>`,
      `<span class="${x.freq_diff < x.guard ? "conflict-num" : ""}">${x.freq_diff}</span> / ${x.guard} kHz`],
    "暂无实际冲突 🎉"));

  // 偏离 / 计划外开机
  sec.appendChild(reportTable(`⚠️ 频点偏离 / 计划未获配却开机（${r.deviations.length}）`,
    r.deviations, ["时间", "话筒", "实际频点", "问题"],
    (x) => [_range(x.start_min, x.end_min), esc(x.mic),
      `<span class="freq-chip">${x.freq}</span>`
        + (x.planned_freq != null ? ` <span class="hint">计划 ${x.planned_freq}</span>` : ""),
      `<span class="tag tag-dev">${esc(x.reason)}</span>`],
    "无频点偏离"));

  // 未知
  sec.appendChild(reportTable(`❔ 计划启用但未上报（未知，不推定正常或冲突）（${r.unknown.length}）`,
    r.unknown, ["时间", "话筒", "计划频点"],
    (x) => [_range(x.start_min, x.end_min), esc(x.mic),
      `<span class="freq-chip">${x.planned_freq}</span>`],
    "计划启用时段均已上报覆盖"));
  return sec;
}

function reportTable(title, rows, cols, renderRow, emptyText) {
  const div = document.createElement("div");
  div.className = "report-block";
  const h = document.createElement("div");
  h.className = "report-title";
  h.textContent = title;
  div.appendChild(h);
  if (!rows.length) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = emptyText;
    div.appendChild(p);
    return div;
  }
  const t = document.createElement("table");
  t.innerHTML = `<thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>`;
  const tbody = t.querySelector("tbody");
  for (const x of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = renderRow(x).map((c) => `<td>${c}</td>`).join("");
    tbody.appendChild(tr);
  }
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  wrap.appendChild(t);
  div.appendChild(wrap);
  return div;
}

$("btn-verif-start").addEventListener("click", async () => {
  try {
    const r = await api("/api/verifications", {
      method: "POST",
      body: JSON.stringify({ note: "" }),
    });
    selectedVerifId = r.id;
    await refresh();
    showAlert(`已从方案 #${r.solution_id} 开启核验 #${r.id}，方案与输入已冻结`,
              "success", 6000);
  } catch (err) { showAlert(err.message, "error", 12000); }
});

// ------------------------------------------------------------------ 启动

refresh().catch((e) => showAlert(`初始化失败：${e.message}`));
