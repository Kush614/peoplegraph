// PeopleGraph overlay: finds the people on screen in Gmail / Calendar, asks the local API about them,
// renders a card panel, and decorates names with a warmth dot.
(() => {
  const DEFAULT_API = "http://127.0.0.1:8010";
  let API = DEFAULT_API;
  chrome.storage?.sync?.get({ apiBase: DEFAULT_API }, v => { API = v.apiBase || DEFAULT_API; });

  const color = w => w == null ? "#6e7681" : w > 70 ? "#3fb950" : w >= 40 ? "#d29922" : "#f85149";
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const EMAIL_RE = /[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/gi;

  // ---- who is on screen? ---------------------------------------------------------------
  function visibleEmails() {
    const found = new Set();
    const add = e => { e = (e || "").trim().toLowerCase(); if (e.includes("@") && !e.endsWith("google.com") && !e.startsWith("noreply")) found.add(e); };
    const host = location.host;
    if (host === "mail.google.com") {
      // An open conversation: Gmail stamps sender/recipient spans with email="…". Only look inside the main pane
      // so the inbox list (which has hundreds) doesn't flood the panel.
      const main = document.querySelector('div[role="main"]') || document;
      main.querySelectorAll("span[email], [data-hovercard-id*='@']").forEach(el => add(el.getAttribute("email") || el.getAttribute("data-hovercard-id")));
      // Compose window recipients
      document.querySelectorAll('div[role="dialog"] [email], div[role="dialog"] [data-hovercard-id*="@"]').forEach(el => add(el.getAttribute("email") || el.getAttribute("data-hovercard-id")));
    } else if (host === "calendar.google.com") {
      // Event detail bubble / edit page: attendees carry data-hovercard-id or data-email; fall back to text scan.
      const scopes = document.querySelectorAll('[role="dialog"], [data-eventid], [jsname][data-email], main');
      scopes.forEach(sc => {
        sc.querySelectorAll("[data-hovercard-id*='@'], [data-email*='@']").forEach(el => add(el.getAttribute("data-hovercard-id") || el.getAttribute("data-email")));
        (sc.innerText.match(EMAIL_RE) || []).forEach(add);
      });
    } else {
      (document.body.innerText.match(EMAIL_RE) || []).slice(0, 40).forEach(add);
    }
    return [...found].slice(0, 25);
  }

  // ---- panel ---------------------------------------------------------------------------
  let panel, lastKey = "", collapsed = false;
  function ensurePanel() {
    if (panel) return panel;
    panel = document.createElement("div");
    panel.id = "pg-panel";
    panel.innerHTML = `<div class="pg-head"><b>PeopleGraph</b><span id="pg-status"></span><button id="pg-toggle" title="collapse">–</button></div>
      <div class="pg-search"><input id="pg-company" placeholder="Warm path to… (stripe, a16z, notion)"><button id="pg-go">Path</button></div>
      <div id="pg-paths"></div>
      <div id="pg-body" class="pg-empty">Open an email or event to see who you actually know.</div>`;
    document.body.appendChild(panel);
    panel.querySelector("#pg-toggle").onclick = () => { collapsed = !collapsed; panel.querySelector("#pg-body").hidden = collapsed; panel.querySelector("#pg-paths").hidden = collapsed; panel.querySelector("#pg-toggle").textContent = collapsed ? "+" : "–"; };
    panel.querySelector("#pg-go").onclick = warmPath;
    panel.querySelector("#pg-company").addEventListener("keydown", e => { if (e.key === "Enter") warmPath(); e.stopPropagation(); });
    panel.querySelector("#pg-company").addEventListener("keypress", e => e.stopPropagation()); // keep Gmail shortcuts out
    // drag by the header
    const head = panel.querySelector(".pg-head");
    let drag = null;
    head.addEventListener("mousedown", e => { if (e.target.tagName === "BUTTON") return; const r = panel.getBoundingClientRect(); drag = { dx: e.clientX - r.left, dy: e.clientY - r.top }; e.preventDefault(); });
    window.addEventListener("mousemove", e => { if (!drag) return; panel.style.left = (e.clientX - drag.dx) + "px"; panel.style.top = (e.clientY - drag.dy) + "px"; panel.style.right = "auto"; panel.style.bottom = "auto"; });
    window.addEventListener("mouseup", () => drag = null);
    return panel;
  }

  async function warmPath() {
    const q = panel.querySelector("#pg-company").value.trim(), out = panel.querySelector("#pg-paths");
    if (!q) return;
    out.innerHTML = `<div class="pg-card pg-muted">searching the graph…</div>`;
    try {
      const r = await fetch(`${API}/path?company=${encodeURIComponent(q)}`);
      const { paths } = await r.json();
      out.innerHTML = paths.length ? paths.map(p => `<div class="pg-card pg-path"><span class="pg-score" style="color:${color(p.score)}">${p.score}</span>
          ${p.path.map((n, i) => i === 0 ? "You" : i === p.path.length - 1 ? `<b>${esc(n)}</b>` : esc(n)).join(" → ")}
          <div class="pg-muted">${p.hops} hop${p.hops > 1 ? "s" : ""} · ${esc(p.contact)} @ ${esc(p.company)}</div></div>`).join("")
        : `<div class="pg-card pg-muted">No path to "${esc(q)}" in your graph yet.</div>`;
    } catch (e) { out.innerHTML = `<div class="pg-card pg-muted">API offline</div>`; }
  }

  // Connection tree: You ── (introducer / mutuals / colleagues) ── Person, as inline SVG.
  function tree(p) {
    const mids = [];
    p.introducedBy.forEach(n => mids.push({ n, kind: "introduced you" }));
    p.mutual.forEach(m => { if (!mids.find(x => x.n === m.name)) mids.push({ n: m.name, kind: "mutual", w: m.warmth }); });
    p.colleagues.forEach(m => { if (!mids.find(x => x.n === m.name)) mids.push({ n: m.name, kind: "colleague", w: m.warmth }); });
    const rows = mids.slice(0, 4);
    if (!rows.length) return "";
    const W = 300, rowH = 22, H = Math.max(44, rows.length * rowH + 12), midY = H / 2;
    const trunc = s => s.length > 16 ? s.slice(0, 15) + "…" : s;
    const lines = rows.map((m, i) => {
      const y = 10 + i * rowH + rowH / 2, c = color(m.w);
      return `<path d="M30 ${midY} C 80 ${midY}, 80 ${y}, 120 ${y}" stroke="${c}" stroke-opacity=".7" fill="none"/>
              <path d="M120 ${y} C 200 ${y}, 200 ${midY}, 270 ${midY}" stroke="${c}" stroke-opacity=".7" fill="none"/>
              <circle cx="120" cy="${y}" r="3.5" fill="${c}"/><text x="128" y="${y + 3.5}">${esc(trunc(m.n))}</text>
              <text class="lbl" x="128" y="${y - 6}">${m.kind}</text>`;
    }).join("");
    return `<svg class="pg-tree" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${lines}
      <circle cx="30" cy="${midY}" r="6" fill="#fff"/><text x="6" y="${midY + 16}">You</text>
      <circle cx="270" cy="${midY}" r="6" fill="${color(p.warmth)}"/><text x="240" y="${midY + 16}">${esc(trunc((p.name || p.email).split(" ")[0]))}</text></svg>`;
  }

  function card(p) {
    const cold = p.daysSilent > 45 && p.emails >= 5;
    return `<div class="pg-card" data-email="${esc(p.email)}">
      <div class="pg-name"><span class="pg-dot" style="background:${color(p.warmth)};margin-left:0"></span><a href="https://mail.google.com/mail/u/0/#search/${encodeURIComponent("from:" + p.email + " OR to:" + p.email)}" target="_blank" style="color:inherit;text-decoration:none;border-bottom:1px dotted #9aa7b5" title="open their emails">${esc(p.name || p.email)}</a>
        <span class="pg-score" style="color:${color(p.warmth)}">${p.warmth ?? "–"}</span></div>
      <div class="pg-muted">${esc(p.company || p.email)} · ${p.emails} emails · last contact ${esc(p.lastSeen || "never")}${p.daysSilent > 0 ? ` (${p.daysSilent}d ago)` : ""}</div>
      ${p.topics.length ? `<div>${p.topics.map(t => `<span class="pg-tag">${esc(t)}</span>`).join("")}</div>` : ""}
      ${p.iOwe.length ? `<div class="pg-line"><b>you owe:</b> ${p.iOwe.map(esc).join("; ")}</div>` : ""}
      ${p.theyOwe.length ? `<div class="pg-line"><b>they owe:</b> ${p.theyOwe.map(esc).join("; ")}</div>` : ""}
      ${tree(p)}
      ${p.introducedBy.length ? `<div class="pg-line"><b>introduced you:</b> ${p.introducedBy.map(esc).join(", ")}</div>` : ""}
      ${p.introduced.length ? `<div class="pg-line"><b>they introduced you to:</b> ${p.introduced.map(esc).join(", ")}</div>` : ""}
      ${p.actions ? `<div class="pg-line"><b>agent memory:</b> ${p.actions} prior action${p.actions > 1 ? "s" : ""}</div>` : ""}
      ${cold ? `<div class="pg-alert">Going cold — ${p.daysSilent} days silent after ${p.emails} emails.</div>` : ""}
      <button class="pg-btn ${cold ? "" : "ghost"}" data-draft="${esc(p.email)}">Draft re-engagement</button>
    </div>`;
  }

  async function render(emails) {
    const key = emails.join(",");
    if (key === lastKey) return;
    lastKey = key;
    const body = ensurePanel().querySelector("#pg-body"), status = panel.querySelector("#pg-status");
    if (!emails.length) { body.className = "pg-empty"; body.innerHTML = "Open an email or event to see who you actually know."; status.textContent = ""; return; }
    status.textContent = "looking up…";
    try {
      const r = await fetch(`${API}/lookup?emails=${encodeURIComponent(key)}`);
      if (!r.ok) throw new Error(`API ${r.status}`);
      const { people, unknown } = await r.json();
      status.textContent = `${people.length} known · ${unknown.length} new`;
      body.className = "";
      body.innerHTML = (people.sort((a, b) => (b.warmth ?? -1) - (a.warmth ?? -1)).map(card).join("")) +
        (unknown.length ? `<div class="pg-card pg-muted">Not in your graph yet: ${unknown.map(esc).join(", ")}</div>` : "");
      body.querySelectorAll("[data-draft]").forEach(btn => btn.onclick = () => draft(btn));
      decorate(people);
    } catch (e) {
      status.textContent = "offline";
      body.className = "pg-empty";
      body.innerHTML = `Can't reach the PeopleGraph API at ${esc(API)}.<br>Start it: <code>uvicorn peoplegraph.app:app --port 8010</code>`;
    }
  }

  async function draft(btn) {
    const email = btn.dataset.draft, cardEl = btn.closest(".pg-card");
    btn.disabled = true; btn.textContent = "Reading graph memory + drafting…";
    try {
      const r = await fetch(`${API}/draft/${encodeURIComponent(email)}`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || r.statusText);
      const box = document.createElement("div");
      box.innerHTML = `<div class="pg-draft">Subject: ${esc(d.subject)}\n\n${esc(d.body)}</div>
        <div class="pg-muted" style="margin-top:4px"><b>why:</b> ${esc(d.rationale)}</div>
        <div class="pg-muted">grounded on ${d.groundedOn.threads} threads · ${d.groundedOn.commitments} commitments · ${d.usedPriorActions} prior agent actions read · written back to the graph as AgentAction ${esc(d.actionId)}</div>
        <button class="pg-btn" data-compose>Open in Gmail compose</button><button class="pg-btn ghost" data-copy>Copy</button>`;
      cardEl.appendChild(box);
      box.querySelector("[data-copy]").onclick = () => navigator.clipboard.writeText(`Subject: ${d.subject}\n\n${d.body}`);
      box.querySelector("[data-compose]").onclick = () =>
        window.open(`https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(email)}&su=${encodeURIComponent(d.subject)}&body=${encodeURIComponent(d.body)}`, "_blank");
      btn.textContent = "Drafted ✓";
    } catch (e) { btn.disabled = false; btn.textContent = "Draft failed — retry"; console.warn("PeopleGraph draft", e); }
  }

  // ---- inline warmth dots next to names --------------------------------------------------
  function decorate(people) {
    const byEmail = Object.fromEntries(people.map(p => [p.email, p]));
    document.querySelectorAll("span[email], [data-hovercard-id*='@']").forEach(el => {
      const e = (el.getAttribute("email") || el.getAttribute("data-hovercard-id") || "").toLowerCase();
      const p = byEmail[e];
      if (!p || el.querySelector(".pg-dot") || el.dataset.pgDone) return;
      el.dataset.pgDone = "1";
      const dot = document.createElement("span");
      dot.className = "pg-dot"; dot.style.background = color(p.warmth);
      dot.title = `PeopleGraph warmth ${p.warmth ?? "–"} · last contact ${p.lastSeen || "never"}`;
      el.appendChild(dot);
    });
  }

  // ---- observe the SPA ----------------------------------------------------------------
  let timer;
  const schedule = () => { clearTimeout(timer); timer = setTimeout(() => render(visibleEmails()), 400); };
  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener("hashchange", schedule);
  schedule();
})();
