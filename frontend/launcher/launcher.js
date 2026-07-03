import { WSClient, fmtDuration } from "/shared/ws-client.js";

const $ = (id) => document.getElementById(id);

let sites = [];
let timers = [];
let settings = {};
let presets = [];
let detailTimerId = null;

// The extension injects this host element on every page (including here).
// When it's present it owns scrims + alarms; the launcher stands down.
const hasExtension = () => !!document.getElementById("recipehud-overlay-host");

// ---------------------------------------------------------------- clock

function updateClock() {
  const now = new Date();
  const time = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const date = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
  $("clock").textContent = time;
  $("date").textContent = date;
  $("scrim-clock").textContent = time;
  $("scrim-date").textContent = date;
}
setInterval(updateClock, 1000);
updateClock();

// ---------------------------------------------------------------- sites

async function loadSites() {
  sites = await (await fetch("/api/sites")).json();
  renderSites();
}

function tileButton(site) {
  const btn = document.createElement("button");
  btn.className = "tile";
  btn.style.background = site.color || "var(--accent)";
  btn.innerHTML = `
    <span class="tile-icon">${site.icon || "🍽"}</span>
    <span class="tile-name"></span>
    <span class="tile-mode">${site.open_mode === "clean" ? "✨ clean view" : ""}</span>`;
  btn.querySelector(".tile-name").textContent = site.name;
  btn.onclick = () => openSite(site);
  return btn;
}

function renderSites() {
  const grid = $("tiles");
  grid.replaceChildren(...sites.map(tileButton));
  $("no-sites").hidden = sites.length > 0;
  $("admin-hint").textContent = `${location.origin}/admin`;

  const recent = sites
    .filter((s) => s.last_visited_at)
    .sort((a, b) => b.last_visited_at.localeCompare(a.last_visited_at))
    .slice(0, 2);
  $("recents").hidden = !(recent.length && sites.length > 4);
  $("recent-tiles").replaceChildren(...recent.map(tileButton));
}

function openSite(site) {
  fetch(`/api/sites/${site.id}/visit`, { method: "POST" }).catch(() => {});
  location.href = site.open_mode === "clean"
    ? `/recipe?url=${encodeURIComponent(site.url)}`
    : site.url;
}

// ---------------------------------------------------------------- timers

function renderTimers() {
  const strip = $("timers-strip");
  strip.hidden = timers.length === 0;
  strip.replaceChildren(...timers.map((t) => {
    const chip = document.createElement("div");
    chip.className = `timer-chip ${t.state}`;
    chip.innerHTML = `<span class="t-label"></span><span class="t-time"></span>`;
    chip.querySelector(".t-label").textContent =
      t.state === "paused" ? `⏸ ${t.label}` : t.label;
    chip.querySelector(".t-time").textContent =
      t.state === "ringing" ? "DONE" : fmtDuration(t.remaining_s);
    chip.onclick = () => openDetail(t.id);
    return chip;
  }));
  if (detailTimerId) refreshDetail();
}

function timerById(id) { return timers.find((t) => t.id === id); }

function openDetail(id) {
  detailTimerId = id;
  refreshDetail();
  $("detail-sheet").hidden = false;
}

function closeDetail() {
  detailTimerId = null;
  $("detail-sheet").hidden = true;
}

function refreshDetail() {
  const t = timerById(detailTimerId);
  if (!t) { closeDetail(); return; }
  $("detail-label").textContent = t.label;
  $("detail-remaining").textContent = t.state === "ringing" ? "DONE" : fmtDuration(t.remaining_s);
  $("detail-pause").textContent = t.state === "paused" ? "▶ Resume" : "⏸ Pause";
  $("detail-pause").hidden = t.state === "ringing";
}

async function timerAction(id, action, body) {
  await fetch(`/api/timers/${id}/${action}`, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
}

$("detail-pause").onclick = () => {
  const t = timerById(detailTimerId);
  if (t) timerAction(t.id, t.state === "paused" ? "resume" : "pause");
};
$("detail-extend").onclick = () => detailTimerId && timerAction(detailTimerId, "extend", { seconds: 60 });
$("detail-cancel").onclick = () => { if (detailTimerId) { timerAction(detailTimerId, "cancel"); closeDetail(); } };
$("detail-close").onclick = closeDetail;

// ------------------------------------------------------- new-timer sheet

const LABELS = ["Timer", "Pasta", "Oven", "Eggs", "Sauce", "Rice", "Bread"];
let keypadDigits = "";
let selectedLabel = LABELS[0];

function keypadSeconds() {
  const padded = keypadDigits.padStart(6, "0");
  const h = +padded.slice(0, 2), m = +padded.slice(2, 4), s = +padded.slice(4, 6);
  return h * 3600 + m * 60 + s;
}

function renderKeypadDisplay() {
  const padded = keypadDigits.padStart(6, "0");
  const h = padded.slice(0, 2), m = padded.slice(2, 4), s = padded.slice(4, 6);
  $("keypad-display").textContent = +h > 0 ? `${+h}:${m}:${s}` : `${+m}:${s}`;
}

function buildKeypad() {
  const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "00", "0", "⌫"];
  $("keypad").replaceChildren(...keys.map((key) => {
    const btn = document.createElement("button");
    btn.textContent = key;
    btn.onclick = () => {
      if (key === "⌫") keypadDigits = keypadDigits.slice(0, -1);
      else if (keypadDigits.length < 6) keypadDigits += key === "00" ? "00" : key;
      keypadDigits = keypadDigits.slice(0, 6);
      renderKeypadDisplay();
    };
    return btn;
  }));
}

