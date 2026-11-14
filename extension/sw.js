// Service worker: owns the single WebSocket to the backend, relays events to
// content scripts (via runtime ports) and drives alarm audio (offscreen doc).
// The 1 Hz timer.tick traffic keeps this worker alive whenever timers exist —
// exactly the times an alarm can fire. A chrome.alarms heartbeat reconnects
// after idle sleeps.

const BACKEND_WS = "ws://localhost:8000/ws?role=overlay";
const BACKEND_HTTP = "http://localhost:8000";

let ws = null;
let lastSnapshot = null;
// Bumped by every live state event. refreshDisplay() uses it to tell whether
// its (async) HTTP answer is still current — see the race note there.
let stateEpoch = 0;
// A half-open socket never fires onclose, so readyState stays OPEN forever and
// the overlay silently stops receiving events. Frames stop arriving though, and
// the backend beats every 20s, so silence past this long means the socket is a
// zombie and must be replaced.
let lastRx = 0;
const STALE_MS = 70000;
const ports = new Set();
const ringing = new Map(); // timer id -> volume

function connect() {
  if (ws && ws.readyState === WebSocket.CONNECTING) return;
  if (ws && ws.readyState === WebSocket.OPEN) {
    if (Date.now() - lastRx < STALE_MS) return;
    // Detach before closing: a zombie's onclose can fire long after its
    // replacement is up, and must not be allowed to touch `ws` by then.
    ws.onmessage = ws.onclose = ws.onerror = null;
    try { ws.close(); } catch { /* already gone */ }
    ws = null;
  }
  lastRx = Date.now();
  // Handlers are bound to this socket, never to the `ws` global. A late event
  // from a superseded socket used to null out the live one and schedule yet
  // another connect() — two sockets, every event relayed twice.
  const sock = new WebSocket(BACKEND_WS);
  ws = sock;
  sock.onmessage = (ev) => {
    if (ws !== sock) return;
    lastRx = Date.now();
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    handleEvent(msg);
    for (const port of ports) {
      try { port.postMessage(msg); } catch { /* port gone */ }
    }
  };
  sock.onclose = () => {
    if (ws !== sock) return; // superseded; the live socket owns reconnects
    ws = null;
    setTimeout(connect, 3000);
  };
  sock.onerror = () => { try { sock.close(); } catch { /* already closed */ } };
}

function handleEvent(msg) {
  const { type, data } = msg;
  if (type === "snapshot" || type === "display.state" || type === "night.state") {
    stateEpoch++;
  }
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
  refreshDisplay(port); // authoritative state overrides a possibly-stale cache
});

// The cached snapshot replayed above can be stale (e.g. frozen "off" from an
// idle reconnect the SW never corrected), which paints the black idle scrim
// over a freshly-opened recipe. Fetch ground truth over HTTP — independent of
// the (possibly zombie) WS — and apply it after the cached replay so it wins.
async function refreshDisplay(port) {
  const epoch = stateEpoch;
  let display;
  try {
    display = await (await fetch(BACKEND_HTTP + "/api/display")).json();
  } catch {
    return; // backend unreachable — keep whatever the cache had
  }
  // This answer describes the state at request time. If a live event landed
  // while it was in flight, that event is newer — applying the response now
  // would re-paint an already-dismissed scrim (and, because the backend only
  // announces state *changes*, nothing would ever clear it again).
  if (epoch !== stateEpoch) return;
  if (lastSnapshot) lastSnapshot.display = display; // heal the cache for next connect
  try {
    port.postMessage({ type: "display.state", data: { state: display.state } });
    port.postMessage({ type: "night.state", data: { night: display.night } });
  } catch { /* port already gone */ }
}

// ------------------------------------------------------------ backend proxy
// Content scripts must not fetch() the backend themselves. Their requests count
// as the *page's*, and Chromium's Local Network Access rules stop a public
// page (any recipe site) from reaching localhost without a per-site permission
// prompt nobody at a kiosk will answer — so every timer button, the alarm's
// Dismiss and the screensaver silently did nothing off the launcher. This
// worker holds the host permission, so the overlay asks it instead. Page
// scripts cannot reach this listener (no externally_connectable), which keeps
// that browser protection in front of the backend's open API.
const PROXY_PATH = /^\/(api|media)\//;

async function proxyRequest({ method = "GET", path, body, as = "json" }) {
  let url;
  try { url = new URL(BACKEND_HTTP + path); } catch { url = null; }
  if (!url || url.origin !== BACKEND_HTTP || !PROXY_PATH.test(url.pathname)) {
    return { ok: false, status: 0, error: "path not allowed" };
  }
  const hasBody = body !== undefined && body !== null;
  const res = await fetch(url, {
    method,
    headers: hasBody ? { "Content-Type": "application/json" } : {},
    body: hasBody ? JSON.stringify(body) : undefined,
  });
  if (as === "blob") {
    if (!res.ok) return { ok: false, status: res.status };
    // Runtime messages are JSON, so binary crosses as base64.
    const bytes = new Uint8Array(await res.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return {
      ok: true,
      status: res.status,
      type: res.headers.get("content-type") || "",
      data: btoa(binary),
    };
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty or non-JSON body */ }
  return { ok: res.ok, status: res.status, data };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== "sw" || msg.cmd !== "api") return undefined;
  proxyRequest(msg).then(
    sendResponse,
    (err) => sendResponse({ ok: false, status: 0, error: String(err) }),
  );
  return true; // answer asynchronously
});

chrome.alarms.create("ws-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "ws-keepalive") connect();
});

connect();
