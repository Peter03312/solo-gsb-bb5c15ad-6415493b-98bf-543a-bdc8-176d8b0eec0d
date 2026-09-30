/* 剧场无线话筒频点排演 —— 前端逻辑（原生 JS） */
"use strict";

const state = { rooms: [], interference: [], mics: [], solution: null };
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
  renderRooms();
  renderMics();
  renderSolution();
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

// ------------------------------------------------------------------ 启动

refresh().catch((e) => showAlert(`初始化失败：${e.message}`));
