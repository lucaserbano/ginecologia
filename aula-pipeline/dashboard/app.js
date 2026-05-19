const API_BASE = "";

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
const detailCloseBtn = document.getElementById("detail-close");
const moduleFilterEl = document.getElementById("filter-module");
const themeFilterEl = document.getElementById("filter-theme");
const clearFiltersBtn = document.getElementById("btn-clear-filters");

syncBtn.addEventListener("click", () => loadAll(true));
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
    showToast(`Erro: ${err.message}`, true);
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

    <section>
      <h3>Ações</h3>
      <div class="actions-wrap">${actions}</div>
    </section>
  `;
}

async function runAction(aulaId, route) {
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
