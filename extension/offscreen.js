// Offscreen document: the only MV3 context allowed to play audio from the
// service worker's world. Runs in the extension origin, so page CSP can't
// block it.

const audio = new Audio(chrome.runtime.getURL("assets/alarm.wav"));
audio.loop = true;

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.target !== "offscreen") return;
  if (msg.cmd === "play") {
    audio.volume = Math.min(1, Math.max(0, (msg.volume ?? 80) / 100));
    audio.play().catch(() => {});
  } else if (msg.cmd === "stop") {
    audio.pause();
    audio.currentTime = 0;
  }
});
