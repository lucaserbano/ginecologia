const API_BASE = resolveApiBase();
const FALLBACK_COLUMNS = [
  ["proximas_aulas", "Próximas aulas"],
  ["bibliografia_em_geracao", "Bibliografia em geração"],
  ["bibliografia_pronta", "Bibliografia pronta"],
  ["aguardando_aprovacao_fontes", "Aguardando aprovação das fontes"],
  ["aguardando_pdfs", "Aguardando PDFs"],
  ["pdfs_adicionados", "PDFs adicionados"],
  ["texto_em_producao", "Texto em produção"],
  ["texto_pronto_revisao", "Texto pronto para revisão"],
  ["texto_revisado", "Texto revisado"],
  ["slides_em_producao", "Slides em produção"],
  ["pptx_pronto", "PPTX pronto"],
  ["revisao_final", "Revisão final"],
  ["concluida", "Concluída"],
  ["erro_bloqueada", "Erro / bloqueada"],
];

const ACTIONS = [
  { route: "avancar-etapa", label: "Avançar etapa" },
  { route: "voltar-etapa", label: "Voltar etapa" },
  { route: "gerar-bibliografia", label: "Gerar bibliografia" },
  { route: "aprovar-bibliografia", label: "Aprovar bibliografia" },
  { route: "marcar-pdfs", label: "Marcar PDFs" },
  { route: "gerar-texto", label: "Gerar texto" },
  { route: "enviar-revisao", label: "Enviar revisão" },
  { route: "gerar-pptx", label: "Gerar PPTX" },
  { route: "concluir", label: "Concluir" },
  { route: "abrir-pasta", label: "Abrir pasta" },
];

const NEXT_ACTION_TO_ROUTE = {
  "Gerar bibliografia": "gerar-bibliografia",
  "Aprovar bibliografia": "aprovar-bibliografia",
  "Marcar PDFs como baixados": "marcar-pdfs",
  "Gerar texto da aula": "gerar-texto",
  "Enviar para revisão": "enviar-revisao",
  "Gerar PPTX": "gerar-pptx",
  "Marcar como concluída": "concluir",
  "Concluída": "concluir",
  "Resolver bloqueio": "abrir-pasta",
};

let state = null;
let columns = [];
let selectedAulaId = null;
const driveFilesCache = new Map();
let apiAvailable = true;
let filters = {
  module: "",
  theme: "",
};

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

    if (!stateRes.ok || !colRes.ok) {
      throw new Error("Falha ao carregar dados do backend.");
    }

    state = await stateRes.json();
    const colJson = await colRes.json();
    columns = colJson.columns || [];
    populateModuleFilter();

    renderStats();
    renderBoard();

    if (selectedAulaId) {
      openDetail(selectedAulaId);
    }

    if (showMessage) showToast("Kanban sincronizado.");
  } catch (err) {
    const loadedFallback = await tryLoadStaticFallback();
    if (!loadedFallback) {
      showToast(`Erro: ${err.message}`, true);
    }
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
  const concluidas = filtered.filter((a) => a.status === "concluida").length;
  const bloqueadas = filtered.filter((a) => a.status === "erro_bloqueada").length;

  statsEl.innerHTML = `
    <div class="stat"><strong>${total}</strong><span>Aulas exibidas</span></div>
    <div class="stat"><strong>${all.length}</strong><span>Aulas totais</span></div>
    <div class="stat"><strong>${concluidas}</strong><span>Concluídas</span></div>
    <div class="stat"><strong>${bloqueadas}</strong><span>Bloqueadas</span></div>
    <div class="stat"><strong>${new Date(state.updated_at).toLocaleString("pt-BR")}</strong><span>Última sincronização</span></div>
  `;
}

function renderBoard() {
  boardEl.innerHTML = "";
  const filteredAulas = getFilteredAulas();

  for (const [statusKey, statusLabel] of columns) {
    const aulas = filteredAulas.filter((a) => a.status === statusKey);

    const column = document.createElement("section");
    column.className = "column";
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
      for (const aula of aulas) {
        cardsEl.appendChild(buildCard(aula));
      }
    }

    boardEl.appendChild(column);
  }
}

