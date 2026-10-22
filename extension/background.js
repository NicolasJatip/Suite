const OFFSCREEN_URL = "offscreen.html";
// El servidor reemplaza esta URL por SUITE_PUBLIC_URL al armar el zip.
const SERVER_URL = "http://localhost:8000";
const DEFAULT_STATE = {
  status: "idle",
  tabId: null,
  tabTitle: "",
  startedAt: null,
  message: "Listo para grabar"
};

async function getRecorderState() {
  const { recorderState } = await chrome.storage.session.get({ recorderState: DEFAULT_STATE });
  return { ...DEFAULT_STATE, ...recorderState };
}

async function setRecorderState(update) {
  const current = await getRecorderState();
  const next = { ...current, ...update };
  await chrome.storage.session.set({ recorderState: next });
  return next;
}

async function hasOffscreenDocument() {
  if (chrome.offscreen.hasDocument) {
    return chrome.offscreen.hasDocument();
  }
  const clientsList = await clients.matchAll();
  return clientsList.some((client) => client.url === chrome.runtime.getURL(OFFSCREEN_URL));
}

async function ensureOffscreenDocument() {
  if (await hasOffscreenDocument()) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_URL,
    reasons: ["USER_MEDIA"],
    justification: "Grabar el audio de la pestana activa aunque el popup se cierre."
  });
}

function getTabStreamId(tabId) {
  return new Promise((resolve, reject) => {
    chrome.tabCapture.getMediaStreamId({ targetTabId: tabId }, (streamId) => {
      if (chrome.runtime.lastError || !streamId) {
        reject(new Error(chrome.runtime.lastError?.message || "No se pudo capturar la pestana"));
        return;
      }
      resolve(streamId);
    });
  });
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    throw new Error("No se encontro una pestana activa");
  }
  return tab;
}

async function getSessionCookie() {
  const cookie = await chrome.cookies.get({
    url: SERVER_URL,
    name: "mini_portal_session"
  });
  return cookie?.value || null;
}

async function checkSession(sessionToken) {
  const response = await fetch(`${SERVER_URL}/api/extension/session`, {
    method: "GET",
    headers: {
      "X-Transcript-Session": sessionToken,
      "X-Transcript-Recorder": "1"
    }
  });
  return response.ok;
}

async function startRecording() {
  const state = await getRecorderState();
  if (state.status === "recording" || state.status === "uploading") {
    return state;
  }

  const sessionToken = await getSessionCookie();
  if (!sessionToken) {
    chrome.tabs.create({ url: `${SERVER_URL}/login` });
    return setRecorderState({ status: "idle", message: "Inicia sesion y vuelve a grabar" });
  }

  let valid = false;
  try {
    valid = await checkSession(sessionToken);
  } catch (_) {
    return setRecorderState({ status: "error", message: "No se pudo conectar al servidor" });
  }

  if (!valid) {
    chrome.tabs.create({ url: `${SERVER_URL}/login` });
    return setRecorderState({ status: "idle", message: "Sesion vencida. Inicia sesion de nuevo" });
  }

  const tab = await getActiveTab();
  const streamId = await getTabStreamId(tab.id);
  await ensureOffscreenDocument();

  const next = await setRecorderState({
    status: "recording",
    tabId: tab.id,
    tabTitle: tab.title || "Pestana sin titulo",
    tabUrl: tab.url || "",
    startedAt: new Date().toISOString(),
    message: "Grabando audio de la pestana"
  });

  const response = await chrome.runtime.sendMessage({
    target: "offscreen",
    type: "start-recording",
    streamId,
    tabId: tab.id,
    tabTitle: next.tabTitle,
    tabUrl: next.tabUrl,
    startedAt: next.startedAt,
    serverUrl: SERVER_URL,
    sessionToken
  });

  if (!response?.ok) {
    await setRecorderState({ status: "error", message: response?.error || "No se pudo iniciar la grabacion" });
    throw new Error(response?.error || "No se pudo iniciar la grabacion");
  }

  return getRecorderState();
}

async function stopRecording() {
  const state = await getRecorderState();
  if (state.status !== "recording") {
    return state;
  }

  await setRecorderState({ status: "uploading", message: "Preparando y subiendo audio" });
  const response = await chrome.runtime.sendMessage({ target: "offscreen", type: "stop-recording" });
  if (!response?.ok) {
    const message = response?.error || "No se pudo detener la grabacion";
    await setRecorderState({ status: "error", message });
    throw new Error(message);
  }

  return getRecorderState();
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.target === "offscreen") return false;

  if (message?.type === "get-status") {
    getRecorderState().then(sendResponse);
    return true;
  }

  if (message?.type === "start-recording") {
    startRecording().then(sendResponse).catch((error) => sendResponse({ status: "error", message: error.message }));
    return true;
  }

  if (message?.type === "stop-recording") {
    stopRecording().then(sendResponse).catch((error) => sendResponse({ status: "error", message: error.message }));
    return true;
  }

  if (message?.type === "recording-finished") {
    setRecorderState({
      status: "done",
      message: "Enviado. El servidor ya lo esta procesando",
      jobId: message.jobId || null,
      finishedAt: new Date().toISOString()
    }).then(sendResponse);
    return true;
  }

  if (message?.type === "recording-error") {
    setRecorderState({ status: "error", message: message.error || "Error de grabacion" }).then(sendResponse);
    return true;
  }

  return false;
});
