const API_BASE = resolveApiBase();
const FALLBACK_COLUMNS = [
  ["proximas_aulas", "Próximas aulas"],
  ["bibliografia_em_geracao", "Bibliografia em geração"],
  ["bibliografia_pronta", "Bibliografia pronta para download"],
  ["pdfs_baixados", "PDFs baixados"],
  ["texto_feito", "Texto feito"],
  ["texto_editado", "Texto editado"],
  ["pptx_gerado", "PPTX gerado"],
  ["pptx_finalizado", "PPTX finalizado"],
  ["pptx_na_pasta_final", "PPTX na pasta final"],
  ["erro_bloqueada", "Erro / bloqueada"],
];

// Cores por coluna (fase 1 = verde-azulado, fase 2 = azul, fase 3 = roxo, fase 4 = âmbar, final = verde, erro = vermelho)
const COLUMN_PHASE_CLASS = {
  proximas_aulas: "phase-start",
  bibliografia_em_geracao: "phase-1",
  bibliografia_pronta: "phase-1",
  pdfs_baixados: "phase-2",
  texto_feito: "phase-2",
  texto_editado: "phase-3",
  pptx_gerado: "phase-4",
  pptx_finalizado: "phase-4",
  pptx_na_pasta_final: "phase-final",
  erro_bloqueada: "phase-erro",
};

let state = null;
let columns = [];
let selectedAulaId = null;
const driveFilesCache = new Map();
let apiAvailable = true;
let filters = { module: "", theme: "" };
let pollTimer = null;

const boardEl = document.getElementById("board");
const statsEl = document.getElementById("stats");
const detailEl = document.getElementById("detail");
const detailTitleEl = document.getElementById("detail-title");
const detailContentEl = document.getElementById("detail-content");
const toastEl = document.getElementById("toast");

const syncBtn = document.getElementById("btn-sync");
const driveBootstrapBtn = document.getElementById("btn-drive-bootstrap");
const detailCloseBtn = document.getElementById("detail-close");
const moduleFilterEl = document.getElementById("filter-module");
const themeFilterEl = document.getElementById("filter-theme");
const clearFiltersBtn = document.getElementById("btn-clear-filters");

syncBtn.addEventListener("click", () => loadAll(true));
driveBootstrapBtn.addEventListener("click", runDriveBootstrap);
detailCloseBtn.addEventListener("click", closeDetail);
moduleFilterEl.addEventListener("change", () => {
  filters.module = moduleFilterEl.value;
  renderStats();
  renderBoard();
});
themeFilterEl.addEventListener("input", () => {
  filters.theme = themeFilterEl.value;
  renderStats();
  renderBoard();
});
clearFiltersBtn.addEventListener("click", () => {
  filters = { module: "", theme: "" };
  moduleFilterEl.value = "";
  themeFilterEl.value = "";
  renderStats();
  renderBoard();
});

async function loadAll(showMessage = false) {
  try {
    const [stateRes, colRes] = await Promise.all([
      fetch(`${API_BASE}/api/aulas`),
      fetch(`${API_BASE}/api/columns`),
    ]);
    if (!stateRes.ok || !colRes.ok) throw new Error("Falha ao carregar dados do backend.");
    state = await stateRes.json();
    const colJson = await colRes.json();
    columns = colJson.columns || FALLBACK_COLUMNS;
    populateModuleFilter();
    renderStats();
    renderBoard();
    if (selectedAulaId) openDetail(selectedAulaId);
    schedulePollingIfNeeded();
    if (showMessage) showToast("Kanban sincronizado.");
  } catch (err) {
    const loadedFallback = await tryLoadStaticFallback();
    if (!loadedFallback) showToast(`Erro: ${err.message}`, true);
  }
}

function schedulePollingIfNeeded() {
  // Auto-refresh enquanto houver bibliografia em geração.
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  const hasInflight = state?.aulas?.some((a) => a.status === "bibliografia_em_geracao");
  if (hasInflight && apiAvailable) {
    pollTimer = setTimeout(() => loadAll(false), 5000);
  }
}

function renderStats() {
  if (!state?.aulas?.length) {
    statsEl.innerHTML = "<div class='stat'>Nenhuma aula encontrada.</div>";
    return;
  }
  const all = state.aulas;
  const filtered = getFilteredAulas();
  const total = filtered.length;
  const finalizadas = filtered.filter((a) => a.status === "pptx_na_pasta_final").length;
  const bloqueadas = filtered.filter((a) => a.status === "erro_bloqueada").length;
  const emGeracao = filtered.filter((a) => a.status === "bibliografia_em_geracao").length;
  statsEl.innerHTML = `
    <div class="stat"><strong>${total}</strong><span>Aulas exibidas</span></div>
    <div class="stat"><strong>${all.length}</strong><span>Aulas totais</span></div>
    <div class="stat"><strong>${finalizadas}</strong><span>Na pasta final</span></div>
    <div class="stat"><strong>${emGeracao}</strong><span>Bibliografia em geração</span></div>
    <div class="stat"><strong>${bloqueadas}</strong><span>Bloqueadas</span></div>
    <div class="stat"><strong>${new Date(state.updated_at).toLocaleString("pt-BR")}</strong><span>Última sincronização</span></div>
  `;
}

