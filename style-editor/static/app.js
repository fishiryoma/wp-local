let currentPostId = null;
let currentPosts = [];
let pendingRedoNodeIndex = null;

const el = (id) => document.getElementById(id);

async function fetchPosts(q) {
  const res = await fetch(`/api/posts?q=${encodeURIComponent(q || "")}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    el("post-list").innerHTML = `<div class="warn-box">${err.error || "讀取文章清單失敗"}</div>`;
    return;
  }
  currentPosts = await res.json();
  renderPostList();
}

function renderPostList() {
  const list = el("post-list");
  list.innerHTML = "";
  for (const p of currentPosts) {
    const div = document.createElement("div");
    div.className = "post-item" + (p.id === currentPostId ? " active" : "");
    div.innerHTML = `${escapeHtml(p.title)} ${p.has_session ? '<span class="dot">●</span>' : ""}
      <div class="meta">${p.status} ・ ${p.modified}</div>`;
    div.onclick = () => selectPost(p.id);
    list.appendChild(div);
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

async function selectPost(postId) {
  currentPostId = postId;
  renderPostList();
  el("empty-state").style.display = "none";
  el("editor").style.display = "block";
  el("warnings").innerHTML = "";
  el("applied-banner").style.display = "none";

  const post = currentPosts.find((p) => p.id === postId);
  el("post-title").textContent = post ? post.title : "";
  el("review-content").innerHTML = "";
  el("summary").textContent = "";
  el("btn-apply").disabled = true;
  el("title-panel").style.display = "none";

  const res = await fetch(`/api/posts/${postId}/review`);
  if (res.ok) {
    const data = await res.json();
    renderReview(data);
  } else {
    el("review-content").innerHTML = '<p style="color:#999">尚未產生校對建議，請按上面「開始 / 重新 AI 校對」。</p>';
  }
}

function renderReview(data) {
  el("post-title").textContent = data.title;
  el("review-content").innerHTML = data.review_html;
  updateSummary(data.summary);
  el("btn-apply").disabled = data.applied;

  if (data.warnings && data.warnings.length) {
    el("warnings").innerHTML = data.warnings.map((w) => `<div>${escapeHtml(w)}</div>`).join("");
  } else {
    el("warnings").innerHTML = "";
  }

  if (data.applied) {
    el("applied-banner").style.display = "block";
    el("applied-banner").textContent = "✅ 已套用並覆蓋原文。若要再次修改，請重新按「開始 / 重新 AI 校對」。";
  } else {
    el("applied-banner").style.display = "none";
  }

  renderTitlePanel(data.title, data.title_check, data.applied);
}

function renderTitlePanel(originalTitle, titleCheck, applied) {
  const panel = el("title-panel");
  if (!titleCheck) {
    panel.style.display = "none";
    return;
  }

  const selected = titleCheck.selected || originalTitle;
  const options = [{ label: "維持原標題", value: originalTitle, tag: "" }];
  (titleCheck.suggestions || []).forEach((s, i) => {
    options.push({ label: s, value: s, tag: `建議 ${i + 1}` });
  });

  const noteClass = titleCheck.grammar_ok === false ? "bad" : "ok";
  const noteText = titleCheck.note || (titleCheck.grammar_ok === false ? "標題文法可能有問題" : "標題文法上沒有問題");

  let html = `<div class="tp-title-note ${noteClass}">📝 ${escapeHtml(noteText)}</div>`;

  if (options.length === 1) {
    html += `<div style="color:#999">目前標題已經很好，沒有更好的建議。</div>`;
  } else {
    html += `<div>`;
    for (const opt of options) {
      const checked = opt.value === selected ? "checked" : "";
      html += `
        <label class="title-option">
          <input type="radio" name="title-choice" value="${escapeHtml(opt.value)}" ${checked} ${applied ? "disabled" : ""}>
          <span>${opt.tag ? `<span class="label-tag">${opt.tag}</span>` : ""}${escapeHtml(opt.value)}</span>
        </label>`;
    }
    html += `</div>`;
  }

  panel.innerHTML = html;
  panel.style.display = "block";

  panel.querySelectorAll('input[name="title-choice"]').forEach((input) => {
    input.addEventListener("change", async (e) => {
      const chosen = e.target.value;
      el("post-title").textContent = chosen;
      await fetch(`/api/posts/${currentPostId}/title/select`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: chosen }),
      });
    });
  });
}

function updateSummary(summary) {
  el("summary").textContent = summary
    ? `共 ${summary.total_changes} 處修改（採用 ${summary.accepted} ・維持原文 ${summary.rejected}）`
    : "";
  el("btn-apply").disabled = !summary || summary.total_changes === 0;
}

async function prepare(force) {
  if (!currentPostId) return;
  el("review-content").innerHTML = '<p style="color:#999">AI 校對中，請稍候...（文章較長時可能需要一點時間）</p>';
  const res = await fetch(`/api/posts/${currentPostId}/prepare`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force: !!force }),
  });
  const data = await res.json();
  if (!res.ok) {
    el("review-content").innerHTML = `<p style="color:#b3402a">${escapeHtml(data.error || "校對失敗")}</p>`;
    return;
  }
  renderReview(data);
  fetchPosts(el("search").value);
}

async function setStatus(nodeIndex, changeIndex, status, spanEl) {
  spanEl.classList.remove("accepted", "rejected");
  spanEl.classList.add(status);
  const res = await fetch(`/api/posts/${currentPostId}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ node_index: nodeIndex, change_index: changeIndex, status }),
  });
  if (res.ok) {
    const data = await res.json();
    updateSummary(data.summary);
  }
}

