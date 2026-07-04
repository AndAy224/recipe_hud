import { WSClient, fmtDuration } from "/shared/ws-client.js";

const $ = (id) => document.getElementById(id);

async function api(path, method = "GET", body) {
  const resp = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch { /* not json */ }
    alert(`Request failed: ${detail}`);
    throw new Error(detail);
  }
  return resp.json();
}

// ------------------------------------------------------------------ sites

let sites = [];

async function loadSites() {
  sites = await api("/api/sites");
  renderSites();
}

function renderSites() {
  $("sites-list").replaceChildren(...sites.map((site, i) => {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `
      <span style="font-size:1.4rem">${site.icon || "🍽"}</span>
      <span class="grow"><strong></strong><br><span class="sub"></span></span>
      <span class="sub">${site.open_mode === "clean" ? "✨ clean" : "direct"}</span>
      <button class="up" ${i === 0 ? "disabled" : ""}>↑</button>
      <button class="down" ${i === sites.length - 1 ? "disabled" : ""}>↓</button>
      <button class="edit">Edit</button>
      <button class="danger del">Delete</button>`;
    div.querySelector("strong").textContent = site.name;
    div.querySelector(".sub").textContent = site.url;
    div.querySelector(".up").onclick = () => moveSite(i, -1);
    div.querySelector(".down").onclick = () => moveSite(i, 1);
    div.querySelector(".edit").onclick = () => editSite(site);
    div.querySelector(".del").onclick = async () => {
      if (confirm(`Delete "${site.name}"?`)) {
        await api(`/api/sites/${site.id}`, "DELETE");
        loadSites();
      }
    };
    return div;
  }));
}

async function moveSite(index, delta) {
  const order = sites.map((s) => s.id);
  [order[index], order[index + delta]] = [order[index + delta], order[index]];
  await api("/api/sites/reorder", "POST", { ids: order });
  loadSites();
}

async function editSite(site) {
  const name = prompt("Name:", site.name);
  if (name === null) return;
  const url = prompt("URL:", site.url);
  if (url === null) return;
  const icon = prompt("Icon (emoji, empty for default):", site.icon);
  if (icon === null) return;
  const mode = confirm("OK = open in ✨ clean view, Cancel = open original site")
    ? "clean" : "direct";
  await api(`/api/sites/${site.id}`, "PATCH", { name, url, icon, open_mode: mode });
  loadSites();
}

$("site-add").onsubmit = async (ev) => {
  ev.preventDefault();
  const form = new FormData(ev.target);
  await api("/api/sites", "POST", {
    name: form.get("name"),
    url: form.get("url"),
    color: form.get("color"),
    icon: form.get("icon"),
    open_mode: form.get("open_mode"),
  });
  ev.target.reset();
  loadSites();
};

// ------------------------------------------------------------- my recipes

let savedRecipes = [];

async function loadRecipes() {
  savedRecipes = await api("/api/recipe/saved");
  renderRecipes();
}

function renderRecipes() {
  const query = $("recipe-search").value.trim().toLowerCase();
  const shown = query
    ? savedRecipes.filter((r) =>
        [r.title, r.source_host, ...(r.tags || [])].join(" ").toLowerCase().includes(query))
    : savedRecipes;
  $("recipes-list").replaceChildren(...shown.map((r) => {
    const div = document.createElement("div");
    div.className = "item";
    const tags = (r.tags || []).map((t) => `#${t}`).join(" ");
    div.innerHTML = `
      <span class="grow"><strong></strong><br><span class="sub"></span></span>
      <button class="rename">Rename</button>
      <button class="tags">Tags</button>
      <button class="open">Open on kiosk</button>
      <button class="danger del">Remove</button>`;
    div.querySelector("strong").textContent = r.title;
    div.querySelector(".sub").textContent =
      `${r.source_host} · saved ${(r.saved_at || "").slice(0, 10)}${tags ? " · " + tags : ""}`;
    div.querySelector(".rename").onclick = async () => {
      const title = prompt("Recipe name:", r.title);
      if (title === null) return;
      if (!title.trim()) { alert("Name can't be empty."); return; }
      await api("/api/recipe/rename", "POST", { url: r.url, title: title.trim() });
      loadRecipes();
    };
    div.querySelector(".tags").onclick = async () => {
      const input = prompt("Tags (comma-separated):", (r.tags || []).join(", "));
      if (input === null) return;
      await api("/api/recipe/tags", "POST", {
        url: r.url,
        tags: input.split(",").map((t) => t.trim()).filter(Boolean),
      });
      loadRecipes();
    };
    div.querySelector(".open").onclick = () =>
      api("/api/system/navigate", "POST", {
        url: `${location.origin}/recipe?url=${encodeURIComponent(r.url)}`,
      });
    div.querySelector(".del").onclick = async () => {
      if (confirm(`Remove "${r.title}" from My Recipes?`)) {
        await api("/api/recipe/unsave", "POST", { url: r.url });
        loadRecipes();
      }
    };
    return div;
  }));
}

