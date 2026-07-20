let currentKind = "grammar"; // "grammar" | "factcheck"（"manage" 分頁不算 kind，另外處理）
let currentPostId = null;
let currentPosts = [];
let pendingRedoNodeIndex = null;
let currentApplied = false; // 這份草稿是不是已經套用覆蓋原文了——套用後逐點的勾/叉/重新調整就該鎖起來

const el = (id) => document.getElementById(id);

const PREPARE_LABEL = {
    grammar: "開始 / 重新 AI 校對",
    factcheck: "開始 / 重新查核（匯出給 Claude）",
};

const EMPTY_REVIEW_HINT = {
    grammar: () =>
        `<p class="muted">尚未產生校對建議，請按上面「開始 / 重新 AI 校對」。</p>`,
    factcheck: (postId) =>
        factcheckSkillHint(
            postId,
            "這篇文章還沒有查核結果。請先按上面「開始 / 重新查核」匯出內容，然後到 Cowork 對話跟 Claude 說：",
        ),
};

// 事實查核分頁共用的「怎麼呼叫 Claude 查核」提示區塊：說明文字 + 可以一鍵複製的指令。
// intro 是最上面那句引導文字，postId 決定指令裡要查核哪篇文章。
function factcheckSkillHint(postId, intro) {
    const command = `幫我用 blog-factcheck skill 查核文章 ID ${postId}`;
    return `
    <div class="factcheck-skill-hint">
      <p class="fsh-intro">${escapeHtml(intro)}</p>
      <button type="button" class="fsh-command" data-copy-text="${escapeHtml(command)}" title="點擊複製指令">
        <code>${escapeHtml(command)}</code>
        <span class="fsh-copy-icon" aria-hidden="true"></span>
      </button>
      <p class="fsh-footer">Claude 會自動讀取匯出內容、上網查證、檢查文章裡的連結，寫完後回來這個畫面重新整理就看得到。</p>
    </div>`;
}

// 點一下上面那個指令方塊就複製到剪貼簿，並短暫顯示「已複製」的回饋。
document.addEventListener("click", (e) => {
    const btn = e.target.closest(".fsh-command");
    if (!btn) return;
    const text = btn.dataset.copyText || "";
    navigator.clipboard
        .writeText(text)
        .then(() => {
            const icon = btn.querySelector(".fsh-copy-icon");
            const original = icon.textContent;
            btn.classList.add("copied");
            icon.textContent = "✅";
            setTimeout(() => {
                btn.classList.remove("copied");
                icon.textContent = original;
            }, 1500);
        })
        .catch(() => {
            notifyModal("複製失敗，請手動選取文字複製：\n" + text);
        });
});

// 取代瀏覽器內建 confirm()/alert() 的自訂 modal，統一顯示在畫面正中間。
// showCancel=true 時是「確定/取消」的確認框（回傳 Promise<boolean>）；
// showCancel=false 時是只有一顆「好」的純提示框（回傳 Promise<void>，一定 resolve true）。
function showAppModal(message, { showCancel = true, okLabel = "確定" } = {}) {
    return new Promise((resolve) => {
        const modal = el("app-modal");
        const okBtn = el("app-modal-ok");
        const cancelBtn = el("app-modal-cancel");

        el("app-modal-message").textContent = message;
        okBtn.textContent = okLabel;
        cancelBtn.style.display = showCancel ? "" : "none";
        modal.style.display = "flex";

        const cleanup = (result) => {
            modal.style.display = "none";
            okBtn.removeEventListener("click", onOk);
            cancelBtn.removeEventListener("click", onCancel);
            resolve(result);
        };
        const onOk = () => cleanup(true);
        const onCancel = () => cleanup(false);
        okBtn.addEventListener("click", onOk);
        cancelBtn.addEventListener("click", onCancel);
    });
}

function confirmModal(message) {
    return showAppModal(message, { showCancel: true });
}

function notifyModal(message) {
    return showAppModal(message, { showCancel: false, okLabel: "好" });
}

async function fetchPosts(q) {
    const res = await fetch(`/api/posts?q=${encodeURIComponent(q || "")}`);
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        el("post-list").innerHTML =
            `<div class="warn-box">${err.error || "讀取文章清單失敗"}</div>`;
        return;
    }
    currentPosts = await res.json();
    renderPostList();
}

