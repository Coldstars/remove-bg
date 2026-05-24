const state = {
  mode: "image",
  files: [],
  fileUrls: new Map(),
  jobs: [],
  activeJobId: null,
  selectedKey: null,
  selectedObjectUrl: null,
};

const els = {
  tabs: document.querySelectorAll(".tab"),
  fileInput: document.getElementById("fileInput"),
  pickFiles: document.getElementById("pickFiles"),
  submitJob: document.getElementById("submitJob"),
  openOutput: document.getElementById("openOutput"),
  dropZone: document.getElementById("dropZone"),
  dropHint: document.getElementById("dropHint"),
  previewEmpty: document.getElementById("previewEmpty"),
  previewImage: document.getElementById("previewImage"),
  previewVideo: document.getElementById("previewVideo"),
  processingOverlay: document.getElementById("processingOverlay"),
  statusLine: document.getElementById("statusLine"),
  queue: document.getElementById("queue"),
  queueTitle: document.getElementById("queueTitle"),
  paramsPanel: document.getElementById("paramsPanel"),
};

function setMode(mode) {
  state.mode = mode;
  clearPendingFiles();
  state.activeJobId = null;
  state.selectedKey = null;
  els.tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.mode === mode));
  els.paramsPanel.classList.toggle("hidden", mode !== "video");
  els.queueTitle.textContent = mode === "image" ? "图片列表" : "任务队列";
  els.fileInput.multiple = mode === "image";
  els.fileInput.accept = mode === "image" ? ".png,.jpg,.jpeg,.webp" : ".mp4,.mov,.m4v,.webm,.avi,.mkv";
  els.dropHint.textContent = mode === "image" ? "支持 png / jpg / jpeg / webp，可多选" : "支持 10 秒以内 mp4 / mov / webm / avi / mkv";
  clearPreview();
  setStatus("等待文件");
  renderQueue();
}

function setStatus(text) {
  els.statusLine.textContent = text;
}

function setOverlay(visible, text = "处理中") {
  els.processingOverlay.classList.toggle("hidden", !visible);
  els.processingOverlay.querySelector("span:last-child").textContent = text;
}

function clearPreview() {
  state.selectedObjectUrl = null;
  els.previewImage.classList.add("hidden");
  els.previewVideo.classList.add("hidden");
  els.previewEmpty.classList.remove("hidden");
  els.previewImage.removeAttribute("src");
  els.previewVideo.removeAttribute("src");
  setOverlay(false);
}

function showFiles(files) {
  const incoming = Array.from(files);
  if (state.mode === "image") {
    const existing = new Set(state.files.map((file) => fileKey(file)));
    incoming.forEach((file) => {
      const key = fileKey(file);
      if (!existing.has(key)) {
        state.files.push(file);
        existing.add(key);
      }
    });
  } else {
    clearPendingFiles();
    state.files = incoming.slice(0, 1);
  }
  state.activeJobId = null;
  state.selectedKey = state.selectedKey || state.files[0]?.name || null;
  if (!state.files.length) {
    clearPreview();
    setStatus("等待文件");
  } else {
    const selected = state.files.find((file) => file.name === state.selectedKey) || state.files[0];
    showSelectedFile(selected);
    setStatus(`${state.files.length} 个文件已选择`);
  }
  renderQueue();
}

function showSelectedFile(file) {
  clearPreview();
  if (!file) return;
  state.selectedKey = file.name;
  state.selectedObjectUrl = objectUrlFor(file);
  els.previewEmpty.classList.add("hidden");
  if (state.mode === "video") {
    els.previewVideo.src = state.selectedObjectUrl;
    els.previewVideo.classList.remove("hidden");
  } else {
    els.previewImage.src = state.selectedObjectUrl;
    els.previewImage.classList.remove("hidden");
  }
}

function objectUrlFor(file) {
  const key = fileKey(file);
  if (!state.fileUrls.has(key)) {
    state.fileUrls.set(key, URL.createObjectURL(file));
  }
  return state.fileUrls.get(key);
}

function fileKey(file) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function clearPendingFiles() {
  state.fileUrls.forEach((url) => URL.revokeObjectURL(url));
  state.fileUrls.clear();
  state.files = [];
}

