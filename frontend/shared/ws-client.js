// Reconnecting WebSocket wrapper shared by launcher, recipe view and admin.
export class WSClient {
  constructor(role, onEvent) {
    this.role = role;
    this.onEvent = onEvent;
    this.ws = null;
    this.backoff = 1000;
    this.lastActivitySent = 0;
    this.connect();
  }

  connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws?role=${this.role}`);
    this.ws.onopen = () => { this.backoff = 1000; };
    this.ws.onmessage = (ev) => {
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