function renderBoard() {
  boardEl.innerHTML = "";
  const filteredAulas = getFilteredAulas();
  for (const [statusKey, statusLabel] of columns) {
    const aulas = filteredAulas.filter((a) => a.status === statusKey);
    const phaseClass = COLUMN_PHASE_CLASS[statusKey] || "phase-default";
    const column = document.createElement("section");
    column.className = `column ${phaseClass}`;
    column.innerHTML = `
      <header class="column-head">
        <h3>${statusLabel}</h3>
        <span class="badge">${aulas.length}</span>
      </header>
      <div class="cards"></div>
    `;
    const cardsEl = column.querySelector(".cards");
    if (!aulas.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Sem aulas nesta etapa.";
      cardsEl.appendChild(empty);
    } else {
      for (const aula of aulas) cardsEl.appendChild(buildCard(aula));
    }
    boardEl.appendChild(column);
  }
}

function buildCard(aula) {
  const card = document.createElement("article");
  card.className = `card phase-${COLUMN_PHASE_CLASS[aula.status] || "default"}`;

  const headerHtml = `
    <div class="card-head">
      <p class="kicker">M${aula.modulo_num} · Aula ${aula.aula_num}</p>
      <h4>${escapeHtml(aula.aula_tema)}</h4>
      <p class="module">${escapeHtml(aula.modulo_nome)}</p>
    </div>
  `;

  const stateBlocks = renderCardStateBlock(aula);
  const actionsHtml = renderCardActions(aula);

  card.innerHTML = `
    ${headerHtml}
    ${stateBlocks}
    <div class="card-actions">${actionsHtml}</div>
  `;

  attachCardHandlers(card, aula);
  return card;
}

function renderCardStateBlock(aula) {
  if (aula.status === "bibliografia_em_geracao") {
    const msg = aula.progresso || "Iniciando…";
    return `
      <div class="card-progress">
        <div class="spinner"></div>
        <div>
          <p class="progress-label">Em execução</p>
          <p class="progress-msg">${escapeHtml(msg)}</p>
        </div>
      </div>
    `;
  }
  if (aula.status === "erro_bloqueada") {
    const pend = (aula.pendencias || []).map((p) => `<li>${escapeHtml(p)}</li>`).join("");
    return `<div class="card-error"><strong>Falha:</strong><ul>${pend || "<li>Sem detalhes.</li>"}</ul></div>`;
  }

  const pdfLine = `PDFs: <strong>${aula.pdfs.baixados}/${aula.pdfs.total}</strong>`;
  const nextLine = `Próxima: <strong>${escapeHtml(aula.proxima_acao)}</strong>`;
  return `<ul class="meta"><li>${nextLine}</li><li>${pdfLine}</li></ul>`;
}

function renderCardActions(aula) {
  const buttons = [];

  switch (aula.status) {
    case "proximas_aulas":
      buttons.push(actionBtn("gerar-bibliografia", "Gerar bibliografia", "color-biblio"));
      break;

    case "bibliografia_em_geracao":
      buttons.push(`<button class="btn secondary" data-action="voltar-etapa" data-id="${aula.id}">Cancelar (voltar)</button>`);
      break;

    case "bibliografia_pronta":
      if (!hasBibliografiaArtifacts(aula)) {
        buttons.push(`<button class="btn primary" data-rehidratar="${aula.id}">Carregar referências do Drive</button>`);
      } else {
        buttons.push(linksBtn(aula, "Abrir todos os links"));
      }
      buttons.push(livrosBtn(aula));
      buttons.push(actionBtn("marcar-pdfs-baixados", "PDFs baixados", "color-pdf"));
      break;

    case "pdfs_baixados":
      buttons.push(editorBtn(aula, "Colar texto do NotebookLM", "color-texto"));
      break;

    case "texto_feito":
      buttons.push(editorBtn(aula, "Editar texto", "color-texto"));
      buttons.push(actionBtn("concluir-edicao", "Concluir edição", "color-aprovar"));
      break;

    case "texto_editado":
      buttons.push(editorBtn(aula, "Ver/editar texto", "color-default"));
      buttons.push(actionBtn("gerar-pptx", "Gerar PPTX", "color-pptx"));
      break;

    case "pptx_gerado":
      buttons.push(pptxBtn(aula, "Abrir PPTX no Drive"));
      buttons.push(actionBtn("marcar-imagens-prontas", "Imagens prontas", "color-aprovar"));
      break;

    case "pptx_finalizado":
      buttons.push(pptxBtn(aula, "Abrir PPTX"));
      buttons.push(actionBtn("mover-pptx-final", "Mover para pasta final", "color-concluir"));
      break;

    case "pptx_na_pasta_final":
      buttons.push(pptxBtn(aula, "Abrir PPTX"));
      break;

    case "erro_bloqueada":
      buttons.push(`<button class="btn secondary" data-action="voltar-etapa" data-id="${aula.id}">Voltar etapa</button>`);
      break;
  }

  buttons.push(`<button class="btn ghost" data-detail="${aula.id}">Detalhes</button>`);
  return buttons.join("");
}

