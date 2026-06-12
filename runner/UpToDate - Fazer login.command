#!/bin/bash
# Abre a janela do Chrome (agent-browser) na pagina de login do UpToDate.
# Use isto quando o download vier vazio (login expirado) ou na 1a vez.

export PATH="$HOME/.npm-global/bin:$PATH"
PROFILE="$HOME/agent-browser-automations/profiles/uptodate"

echo "Abrindo a janela de login do UpToDate..."
agent-browser --session uptodate close >/dev/null 2>&1
agent-browser --session uptodate --profile "$PROFILE" --headed open "https://www.uptodate.com/login"

echo ""
echo "Uma janela do Chrome abriu na pagina de login do UpToDate."
echo "1) Faca login NESSA janela (acesso institucional / usuario e senha)."
echo "2) Espere carregar logado."
echo "3) Depois rode 'UpToDate - Baixar'."
read -r -p "Pressione Enter para fechar esta janela..."
