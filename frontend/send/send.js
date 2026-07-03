const $ = (id) => document.getElementById(id);

function setStatus(text, ok) {
  $("status").textContent = text;
  $("status").className = ok ? "ok" : "err";
}

function getUrl() {
  const url = $("url").value.trim();
  if (!/^https?:\/\/.+/.test(url)) {
    setStatus("Enter a full link starting with http(s)://", false);
    return null;
  }
  return url;
}

async function send(clean) {
  const url = getUrl();
  if (!url) return;
  setStatus("Sending…", true);
  try {
    const resp = await fetch("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, clean }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const data = await resp.json();
    setStatus(data.clients > 0 ? "Sent ✓ — check the kitchen display" : "Sent, but the kiosk looks disconnected", data.clients > 0);
  } catch (err) {
    setStatus(`Failed: ${err.message}`, false);
  }
}

$("open-direct").onclick = () => send(false);
$("open-clean").onclick = () => send(true);

$("save").onclick = async () => {
  const url = getUrl();
  if (!url) return;
  const btn = $("save");
  btn.disabled = true;
  setStatus("Fetching recipe… (can take a few seconds)", true);
  try {
    const resp = await fetch("/api/recipe/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const saved = await resp.json();
    setStatus(`Saved ✓ — "${saved.title}" is now in My Recipes`, true);
  } catch (err) {
    setStatus(`Couldn't save: ${err.message}`, false);
  } finally {
    btn.disabled = false;
  }
};
