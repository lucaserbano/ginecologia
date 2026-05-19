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

## Endpoints v1
- `GET /api/aulas`
- `GET /api/aulas/{id}`
- `POST /api/aulas/{id}/actions/gerar-bibliografia`
- `POST /api/aulas/{id}/actions/aprovar-bibliografia`
- `POST /api/aulas/{id}/actions/marcar-pdfs`
- `POST /api/aulas/{id}/actions/gerar-texto`
- `POST /api/aulas/{id}/actions/enviar-revisao`
- `POST /api/aulas/{id}/actions/gerar-pptx`
- `POST /api/aulas/{id}/actions/concluir`
- `POST /api/aulas/{id}/actions/abrir-pasta`
- `POST /api/aulas/{id}/actions/avancar-etapa`
- `POST /api/aulas/{id}/actions/voltar-etapa`

## Endpoints Google Drive (OAuth)
- `GET /api/drive/status`
- `POST /api/drive/auth-start`
- `POST /api/drive/bootstrap`
- `GET /api/aulas/{id}/drive-files`
- `POST /api/aulas/{id}/upload`

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
