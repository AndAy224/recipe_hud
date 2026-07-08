// Service worker: owns the single WebSocket to the backend, relays events to
// content scripts (via runtime ports) and drives alarm audio (offscreen doc).
// The 1 Hz timer.tick traffic keeps this worker alive whenever timers exist —
// exactly the times an alarm can fire. A chrome.alarms heartbeat reconnects
// after idle sleeps.

const BACKEND_WS = "ws://localhost:8000/ws?role=overlay";

let ws = null;
let lastSnapshot = null;
const ports = new Set();
const ringing = new Map(); // timer id -> volume

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  ws = new WebSocket(BACKEND_WS);
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    handleEvent(msg);
    for (const port of ports) {
      try { port.postMessage(msg); } catch { /* port gone */ }
    }
  };
  ws.onclose = () => { ws = null; setTimeout(connect, 3000); };
  ws.onerror = () => { try { ws.close(); } catch { /* already closed */ } };
}

function handleEvent(msg) {
  const { type, data } = msg;
  if (type === "snapshot") {
    lastSnapshot = data;
    ringing.clear();
    for (const t of data.timers) {
      if (t.state === "ringing") ringing.set(t.id, data.settings.alarm_volume ?? 80);
    }
    syncAudio();
  } else if (type === "alarm.start") {
    ringing.set(data.id, data.volume ?? 80);
    syncAudio();
  } else if (type === "alarm.stop" || type === "timer.cancelled") {
    ringing.delete(data.id);
    syncAudio();
  }
  // Fold live state events into lastSnapshot so a page navigated to *after* a
  // transition is replayed the current state, not a frozen one. Without this,
  // a page loaded while lastSnapshot is stale (e.g. cached "off" from an idle
  // reconnect) paints the wrong scrim until the next live event. Timers self-
  // heal within ~1s via ticks, but display/night/settings never do.
  if (lastSnapshot) {
    if (type === "display.state") {
      lastSnapshot.display.state = data.state;
    } else if (type === "night.state") {
      lastSnapshot.display.night = data.night;
    } else if (type === "settings.updated") {
      Object.assign(lastSnapshot.settings, data);
    } else if (type === "timer.tick") {
      lastSnapshot.timers = data;
    } else if (type === "timer.created" || type === "timer.updated") {
      const idx = lastSnapshot.timers.findIndex((t) => t.id === data.id);
      if (idx >= 0) lastSnapshot.timers[idx] = data;
      else lastSnapshot.timers.push(data);
    } else if (type === "timer.cancelled") {
      lastSnapshot.timers = lastSnapshot.timers.filter((t) => t.id !== data.id);
    }
  }
}

async function syncAudio() {
  if (ringing.size > 0) {
    await ensureOffscreen();
    const volume = Math.max(...ringing.values());
    chrome.runtime.sendMessage({ target: "offscreen", cmd: "play", volume }).catch(() => {});
  } else {
    chrome.runtime.sendMessage({ target: "offscreen", cmd: "stop" }).catch(() => {});
  }
}

async function ensureOffscreen() {
  const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (contexts.length > 0) return;
  try {
    await chrome.offscreen.createDocument({
      url: "offscreen.html",
      reasons: ["AUDIO_PLAYBACK"],
      justification: "Play the cooking timer alarm sound",
    });
  } catch (err) {
    if (!String(err).includes("single offscreen")) throw err;
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "overlay") return;
  ports.add(port);
  port.onDisconnect.addListener(() => ports.delete(port));
  connect();
  if (lastSnapshot) port.postMessage({ type: "snapshot", data: lastSnapshot });
});

chrome.alarms.create("ws-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "ws-keepalive") connect();
});

connect();
