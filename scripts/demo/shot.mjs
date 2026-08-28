// Tiny CDP screenshotter: chromium --remote-debugging-port + Node's built-in WebSocket.
// usage: node shot.mjs --url URL --out file.png [--w 1440 --h 900] [--scheme light|dark]
//        [--local key=value ...] [--wait ms] [--full] [--click selector] [--type selector=text]
import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = process.argv.slice(2);
const opt = { w: 1440, h: 900, scheme: "light", wait: 1500, local: [], full: false, click: [], type: [], act: [] };
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === "--full") opt.full = true;
  else if (a === "--local") opt.local.push(args[++i]);
  else if (a === "--click") opt.click.push(args[++i]);
  else if (a === "--type") opt.type.push(args[++i]);
  else if (a === "--act") opt.act.push(args[++i]);
  else if (a.startsWith("--")) opt[a.slice(2)] = args[++i];
}
opt.w = Number(opt.w); opt.h = Number(opt.h); opt.wait = Number(opt.wait);

const port = 9222 + Math.floor(Math.random() * 500);
// Its own profile, always. On the shared default one a second chromium hands
// itself to the instance already running and exits, and this script then talks
// to a debugging port that is about to disappear.
const profile = mkdtempSync(join(tmpdir(), "shot-"));
const chrome = spawn("chromium", [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
  `--user-data-dir=${profile}`, "--no-first-run", "--no-default-browser-check",
  `--remote-debugging-port=${port}`, `--window-size=${opt.w},${opt.h}`, "about:blank",
], { stdio: "ignore" });

async function json(path) {
  for (let i = 0; i < 50; i++) {
    try { const r = await fetch(`http://127.0.0.1:${port}${path}`); return await r.json(); } catch { await sleep(100); }
  }
  throw new Error("chromium did not start");
}

const [target] = (await json("/json/list")).filter((t) => t.type === "page");
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((res) => (ws.onopen = res));
let id = 0; const pending = new Map();
const consoleLog = [];
let rec = null; // active screencast: { prefix, maxHold, frames: [{ file, t }] }
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === "Page.screencastFrame") {
    // The stream stalls unless every frame is acked. Chromium only emits on repaint,
    // so a still screen costs nothing and its duration comes from the next frame's clock.
    send("Page.screencastFrameAck", { sessionId: m.params.sessionId });
    if (rec) {
      const file = `${rec.prefix}-${String(rec.frames.length).padStart(4, "0")}.png`;
      writeFileSync(file, Buffer.from(m.params.data, "base64"));
      rec.frames.push({ file, t: Date.now() });
    }
  }
  if (m.method === "Runtime.consoleAPICalled" && (m.params.type === "error" || m.params.type === "warning")) consoleLog.push(`${m.params.type}: ${m.params.args.map((a) => a.value ?? a.description ?? "").join(" ")}`);
  if (m.method === "Runtime.exceptionThrown") consoleLog.push(`exception: ${m.params.exceptionDetails.exception?.description ?? m.params.exceptionDetails.text}`);
  if (m.method === "Log.entryAdded" && m.params.entry.level !== "info" && m.params.entry.level !== "verbose") consoleLog.push(`${m.params.entry.level}: ${m.params.entry.text}`);
  if (m.method === "Network.responseReceived" && m.params.response.status >= 400) consoleLog.push(`http ${m.params.response.status}: ${m.params.response.url}`);
};
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
const evaluate = async (expression) => { const r = (await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true })).result; return r?.exceptionDetails ? "EXC: " + (r.exceptionDetails.exception?.description ?? r.exceptionDetails.text) : r?.result?.value; };