function actionBtn(route, label, colorClass = "color-default") {
  return `<button class="btn next-action ${colorClass}" data-action="${route}">${escapeHtml(label)}</button>`;
}

function editorBtn(aula, label, colorClass = "color-texto") {
  return `<button class="btn next-action ${colorClass}" data-editor="${aula.id}">${escapeHtml(label)}</button>`;
}

function linksBtn(aula, label) {
  return `<button class="btn primary" data-open-all="${aula.id}">${escapeHtml(label)}</button>`;
}

function hasBibliografiaArtifacts(aula) {
  const a = aula.ai_artifacts || {};
  return BIBLIO_SOURCES.some((s) => (a[s.key] || "").trim().length > 0);
}

async function rehidratarBibliografia(aulaId) {
  if (!apiAvailable) {
    showToast("Indisponível em modo somente leitura.", true);
    return false;
  }
  try {
    const res = await fetch(`${API_BASE}/api/aulas/${encodeURIComponent(aulaId)}/rehidratar-bibliografia`, {
      method: "POST",
    });
    const payload = await res.json();
    if (!res.ok || !payload.ok) throw new Error(payload.detail || payload.message || "Falha");
    showToast(`Carregadas ${payload.total} fonte(s) do Drive.`);
    await loadAll(false);
    return payload.total > 0;
  } catch (err) {
    showToast(`Erro ao carregar do Drive: ${err.message}`, true);
    return false;
  }
}

function livrosBtn(aula) {
  const folderId = aula.drive_subfolders?.["02_livros_extraidos"];
  if (!folderId) {
    return `<button class="btn secondary" disabled title="Pasta não localizada no Drive">Pasta dos livros</button>`;
  }
  const url = `https://drive.google.com/drive/folders/${encodeURIComponent(folderId)}`;
  return `<a class="btn secondary" href="${escapeAttr(url)}" target="_blank" rel="noopener">Pasta dos livros</a>`;
}

function pptxBtn(aula, label) {
  const url = aula.arquivos?.pptx_web_view_link || "";
  if (!url) {
    return `<button class="btn secondary" data-drive-action="locate-pptx" data-id="${aula.id}">Localizar PPTX</button>`;
  }
  return `<a class="btn secondary" href="${escapeAttr(url)}" target="_blank" rel="noopener">${escapeHtml(label)}</a>`;
}

