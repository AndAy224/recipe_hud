// Reconnecting WebSocket wrapper shared by launcher, recipe view and admin.
// A half-open TCP socket never fires onclose: readyState stays OPEN and the
// page quietly stops receiving events (stale scrim, frozen timers) with no way
// back. The backend beats every 20s, so silence longer than this means the
// socket is a zombie and has to be torn down by hand.
const STALE_MS = 70000;

export class WSClient {
  constructor(role, onEvent) {
    this.role = role;
    this.onEvent = onEvent;
    this.ws = null;
    this.backoff = 1000;
    this.lastActivitySent = 0;
    this.lastRx = Date.now();
    setInterval(() => this.checkAlive(), 15000);
    this.connect();
  }

  checkAlive() {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    if (Date.now() - this.lastRx < STALE_MS) return;
    this.ws.close(); // fires onclose -> the normal reconnect path
  }

  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.lastRx = Date.now();
    this.ws = new WebSocket(`${proto}://${location.host}/ws?role=${this.role}`);
    this.ws.onopen = () => { this.backoff = 1000; this.lastRx = Date.now(); };
    this.ws.onmessage = (ev) => {
      this.lastRx = Date.now();
      try {
        const msg = JSON.parse(ev.data);
        this.onEvent(msg.type, msg.data);
      } catch { /* ignore malformed frames */ }
    };
    this.ws.onclose = () => {
      setTimeout(() => this.connect(), this.backoff);
      this.backoff = Math.min(this.backoff * 2, 15000);
    };
    this.ws.onerror = () => this.ws.close();
  }

  sendActivity() {
    const now = Date.now();
    if (now - this.lastActivitySent < 10000) return;
    this.lastActivitySent = now;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "activity" }));
    }
  }
}

export function fmtDuration(totalSeconds) {
  const s = Math.max(0, Math.round(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}