function openRedoModal(nodeIndex) {
  pendingRedoNodeIndex = nodeIndex;
  el("redo-instruction").value = "";
  el("redo-modal").style.display = "flex";
  el("redo-instruction").focus();
}

function closeRedoModal() {
  el("redo-modal").style.display = "none";
  pendingRedoNodeIndex = null;
}

async function submitRedo() {
  const instruction = el("redo-instruction").value.trim();
  if (!instruction || pendingRedoNodeIndex === null) return;
  const nodeIndex = pendingRedoNodeIndex;
  closeRedoModal();

  const nodeEl = document.querySelector(`.tp-node[data-node-index="${nodeIndex}"]`);
  if (nodeEl) nodeEl.style.opacity = "0.4";

  const res = await fetch(`/api/posts/${currentPostId}/redo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ node_index: nodeIndex, instruction }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "重新調整失敗");
    if (nodeEl) nodeEl.style.opacity = "1";
    return;
  }
  if (nodeEl) {
    nodeEl.outerHTML = data.fragment_html;
  }
  updateSummary(data.summary);
}

async function applyChanges() {
  if (!currentPostId) return;
  if (!confirm("確定要覆蓋原本的文章內容（含標題，如有變更）嗎？（原文會自動備份，可從 style-editor/backups 資料夾找回）")) return;
  const res = await fetch(`/api/posts/${currentPostId}/apply`, { method: "POST" });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || "套用失敗");
    return;
  }
  alert(`已覆蓋原文（標題：${data.final_title}）。原始內容備份於：\n${data.backup_file}`);
  selectPost(currentPostId);
}

// ── イベントバインド ───────────────────────────────────────

document.addEventListener("click", (e) => {
  const change = e.target.closest(".tp-change");
  if (!change) return;
  const [nodeIndex, changeIndex] = change.dataset.cid.split(":").map(Number);

  if (e.target.classList.contains("tp-accept")) {
    setStatus(nodeIndex, changeIndex, "accepted", change);
  } else if (e.target.classList.contains("tp-reject")) {
    setStatus(nodeIndex, changeIndex, "rejected", change);
  } else if (e.target.classList.contains("tp-redo")) {
    openRedoModal(nodeIndex);
  }
});

el("search").addEventListener("input", (e) => fetchPosts(e.target.value));
el("btn-prepare").addEventListener("click", () => prepare(true));
el("btn-apply").addEventListener("click", applyChanges);
el("redo-cancel").addEventListener("click", closeRedoModal);
el("redo-submit").addEventListener("click", submitRedo);

fetchPosts("");