function removePendingFile(file) {
  const key = fileKey(file);
  const url = state.fileUrls.get(key);
  if (url) URL.revokeObjectURL(url);
  state.fileUrls.delete(key);
  state.files = state.files.filter((item) => fileKey(item) !== key);
  if (state.selectedKey === file.name) {
    state.selectedKey = state.files[0]?.name || null;
    if (state.files[0]) showSelectedFile(state.files[0]);
    else clearPreview();
  }
  setStatus(state.files.length ? `${state.files.length} 个文件已选择` : "等待文件");
  renderQueue();
}

function showUrl(url, isVideo = false, overlay = false) {
  clearPreview();
  els.previewEmpty.classList.add("hidden");
  if (isVideo) {
    els.previewVideo.src = url;
    els.previewVideo.classList.remove("hidden");
  } else {
    els.previewImage.src = url;
    els.previewImage.classList.remove("hidden");
  }
  setOverlay(overlay);
}

function formValue(id) {
  return document.getElementById(id).value;
}

async function submitJob() {
  if (!state.files.length) {
    setStatus("请先选择文件");
    return;
  }
  const form = new FormData();
  state.files.forEach((file) => form.append("files", file));
  form.append("edge_mode", "auto");
  form.append("quality", "clean");
  if (state.mode === "video") {
    form.append("fps", formValue("fps"));
    form.append("max_side", formValue("maxSide"));
    form.append("workers", formValue("workers"));
    form.append("alpha_smooth", formValue("alphaSmooth"));
    document.querySelectorAll('input[name="formats"]:checked').forEach((item) => form.append("formats", item.value));
  }
  els.submitJob.disabled = true;
  setStatus("提交任务中");
  try {
    const response = await fetch(`/api/jobs/${state.mode}`, { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "提交失败");
    state.activeJobId = data.id;
    state.selectedKey = data.inputs?.[0]?.name || null;
    clearPendingFiles();
    await refreshJobs();
    const job = state.jobs.find((item) => item.id === data.id);
    if (job) selectItem(job, job.inputs?.[0]?.name);
  } catch (error) {
    setStatus(error.message);
  } finally {
    els.submitJob.disabled = false;
  }
}

async function refreshJobs() {
  const response = await fetch("/api/jobs");
  state.jobs = await response.json();
  renderQueue();
  refreshSelectedPreview();
}

function renderQueue() {
  els.queue.innerHTML = "";
  if (state.files.length) {
    state.files.forEach((file) => {
      els.queue.appendChild(createQueueItem({
        key: file.name,
        title: file.name,
        subtitle: "待处理",
        status: "pending-delete",
        thumbUrl: objectUrlFor(file),
        active: state.selectedKey === file.name,
        onClick: () => showSelectedFile(file),
        onRemove: () => removePendingFile(file),
      }));
    });
    return;
  }

  const visibleJobs = state.jobs.filter((job) => state.mode === "image" ? job.type === "image" : job.type === "video");
  if (!visibleJobs.length) {
    els.queue.innerHTML = '<div class="empty-list">暂无任务</div>';
    return;
  }

  visibleJobs.forEach((job) => {
    if (job.type === "image") {
      imageRows(job).forEach((row) => els.queue.appendChild(createQueueItem(row)));
    } else {
      els.queue.appendChild(createQueueItem(videoRow(job)));
    }
  });
}

function imageRows(job) {
  const outputs = new Map((job.outputs || []).map((output) => [stem(output.name), output]));
  const runningIndex = firstPendingIndex(job, outputs);
  return (job.inputs || []).map((input, index) => {
    const output = outputs.get(stem(input.name));
    const done = Boolean(output);
    const processing = job.status === "running" && !done && index === runningIndex;
    const failed = job.status === "failed" && !done;
    const status = done ? "done" : processing ? "running" : failed ? "failed" : "queued";
    const subtitle = done ? (output.size || "处理完成") : processing ? "处理中..." : failed ? "处理失败" : "待处理";
    return {
      key: `${job.id}:${input.name}`,
      title: input.name,
      subtitle,
      status,
      thumbUrl: output?.url || input.url,
      active: state.selectedKey === `${job.id}:${input.name}`,
      onClick: () => selectItem(job, input.name),
    };
  });
}

function videoRow(job) {
  const input = job.inputs?.[0] || {};
  const firstOutput = (job.outputs || []).find((item) => /\.(png|webp|apng)$/i.test(item.name));
  return {
    key: job.id,
    title: input.name || job.id,
    subtitle: job.status === "done" ? `${job.outputs.length} 个输出` : job.error || job.message || statusFor(job.status),
    status: job.status,
    thumbUrl: firstOutput?.url || input.url,
    active: state.selectedKey === job.id,
    onClick: () => selectItem(job),
  };
}

function createQueueItem(row) {
  const item = document.createElement("article");
  item.className = `queue-item ${row.active ? "active" : ""}`;
  item.addEventListener("click", row.onClick);
  const removeButton = row.onRemove
    ? `<button class="remove-file" title="删除" aria-label="删除">×</button>`
    : `<div class="state-icon ${row.status}">${iconFor(row.status)}</div>`;
  item.innerHTML = `
    <img class="queue-thumb" src="${row.thumbUrl}" alt="" />
    <div class="queue-copy">
      <div class="queue-name">${escapeHtml(row.title)}</div>
      <div class="queue-state">${escapeHtml(row.subtitle)}</div>
    </div>
    ${removeButton}
  `;
  const button = item.querySelector(".remove-file");
  if (button) {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      row.onRemove();
    });
  }
  return item;
}

