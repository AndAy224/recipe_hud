// Phone companion: a recipe's ingredients as a tick-off list, at the scale the
// kitchen display was showing. Opened by scanning the QR on the clean view
// (/list?url=<recipe>&scale=<factor>).
//
// The page is served by the kiosk, so it only loads on the home network —
// which is why the main action is "copy what's left" into the phone's own
// notes app rather than keeping the list here.
import { fmtQty, parseQty, scaleLine } from "/recipe/scale.js";

const $ = (id) => document.getElementById(id);

const params = new URLSearchParams(location.search);
const recipeUrl = params.get("url");
const scale = [0.5, 1, 2, 3].includes(Number(params.get("scale"))) ? Number(params.get("scale")) : 1;

let lines = [];
let have = new Set(); // indices already in the cupboard
const storeKey = `list:${recipeUrl}`;

function loadTicks() {
  try {
    const saved = JSON.parse(localStorage.getItem(storeKey));
    // Indices only mean something for the same ingredient list.
    if (saved && saved.count === lines.length) have = new Set(saved.have);
  } catch { /* nothing saved, or storage unavailable */ }
}

function saveTicks() {
  try {
    localStorage.setItem(storeKey, JSON.stringify({ count: lines.length, have: [...have] }));
  } catch { /* ignore */ }
}

function render() {
  $("items").replaceChildren(...lines.map((text, i) => {
    const li = document.createElement("li");
    li.textContent = text;
    li.classList.toggle("have", have.has(i));
    li.onclick = () => {
      if (have.has(i)) have.delete(i); else have.add(i);
      saveTicks();
      render();
    };
    return li;
  }));
  const left = lines.length - have.size;
  $("copy").textContent = left === lines.length
    ? `Copy all ${left} items`
    : `Copy the ${left} I still need`;
  $("copy").disabled = left === 0;
  $("reset").hidden = have.size === 0;
}

// navigator.clipboard needs HTTPS, and this page is plain http on the LAN —
// so the old selection-based copy is the path that actually runs on a phone.
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall through */ }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.cssText = "position:fixed;top:0;left:0;opacity:0;";
  document.body.appendChild(area);
  area.select();
  area.setSelectionRange(0, text.length);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  area.remove();
  return ok;
}

$("copy").onclick = async () => {
  const wanted = lines.filter((_, i) => !have.has(i));
  const text = `${$("title").textContent}\n` + wanted.map((line) => `- ${line}`).join("\n");
  const ok = await copyText(text);
  $("status").textContent = ok
    ? "Copied ✓ — paste it into your notes or reminders app"
    : "Couldn't copy automatically — press and hold the list to select it";
  $("status").className = ok ? "hint ok" : "hint err";
};

$("reset").onclick = () => { have.clear(); saveTicks(); render(); };

function scaledYields(yields) {
  if (!yields || scale === 1) return yields || "";
  const lead = yields.match(/^(\d+(?:\.\d+)?)/);
  const value = lead && parseQty(lead[1]);
  return value ? `${fmtQty(value * scale)}${yields.slice(lead[1].length)}` : yields;
}

async function load() {
  const fail = (text) => {
    $("loading").hidden = true;
    $("error").textContent = text;
    $("error").hidden = false;
  };
  if (!recipeUrl) return fail("No recipe given. Scan the code on the kitchen display's recipe page.");
  let data;
  try {
    const resp = await fetch(`/api/recipe/extract?url=${encodeURIComponent(recipeUrl)}`);
    if (!resp.ok) return fail("The kitchen display couldn't load that recipe.");
    data = await resp.json();
  } catch {
    return fail("Can't reach the kitchen display — are you on the home Wi-Fi?");
  }
  if (!data.ingredients || data.ingredients.length === 0) {
    return fail("That page has no ingredient list.");
  }
  document.title = `Shopping list — ${data.title}`;
  $("title").textContent = data.title;
  const meta = [];
  if (scale !== 1) meta.push(`scaled ${scale === 0.5 ? "½" : scale}×`);
  const yields = scaledYields(data.yields);
  if (yields) meta.push(yields);
  $("meta").textContent = meta.join(" · ");
  $("meta").hidden = meta.length === 0;
  lines = data.ingredients.map((text) => scaleLine(text, scale));
  loadTicks();
  render();
  $("loading").hidden = true;
  $("list").hidden = false;
}

load();
