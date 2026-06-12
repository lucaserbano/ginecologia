# Coordenador de Producao de Aulas Medicas (Ginecologia)

Voce e o coordenador do pipeline de producao de aulas medicas.
Seu objetivo e conduzir o fluxo de ponta a ponta sem inventar referencias.

## Regras gerais
- Nunca invente referencias, dados, DOI, PMID, links ou citacoes.
- Marque toda afirmacao clinica relevante com fonte rastreavel.
- Sempre sinalize quando faltar PDF, fonte ou dado.
- Trabalhe em lotes pequenos: 1 modulo por vez, 1 aula por vez.
- Nao avance de fase sem criterio minimo de qualidade da fase anterior.

## Estrutura esperada
- `agents/` (prompts dos agentes: curador-diretrizes-consensos, buscador-pubmed, curador-uptodate, indexador-livros, redator-aula, montador-pptx)
- `aulas/temas.md`
- `aulas/templates/system_prompt_aula.md`
- `aulas_em_producao/modulos/<modulo>/<aula>/`
- `aula-pipeline/` (Kanban: backend FastAPI + dashboard estatico)

## Pastas por aula
Cada aula deve conter:
- `00_briefing.yaml`
- `01_bibliografia/` (pubmed_busca.md, uptodate.md, diretrizes_consensos.md, capitulos_livros.md, 01_bibliografia.md)
- `02_livros_extraidos/`
- `03_pdfs_artigos/`
- `04_aula_texto.md` (ou subpasta `04_aula_texto/`)

No Google Drive a estrutura espelha esse layout por subpastas (`01_bibliografia`, `02_livros_extraidos`, `03_pdfs_artigos`, `04_aula_texto`). O `.pptx` montado NAO fica na pasta da aula: vai para subpastas do modulo (irmas das pastas de aula) - `pptx sem imagens` quando e gerado, e `pptx prontos` quando o coordenador conclui as imagens. O arquivo chama-se `M{X}_A{Y}.pptx`.

## Pipeline (fases / colunas do Kanban)
Colunas (status interno → label):
1. `proximas_aulas` → "Proximas aulas"
2. `bibliografia_em_geracao` → "Bibliografia em geracao"
3. `bibliografia_pronta` → "Bibliografia pronta para download"
4. `pdfs_baixados` → "PDFs baixados"
5. `texto_feito` → "Texto feito" (NotebookLM colado no kanban)
6. `texto_editado` → "Texto editado" (edicao inline pelo coordenador)
7. `pptx_gerado` → "PPTX gerado" (.pptx montado, na subpasta `pptx sem imagens`)
8. `pptx_finalizado` → "PPTX pronto" (imagens adicionadas, .pptx em `pptx prontos`) — estado final

Mais o estado lateral `erro_bloqueada` para falhas.

**Fase 1 - Bibliografia (assincrona)**: acionar `gerar-bibliografia`. Flipa para `bibliografia_em_geracao` imediatamente e dispara `BackgroundTasks` que gera `pubmed_busca.md`, `uptodate.md`, `diretrizes_consensos.md`, `capitulos_livros.md`, `01_bibliografia.md` e extrai capitulos para `02_livros_extraidos`. Progresso aparece em `aula.progresso`. Ao terminar, marca `bibliografia_pronta`. Erro -> `erro_bloqueada`.
   - Queries por Gemini (`_generate_search_terms`).
   - PubMed: filtro `humans + 2019-3000 + (review|meta-analysis|RCT|practice guideline)`, limite 5, fallback sem filtro se < 3.
   - UpToDate: ate 5 links `/contents/`, ranker prioriza diagnosis/treatment/manifestations. A busca usa `domain_search`, que cai para DuckDuckGo quando o Google CSE retorna vazio (o CSE so indexa os sites cadastrados nele - FEBRASGO/MS - entao para uptodate.com sempre cairia vazio).
   - Diretrizes nacionais (FEBRASGO/MS): Google CSE + fallback DuckDuckGo. Internacionais: Gemini com **Grounding (Google Search)** quando `ENABLE_GEMINI_GROUNDING=1` (default) - o modelo busca na web em vez de recorrer a memoria; depois `_validate_url` confere cada URL. Limite total 8.