$("recipe-search").oninput = renderRecipes;

$("recipe-add").onsubmit = async (ev) => {
  ev.preventDefault();
  const url = new FormData(ev.target).get("url");
  const btn = $("recipe-add-btn");
  btn.disabled = true;
  btn.textContent = "Fetching…";
  try {
    await api("/api/recipe/save", "POST", { url });
    ev.target.reset();
    loadRecipes();
  } finally {
    btn.disabled = false;
    btn.textContent = "Save recipe";
  }
};

// ---------------------------------------------------------------- presets

async function loadPresets() {
  const presets = await api("/api/presets");
  $("presets-list").replaceChildren(...presets.map((p) => {
    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `
      <span class="grow"><strong></strong> — ${fmtDuration(p.seconds)}</span>
      <button class="danger del">Delete</button>`;
    div.querySelector("strong").textContent = p.label;
    div.querySelector(".del").onclick = async () => {
      await api(`/api/presets/${p.id}`, "DELETE");
      loadPresets();
    };
    return div;
  }));
}

$("preset-add").onsubmit = async (ev) => {
  ev.preventDefault();
  const form = new FormData(ev.target);
  const seconds = (+form.get("minutes") || 0) * 60 + (+form.get("seconds") || 0);
  if (seconds <= 0) return;
  await api("/api/presets", "POST", { label: form.get("label"), seconds });
  ev.target.reset();
  loadPresets();
};

// --------------------------------------------------------------- settings

const SETTINGS_FIELDS = [
  "idle_timeout_s", "clock_to_off_s", "night_mode_enabled", "night_dim_enabled",
  "night_off_start", "night_off_end", "night_idle_timeout_s", "alarm_volume",
  "theme", "weather_location", "display_output", "touch_device",
];

function fillSettings(settings) {
  const form = $("settings-form");
  for (const key of SETTINGS_FIELDS) {
    const input = form.elements[key];
    if (!input) continue;
    if (input.type === "checkbox") input.checked = !!settings[key];
    else input.value = settings[key];
  }
  $("volume-value").textContent = `${settings.alarm_volume}%`;
}

async function loadSettings() {
  fillSettings(await api("/api/settings"));
}

$("settings-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const form = ev.target;
  const updates = {};
  for (const key of SETTINGS_FIELDS) {
    const input = form.elements[key];
    if (!input) continue;
    updates[key] = input.type === "checkbox" ? input.checked : input.value;
  }
  fillSettings(await api("/api/settings", "PATCH", updates));
  $("settings-saved").hidden = false;
  setTimeout(() => { $("settings-saved").hidden = true; }, 2000);
};

$("settings-form").elements.alarm_volume.oninput = (ev) => {
  $("volume-value").textContent = `${ev.target.value}%`;
};

let testAudio = null;
$("test-alarm").onclick = () => {
  if (!testAudio) testAudio = new Audio("/shared/alarm.wav");
  testAudio.volume = (+$("settings-form").elements.alarm_volume.value || 80) / 100;
  testAudio.currentTime = 0;
  testAudio.play();
  setTimeout(() => testAudio.pause(), 3000);
};

$("geo-search").onclick = async () => {
  const q = $("geo-query").value.trim();
  if (q.length < 2) return;
  const results = await api(`/api/weather/geocode?q=${encodeURIComponent(q)}`);
  $("geo-results").replaceChildren(...(results.length ? results : []).map((r) => {
    const div = document.createElement("div");
    div.className = "item";
    const coords = `${r.latitude.toFixed(2)},${r.longitude.toFixed(2)}`;
    div.innerHTML = `<span class="grow"></span><button class="pick">Use</button>`;
    div.querySelector(".grow").textContent =
      [r.name, r.admin1, r.country].filter(Boolean).join(", ");
    div.querySelector(".pick").onclick = async () => {
      fillSettings(await api("/api/settings", "PATCH", { weather_location: coords }));
      $("geo-results").replaceChildren();
    };
    return div;
  }));
  if (!results.length) $("geo-results").textContent = "No matches.";
};

