#!/bin/bash
# Setup do NotebookLM NESTA maquina. Rode UMA vez por computador.
# Cria um ambiente Python proprio (via uv) e instala o notebooklm-py.
# Ao final, abre o login do Google (se ainda nao estiver logado).
set -u

VENV="$HOME/.venvs/gineco-nlm"

echo "== 1/4 garantindo o 'uv' (gerenciador de Python) =="
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
echo "uv: $UV"

echo "== 2/4 criando o ambiente Python 3.12 em $VENV =="
# (o Python do sistema costuma ser velho/quebrado; o uv baixa um 3.12 saudavel)
"$UV" venv --python 3.12 "$VENV"

echo "== 3/4 instalando dependencias (requests, fpdf2, notebooklm-py) =="
VIRTUAL_ENV="$VENV" "$UV" pip install requests "fpdf2>=2.7" "notebooklm-py[browser]"

echo "== 4/4 login no NotebookLM (Google) =="
if "$VENV/bin/notebooklm" auth check >/dev/null 2>&1; then
  echo "Ja autenticado neste computador."
else
  echo "Abrindo o Chrome para login (use erbano.lho@gmail.com)..."
  "$VENV/bin/notebooklm" login --browser chrome
fi
"$VENV/bin/notebooklm" auth check --test

echo ""
echo "==================================================="
echo " Tudo pronto! A partir de agora, para gerar textos,"
echo " use o lancador 'NotebookLM - Gerar texto'."
echo "==================================================="
read -r -p "Pressione Enter para fechar..."