function attachCardHandlers(card, aula) {
  card.querySelectorAll("[data-detail]").forEach((btn) => {
    btn.addEventListener("click", () => openDetail(aula.id));
  });
  card.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const target = e.currentTarget;
      await runAction(aula.id, target.dataset.action);
    });
  });
  card.querySelectorAll("[data-editor]").forEach((btn) => {
    btn.addEventListener("click", () => openTextEditor(aula));
  });
  card.querySelectorAll("[data-open-all]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      let links = collectAllLinks(aula);
      if (!links.length) {
        const loaded = await rehidratarBibliografia(aula.id);
        if (!loaded) return;
        const refreshed = state.aulas.find((a) => a.id === aula.id);
        links = refreshed ? collectAllLinks(refreshed) : [];
      }
      openLinksInTabs(links);
    });
  });
  card.querySelectorAll("[data-rehidratar]").forEach((btn) => {
    btn.addEventListener("click", () => rehidratarBibliografia(aula.id));
  });
  card.querySelectorAll("[data-drive-action]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const target = e.currentTarget;
      const action = target.dataset.driveAction;
      if (action === "locate-pptx") {
        await listDriveFilesForAula(aula.id, false);
        showToast("Listagem de arquivos atualizada — verifique nos detalhes da aula.");
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Editor de texto inline
// ---------------------------------------------------------------------------

async function openTextEditor(aula) {
  if (!apiAvailable) {
    showToast("Editor indisponível no GitHub Pages (modo somente leitura).", true);
    return;
  }
  const overlay = document.createElement("div");
  overlay.className = "editor-overlay";
  overlay.innerHTML = `
    <div class="editor-modal">
      <header>
        <h3>${escapeHtml(aula.id)} · ${escapeHtml(aula.aula_tema)}</h3>
        <button class="btn ghost" data-editor-close>Fechar</button>
      </header>
      <div class="editor-loading">Carregando texto do Drive…</div>
      <textarea class="editor-textarea" placeholder="Cole aqui o texto do NotebookLM, ou edite o existente." hidden></textarea>
      <footer>
        <span class="editor-hint">O texto é gravado em <code>04_aula_texto.md</code> no Drive.</span>
        <div class="editor-actions">
          <button class="btn secondary" data-editor-save>Salvar</button>
          <button class="btn primary" data-editor-save-advance>Salvar e avançar</button>
        </div>
      </footer>
    </div>
  `;
  document.body.appendChild(overlay);

  const closeBtn = overlay.querySelector("[data-editor-close]");
  const textarea = overlay.querySelector(".editor-textarea");
  const loadingEl = overlay.querySelector(".editor-loading");
  const saveBtn = overlay.querySelector("[data-editor-save]");
  const saveAdvanceBtn = overlay.querySelector("[data-editor-save-advance]");

  const close = () => overlay.remove();
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
  });

  try {
    const res = await fetch(`${API_BASE}/api/aulas/${encodeURIComponent(aula.id)}/texto`);
    const payload = await res.json();
    textarea.value = payload.conteudo || "";
  } catch (err) {
    showToast(`Erro ao carregar texto: ${err.message}`, true);
  } finally {
    loadingEl.remove();
    textarea.hidden = false;
    textarea.focus();
  }

  const saveOnly = async () => {
    saveBtn.disabled = true;
    saveAdvanceBtn.disabled = true;
    try {
      await saveTexto(aula.id, textarea.value);
      showToast("Texto salvo. Sincronizando com o Drive…");
    } catch (err) {
      showToast(`Erro ao salvar: ${err.message}`, true);
    } finally {
      saveBtn.disabled = false;
      saveAdvanceBtn.disabled = false;
    }
  };

  const saveAndAdvance = async () => {
    saveBtn.disabled = true;
    saveAdvanceBtn.disabled = true;
    try {
      await saveTexto(aula.id, textarea.value);
      // Disparar action de transição correspondente ao status atual.
      const nextRoute = nextRouteAfterEditor(aula.status);
      if (nextRoute) {
        await runAction(aula.id, nextRoute, false);
      }
      showToast("Texto salvo e aula avançada.");
      close();
    } catch (err) {
      showToast(`Erro: ${err.message}`, true);
      saveBtn.disabled = false;
      saveAdvanceBtn.disabled = false;
    }
  };

  saveBtn.addEventListener("click", saveOnly);
  saveAdvanceBtn.addEventListener("click", saveAndAdvance);
}

function nextRouteAfterEditor(currentStatus) {
  if (currentStatus === "pdfs_baixados") return "salvar-texto-inicial";
  if (currentStatus === "texto_feito") return "concluir-edicao";
  return null; // texto_editado e além: só salva, não avança.
}

async function saveTexto(aulaId, conteudo) {
  const res = await fetch(`${API_BASE}/api/aulas/${encodeURIComponent(aulaId)}/texto`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conteudo }),
  });
  const payload = await res.json();
  if (!res.ok || !payload.ok) throw new Error(payload.detail || payload.message || "Falha ao salvar");
  return payload;
}

// ---------------------------------------------------------------------------
// Detalhes (modal lateral)
// ---------------------------------------------------------------------------

function closeDetail() {
  detailEl.classList.add("hidden");
  selectedAulaId = null;
}

function openDetail(aulaId) {
  selectedAulaId = aulaId;
  const aula = state.aulas.find((a) => a.id === aulaId);
  if (!aula) return;
  detailTitleEl.textContent = `${aula.id} · ${aula.aula_tema}`;
  detailContentEl.innerHTML = buildDetailHtml(aula);
  detailEl.classList.remove("hidden");

  for (const btn of detailContentEl.querySelectorAll("[data-action]")) {
    btn.addEventListener("click", async (e) => {
      await runAction(aula.id, e.currentTarget.dataset.action);
    });
  }
  for (const btn of detailContentEl.querySelectorAll("[data-drive-action]")) {
    btn.addEventListener("click", async (e) => {
      const action = e.currentTarget.dataset.driveAction;
      if (action === "list-files") await listDriveFilesForAula(aula.id, false);
      if (action === "upload-file") await uploadFileForAula(aula.id, false);
    });
  }
  const openAllBtn = detailContentEl.querySelector("[data-open-all]");
  if (openAllBtn) {
    openAllBtn.addEventListener("click", async () => {
      let links = collectAllLinks(aula);
      if (!links.length) {
        const loaded = await rehidratarBibliografia(aula.id);
        if (!loaded) return;
        const refreshed = state.aulas.find((a) => a.id === aula.id);
        links = refreshed ? collectAllLinks(refreshed) : [];
      }
      openLinksInTabs(links);
    });
  }
  for (const btn of detailContentEl.querySelectorAll("[data-open-group]")) {
    btn.addEventListener("click", (e) => {
      const groupKey = e.currentTarget.dataset.openGroup;
      const groups = collectBibliografiaGroups(aula);
      const grupo = groups.find((g) => g.key === groupKey);
      openLinksInTabs(grupo ? grupo.links : []);
    });
  }
  const editorBtnEl = detailContentEl.querySelector("[data-editor]");
  if (editorBtnEl) {
    editorBtnEl.addEventListener("click", () => openTextEditor(aula));
  }
  for (const btn of detailContentEl.querySelectorAll("[data-remove-link]")) {
    btn.addEventListener("click", async (e) => {
      const target = e.currentTarget;
      await removerLinkBibliografia(aula.id, target.dataset.source, target.dataset.url, target);
    });
  }
  for (const btn of detailContentEl.querySelectorAll("[data-rehidratar]")) {
    btn.addEventListener("click", () => rehidratarBibliografia(aula.id));
  }
}