**Fase 2 - Download e texto base (Eduardo)**: assistente baixa as referencias (botao "Abrir todos os links" no card). Status: `bibliografia_pronta` -> `pdfs_baixados`. Em seguida, o botao **"Gerar texto do NotebookLM"** (coluna *PDFs baixados*) enfileira um job `tipo=gerar_texto_notebooklm` (`POST /api/aulas/{id}/job/gerar-texto-notebooklm`) que o **runner local** processa (`runner/notebooklm_runner.py`): cria **um notebook por aula** (`MX AY`, ex. `M10 A1`), sobe como fontes **todos os PDFs** de `03_pdfs_artigos` (UpToDate + baixados manualmente) e `02_livros_extraidos` — via `source add-drive` por file ID (mesma conta Google `erbano.lho@gmail.com`), com fallback baixando o PDF do Drive (`GET .../drive-files/{id}/download`) -, sobe as **diretrizes de roteirizacao** (`aulas/templates/system_prompt_certo.md` em PDF; fallback text source), roda `notebooklm ask` com o prompt `aulas/templates/prompt_certo.md` (tema da aula substitui `[tema da aula aqui]`), cola o roteiro via `PUT /api/aulas/{id}/texto` e avanca `pdfs_baixados -> texto_feito` (`salvar-texto-inicial`). O botao manual "Colar/editar manualmente" (editor inline) segue como fallback. O NotebookLM e browser/login-bound (CLI `notebooklm-py`), por isso roda no runner local, **nao** no Cloud Run. Texto vai direto pro Drive em `04_aula_texto/04_aula_texto.md`.

**Fase 3 - Edicao (coordenador)**: editor inline no kanban. Mesma rota `PUT /texto` para salvar. Ao concluir edicao, status `texto_feito` -> `texto_editado`.

**Fase 4 - PPTX**: `gerar-pptx` monta o `.pptx` real a partir do template `aulas/templates/MX AY.pptx` e do texto editado, e marca `pptx_gerado`. Logica em `pptx_builder.py`: o texto e dividido em blocos por linhas de hifens (`-----`); o slide 1 e a capa (troca so numero/nome de modulo e aula); cada bloco vira uma copia do slide 2 com o texto colado na caixa a direita (fonte fixa 14pt). Um **slide final de referencias** e compilado automaticamente dos 4 `.md` curados de `01_bibliografia` (diretrizes, PubMed, UpToDate, capitulos de livros) — titulo + URL por referencia, fonte auto-ajustada (9-12pt) para caber. O arquivo `M{X}_A{Y}.pptx` e salvo na subpasta `pptx sem imagens` do modulo. O coordenador baixa, adiciona imagens manualmente e clica "Imagens prontas" (`marcar-imagens-prontas`): isso move o `.pptx` de `pptx sem imagens` para `pptx prontos` e marca `pptx_finalizado` — **etapa final do pipeline**.

Agentes-prompt usados pelas acoes:
- `@curador-diretrizes-consensos.md`, `@buscador-pubmed.md`, `@curador-uptodate.md`, `@indexador-livros.md`
- `@redator-aula.md` e `@montador-pptx.md` continuam no repo mas nao sao mais usados no fluxo atual (texto vem do NotebookLM; o `.pptx` e montado por `pptx_builder.py` a partir do template, sem IA).

## Criterios de saida
- Referencias validadas e justificadas.
- Texto editado com fluxo didatico, decisoes clinicas e citacoes.
- `.pptx` montado a partir do template, um slide por bloco de texto.
- PPTX final com imagens, na subpasta `pptx prontos` do modulo.

---

# Arquitetura e Infraestrutura (handoff)

## Stack atual
- **Backend**: FastAPI em Cloud Run (servico `gineco-api`, regiao `us-central1`).
- **Frontend**: GitHub Pages em `https://lucaserbano.github.io/ginecologia/`.
- **Storage de artefatos**: Google Drive (uma pasta por aula com subpastas padronizadas).
- **Estado**: **Firestore Native** (collection `aulas`, 1 doc por aula). `aulas.json` no repo serve apenas como seed inicial: na primeira invocacao com Firestore vazio, `store.load_state` migra o JSON para o Firestore (one-shot). Daí em diante todas as leituras/escritas vão pelo Firestore — sobrevive a deploys.
- **IA**: Vertex AI / Gemini 2.5 Flash (backend `vertex`). Fallback opcional: OpenRouter.

