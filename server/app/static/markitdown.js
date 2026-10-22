const form = document.querySelector("#markitdown-form");
const fileInput = document.querySelector("#markitdown-file");
const dropZone = document.querySelector("#drop-zone");
const state = document.querySelector("#markitdown-state");
const label = document.querySelector("#markitdown-label");
const percent = document.querySelector("#markitdown-percent");
const progress = document.querySelector("#markitdown-progress");
const jobsEl = document.querySelector("#markitdown-jobs");

function humanSize(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Number(bytes);
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function setProgress(value, text) {
  state.classList.remove("hidden");
  progress.value = value;
  percent.textContent = `${Math.floor(value)}%`;
  label.textContent = text;
}

async function postFile(file) {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/api/markitdown/jobs", { method: "POST", body });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
    } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    setProgress(15, "Subiendo archivo");
    await postFile(file);
    setProgress(100, "Conversion enviada");
    fileInput.value = "";
    await refreshJobs();
  } catch (error) {
    setProgress(0, `Error: ${error.message}`);
  } finally {
    button.disabled = false;
  }
});

dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("dragging");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("dragging");
});

dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
  const [file] = event.dataTransfer.files;
  if (!file) return;
  const transfer = new DataTransfer();
  transfer.items.add(file);
  fileInput.files = transfer.files;
});

jobsEl.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-markitdown-job]");
  if (!button) return;

  const jobId = button.dataset.deleteMarkitdownJob;
  const jobName = button.dataset.jobName || "este trabajo";
  const confirmed = window.confirm(
    `Eliminar "${jobName}"?\n\nEsto borra la conversion del historial y elimina sus archivos del servidor.`
  );
  if (!confirmed) return;

  button.disabled = true;
  button.textContent = "Eliminando";
  try {
    const response = await fetch(`/api/markitdown/jobs/${jobId}`, { method: "DELETE" });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    await refreshJobs();
  } catch (error) {
    window.alert(`No se pudo eliminar: ${error.message}`);
    button.disabled = false;
    button.textContent = "Eliminar";
  }
});

function jobStatusLabel(status) {
  return {
    queued: "En cola",
    processing: "Procesando",
    done: "Listo",
    failed: "Error"
  }[status] || status;
}

function renderJob(job) {
  const name = escapeHtml(job.original_name);
  const message = escapeHtml(job.message || "");
  const errorText = escapeHtml(job.error || "");
  const canDownload = job.status === "done";
  const error = job.error ? `<div class="alert">${errorText}</div>` : "";
  const deleteButton = `
    <button class="danger-action" type="button" data-delete-markitdown-job="${job.id}" data-job-name="${name}">
      Eliminar
    </button>
  `;

  return `
    <article class="job">
      <div class="job-head">
        <div>
          <div class="job-title">${name}</div>
          <div class="job-meta">${humanSize(job.size_bytes)} - ${message}</div>
        </div>
        <span class="status ${job.status}">${jobStatusLabel(job.status)}</span>
      </div>
      <progress max="100" value="${job.progress || 0}"></progress>
      ${error}
      ${canDownload ? `
        <div class="downloads">
          <a class="secondary" href="/download/markitdown/${job.id}">Descargar MD</a>
          ${deleteButton}
        </div>
      ` : `<div class="downloads">${deleteButton}</div>`}
    </article>
  `;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char]));
}

async function refreshJobs() {
  const response = await fetch("/api/markitdown/jobs");
  if (!response.ok) return;
  const jobs = await response.json();
  jobsEl.innerHTML = jobs.length
    ? jobs.map(renderJob).join("")
    : `<p class="muted-text">Todavia no hay conversiones.</p>`;
}

refreshJobs();
setInterval(refreshJobs, 3000);
