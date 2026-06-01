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
- **PDFs diretos** (diretrizes/consensos com link `.pdf`, ex. FEBRASGO/MS) — via HTTP.
- **PubMed** — só quando há versão **open-access no PMC**; o resto fica para download manual.
- Capítulos de livro (`capitulos_livros.md`) são ignorados: já ficam em `02_livros_extraidos` no Drive.

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

## Limitações conhecidas

- PMC via HTTP pode ser bloqueado por bot-protection; nesses casos o artigo vai
  para a lista de **download manual** (comportamento esperado).
- O runner não tenta burlar paywall. Texto completo de PubMed atrás de editora
  fica sempre como manual.
