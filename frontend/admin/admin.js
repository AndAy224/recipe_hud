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
  "idle_timeout_s", "clock_to_off_s", "night_mode_enabled", "night_off_start",
  "night_off_end", "night_idle_timeout_s", "alarm_volume", "theme",
  "display_output", "touch_device",
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
  }
});

loadSites();
loadPresets();
loadSettings();
