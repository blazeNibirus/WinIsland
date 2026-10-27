// WinIsland: отправляет прогресс загрузок браузера в программу на этом же компьютере.
// Никуда в интернет ничего не уходит: адрес 127.0.0.1 — это ваш собственный компьютер.
const URL_ISLAND = "http://127.0.0.1:47831/download";
let timer = null;

function fileName(path) {
  if (!path) return "";
  return path.split(/[\\/]/).pop();
}

function pack(d) {
  let name = fileName(d.filename);
  if (!name && d.url) {
    try { name = decodeURIComponent(d.url.split("?")[0].split("/").pop()); } catch (e) { name = ""; }
  }
  return {
    id: d.id,
    name: name || "Файл",
    received: d.bytesReceived || 0,
    total: d.totalBytes > 0 ? d.totalBytes : 0,
    state: d.state,
    paused: !!d.paused
  };
}

function send(items) {
  if (!items.length) return;
  fetch(URL_ISLAND, {
    method: "POST",
    headers: { "Content-Type": "text/plain" },
    body: JSON.stringify({ browser: "chromium", items: items })
  }).catch(function () { /* программа не запущена — ничего страшного */ });
}

async function tick() {
  const active = await chrome.downloads.search({ state: "in_progress" });
  if (active.length === 0) { stop(); return; }
  send(active.map(pack));
}

function start() {
  if (timer) return;
  tick();
  timer = setInterval(tick, 500);
}

function stop() {
  if (timer) { clearInterval(timer); timer = null; }
}

chrome.downloads.onCreated.addListener(function () { start(); });

chrome.downloads.onChanged.addListener(async function (delta) {
  if (delta.state) {
    const res = await chrome.downloads.search({ id: delta.id });
    if (res.length) send([pack(res[0])]);
  }
  start();
});

chrome.runtime.onStartup.addListener(function () { start(); });
start();
