const DEFAULT_API = "http://127.0.0.1:8010";
const api = document.getElementById("api"), s = document.getElementById("s"), ui = document.getElementById("ui");
chrome.storage.sync.get({ apiBase: DEFAULT_API }, v => { api.value = v.apiBase; ui.href = v.apiBase; });
document.getElementById("save").onclick = async () => {
  const base = (api.value || DEFAULT_API).replace(/\/$/, "");
  chrome.storage.sync.set({ apiBase: base });
  ui.href = base;
  s.textContent = "testing…";
  try { const r = await fetch(base + "/health"); const j = await r.json(); s.textContent = j.ok ? "connected to the graph ✓" : "API up, graph unreachable: " + j.error; }
  catch (e) { s.textContent = "can't reach " + base + " — start uvicorn peoplegraph.app:app --port 8010"; }
};