## URLs e identificadores
- Servico Cloud Run: `gineco-api`
- URL backend: `https://gineco-api-468351448933.us-central1.run.app`
- Revisao ativa de referencia: `gineco-api-00012-v24` (a revisao real muda a cada deploy; consultar `gcloud run revisions list`).
- Projeto GCP: `project-5ca1d427-8a03-4908-8cb`
- Pasta Drive de livros-base: `BOOKS_DRIVE_FOLDER_ID=1MfyJgRryqhSfj0cp0K3OX0ATkFfRZsiN`
- Pasta raiz Drive de aulas: configurada via `DRIVE_ROOT_FOLDER_ID` (real, nao placeholder).

## Autenticacao Google Drive (IMPORTANTE)
Modo em producao: **OAuth de usuario** (conta pessoal `erbano.lho@gmail.com`).

Motivo: Service Account em Drive pessoal retorna `403 storageQuotaExceeded`. So funcionaria com Shared Drive. Por isso migramos para OAuth de usuario, armazenando o JSON do client e do token no Google Secret Manager:
- `gineco-oauth-client` -> exposto no container como env `GOOGLE_OAUTH_CLIENT_SECRET_JSON`
- `gineco-oauth-token` -> exposto no container como env `GOOGLE_OAUTH_TOKEN_JSON`

O backend (`drive_client.py`) le esses JSONs do ambiente, faz refresh em memoria quando expirado e tolera filesystem read-only (Secret Manager monta como read-only) - o refresh em memoria basta para a requisicao corrente.

**Mapa de projetos GCP (cuidado - sao DOIS projetos):**
- O OAuth client do Drive (`client_id` comeca com `475149657197-...`) vive no projeto de **numero `475149657197`**. E nesse projeto que fica a **tela de consentimento OAuth** (menu "Google Auth Platform" -> "Publico-alvo"). Link direto: `https://console.cloud.google.com/auth/audience?project=475149657197`.
- O backend (Cloud Run, Firestore, Vertex, Secret Manager) roda no projeto `project-5ca1d427-8a03-4908-8cb`, **numero `468351448933`** (visivel na URL `gineco-api-468351448933...`).
- Ao procurar a tela de consentimento, NAO use "My First Project" nem o projeto do backend - selecione o de numero `475149657197`.

**A tela de consentimento DEVE ficar em "Em producao" (In production), nao em "Testing".** Em modo Testing o Google revoga o refresh token a cada ~7 dias, gerando `invalid_grant: Token has been expired or revoked` (aparece, p.ex., ao salvar o PPTX no Drive). Publicada em producao, o refresh token para de expirar.

### Rotacao de token OAuth revogado (`invalid_grant`)
Quando aparecer `invalid_grant: Token has been expired or revoked`, o refresh token foi invalidado e o refresh em memoria nao resolve - e preciso gerar token novo e rotacionar o secret:

1. **Gerar token novo localmente** (precisa de browser p/ login com `erbano.lho@gmail.com`). Em venv com `google-auth-oauthlib`, rodar `InstalledAppFlow.from_client_secrets_file(credentials/oauth_client.json, ["https://www.googleapis.com/auth/drive"])` com `run_local_server(access_type="offline", prompt="consent")` -> salva o `creds.to_json()` (uma linha). `access_type=offline + prompt=consent` garante refresh_token novo.
2. **Validar** antes de subir: `creds.refresh(Request())` deve passar e um GET em `drive/v3/about?fields=user(emailAddress)` deve retornar 200 com a conta certa.
3. **Rotacionar o secret** `gineco-oauth-token` (projeto do backend): Secret Manager -> `+ NEW VERSION`. **Faca UPLOAD do arquivo .json**, nao cole no campo de texto - colar costuma inserir quebra de linha e gera `GOOGLE_OAUTH_TOKEN_JSON invalido: Invalid control character`.
4. **Redeploy** do Cloud Run (o env do secret so e lido quando a instancia sobe): `gineco-api` -> nova revisao (pega `latest`).
5. **Verificar**: `curl .../api/drive/status` deve retornar `{"ok":true,"authorized":true}`.
6. Se a tela de consentimento ainda estiver em Testing, **publique para producao** (ver acima) - senao volta a quebrar em ~7 dias.