function selectItem(job, inputName = null) {
  state.activeJobId = job.id;
  els.openOutput.disabled = job.status !== "done";
  if (job.type === "image" && inputName) {
    state.selectedKey = `${job.id}:${inputName}`;
    const output = (job.outputs || []).find((item) => stem(item.name) === stem(inputName));
    const input = (job.inputs || []).find((item) => item.name === inputName);
    const processing = job.status === "running" && !output;
    showUrl((output || input)?.url, false, processing);
    setStatus(output ? "处理完成" : job.error || job.message || statusFor(job.status));
  } else {
    state.selectedKey = job.id;
    const output = (job.outputs || []).find((item) => /\.(png|webp|apng)$/i.test(item.name));
    const input = job.inputs?.[0];
    showUrl((output || input)?.url, job.type === "video" && !output, job.status === "running");
    setStatus(job.error || job.message || statusFor(job.status));
  }
  renderQueue();
}

function refreshSelectedPreview() {
  if (!state.selectedKey || state.files.length) return;
  for (const job of state.jobs) {
    if (job.type === "image") {
      for (const input of job.inputs || []) {
        if (state.selectedKey === `${job.id}:${input.name}`) {
          selectItem(job, input.name);
          return;
        }
      }
    } else if (state.selectedKey === job.id) {
      selectItem(job);
      return;
    }
  }
}

async function openOutput() {
  if (!state.activeJobId) return;
  await fetch(`/api/open-output/${state.activeJobId}`, { method: "POST" });
}

function firstPendingIndex(job, outputs) {
  const inputs = job.inputs || [];
  const index = inputs.findIndex((input) => !outputs.has(stem(input.name)));
  return index < 0 ? inputs.length : index;
}

function stem(name) {
  return name.replace(/\.[^.]+$/, "");
}

function statusFor(status) {
  return { queued: "待处理", running: "处理中", done: "处理完成", failed: "处理失败" }[status] || status;
}

function iconFor(status) {
  if (status === "done") return "✓";
  if (status === "running") return "";
  if (status === "failed") return "!";
  return "";
}

function escapeHtml(value) {
  return String(value || "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

els.tabs.forEach((tab) => tab.addEventListener("click", () => setMode(tab.dataset.mode)));
els.pickFiles.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", () => showFiles(els.fileInput.files));
els.submitJob.addEventListener("click", submitJob);
els.openOutput.addEventListener("click", openOutput);

["dragenter", "dragover"].forEach((eventName) => {
  els.dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    els.dropZone.classList.add("drag");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  els.dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    els.dropZone.classList.remove("drag");
  });
});

els.dropZone.addEventListener("drop", (event) => showFiles(event.dataTransfer.files));

setMode("image");
refreshJobs();
setInterval(refreshJobs, 1500);
