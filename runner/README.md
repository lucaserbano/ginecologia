# Runner local — Download de todos (Fase 1)

Roda **na máquina do Lucas** (não no Cloud Run), porque depende do agent-browser
logado no UpToDate. Faz polling no backend por jobs de download e baixa as
referências de cada aula, subindo os PDFs para a subpasta `03_pdfs_artigos` do
Drive da aula.

## Como funciona

1. No dashboard, na coluna **"Bibliografia pronta"**, você clica **"Download de todos"**.
2. Isso enfileira um job (`POST /api/aulas/{id}/job/download-pdfs`) — o backend só marca o job, não baixa nada.
3. Este runner, rodando aqui, vê o job pendente (`GET /api/jobs/pendentes`), baixa os PDFs e sobe pro Drive.
4. O dashboard mostra o progresso e a lista do que ficou para baixar manualmente.

O que é baixado automaticamente (decisão de projeto):
- **UpToDate** — via `~/agent-browser-automations/baixar_uptodate.py` (sessão logada).
- **PMC (PubMed open-access)** — abre a **página do artigo no PMC** no agent-browser e imprime para PDF (texto + figuras). Dispensa o endpoint `/pdf/` que costuma ser bloqueado.
- **PDFs diretos** (diretrizes/consensos com link `.pdf`, ex. FEBRASGO/MS) — download nativo do navegador (ou print, se for renderizado inline).
- **Diretrizes em HTML** (`outro`) — também são impressas para PDF via browser, com guarda contra páginas de login/paywall.
- **PubMed sem PMC** continua indo para download manual (texto completo atrás de paywall de editora).
- Capítulos de livro (`capitulos_livros.md`) são ignorados: já ficam em `02_livros_extraidos` no Drive.

> **Por que via agent-browser?** O navegador real (com cookies/JS/UA de verdade) fura
> bloqueios de bot que o download via HTTP simples toma — em especial no PMC. Para
> desligar e voltar ao HTTP, use `USE_BROWSER_PDF=0`.

A sessão de download usa um perfil próprio (`gineco-dl`), **sem login** — PMC e
diretrizes são conteúdo aberto. É separada do perfil logado do UpToDate.

## Pré-requisitos

1. Python 3 com `requests`:
   ```bash
   pip3 install -r runner/requirements.txt
   ```
2. **UpToDate logado** no perfil persistente do agent-browser (uma vez):
   ```bash
   agent-browser --session uptodate close || true
   agent-browser --session uptodate \
     --profile ~/agent-browser-automations/profiles/uptodate \
     --headed open https://www.uptodate.com/login
   # faça login na janela que abrir, depois valide:
   python3 ~/agent-browser-automations/verificar_login_uptodate.py
   ```

## Uso

```bash
# loop contínuo (deixe rodando em um terminal):
python3 runner/runner.py

# processa os jobs pendentes uma vez e sai:
python3 runner/runner.py --once

# força uma aula específica (enfileira + baixa na hora) — bom para testar:
python3 runner/runner.py --aula M10_A1
```

## Configuração (variáveis de ambiente, todas opcionais)

| Var | Default | Para quê |
|-----|---------|----------|
| `BACKEND_URL` | produção no Cloud Run | apontar para outro backend (ex.: local) |
| `UPTODATE_SCRIPT` | `~/agent-browser-automations/baixar_uptodate.py` | caminho do baixador do UpToDate |
| `POLL_INTERVAL` | `15` | segundos entre verificações de jobs |
| `NCBI_API_KEY` | (vazio) | acelera/eleva o rate limit do PubMed/PMC |
| `USE_BROWSER_PDF` | `1` | usa o agent-browser para PMC/diretrizes/PDFs diretos. `0` volta ao HTTP. |

## Fase 2 — Gerar texto do NotebookLM

O mesmo runner também processa o job **"Gerar texto do NotebookLM"** (botão na
coluna *PDFs baixados* do dashboard). O fluxo (`notebooklm_runner.py`):

1. Dashboard enfileira `POST /api/aulas/{id}/job/gerar-texto-notebooklm`
   (`tipo=gerar_texto_notebooklm`).
2. O runner cria **um notebook por aula**, nomeado `MX AY` (ex.: `M10 A1`).
3. Sobe como fontes **todos os PDFs** de `03_pdfs_artigos` (UpToDate + baixados
   manualmente) e `02_livros_extraidos` — via `notebooklm source add-drive` pelo
   file ID (mesma conta Google); se a CLI recusar, baixa o PDF do Drive
   (`GET .../drive-files/{id}/download`) e usa `source add`.
4. Sobe as **diretrizes de roteirização** (`aulas/templates/system_prompt_certo.md`)
   convertidas em PDF (fpdf2; fallback: text source).
5. Roda `notebooklm ask` com o prompt de `aulas/templates/prompt_certo.md` (o tema
   da aula substitui `[tema da aula aqui]`).
6. Cola o roteiro no kanban (`PUT /texto`) e avança `pdfs_baixados → texto_feito`.

### Pré-requisitos da Fase 2 (one-time)

```bash
pip install "notebooklm-py[browser]"
playwright install chromium
notebooklm auth check    # login Google: erbano.lho@gmail.com
```

Ver também `notebooklm-integration/SKILL.md`. Config: `NOTEBOOKLM_BIN` (default
`notebooklm`).

### Testar uma aula específica

```bash
# roda a geração do NotebookLM para uma aula em 'PDFs baixados':
python3 runner/runner.py --aula M10_A1 --notebooklm
```

## Lançadores de duplo-clique (.command) e outra máquina

Na pasta `runner/` há 4 atalhos `.command` (dois cliques no Finder). Eles se
localizam sozinhos — funcionam em qualquer Mac, sem editar caminhos:

| Arquivo | Para quê |
|---|---|
| `UpToDate - Fazer login` | login no UpToDate (agent-browser), 1ª vez / quando expira |
| `UpToDate - Baixar` | processa os jobs de download (`runner.py --once --only download_pdfs`) |
| `NotebookLM - Instalar (1a vez)` | setup do ambiente NotebookLM **uma vez por computador** (cria venv via `uv`, instala `notebooklm-py`, faz o login Google) |
| `NotebookLM - Gerar texto` | processa os jobs do NotebookLM (`runner.py --once --only gerar_texto_notebooklm`) usando o venv `~/.venvs/gineco-nlm` |

O flag `--only <tipo>` mantém os dois fluxos separados: UpToDate roda no Python do
sistema + agent-browser; NotebookLM roda no venv com `notebooklm-py`/`fpdf2`. Assim
um lançador nunca pega o job do outro.

**Para usar numa máquina nova** (a pasta chega pelo iCloud ou `git pull`):
1. UpToDate: dê o login (`UpToDate - Fazer login`) — o perfil do navegador é por
   máquina.
2. NotebookLM: rode `NotebookLM - Instalar (1a vez)` — faz tudo (ambiente + login
   Google `erbano.lho@gmail.com`). Depois é só `NotebookLM - Gerar texto`.

As autenticações (UpToDate e NotebookLM) e o ambiente Python **não** sincronizam
pelo iCloud: são por computador. Por isso o passo de instalar/logar é por máquina.

## Limitações conhecidas

- O runner não tenta burlar paywall. Texto completo de PubMed sem versão no PMC,
  e diretrizes que exigem login, ficam como **download manual**.
- A captura via browser é mais lenta que o HTTP (abre uma aba por referência);
  em compensação fura os bloqueios. ~10-30s por referência.
