#!/bin/bash
# Gera o roteiro no NotebookLM das aulas que voce marcou "Gerar texto do
# NotebookLM" no kanban. Dois cliques aqui depois de clicar o botao no kanban.
# Pre-requisito: ter rodado uma vez 'NotebookLM - Instalar (1a vez)'.
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"   # .../GINECOLOGIA - AFYA/runner
VENV="$HOME/.venvs/gineco-nlm"

if [ ! -x "$VENV/bin/notebooklm" ]; then
  echo "Ambiente do NotebookLM nao encontrado."
  echo "Rode primeiro 'NotebookLM - Instalar (1a vez)' nesta maquina."
  read -r -p "Enter para fechar..."; exit 1
fi

# Aponta o runner para o notebooklm deste ambiente e roda so os jobs do NotebookLM.
export NOTEBOOKLM_BIN="$VENV/bin/notebooklm"
cd "$DIR" || { echo "ERRO: pasta do runner nao encontrada."; read -r -p "Enter..."; exit 1; }

echo "==================================================="
echo " Gerando textos do NotebookLM marcados no kanban..."
echo "==================================================="
"$VENV/bin/python" runner.py --once --only gerar_texto_notebooklm

echo ""
echo "Concluido. O texto aparece no card (botao 'Editar texto') e em"
echo "04_aula_texto.md no Drive da aula."
read -r -p "Pressione Enter para fechar esta janela..."
