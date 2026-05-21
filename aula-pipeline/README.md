# Kanban de Produção de Aulas (v1)

Dashboard local para acompanhamento do pipeline de aulas de Ginecologia.

## Stack
- Backend: FastAPI
- Frontend: HTML + CSS + JavaScript puro
- Estado: `aula-pipeline/data/aulas.json`

## Estrutura
- `dashboard/index.html`: tela do Kanban
- `dashboard/app.js`: renderização de colunas/cards + chamadas de API
- `dashboard/style.css`: estilos responsivos
- `backend/server.py`: API FastAPI
- `backend/store.py`: leitura/gravação/sincronização do estado
- `backend/pipeline_simulado.py`: transições simuladas do pipeline
- `backend/schemas.py`: modelos Pydantic e constantes
- `data/aulas.json`: estado persistido

## Como rodar
1. Entre no backend:
```bash
cd aula-pipeline/backend
```

2. Instale dependências:
```bash
python3 -m pip install -r requirements.txt
```

3. Inicie o servidor:
```bash
python3 -m uvicorn server:app --reload --host 127.0.0.1 --port 8787
```

4. Abra no navegador:
- `http://127.0.0.1:8787/`

## Endpoints v2
- `GET /api/aulas`
- `GET /api/aulas/{id}`
- `GET /api/aulas/{id}/texto` (lê `04_aula_texto.md` do Drive)
- `PUT /api/aulas/{id}/texto` (grava `04_aula_texto.md` no Drive)
- `POST /api/aulas/{id}/actions/gerar-bibliografia` (assíncrono — flipa para `bibliografia_em_geracao` e dispara worker)
- `POST /api/aulas/{id}/actions/marcar-pdfs-baixados`
- `POST /api/aulas/{id}/actions/salvar-texto-inicial`
- `POST /api/aulas/{id}/actions/concluir-edicao`
- `POST /api/aulas/{id}/actions/gerar-pptx`
- `POST /api/aulas/{id}/actions/marcar-imagens-prontas`
- `POST /api/aulas/{id}/actions/mover-pptx-final`
- `POST /api/aulas/{id}/actions/abrir-pasta`
- `POST /api/aulas/{id}/actions/avancar-etapa`
- `POST /api/aulas/{id}/actions/voltar-etapa`

## Endpoints Google Drive (OAuth)
- `GET /api/drive/status`
- `POST /api/drive/auth-start`
- `POST /api/drive/bootstrap`
- `GET /api/aulas/{id}/drive-files`
- `POST /api/aulas/{id}/upload`
- `POST /api/aulas/{id}/upload-browser` (multipart/form-data, direto do navegador)

### Bootstrap otimizado (anti-timeout)
O endpoint `POST /api/drive/bootstrap` aceita query params opcionais:
- `force_relink=true|false`: quando `true`, ignora vínculos antigos e religa as aulas para as pastas sob o `DRIVE_ROOT_FOLDER_ID` atual (útil em migração para Shared Drive).
- `max_aulas=<n>`: processa em lotes (ex.: 50) para evitar timeout em ambientes com muitas aulas.

Exemplos:
```bash
curl -X POST "https://SEU_BACKEND/api/drive/bootstrap?force_relink=true&max_aulas=50"
curl -X POST "https://SEU_BACKEND/api/drive/bootstrap?max_aulas=50"
```

## Execução de IA nas etapas do Kanban (Vertex AI / Gemini)
O backend suporta execução real de IA ao clicar nas ações do Kanban.
Backend padrão: `vertex` (Gemini no Google Cloud). Fallback opcional: `openrouter`.

### Como funciona
- `gerar-bibliografia`: ação **assíncrona**. Flipa a aula para `bibliografia_em_geracao` imediatamente, dispara worker (`BackgroundTasks` do FastAPI) e ao final marca `bibliografia_pronta`. Em caso de erro, marca `erro_bloqueada`. O progresso aparece em `progresso` no JSON da aula (lido pelo frontend para mostrar substep atual: PubMed → UpToDate → Diretrizes → Livros).
- `gerar-pptx`: gera `05_outline_slides.md` a partir do texto editado no Drive e marca `pptx_gerado` (outline-only por enquanto, montagem `.pptx` real ainda não implementada).

Texto da aula (`04_aula_texto.md`) é editado diretamente no dashboard e gravado no Drive via `PUT /api/aulas/{id}/texto`.

### Artefatos automáticos
- `gerar-bibliografia`: executa a fase 1, gerando `pubmed_busca.md`, `uptodate.md`, `diretrizes_consensos.md`, `capitulos_livros.md` e `01_bibliografia.md`; extrai capítulos de livros para `02_livros_extraidos` quando os PDFs estão disponíveis no Drive.
- `gerar-pptx`: gera `05_outline_slides.md` a partir do texto da aula (Drive) e envia ao Drive em `05_outline_slides`.

As ações usam os prompts-base em `agents/*.md` quando esses arquivos estão disponíveis no ambiente. Para Cloud Run, use o deploy pelo Dockerfile da raiz do repositório para incluir `agents/` e `aulas/templates/` na imagem.

### Configuração da fase 1
- `BOOKS_DRIVE_FOLDER_ID`: pasta Drive dos livros-base. Padrão: `1MfyJgRryqhSfj0cp0K3OX0ATkFfRZsiN`.
- `NCBI_TOOL`: identificador da aplicação nas chamadas NCBI. Padrão: `GinecoKanban`.
- `NCBI_EMAIL`: opcional, recomendado pelo NCBI para contato em caso de uso excessivo.
- `NCBI_API_KEY`: opcional e não paga; aumenta limite de requisições do E-utilities.
- `PHASE1_MAX_WEB_RESULTS`: máximo de candidatos de diretrizes coletados em busca pública.

