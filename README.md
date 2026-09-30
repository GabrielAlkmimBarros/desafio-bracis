# Caça-Alucinações — BRACIS 2026 × Jusbrasil

Detecta citações de jurisprudência e de lei em peças jurídicas e classifica cada uma como `real`,
`inventada` ou `incompleta`, resolvendo as reais para o `id` do registro na base canônica (`.db`).

**Não usa modelo nenhum** (nem de linguagem, nem treinado, nem pesos a baixar): é um sistema de regras
em Python puro, só com a biblioteca padrão. Roda **offline**, sem rede e sem APIs, em CPU, em poucos
segundos para dezenas de documentos, e é **determinístico** (mesma entrada, mesma saída, byte a byte).

## Como executar (do zero)

Requisito único: **Python 3.10 ou mais novo**. Não há dependência a instalar para executar a solução.

```bash
bash run.sh <caminho_db> <pasta_txt> <arquivo_saida>
```

- `<caminho_db>`: a base canônica SQLite (qualquer base no mesmo formato: tabela `documentos`).
- `<pasta_txt>`: pasta com um `.txt` UTF-8 por documento; o nome do arquivo é o `documento_id`.
- `<arquivo_saida>`:
  - terminado em `.csv` → grava ali o CSV de submissão (uma linha por documento, `documento_id,citacoes`)
    e os JSONs completos do contrato (schema 1.2) na pasta irmã `<arquivo_saida sem .csv>_json/`;
  - qualquer outro caminho → tratado como pasta: grava dentro dela um JSON por documento **e** o
    `submission.csv`.

Exemplos:

```bash
bash run.sh data/desafio1_bracis.db data/txt saida/submission.csv    # saida/submission.csv + saida/submission_json/
bash run.sh /dados/novo.db /dados/txt /dados/saida                    # /dados/saida/*.json + /dados/saida/submission.csv
```

Os caminhos valem a partir de onde o comando é chamado; o código é localizado pela posição do `run.sh`,
sem nenhum caminho absoluto. Outro interpretador: `PYTHON=/caminho/python3 bash run.sh ...`. Um resumo da
base lida (quantos registros e normas foram identificados) sai em stderr, fora da saída.

A CLI original continua disponível: `python -m src.run --input <pasta_txt> --output <pasta_json> --db <db>`.

## Abordagem

Cada documento passa por três etapas; nada do banco de desenvolvimento está fixo no código — tudo é lido
do `.db` recebido na execução.

1. **Índice da base** (`src/indice.py`, montado uma vez, ~1 s). Não usa a busca de texto (FTS), porque ela
   devolve todo acórdão que *menciona* um número. Lê o **cabeçalho** de cada acórdão (classe processual,
   número e UF, num formato próprio por tribunal; tribunal desconhecido ou nulo é inferido pelo próprio
   texto) e monta o dicionário número → registros. Súmulas e artigos de lei são identificados pela
   primeira linha do registro, com **as mesmas funções que leem as citações nas peças** — assim
   "Súmula nº 7 do STJ" e "Art. 5º da CF/88" viram a mesma chave dos dois lados, qualquer que seja o
   formato do cabeçalho.
2. **Detecção** (`src/detectar.py`). Âncora no número: um número só vira citação de acórdão se houver uma
   classe processual à esquerda ("AgInt no REsp") — é o que descarta autos, OAB, fls. e valores. Súmulas,
   artigos de lei (`src/leis.py`), Temas e referências sem número ("julgado do STF de 2024, Rel. Min. X",
   com o relator antes ou depois do ano) têm padrões próprios. Toda a leitura tolera o ruído do nível 2:
   abreviações (`src/vocabulario.py`), número com espaços, quebrado ou sem pontos, separadores de UF,
   confusões de OCR (0↔O, 1↔l, 5↔S, m↔rn, e↔c) nas letras e nos dígitos, e quebras de linha.
   `src/normalizar.py` leva todas as variantes a uma forma canônica (inclusive o número CNJ).
3. **Resolução** (`src/resolver.py`). Conta os candidatos no índice: um → `real` (com o `id`); nenhum →
   `inventada`; vários sem desempate → `incompleta`; sem número → `incompleta`. O desempate usa tribunal
   (dito na citação, implícito no número CNJ ou na classe — mapa fixo somado ao que a própria base mostra),
   classe e UF.