function hasDraft(post) {
    const field =
        currentKind === "grammar" ? "grammar_session" : "factcheck_session";
    return !!post[field];
}

function sortedPosts() {
    if (!el("draft-first").checked) return currentPosts;
    // 有草稿的排前面，各自維持原本（依修改時間）的相對順序
    return [...currentPosts].sort(
        (a, b) => (hasDraft(b) ? 1 : 0) - (hasDraft(a) ? 1 : 0),
    );
}

function badge(label, state) {
    if (!state) return "";
    const cls = state === "applied" ? "badge applied" : "badge pending";
    const title = state === "applied" ? `${label}：已套用` : `${label}：審核中`;
    return `<span class="${cls}" title="${title}">${label}</span>`;
}

function renderPostList() {
    const list = el("post-list");
    list.innerHTML = "";
    for (const p of sortedPosts()) {
        const div = document.createElement("div");
        div.className = "post-item" + (p.id === currentPostId ? " active" : "");
        div.innerHTML = `${escapeHtml(p.title)} ${badge("校", p.grammar_session)}${badge("查", p.factcheck_session)}
      <div class="meta">${p.status} ・ ${p.modified}</div>`;
        div.onclick = () => selectPost(p.id);
        list.appendChild(div);
    }
}

function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : s;
    return d.innerHTML;
}

// 後端存的時間是 "2026-07-11T17:48:21" 這種 ISO 格式，這裡轉成一般人看得懂的 "2026-07-11 17:48"。
// 字串沒帶時區資訊，new Date() 會直接當作本機時間解析，不會被誤轉時區。
function formatDateTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function selectPost(postId) {
    currentPostId = postId;
    renderPostList();
    el("empty-state").style.display = "none";
    el("editor").style.display = "block";
    el("warnings").innerHTML = "";
    el("applied-banner").style.display = "none";
    el("stale-banner").style.display = "none";

    const post = currentPosts.find((p) => p.id === postId);
    el("post-title").textContent = post ? post.title : "";
    el("review-content").innerHTML = "";
    el("review-content").classList.remove("is-applied");
    currentApplied = false;
    el("summary").textContent = "";
    updateApplyButtonVisibility();
    el("title-panel").style.display = "none";

    const res = await fetch(`/api/posts/${postId}/${currentKind}/review`);
    if (res.ok) {
        const data = await res.json();
        renderReview(data);
    } else {
        el("review-content").innerHTML = EMPTY_REVIEW_HINT[currentKind](postId);
    }
}

function renderReview(data) {
    el("post-title").textContent = data.title;
    el("review-content").innerHTML = data.review_html;
    currentApplied = !!data.applied;
    el("review-content").classList.toggle("is-applied", currentApplied);
    updateSummary(data.summary);
    updateApplyButtonVisibility(data.applied);

    if (data.warnings && data.warnings.length) {
        el("warnings").innerHTML = data.warnings
            .map((w) => `<div>${escapeHtml(w)}</div>`)
            .join("");
    } else {
        el("warnings").innerHTML = "";
    }

    if (data.applied) {
        el("applied-banner").style.display = "block";
        el("applied-banner").textContent =
            "✅ 已套用並覆蓋原文。若要再次修改，請重新按上面「開始 / 重新」。";
    } else {
        el("applied-banner").style.display = "none";
    }

    if (data.stale) {
        el("stale-banner").style.display = "block";
        el("stale-banner").textContent = "⚠️ " + data.stale_reason;
    } else {
        el("stale-banner").style.display = "none";
    }

    // 事實查核不檢查標題，也不顯示這個區塊
    if (currentKind === "grammar") {
        renderTitlePanel(data.title, data.title_check, data.applied);
    } else {
        el("title-panel").style.display = "none";
    }
}

// 事實查核是筆記功能，沒有套用/覆蓋原文這回事
function updateApplyButtonVisibility(applied) {
    const btn = el("btn-apply");
    if (currentKind !== "grammar") {
        btn.style.display = "none";
        return;
    }
    btn.style.display = "";
    btn.disabled = !!applied;
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
    const noteText =
        titleCheck.note ||
        (titleCheck.grammar_ok === false
            ? "標題文法可能有問題"
            : "標題文法上沒有問題");

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
            await fetch(
                `/api/posts/${currentPostId}/${currentKind}/title/select`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ title: chosen }),
                },
            );
        });
    });
}