Substituir tambem a copia local `credentials/token.json` pelo token novo mantem o ambiente local funcionando. Nunca deixar copias soltas do token (Desktop/tmp): contem refresh token valido.

## Variaveis de ambiente do Cloud Run
Conjunto minimo esperado:
- `ENABLE_AI_ACTIONS=1`
- `AI_BACKEND=vertex`
- `VERTEX_PROJECT_ID=project-5ca1d427-8a03-4908-8cb`
- `VERTEX_LOCATION=us-central1`
- `VERTEX_MODEL=gemini-2.5-pro` (atual; Flash deu 0 validadas em diretrizes internacionais por falta de memoria das URLs canonicas)
- `GOOGLE_DRIVE_AUTH_MODE=oauth` (NAO usar `service_account` no Drive pessoal)
- `DRIVE_ROOT_FOLDER_ID=<id real da pasta raiz>`
- `BOOKS_DRIVE_FOLDER_ID=1MfyJgRryqhSfj0cp0K3OX0ATkFfRZsiN`
- `GOOGLE_OAUTH_CLIENT_SECRET_JSON` (via Secret Manager: `gineco-oauth-client`)
- `GOOGLE_OAUTH_TOKEN_JSON` (via Secret Manager: `gineco-oauth-token`)
- `ALLOWED_ORIGINS=https://lucaserbano.github.io`
- `OPEN_FOLDER_ACTION_ENABLED=0`
- `ENABLE_FIRESTORE=1` (default; setar `0` apenas para forçar fallback ao JSON local)
- `ENABLE_GEMINI_GROUNDING=1` (default; grounding com Google Search nas diretrizes internacionais — setar `0` desativa)
- `FIRESTORE_PROJECT_ID=project-5ca1d427-8a03-4908-8cb` (default: usa VERTEX_PROJECT_ID)
- `FIRESTORE_COLLECTION=aulas` (default)

## Deploy
Sempre buildar a partir da raiz do repositorio (Dockerfile na raiz, traz `agents/` e `aulas/templates/` para a imagem):
```bash
# rodar a partir da raiz do repo (o caminho varia por maquina; o projeto roda em
# 2 maquinas - ex.: ~/Downloads/GINECOLOGIA - AFYA e ~/Library/.../iCloud/.../GINECOLOGIA - AFYA)
cd "<raiz do repo>"
gcloud run deploy gineco-api --source . --project project-5ca1d427-8a03-4908-8cb --region us-central1
```
Obs.: nem toda maquina tem o `gcloud` instalado. Sem `gcloud`, da pra fazer deploy e rotacao de secret pelo Console web (Cloud Run -> "Edit & deploy new revision"; Secret Manager -> "New version").

Update de envs sem rebuild completo:
```bash
gcloud run services update gineco-api \
  --project project-5ca1d427-8a03-4908-8cb \
  --region us-central1 \
  --set-env-vars ENABLE_AI_ACTIONS=1,AI_BACKEND=vertex,VERTEX_MODEL=gemini-2.5-flash
```

## Endpoints chave
- `GET /api/aulas` / `GET /api/aulas/{id}`
- `GET /api/aulas/{id}/texto` / `PUT /api/aulas/{id}/texto` (le/grava `04_aula_texto.md` no Drive)
- Acoes: `POST /api/aulas/{id}/actions/{gerar-bibliografia|marcar-pdfs-baixados|salvar-texto-inicial|concluir-edicao|gerar-pptx|marcar-imagens-prontas|avancar-etapa|voltar-etapa|abrir-pasta}`
- Drive: `GET /api/drive/status`, `POST /api/drive/auth-start`, `POST /api/drive/bootstrap?force_relink=true&max_aulas=50`
- Upload por aula: `POST /api/aulas/{id}/upload`, `POST /api/aulas/{id}/upload-browser` (multipart)
- Jobs do runner local: `POST /api/aulas/{id}/job/download-pdfs`, `POST /api/aulas/{id}/job/gerar-texto-notebooklm`, `PUT /api/aulas/{id}/job`, `GET /api/jobs/pendentes`
- Fontes do NotebookLM: `GET /api/aulas/{id}/drive-files`, `GET /api/aulas/{id}/drive-files/{file_id}/download` (bytes do PDF; fallback de ingestao)