async function removerLinkBibliografia(aulaId, source, url, btnEl) {
  if (!apiAvailable) {
    showToast("Indisponível no modo somente leitura.", true);
    return;
  }
  const ok = window.confirm(`Remover esta referência de ${source}?\n\n${url}`);
  if (!ok) return;

  // UI otimista: esconde o item na hora; restaura se a API falhar.
  const itemEl = btnEl ? btnEl.closest(".biblio-item") : null;
  if (itemEl) itemEl.classList.add("biblio-item-removing");

  try {
    const res = await fetch(`${API_BASE}/api/aulas/${encodeURIComponent(aulaId)}/bibliografia/remover-link`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source, url }),
    });
    const payload = await res.json();
    if (!res.ok || !payload.ok) throw new Error(payload.detail || payload.message || "Falha");
    if (itemEl) itemEl.remove();
    showToast(
      payload.drive_scheduled
        ? "Referência removida. Sincronizando com o Drive…"
        : "Referência removida."
    );
    // Recarrega em segundo plano para manter contadores corretos.
    loadAll(false);
  } catch (err) {
    if (itemEl) itemEl.classList.remove("biblio-item-removing");
    showToast(`Erro ao remover: ${err.message}`, true);
  }
}

function buildDetailHtml(aula) {
  const pendencias = aula.pendencias?.length
    ? `<ul>${aula.pendencias.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>`
    : "<p>Sem pendências registradas.</p>";

  const pdfs = aula.pdfs.nomes?.length
    ? `<ul>${aula.pdfs.nomes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>`
    : "<p>Nenhum PDF encontrado na pasta 03_pdfs_artigos.</p>";

  const preview = aula.texto_preview
    ? `<p class="preview">${escapeHtml(aula.texto_preview)}</p>`
    : "<p>Texto da aula ainda não disponível.</p>";

  const pptxLink = aula.arquivos?.pptx_web_view_link
    ? `<p><a href="${escapeAttr(aula.arquivos.pptx_web_view_link)}" target="_blank" rel="noopener">Abrir PPTX no Drive</a></p>`
    : "<p>PPTX ainda não localizado.</p>";

  const editorBtn = ["pdfs_baixados", "texto_feito", "texto_editado"].includes(aula.status)
    ? `<button class="btn next-action color-texto" data-editor="${aula.id}">Abrir editor de texto</button>`
    : "";

  const bibliografia = renderBibliografiaSection(aula);

  return `
    <section class="detail-grid">
      <div>
        <h3>Dados da aula</h3>
        <p><strong>Módulo:</strong> M${aula.modulo_num} - ${escapeHtml(aula.modulo_nome)}</p>
        <p><strong>Aula:</strong> ${aula.aula_num} - ${escapeHtml(aula.aula_tema)}</p>
        <p><strong>Status:</strong> ${escapeHtml(aula.status)}</p>
        <p><strong>Próxima ação:</strong> ${escapeHtml(aula.proxima_acao)}</p>
        <p><strong>Pasta:</strong> <code>${escapeHtml(aula.pasta_relativa)}</code></p>
      </div>
      <div>
        <h3>PDFs da aula</h3>
        <p><strong>Baixados/Total:</strong> ${aula.pdfs.baixados}/${aula.pdfs.total}</p>
        ${pdfs}
      </div>
    </section>

    <section>
      <h3>Pendências</h3>
      ${pendencias}
    </section>

    <section>
      <h3>Texto da aula</h3>
      ${preview}
      ${editorBtn}
    </section>

    <section>
      <h3>PPTX</h3>
      ${pptxLink}
    </section>

    ${bibliografia}

    <section>
      <h3>Drive</h3>
      <p><strong>Drive folder ID:</strong> ${escapeHtml(aula.drive_folder_id || "não vinculado")}</p>
      <div class="actions-wrap">
        <button class="btn secondary" data-drive-action="list-files">Listar arquivos Drive</button>
        <button class="btn secondary" data-drive-action="upload-file">Upload para Drive</button>
      </div>
      ${renderDriveFilesSection(aula.id)}
    </section>

    <section>
      <h3>Ações manuais</h3>
      <div class="actions-wrap">
        <button class="btn secondary" data-action="avancar-etapa">Avançar etapa</button>
        <button class="btn secondary" data-action="voltar-etapa">Voltar etapa</button>
        <button class="btn secondary" data-action="abrir-pasta">Abrir pasta local</button>
      </div>
    </section>
  `;
}

