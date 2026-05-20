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
- `agents/` (prompts dos agentes: curador-diretrizes-consensos, buscador-pubmed, curador-uptodate, indexador-livros, redator-aula, revisor-cientifico, montador-pptx)
- `aulas/temas.md`
- `aulas/templates/system_prompt_aula.md`
- `aulas/templates/criterios_revisao.md`
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
- `06_revisao.md` (ou subpasta `06_revisao/`)
- `M{X}_A{Y}.pptx`

No Google Drive a estrutura espelha esse layout por subpastas (`01_bibliografia`, `02_livros_extraidos`, `03_pdfs_artigos`, `04_aula_texto`, `05_outline_slides`, `06_revisao`).

## Pipeline (fases)
1. **Fase 1 - Bibliografia**: ler `aulas/temas.md`, selecionar modulo/aula, acionar `gerar-bibliografia` -> gera `pubmed_busca.md`, `uptodate.md`, `diretrizes_consensos.md`, `capitulos_livros.md`, `01_bibliografia.md` e extrai capitulos de livros para `02_livros_extraidos` quando ha PDF no Drive.
2. **Fase 2 - Texto**: aguardar PDFs em `03_pdfs_artigos/`, acionar `gerar-texto` -> gera `04_aula_texto.md` (le bibliografia do Drive como fonte primaria).
3. **Fase 3 - Revisao**: acionar `enviar-revisao` -> gera `06_revisao.md` (le texto do Drive como fonte primaria).
4. **Fase 4 - Slides**: acionar `gerar-pptx` 2x. 1a execucao gera `05_outline_slides.md` e move para `slides_em_producao`. 2a execucao marca `pptx_pronto`. Montagem PPTX real ainda nao implementada.

Agentes-prompt usados pelas acoes:
- `@curador-diretrizes-consensos.md`, `@buscador-pubmed.md`, `@curador-uptodate.md`, `@indexador-livros.md`
- `@redator-aula.md`, `@revisor-cientifico.md`, `@montador-pptx.md`

## Criterios de saida
- Referencias validadas e justificadas.
- Texto com fluxo didatico, decisoes clinicas e citacoes.
- Outline de slides coerente com objetivo da aula.
- Revisao com pendencias criticas zeradas.
- PPTX final dentro do limite de slides definido no briefing.

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
- `VERTEX_MODEL=gemini-2.5-flash`
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
- Acoes: `POST /api/aulas/{id}/actions/{gerar-bibliografia|gerar-texto|enviar-revisao|gerar-pptx|aprovar-bibliografia|marcar-pdfs|concluir|avancar-etapa|voltar-etapa|abrir-pasta}`
- Drive: `GET /api/drive/status`, `POST /api/drive/auth-start`, `POST /api/drive/bootstrap?force_relink=true&max_aulas=50`
- Upload por aula: `POST /api/aulas/{id}/upload`, `POST /api/aulas/{id}/upload-browser` (multipart)

`bootstrap` aceita `force_relink` (religa pastas ao novo root, util em migracao para Shared Drive) e `max_aulas` (lote anti-timeout).

## Maquina dos status (referencia rapida)
`proximas_aulas` -> (gerar_bibliografia) -> `bibliografia_pronta` -> (marcar_pdfs) -> `pdfs_adicionados` -> (gerar_texto) -> `texto_pronto_revisao` -> (enviar_revisao) -> `texto_revisado` -> (gerar_pptx #1) -> `slides_em_producao` -> (gerar_pptx #2) -> `pptx_pronto` -> (concluir) -> `concluida`.

Detalhes em `aula-pipeline/backend/schemas.py` (`NEXT_ACTION_BY_STATUS`).

## Pontos de cuidado
- Fase 2/3 sempre tentam ler artefato do Drive primeiro (`drive_artifacts.py`) e so caem para estado interno se nao houver no Drive. Isso evita perder contexto quando o container reinicia.
- Token de OAuth pode expirar; refresh em memoria funciona automaticamente, mas se o refresh token for revogado e preciso rotacionar o secret `gineco-oauth-token`.
- Nunca commitar `aula-pipeline/backend/credentials/`, `*.env`, `token.json`. Ja ignorado em `.gitignore`.
- PDFs/PPTX nao vao para o git (ja ignorados); ficam apenas no Drive.

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
