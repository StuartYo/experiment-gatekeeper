/* Gatekeeper console. Live mode talks to `gk serve`; replay mode steps through a recorded run. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x, d = 1) => (x == null ? "–" : `${(x * 100).toFixed(d)}%`);
  const spct = (x, d = 1) => (x == null ? "–" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%`);
  const n = (x) => (x == null ? "–" : Number(x).toLocaleString("en-US"));
  const time = (iso) => { if (!iso) return ""; const d = new Date(iso); return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }); };
  const day = (iso) => { if (!iso) return ""; const d = new Date(iso); return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }); };

  const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
  const state = { mode: "live", list: [], sel: null, stage: null, busy: null, msg: null };

  const STATUS = {
    draft: ["neutral", "Draft"], blocked: ["block", "Blocked by preflight"], awaiting_approval: ["wait", "Waiting for a person"],
    approved: ["info", "Approved"], running: ["info", "Running"], invalid: ["block", "Data invalid"],
    decided: ["pass", "Decided"], awaiting_ship_approval: ["wait", "Rollout waiting"], shipped: ["pass", "Rolled out"],
  };
  const st = (s) => { const [c, t] = STATUS[s] || ["neutral", s]; return `<span class="st ${c}">${esc(t)}</span>`; };

  // ------------------------------------------------------------------ data
  async function load() {
    let data;
    try {
      // gk serve only runs on this machine; a hosted copy goes straight to the recorded run
      if (!["127.0.0.1", "localhost", "[::1]"].includes(location.hostname)) throw new Error();
      const r = await fetch("/api/state", { cache: "no-store" });
      if (!r.ok) throw new Error();
      data = await r.json();
    } catch {
      const r = await fetch("replay.json", { cache: "no-store" });
      data = await r.json();
    }
    state.mode = data.mode;
    state.meta = data;
    state.list = data.experiments.map((e) => (data.mode === "replay" ? { frames: e.frames, i: e.start ?? 0, story: e.story } : { live: e }));
    if (!state.sel && state.list.length) state.sel = 0;
    render();
  }
  const rec = (i = state.sel) => { const x = state.list[i]; return x ? (x.frames ? x.frames[x.i] : x.live) : null; };

  // ------------------------------------------------------------------ stages
  function stages(e) {
    const pre = e.preflight || {};
    const approvals = e.approvals || [];
    const v = (e.verdicts || []).at(-1);
    const started = !!e.started_at;
    const startOk = approvals.some((a) => a.kind === "start");
    const shipOk = approvals.some((a) => a.kind === "ship");
    const out = [
      { id: "preflight", k: "01", t: "Preflight", s: pre.clean ? "Clean" : pre.blocking ? `${pre.blocking} blocking` : "Not run", state: pre.clean ? "done" : pre.blocking ? "stop" : "todo" },
      { id: "approval", k: "02", t: "Approval", s: startOk ? "Approved" : e.status === "awaiting_approval" ? "Waiting for a person" : "Not requested", state: startOk ? "done" : e.status === "awaiting_approval" ? "now" : "todo" },
      { id: "running", k: "03", t: "Running", s: started ? "Started" : "Not started", state: started ? "done" : startOk ? "now" : "todo" },
      { id: "verdict", k: "04", t: "Verdict", s: v ? v.headline.replace(/:.*/, "") : "No read yet", state: !v ? "todo" : v.decision === "invalid" ? "stop" : ["ship", "drop"].includes(v.decision) ? "done" : "now" },
      { id: "rollout", k: "05", t: "Rollout", s: e.status === "shipped" ? "Done" : shipOk ? "Approved" : e.status === "awaiting_ship_approval" ? "Waiting for a person" : "Locked", state: e.status === "shipped" ? "done" : e.status === "awaiting_ship_approval" ? "now" : "todo" },
    ];
    return out;
  }
  function currentStage(e) {
    const s = stages(e);
    const stop = s.find((x) => x.state === "stop");
    if (stop) return stop.id;
    const now = s.find((x) => x.state === "now");
    if (now) return now.id;
    const todo = s.find((x) => x.state === "todo");
    return todo ? (todo.id === "running" ? "running" : todo.id) : "rollout";
  }

  // ------------------------------------------------------------------ render
  function render() {
    const m = state.meta || {};
    $("#env").textContent = m.environment ? `Flagsmith · ${m.environment}` : "Flagsmith";
    const mode = $("#mode");
    mode.hidden = state.mode !== "replay";
    mode.textContent = "Recorded run";
    const b = $("#banner");
    if (state.mode === "replay") {
      b.hidden = false;
      b.innerHTML = `<div class="wrap"><span class="long"><b>Recorded run.</b> Every screen below was captured from a real self-hosted Flagsmith ${esc(m.flagsmith_version || "")} on ${esc(day(m.recorded_at))}. Traffic is simulated by a script and labelled as such. Buttons replay the next recorded step.</span><span class="short"><b>Recorded run:</b> real Flagsmith, simulated traffic.</span></div>`;
    }
    $("#count").textContent = state.list.length;
    $("#exp-list").innerHTML = state.list.map((x, i) => {
      const e = x.frames ? x.frames[x.i] : x.live;
      return `<li><button data-i="${i}" aria-current="${i === state.sel}"><span class="name">${esc(e.name)}</span><span class="sub"><span class="flag">${esc(e.spec.flag)}</span>${st(e.status)}</span></button></li>`;
    }).join("");
    const e = rec();
    if (!e) {
      $("#detail").innerHTML = `<div class="empty">No experiments yet. Ask your agent to draft one, or run <code>gk draft</code>.</div>`;
      $("#timeline").innerHTML = "";
      return;
    }
    if (!state.stage) state.stage = (state.list[state.sel].frames && e.replay_stage) || currentStage(e);
    const sts = stages(e);
    const sp = e.spec;
    $("#detail").innerHTML = `
      <section class="panel">
        <div class="exp-head">
          <div class="exp-title"><h1>${esc(e.name)}</h1>${st(e.status)}</div>
          <p class="hyp">${esc(sp.hypothesis)}</p>
          ${state.list[state.sel].story ? `<p class="story">${esc(state.list[state.sel].story)}</p>` : ""}
          <dl class="meta">
            <div><dt>Flag</dt><dd>${esc(sp.flag)}</dd></div>
            <div><dt>Metric event</dt><dd>${esc(sp.metric.event)}</dd></div>
            <div><dt>Split</dt><dd>${Object.entries(sp.split).map(([k, v]) => `${esc(k)} ${Math.round(v * 100)}%`).join(" · ")}</dd></div>
            <div><dt>Needed per group</dt><dd class="num">${n(e.plan.per_arm)}</dd></div>
            <div><dt>Flagsmith experiment</dt><dd>#${esc(e.flagsmith.experiment_id)}</dd></div>
          </dl>
        </div>
        <ol class="track" role="tablist">
          ${sts.map((s) => `<li data-state="${s.state}"><button role="tab" data-stage="${s.id}" aria-selected="${s.id === state.stage}"><span class="k">${s.k}</span><span class="t">${s.t}</span><span class="s">${esc(s.s)}</span></button></li>`).join("")}
        </ol>
      </section>
      <section class="panel stage" aria-live="polite">${stagePanel(e)}</section>`;
    $("#timeline").innerHTML = (e.timeline || []).slice().reverse().map((t) => {
      const a = t.actor || "";
      const cls = a.startsWith("person") || a === "person" ? "person" : a === "gatekeeper" ? "gate" : "agent";
      const label = cls === "person" ? (a.split(":")[1] || "person") : cls === "gate" ? "gatekeeper" : a;
      return `<li><span class="tm">${esc(time(t.at))}<br>${esc(day(t.at))}</span><div><div class="ac"><span class="actor ${cls}">${esc(label)}</span>${esc(t.action)}</div>${t.detail ? `<div class="dt2">${esc(t.detail)}</div>` : ""}</div></li>`;
    }).join("");
  }

  function btn(label, action, kind = "primary", disabled = false, force = false) {
    if (state.mode === "replay" && !force) return "";
    const busy = state.busy === action;
    return `<button class="btn ${kind}" data-act="${action}" ${disabled || state.busy ? "disabled" : ""}>${busy ? '<span class="spin" aria-hidden="true"></span>' : ""}${esc(busy ? busyText[action] || "Working…" : label)}</button>`;
  }
  const busyText = { preflight: "Scanning code…", request: "Re-checking code and Flagsmith…", start: "Checking approval, starting…", verdict: "Refreshing results in Flagsmith…", "request-ship": "Asking…", ship: "Completing in Flagsmith…" };
  const shortMsg = (t) => t.replace(/ Fix them, then ask again:.*$/s, " Fix them (listed below), then ask again.");
  const msg = () => (state.msg ? `<div class="${state.msg.kind}">${esc(shortMsg(state.msg.text))}</div>` : "");

  function stagePanel(e) {
    const html = ({ preflight: preflightPanel, approval: approvalPanel, running: runningPanel, verdict: verdictPanel })[state.stage]?.(e) ?? rolloutPanel(e);
    if (state.mode !== "replay") return html;
    const x = state.list[state.sel];
    const nx = x.frames[x.i + 1];
    let b = "";
    if (nx) b = btn(nx.replay_action, nx.replay_button === "gate" ? (nx.replay_stage === "rollout" ? "approve-ship" : "approve") : "next", nx.replay_button === "gate" ? "gate" : "primary", false, true);
    else if (x.frames.length > 1) b = btn("Replay from the start", "restart", "btn", false, true);
    const withActions = html.includes('<div class="actions">') ? html.replace('<div class="actions">', `<div class="actions">${b}`) : html.replace('<div class="stage-top"><div>', `<div class="stage-top"><div>`).replace(/(<div class="stage-top">.*?<\/div>)(<\/div>)/s, `$1<div class="actions">${b}</div>$2`);
    return withActions;
  }

  function preflightPanel(e) {
    const p = e.preflight || { findings: [] };
    const head = p.clean
      ? `<h2>Preflight is clean</h2><p>${n(p.files_scanned)} files checked against rules G1–G9. Nothing will quietly break the counts.</p>`
      : `<h2>${p.blocking} problem${p.blocking === 1 ? "" : "s"} would ruin this experiment</h2><p>The experiment cannot be approved until these are fixed. Each one is a way Flagsmith would record wrong or zero data.</p>`;
    let opened = false;
    const rows = (p.findings || []).map((f) => {
      const open = !opened && f.severity === "block" ? (opened = true) : false;
      return `
      <tr>
        <td class="sev"><span class="st ${f.severity === "block" ? "block" : "wait"}">${f.severity === "block" ? "Blocks" : "Warning"}</span></td>
        <td class="rl"><span class="rule">${esc(f.rule)}</span></td>
        <td>
          <details ${open ? "open" : ""}><summary><span class="f-title">${esc(f.title)}</span><span class="f-where">${esc(f.file)}${f.line ? ":" + f.line : ""}</span></summary>
          ${f.snippet ? `<div class="f-snip">${esc(f.snippet)}</div>` : ""}
          <div class="f-more">${f.detail ? `<span>${esc(f.detail)}</span>` : ""}<span><b>Why it matters.</b> ${esc(f.why)}</span><span><b>Fix.</b> ${esc(f.fix)}</span></div></details>
        </td>
      </tr>`;
    }).join("");
    const action = state.mode === "replay" ? nextLabel(e) : null;
    return `
      <div class="stage-top"><div>${head}</div><div class="actions">${action ? btn(action, "next") : btn("Re-run preflight", "preflight", "primary")}</div></div>
      ${msg()}
      ${rows ? `<div class="tbl tbl-scroll"><table><thead><tr><th>Severity</th><th>Rule</th><th>Finding</th></tr></thead><tbody>${rows}</tbody></table></div>` : ""}
      <p class="who">Last scan ${esc(day(p.at))} ${esc(time(p.at))} · code at <span class="mono">${esc(shortPath(e.code_path))}</span></p>`;
  }

  function approvalPanel(e) {
    const sp = e.spec;
    const ap = (e.approvals || []).filter((a) => a.kind === "start").at(-1);
    const req = (e.requests || []).filter((r) => r.kind === "start").at(-1);
    const rule = e.plan.decision_rule;
    const top = ap
      ? `<h2>Approved by ${esc(ap.by)}</h2><p>Approved ${esc(day(ap.at))} ${esc(time(ap.at))} from the ${esc(ap.via)}. The approval is tied to fingerprint <span class="mono">${esc(ap.fingerprint.slice(0, 12))}</span>; if the flag, split or metric changes in Flagsmith, it stops counting.</p>`
      : req ? `<h2>${esc(req.by === "agent" ? "Your agent" : req.by)} asked to start this experiment</h2><p>Check what will run. Agents can draft and ask, but only a person can approve, from this console or <span class="mono">gk approve</span> in a terminal.</p>`
        : `<h2>Not requested yet</h2><p>Approval opens once preflight is clean and someone asks to start.</p>`;
    const can = !ap && req && e.preflight?.clean;
    const act = can ? btn("Approve start", "approve", "gate") : "";
    return `
      <div class="stage-top"><div>${top}</div><div class="actions">${act}</div></div>
      ${msg()}
      <dl class="spec">
        <dt>Hypothesis</dt><dd>${esc(sp.hypothesis)}</dd>
        <dt>Variations</dt><dd><span class="mono">control</span> = ${esc(sp.control_value)}<br>${sp.variants.map((v) => `<span class="mono">${esc(v.key)}</span> = ${esc(v.value)}`).join("<br>")}</dd>
        <dt>Traffic split</dt><dd><div class="split">${Object.entries(sp.split).map(([k, v]) => `<span style="width:${v * 100}%">${esc(k)} ${Math.round(v * 100)}%</span>`).join("")}</div></dd>
        <dt>Success metric</dt><dd><span class="mono">${esc(sp.metric.event)}</span> (visitors who did it at least once)</dd>
        <dt>Decision rule</dt><dd>No winner before <b class="num">${n(rule.min_exposures_per_arm)}</b> visitors per group${rule.min_days ? ` and ${rule.min_days} days` : ""}. Ship only at ≥ ${pct(rule.ship_if_chance_to_win_at_least, 0)} chance to win. Invalid if the split is off (SRM p &lt; ${rule.invalid_if_srm_p_below}).</dd>
        <dt>Why that many</dt><dd>To detect a ${spct(rule.mde_relative, 0)} relative change from a ${pct(e.plan.baseline_rate, 0)} baseline with 80% power.</dd>
      </dl>`;
  }

  function progress(e, v) {
    const need = e.plan.per_arm;
    const arms = v?.arms?.length ? v.arms : Object.keys(e.spec.split).map((k) => ({ variant: k, exposures: 0 }));
    return `<div class="arms">${arms.map((a) => {
      const w = Math.min(100, (a.exposures / need) * 100);
      return `<div class="arm"><span class="nm">${esc(a.variant)}</span><div class="bar ${w >= 100 ? "done" : ""}"><i style="width:${w}%"></i></div><span class="v">${n(a.exposures)} / ${n(need)}</span></div>`;
    }).join("")}</div>`;
  }

  function runningPanel(e) {
    const v = (e.verdicts || []).at(-1);
    const ap = (e.approvals || []).find((a) => a.kind === "start");
    if (!e.started_at) {
      return `<div class="stage-top"><div><h2>${ap ? "Approved, not started" : "Locked until a person approves"}</h2><p>${ap ? "Starting locks the flag in Flagsmith and turns on the split." : "Start is refused without an approval that matches the current Flagsmith setup."}</p></div><div class="actions">${ap ? btn(state.mode === "replay" ? nextLabel(e) || "Start experiment" : "Start experiment", state.mode === "replay" ? "next" : "start") : ""}</div></div>${msg()}`;
    }
    return `
      <div class="stage-top"><div><h2>Running since ${esc(day(e.started_at))} ${esc(time(e.started_at))}</h2><p>Visitors per group against the number fixed before the start. Reading results early is allowed, but every read is counted.</p></div>
      <div class="actions">${btn(state.mode === "replay" ? nextLabel(e) || "Read results" : "Read results", state.mode === "replay" ? "next" : "verdict")}</div></div>
      ${msg()}
      ${progress(e, v)}
      <p class="who">${(e.peeks || []).length} result read${(e.peeks || []).length === 1 ? "" : "s"} so far. Traffic in this demo is simulated.</p>`;
  }

  function verdictPanel(e) {
    const v = (e.verdicts || []).at(-1);
    if (!v) return `<div class="stage-top"><div><h2>No results read yet</h2><p>Start the experiment, then read results.</p></div></div>`;
    const fsBig = v.flagsmith_says ? v.flagsmith_says.replace(/^Flagsmith: /, "") : "Collecting data";
    const fsWin = (v.arms || []).filter((a) => a.chance_to_win != null).sort((a, b) => b.chance_to_win - a.chance_to_win)[0];
    const rows = (v.arms || []).map((a) => `<tr><td class="mono">${esc(a.variant)}</td><td class="r num" data-l="Visitors">${n(a.exposures)}</td><td class="r num">${n(a.conversions)}</td><td class="r num">${pct(a.rate)}</td><td class="r num c5">${a.lift == null ? "–" : spct(a.lift)}</td><td class="r num c6">${a.ci_low == null ? "–" : `${spct(a.ci_low)} to ${spct(a.ci_high)}`}</td><td class="r num">${a.chance_to_win == null ? "–" : pct(a.chance_to_win, 0)}</td></tr>`).join("");
    const canShip = ["ship", "drop"].includes(v.decision) && e.status !== "shipped" && e.status !== "awaiting_ship_approval";
    const act = state.mode === "replay" ? (nextLabel(e) ? btn(nextLabel(e), "next") : "") : canShip ? btn("Ask for rollout approval", "request-ship") : btn("Read results again", "verdict", "btn");
    return `
      <div class="stage-top"><div><h2>${esc(v.headline)}</h2><p>Read ${esc(day(v.at))} ${esc(time(v.at))}${v.as_of ? `, Flagsmith computed at ${esc(time(v.as_of))}` : ""}. Rule fixed at approval; nothing here can be tuned after the fact.</p></div><div class="actions">${act}</div></div>
      ${msg()}
      <div class="compare">
        <div><span class="lbl">Flagsmith results page</span><span class="big">${fsWin ? `${esc(fsWin.variant)} ${pct(fsWin.chance_to_win, 0)} to win` : "Collecting data"}</span><p>${esc(fsBig)}</p></div>
        <div class="gk ${esc(v.decision)}"><span class="lbl">Gatekeeper verdict</span><span class="big ${esc(v.decision)}">${esc({ ship: "Ship", drop: "Keep control", keep_running: "Keep running", invalid: "Data invalid" }[v.decision])}${v.winner && v.decision === "ship" ? " " + esc(v.winner) : ""}</span><p>${esc(gkReason(v))}</p></div>
      </div>
      <div class="tbl tbl-scroll res"><table><thead><tr><th>Group</th><th class="r">Visitors</th><th class="r">Converted</th><th class="r">Rate</th><th class="r c5">Lift</th><th class="r c6">95% interval</th><th class="r">Chance to win</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${progress(e, v)}
      <ul class="checks">${v.checks.map((c) => `<li><span><span class="st ${({ pass: "pass", fail: "block", wait: "wait", warn: "wait" })[c.status]}">${esc({ pass: "Pass", fail: "Fail", wait: "Not yet", warn: "Warning" }[c.status])}</span></span><div><div class="lb">${esc(c.label)}</div><div class="dt">${esc(c.detail)}</div></div></li>`).join("")}</ul>`;
  }

  function gkReason(v) {
    const bad = v.checks.find((c) => c.status === "fail") || v.checks.find((c) => c.status === "wait");
    if (bad && v.decision !== "ship") return bad.detail;
    const need = v.checks.find((c) => c.id === "V3");
    return `Split matches the approved plan, ${need ? need.detail.charAt(0).toLowerCase() + need.detail.slice(1, -1) : "planned sample reached"}, and ${v.checks.find((c) => c.id === "V6")?.detail.replace(/\.$/, "")} clears the 95% bar set before the test.`;
  }

  function rolloutPanel(e) {
    const v = (e.verdicts || []).at(-1);
    const ap = (e.approvals || []).filter((a) => a.kind === "ship").at(-1);
    if (e.status === "shipped") {
      return `<div class="stage-top"><div><h2>Rolled out in Flagsmith</h2><p>The experiment is completed and the flag now serves ${esc(v?.winner || "control")} to everyone. Approved by ${esc(ap?.by || "a person")}.</p></div></div>${msg()}`;
    }
    if (!v || !["ship", "drop"].includes(v.decision)) {
      return `<div class="stage-top"><div><h2>Rollout is locked</h2><p>Only a ship or keep-control verdict can be rolled out. ${v ? `Current verdict: ${esc(v.headline)}` : ""}</p></div></div>${msg()}`;
    }
    const waiting = e.status === "awaiting_ship_approval";
    let act = "";
    if (state.mode === "replay") act = nextLabel(e) ? btn(nextLabel(e), ap ? "next" : "approve-ship", ap ? "primary" : "gate") : "";
    else if (waiting && !ap) act = btn(`Approve rollout of ${v.winner}`, "approve-ship", "gate");
    else if (ap) act = btn(`Roll out ${v.winner}`, "ship");
    return `<div class="stage-top"><div><h2>${waiting ? "Rollout waiting for a person" : ap ? "Rollout approved" : "Ready to ask for rollout"}</h2><p>Completing the experiment makes Flagsmith serve <span class="mono">${esc(v.winner)}</span> to every visitor. ${ap ? `Approved by ${esc(ap.by)}.` : ""}</p></div><div class="actions">${act}</div></div>${msg()}`;
  }

  const shortPath = (p) => (p || "").split(/[\\/]/).slice(-2).join("/");
  function nextLabel(e) {
    const x = state.list[state.sel];
    if (!x.frames || x.i >= x.frames.length - 1) return null;
    return x.frames[x.i + 1].replay_action || "Next step";
  }

  // ------------------------------------------------------------------ actions
  async function act(action) {
    const e = rec();
    state.msg = null;
    if (action === "approve" || action === "approve-ship") {
      const ok = await confirmApprove(e, action === "approve-ship" ? "rollout" : "start");
      if (!ok) return;
      if (state.mode === "replay") return step();
    }
    if (action === "restart") { const x = state.list[state.sel]; x.i = 0; state.stage = x.frames[0].replay_stage || currentStage(x.frames[0]); render(); return; }
    if (state.mode === "replay" || action === "next") return step();
    state.busy = action;
    render();
    try {
      const r = await fetch(`/api/${action}/${encodeURIComponent(e.slug)}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
      });
      const j = await r.json();
      if (j.experiment) state.list[state.sel].live = j.experiment;
      if (j.refused) state.msg = { kind: "refused", text: `Refused: ${j.reason}` };
      else if (j.error) state.msg = { kind: "refused", text: j.error };
      state.stage = currentStage(rec());
    } catch (err) {
      state.msg = { kind: "refused", text: "The console lost contact with gk serve. Is it still running in your terminal?" };
    }
    state.busy = null;
    render();
  }
  function step() {
    const x = state.list[state.sel];
    state.busy = "next";
    render();
    setTimeout(() => {
      x.i = Math.min(x.i + 1, x.frames.length - 1);
      state.busy = null;
      const f = x.frames[x.i];
      state.msg = f.replay_msg ? { kind: f.replay_msg_kind || "note", text: f.replay_msg } : null;
      state.stage = f.replay_stage || currentStage(f);
      render();
    }, 450);
  }
  function confirmApprove(e, what) {
    const dlg = $("#approve-dlg");
    $("#dlg-title").textContent = what === "start" ? "Approve start" : "Approve rollout";
    $("#dlg-body").textContent = what === "start"
      ? `This lets ${e.name} start in Flagsmith with the split, metric and rule shown. Changing any of them later voids this approval.`
      : `This lets Gatekeeper complete the experiment and serve ${(e.verdicts || []).at(-1)?.winner} to everyone.`;
    const input = $("#dlg-name");
    input.value = "";
    input.placeholder = e.slug;
    $("#dlg-err").hidden = true;
    if (state.mode === "live" && !token) {
      state.msg = { kind: "refused", text: "This browser tab has no approval token. Open the link printed by `gk serve` in your terminal." };
      render();
      return Promise.resolve(false);
    }
    dlg.showModal();
    input.focus();
    return new Promise((resolve) => {
      const form = $("#approve-form");
      const onSubmit = (ev) => {
        if (ev.submitter?.value !== "ok") { cleanup(); resolve(false); return; }
        if (input.value.trim() !== e.slug) { ev.preventDefault(); $("#dlg-err").hidden = false; $("#dlg-err").textContent = `Type ${e.slug} exactly.`; return; }
        cleanup(); resolve(true);
      };
      const cleanup = () => form.removeEventListener("submit", onSubmit);
      form.addEventListener("submit", onSubmit);
    });
  }

  document.addEventListener("click", (ev) => {
    const li = ev.target.closest("#exp-list button");
    if (li) { state.sel = +li.dataset.i; state.stage = null; state.msg = null; render(); return; }
    const tab = ev.target.closest(".track button");
    if (tab) { state.stage = tab.dataset.stage; state.msg = null; render(); return; }
    const a = ev.target.closest("[data-act]");
    if (a && !a.disabled) act(a.dataset.act);
  });

  load().catch(() => { $("#detail").innerHTML = `<div class="empty">Could not load experiments. Start the console with <code>gk serve</code>.</div>`; });
})();