async function runAction(aulaId, route, silent = false) {
  if (!apiAvailable) {
    showToast("Ações desabilitadas no GitHub Pages (modo somente leitura).", true);
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/aulas/${aulaId}/actions/${route}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: "ação via dashboard" }),
    });
    const payload = await res.json();
    if (!res.ok || !payload.ok) throw new Error(payload.message || payload.detail || "Falha na ação");
    if (!silent) showToast(payload.message || "Ação executada.");
    await loadAll(false);
  } catch (err) {
    if (!silent) showToast(`Erro: ${err.message}`, true);
    throw err;
  }
}

async function runDriveBootstrap() {
  if (!apiAvailable) {
    showToast("Drive indisponível no GitHub Pages (modo somente leitura).", true);
    return;
  }
  driveBootstrapBtn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/api/drive/bootstrap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const payload = await res.json();
    if (!res.ok || !payload.ok) throw new Error(payload.message || payload.detail || "Falha no bootstrap");
    const summary = payload.summary || {};
    showToast(`Drive bootstrap OK: módulos ${summary.modules_touched ?? 0}, aulas ${summary.aulas_touched ?? 0}.`);
    await loadAll(false);
  } catch (err) {
    showToast(`Erro Drive bootstrap: ${err.message}`, true);
  } finally {
    driveBootstrapBtn.disabled = false;
  }
}

async function listDriveFilesForAula(aulaId, forceOpenDetail = false) {
  if (!apiAvailable) return;
  if (forceOpenDetail && selectedAulaId !== aulaId) openDetail(aulaId);
  try {
    const res = await fetch(`${API_BASE}/api/aulas/${encodeURIComponent(aulaId)}/drive-files`);
    const payload = await res.json();
    if (!res.ok || !payload.ok) throw new Error(payload.message || payload.detail || "Falha");
    driveFilesCache.set(aulaId, payload.files || []);
    showToast(`Drive: ${payload.count ?? (payload.files || []).length} arquivo(s) para ${aulaId}.`);
    if (selectedAulaId === aulaId) openDetail(aulaId);
  } catch (err) {
    showToast(`Erro listagem Drive: ${err.message}`, true);
  }
}