function buildCard(aula) {
  const card = document.createElement("article");
  card.className = "card";

  const nextRoute = NEXT_ACTION_TO_ROUTE[aula.proxima_acao] || "abrir-pasta";
  const nextClass = getActionColorClass(nextRoute);

  card.innerHTML = `
    <div class="card-head">
      <p class="kicker">M${aula.modulo_num} · Aula ${aula.aula_num}</p>
      <h4>${escapeHtml(aula.aula_tema)}</h4>
      <p class="module">${escapeHtml(aula.modulo_nome)}</p>
    </div>

    <ul class="meta">
      <li><strong>Próxima ação:</strong> ${escapeHtml(aula.proxima_acao)}</li>
      <li><strong>PDFs:</strong> ${aula.pdfs.baixados}/${aula.pdfs.total}</li>
    </ul>

    <div class="card-actions">
      <button class="btn next-action ${nextClass}" data-action="${nextRoute}" data-id="${aula.id}">Executar próxima ação</button>
      <button class="btn secondary" data-action="voltar-etapa" data-id="${aula.id}">Voltar etapa</button>
      <button class="btn secondary" data-drive-action="list-files" data-id="${aula.id}">Listar Drive</button>
      <button class="btn secondary" data-drive-action="upload-file" data-id="${aula.id}">Upload Drive</button>
      <button class="btn secondary" data-detail="${aula.id}">Detalhes</button>
    </div>
  `;

  card.querySelector("[data-detail]").addEventListener("click", () => openDetail(aula.id));
  card.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const target = e.currentTarget;
      await runAction(target.dataset.id, target.dataset.action);
    });
  });
  card.querySelectorAll("[data-drive-action]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const target = e.currentTarget;
      await handleDriveAction(target.dataset.id, target.dataset.driveAction, true);
    });
  });

  return card;
}

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
      const target = e.currentTarget;
      await runAction(aula.id, target.dataset.action);
    });
  }
  for (const btn of detailContentEl.querySelectorAll("[data-drive-action]")) {
    btn.addEventListener("click", async (e) => {
      const target = e.currentTarget;
      await handleDriveAction(aula.id, target.dataset.driveAction, false);
    });
  }
  const openAllBtn = detailContentEl.querySelector("[data-open-all]");
  if (openAllBtn) {
    openAllBtn.addEventListener("click", () => {
      openLinksInTabs(collectAllLinks(aula));
    });
  }
  for (const btn of detailContentEl.querySelectorAll("[data-open-group]")) {
    btn.addEventListener("click", (e) => {
      const target = e.currentTarget;
      const groupKey = target.dataset.openGroup;
      const groups = collectBibliografiaGroups(aula);
      const grupo = groups.find((g) => g.key === groupKey);
      openLinksInTabs(grupo ? grupo.links : []);
    });
  }
}

