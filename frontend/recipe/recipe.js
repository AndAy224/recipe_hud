import { WSClient } from "/shared/ws-client.js";
import { QTY, fmtQty, parseQty, scaleLine } from "/recipe/scale.js";

const $ = (id) => document.getElementById(id);

const params = new URLSearchParams(location.search);
const targetUrl = params.get("url");

let currentData = null;
const checkedIngredients = new Set(); // indices; shared by main list + cook-mode peek

// ------------------------------------------------------------- scaling

const SCALE_STEPS = [[0.5, "½×"], [1, "1×"], [2, "2×"], [3, "3×"]];
let scaleFactor = 1;
try {
  scaleFactor = Number(sessionStorage.getItem(`scale:${targetUrl}`)) || 1;
} catch { /* storage unavailable */ }

function setScale(factor) {
  scaleFactor = factor;
  try { sessionStorage.setItem(`scale:${targetUrl}`, String(factor)); } catch { /* ignore */ }
  renderScaleButtons();
  renderIngredients($("ingredients"));
  renderIngredients($("cm-ing-list"));
  renderMeta();
}

function renderScaleButtons() {
  for (const container of document.querySelectorAll(".scale-ctl")) {
    container.replaceChildren(...SCALE_STEPS.map(([factor, label]) => {
      const btn = document.createElement("button");
      btn.textContent = label;
      btn.classList.toggle("active", factor === scaleFactor);
      btn.onclick = (ev) => { ev.stopPropagation(); setScale(factor); };
      return btn;
    }));
  }
}

// Duration phrases in step text. One match covers a single amount
// ("20 minutes"), a range ("20 to 25 minutes" — larger end wins), or a
// compound ("1 hour 30 minutes", "1h 30m" — parts sum). Single-letter units
// only count when attached to the number, so prose like "1 h" stays text.
const UNIT_WORD = "(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)";
const DUR_SEG = `${QTY}(?:\\s*(?:to|[-–—])\\s*${QTY})?(?:\\s*${UNIT_WORD}\\b|[hms]\\b)`;
const DURATION_RE = new RegExp(`${DUR_SEG}(?:\\s*(?:and\\s+)?${DUR_SEG}){0,2}`, "gi");
const DUR_TOKEN_RE = new RegExp(`(${QTY})\\s*(${UNIT_WORD}|[hms]\\b)?`, "gi");

const UNIT_SECONDS = { h: 3600, m: 60, s: 1 };

function parseDurationSeconds(phrase) {
  const tokens = [...phrase.matchAll(DUR_TOKEN_RE)]
    .map(([, qty, unit]) => ({ value: parseQty(qty), unit }))
    .filter((t) => t.value !== null);
  let total = 0;
  for (let i = 0; i < tokens.length; i++) {
    let { value, unit } = tokens[i];
    if (!unit && tokens[i + 1]?.unit) {
      // Unit-less quantity = the low end of a range; take the larger end.
      value = Math.max(value, tokens[i + 1].value);
      unit = tokens[i + 1].unit;
      i++;
    }
    if (!unit) return null;
    total += value * UNIT_SECONDS[unit[0].toLowerCase()];
  }
  return total || null;
}

function show(id) {
  for (const section of ["loading", "error", "recipe"]) $(section).hidden = section !== id;
}

async function load(refresh = false) {
  if (!targetUrl) {
    showError("No URL given. Open this page as /recipe?url=…");
    return;
  }
  show("loading");
  const query = `url=${encodeURIComponent(targetUrl)}${refresh ? "&refresh=1" : ""}`;
  let resp;
  try {
    resp = await fetch(`/api/recipe/extract?${query}`);
  } catch {
    showError("Backend unreachable.");
    return;
  }
  if (!resp.ok) {
    let detail = "Extraction failed.";
    try { detail = (await resp.json()).detail || detail; } catch { /* not json */ }
    showError(detail);
    return;
  }
  render(await resp.json(), refresh);
}

function showError(detail) {
  $("error-detail").textContent = detail;
  show("error");
}

function render(data, refresh = false) {
  currentData = data;
  checkedIngredients.clear();
  document.title = data.title;
  $("title").textContent = data.title;

  const hero = $("hero");
  hero.hidden = !data.image_url;
  if (data.image_url) hero.src = data.image_url;

  renderMeta();
  renderByline();
  $("wine").hidden = true;
  renderNutrition();
  if (data.kind === "recipe") loadWine(data.url, refresh);

  const tags = data.tags || [];
  $("tags").hidden = tags.length === 0;
  $("tags").textContent = tags.map((t) => `#${t}`).join("  ");

  $("ingredients-section").hidden = data.ingredients.length === 0;
  renderScaleButtons();
  renderIngredients($("ingredients"));

  const isArticle = data.kind === "article";
  document.querySelector("article").classList.toggle("plain", isArticle);
  $("steps-heading").textContent = isArticle ? "Page text" : "Steps";
  $("steps").replaceChildren(...data.steps.map((text, i) => stepItem(text, i, isArticle)));

  renderSaveButton(data.saved);
  $("cook-mode-btn").hidden = isArticle || data.steps.length === 0;

  show("recipe");
  restoreCookMode();
}

