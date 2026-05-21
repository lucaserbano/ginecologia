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
- `05_outline_slides.md` (ou subpasta `05_outline_slides/`)
- `M{X}_A{Y}.pptx`

No Google Drive a estrutura espelha esse layout por subpastas (`01_bibliografia`, `02_livros_extraidos`, `03_pdfs_artigos`, `04_aula_texto`, `05_outline_slides`). Quando a aula avanca para "PPTX na pasta final", o `.pptx` e movido para a subpasta `PPTX finais` do modulo (irma das pastas de aula).

## Pipeline (fases / colunas do Kanban)
Colunas (status interno → label):
1. `proximas_aulas` → "Proximas aulas"
2. `bibliografia_em_geracao` → "Bibliografia em geracao"
3. `bibliografia_pronta` → "Bibliografia pronta para download"
4. `pdfs_baixados` → "PDFs baixados"
5. `texto_feito` → "Texto feito" (NotebookLM colado no kanban)
6. `texto_editado` → "Texto editado" (edicao inline pelo coordenador)
7. `pptx_gerado` → "PPTX gerado" (outline gerado por IA)
8. `pptx_finalizado` → "PPTX finalizado" (imagens adicionadas)
9. `pptx_na_pasta_final` → "PPTX na pasta final" (movido para "PPTX finais" do modulo)

Mais o estado lateral `erro_bloqueada` para falhas.

**Fase 1 - Bibliografia (assincrona)**: acionar `gerar-bibliografia`. Flipa para `bibliografia_em_geracao` imediatamente e dispara `BackgroundTasks` que gera `pubmed_busca.md`, `uptodate.md`, `diretrizes_consensos.md`, `capitulos_livros.md`, `01_bibliografia.md` e extrai capitulos para `02_livros_extraidos`. Progresso aparece em `aula.progresso`. Ao terminar, marca `bibliografia_pronta`. Erro -> `erro_bloqueada`.
   - Queries por Gemini (`_generate_search_terms`).
   - PubMed: filtro `humans + 2019-3000 + (review|meta-analysis|RCT|practice guideline)`, limite 5, fallback sem filtro se < 3.
   - UpToDate: 3 links `/contents/`, ranker prioriza diagnosis/treatment/manifestations.
   - Diretrizes nacionais (FEBRASGO/MS): Google CSE + fallback DuckDuckGo. Internacionais: Gemini Pro + `_validate_url`. Limite total 8.

**Fase 2 - Download e texto base (Eduardo)**: assistente baixa as referencias (botao "Abrir todos os links" no card), gera texto no NotebookLM, cola no editor do kanban e salva. Status: `bibliografia_pronta` -> `pdfs_baixados` -> `texto_feito`. Texto vai direto pro Drive em `04_aula_texto/04_aula_texto.md` via `PUT /api/aulas/{id}/texto`.

**Fase 3 - Edicao (coordenador)**: editor inline no kanban. Mesma rota `PUT /texto` para salvar. Ao concluir edicao, status `texto_feito` -> `texto_editado`.

**Fase 4 - PPTX**: `gerar-pptx` gera `05_outline_slides.md` a partir do texto editado e marca `pptx_gerado` (outline-only por enquanto; montagem `.pptx` real ainda nao implementada). Coordenador adiciona imagens manualmente no .pptx e clica "Marcar imagens prontas" (`pptx_finalizado`). Finalmente "Mover para pasta final" move o .pptx para a subpasta `PPTX finais` do modulo no Drive (`pptx_na_pasta_final`).

Agentes-prompt usados pelas acoes:
- `@curador-diretrizes-consensos.md`, `@buscador-pubmed.md`, `@curador-uptodate.md`, `@indexador-livros.md`, `@montador-pptx.md`
- `@redator-aula.md` ainda esta no repo, mas nao e mais usado no fluxo atual (texto vem do NotebookLM).

## Criterios de saida
- Referencias validadas e justificadas.
- Texto editado com fluxo didatico, decisoes clinicas e citacoes.
- Outline de slides coerente com objetivo da aula.
- PPTX final com imagens e dentro do limite de slides definido no briefing.

---

# Arquitetura e Infraestrutura (handoff)

## Stack atual
- **Backend**: FastAPI em Cloud Run (servico `gineco-api`, regiao `us-central1`).
- **Frontend**: GitHub Pages em `https://lucaserbano.github.io/ginecologia/`.
- **Storage de artefatos**: Google Drive (uma pasta por aula com subpastas padronizadas).
- **Estado**: `aula-pipeline/data/aulas.json` (in-container; ainda nao persistido entre revisoes - migrar para Firestore/Cloud SQL e o proximo passo).
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