function buildDetailHtml(aula) {
  const pendencias = aula.pendencias?.length
    ? `<ul>${aula.pendencias.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>`
    : "<p>Sem pendências registradas.</p>";

  const pdfs = aula.pdfs.nomes?.length
    ? `<ul>${aula.pdfs.nomes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>`
    : "<p>Nenhum PDF encontrado na pasta 03_pdfs_artigos.</p>";

  const nextRoute = NEXT_ACTION_TO_ROUTE[aula.proxima_acao] || "abrir-pasta";
  const actions = ACTIONS.map((a) => {
    const isNext = a.route === nextRoute;
    const cls = isNext ? `btn next-action ${getActionColorClass(a.route)}` : "btn secondary";
    return `<button class="${cls}" data-action="${a.route}">${a.label}</button>`;
  }).join("");

  const preview = aula.texto_preview
    ? `<p class="preview">${escapeHtml(aula.texto_preview)}</p>`
    : "<p>Texto da aula ainda não disponível.</p>";
  const aiArtifacts = aula.ai_artifacts && Object.keys(aula.ai_artifacts).length
    ? `<ul>${Object.keys(aula.ai_artifacts).map((k) => `<li>${escapeHtml(k)}</li>`).join("")}</ul>`
    : "<p>Nenhum artefato de IA salvo.</p>";
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
      <h3>Preview do texto</h3>
      ${preview}
    </section>

    ${bibliografia}

    <section>
      <h3>Artefatos IA</h3>
      ${aiArtifacts}
    </section>

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
      <h3>Ações</h3>
      <div class="actions-wrap">${actions}</div>
    </section>
  `;
}

async function runAction(aulaId, route) {
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

    if (!res.ok || !payload.ok) {
      throw new Error(payload.message || payload.detail || "Falha na ação");
    }

    showToast(payload.message || "Ação executada.");
    await loadAll(false);
  } catch (err) {
    showToast(`Erro: ${err.message}`, true);
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
    if (!res.ok || !payload.ok) {
      throw new Error(payload.message || payload.detail || "Falha no bootstrap do Drive");
    }
    const summary = payload.summary || {};
    showToast(
      `Drive bootstrap OK: módulos ${summary.modules_touched ?? 0}, aulas ${summary.aulas_touched ?? 0}.`
    );
    await loadAll(false);
  } catch (err) {
    showToast(`Erro Drive bootstrap: ${err.message}`, true);
  } finally {
    driveBootstrapBtn.disabled = false;
  }
}

async function handleDriveAction(aulaId, driveAction, fromCard = false) {
  if (driveAction === "list-files") {
    await listDriveFilesForAula(aulaId, fromCard);
    return;
  }
  if (driveAction === "upload-file") {
    await uploadFileForAula(aulaId, fromCard);
    return;
  }
}

async function listDriveFilesForAula(aulaId, forceOpenDetail = false) {
  if (!apiAvailable) {
    showToast("Drive indisponível no GitHub Pages (modo somente leitura).", true);
    return;
  }
  if (forceOpenDetail && selectedAulaId !== aulaId) {
    openDetail(aulaId);
  }
  try {
    const res = await fetch(`${API_BASE}/api/aulas/${encodeURIComponent(aulaId)}/drive-files`);
    const payload = await res.json();
    if (!res.ok || !payload.ok) {
      throw new Error(payload.message || payload.detail || "Falha ao listar arquivos no Drive");
    }

    driveFilesCache.set(aulaId, payload.files || []);
    showToast(`Drive: ${payload.count ?? (payload.files || []).length} arquivo(s) para ${aulaId}.`);

    if (selectedAulaId === aulaId) {
      openDetail(aulaId);
    }
  } catch (err) {
    showToast(`Erro listagem Drive: ${err.message}`, true);
  }
}

async function uploadFileForAula(aulaId, forceOpenDetail = false) {
  if (!apiAvailable) {
    showToast("Upload indisponível no GitHub Pages (modo somente leitura).", true);
    return;
  }
  const aula = state?.aulas?.find((item) => item.id === aulaId);
  if (!aula) {
    showToast("Aula não encontrada no estado atual.", true);
    return;
  }
  if (forceOpenDetail && selectedAulaId !== aulaId) {
    openDetail(aulaId);
  }

  const file = await pickFileFromUser();
  if (!file) return;

  const targetSubfolder = window.prompt(
    "Subpasta destino no Drive (opcional): 01_bibliografia, 02_livros_extraidos, 03_pdfs_artigos. Deixe vazio para raiz da aula.",
    ""
  );
  if (targetSubfolder === null) return;

  const targetName = window.prompt(
    "Nome final do arquivo no Drive (opcional). Deixe vazio para usar o nome local.",
    ""
  );
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
    if (!res.ok || !data.ok) {
      throw new Error(data.message || data.detail || "Falha no upload para Drive");
    }
    const uploadedName = data.file?.name || "arquivo";
    showToast(`Upload concluído (${aulaId}): ${uploadedName}`);
    await listDriveFilesForAula(aulaId, false);
  } catch (err) {
    showToast(`Erro upload Drive: ${err.message}`, true);
  }
}

const BIBLIO_SOURCES = [
  { key: "diretrizes_consensos.md", label: "Diretrizes e Consensos" },
  { key: "pubmed_busca.md", label: "PubMed" },
  { key: "uptodate.md", label: "UpToDate" },
  { key: "capitulos_livros.md", label: "Livros (capítulos extraídos)" },
];

function renderBibliografiaSection(aula) {
  const groups = collectBibliografiaGroups(aula);
  const total = groups.reduce((n, g) => n + g.links.length, 0);
  if (!total) {
    return "";
  }

  const groupsHtml = groups
    .filter((g) => g.links.length)
    .map((g) => {
      const items = g.links
        .map(
          (l, i) =>
            `<li><a href="${escapeAttr(l.url)}" target="_blank" rel="noopener">${escapeHtml(
              l.title || l.url
            )}</a>${l.meta ? ` <span class="biblio-meta">${escapeHtml(l.meta)}</span>` : ""}</li>`
        )
        .join("");
      return `
        <div class="biblio-group">
          <div class="biblio-group-head">
            <h4>${escapeHtml(g.label)} <span class="biblio-count">${g.links.length}</span></h4>
            <button class="btn ghost" data-open-group="${escapeAttr(g.key)}" data-id="${escapeAttr(
        aula.id
      )}">Abrir esta fonte</button>
          </div>
          <ul class="biblio-list">${items}</ul>
        </div>`;
    })
    .join("");

  return `
    <section class="biblio">
      <div class="biblio-head">
        <h3>Bibliografia · ${total} links</h3>
        <button class="btn primary" data-open-all="${escapeAttr(aula.id)}">Abrir todos os links</button>
      </div>
      <p class="biblio-hint">Cada link abre em uma nova aba. Se o navegador bloquear popups, permita-os para este site uma vez.</p>
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
    // Captura meta depois do link: " — 2024 · NEJM · Practice Guideline" ou " · PDF"
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
  for (const link of links) {
    const win = window.open(link.url, "_blank", "noopener");
    if (win) opened += 1;
    else blocked += 1;
  }
  if (blocked > 0) {
    showToast(
      `Abertas ${opened} de ${links.length} abas. ${blocked} bloqueadas pelo navegador — autorize popups para este site.`,
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
  if (!driveFilesCache.has(aulaId)) {
    return "<p>Sem listagem carregada. Clique em \"Listar arquivos Drive\".</p>";
  }
  const files = driveFilesCache.get(aulaId) || [];
  if (!files.length) {
    return "<p>Sem arquivos encontrados para esta aula no Drive.</p>";
  }

  const items = files
    .map((f) => {
      const parentLabel = f.parentLabel ? ` (${escapeHtml(f.parentLabel)})` : "";
      return `<li>${escapeHtml(f.name)}${parentLabel}</li>`;
    })
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
  showToast._timer = setTimeout(() => toastEl.classList.add("hidden"), 3200);
}

loadAll(false);

function getActionColorClass(route) {
  const map = {
    "gerar-bibliografia": "color-biblio",
    "aprovar-bibliografia": "color-aprovar",
    "marcar-pdfs": "color-pdf",
    "gerar-texto": "color-texto",
    "enviar-revisao": "color-revisao",
    "gerar-pptx": "color-pptx",
    "concluir": "color-concluir",
    "abrir-pasta": "color-pasta",
    "avancar-etapa": "color-avancar",
    "voltar-etapa": "color-voltar",
  };
  return map[route] || "color-default";
}

function populateModuleFilter() {
  const previous = moduleFilterEl.value;
  const moduleMap = new Map();
  for (const aula of state.aulas) {
    if (!moduleMap.has(aula.modulo_num)) {
      moduleMap.set(aula.modulo_num, aula.modulo_nome);
    }
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
    .replace(/[\u0300-\u036f]/g, "")
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

  if (window.location.hostname.endsWith("github.io")) {
    return defaultCloudRunApi;
  }
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
    if (selectedAulaId) {
      openDetail(selectedAulaId);
    }
    showToast("Modo GitHub Pages: visualização estática carregada.");
    return true;
  } catch (_) {
    return false;
  }
}
