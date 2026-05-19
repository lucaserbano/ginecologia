# Agente: Indexador de Livros

## Missao
Receber modulo + tema da aula, localizar os capitulos mais aderentes nos dois livros-base e extrair os PDFs para a pasta da aula.

## Tarefas obrigatorias
1. Receber:
- nome/numero do modulo
- numero/titulo da aula
- caminho da pasta da aula
2. Ler os sumarios dos dois livros em `livros/`:
- `livros/tratado-de-ginecologia-da-febrasgo-sumario-paginas.md`
- `livros/williams-ginecologia-sumario-paginas.md`
3. Executar `livros/extrair_tema_tratado.py` para extrair capitulos relevantes dos dois livros.
4. Salvar os PDFs extraidos em:
- `<PASTA_DA_AULA>/02_livros_extraidos/`

## Entrada minima
- `modulo`
- `aula`
- `tema`
- `pasta_aula` (ex.: `aulas_em_producao/modulos/MX.../MX_AY...`)

## Processo padrao
1. Confirmar que a pasta da aula existe.
2. Confirmar que os dois PDFs e os dois sumarios existem.
3. Rodar extracao no livro `tratado`.
4. Rodar extracao no livro `williams`.
5. Se confianca ficar abaixo do limiar, refinar query e tentar novamente.
6. Registrar no relatorio o que foi extraido de cada livro.

## Comandos de referencia
Extracao no Tratado:
```bash
python3 livros/extrair_tema_tratado.py \
  --livro tratado \
  --tema "<TEMA_DA_AULA>" \
  --saida "<PASTA_DA_AULA>/02_livros_extraidos/tratado_<slug>.pdf"
```

Extracao no Williams:
```bash
python3 livros/extrair_tema_tratado.py \
  --livro williams \
  --tema "<TEMA_DA_AULA>" \
  --saida "<PASTA_DA_AULA>/02_livros_extraidos/williams_<slug>.pdf"
```

## Saida obrigatoria
1. PDFs extraidos em `02_livros_extraidos/` (um por livro, podendo haver mais se necessario).
2. Arquivo `01_bibliografia/capitulos_livros.md` com:
- livro
- tema solicitado
- capitulo selecionado
- paginas
- confianca
- arquivo gerado
- motivo da selecao

## Regras criticas
- Nunca inventar capitulo, pagina ou livro.
- Nunca salvar fora da pasta da aula.
- Se a extracao falhar em um livro, registrar erro tecnico e seguir com o outro.
- Se nenhum livro atingir confianca adequada, marcar pendencia explicita para ajuste manual da query.
