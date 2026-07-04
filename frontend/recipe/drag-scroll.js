// Drag-anywhere kinetic scrolling for the kitchen touchscreen.
//
// The panel's touch is delivered to Chromium as MOUSE events (labwc runs the
// touch device with mouseEmulation), so the browser's native finger-drag
// scrolling never kicks in — you'd be stuck aiming at the thin scrollbar.
// Pointer events cover mouse *and* touch, so we grab the press, scroll the
// nearest scrollable element as the finger moves, and coast with momentum on
// release — exactly like flicking a phone. A short press still clicks through
// (tap-to-check ingredients, buttons), because we only take over once the
// finger travels past a small threshold.
(() => {
  const THRESHOLD = 8;     // px of travel before a press becomes a scroll drag
  const FRICTION = 0.94;   // momentum velocity kept per frame (~16ms)
  const MIN_VEL = 0.02;    // px/ms — below this momentum stops

  const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
  const doc = () => document.scrollingElement || document.documentElement;

  const isPage = (el) => el === doc();
  const scrollTopOf = (el) => (isPage(el) ? window.scrollY : el.scrollTop);
  const setTop = (el, v) => (isPage(el) ? window.scrollTo(0, v) : (el.scrollTop = v));
  const maxTop = (el) =>
    isPage(el)
      ? doc().scrollHeight - window.innerHeight
      : el.scrollHeight - el.clientHeight;

  // Walk up from the touched element to the first thing that actually scrolls;
  // fall back to the whole page. Handles cook mode's step + ingredient drawer.
  function findScroller(target) {
    let el = target;
    while (el && el !== document.body && el !== document.documentElement) {
      if (el.scrollHeight - el.clientHeight > 4) {
        const oy = getComputedStyle(el).overflowY;
        if (oy === "auto" || oy === "scroll") return el;
      }
      el = el.parentElement;
    }
    return doc();
  }

  let active = false;   // pointer is down and being tracked
  let dragging = false; // travelled past threshold → we own the scroll
  let scroller = null;
  let pointerId = null;
  let startY = 0, startScroll = 0, lastY = 0, lastT = 0, vel = 0;
  let raf = 0;

  function stopMomentum() {
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
    vel = 0;
  }

  function onDown(e) {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    stopMomentum(); // a press pauses coasting, like a phone
    scroller = findScroller(e.target);
    if (maxTop(scroller) <= 0) {
      scroller = doc();
      if (maxTop(scroller) <= 0) return; // nothing to scroll
    }
    active = true;
    dragging = false;
    pointerId = e.pointerId;
    startY = lastY = e.clientY;
    lastT = e.timeStamp;
    startScroll = scrollTopOf(scroller);
  }

  function onMove(e) {
    if (!active || e.pointerId !== pointerId) return;
    const dy = e.clientY - startY;
    if (!dragging) {
      if (Math.abs(dy) < THRESHOLD) return;
      dragging = true;
      document.documentElement.style.userSelect = "none";
    }
    e.preventDefault(); // suppress text selection / native drag
    setTop(scroller, clamp(startScroll - dy, 0, maxTop(scroller)));
    const dt = e.timeStamp - lastT;
    if (dt > 0) vel = (e.clientY - lastY) / dt; // px/ms, +down
    lastY = e.clientY;
    lastT = e.timeStamp;
  }

  function onUp(e) {
    if (!active || e.pointerId !== pointerId) return;
    active = false;
    document.documentElement.style.userSelect = "";
    if (dragging) {
      suppressNextClick(); // don't let the release tap toggle an ingredient
      startMomentum();
    }
    dragging = false;
  }

  function startMomentum() {
    if (Math.abs(vel) < MIN_VEL) return;
    let last = performance.now();
    const step = (now) => {
      const dt = now - last;
      last = now;
      const next = clamp(scrollTopOf(scroller) - vel * dt, 0, maxTop(scroller));
      setTop(scroller, next);
      if (next <= 0 || next >= maxTop(scroller)) return; // hit an edge
      vel *= FRICTION;
      if (Math.abs(vel) < MIN_VEL) return;
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
  }

  function suppressNextClick() {
    const kill = (ev) => { ev.stopPropagation(); ev.preventDefault(); };
    document.addEventListener("click", kill, { capture: true, once: true });
    // If no click follows (some drags don't synthesize one), clean up.
    setTimeout(() => document.removeEventListener("click", kill, { capture: true }), 350);
  }

  window.addEventListener("pointerdown", onDown, { passive: true });
  window.addEventListener("pointermove", onMove, { passive: false });
  window.addEventListener("pointerup", onUp, { passive: true });
  window.addEventListener("pointercancel", onUp, { passive: true });
})();