await send("Page.enable");
await send("Runtime.enable");
await send("Log.enable");
await send("Network.enable");
await send("Emulation.setDeviceMetricsOverride", { width: opt.w, height: opt.h, deviceScaleFactor: 1, mobile: opt.w < 600 });
await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", opt_scheme: opt.scheme, value: opt.scheme }] });
// Seed localStorage on the origin before the app boots.
const origin = new URL(opt.url).origin;
await send("Page.navigate", { url: origin + "/__blank__" });
await sleep(400);
for (const kv of opt.local) { const eq = kv.indexOf("="); const k = kv.slice(0, eq), v = kv.slice(eq + 1); await evaluate(`localStorage.setItem(${JSON.stringify(k)}, ${JSON.stringify(v)}); 1`); }
await send("Page.navigate", { url: opt.url });
await sleep(opt.wait);
for (const sel of opt.click) { await evaluate(`document.querySelector(${JSON.stringify(sel)})?.click(); 1`); await sleep(400); }
for (const kv of opt.type) { const eq = kv.indexOf("="); const sel = kv.slice(0, eq), text = kv.slice(eq + 1); await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(sel)}); if(!el) return 0; const setter=Object.getOwnPropertyDescriptor(el.__proto__,'value').set; setter.call(el, ${JSON.stringify(text)}); el.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`); }
// Ordered actions: --act click=SEL | type=SEL=TEXT | wait=MS
for (const spec of opt.act) {
  const eq = spec.indexOf("=");
  const kind = eq < 0 ? spec : spec.slice(0, eq); const rest = eq < 0 ? "" : spec.slice(eq + 1);
  if (kind === "click") { await evaluate(`document.querySelector(${JSON.stringify(rest)})?.click(); 1`); await sleep(500); }
  else if (kind === "type") { const eq = rest.indexOf("="); const sel = rest.slice(0, eq), text = rest.slice(eq + 1); await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(sel)}); if(!el) return 0; const setter=Object.getOwnPropertyDescriptor(el.__proto__,'value').set; setter.call(el, ${JSON.stringify(text)}); el.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`); await sleep(200); }
  else if (kind === "wait") { await sleep(Number(rest)); }
  else if (kind === "waitfor" || kind === "waitgone") {
    // waitfor=SEL[|timeoutMs]: poll until SEL exists (or, for waitgone, is absent).
    const [sel, to] = rest.split("|"); const deadline = Date.now() + Number(to ?? 90000); let ok = false;
    while (Date.now() < deadline) { const n = await evaluate(`document.querySelectorAll(${JSON.stringify(sel)}).length`); if ((kind === "waitfor") === (n > 0)) { ok = true; break; } await sleep(150); }
    console.log(`${kind} ${sel} -> ${ok ? "ok" : "TIMEOUT"} (${new Date().toISOString().slice(11, 19)})`);
  }
  else if (kind === "shot") { await snap(rest); }
  else if (kind === "burstif") {
    // burstif=SEL|PREFIX|N|MS: like burst, but only when SEL exists right now (no-op otherwise).
    const [sel, prefix, n, ms] = rest.split("|");
    const present = await evaluate(`document.querySelectorAll(${JSON.stringify(sel)}).length`);
    if (present > 0) { console.log(`burstif ${sel} -> recording ${prefix} (${new Date().toISOString().slice(11, 19)})`); for (let i = 0; i < Number(n); i++) { const t = Date.now(); await snap(`${prefix}-${String(i).padStart(2, "0")}.png`, true); const left = Number(ms) - (Date.now() - t); if (left > 0) await sleep(left); } }
  }
  else if (kind === "burst") {
    // burst=PREFIX|N|MS: N frames named PREFIX-00.png.. at MS intervals (best effort; a frame costs ~80 ms).
    const [prefix, n, ms] = rest.split("|");
    for (let i = 0; i < Number(n); i++) { const t = Date.now(); await snap(`${prefix}-${String(i).padStart(2, "0")}.png`, true); const left = Number(ms) - (Date.now() - t); if (left > 0) await sleep(left); }
  }
  else if (kind === "rec") {
    // rec=PREFIX[|MAXHOLDMS]: record the viewport until recstop. Frames land as PREFIX-0000.png
    // and PREFIX.txt lists "file<TAB>seconds"; a still longer than MAXHOLD is cut back to it,
    // which is how the LLM's thinking time disappears from the cut.
    const [prefix, hold] = rest.split("|");
    rec = { prefix, maxHold: Number(hold ?? 2000) / 1000, frames: [] };
    await send("Page.startScreencast", { format: "png", maxWidth: opt.w, maxHeight: opt.h, everyNthFrame: 1 });
    console.log(`rec ${prefix} (${new Date().toISOString().slice(11, 19)})`);
  }
  else if (kind === "recstop") {
    await send("Page.stopScreencast");
    await sleep(150); // let the last in-flight frame land
    const end = Date.now(); const r = rec; rec = null;
    const durs = r.frames.map((f, i) => Math.min(Math.max(((i + 1 < r.frames.length ? r.frames[i + 1].t : end) - f.t) / 1000, 1 / 60), r.maxHold));
    writeFileSync(`${r.prefix}.txt`, r.frames.map((f, i) => `${f.file}\t${durs[i].toFixed(3)}`).join("\n") + "\n");
    console.log(`recstop ${r.prefix}: ${r.frames.length} frames, ${durs.reduce((a, b) => a + b, 0).toFixed(1)}s cut (${((end - (r.frames[0]?.t ?? end)) / 1000).toFixed(1)}s real)`);
  }
  else if (kind === "typein") {
    // typein=SEL|MS|TEXT: fill SEL a chunk at a time, ~60 ticks whatever the length,
    // so a long answer still reads as someone typing instead of a paste.
    const i1 = rest.indexOf("|"), i2 = rest.indexOf("|", i1 + 1);
    const sel = rest.slice(0, i1), ms = Number(rest.slice(i1 + 1, i2)), text = rest.slice(i2 + 1);
    const step = Math.max(1, Math.ceil(text.length / 60));
    for (let n = step; n < text.length + step; n += step) {
      await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(sel)}); if(!el) return 0; const s=Object.getOwnPropertyDescriptor(el.__proto__,'value').set; s.call(el, ${JSON.stringify(text)}.slice(0,${Math.min(n, text.length)})); el.dispatchEvent(new Event('input',{bubbles:true})); return 1;})()`);
      await sleep(ms);
    }
  }
  else if (kind === "cursor") {
    // cursor=SEL: glide a stand-in pointer onto SEL, press it, then click. Headless has no
    // cursor, so without this every button in the recording fires with nothing touching it.
    console.log("cursor ->", await evaluate(`(()=>{const el=document.querySelector(${JSON.stringify(rest)}); if(!el) return 'no target'; const r=el.getBoundingClientRect();
      let d=document.getElementById('__demo_cursor'); if(!d){d=document.createElement('div'); d.id='__demo_cursor';
        d.style.cssText='position:fixed;left:50%;top:78%;z-index:99999;width:20px;height:20px;margin:-10px 0 0 -10px;border-radius:50%;background:rgba(30,30,30,.35);box-shadow:0 0 0 2px rgba(255,255,255,.9),0 2px 8px rgba(0,0,0,.35);pointer-events:none;transition:left .5s cubic-bezier(.33,0,.15,1),top .5s cubic-bezier(.33,0,.15,1),transform .12s ease';
        document.body.appendChild(d); d.offsetHeight;} d.style.transform='scale(1)'; d.style.left=(r.left+r.width/2)+'px'; d.style.top=(r.top+r.height/2)+'px'; return 'to '+Math.round(r.left)+','+Math.round(r.top);})()`));
    await sleep(620);
    await evaluate(`document.getElementById('__demo_cursor').style.transform='scale(.62)'; 1`);
    await sleep(140);
    await evaluate(`document.querySelector(${JSON.stringify(rest)})?.click(); 1`);
    await sleep(120);
    await evaluate(`document.getElementById('__demo_cursor').style.transform='scale(1)'; 1`);
    await sleep(240);
  }
  else if (kind === "scroll") { await evaluate(`window.scrollTo(0, ${rest === "bottom" ? "document.documentElement.scrollHeight" : Number(rest)}); 1`); await sleep(300); }
  else if (kind === "eval") { console.log("eval ->", await evaluate(rest)); }
}
if (opt.click.length || opt.type.length || opt.act.length) await sleep(600);
async function snap(file, quiet = false) {
  if (!quiet) await evaluate("document.fonts.ready.then(()=>1)");
  // --clip x,y,w,h: capture only that viewport region
  let clip = opt.clip ? (([x, y, width, height]) => ({ x, y, width, height, scale: 1 }))(opt.clip.split(",").map(Number)) : undefined;
  if (opt.full) {
    const height = await evaluate("Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)");
    await send("Emulation.setDeviceMetricsOverride", { width: opt.w, height, deviceScaleFactor: 1, mobile: opt.w < 600 });
    await sleep(250);
    clip = { x: 0, y: 0, width: opt.w, height, scale: 1 };
  }
  const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: !!opt.full, ...(clip ? { clip } : {}) });
  writeFileSync(file, Buffer.from(shot.result.data, "base64"));
  if (opt.full) await send("Emulation.setDeviceMetricsOverride", { width: opt.w, height: opt.h, deviceScaleFactor: 1, mobile: opt.w < 600 });
  if (quiet) return;
  const title = await evaluate("document.title + ' | ' + location.pathname");
  console.log(`${file} <- ${title} (${opt.w}x${opt.full ? clip.height : opt.h}, ${opt.scheme})`);
}
if (opt.out) await snap(opt.out);
const noise = consoleLog.filter((l) => !/React DevTools|vite\]|\[HMR\]/.test(l));
console.log(`console: ${noise.length ? "\n  " + noise.join("\n  ") : "clean"}`);
ws.close(); chrome.kill();
rmSync(profile, { recursive: true, force: true });