$("password-form").onsubmit = async (ev) => {
  ev.preventDefault();
  await api("/api/settings/password", "POST", {
    password: new FormData(ev.target).get("password"),
  });
  ev.target.reset();
  alert("Password changed. Your browser will ask for it again.");
};

// ------------------------------------------------------------------- live

let timers = [];

function renderLive(display) {
  if (display) {
    const inhibitors = display.inhibitors.length ? ` · kept awake by: ${display.inhibitors.join(", ")}` : "";
    $("live-display").textContent =
      `Display: ${display.power ? "on" : "off"} · state: ${display.state}` +
      ` · backend: ${display.backend}${display.night ? " · night window" : ""}${inhibitors}`;
  }
  $("live-timers").replaceChildren(...timers.map((t) => {
    const div = document.createElement("div");
    div.className = "timer-live";
    div.innerHTML = `
      <span></span>
      <span class="${t.state === "ringing" ? "ringing" : ""}">${t.state === "ringing" ? "RINGING" : fmtDuration(t.remaining_s)}</span>
      <button class="danger">${t.state === "ringing" ? "Dismiss" : "Cancel"}</button>`;
    div.querySelector("span").textContent = `${t.label} (${t.state})`;
    div.querySelector("button").onclick = () =>
      api(`/api/timers/${t.id}/${t.state === "ringing" ? "dismiss" : "cancel"}`, "POST");
    return div;
  }));
}

for (const btn of document.querySelectorAll("[data-display]")) {
  btn.onclick = async () => renderLive(await api(`/api/display/${btn.dataset.display}`, "POST"));
}

$("kiosk-restart").onclick = () => {
  if (confirm("Restart the kiosk browser?")) api("/api/system/kiosk/restart", "POST");
};

$("navigate-go").onclick = async () => {
  const url = $("navigate-url").value;
  if (url) await api("/api/system/navigate", "POST", { url });
};

// ------------------------------------------------------------------ system

