import { WSClient } from "/shared/ws-client.js";

const $ = (id) => document.getElementById(id);

const params = new URLSearchParams(location.search);
const targetUrl = params.get("url");

let currentData = null;
const checkedIngredients = new Set(); // indices; shared by main list + cook-mode peek

const DURATION_RE = /(\d+)(?:\s*(?:to|[-–—])\s*(\d+))?\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)\b/gi;

const UNIT_SECONDS = { h: 3600, m: 60, s: 1 };

function unitFactor(unit) {
  const c = unit[0].toLowerCase();
  return UNIT_SECONDS[c === "h" ? "h" : c === "s" && unit.toLowerCase().startsWith("sec") ? "s" : "m"];
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
  render(await resp.json());
}

function showError(detail) {
  $("error-detail").textContent = detail;
  show("error");
}

function render(data) {
  currentData = data;
  checkedIngredients.clear();
  document.title = data.title;
  $("title").textContent = data.title;

  const hero = $("hero");
  hero.hidden = !data.image_url;
  if (data.image_url) hero.src = data.image_url;

  const meta = [];
  if (data.source_host) meta.push(data.source_host);
  if (data.yields) meta.push(data.yields);
  if (data.total_time_s) meta.push(`⏱ ${Math.round(data.total_time_s / 60)} min total`);
  $("meta").textContent = meta.join(" · ");

  $("ingredients-section").hidden = data.ingredients.length === 0;
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

function renderIngredients(listEl) {
  listEl.replaceChildren(...currentData.ingredients.map((text, i) => {
    const li = document.createElement("li");
    li.appendChild(document.createTextNode(text));
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
    const [full, a, b, unit] = match;
    const seconds = Math.max(+a, +(b || 0)) * unitFactor(unit);
    frag.appendChild(document.createTextNode(text.slice(last, match.index)));
    last = match.index + full.length;
    if (seconds >= 10 && seconds <= 12 * 3600) {
      const btn = document.createElement("button");
      btn.className = "step-timer";
      btn.textContent = `⏱ ${full}`;
      btn.onclick = async (ev) => {
        ev.stopPropagation();
        await fetch("/api/timers", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            label: `Step ${index + 1} — ${full}`.slice(0, 60),
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
