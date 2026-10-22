const MAX_RECORDING_MS = 4 * 60 * 60 * 1000;

let recorder = null;
let stream = null;
let audioContext = null;
let chunks = [];
let activeMeta = null;
let activeOptions = null;
let maxTimer = null;
let stopPromise = null;

function sanitizeName(value) {
  return String(value || "Meet")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Za-z0-9._-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 120) || "Meet";
}

function localTimestamp(date = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate())
  ].join("-") + "_" + [pad(date.getHours()), pad(date.getMinutes()), pad(date.getSeconds())].join("-");
}

function chooseMimeType() {
  if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {
    return "audio/webm;codecs=opus";
  }
  if (MediaRecorder.isTypeSupported("audio/webm")) {
    return "audio/webm";
  }
  return "";
}

function stopTracks() {
  if (maxTimer) {
    clearTimeout(maxTimer);
    maxTimer = null;
  }
  if (stream) {
    stream.getTracks().forEach((track) => track.stop());
    stream = null;
  }
  if (audioContext) {
    audioContext.close().catch(() => {});
    audioContext = null;
  }
}

async function uploadRecording(blob) {
  const serverUrl = String(activeOptions?.serverUrl || "").replace(/\/+$/, "");
  if (!serverUrl) {
    throw new Error("Falta configurar el servidor de Transcrip");
  }
  const sessionToken = String(activeOptions?.sessionToken || "").trim();
  if (!sessionToken) {
    throw new Error("Sin sesion activa. Inicia sesion en la web primero");
  }

  const recordedAt = localTimestamp(new Date());
  const title = `Meet - ${activeMeta?.tabTitle || "Pestana"} - ${recordedAt}`;
  const filename = `${sanitizeName(title)}.webm`;
  const form = new FormData();
  form.append("title", title);
  form.append("source_url", activeMeta?.tabUrl || "");
  form.append("recorded_at", recordedAt);
  form.append("file", blob, filename);

  const response = await fetch(`${serverUrl}/api/extension/recordings`, {
    method: "POST",
    headers: {
      "X-Transcript-Session": sessionToken,
      "X-Transcript-Recorder": "1"
    },
    body: form
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (_) {}
    throw new Error(`Servidor rechazo la grabacion: ${detail}`);
  }

  return response.json();
}

async function startRecording(message) {
  if (recorder && recorder.state !== "inactive") {
    throw new Error("Ya hay una grabacion activa");
  }

  chunks = [];
  activeMeta = {
    tabId: message.tabId,
    tabTitle: message.tabTitle,
    tabUrl: message.tabUrl,
    startedAt: message.startedAt
  };
  activeOptions = {
    serverUrl: message.serverUrl,
    sessionToken: message.sessionToken
  };

  stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      mandatory: {
        chromeMediaSource: "tab",
        chromeMediaSourceId: message.streamId
      }
    },
    video: false
  });

  audioContext = new AudioContext();
  audioContext.createMediaStreamSource(stream).connect(audioContext.destination);

  const mimeType = chooseMimeType();
  recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  recorder.ondataavailable = (event) => {
    if (event.data?.size) chunks.push(event.data);
  };
  recorder.start(1000);

  maxTimer = setTimeout(() => {
    stopRecording().catch((error) => {
      chrome.runtime.sendMessage({ type: "recording-error", error: error.message });
    });
  }, MAX_RECORDING_MS);
}

function stopRecording() {
  if (!recorder || recorder.state === "inactive") {
    return Promise.resolve();
  }
  if (stopPromise) return stopPromise;

  stopPromise = new Promise((resolve, reject) => {
    recorder.onstop = async () => {
      try {
        stopTracks();
        const mimeType = recorder.mimeType || "audio/webm";
        const blob = new Blob(chunks, { type: mimeType });
        if (!blob.size) {
          throw new Error("La grabacion quedo vacia");
        }
        const result = await uploadRecording(blob);
        await chrome.runtime.sendMessage({ type: "recording-finished", jobId: result.job_id });
        resolve(result);
      } catch (error) {
        await chrome.runtime.sendMessage({ type: "recording-error", error: error.message });
        reject(error);
      } finally {
        recorder = null;
        chunks = [];
        activeMeta = null;
        activeOptions = null;
        stopPromise = null;
      }
    };
    recorder.stop();
  });

  return stopPromise;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.target !== "offscreen") return false;

  if (message.type === "start-recording") {
    startRecording(message)
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  if (message.type === "stop-recording") {
    stopRecording()
      .then((result) => sendResponse({ ok: true, result }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  return false;
});
