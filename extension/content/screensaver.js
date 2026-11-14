// Recipe HUD seasonal screensaver — the ONE implementation, used by two hosts:
//   * the extension overlay (content script, isolated world, closed shadow
//     root) — manifest.json lists this file before content/overlay.js;
//   * the launcher's no-extension fallback, which loads this same file from
//     the backend at /ext/screensaver.js.
// Classic script, no modules (content scripts can't import). It only defines
// globalThis.RHScreensaver; each host decides where it mounts.
//
// The idle CLOCK state renders this when the backend reports an active deck
// (GET /api/screensaver); otherwise the host's plain clock shows through.
// Nothing here knows about any particular season: category labels, colours,
// effects, the countdown line and the hint all come from the deck's `ui` block
// (backend/recipehud/decks/*.json), so a new season is a new JSON file.
// The view is placed INSIDE the host's scrim, so the scrim's wake-on-tap and
// show/hide logic cover it unchanged. Nothing on the screensaver itself is
// tappable — the evdev watcher wakes the panel on touch-down, before Chromium
// would ever deliver a click — so saving a card happens in the toast shown
// right after the wake, which is a separate, deliberate tap.
//
// Sizes use --u (a viewport unit, never rem): inside the overlay's shadow root
// rem would follow whatever font size the host site sets.
(() => {
  if (globalThis.RHScreensaver) return;

  const ROTATE_MS = 20000;
  const TOAST_MS = 15000;
  const TOAST_MIN_SHOWN_MS = 3000;
  const WEATHER_MS = 15 * 60 * 1000;
  const MAX_BLOBS = 4;

  const BULB_COLORS = ["#ff5a5a", "#ffd166", "#5fe08a", "#6cb8ff", "#fff0b8"];
  const LEAVES = ["🍂", "🍁", "🍂", "🍃"];
  // deck.ui.palette -> CSS custom properties. With no palette nothing is set
  // and the stylesheet's defaults (the Christmas colours) apply untouched; a
  // deck that brings its own gets matching secondary shades derived from it.
  function paletteVars(palette) {
    const vars = {};
    for (const key of ["gold", "cream", "soft", "accent"]) {
      if (palette[key]) vars[`--${key}`] = palette[key];
    }
    if (palette.gold) {
      vars["--gold-line"] = `color-mix(in srgb, ${palette.gold} 75%, transparent)`;
      vars["--gold-glow"] = `color-mix(in srgb, ${palette.gold} 18%, transparent)`;
    }
    if (palette.soft) {
      vars["--softer"] = `color-mix(in srgb, ${palette.soft} 86%, #000)`;
      vars["--blurb"] = `color-mix(in srgb, ${palette.soft} 80%, #fff)`;
    }
    if (palette.accent) {
      vars["--accent-hi"] = `color-mix(in srgb, ${palette.accent} 55%, #fff)`;
      vars["--accent-lo"] = `color-mix(in srgb, ${palette.accent} 48%, #000)`;
      vars["--accent-glow"] = `color-mix(in srgb, ${palette.accent} 45%, transparent)`;
    }
    if (palette.bg) {
      const [top, mid, bottom] = palette.bg;
      Object.assign(vars, { "--bg-top": top, "--bg-mid": mid, "--bg-bottom": bottom });
      vars["--photo-bg"] = `color-mix(in srgb, ${mid} 75%, #000)`;
      vars["--toast-bg"] = `color-mix(in srgb, color-mix(in srgb, ${mid} 55%, ${bottom}) 97%, transparent)`;
    }
    return vars;
  }
  const THEME_VARS = Object.keys(paletteVars({
    gold: "#000", cream: "#000", soft: "#000", accent: "#000", bg: ["#000", "#000", "#000"],
  }));

  const CSS = `
.rhss, .rhss-toast {
  --u: min(1vw, 0.36vh);
  /* Theme colours. These defaults ARE the Christmas look; a deck with its own
     ui.palette overrides the first two rows and applyTheme() derives the rest. */
  --gold: #d9b44a; --cream: #f6eedb; --soft: #b9c8bb; --accent: #b3263a;
  --bg-top: #1d4d35; --bg-mid: #0f2a1d; --bg-bottom: #050f0a;
  --softer: #9fb3a4; --blurb: #c9d8cc; --accent-hi: #ff8a8a; --accent-lo: #5c0f1b;
  --photo-bg: #0b1f15; --toast-bg: rgba(9, 28, 19, 0.97);
  --gold-line: rgba(217, 180, 74, 0.75); --gold-glow: rgba(217, 180, 74, 0.18);
  --accent-glow: rgba(179, 38, 58, 0.45);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  box-sizing: border-box; -webkit-tap-highlight-color: transparent;
}
.rhss *, .rhss-toast * { box-sizing: border-box; }
.rhss[hidden], .rhss-toast[hidden], .rhss [hidden], .rhss-toast [hidden] { display: none !important; }

.rhss {
  position: absolute; inset: 0; z-index: 1; overflow: hidden; color: var(--cream);
  background: radial-gradient(ellipse 120% 70% at 50% 18%, var(--bg-top) 0%, var(--bg-mid) 48%, var(--bg-bottom) 100%);
  display: flex; flex-direction: column; align-items: center; justify-content: space-evenly;
  padding: calc(var(--u) * 12) calc(var(--u) * 5) calc(var(--u) * 8);
}

/* string of lights along the top */
.rhss-lights { position: absolute; top: 0; left: 0; right: 0; height: calc(var(--u) * 12); pointer-events: none; }
.rhss-lights svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.rhss-bulb {
  position: absolute; width: calc(var(--u) * 1.9); height: calc(var(--u) * 2.9);
  margin-left: calc(var(--u) * -0.95); border-radius: 50% 50% 46% 46%;
  background: radial-gradient(circle at 40% 35%, #fff 0 14%, var(--c) 42%);
  box-shadow: 0 0 calc(var(--u) * 2.2) calc(var(--u) * 0.6) var(--c);
  animation: rhss-twinkle var(--t) ease-in-out var(--d) infinite alternate;
}
.rhss-bulb::before {
  content: ""; position: absolute; left: 25%; right: 25%; top: calc(var(--u) * -0.9);
  height: calc(var(--u) * 1.1); border-radius: 2px; background: #2c3a2f;
}
@keyframes rhss-twinkle { from { opacity: 0.35; } to { opacity: 1; } }

/* snow: transform-only so the Pi's compositor does the work */
.rhss-snow { position: absolute; inset: 0; pointer-events: none; }
.rhss-flake {
  position: absolute; top: -4vh; border-radius: 50%;
  width: var(--s); height: var(--s); opacity: var(--o);
  background: radial-gradient(circle, #fff 0 35%, rgba(255,255,255,0) 70%);
  animation: rhss-fall var(--t) linear var(--d) infinite;
  will-change: transform;
}
@keyframes rhss-fall {
  from { transform: translate3d(0, 0, 0); }
  to { transform: translate3d(var(--x), 108vh, 0); }
}
/* leaves: same transform-only fall, plus a slow tumble */
.rhss-leaf {
  position: absolute; top: -8vh; font-size: var(--s); opacity: var(--o); line-height: 1;
  animation: rhss-tumble var(--t) linear var(--d) infinite;
  will-change: transform;
}
@keyframes rhss-tumble {
  from { transform: translate3d(0, 0, 0) rotate(0deg); }
  to { transform: translate3d(var(--x), 116vh, 0) rotate(var(--r)); }
}

.rhss-top { text-align: center; position: relative; }
.rhss-time { font-size: calc(var(--u) * 21); font-weight: 200; line-height: 1; letter-spacing: 0.02em; }
.rhss-date { font-size: calc(var(--u) * 4.8); color: var(--soft); margin-top: calc(var(--u) * 1.6); }
.rhss-weather { font-size: calc(var(--u) * 4); color: var(--softer); margin-top: calc(var(--u) * 1.4); }
.rhss-weather:empty { display: none; }
.rhss-count {
  font-family: "P052", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
  font-style: italic; font-size: calc(var(--u) * 5.6); color: var(--gold);
  margin-top: calc(var(--u) * 3);
}
.rhss-count:empty { display: none; }

.rhss-stage { display: grid; width: 100%; position: relative; }
.rhss-card {
  grid-area: 1 / 1; display: flex; flex-direction: column; align-items: center; text-align: center;
  opacity: 0; transition: opacity 1.4s ease;
}
.rhss-card.on { opacity: 1; }
.rhss-photo {
  width: calc(var(--u) * 88); aspect-ratio: 4 / 5; border-radius: calc(var(--u) * 4);
  overflow: hidden; position: relative; background: var(--photo-bg);
  border: calc(var(--u) * 0.35) solid var(--gold-line);
  box-shadow: 0 calc(var(--u) * 3) calc(var(--u) * 10) rgba(0, 0, 0, 0.55),
              0 0 calc(var(--u) * 8) var(--gold-glow);
}
/* Ken Burns runs regardless of .on (restarted on refill, while the card is
   still invisible) so a fading-out card doesn't snap back to scale 1. */
.rhss-photo img {
  width: 100%; height: 100%; object-fit: cover; display: block;
  transform-origin: var(--o, 50% 50%); animation: rhss-kb 24s ease-out forwards;
}
@keyframes rhss-kb { from { transform: scale(1.02); } to { transform: scale(1.13); } }
.rhss-ornament {
  width: calc(var(--u) * 66); height: calc(var(--u) * 66); border-radius: 50%; position: relative;
  display: flex; align-items: center; justify-content: center; font-size: calc(var(--u) * 28);
  margin: calc(var(--u) * 14) 0 calc(var(--u) * 10);
  background: radial-gradient(circle at 35% 30%, var(--accent-hi) 0, var(--accent) 38%, var(--accent-lo) 100%);
  box-shadow: inset 0 0 calc(var(--u) * 6) rgba(0,0,0,.45),
              0 0 calc(var(--u) * 10) var(--accent-glow);
}
.rhss-ornament::before {
  content: ""; position: absolute; top: calc(var(--u) * -4); width: calc(var(--u) * 12);
  height: calc(var(--u) * 6); border-radius: calc(var(--u) * 1.2);
  background: linear-gradient(#f3d77c, #a8801f);
}
.rhss-chip:empty { display: none; }
.rhss-chip {
  margin-top: calc(var(--u) * 5); padding: calc(var(--u) * 1.1) calc(var(--u) * 3);
  border-radius: 999px; font-size: calc(var(--u) * 3.6); font-weight: 600; letter-spacing: 0.02em;
  background: var(--chip); color: var(--cream); border: 1px solid rgba(246, 238, 219, 0.25);
}
.rhss-name {
  font-family: "P052", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
  font-size: calc(var(--u) * 10); line-height: 1.05; margin-top: calc(var(--u) * 3);
  text-shadow: 0 2px 12px rgba(0, 0, 0, 0.6); max-width: calc(var(--u) * 90);
}
.rhss-blurb {
  font-size: calc(var(--u) * 4.4); line-height: 1.3; color: var(--blurb);
  margin-top: calc(var(--u) * 2.4); max-width: calc(var(--u) * 82);
}
.rhss-meta { font-size: calc(var(--u) * 3.6); color: var(--gold); margin-top: calc(var(--u) * 2.6); }
.rhss-meta:empty { display: none; }
.rhss-hint { font-size: calc(var(--u) * 3.3); color: rgba(246, 238, 219, 0.5); position: relative; }

/* the after-wake toast: the one place a card can be saved */
.rhss-toast {
  position: fixed; left: 50%; bottom: calc(var(--u) * 5); z-index: 2147483645;
  width: calc(var(--u) * 94); transform: translateX(-50%);
  background: var(--toast-bg); color: var(--cream);
  border: calc(var(--u) * 0.3) solid var(--gold); border-radius: calc(var(--u) * 4);
  box-shadow: 0 calc(var(--u) * 2) calc(var(--u) * 9) rgba(0, 0, 0, 0.6);
  padding: calc(var(--u) * 3); display: flex; flex-direction: column; gap: calc(var(--u) * 2.6);
  animation: rhss-rise 0.35s ease-out;
}
@keyframes rhss-rise { from { transform: translate(-50%, 30%); opacity: 0; } }
.rhss-toast .rt-row { display: flex; gap: calc(var(--u) * 3); align-items: center; }
.rhss-toast .rt-thumb {
  width: calc(var(--u) * 20); height: calc(var(--u) * 20); border-radius: calc(var(--u) * 2.4);
  flex: none; object-fit: cover; background: var(--accent); display: flex; align-items: center;
  justify-content: center; font-size: calc(var(--u) * 10);
}
.rhss-toast .rt-text { flex: 1; min-width: 0; }
.rhss-toast .rt-kicker { font-size: calc(var(--u) * 3.2); color: var(--gold); }
.rhss-toast .rt-name {
  font-family: "P052", "Palatino Linotype", Palatino, Georgia, serif;
  font-size: calc(var(--u) * 6.2); line-height: 1.1; margin-top: calc(var(--u) * 0.6);
}
.rhss-toast .rt-actions { display: flex; gap: calc(var(--u) * 2); }
.rhss-toast button {
  font: inherit; font-size: calc(var(--u) * 4.2); min-height: calc(var(--u) * 11.5);
  padding: 0 calc(var(--u) * 3.4); border: none; border-radius: calc(var(--u) * 2.6);
  background: rgba(246, 238, 219, 0.12); color: var(--cream); cursor: pointer;
}
.rhss-toast button:active { filter: brightness(1.3); }
.rhss-toast .rt-add { flex: 1; background: var(--accent); color: #fff; font-weight: 600; }
.rhss-toast .rt-add:disabled { background: #2f6f45; opacity: 1; }
.rhss-toast .rt-close { width: calc(var(--u) * 11.5); padding: 0; }
`;

  function fmtMinutes(seconds) {
    const m = Math.round(seconds / 60);
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60), r = m % 60;
    return r ? `${h} hr ${r} min` : `${h} hr`;
  }

  // The deck's big day in a given year: "MM-DD", or a rule such as
  // {month: 11, weekday: 4, nth: 4} — the 4th Thursday of November.
  function targetDate(to, year) {
    if (typeof to === "string") {
      const [month, day] = to.split("-").map(Number);
      return new Date(year, month - 1, day);
    }
    const first = new Date(year, to.month - 1, 1);
    const firstWeekday = first.getDay() || 7; // 1 = Monday … 7 = Sunday
    const day = 1 + ((to.weekday - firstWeekday + 7) % 7) + (to.nth - 1) * 7;
    return new Date(year, to.month - 1, day);
  }

  // The line under the clock: an exact-date message if the deck has one for
  // today, else "N days to go" ("today" on the day itself — for a day with no
  // fixed date), else the deck's "after" line once the day has passed. A season can straddle New Year, so the day that counts is whichever
  // occurrence is nearest to today.
  function countdown(cd, now) {
    if (!cd) return "";
    const pad = (n) => String(n).padStart(2, "0");
    const special = cd.days && cd.days[`${pad(now.getMonth() + 1)}-${pad(now.getDate())}`];
    if (special) return special;
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const days = [-1, 0, 1]
      .map((dy) => Math.round((targetDate(cd.to, today.getFullYear() + dy) - today) / 86400000))
      .reduce((best, d) => (Math.abs(d) < Math.abs(best) ? d : best));
    if (days === 0 && cd.today) return cd.today;
    if (days <= 0) return cd.after || "";
    return days === 1 ? cd.one : cd.many.replace("{n}", days);
  }

  function shuffle(list) {
    const a = list.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  // ------------------------------------------------------------------ create

  // `fetchJson(path, {method, body})` and `fetchBlob(path)` are how this talks
  // to the backend; both throw on failure. The launcher fallback is same-origin
  // and uses the plain-fetch defaults below. The extension overlay MUST inject
  // its own (routed through the service worker): a content script's fetch() is
  // the page's request, and Chromium blocks a recipe site from reaching
  // localhost (Local Network Access).
  function create({ backend = "", scrim, root, isBlocked = () => false, fetchJson, fetchBlob }) {
    const getJson = fetchJson || (async (path, { method = "GET", body } = {}) => {
      const res = await fetch(backend + path, {
        method,
        headers: body !== undefined ? { "Content-Type": "application/json" } : {},
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) throw new Error(`backend ${res.status}`);
      return res.json();
    });
    const getBlob = fetchBlob || (async (path) => {
      const res = await fetch(backend + path);
      if (!res.ok) throw new Error(`backend ${res.status}`);
      return res.blob();
    });
    const view = el("div", "rhss");
    view.hidden = true;
    view.innerHTML = `
      <div class="rhss-lights"></div>
      <div class="rhss-snow"></div>
      <div class="rhss-top">
        <div class="rhss-time"></div>
        <div class="rhss-date"></div>
        <div class="rhss-weather"></div>
        <div class="rhss-count"></div>
      </div>
      <div class="rhss-stage"></div>
      <div class="rhss-hint"></div>`;
    const q = (sel) => view.querySelector(sel);
    const cards = [makeCard(), makeCard()];
    cards.forEach((c) => q(".rhss-stage").appendChild(c.node));
    scrim.appendChild(view);

    const toast = el("div", "rhss-toast");
    toast.hidden = true;
    root.appendChild(toast);

    let state = "active";
    let gen = 0;            // bumps on every start/stop; stale async work checks it
    let running = false;
    let deck = null;
    let ui = {};            // the active deck's look and wording (deck.ui)
    const built = {};       // effect name -> its nodes exist already
    let order = [];
    let pos = 0;
    let front = 0;          // index into cards[] of the visible card
    let current = null;     // item on screen
    let currentSince = 0;
    let busy = false;       // a card swap (image fetch + decode) is in flight
    let clockTimer = null, rotateTimer = null, weatherTimer = null, toastTimer = null;
    const blobs = new Map(); // image path -> object URL (small LRU)

    // ----------------------------------------------------------- lifecycle

    function setState(next) {
      const prev = state;
      state = next;
      if (next === "clock") {
        start();
        return;
      }
      const lingered = !view.hidden && current && Date.now() - currentSince >= TOAST_MIN_SHOWN_MS;
      const item = current;
      stop();
      if (next === "active" && prev === "clock" && lingered) showToast(item);
    }

    async function start() {
      if (running) return;
      running = true;
      const my = ++gen;
      try {
        deck = await getJson("/api/screensaver");
      } catch {
        deck = null;
      }
      if (my !== gen || state !== "clock") return;
      if (!deck || !deck.active || !deck.items.length) {
        running = false; // plain clock shows through; refresh() can retry
        return;
      }
      hideToast();
      applyTheme(deck.ui || {});
      order = shuffle(deck.items);
      pos = 0;
      tickClock();
      clockTimer = setInterval(tickClock, 1000);
      loadWeather();
      weatherTimer = setInterval(loadWeather, WEATHER_MS);
      await showNext(my, true);
      if (my !== gen) return;
      view.hidden = false;
      rotateTimer = setInterval(() => showNext(my, false), ROTATE_MS);
    }

    function stop() {
      gen++;
      running = false;
      view.hidden = true;
      clearInterval(clockTimer); clearInterval(rotateTimer); clearInterval(weatherTimer);
      clockTimer = rotateTimer = weatherTimer = null;
      cards.forEach((c) => c.node.classList.remove("on"));
      current = null;
      busy = false;
    }

    function refresh() {
      if (state !== "clock") return;
      stop();
      start();
    }

    // Dress the view (and the after-wake toast) for this deck: colours as CSS
    // custom properties, effects built the first time a deck asks for them.
    function applyTheme(next) {
      ui = next;
      const vars = paletteVars(ui.palette || {});
      for (const node of [view, toast]) {
        for (const prop of THEME_VARS) {
          if (prop in vars) node.style.setProperty(prop, vars[prop]);
          else node.style.removeProperty(prop); // back to the stylesheet default
        }
      }
      const effects = ui.effects || [];
      const lights = q(".rhss-lights"), fall = q(".rhss-snow");
      lights.hidden = !effects.includes("lights");
      if (!lights.hidden && !built.lights) { buildLights(lights); built.lights = true; }
      // Snow and leaves share one layer; a deck uses one or the other.
      const falling = effects.includes("snow") ? "snow" : effects.includes("leaves") ? "leaves" : null;
      fall.hidden = !falling;
      if (falling && built.fall !== falling) {
        fall.replaceChildren();
        (falling === "snow" ? buildSnow : buildLeaves)(fall);
        built.fall = falling;
      }
      q(".rhss-hint").textContent = ui.hint || "Tap to wake";
    }

    // --------------------------------------------------------------- cards

    async function showNext(my, first) {
      if (busy) return; // a slow image must not stack up swaps
      busy = true;
      if (pos >= order.length) { order = shuffle(deck.items); pos = 0; }
      const item = order[pos++];
      const back = cards[1 - front];
      try {
        await fillCard(back, item);
      } finally {
        if (my === gen) busy = false;
      }
      if (my !== gen) return;
      if (!first) cards[front].node.classList.remove("on");
      back.node.classList.add("on");
      front = 1 - front;
      current = item;
      currentSince = Date.now();
      // Warm the next photo while this one is up.
      const upcoming = order[pos] || null;
      if (upcoming && upcoming.image) imageUrl(upcoming.image).catch(() => {});
    }

    function makeCard() {
      const node = el("div", "rhss-card");
      node.innerHTML = `
        <div class="rhss-photo"><img alt=""></div>
        <div class="rhss-ornament" hidden></div>
        <div class="rhss-chip"></div>
        <div class="rhss-name"></div>
        <div class="rhss-blurb"></div>
        <div class="rhss-meta"></div>`;
      return {
        node,
        photo: node.querySelector(".rhss-photo"),
        img: node.querySelector("img"),
        ornament: node.querySelector(".rhss-ornament"),
        chip: node.querySelector(".rhss-chip"),
        name: node.querySelector(".rhss-name"),
        blurb: node.querySelector(".rhss-blurb"),
        meta: node.querySelector(".rhss-meta"),
      };
    }

    async function fillCard(card, item) {
      const cat = (ui.categories || {})[item.category];
      card.chip.textContent = cat ? cat.label : "";
      if (cat) card.chip.style.setProperty("--chip", cat.color);
      card.name.textContent = item.name;
      card.blurb.textContent = item.blurb;
      const meta = [];
      if (item.total_time_s) meta.push(`⏱ ${fmtMinutes(item.total_time_s)}`);
      if (item.yields) meta.push(String(item.yields));
      // Only worth saying where a card *can* be added (a seasonal deck).
      if (item.saved && deck.addable) meta.push("✓ In My Recipes");
      card.meta.textContent = meta.join("  ·  ");
      let src = null;
      if (item.image) {
        try { src = await imageUrl(item.image); } catch { src = null; }
      }
      if (src) {
        card.img.src = src;
        try { await card.img.decode(); } catch { /* paint anyway */ }
        card.img.style.setProperty("--o", `${20 + Math.random() * 60}% ${20 + Math.random() * 60}%`);
        card.img.style.animation = "none"; // restart the zoom for this card
        void card.img.offsetWidth;
        card.img.style.animation = "";
        card.photo.hidden = false;
        card.ornament.hidden = true;
      } else {
        card.photo.hidden = true;
        card.ornament.hidden = false;
        card.ornament.textContent = item.emoji || ui.default_emoji || "🍽";
      }
    }

    // Images arrive as blobs through the host's fetchBlob rather than
    // <img src="http://localhost:8000/…">, which a recipe site's CSP or
    // Chromium's local-network rules would block.
    async function imageUrl(path) {
      if (blobs.has(path)) {
        const url = blobs.get(path);
        blobs.delete(path); blobs.set(path, url); // LRU touch
        return url;
      }
      const url = URL.createObjectURL(await getBlob(path));
      blobs.set(path, url);
      while (blobs.size > MAX_BLOBS) {
        const [oldPath, oldUrl] = blobs.entries().next().value;
        blobs.delete(oldPath);
        URL.revokeObjectURL(oldUrl);
      }
      return url;
    }

    // --------------------------------------------------- clock + weather

    function tickClock() {
      const now = new Date();
      q(".rhss-time").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      q(".rhss-date").textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
      q(".rhss-count").textContent = countdown(ui.countdown, now);
    }

    async function loadWeather() {
      let w = null;
      try { w = await getJson("/api/weather"); } catch { /* offline */ }
      const ok = w && w.configured && !w.error;
      q(".rhss-weather").textContent = ok
        ? `${w.emoji} ${Math.round(w.temp)}${w.unit || "°"} · H ${Math.round(w.high)}° / L ${Math.round(w.low)}°`
        : "";
    }

    // ---------------------------------------------------------------- toast

    function showToast(item) {
      if (!item || isBlocked()) return;
      clearTimeout(toastTimer);
      toast.innerHTML = `
        <div class="rt-row">
          <div class="rt-thumb"></div>
          <div class="rt-text">
            <div class="rt-kicker">On the screensaver just now</div>
            <div class="rt-name"></div>
          </div>
        </div>
        <div class="rt-actions">
          <button class="rt-add"></button>
          <button class="rt-view">View</button>
          <button class="rt-close" aria-label="Close">✕</button>
        </div>`;
      toast.querySelector(".rt-name").textContent = item.name;
      const thumbSlot = toast.querySelector(".rt-thumb");
      const cached = item.image && blobs.get(item.image);
      if (cached) {
        const img = el("img", "rt-thumb");
        img.src = cached;
        thumbSlot.replaceWith(img);
      } else {
        thumbSlot.textContent = item.emoji || ui.default_emoji || "🍽";
      }
      const add = toast.querySelector(".rt-add");
      // The saved-recipes deck is the user's own: nothing to add, only View.
      add.hidden = !(deck && deck.addable);
      const markSaved = (text) => { add.textContent = text; add.disabled = true; };
      if (item.saved) markSaved("✓ In My Recipes");
      else add.textContent = "＋ Add to My Recipes";
      add.onclick = async () => {
        add.disabled = true;
        add.textContent = "Adding…";
        clearTimeout(toastTimer);
        try {
          await getJson("/api/screensaver/add", { method: "POST", body: { url: item.url } });
          item.saved = true;
          markSaved("✓ Added to My Recipes");
        } catch {
          add.disabled = false;
          add.textContent = "Couldn't add — tap to retry";
        }
        toastTimer = setTimeout(hideToast, 5000);
      };
      toast.querySelector(".rt-view").onclick = () => {
        const base = backend || location.origin;
        location.href = `${base}/recipe?url=${encodeURIComponent(item.url)}`;
      };
      toast.querySelector(".rt-close").onclick = hideToast;
      toast.hidden = false;
      toastTimer = setTimeout(hideToast, TOAST_MS);
    }

    function hideToast() {
      clearTimeout(toastTimer);
      toast.hidden = true;
    }

    return { setState, refresh, hideToast };
  }

  // ------------------------------------------------------------ decoration

  function buildLights(host) {
    const SWAGS = 3, PER_SWAG = 6, SAG = 7; // SAG in --u units
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 100 12");
    svg.setAttribute("preserveAspectRatio", "none");
    let d = "M 0 1";
    for (let s = 0; s < SWAGS; s++) {
      const x0 = (s / SWAGS) * 100, x1 = ((s + 1) / SWAGS) * 100;
      d += ` Q ${(x0 + x1) / 2} ${1 + SAG * 2} ${x1} 1`;
    }
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "#2c3a2f");
    path.setAttribute("stroke-width", "2");
    path.setAttribute("vector-effect", "non-scaling-stroke");
    svg.appendChild(path);
    host.appendChild(svg);
    let n = 0;
    for (let s = 0; s < SWAGS; s++) {
      for (let i = 0; i < PER_SWAG; i++) {
        const t = (i + 0.5) / PER_SWAG;            // position along this swag
        const x = ((s + t) / SWAGS) * 100;
        // On the wire: a quadratic from y=1 via control y=1+2*SAG back to
        // y=1 is y(t) = 1 + 4*SAG*t*(1-t) (x is linear in t).
        const y = 1 + 4 * SAG * t * (1 - t);
        const bulb = el("span", "rhss-bulb");
        bulb.style.left = `${x}%`;
        bulb.style.top = `calc(var(--u) * ${y})`;
        bulb.style.setProperty("--c", BULB_COLORS[n % BULB_COLORS.length]);
        bulb.style.setProperty("--t", `${1.4 + Math.random() * 1.8}s`);
        bulb.style.setProperty("--d", `${-Math.random() * 3}s`);
        host.appendChild(bulb);
        n++;
      }
    }
  }

  function buildSnow(host) {
    for (let i = 0; i < 28; i++) {
      const flake = el("span", "rhss-flake");
      const size = 4 + Math.random() * 12;
      const dur = 14 + Math.random() * 18;
      flake.style.left = `${Math.random() * 100}%`;
      flake.style.setProperty("--s", `${size}px`);
      flake.style.setProperty("--o", (0.25 + Math.random() * 0.55).toFixed(2));
      flake.style.setProperty("--t", `${dur}s`);
      flake.style.setProperty("--d", `${-Math.random() * dur}s`); // already mid-fall at start
      flake.style.setProperty("--x", `${(Math.random() - 0.5) * 18}vw`);
      host.appendChild(flake);
    }
  }

  function buildLeaves(host) {
    for (let i = 0; i < 14; i++) {
      const leaf = el("span", "rhss-leaf", LEAVES[i % LEAVES.length]);
      const dur = 16 + Math.random() * 16;
      leaf.style.left = `${Math.random() * 100}%`;
      leaf.style.setProperty("--s", `${18 + Math.random() * 20}px`);
      leaf.style.setProperty("--o", (0.35 + Math.random() * 0.45).toFixed(2));
      leaf.style.setProperty("--t", `${dur}s`);
      leaf.style.setProperty("--d", `${-Math.random() * dur}s`);
      leaf.style.setProperty("--x", `${(Math.random() - 0.5) * 30}vw`);
      leaf.style.setProperty("--r", `${(Math.random() < 0.5 ? -1 : 1) * (240 + Math.random() * 480)}deg`);
      host.appendChild(leaf);
    }
  }

  // For hosts without a shadow root (the launcher fallback).
  function injectCss(doc = document) {
    if (doc.getElementById("rhss-css")) return;
    const style = doc.createElement("style");
    style.id = "rhss-css";
    style.textContent = CSS;
    doc.head.appendChild(style);
  }

  globalThis.RHScreensaver = { CSS, create, injectCss };
})();
