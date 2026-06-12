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

echo "==================================================="
echo " Baixando paginas do UpToDate marcadas no kanban..."
echo "==================================================="
# --only download_pdfs: nao pega o job do NotebookLM (que roda no outro lancador).
python3 runner.py --once --only download_pdfs

echo ""
echo "Concluido. Se aparecer '0 PDF(s) ... para revisar', o login do UpToDate"
echo "expirou: rode 'UpToDate - Fazer login' e tente de novo."
read -r -p "Pressione Enter para fechar esta janela..."