function fmtBytes(n) {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

function fmtUptime(s) {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

let lastHealth = null;

async function loadHealth() {
  let h;
  try { h = await api("/api/system/health"); } catch { return; }
  lastHealth = h;
  const throttleFlags = h.throttled
    ? Object.entries(h.throttled.current).filter(([, v]) => v).map(([k]) => k)
    : [];
  const occurredFlags = h.throttled
    ? Object.entries(h.throttled.occurred).filter(([, v]) => v).map(([k]) => k)
    : [];
  const stats = [
    ["Version", `${h.version}${h.git_rev ? " @ " + h.git_rev : ""}`],
    ["Uptime", fmtUptime(h.uptime_s)],
    ["CPU temp", h.cpu_temp_c != null ? `${h.cpu_temp_c.toFixed(1)} °C` : "—",
      h.cpu_temp_c > 80],
    ["Throttling", throttleFlags.length ? throttleFlags.join(", ") : "none",
      throttleFlags.length > 0],
    ["Issues since boot", occurredFlags.length ? occurredFlags.join(", ") : "none",
      occurredFlags.length > 0],
    ["Disk free", `${fmtBytes(h.disk.free)} / ${fmtBytes(h.disk.total)}`,
      h.disk.free < 500 * 1024 * 1024],
    ["Memory free", h.memory ? fmtBytes(h.memory.available) : "—"],
    ["Load", h.load_avg ? h.load_avg.map((x) => x.toFixed(2)).join(" ") : "—"],
    ["Database", fmtBytes(h.db_bytes)],
    ["Saved images", `${h.media_files} (${fmtBytes(h.media_bytes)})`],
    ["Connected screens", String(h.ws_clients)],
  ];
  $("health-grid").replaceChildren(...stats.map(([key, value, warn]) => {
    const div = document.createElement("div");
    div.className = "stat";
    div.innerHTML = `<span class="k"></span><span class="v${warn ? " warn" : ""}"></span>`;
    div.querySelector(".k").textContent = key;
    div.querySelector(".v").textContent = value;
    return div;
  }));
}
setInterval(loadHealth, 10000);
loadHealth();

function sysStatus(text, ok = true) {
  $("system-status").textContent = text;
  $("system-status").className = ok ? "" : "err";
}

$("restore-btn").onclick = () => $("restore-file").click();
$("restore-file").onchange = async () => {
  const file = $("restore-file").files[0];
  if (!file) return;
  if (!confirm(`Restore "${file.name}"? This REPLACES all sites, recipes and settings, then restarts the backend.`)) {
    $("restore-file").value = "";
    return;
  }
  sysStatus("Uploading backup…");
  const form = new FormData();
  form.append("file", file);
  try {
    const resp = await fetch("/api/system/restore", { method: "POST", body: form });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || resp.statusText);
    sysStatus(data.restarting
      ? "Restore staged — backend restarting, page will refresh…"
      : `Restore staged. ${data.note || ""}`);
    if (data.restarting) awaitBackendBack();
  } catch (err) {
    sysStatus(`Restore failed: ${err.message}`, false);
  }
  $("restore-file").value = "";
};

$("backend-restart").onclick = async () => {
  if (!confirm("Restart the backend? Timers survive; screens reconnect in a few seconds.")) return;
  try {
    await api("/api/system/restart-backend", "POST");
    sysStatus("Backend restarting…");
    awaitBackendBack();
  } catch { /* alert already shown by api() */ }
};

$("update-btn").onclick = async () => {
  if (!confirm("Update to the latest version? Brief interruption; running timers survive.")) return;
  try {
    await api("/api/system/update", "POST");
  } catch { return; }
  const prevRev = lastHealth && lastHealth.git_rev;
  sysStatus("Updating…");
  const poll = setInterval(async () => {
    try {
      const { status } = await api("/api/system/update/status");
      if (status && !status.ok) {
        clearInterval(poll);
        sysStatus("Update FAILED — see logs below", false);
        loadLogs();
        return;
      }
      if (status) sysStatus(`Updating… (${status.phase})`);
      if (status && ["restarting", "done"].includes(status.phase)) {
        clearInterval(poll);
        awaitBackendBack(prevRev);
      }
    } catch { /* backend mid-restart; awaitBackendBack takes over */ }
  }, 2000);
};

function awaitBackendBack(prevRev) {
  const poll = setInterval(async () => {
    try {
      const h = await api("/api/system/health");
      if (prevRev === undefined || h.git_rev !== prevRev || h.uptime_s < 60) {
        clearInterval(poll);
        sysStatus(`Back up ✓ (${h.version}${h.git_rev ? " @ " + h.git_rev : ""})`);
        loadHealth();
      }
    } catch { /* still down */ }
  }, 2000);
}

async function loadLogs() {
  try {
    const { records, update_log } = await api("/api/system/logs");
    const lines = records.map((r) =>
      `${new Date(r.ts * 1000).toLocaleTimeString()} ${r.level.padEnd(7)} ${r.logger}: ${r.message}`);
    if (update_log.length) lines.push("", "--- update.log ---", ...update_log);
    $("sys-logs").textContent = lines.join("\n") || "(empty)";
    $("sys-logs").scrollTop = $("sys-logs").scrollHeight;
  } catch { /* auth or network */ }
}
$("logs-refresh").onclick = loadLogs;
document.querySelector("#system-section details").addEventListener("toggle", (ev) => {
  if (ev.target.open) loadLogs();
});

new WSClient("admin", (type, data) => {
  if (type === "snapshot") {
    timers = data.timers;
    renderLive(data.display);
    fillSettings(data.settings);
  } else if (type === "timer.tick") {
    timers = data; renderLive();
  } else if (type === "timer.created" || type === "timer.updated") {
    const idx = timers.findIndex((t) => t.id === data.id);
    if (idx >= 0) timers[idx] = data; else timers.push(data);
    renderLive();
  } else if (type === "timer.cancelled") {
    timers = timers.filter((t) => t.id !== data.id);
    renderLive();
  } else if (type === "display.state") {
    api("/api/display").then(renderLive);
  } else if (type === "recipes.updated") {
    loadRecipes();
  }
});

loadSites();
loadRecipes();
loadPresets();
loadSettings();
