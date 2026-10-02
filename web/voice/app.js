/* آلي الصوتي — mic -> PCM WebSocket -> events + playback (AR/HE/EN) */
"use strict";

const WS_URL = (location.protocol === "https:" ? "wss://" : "ws://") + location.hostname + ":5080";
const SR_IN = 16000, SR_OUT = 24000;

const $ = (id) => document.getElementById(id);
const connPill = $("conn"), langPill = $("lang"), enginePill = $("engine");
const micBtn = $("mic"), hint = $("hint"), levelbar = $("levelbar");
const log = $("log"), latencyEl = $("latency");

let ws = null, mySid = null;
let audioCtx = null, playQueue = [], playing = false;
let turnStart = 0, gotAudioThisTurn = false;

// ---------- logging UI ----------
function addMsg(cls, text, meta) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  if (meta) {
    const m = document.createElement("span");
    m.className = "meta"; m.textContent = meta;
    div.appendChild(m);
  }
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

// ---------- websocket ----------
function connect() {
  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { connPill.textContent = "متصل"; connPill.className = "pill on"; };
  ws.onclose = () => {
    connPill.textContent = "غير متصل"; connPill.className = "pill off";
    setTimeout(connect, 2000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) { playPCM(ev.data); return; }
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    handle(msg);
  };
}

function send(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

function handle(m) {
  switch (m.type) {
    case "hello": mySid = m.sid; break;
    case "transcript": break;
    case "reply":
      addMsg("aali", m.text, m.lang ? `العقل: ${m.lang}` : "");
      break;
    case "chunk_start":
      enginePill.textContent = m.engine || "—";
      if (m.lang) langPill.textContent = m.lang;
      break;
    case "turn_done":
      if (!gotAudioThisTurn) addMsg("err", "ما في صوت رجع من المحرك — شوف سجل الخادم");
      latencyEl.textContent = turnStart ? `زمن الدورة: ${((performance.now() - turnStart) / 1000).toFixed(1)}s` : "—";
      gotAudioThisTurn = false; turnStart = 0;
      micBtn.disabled = false; hint.textContent = "اضغط للتحدث";
      break;
    case "barge_in": addMsg("err", "⏹ توقفت — أسمعك"); break;
    case "status":
      if (m.tts_engine) {
        enginePill.textContent = m.tts_engine.xtts ? "xtts" : (m.tts_engine.piper ? "piper" : "—");
      }
      break;
    case "error": addMsg("err", "⚠ " + (m.message || "خطأ")); break;
  }
}

// ---------- playback (24k int16 mono) ----------
function ensureCtx() {
  if (!audioCtx) audioCtx = new AudioContext({ sampleRate: SR_OUT });
  return audioCtx;
}

function playPCM(buf) {
  gotAudioThisTurn = true;
  playQueue.push(new Int16Array(buf));
  if (!playing) drainQueue();
}

function drainQueue() {
  const next = playQueue.shift();
  if (!next) { playing = false; return; }
  playing = true;
  const ctx = ensureCtx();
  const frames = next.length;
  const buffer = ctx.createBuffer(1, frames, SR_OUT);
  const ch = buffer.getChannelData(0);
  for (let i = 0; i < frames; i++) ch[i] = next[i] / 32768;
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  src.onended = drainQueue;
  src.connect(ctx.destination);
  src.start();
}

// ---------- mic capture (16k int16 mono) ----------
let mediaStream = null, processor = null, source = null;

async function startMic() {
  try { await ensureCtx().resume(); } catch {}
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
  });
  audioCtx = ensureCtx();
  source = audioCtx.createMediaStreamSource(mediaStream);
  processor = audioCtx.createScriptProcessor(4096, 1, 1);
  processor.onaudioprocess = (e) => {
    if (!ws || ws.readyState !== 1) return;
    const f32 = e.inputBuffer.getChannelData(0);
    // linear resample ctx.sampleRate -> 16k
    const ratio = ctxSR() / SR_IN;
    const n = Math.max(1, Math.floor(f32.length / ratio));
    const out = new Int16Array(n);
    let peak = 0;
    for (let i = 0; i < n; i++) {
      const v = Math.max(-1, Math.min(1, f32[Math.floor(i * ratio)]));
      const s = v * 32767;
      out[i] = s < 0 ? s : s; // int16
      const a = Math.abs(v); if (a > peak) peak = a;
    }
    levelbar.style.width = Math.min(100, peak * 140) + "%";
    ws.send(out.buffer);
  };
  source.connect(processor);
  processor.connect(audioCtx.destination); // ScriptProcessor needs a destination
  micBtn.classList.add("rec");
  hint.textContent = "أسمعك… تحدث واضغط مرة أخرى للتوقف";
  micBtn.setAttribute("aria-pressed", "true");
}

function ctxSR() { return audioCtx ? audioCtx.sampleRate : 48000; }

function stopMic() {
  try { if (processor) processor.disconnect(); } catch {}
  try { if (source) source.disconnect(); } catch {}
  try { if (mediaStream) mediaStream.getTracks().forEach(t => t.stop()); } catch {}
  processor = null; source = null; mediaStream = null;
  levelbar.style.width = "0%";
  micBtn.classList.remove("rec");
  micBtn.setAttribute("aria-pressed", "false");
}

micBtn.addEventListener("click", async () => {
  if (micBtn.classList.contains("rec")) { stopMic(); hint.textContent = "اضغط للتحدث"; return; }
  turnStart = performance.now();
  micBtn.disabled = false;
  try { await startMic(); } catch (err) {
    addMsg("err", "ما قدرت أفتح المايك: " + err.message);
  }
});

// init
connect();
send({ type: "ping" });
setTimeout(() => send({ type: "status" }), 500);