`bootstrap` aceita `force_relink` (religa pastas ao novo root, util em migracao para Shared Drive) e `max_aulas` (lote anti-timeout).

## Maquina dos status (referencia rapida)
`proximas_aulas` -(gerar_bibliografia, async)-> `bibliografia_em_geracao` -(worker termina)-> `bibliografia_pronta` -(marcar_pdfs_baixados)-> `pdfs_baixados` -(salvar_texto_inicial, ou seja, primeiro PUT /texto + acao)-> `texto_feito` -(concluir_edicao)-> `texto_editado` -(gerar_pptx, monta o .pptx)-> `pptx_gerado` -(marcar_imagens_prontas, move .pptx p/ `pptx prontos`)-> `pptx_finalizado` (estado final).

Detalhes em `aula-pipeline/backend/schemas.py` (`NEXT_ACTION_BY_STATUS`).

## Pontos de cuidado
- Texto da aula e a Bibliografia tentam ler artefato do Drive primeiro (`drive_artifacts.py`) e so caem para estado interno se nao houver no Drive. Isso evita perder contexto quando o container reinicia.
- Token de OAuth pode expirar; refresh em memoria funciona automaticamente, mas se o refresh token for revogado (`invalid_grant`) e preciso rotacionar o secret `gineco-oauth-token` - ver "Rotacao de token OAuth revogado" na secao de Autenticacao Google Drive. Causa raiz mais comum: tela de consentimento em modo Testing (revoga a cada ~7 dias) - manter "Em producao".
- Nunca commitar `aula-pipeline/backend/credentials/`, `*.env`, `token.json`. Ja ignorado em `.gitignore`.
- PDFs/PPTX nao vao para o git (ja ignorados); ficam apenas no Drive.
- **Gemini 2.5 Pro NAO aceita `thinkingBudget=0`** (so >= 128). `openrouter_client._resolve_thinking_budget` ignora a requisicao silenciosamente quando o modelo atual e Pro. Se mudar para Flash/Flash-Lite no futuro, o `thinking_budget=0` volta a ser respeitado.
- Google CSE: a Custom Search JSON API exige a chave API criada via Console GCP (a CLI `gcloud alpha services api-keys create` cria keys com 403 persistente). CSE precisa de sites cadastrados em "Sites para pesquisar" (modo "Search the entire web" foi descontinuado).

## Estado da sessao 2026-05-22
- Fase 4 reescrita: `gerar-pptx` monta o `.pptx` real (`pptx_builder.py`) a partir do template `aulas/templates/MX AY.pptx`; nao gera mais outline. Dependencia nova: `python-pptx`.
- `.pptx` salvo na subpasta `pptx sem imagens` do modulo; `marcar-imagens-prontas` move para `pptx prontos` e e a etapa final.
- Status `pptx_na_pasta_final` e a acao `mover-pptx-final` removidos; pipeline passou de 9 para 8 colunas. `pptx_finalizado` virou o estado final ("PPTX pronto").
- M10_A1 e M10_A2 montadas e validadas em producao (fonte 14pt, capa com nomes acentuados corrigidos no Firestore).
- Dashboard so reflete em GitHub Pages apos `git push` na branch `main` (Pages serve `aula-pipeline/dashboard/` deste repo).

## Proximos passos sugeridos
1. Validar Fase 2 em producao: gerar texto via NotebookLM e conferir `04_aula_texto.md` no Drive.
2. Adicionar feedback de progresso/erro por etapa no frontend.
3. Avaliar auto-ajuste de fonte/quebra quando um bloco de texto for muito longo (hoje a fonte e fixa em 14pt e a caixa cresce com `spAutoFit`).
