# Guia de estilo do coordenador — reescrita do texto das aulas

Este guia padroniza a reescrita do texto gerado pelo NotebookLM (`04_aula_texto.md`)
para a "assinatura" de escrita do coordenador. **A reescrita é feita pelo Claude**, na mão,
aula a aula — não é um passo automático de IA no backend (o coordenador descartou essa via
por ser instável). Foi destilado de edições reais (M1 A8 antes×depois + finais de M1 A5/A6/A7)
e validado na reescrita das 28 aulas de M1/M3 em 2026-06-20.

**Regra de ouro: muda só a FORMA, nunca o conteúdo clínico.** Nenhuma alteração de número,
dose, corte laboratorial, fármaco, sigla, epônimo, percentual, gene ou conduta.

## Regras de reescrita (aplicar bloco a bloco)

1. **Remover marcações de citação** do corpo: `[1, 2]`, `[5-7]`, `[14, 18, 19]` etc.
   (As referências viram um slide final, compilado automaticamente — não entram no texto.)
2. **Remover bullets `•`** e as **fragmentações com reticências** do NotebookLM
   (`...impõe a gonadectomia... ...devido ao risco`) → juntar numa frase só.
3. **Remover formatação de IA**: `**negrito**`, `*itálico*`, símbolos `®`, e converter
   LaTeX para texto legível (`$T_{max}$`→Tmax; `$IMC \ge 30 kg/m^2$`→IMC ≥ 30 kg/m²;
   `$25^{\circ}C$`→25 °C; `$\le$`→≤; `$\ge$`→≥; `$2/3$`→dois terços).
4. **Cortar superlativos / "IA-ês"**: "marcadores"→"pontos"; "divergem drasticamente"→"são bem
   distintos"; "crucial"→"essencial"; "riscos severos"→"riscos"; "raríssima"→"muito rara";
   "altíssimos"→"bem altos"; "meramente/severo"→cortar. **Apagar frases de efeito vazias**
   (ex.: a frase final da A8 sobre a "odisséia de exames" foi removida inteira).
5. **Voz de professor, 1ª pessoa do plural**: "nossa missão", "devemos ficar de olho", "podemos",
   "fique atento", "lembrem-se", ênfase com "!" e reticências próprias para pausa.
   Trocar coloquialismos como "a gente" por "devemos/podemos/garantimos".
6. **Ganchos didáticos**: abrir seções com pergunta curta isolada ("Quando ela está definida?",
   "Qual a consequência disso?"); interpelar ("Vocês sabiam que…?", "Vocês já avaliaram…?");
   fechar a aula com **"Em resumo:"** no último bloco.
7. **Simplificar latinismos/anglicismos** ("ultrassonografia pélvica"→"da pelve"; "patch"→"adesivo")
   e **quebrar frases longas** em duas com conector ("Por isso, …").
8. **Preservar verbatim** todo o conteúdo clínico (ver regra de ouro) e **manter as atribuições
   a fontes** no corpo ("Conforme a Mayo Clinic", "Segundo a Endocrine Society", "Teo e Ong (2021)",
   "Critchley e colaboradores (2020)") — elas soam como professor citando, não como IA.
9. **Conservador com cross-referências** a outras aulas: só quando o texto já as sugere; o
   coordenador faz o ajuste fino depois. Corrigir erros óbvios de digitação/concordância
   ("vescorretal"→"vesicorretal"; "isoeicoicos"→"isoecoicos"; "das neurônios"→"dos neurônios").

## Formato do `.md` de saída (crítico para o `pptx_builder.py`)

- O builder divide slides por **linha de `-----`** (regex `^[ \t]*-{3,}$`, em
  `aula-pipeline/backend/pptx_builder.py`). O separador cru do NotebookLM costuma ser `\--------`
  (com barra) e **não casa** a regex. Ao reescrever, usar **uma linha de `-----`** (3+ hífens,
  sem barra) entre cada bloco → 1 bloco = 1 slide. Normalizar separadores variados (`---`,
  `------------`) para `-----`.
- **Preservar o mesmo número de blocos** do original (mesma contagem de slides).
- Dentro do bloco, frases curtas separadas por linha em branco.
- **Não** incluir seção "Referências" — é gerada automaticamente a partir dos `.md` de
  `01_bibliografia` no momento do `gerar-pptx`.

## Procedimento (mecânica da API)

Backend: `https://gineco-api-468351448933.us-central1.run.app` (conferir
`GET /api/drive/status` → `authorized:true` antes de começar).

Para cada aula:
1. `GET /api/aulas/{id}/texto` → texto atual do Drive.
2. Claude reescreve segundo as regras acima.
3. `PUT /api/aulas/{id}/texto` com body `{"conteudo": "<texto reescrito>"}` → grava
   `04_aula_texto/04_aula_texto.md` no Drive (não altera o status).
4. Avançar para `texto_editado`, conforme o status atual:
   - status `texto_feito` (fluxo normal de aula nova vinda do NotebookLM) →
     `POST /api/aulas/{id}/actions/concluir-edicao`.
   - status `pptx_gerado` (reprocessamento) →
     `POST /api/aulas/{id}/actions/voltar-etapa`.

Validação antes do PUT: o texto não pode conter `•`, `[\d`, `$`, `**` nem estar vazio.
Verificação depois: `GET /texto` limpo e `GET /api/aulas/{id}` em `status: texto_editado`.

**Atenção operacional:** o `voltar-etapa`/`concluir-edicao` deve rodar **só depois** que o
`PUT` confirmar gravação — em 2026-06-20 um erro de nome de arquivo fez o PUT falhar mas o
avanço de etapa rodar mesmo assim, deixando a aula em `texto_editado` com texto cru. Sempre
checar o `ok:true` do PUT antes de avançar.

## Exemplo (antes → depois)

Antes (NotebookLM):
> • A telarca presente indica que os ovários foram funcionantes em algum momento... \[5, 9, 10\].
> • A diferenciação correta é crucial, pois as condutas cirúrgicas e o aconselhamento reprodutivo
>   divergem drasticamente \[28-30\].

Depois (estilo do coordenador):
> A telarca presente indica que os ovários foram funcionantes em algum momento.
>
> A diferenciação correta é essencial, porque as condutas cirúrgicas e o aconselhamento
> reprodutivo são bem distintos.
