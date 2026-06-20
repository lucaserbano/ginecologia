---
name: reescrever-estilo-aula
description: Use when the coordinator wants the lesson text rewritten in his own writing style after NotebookLM generates it (status "texto feito" / "pptx gerado") — strips AI markers, applies his voice, and advances the lesson to "texto editado". Triggers like "reescreve a aula no meu estilo", "passa as aulas novas pro meu estilo", "aplica a assinatura".
version: 1.0.0
metadata:
  project: GINECOLOGIA - AFYA
  related: [notebooklm-integration]
---

# Reescrever a aula no estilo do coordenador

## Quando usar
Quando o coordenador gera um texto novo no NotebookLM (a aula chega em `texto_feito`) e quer
que ele seja reescrito na sua "assinatura" antes de virar PPTX — ou quando quer reprocessar
aulas que já estão em `pptx_gerado`. A reescrita é feita pelo **Claude**, na mão.

## Regra de ouro
Muda só a **forma**, nunca o conteúdo clínico (número, dose, corte laboratorial, fármaco,
sigla, epônimo, percentual, gene, conduta). Mantém as atribuições a fontes no corpo.

## Passos
1. **Leia o guia de estilo completo** antes de qualquer reescrita:
   `GINECOLOGIA - AFYA/aulas/templates/estilo_coordenador.md`. Ele tem as regras (remover
   bullets/citações/LaTeX/negrito, juntar frases, cortar superlativos, voz de professor,
   ganchos, "Em resumo:"), o formato do `.md` (separadores `-----` = slides; preservar nº de
   blocos; sem seção "Referências") e a mecânica da API.
2. **Descubra o alvo**: um ID dado pelo coordenador, ou todas as aulas no status pedido.
   `GET https://gineco-api-468351448933.us-central1.run.app/api/aulas` e filtre por
   `status == "texto_feito"` (aula nova) ou `"pptx_gerado"` (reprocessamento). Confirme
   `GET /api/drive/status` → `authorized:true`.
3. Para cada aula, em lotes pequenos (1 módulo por vez):
   a. `GET /api/aulas/{id}/texto` → texto atual.
   b. Reescreva segundo o guia (preservando o nº de blocos e usando `-----` entre eles).
   c. `PUT /api/aulas/{id}/texto` com `{"conteudo": "<texto>"}`. **Cheque `ok:true`.**
      Antes do PUT, valide que o texto não tem `•`, `[\d`, `$`, `**` nem está vazio.
   d. **Só depois do PUT confirmado**, avance para `texto_editado`:
      - se status era `texto_feito` → `POST /api/aulas/{id}/actions/concluir-edicao`;
      - se era `pptx_gerado` → `POST /api/aulas/{id}/actions/voltar-etapa`.
4. **Verifique**: `GET /texto` limpo e `GET /api/aulas/{id}` em `status: texto_editado`.
5. O coordenador roda `gerar-pptx` quando quiser reconstruir os slides.

## Cuidados
- Nunca rode o avanço de etapa antes de o PUT retornar `ok:true` (senão a aula fica em
  `texto_editado` com texto cru).
- Calibração: na primeira aula de um novo módulo/tema, mostre o antes→depois ao coordenador
  antes de seguir o lote.
- Use `python3 urllib` para o `PUT` (escapa o JSON corretamente); evite heredocs frágeis.
