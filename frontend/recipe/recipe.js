const $ = (id) => document.getElementById(id);

const params = new URLSearchParams(location.search);
const targetUrl = params.get("url");

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
  $("ingredients").replaceChildren(...data.ingredients.map((text) => {
    const li = document.createElement("li");
    li.appendChild(document.createTextNode(text));
    li.onclick = () => li.classList.toggle("checked");
    return li;
  }));

  const isArticle = data.kind === "article";
  document.querySelector("article").classList.toggle("plain", isArticle);
  $("steps-heading").textContent = isArticle ? "Page text" : "Steps";
  $("steps").replaceChildren(...data.steps.map((text, i) => stepItem(text, i, isArticle)));

  show("recipe");
}

function stepItem(text, index, isArticle) {
  const li = document.createElement("li");
  let last = 0;
  for (const match of text.matchAll(DURATION_RE)) {
    const [full, a, b, unit] = match;
    const seconds = Math.max(+a, +(b || 0)) * unitFactor(unit);
    li.appendChild(document.createTextNode(text.slice(last, match.index)));
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
      li.appendChild(btn);
    } else {
      li.appendChild(document.createTextNode(full));
    }
  }
  li.appendChild(document.createTextNode(text.slice(last)));
  if (!isArticle) {
    li.onclick = () => li.classList.toggle("done");
  }
  return li;
}

$("open-original").onclick = () => { location.href = targetUrl; };
$("error-original").onclick = () => { location.href = targetUrl; };
$("error-home").onclick = () => { location.href = "/"; };
$("refresh").onclick = () => load(true);

load();