async function uploadFileForAula(aulaId, forceOpenDetail = false) {
  if (!apiAvailable) return;
  const aula = state?.aulas?.find((item) => item.id === aulaId);
  if (!aula) return;
  if (forceOpenDetail && selectedAulaId !== aulaId) openDetail(aulaId);

  const file = await pickFileFromUser();
  if (!file) return;

  const targetSubfolder = window.prompt(
    "Subpasta destino no Drive (opcional): 01_bibliografia, 02_livros_extraidos, 03_pdfs_artigos, 04_aula_texto. Deixe vazio para raiz da aula.",
    ""
  );
  if (targetSubfolder === null) return;
  const targetName = window.prompt("Nome final do arquivo (opcional).", "");
  if (targetName === null) return;

  const formData = new FormData();
  formData.append("file", file);
  if (targetSubfolder.trim()) formData.append("target_subfolder", targetSubfolder.trim());
  if (targetName.trim()) formData.append("target_name", targetName.trim());

  try {
    const res = await fetch(`${API_BASE}/api/aulas/${encodeURIComponent(aulaId)}/upload-browser`, {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.message || data.detail || "Falha no upload");
    showToast(`Upload OK (${aulaId}): ${data.file?.name || "arquivo"}`);
    await listDriveFilesForAula(aulaId, false);
  } catch (err) {
    showToast(`Erro upload Drive: ${err.message}`, true);
  }
}

// ---------------------------------------------------------------------------
// Bibliografia — links
// ---------------------------------------------------------------------------

const BIBLIO_SOURCES = [
  { key: "diretrizes_consensos.md", label: "Diretrizes e Consensos" },
  { key: "pubmed_busca.md", label: "PubMed" },
  { key: "uptodate.md", label: "UpToDate" },
  { key: "capitulos_livros.md", label: "Livros (capítulos extraídos)" },
];

function renderBibliografiaSection(aula) {
  const groups = collectBibliografiaGroups(aula);
  const total = groups.reduce((n, g) => n + g.links.length, 0);
  // Mesmo sem artifacts em memória, oferece carregar do Drive se aula já passou da fase 1.
  if (!total) {
    const beyondPhase1 = [
      "bibliografia_pronta",
      "pdfs_baixados",
      "texto_feito",
      "texto_editado",
      "pptx_gerado",
      "pptx_finalizado",
      "pptx_na_pasta_final",
    ].includes(aula.status);
    if (!beyondPhase1) return "";
    return `
      <section class="biblio">
        <div class="biblio-head">
          <h3>Bibliografia</h3>
          <button class="btn primary" data-rehidratar="${escapeAttr(aula.id)}">Carregar referências do Drive</button>
        </div>
        <p class="biblio-hint">Os arquivos .md estão no Drive, mas o servidor reiniciou e perdeu o cache. Clique acima para re-carregar.</p>
      </section>
    `;
  }

  const groupsHtml = groups
    .filter((g) => g.links.length)
    .map((g) => {
      const items = g.links
        .map(
          (l) => `
            <li class="biblio-item">
              <div class="biblio-item-text">
                <a href="${escapeAttr(l.url)}" target="_blank" rel="noopener">${escapeHtml(l.title || l.url)}</a>${l.meta ? ` <span class="biblio-meta">${escapeHtml(l.meta)}</span>` : ""}
              </div>
              <button class="biblio-remove" title="Remover esta referência" data-remove-link data-source="${escapeAttr(g.key)}" data-url="${escapeAttr(l.url)}">✕</button>
            </li>`
        )
        .join("");
      return `
        <div class="biblio-group">
          <div class="biblio-group-head">
            <h4>${escapeHtml(g.label)} <span class="biblio-count">${g.links.length}</span></h4>
            <button class="btn ghost" data-open-group="${escapeAttr(g.key)}">Abrir esta fonte</button>
          </div>
          <ul class="biblio-list">${items}</ul>
        </div>`;
    })
    .join("");

  const livrosFolderId = aula.drive_subfolders?.["02_livros_extraidos"];
  const livrosLink = livrosFolderId
    ? `<a class="btn secondary" href="https://drive.google.com/drive/folders/${encodeURIComponent(livrosFolderId)}" target="_blank" rel="noopener">Pasta dos livros (Drive)</a>`
    : "";
  return `
    <section class="biblio">
      <div class="biblio-head">
        <h3>Bibliografia · ${total} links</h3>
        <div class="biblio-head-actions">
          ${livrosLink}
          <button class="btn primary" data-open-all="${escapeAttr(aula.id)}">Abrir todos os links</button>
        </div>
      </div>
      <p class="biblio-hint">Se o navegador bloquear popups, clique no ícone de bloqueio na barra de endereço → Sempre permitir pop-ups deste site.</p>
      ${groupsHtml}
    </section>
  `;
}

function collectBibliografiaGroups(aula) {
  const artifacts = aula.ai_artifacts || {};
  return BIBLIO_SOURCES.map(({ key, label }) => {
    const md = artifacts[key] || "";
    const links = extractLinksFromMarkdown(md);
    return { key, label, links };
  });
}

function collectAllLinks(aula) {
  const groups = collectBibliografiaGroups(aula);
  const seen = new Set();
  const out = [];
  for (const g of groups) {
    for (const link of g.links) {
      if (!link.url || seen.has(link.url)) continue;
      seen.add(link.url);
      out.push(link);
    }
  }
  return out;
}

const MARKDOWN_LINK_RE = /\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g;

function extractLinksFromMarkdown(md) {
  if (!md) return [];
  const seen = new Set();
  const links = [];
  let match;
  while ((match = MARKDOWN_LINK_RE.exec(md)) !== null) {
    const title = (match[1] || "").trim();
    const url = (match[2] || "").trim();
    if (!url || seen.has(url)) continue;
    seen.add(url);
    const tail = md.slice(match.index + match[0].length, match.index + match[0].length + 240);
    const metaMatch = tail.match(/^\s*[—\-]\s*([^\n]+?)(?=\n|$)/);
    const pdfMatch = tail.match(/^\s*·\s*(PDF)(?=\b|\s|$)/);
    let meta = "";
    if (metaMatch) meta = metaMatch[1].trim();
    else if (pdfMatch) meta = pdfMatch[1];
    links.push({ title, url, meta });
  }
  return links;
}

function openLinksInTabs(links) {
  if (!links.length) {
    showToast("Sem links para abrir.", true);
    return;
  }
  let opened = 0;
  let blocked = 0;
  const stamp = Date.now();
  // Usa nome de janela único por link para garantir que cada um vire uma aba
  // separada (com `_blank` o navegador pode reaproveitar a mesma janela em
  // alguns casos). Sem o terceiro parâmetro `features`, o navegador trata como
  // nova aba — passar features força o popup, que costuma ser bloqueado.
  for (let i = 0; i < links.length; i++) {
    const target = `gineco-${stamp}-${i}`;
    const win = window.open(links[i].url, target);
    if (win) {
      try { win.opener = null; } catch (_) { /* cross-origin: ignore */ }
      opened += 1;
    } else {
      blocked += 1;
    }
  }
  if (blocked > 0) {
    showToast(
      `${opened}/${links.length} abertas. ${blocked} bloqueadas — clique no ícone de bloqueio de pop-ups na barra de endereço → Sempre permitir pop-ups deste site → Concluído → clique novamente em "Abrir todos".`,
      true
    );
  } else {
    showToast(`Abertas ${opened} aba(s).`);
  }
}

function escapeAttr(value) {
  return escapeHtml(value);
}

function renderDriveFilesSection(aulaId) {
  if (!driveFilesCache.has(aulaId)) return "<p>Sem listagem carregada. Clique em \"Listar arquivos Drive\".</p>";
  const files = driveFilesCache.get(aulaId) || [];
  if (!files.length) return "<p>Sem arquivos encontrados para esta aula no Drive.</p>";
  const items = files
    .map((f) => `<li>${escapeHtml(f.name)}${f.parentLabel ? ` (${escapeHtml(f.parentLabel)})` : ""}</li>`)
    .join("");
  return `<ul class="drive-files-list">${items}</ul>`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function showToast(message, isError = false) {
  toastEl.textContent = message;
  toastEl.classList.remove("hidden", "error");
  if (isError) toastEl.classList.add("error");
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => toastEl.classList.add("hidden"), 4200);
}

loadAll(false);

function populateModuleFilter() {
  const previous = moduleFilterEl.value;
  const moduleMap = new Map();
  for (const aula of state.aulas) {
    if (!moduleMap.has(aula.modulo_num)) moduleMap.set(aula.modulo_num, aula.modulo_nome);
  }
  moduleFilterEl.innerHTML = '<option value="">Todos</option>';
  Array.from(moduleMap.entries())
    .sort((a, b) => a[0] - b[0])
    .forEach(([num, nome]) => {
      const opt = document.createElement("option");
      opt.value = String(num);
      opt.textContent = `M${num} - ${nome}`;
      moduleFilterEl.appendChild(opt);
    });
  if (previous && moduleMap.has(Number(previous))) {
    moduleFilterEl.value = previous;
    filters.module = previous;
  }
}

function getFilteredAulas() {
  if (!state?.aulas?.length) return [];
  const module = filters.module?.trim();
  const theme = normalizeText(filters.theme || "");
  return state.aulas.filter((aula) => {
    const okModule = !module || String(aula.modulo_num) === module;
    const okTheme =
      !theme ||
      normalizeText(aula.aula_tema).includes(theme) ||
      normalizeText(aula.modulo_nome).includes(theme) ||
      normalizeText(`${aula.id}`).includes(theme);
    return okModule && okTheme;
  });
}

function normalizeText(v) {
  return String(v || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim();
}

function resolveApiBase() {
  const defaultCloudRunApi = "https://gineco-api-468351448933.us-central1.run.app";
  const byWindow = String(window.KANBAN_API_BASE || "").trim();
  if (byWindow) return byWindow.replace(/\/$/, "");
  const fromQuery = new URLSearchParams(window.location.search).get("api_base");
  if (fromQuery && fromQuery.trim()) {
    const normalized = fromQuery.trim().replace(/\/$/, "");
    window.localStorage.setItem("kanban_api_base", normalized);
    return normalized;
  }
  const fromStorage = window.localStorage.getItem("kanban_api_base");
  if (fromStorage && fromStorage.trim()) return fromStorage.trim().replace(/\/$/, "");
  if (window.location.hostname.endsWith("github.io")) return defaultCloudRunApi;
  return "";
}

function pickFileFromUser() {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.style.display = "none";
    input.addEventListener("change", () => {
      const file = input.files && input.files[0] ? input.files[0] : null;
      input.remove();
      resolve(file);
    });
    document.body.appendChild(input);
    input.click();
  });
}

async function tryLoadStaticFallback() {
  try {
    const res = await fetch("../data/aulas.json");
    if (!res.ok) return false;
    state = await res.json();
    columns = FALLBACK_COLUMNS;
    apiAvailable = false;
    populateModuleFilter();
    renderStats();
    renderBoard();
    if (selectedAulaId) openDetail(selectedAulaId);
    showToast("Modo GitHub Pages: visualização estática carregada.");
    return true;
  } catch (_) {
    return false;
  }
}
