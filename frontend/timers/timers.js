// Phone companion: see and control the kitchen's timers from another room.
//
// Same wiring as every other page — events arrive over the WebSocket, commands
// go over REST — with one deliberate difference: this page NEVER sends the
// `activity` ping. Activity wakes the kitchen panel, and glancing at a timer
// from the sofa must not light up the kitchen.
import { WSClient, fmtDuration } from "/shared/ws-client.js";

const $ = (id) => document.getElementById(id);

let timers = [];
const ringing = new Map(); // timer id -> label, oldest first (as on the kiosk)

// ---------------------------------------------------------------- commands

async function post(path, body) {
  try {
    const resp = await fetch(path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (resp.ok) { setStatus(""); return true; }
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      if (typeof data.detail === "string") detail = data.detail;
    } catch { /* not json */ }
    setStatus(`Didn't work: ${detail}`, true);
  } catch {
    setStatus("Can't reach the kitchen display — are you on the home Wi-Fi?", true);
  }
  return false;
}

function setStatus(text, isError = false) {
  $("status").textContent = text;
  $("status").className = isError ? "hint err" : "hint";
}

const action = (id, name, body) => post(`/api/timers/${id}/${name}`, body);

// ------------------------------------------------------------------ render

function render() {
  $("empty").hidden = timers.length > 0;
  $("timers").replaceChildren(...timers.map((t) => {
    const row = document.createElement("div");
    row.className = `timer ${t.state}`;
    row.innerHTML = `
      <div class="t-top"><span class="t-label"></span><span class="t-time"></span></div>
      <div class="t-actions"></div>`;
    row.querySelector(".t-label").textContent = t.state === "paused" ? `⏸ ${t.label}` : t.label;
    row.querySelector(".t-time").textContent =
      t.state === "ringing" ? "DONE" : fmtDuration(t.remaining_s);
    const button = (label, onclick, cls = "") => {
      const btn = document.createElement("button");
      btn.textContent = label;
      btn.className = cls;
      btn.onclick = onclick;
      return btn;
    };
    const actions = row.querySelector(".t-actions");
    if (t.state === "ringing") {
      actions.append(
        button("Dismiss", () => action(t.id, "dismiss"), "danger"),
        button("+1 min", () => action(t.id, "extend", { seconds: 60 })));
    } else {
      actions.append(
        button(t.state === "paused" ? "▶ Resume" : "⏸ Pause",
          () => action(t.id, t.state === "paused" ? "resume" : "pause")),
        button("+1 min", () => action(t.id, "extend", { seconds: 60 })),
        button("✕ Cancel", () => action(t.id, "cancel")));
    }
    return row;
  }));
  renderAlarm();
}

// ------------------------------------------------------------------- alarm

let audio = null;
let buzz = null;

function syncRinging(list) {
  const live = new Set(list.filter((t) => t.state === "ringing").map((t) => t.id));
  for (const id of [...ringing.keys()]) if (!live.has(id)) ringing.delete(id);
  for (const t of list) if (t.state === "ringing" && !ringing.has(t.id)) ringing.set(t.id, t.label);
}

function renderAlarm() {
  const first = ringing.entries().next().value;
  $("alarm").hidden = !first;
  if (!first) {
    if (audio) audio.pause();
    clearInterval(buzz);
    buzz = null;
    return;
  }
  const more = ringing.size - 1;
  $("alarm-label").textContent = `${first[1]} — done!${more > 0 ? ` (+${more} more)` : ""}`;
  if (!buzz) {
    // Android only; iOS has no vibration API, hence the optional sound.
    const pulse = () => { try { navigator.vibrate?.([400, 200, 400]); } catch { /* unsupported */ } };
    pulse();
    buzz = setInterval(pulse, 2500);
  }
  if ($("sound").checked && audio && audio.paused) audio.play().catch(() => {});
}

$("alarm-dismiss").onclick = () => {
  const id = ringing.keys().next().value;
  if (id) action(id, "dismiss");
};
$("alarm-extend").onclick = () => {
  const id = ringing.keys().next().value;
  if (id) action(id, "extend", { seconds: 60 });
};

// Browsers only allow sound after a tap, so ticking the box is what unlocks it.
$("sound").onchange = () => {
  if (!$("sound").checked) { if (audio) audio.pause(); return; }
  if (!audio) { audio = new Audio("/shared/alarm.wav"); audio.loop = true; }
  audio.play().then(() => { if (ringing.size === 0) audio.pause(); }).catch(() => {
    $("sound").checked = false;
    setStatus("This browser wouldn't allow sound.", true);
  });
};

// ---------------------------------------------------------------- starting

async function loadPresets() {
  let presets = [];
  try { presets = await (await fetch("/api/presets")).json(); } catch { /* offline */ }
  $("presets").replaceChildren(...presets.map((p) => {
    const btn = document.createElement("button");
    btn.textContent = `${p.label} ${fmtDuration(p.seconds)}`;
    btn.onclick = () => post("/api/timers", { label: p.label, seconds: p.seconds });
    return btn;
  }));
}

$("new-timer").onsubmit = async (ev) => {
  ev.preventDefault();
  const minutes = Number($("new-minutes").value);
  if (!(minutes > 0)) return;
  const label = $("new-label").value.trim() || "Timer";
  if (await post("/api/timers", { label, seconds: Math.round(minutes * 60) })) {
    $("new-minutes").value = "";
  }
};

// ---------------------------------------------------------------------- ws

const ws = new WSClient("phone", (type, data) => {
  if (type === "snapshot") {
    timers = data.timers;
    document.documentElement.dataset.theme = data.settings.theme || "dark";
    ringing.clear();
    syncRinging(timers);
  } else if (type === "timer.tick") {
    timers = data;
    syncRinging(timers);
  } else if (type === "timer.created" || type === "timer.updated") {
    const idx = timers.findIndex((t) => t.id === data.id);
    if (idx >= 0) timers[idx] = data; else timers.push(data);
  } else if (type === "timer.cancelled") {
    timers = timers.filter((t) => t.id !== data.id);
    ringing.delete(data.id);
  } else if (type === "alarm.start") {
    ringing.set(data.id, data.label);
  } else if (type === "alarm.stop") {
    ringing.delete(data.id);
  } else if (type === "settings.updated") {
    if ("theme" in data) document.documentElement.dataset.theme = data.theme;
    return;
  } else {
    return; // display/night/navigate events are the kiosk's business
  }
  render();
});

// The backend beats every 20 s; a socket silent for longer is not really there.
setInterval(() => {
  const live = ws.ws && ws.ws.readyState === WebSocket.OPEN && Date.now() - ws.lastRx < 45000;
  $("conn").textContent = live ? "● live" : "reconnecting…";
  $("conn").className = `conn ${live ? "ok" : "down"}`;
}, 1000);

loadPresets();
render();