**Confiança.** Cada decisão cai numa *situação* (ex.: "real: número único", "inventada: número ausente,
texto com ruído") e a confiança vem de um perfil (`PERFIS` em `src/resolver.py`). O perfil usado,
`padrao`, dá 1,0 em toda situação em que nenhuma fonte de teste mostrou erro do código atual (mais de
600 mil decisões conferidas, inclusive com redação real de decisões do STJ — tabela no próprio arquivo),
0,99 em "inventada com texto ruidoso" (4 erros em 40.960, ambiguidades sigla/número com OCR) e valores
menores onde há dúvida real sobre o rótulo: súmula sem tribunal, número ambíguo, duplicata, Tema. Outros perfis, para comparação:
`CONFIANCA_PERFIL=recomendada|calibrada|colega|um bash run.sh ...` (ou mude `PERFIL` no arquivo).

## Estrutura

```
run.sh              ponto de entrada único (bash run.sh <db> <pasta_txt> <arquivo_saida>)
src/run.py          CLI: lê os .txt, escreve um JSON por documento (contrato schema 1.2)
src/indice.py       ficha de identidade de cada registro da base, lida do cabeçalho
src/detectar.py     acha as citações: acórdãos, súmulas, temas, artigos, incompletas
src/resolver.py     decide real / inventada / incompleta e a confiança (perfis)
src/normalizar.py   formas canônicas: número de processo (com OCR), dígito verificador CNJ
src/vocabulario.py  classes processuais -> siglas canônicas ('Rec. Esp.' -> REsp), com OCR
src/leis.py         reconhece a lei citada ('CPC', 'Lei nº 13.105/2015' -> L13105)
oficial/            arquivos da organização, sem alteração (métrica e conversor JSON -> CSV)
scripts/            ferramentas de desenvolvimento (avaliação, testes) — não participam da execução
data/               dados do desafio (NÃO versionados)
```

## Desenvolvimento e testes

As ferramentas de `scripts/` usam `pandas`/`numpy` (métrica oficial): `pip install -r requirements.txt`.

```bash
python -m src.run --input data/txt --output out/atual --db data/desafio1_bracis.db --debug  # --debug: motivo de cada decisão
python scripts/avaliar.py out/atual --erros       # nota com a métrica oficial + lista de erros
python scripts/estresse.py                        # citações sintéticas geradas da base: deve dar 0 falhas
python scripts/estresse.py --db outra_base.db     # o mesmo, gerado a partir de outra base
python scripts/bancos_modificados.py              # 26 peças contra 7 cópias modificadas do .db
python scripts/metamorfico.py                     # 26 peças sob 8 transformações que não mudam a resposta
python scripts/estresse.py --situacoes            # + casos e erros por situação (evidência da confiança)
python scripts/calibracao.py                      # acerto por situação x confiança adotada
python scripts/cenarios_confianca.py              # ganho/perda de cada perfil conforme a taxa de erro
```

- **Estresse**: os 996 acórdãos em formatos fixos e com ruído de nível 2 combinado, inventadas sintéticas
  e aleatórias, incompletas em 14 moldes, leis e súmulas da base e fora dela, e distratores (autos, OAB,
  fls., valores, frases sem citação) que não podem ser detectados.
- **Redação real** (seção F do estresse): 600 padrões de citação de acórdão, 400 de artigo e 89 de súmula
  extraídos de ementas e íntegras públicas do STJ (Portal de Dados Abertos), guardados só como padrões em
  `scripts/padroes_redacao_real.json` (números, UFs, datas e nomes trocados por marcadores). Cada padrão é
  instanciado com registros da base e com números inexistentes; a resposta certa vem do cabeçalho bruto
  do registro e do vocabulário oficial de classes do STJ, não do código testado.
- **Metamórfico**: reflow das linhas, espaços e quebras, caixa alta, rodapé de citação ('relator Ministro
  ..., julgado em ..., DJe de ...'), parênteses, espaço inseparável, hifenização e prosa de decisões reais
  intercalada (`--prosa PASTA`, só em desenvolvimento): a classificação tem de continuar perfeita.
- **Bases modificadas**: ids e `documento_id` trocados, 25% dos acórdãos e 3 normas removidos, normas com
  cabeçalhos em outros formatos e normas novas, sem normas, só normas, tribunal desconhecido ou nulo, sem a
  tabela FTS. O gabarito é transformado conforme a definição do desafio (registro removido → a citação
  vira `inventada`; id trocado → id novo) e a classificação tem de continuar perfeita.

## Estado atual

- Nota local nas 26 peças: 1,1000 (macro-F1 = 1); estresse com 0 falhas (também gerado a partir de cada base
  modificada); macro-F1 = 1 nas 7 bases modificadas e nas 8 transformações metamórficas.
- Em 19 milhões de caracteres de decisões reais do STJ: nenhum erro de execução, auditoria cega de 180
  detecções sem falso positivo, e 99,97% das menções a processos detectadas.
- Execução verificada em pasta limpa, com Python sem pacotes instalados, sem rede e com caminhos relativos;
  saídas idênticas entre execuções e entre sementes de hash diferentes.
- Pendente: ambiente declarado em Docker (exigido pela organização).