function renderMeta() {
  if (!currentData) return;
  const meta = [];
  if (currentData.source_host) meta.push(currentData.source_host);
  if (currentData.yields) {
    let yields = currentData.yields;
    if (scaleFactor !== 1) {
      const lead = yields.match(/^(\d+(?:\.\d+)?)/);
      const value = lead && parseQty(lead[1]);
      if (value) yields = `${fmtQty(value * scaleFactor)}${yields.slice(lead[1].length)} (scaled)`;
    }
    meta.push(yields);
  }
  const m = currentData.meta || {};
  if (m.prep_time_s && m.cook_time_s) {
    meta.push(`⏱ ${Math.round(m.prep_time_s / 60)} min prep · ${Math.round(m.cook_time_s / 60)} min cook`);
  } else if (currentData.total_time_s) {
    meta.push(`⏱ ${Math.round(currentData.total_time_s / 60)} min total`);
  }
  $("meta").textContent = meta.join(" · ");
}

function renderByline() {
  const m = currentData.meta || {};
  const parts = [m.author && `By ${m.author}`, m.cuisine, m.category]
    .filter(Boolean)
    .map((p) => p.replace(/,(?=\S)/g, ", ")); // sites emit "Italian Inspired,Italian"
  $("byline").hidden = parts.length === 0;
  $("byline").textContent = parts.join(" · ");
}

// Show the at-a-glance card when nutrition and/or wine has content, hiding
// each labelled block independently based on its own row.
function syncGlance() {
  const hasNut = !$("nutrition").hidden;
  const hasWine = !$("wine").hidden;
  $("nut-block").hidden = !hasNut;
  $("wine-block").hidden = !hasWine;
  $("glance").hidden = !(hasNut || hasWine);
}

// Nutrition is per serving; scaling changes servings, not these values, so
// this never re-renders on scale changes.
const NUTRITION_FACTS = [
  ["calories", (v) => `${v} cal`],
  ["protein_g", (v) => `${v}g protein`],
  ["fat_g", (v) => `${v}g fat`],
  ["carbs_g", (v) => `${v}g carbs`],
];

function renderNutrition() {
  const n = (currentData.meta || {}).nutrition;
  const parts = [];
  for (const [key, fmt] of NUTRITION_FACTS) {
    if (n && n[key] != null) parts.push(fmt(n[key]));
  }
  const el = $("nutrition");
  el.hidden = parts.length === 0;
  el.textContent = parts.join("  ·  ");
  syncGlance();
}

// Wine pairing loads after the recipe paints (the backend may call an LLM), so
// the pill pops in when ready. Scale-independent — fetched once per recipe.
async function loadWine(url, refresh = false) {
  let pairing;
  try {
    const query = `url=${encodeURIComponent(url)}${refresh ? "&refresh=1" : ""}`;
    const resp = await fetch(`/api/recipe/wine?${query}`);
    if (!resp.ok) return;
    pairing = await resp.json();
  } catch { return; }
  // The user may have navigated to another recipe while we waited.
  if (!currentData || currentData.url !== url) return;
  renderWine(pairing);
}

function renderWine(pairing) {
  const el = $("wine");
  if (!pairing || !pairing.wine) { el.hidden = true; syncGlance(); return; }
  const name = document.createElement("span");
  name.className = "wine-name";
  name.textContent = pairing.wine;
  el.replaceChildren(name);
  if (pairing.note) {
    const note = document.createElement("span");
    note.className = "wine-note";
    note.textContent = ` — ${pairing.note}`;
    el.appendChild(note);
  }
  el.hidden = false;
  syncGlance();
}

function renderIngredients(listEl) {
  listEl.replaceChildren(...currentData.ingredients.map((text, i) => {
    const li = document.createElement("li");
    li.appendChild(document.createTextNode(scaleLine(text, scaleFactor)));
    li.classList.toggle("checked", checkedIngredients.has(i));
    li.onclick = () => {
      if (checkedIngredients.has(i)) checkedIngredients.delete(i);
      else checkedIngredients.add(i);
      // Repaint both lists so main view and cook-mode peek stay in sync.
      renderIngredients($("ingredients"));
      renderIngredients($("cm-ing-list"));
    };
    return li;
  }));
}