## Deploy
Sempre buildar a partir da raiz do repositorio (Dockerfile na raiz, traz `agents/` e `aulas/templates/` para a imagem):
```bash
cd "/Users/lucas/Downloads/GINECOLOGIA - AFYA"
gcloud run deploy gineco-api --source . --project project-5ca1d427-8a03-4908-8cb --region us-central1
```

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
- Acoes: `POST /api/aulas/{id}/actions/{gerar-bibliografia|marcar-pdfs-baixados|salvar-texto-inicial|concluir-edicao|gerar-pptx|marcar-imagens-prontas|mover-pptx-final|avancar-etapa|voltar-etapa|abrir-pasta}`
- Drive: `GET /api/drive/status`, `POST /api/drive/auth-start`, `POST /api/drive/bootstrap?force_relink=true&max_aulas=50`
- Upload por aula: `POST /api/aulas/{id}/upload`, `POST /api/aulas/{id}/upload-browser` (multipart)

`bootstrap` aceita `force_relink` (religa pastas ao novo root, util em migracao para Shared Drive) e `max_aulas` (lote anti-timeout).

## Maquina dos status (referencia rapida)
`proximas_aulas` -(gerar_bibliografia, async)-> `bibliografia_em_geracao` -(worker termina)-> `bibliografia_pronta` -(marcar_pdfs_baixados)-> `pdfs_baixados` -(salvar_texto_inicial, ou seja, primeiro PUT /texto + acao)-> `texto_feito` -(concluir_edicao)-> `texto_editado` -(gerar_pptx)-> `pptx_gerado` -(marcar_imagens_prontas)-> `pptx_finalizado` -(mover_pptx_final)-> `pptx_na_pasta_final`.

Detalhes em `aula-pipeline/backend/schemas.py` (`NEXT_ACTION_BY_STATUS`).

## Pontos de cuidado
- Texto da aula e a Bibliografia tentam ler artefato do Drive primeiro (`drive_artifacts.py`) e so caem para estado interno se nao houver no Drive. Isso evita perder contexto quando o container reinicia.
- Token de OAuth pode expirar; refresh em memoria funciona automaticamente, mas se o refresh token for revogado e preciso rotacionar o secret `gineco-oauth-token`.
- Nunca commitar `aula-pipeline/backend/credentials/`, `*.env`, `token.json`. Ja ignorado em `.gitignore`.
- PDFs/PPTX nao vao para o git (ja ignorados); ficam apenas no Drive.
- **Gemini 2.5 Pro NAO aceita `thinkingBudget=0`** (so >= 128). `openrouter_client._resolve_thinking_budget` ignora a requisicao silenciosamente quando o modelo atual e Pro. Se mudar para Flash/Flash-Lite no futuro, o `thinking_budget=0` volta a ser respeitado.
- Google CSE: a Custom Search JSON API exige a chave API criada via Console GCP (a CLI `gcloud alpha services api-keys create` cria keys com 403 persistente). CSE precisa de sites cadastrados em "Sites para pesquisar" (modo "Search the entire web" foi descontinuado).

## Estado da sessao 2026-05-20
- Migracao para OAuth de usuario concluida; storageQuotaExceeded resolvido.
- Fase 1 validada ponta a ponta em producao (upload Drive funcionando, 01_bibliografia/02_livros_extraidos populados).
- Bootstrap otimizado (force_relink + max_aulas) publicado.
- Mudancas locais ainda nao commitadas: `aula-pipeline/backend/{ai_actions,drive_client,drive_sync,server,settings}.py`, novo `drive_artifacts.py`, `aula-pipeline/README.md`.

## Proximos passos sugeridos
1. Commitar/push das mudancas locais antes de evoluir features.
2. Validar Fase 2 em producao: rodar `gerar-texto` em aula com bibliografia pronta e conferir `04_aula_texto.md` no Drive.
3. Validar Fase 3: `enviar-revisao` -> conferir `06_revisao.md`.
4. Validar Fase 4: `gerar-pptx` 2x -> conferir `05_outline_slides.md`.
5. Adicionar feedback de progresso/erro por etapa no frontend.
6. Migrar `aulas.json` para Firestore/Cloud SQL (estado nao sobrevive a redeploy hoje).
7. Implementar montagem real do `.pptx` (montador-pptx hoje so gera outline).