function buildLabelChips() {
  $("label-chips").replaceChildren(...LABELS.map((label) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.classList.toggle("selected", label === selectedLabel);
    btn.onclick = () => { selectedLabel = label; buildLabelChips(); };
    return btn;
  }));
}

async function loadPresets() {
  presets = await (await fetch("/api/presets")).json();
  $("preset-buttons").replaceChildren(...presets.map((p) => {
    const btn = document.createElement("button");
    btn.textContent = `${p.label} ${fmtDuration(p.seconds)}`;
    btn.onclick = async () => {
      await fetch("/api/timers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: p.label, seconds: p.seconds }),
      });
      closeTimerSheet();
    };
    return btn;
  }));
}

function openTimerSheet() {
  keypadDigits = "";
  renderKeypadDisplay();
  buildLabelChips();
  loadPresets();
  $("timer-sheet").hidden = false;
}

function closeTimerSheet() { $("timer-sheet").hidden = true; }

$("new-timer").onclick = openTimerSheet;
$("timer-cancel").onclick = closeTimerSheet;
$("timer-start").onclick = async () => {
  const seconds = keypadSeconds();
  if (seconds <= 0) return;
  await fetch("/api/timers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label: selectedLabel, seconds }),
  });
  closeTimerSheet();
};
buildKeypad();

// ------------------------------------------------------------ keep-awake

function renderKeepAwake() {
  $("keep-awake").classList.toggle("on", !!settings.keep_awake);
  $("keep-awake").textContent = settings.keep_awake ? "🍳 Keeping awake" : "🍳 Keep awake";
}

$("keep-awake").onclick = async () => {
  const res = await fetch("/api/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keep_awake: !settings.keep_awake }),
  });
  settings = await res.json();
  renderKeepAwake();
};

// ------------------------------------------- fallback scrim + alarm audio

let alarmAudio = null;

function showAlarm(data) {
  if (hasExtension()) return;
  $("alarm-label").textContent = `${data.label} — done!`;
  $("alarm-flash").classList.add("visible");
  $("alarm-flash").dataset.timerId = data.id;
  if (!alarmAudio) { alarmAudio = new Audio("/shared/alarm.wav"); alarmAudio.loop = true; }
  alarmAudio.volume = (data.volume ?? 80) / 100;
  alarmAudio.play().catch(() => {});
}

function hideAlarm() {
  $("alarm-flash").classList.remove("visible");
  if (alarmAudio) alarmAudio.pause();
}

$("alarm-dismiss").onclick = () => {
  const id = $("alarm-flash").dataset.timerId;
  if (id) timerAction(id, "dismiss");
  hideAlarm();
};

function setDisplayState(state) {
  if (hasExtension()) return;
  const scrim = $("scrim");
  scrim.classList.toggle("visible", state !== "active");
  scrim.classList.toggle("mode-off", state === "off");
}

$("scrim").addEventListener("pointerdown", (ev) => {
  ev.stopPropagation();
  fetch("/api/activity", { method: "POST" }).catch(() => {});
});

// ---------------------------------------------------------------- theme

function applyTheme() {
  document.documentElement.dataset.theme = settings.theme || "dark";
}

// ------------------------------------------------------------------- ws

const ws = new WSClient("launcher", (type, data) => {
  if (type === "snapshot") {
    timers = data.timers;
    settings = data.settings;
    renderTimers(); renderKeepAwake(); applyTheme();
    setDisplayState(data.display.state);
    if (!hasExtension()) {
      const ringing = timers.find((t) => t.state === "ringing");
      if (ringing) showAlarm({ id: ringing.id, label: ringing.label, volume: settings.alarm_volume });
    }
  } else if (type === "timer.tick") {
    timers = data; renderTimers();
  } else if (type === "timer.created" || type === "timer.updated") {
    const idx = timers.findIndex((t) => t.id === data.id);
    if (idx >= 0) timers[idx] = data; else timers.push(data);
    renderTimers();
  } else if (type === "timer.cancelled") {
    timers = timers.filter((t) => t.id !== data.id);
    renderTimers();
  } else if (type === "alarm.start") {
    showAlarm(data);
  } else if (type === "alarm.stop") {
    hideAlarm();
  } else if (type === "display.state") {
    setDisplayState(data.state);
  } else if (type === "navigate") {
    location.href = data.url;
  } else if (type === "settings.updated") {
    Object.assign(settings, data);
    renderKeepAwake(); applyTheme();
  }
});

document.addEventListener("pointerdown", () => ws.sendActivity(), { capture: true });

loadSites();