function updateSummary(summary) {
    if (!summary) {
        el("summary").textContent = "";
        return;
    }
    if (currentKind === "grammar") {
        el("summary").textContent =
            `共 ${summary.total_changes} 處修改（採用 ${summary.accepted} ・維持原文 ${summary.rejected}）`;
    } else {
        // 事實查核沒有採用/維持原文，單純顯示有幾處建議修改可以參考
        el("summary").textContent = summary.total_changes
            ? `共 ${summary.total_changes} 處建議修改`
            : "";
    }
}

async function prepare(force) {
    if (!currentPostId) return;
    if (currentKind === "grammar") {
        el("review-content").innerHTML =
            '<p class="muted">AI 校對中，請稍候...（文章較長時可能需要一點時間）</p>';
    }
    const res = await fetch(
        `/api/posts/${currentPostId}/${currentKind}/prepare`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ force: !!force }),
        },
    );
    const data = await res.json();
    if (!res.ok) {
        el("review-content").innerHTML =
            `<p class="error">${escapeHtml(data.error || "執行失敗")}</p>`;
        return;
    }

    if (currentKind === "factcheck" && data.exported) {
        const noticeHtml = factcheckSkillHint(
            currentPostId,
            `${data.message}請到 Cowork 對話跟 Claude 說：`,
        );
        if (data.has_existing_session) {
            // 已經有 Claude 之前查核的結果，重新載入那份，把提示訊息放在最上面
            const res2 = await fetch(
                `/api/posts/${currentPostId}/factcheck/review`,
            );
            if (res2.ok) {
                const data2 = await res2.json();
                renderReview(data2);
                el("review-content").innerHTML =
                    noticeHtml + el("review-content").innerHTML;
                fetchPosts(el("search").value);
                return;
            }
        }
        el("review-content").innerHTML = noticeHtml;
        el("summary").textContent = "";
        updateApplyButtonVisibility();
        el("applied-banner").style.display = "none";
        el("stale-banner").style.display = "none";
        el("title-panel").style.display = "none";
        fetchPosts(el("search").value);
        return;
    }

    renderReview(data);
    fetchPosts(el("search").value);
}

async function setStatus(nodeIndex, changeIndex, status, spanEl) {
    spanEl.classList.remove("accepted", "rejected");
    spanEl.classList.add(status);
    const res = await fetch(
        `/api/posts/${currentPostId}/${currentKind}/status`,
        {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                node_index: nodeIndex,
                change_index: changeIndex,
                status,
            }),
        },
    );
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

    const nodeEl = document.querySelector(
        `.tp-node[data-node-index="${nodeIndex}"]`,
    );
    if (nodeEl) nodeEl.style.opacity = "0.4";

    const res = await fetch(`/api/posts/${currentPostId}/${currentKind}/redo`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_index: nodeIndex, instruction }),
    });
    const data = await res.json();
    if (!res.ok) {
        await notifyModal(data.error || "重新調整失敗");
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
    const ok = await confirmModal(
        "確定要覆蓋原本的文章內容（含標題，如有變更）嗎？（原文會自動備份，可從 style-editor/backups 資料夾找回）",
    );
    if (!ok) return;
    const res = await fetch(
        `/api/posts/${currentPostId}/${currentKind}/apply`,
        { method: "POST" },
    );
    const data = await res.json();
    if (!res.ok) {
        await notifyModal(data.error || "套用失敗");
        return;
    }
    await notifyModal(
        `已覆蓋原文（標題：${data.final_title}）。原始內容備份於：\n${data.backup_file}`,
    );
    selectPost(currentPostId);
}

// ── 分頁切換（日文校對 / 事實查核 / Session 管理） ──────────────────

const ACTIVE_TAB_STORAGE_KEY = "style-editor:active-tab";

function switchTab(tab) {
    document
        .querySelectorAll(".tab-btn")
        .forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    document.body.dataset.theme = tab; // 讓側欄/開始按鈕的顏色跟著換，一眼看出現在是哪個服務
    // 記住目前停留的分頁，重新整理網頁時可以回到同一頁，不用每次都跳回日文校對。
    localStorage.setItem(ACTIVE_TAB_STORAGE_KEY, tab);

    if (tab === "manage") {
        el("post-list-wrap").style.display = "none";
        el("empty-state").style.display = "none";
        el("editor").style.display = "none";
        el("manage-view").style.display = "block";
        loadManageView();
        return;
    }

    currentKind = tab;
    currentPostId = null;
    el("post-list-wrap").style.display = "block";
    el("manage-view").style.display = "none";
    el("editor").style.display = "none";
    el("empty-state").style.display = "block";
    el("btn-prepare").textContent = PREPARE_LABEL[tab];
    fetchPosts(el("search").value);
}