// Turn durations in step text ("simmer 20 minutes") into tap-to-start timer
// buttons. Used by both the step list and cook mode.
function linkifyDurations(text, index) {
  const frag = document.createDocumentFragment();
  let last = 0;
  for (const match of text.matchAll(DURATION_RE)) {
    const full = match[0];
    const seconds = parseDurationSeconds(full);
    frag.appendChild(document.createTextNode(text.slice(last, match.index)));
    last = match.index + full.length;
    if (seconds && seconds >= 10 && seconds <= 12 * 3600) {
      const btn = document.createElement("button");
      btn.className = "step-timer";
      btn.textContent = `⏱ ${full}`;
      btn.onclick = async (ev) => {
        ev.stopPropagation();
        const title = currentData.title || "Recipe";
        const short = title.length > 40 ? title.slice(0, 39).trimEnd() + "…" : title;
        await fetch("/api/timers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            label: `${short} — step ${index + 1}`,
            seconds,
          }),
        });
        btn.textContent = `✓ ${full}`;
      };
      frag.appendChild(btn);
    } else {
      frag.appendChild(document.createTextNode(full));
    }
  }
  frag.appendChild(document.createTextNode(text.slice(last)));
  return frag;
}

function stepItem(text, index, isArticle) {
  const li = document.createElement("li");
  li.appendChild(linkifyDurations(text, index));
  if (!isArticle) {
    li.onclick = () => li.classList.toggle("done");
  }
  return li;
}

// ------------------------------------------------------------ save star

function renderSaveButton(saved) {
  const btn = $("save-btn");
  btn.textContent = saved ? "★ Saved" : "☆ Save";
  btn.classList.toggle("saved", saved);
}

$("save-btn").onclick = async () => {
  if (!currentData) return;
  const action = currentData.saved ? "unsave" : "save";
  const resp = await fetch(`/api/recipe/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: targetUrl }),
  });
  if (resp.ok) {
    currentData.saved = !currentData.saved;
    renderSaveButton(currentData.saved);
  }
};

// ------------------------------------------------------------ cook mode

let cookStep = 0;
let cookActive = false;
let cookHeartbeat = null;
const cookKey = () => `cookmode:${targetUrl}`;

function enterCookMode(step = 0) {
  cookActive = true;
  cookStep = Math.min(Math.max(0, step), currentData.steps.length - 1);
  $("cookmode").hidden = false;
  renderIngredients($("cm-ing-list"));
  renderCookStep();
  // Keep the screen awake while cooking: 45s beats both the normal (300s)
  // and night (60s) idle timeouts. Dies with the page on navigation.
  const ping = () => fetch("/api/activity", { method: "POST" }).catch(() => {});
  ping();
  cookHeartbeat = setInterval(ping, 45000);
}

function exitCookMode() {
  cookActive = false;
  $("cookmode").hidden = true;
  $("cm-ingredients").hidden = true;
  clearInterval(cookHeartbeat);
  saveCookState();
}

function renderCookStep() {
  const total = currentData.steps.length;
  $("cm-counter").textContent = `Step ${cookStep + 1} / ${total}`;
  $("cm-step").replaceChildren(linkifyDurations(currentData.steps[cookStep], cookStep));
  $("cm-prev").disabled = cookStep === 0;
  $("cm-next").textContent = cookStep === total - 1 ? "✓ Done" : "Next ›";
  fetch("/api/activity", { method: "POST" }).catch(() => {});
  saveCookState();
}

function saveCookState() {
  try {
    sessionStorage.setItem(cookKey(), JSON.stringify({ step: cookStep, active: cookActive }));
  } catch { /* storage unavailable */ }
}

function restoreCookMode() {
  if ($("cook-mode-btn").hidden) return;
  try {
    const state = JSON.parse(sessionStorage.getItem(cookKey()));
    if (state && state.active) enterCookMode(state.step);
  } catch { /* no saved state */ }
}

$("cook-mode-btn").onclick = () => enterCookMode(cookStep);
$("cm-exit").onclick = exitCookMode;
$("cm-prev").onclick = () => { if (cookStep > 0) { cookStep--; renderCookStep(); } };
$("cm-next").onclick = () => {
  if (cookStep >= currentData.steps.length - 1) exitCookMode();
  else { cookStep++; renderCookStep(); }
};
$("cm-ing-toggle").onclick = () => { $("cm-ingredients").hidden = false; };
$("cm-ing-close").onclick = () => { $("cm-ingredients").hidden = true; };

$("open-original").onclick = () => { location.href = targetUrl; };
$("error-original").onclick = () => { location.href = targetUrl; };
$("error-home").onclick = () => { location.href = "/"; };
$("refresh").onclick = () => load(true);

// ------------------------------------------------- ws: night dim, navigate

let nightNow = false;
let nightDimEnabled = true;

function applyNight() {
  document.documentElement.dataset.night = nightNow && nightDimEnabled ? "1" : "";
}

new WSClient("recipe", (type, data) => {
  if (type === "snapshot") {
    nightNow = !!data.display.night;
    nightDimEnabled = !!data.settings.night_dim_enabled;
    document.documentElement.dataset.theme = data.settings.theme || "dark";
    applyNight();
  } else if (type === "night.state") {
    nightNow = !!data.night;
    applyNight();
  } else if (type === "settings.updated") {
    if ("night_dim_enabled" in data) nightDimEnabled = !!data.night_dim_enabled;
    if ("theme" in data) document.documentElement.dataset.theme = data.theme;
    applyNight();
  } else if (type === "navigate") {
    location.href = data.url;
  }
});

load();