### Ativação
Definir no ambiente do backend:
- `ENABLE_AI_ACTIONS=1`
- `AI_BACKEND=vertex`
- `VERTEX_PROJECT_ID=<seu-project-id>`
- `VERTEX_LOCATION=us-central1`
- `VERTEX_MODEL=gemini-2.5-flash`

### Cloud Run (update sem redeploy completo)
```bash
gcloud run services update gineco-api \
  --project project-5ca1d427-8a03-4908-8cb \
  --region us-central1 \
  --set-env-vars ENABLE_AI_ACTIONS=1,AI_BACKEND=vertex,VERTEX_PROJECT_ID=project-5ca1d427-8a03-4908-8cb,VERTEX_LOCATION=us-central1,VERTEX_MODEL=gemini-2.5-flash
```

Também habilite API Vertex AI no projeto:
```bash
gcloud services enable aiplatform.googleapis.com --project project-5ca1d427-8a03-4908-8cb
```

Opcional (fallback OpenRouter):
- `AI_BACKEND=openrouter`
- `OPENROUTER_API_KEY=...`
- `OPENROUTER_MODEL=...`

## Limpeza de pastas duplicadas no Drive
Script: `backend/drive_delete_duplicate_folders.py`

1. Dry-run seguro (não apaga nada):
```bash
cd aula-pipeline/backend
python3 drive_delete_duplicate_folders.py --strategy protected
```

2. Aplicar limpeza real (move duplicadas para lixeira):
```bash
cd aula-pipeline/backend
python3 drive_delete_duplicate_folders.py --strategy protected --apply
```

3. Estratégias opcionais:
- `--strategy newest`: mantém pasta mais recente de cada grupo duplicado.
- `--strategy oldest`: mantém pasta mais antiga de cada grupo duplicado.

## Configuração Google Drive (OAuth de usuário)
1. Copie `aula-pipeline/.env.example` para `aula-pipeline/.env`.
2. Ajuste os caminhos no `.env` conforme sua máquina.
3. Garanta que `backend/credentials/oauth_client.json` exista.
4. Rode o backend.
5. Chame `POST /api/drive/auth-start` uma vez para abrir o login Google e salvar o token.
6. Chame `POST /api/drive/bootstrap` para criar/sincronizar pastas de módulos/aulas no Drive.

### Exemplo com curl
```bash
curl -X POST http://127.0.0.1:8787/api/drive/auth-start
curl -X POST http://127.0.0.1:8787/api/drive/bootstrap
curl http://127.0.0.1:8787/api/drive/status
```

## Push para GitHub
Repositório alvo: `https://github.com/lucaserbano/ginecologia.git`

```bash
cd "/Users/lucas/Downloads/GINECOLOGIA - AFYA"
git init
git add .
git commit -m "feat: kanban v1 + integração OAuth Google Drive"
git branch -M main
git remote add origin https://github.com/lucaserbano/ginecologia.git
git push -u origin main
```

## Observações
- O v1 usa transições simuladas de pipeline.
- O backend sincroniza o estado com `aulas_em_producao/modulos` a cada carregamento.
- A ação `abrir-pasta` tenta abrir o caminho no sistema operacional local.
- **Nunca** suba credenciais/tokens no GitHub (já ignorados pelo `.gitignore` raiz).

## Deploy Cloud Run (Service Account)
Objetivo: frontend no GitHub Pages + backend FastAPI no Cloud Run.

### 1) Pré-requisitos
- `gcloud` instalado e autenticado.
- Projeto GCP criado.
- Drive API habilitada.
- Service Account criada com chave JSON.
- Pasta raiz do Drive compartilhada com o e-mail da Service Account (permissão: Editor).

### 2) Variáveis no Cloud Run
- `GOOGLE_DRIVE_AUTH_MODE=service_account`
- `DRIVE_ROOT_FOLDER_ID=<ID da pasta raiz no Drive>`
- `OPEN_FOLDER_ACTION_ENABLED=0`
- `ALLOWED_ORIGINS=https://lucaserbano.github.io`

### 3) Deploy do backend (sem chave JSON, usando identidade da Service Account)
```bash
cd "/Users/lucas/Downloads/GINECOLOGIA - AFYA"
gcloud run deploy gineco-api \
  --source . \
  --project project-5ca1d427-8a03-4908-8cb \
  --region us-central1 \
  --allow-unauthenticated \
  --service-account gineco-drive-sa@project-5ca1d427-8a03-4908-8cb.iam.gserviceaccount.com \
  --set-env-vars GOOGLE_DRIVE_AUTH_MODE=service_account,DRIVE_ROOT_FOLDER_ID=SEU_DRIVE_ROOT_ID,OPEN_FOLDER_ACTION_ENABLED=0,ALLOWED_ORIGINS=https://lucaserbano.github.io
```

### 4) Ligar frontend GitHub Pages ao backend Cloud Run
- Abrir:
  - `https://lucaserbano.github.io/ginecologia/?api_base=https://SEU-SERVICO-xxxxxx-uc.a.run.app`
- O frontend salva esse `api_base` no `localStorage`.
- Para trocar depois: abra a URL novamente com novo `?api_base=...`.

### 5) Teste ponta a ponta
1. `Bootstrap Drive`
2. `Listar arquivos Drive` numa aula
3. `Upload para Drive` por aula (agora envia arquivo direto do navegador)