// ── Session 管理 ─────────────────────────────────────────────────

async function loadManageView() {
    const res = await fetch("/api/sessions");
    const items = res.ok ? await res.json() : [];
    const tbody = el("manage-tbody");
    tbody.innerHTML = "";
    if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="muted">目前沒有任何校對／查核中的草稿。</td></tr>`;
        return;
    }
    for (const it of items) {
        const tr = document.createElement("tr");
        const kindLabel = it.kind === "grammar" ? "日文校對" : "事實查核";
        const statusLabel =
            it.kind === "grammar" ? (it.applied ? "已套用" : "審核中") : "筆記";
        const changes =
            it.kind === "grammar"
                ? it.summary
                    ? `${it.summary.accepted} / ${it.summary.total_changes}`
                    : "-"
                : `${it.summary ? it.summary.total_changes : 0} 處修改・${it.note_count || 0} 則筆記`;
        tr.innerHTML = `
      <td>${escapeHtml(it.title || "")}</td>
      <td>${kindLabel}</td>
      <td>${statusLabel}</td>
      <td>${escapeHtml(formatDateTime(it.prepared_at))}</td>
      <td>${changes}</td>
      <td><button type="button" class="btn btn-danger btn-sm" data-post="${it.post_id}" data-kind="${it.kind}">刪除</button></td>`;
        tbody.appendChild(tr);
    }
    tbody.querySelectorAll("button[data-post]").forEach((btn) => {
        btn.addEventListener("click", async () => {
            const ok = await confirmModal(
                "確定要刪除這份草稿嗎？（只會刪除本機的審核進度，不會動到 WordPress 裡的文章）",
            );
            if (!ok) return;
            await fetch(
                `/api/sessions/${btn.dataset.kind}/${btn.dataset.post}`,
                { method: "DELETE" },
            );
            loadManageView();
        });
    });
}

// ── イベントバインド ───────────────────────────────────────

document.addEventListener("click", (e) => {
    const change = e.target.closest(".tp-change");
    // 事實查核的 .tp-change 是純靜態刪除線呈現，沒有 data-cid，不需要（也不能）互動
    if (!change || !change.dataset.cid) return;
    // 已經套用覆蓋原文的草稿：內容早就寫進 WordPress 了，勾/叉/重新調整鎖起來不能再點，
    // 避免點了以為改到東西，其實只是在改一份已經作廢的紀錄。
    if (currentApplied) return;
    const [nodeIndex, changeIndex] = change.dataset.cid.split(":").map(Number);

    if (e.target.classList.contains("tp-accept")) {
        setStatus(nodeIndex, changeIndex, "accepted", change);
    } else if (e.target.classList.contains("tp-reject")) {
        setStatus(nodeIndex, changeIndex, "rejected", change);
    } else if (e.target.classList.contains("tp-redo")) {
        openRedoModal(nodeIndex);
    }
});

document
    .querySelectorAll(".tab-btn")
    .forEach((b) =>
        b.addEventListener("click", () => switchTab(b.dataset.tab)),
    );

el("search").addEventListener("input", (e) => fetchPosts(e.target.value));
el("draft-first").addEventListener("change", renderPostList);
el("btn-prepare").addEventListener("click", () => prepare(true));
el("btn-apply").addEventListener("click", applyChanges);
el("redo-cancel").addEventListener("click", closeRedoModal);
el("redo-submit").addEventListener("click", submitRedo);

// 重新整理網頁時，回到上次停留的分頁（日文校對／事實查核／草稿管理），
// 而不是每次都跳回日文校對。第一次使用、或存的值不是三個分頁之一時，才 fallback 回日文校對。
const VALID_TABS = ["grammar", "factcheck", "manage"];
const savedTab = localStorage.getItem(ACTIVE_TAB_STORAGE_KEY);
switchTab(VALID_TABS.includes(savedTab) ? savedTab : "grammar");
