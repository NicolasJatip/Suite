const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#video-file");
const uploadState = document.querySelector("#upload-state");
const uploadLabel = document.querySelector("#upload-label");
const uploadPercent = document.querySelector("#upload-percent");
const uploadProgress = document.querySelector("#upload-progress");
const jobsEl = document.querySelector("#jobs");
const chunkSize = window.TRANSCRIP_CONFIG.chunkSize;

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

function setUploadProgress(percent, label) {
  uploadState.classList.remove("hidden");
  uploadProgress.value = percent;
  uploadPercent.textContent = `${Math.floor(percent)}%`;
  uploadLabel.textContent = label;
}

async function postForm(url, fields) {
  const body = new FormData();
  Object.entries(fields).forEach(([key, value]) => body.append(key, value));
  const response = await fetch(url, { method: "POST", body });
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

async function uploadFile(file) {
  const resumeKey = `transcrip:${file.name}:${file.size}:${file.lastModified}`;
  const uploadId = localStorage.getItem(resumeKey) || crypto.randomUUID();
  localStorage.setItem(resumeKey, uploadId);
  const chunkCount = Math.ceil(file.size / chunkSize);
  let uploaded = new Set();
  try {
    const statusResponse = await fetch(`/api/uploads/${uploadId}`);
    if (statusResponse.ok) {
      const status = await statusResponse.json();
      uploaded = new Set(status.chunks);
    }
  } catch (_) {}

  for (let index = 0; index < chunkCount; index += 1) {
    if (uploaded.has(index)) {
      setUploadProgress(((index + 1) / chunkCount) * 95, `Bloque ${index + 1} ya estaba cargado`);
      continue;
    }
    const start = index * chunkSize;
    const end = Math.min(start + chunkSize, file.size);
    const blob = file.slice(start, end);
    await postForm("/api/uploads/chunk", {
      upload_id: uploadId,
      filename: file.name,
      chunk_index: index,
      chunk_count: chunkCount,
      total_size: file.size,
      file: new File([blob], file.name)
    });
    setUploadProgress(((index + 1) / chunkCount) * 95, `Subiendo bloque ${index + 1} de ${chunkCount}`);
  }

  setUploadProgress(98, "Cerrando carga");
  const result = await postForm("/api/uploads/complete", {
    upload_id: uploadId,
    filename: file.name,
    chunk_count: chunkCount,
    total_size: file.size
  });
  localStorage.removeItem(resumeKey);
  setUploadProgress(100, "Trabajo enviado a la cola");
  return result;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    await uploadFile(file);
    fileInput.value = "";
    await refreshJobs();
  } catch (error) {
    setUploadProgress(0, `Error: ${error.message}`);
  } finally {
    button.disabled = false;
  }
});

jobsEl.addEventListener("click", async (event) => {
  const deleteBtn = event.target.closest("[data-delete-job]");
  if (deleteBtn) {
    const jobId = deleteBtn.dataset.deleteJob;
    const jobName = deleteBtn.dataset.jobName || "este trabajo";
    const confirmed = window.confirm(
      `Eliminar "${jobName}"?\n\nEsto borra el trabajo del historial y elimina sus archivos del servidor.`
    );
    if (!confirmed) return;
    deleteBtn.disabled = true;
    deleteBtn.textContent = "Eliminando";
    try {
      const response = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
      if (!response.ok) {
        let detail = response.statusText;
        try { detail = (await response.json()).detail || detail; } catch (_) {}
        throw new Error(detail);
      }
      await refreshJobs();
    } catch (error) {
      window.alert(`No se pudo eliminar: ${error.message}`);
      deleteBtn.disabled = false;
      deleteBtn.textContent = "Eliminar";
    }
    return;
  }

  const gdocBtn = event.target.closest("[data-gdoc]");
  if (!gdocBtn) return;
  const jobId = gdocBtn.dataset.gdoc;
  const originalText = gdocBtn.textContent;
  gdocBtn.disabled = true;
  gdocBtn.textContent = "Creando…";
  try {
    const response = await fetch(`/api/jobs/${jobId}/gdoc`);
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const data = await response.json();
    window.open(data.url, "_blank");
  } catch (error) {
    window.alert(`No se pudo crear el Google Doc: ${error.message}`);
  } finally {
    gdocBtn.disabled = false;
    gdocBtn.textContent = originalText;
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
    <button class="danger-action" type="button" data-delete-job="${job.id}" data-job-name="${name}">
      Eliminar
    </button>
  `;
  return `
    <article class="job">
      <div class="job-head">
        <div>
          <div class="job-title">${name}</div>
          <div class="job-meta">${humanSize(job.size_bytes)} · ${message}</div>
        </div>
        <span class="status ${job.status}">${jobStatusLabel(job.status)}</span>
      </div>
      <progress max="100" value="${job.progress || 0}"></progress>
      ${error}
      ${canDownload ? `
        <div class="downloads">
          ${window.SUITE_GOOGLE_DOCS ? `<button class="secondary" data-gdoc="${job.id}">Abrir en Google Doc</button>` : ""}
          <a class="secondary" href="/download/${job.id}/srt">Descargar SRT</a>
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
  const response = await fetch("/api/jobs");
  if (!response.ok) return;
  const jobs = await response.json();
  jobsEl.innerHTML = jobs.length
    ? jobs.map(renderJob).join("")
    : `<p class="muted-text">Todavía no hay trabajos.</p>`;
}

refreshJobs();
setInterval(refreshJobs, 4000);

(function detectExtension() {
  if (window.__transcripRecorderInstalled) return;
  const btn = document.querySelector("#ext-download-btn");
  if (btn) btn.classList.remove("hidden");
  const help = document.querySelector("#ext-install");
  if (help) help.classList.remove("hidden");
})();
