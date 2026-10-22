const statusEl = document.querySelector("#status");
const messageEl = document.querySelector("#message");
const toggleButton = document.querySelector("#toggle");

const LABELS = {
  idle: "Listo",
  recording: "Grabando",
  uploading: "Subiendo",
  done: "Enviado",
  error: "Error"
};

function send(message) {
  return chrome.runtime.sendMessage(message);
}

function paint(state) {
  const status = state?.status || "idle";
  statusEl.textContent = LABELS[status] || status;
  messageEl.textContent = state?.message || "Audio de la pestana activa";
  toggleButton.textContent = status === "recording" ? "Detener" : "Grabar";
  toggleButton.classList.toggle("recording", status === "recording");
  toggleButton.disabled = status === "uploading";
}

async function refresh() {
  const state = await send({ type: "get-status" });
  paint(state);
}

toggleButton.addEventListener("click", async () => {
  toggleButton.disabled = true;
  try {
    const current = await send({ type: "get-status" });
    const next = current?.status === "recording"
      ? await send({ type: "stop-recording" })
      : await send({ type: "start-recording" });
    paint(next);
  } catch (error) {
    paint({ status: "error", message: error.message });
  } finally {
    await refresh();
  }
});

refresh();
setInterval(refresh, 1000);
