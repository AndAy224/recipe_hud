// Recipe HUD overlay content script. Injected into every page in the kiosk;
// renders a floating toolbar (home / timers / clean view), the timer panel,
// alarm flash and idle scrims inside a closed shadow root so site CSS and
// CSP can't interfere.
(() => {
  const BACKEND = "http://localhost:8000";
  const onBackendPage = location.origin === BACKEND;
  const isLauncher = onBackendPage && location.pathname === "/";

  const CSS = `
:host { all: initial; }
* { box-sizing: border-box; font-family: system-ui, sans-serif; }
button { font: inherit; cursor: pointer; border: none; border-radius: 14px; }
button:active { filter: brightness(1.3); }

.toolbar {
  position: fixed; right: 10px; top: 50%; transform: translateY(-50%);
  z-index: 2147483644; display: flex; flex-direction: column; gap: 10px;
}
.toolbar button {
  width: 64px; height: 64px; font-size: 26px;
  background: rgba(20, 22, 26, 0.88); color: #f2f0eb;
  box-shadow: 0 2px 10px rgba(0,0,0,0.45); position: relative;
}
.toolbar .badge {
  position: absolute; top: -6px; left: -10px; min-width: 26px; height: 26px;
  border-radius: 13px; background: #e07a5f; color: #fff; font-size: 13px;
  font-weight: 700; display: flex; align-items: center; justify-content: center;
  padding: 0 6px;
}
.toolbar .badge[hidden] { display: none; }

.panel {
  position: fixed; right: 0; top: 0; bottom: 0; width: min(420px, 92vw);
  z-index: 2147483645; background: #1e2128; color: #f2f0eb;
  padding: 18px; display: flex; flex-direction: column; gap: 12px;
  overflow-y: auto; box-shadow: -6px 0 24px rgba(0,0,0,0.5); font-size: 18px;
}
.panel[hidden] { display: none; }
.panel h3 { margin: 0; display: flex; justify-content: space-between; align-items: center; font-size: 22px; }
.panel h3 button { background: none; color: #9aa0ac; font-size: 26px; width: 48px; height: 48px; }

.t-row {
  display: flex; align-items: center; gap: 8px;
  background: #14161a; border-radius: 14px; padding: 10px 12px;
  border-left: 6px solid #81b29a;
}
.t-row.paused { border-left-color: #9aa0ac; opacity: 0.8; }
.t-row.ringing { border-left-color: #d9534f; }
.t-row .t-label { flex: 1; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.t-row .t-time { font-variant-numeric: tabular-nums; font-size: 20px; }
.t-row button { width: 48px; height: 48px; background: #2a2e37; color: #f2f0eb; font-size: 18px; }

.presets { display: flex; flex-wrap: wrap; gap: 8px; }
.presets button { background: #2a2e37; color: #f2f0eb; padding: 10px 14px; min-height: 52px; }

.kp-display { font-size: 42px; text-align: center; font-variant-numeric: tabular-nums; padding: 4px 0; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chips button { background: #14161a; color: #f2f0eb; padding: 8px 12px; min-height: 44px; }
.chips button.selected { background: #e07a5f; }
.keypad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.keypad button { min-height: 62px; font-size: 24px; background: #14161a; color: #f2f0eb; }
.start-btn { min-height: 60px; font-size: 20px; background: #e07a5f; color: #fff; }

.scrim {
  position: fixed; inset: 0; z-index: 2147483646; background: #000;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
}
.scrim[hidden] { display: none; }
.scrim .sc-time { font-size: 15vw; font-weight: 200; color: #cfd3da; }
.scrim .sc-date { font-size: 3.4vw; color: #6c7280; margin-top: 10px; }
.scrim .sc-weather { font-size: 4.5vw; color: #9aa0ac; margin-top: 26px; }
.scrim.mode-off .sc-time, .scrim.mode-off .sc-date,
.scrim.mode-off .sc-weather { visibility: hidden; }

.night-veil {
  position: fixed; inset: 0; pointer-events: none;
  z-index: 2147483647; background: rgba(70, 30, 5, 0.38);
}
.night-veil[hidden] { display: none; }

.alarm {
  position: fixed; inset: 0; z-index: 2147483647;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 26px;
  animation: rhud-pulse 1s infinite;
}
.alarm[hidden] { display: none; }
.alarm .a-label { font-size: 44px; font-weight: 700; color: #fff; text-align: center; padding: 0 20px; }
.alarm button { min-height: 72px; font-size: 22px; padding: 0 30px; }
.alarm .a-dismiss { background: #fff; color: #14161a; }
.alarm .a-extend { background: rgba(255,255,255,0.25); color: #fff; }
@keyframes rhud-pulse {
  0%, 100% { background: rgba(217, 83, 79, 0.97); }
  50% { background: rgba(130, 26, 26, 0.97); }
}`;

  const host = document.createElement("div");
  host.id = "recipehud-overlay-host";
  const shadow = host.attachShadow({ mode: "closed" });
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(CSS);
  shadow.adoptedStyleSheets = [sheet];

  const root = document.createElement("div");
  root.innerHTML = `
    <div class="toolbar" ${isLauncher ? "hidden" : ""}>
      <button class="tb-home" title="Home">🏠</button>
      <button class="tb-timers" title="Timers">⏱<span class="badge" hidden></span></button>
      <button class="tb-clean" title="Clean view" ${onBackendPage ? "hidden" : ""}>✨</button>
    </div>
    <div class="panel" hidden>
      <h3>Timers <button class="p-close">✕</button></h3>
      <div class="t-list"></div>
      <div class="presets"></div>
      <div class="kp-display">0:00</div>
      <div class="chips"></div>
      <div class="keypad"></div>
      <button class="start-btn">Start timer</button>
    </div>
    <div class="scrim" hidden>
      <div class="sc-time"></div>
      <div class="sc-date"></div>
      <div class="sc-weather"></div>
    </div>
    <div class="alarm" hidden>
      <div class="a-label"></div>
      <button class="a-dismiss">Dismiss</button>
      <button class="a-extend">+1 minute</button>
    </div>
    <div class="night-veil" hidden></div>`;
  shadow.appendChild(root);
  document.documentElement.appendChild(host);

  const $ = (sel) => root.querySelector(sel);

  const fmt = (total) => {
    const s = Math.max(0, Math.round(total));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    const mm = String(m).padStart(2, "0"), ss = String(sec).padStart(2, "0");
    return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
  };

  const api = (path, body) =>
    fetch(BACKEND + path, {
      method: "POST",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).catch(() => {});

  let timers = [];
  let alarmTimerId = null;

  // ------------------------------------------------------------ toolbar

  $(".tb-home").onclick = () => { location.href = BACKEND + "/"; };
  $(".tb-clean").onclick = () => {
    location.href = BACKEND + "/recipe?url=" + encodeURIComponent(location.href);
  };
  $(".tb-timers").onclick = () => {
    const panel = $(".panel");
    if (panel.hidden) openPanel(); else panel.hidden = true;
  };

  function renderBadge() {
    const badge = $(".badge");
    const active = timers.filter((t) => t.state !== "paused");
    if (timers.length === 0) { badge.hidden = true; return; }
    badge.hidden = false;
    const soonest = active
      .filter((t) => t.state === "running")
      .sort((a, b) => a.remaining_s - b.remaining_s)[0];
    badge.textContent = soonest ? fmt(soonest.remaining_s) : String(timers.length);
  }

  // -------------------------------------------------------------- panel

  function openPanel() {
    $(".panel").hidden = false;
    renderTimerList();
    loadPresets();
  }
  $(".p-close").onclick = () => { $(".panel").hidden = true; };

  function renderTimerList() {
    const list = $(".t-list");
    list.replaceChildren(...timers.map((t) => {
      const row = document.createElement("div");
      row.className = `t-row ${t.state}`;
      const pauseGlyph = t.state === "paused" ? "▶" : "⏸";
      row.innerHTML = `
        <span class="t-label"></span>
        <span class="t-time">${t.state === "ringing" ? "DONE" : fmt(t.remaining_s)}</span>
        <button class="r-pause" ${t.state === "ringing" ? "hidden" : ""}>${pauseGlyph}</button>
        <button class="r-extend">+1m</button>
        <button class="r-cancel">✕</button>`;
      row.querySelector(".t-label").textContent = t.label;
      row.querySelector(".r-pause").onclick = () =>
        api(`/api/timers/${t.id}/${t.state === "paused" ? "resume" : "pause"}`);
      row.querySelector(".r-extend").onclick = () =>
        api(`/api/timers/${t.id}/extend`, { seconds: 60 });
      row.querySelector(".r-cancel").onclick = () => api(`/api/timers/${t.id}/cancel`);
      return row;
    }));
  }

  async function loadPresets() {
    let presets = [];
    try {
      presets = await (await fetch(BACKEND + "/api/presets")).json();
    } catch { /* backend unreachable */ }
    $(".presets").replaceChildren(...presets.map((p) => {
      const btn = document.createElement("button");
      btn.textContent = `${p.label} ${fmt(p.seconds)}`;
      btn.onclick = () => api("/api/timers", { label: p.label, seconds: p.seconds });
      return btn;
    }));
  }

  // -------------------------------------------------------------- keypad

  const LABELS = ["Timer", "Pasta", "Oven", "Eggs", "Sauce", "Rice"];
  let digits = "";
  let selectedLabel = LABELS[0];

  function keypadSeconds() {
    const p = digits.padStart(6, "0");
    return +p.slice(0, 2) * 3600 + +p.slice(2, 4) * 60 + +p.slice(4, 6);
  }

  function renderKeypadDisplay() {
    const p = digits.padStart(6, "0");
    const h = +p.slice(0, 2);
    $(".kp-display").textContent =
      h > 0 ? `${h}:${p.slice(2, 4)}:${p.slice(4, 6)}` : `${+p.slice(2, 4)}:${p.slice(4, 6)}`;
  }

  $(".keypad").replaceChildren(...["1","2","3","4","5","6","7","8","9","00","0","⌫"].map((key) => {
    const btn = document.createElement("button");
    btn.textContent = key;
    btn.onclick = () => {
      if (key === "⌫") digits = digits.slice(0, -1);
      else if (digits.length < 6) digits = (digits + key).slice(0, 6);
      renderKeypadDisplay();
    };
    return btn;
  }));

  function renderChips() {
    $(".chips").replaceChildren(...LABELS.map((label) => {
      const btn = document.createElement("button");
      btn.textContent = label;
      btn.className = label === selectedLabel ? "selected" : "";
      btn.onclick = () => { selectedLabel = label; renderChips(); };
      return btn;
    }));
  }
  renderChips();

  $(".start-btn").onclick = () => {
    const seconds = keypadSeconds();
    if (seconds <= 0) return;
    api("/api/timers", { label: selectedLabel, seconds });
    digits = "";
    renderKeypadDisplay();
  };

  // ----------------------------------------------------- alarm + scrims

  function showAlarm(data) {
    alarmTimerId = data.id;
    $(".a-label").textContent = `${data.label} — done!`;
    $(".alarm").hidden = false;
  }
  function hideAlarm() {
    alarmTimerId = null;
    $(".alarm").hidden = true;
  }
  $(".a-dismiss").onclick = () => { if (alarmTimerId) api(`/api/timers/${alarmTimerId}/dismiss`); hideAlarm(); };
  $(".a-extend").onclick = () => { if (alarmTimerId) api(`/api/timers/${alarmTimerId}/extend`, { seconds: 60 }); hideAlarm(); };

  let scrimClockInterval = null;
  let scrimWeatherInterval = null;

  async function loadScrimWeather() {
    let w = null;
    try {
      w = await (await fetch(BACKEND + "/api/weather")).json();
    } catch { /* backend unreachable */ }
    $(".sc-weather").textContent = w && w.configured && !w.error
      ? `${w.emoji} ${Math.round(w.temp)}${w.unit || "°"} · H ${Math.round(w.high)}° / L ${Math.round(w.low)}°`
      : "";
  }

  function setDisplayState(state) {
    const scrim = $(".scrim");
    scrim.hidden = state === "active";
    scrim.classList.toggle("mode-off", state === "off");
    if (!scrim.hidden && !scrimClockInterval) {
      const tick = () => {
        const now = new Date();
        $(".sc-time").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        $(".sc-date").textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
      };
      tick();
      scrimClockInterval = setInterval(tick, 1000);
      loadScrimWeather();
      scrimWeatherInterval = setInterval(loadScrimWeather, 15 * 60 * 1000);
    } else if (scrim.hidden && scrimClockInterval) {
      clearInterval(scrimClockInterval);
      scrimClockInterval = null;
      clearInterval(scrimWeatherInterval);
      scrimWeatherInterval = null;
    }
  }

  // ---------------------------------------------------------- night dim

  let nightNow = false;
  let nightDimEnabled = true;
  function applyNight() {
    $(".night-veil").hidden = !(nightNow && nightDimEnabled);
  }

  // The scrim swallows the wake tap so it can't click something on the page.
  $(".scrim").addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    api("/api/activity");
  });

  // ------------------------------------------------------------- events

  function onEvent(msg) {
    const { type, data } = msg;
    if (type === "snapshot") {
      timers = data.timers;
      setDisplayState(data.display.state);
      nightNow = !!data.display.night;
      nightDimEnabled = !!data.settings.night_dim_enabled;
      applyNight();
      const ring = timers.find((t) => t.state === "ringing");
      if (ring) showAlarm(ring); else hideAlarm();
    } else if (type === "timer.tick") {
      timers = data;
    } else if (type === "timer.created" || type === "timer.updated") {
      const idx = timers.findIndex((t) => t.id === data.id);
      if (idx >= 0) timers[idx] = data; else timers.push(data);
    } else if (type === "timer.cancelled") {
      timers = timers.filter((t) => t.id !== data.id);
    } else if (type === "alarm.start") {
      showAlarm(data);
    } else if (type === "alarm.stop") {
      hideAlarm();
    } else if (type === "display.state") {
      setDisplayState(data.state);
    } else if (type === "night.state") {
      nightNow = !!data.night;
      applyNight();
    } else if (type === "settings.updated") {
      if ("night_dim_enabled" in data) {
        nightDimEnabled = !!data.night_dim_enabled;
        applyNight();
      }
    } else if (type === "navigate") {
      location.href = data.url;
    }
    renderBadge();
    if (!$(".panel").hidden) renderTimerList();
  }

  function connectPort() {
    let port;
    try {
      port = chrome.runtime.connect({ name: "overlay" });
    } catch {
      return; // extension reloaded; page refresh will re-inject
    }
    port.onMessage.addListener(onEvent);
    port.onDisconnect.addListener(() => setTimeout(connectPort, 2000));
  }
  connectPort();

  // ----------------------------------------------------------- activity

  let lastActivity = 0;
  document.addEventListener("pointerdown", () => {
    const now = Date.now();
    if (now - lastActivity > 10000) {
      lastActivity = now;
      api("/api/activity");
    }
  }, { capture: true, passive: true });
})();
