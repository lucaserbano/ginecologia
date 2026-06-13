#!/bin/bash
# Baixa as paginas do UpToDate das aulas que voce marcou "Download do UpToDate"
# no kanban. Basta dar dois cliques aqui depois de clicar o botao no kanban.
# Portavel: se localiza sozinho (funciona em qualquer Mac, qualquer usuario).

export PATH="$HOME/.npm-global/bin:$PATH"

# Pasta deste script = .../GINECOLOGIA - AFYA/runner
DIR="$(cd "$(dirname "$0")" && pwd)"
# baixar_uptodate.py fica em Afya/agent-browser-automations (dois niveis acima).
UPTODATE_DIR="$(cd "$DIR/../../agent-browser-automations" 2>/dev/null && pwd)"
export UPTODATE_SCRIPT="${UPTODATE_DIR}/baixar_uptodate.py"

cd "$DIR" || { echo "ERRO: pasta do runner nao encontrada."; read -r -p "Enter para fechar..."; exit 1; }

# Escolhe um python3 que tenha 'requests'. O brew (ex.: ao instalar outras coisas)
# pode trocar o 'python3' do PATH por um sem as libs do runner; aqui tentamos, em
# ordem: override manual, venv do projeto, python do sistema, python3 do PATH.
PY=""
for cand in "$RUNNER_PYTHON" "$HOME/.venvs/gineco-nlm/bin/python" /usr/bin/python3 python3; do
  [ -n "$cand" ] || continue
  if "$cand" -c "import requests" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "ERRO: nenhum python3 com 'requests' encontrado."
  echo "Instale as dependencias: pip3 install -r requirements.txt"
  read -r -p "Enter para fechar..."; exit 1
fi
echo "Usando python: $PY"

echo "==================================================="
echo " Baixando paginas do UpToDate marcadas no kanban..."
echo "==================================================="
# --only download_pdfs: nao pega o job do NotebookLM (que roda no outro lancador).
"$PY" runner.py --once --only download_pdfs

echo ""
echo "Concluido. Se aparecer '0 PDF(s) ... para revisar', o login do UpToDate"
echo "expirou: rode 'UpToDate - Fazer login' e tente de novo."
read -r -p "Pressione Enter para fechar esta janela..."
