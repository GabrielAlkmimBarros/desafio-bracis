# Caça-Alucinações — BRACIS 2026 × Jusbrasil

Detecta citações de jurisprudência e de lei em peças jurídicas, classifica cada uma como `real`, `inventada` ou
`incompleta` e resolve as reais para o `id` do registro na base canônica (`.db`).

- **Sem modelos**: sistema de regras em Python, só com a biblioteca padrão. Nenhum modelo de linguagem, treinado
  ou pesos a baixar.
- **Offline**: sem rede e sem APIs; roda em CPU, em poucos segundos para dezenas de documentos.
- **Determinístico**: mesma entrada, mesma saída, byte a byte.
- **Independente da base de desenvolvimento**: tudo é lido do `.db` recebido na execução.

## Execução

Requisito: Python 3.10 ou mais novo. Não há dependência a instalar.

```bash
bash run.sh <caminho_db> <pasta_txt> <arquivo_saida>
```

- `<caminho_db>`: base canônica SQLite (tabela `documentos`).
- `<pasta_txt>`: um `.txt` UTF-8 por documento; o nome do arquivo é o `documento_id`.
- `<arquivo_saida>`:
  - terminado em `.csv`: grava ali o CSV de submissão (`documento_id,citacoes`) e os JSONs do contrato
    (schema 1.2) em `<arquivo_saida sem .csv>_json/`;
  - qualquer outro caminho: tratado como pasta, recebe um JSON por documento e o `submission.csv`.

```bash
bash run.sh data/desafio1_bracis.db data/txt saida/submission.csv   # saida/submission.csv + saida/submission_json/
bash run.sh /dados/novo.db /dados/txt /dados/saida                   # /dados/saida/*.json + /dados/saida/submission.csv
```

Caminhos relativos valem a partir de onde o comando é chamado. Outro interpretador: `PYTHON=/caminho/python3 bash
run.sh ...`. Um resumo da base lida sai em stderr.

Só os JSONs: `python -m src.run --input <pasta_txt> --output <pasta_json> --db <db>` (`--debug` inclui o motivo de
cada decisão; `--limite-segundos` limita o tempo por peça, 120 s por padrão).

## Abordagem

1. **Índice da base** (`src/indice.py`, montado uma vez, ~1 s). A busca de texto (FTS) não é usada, porque devolve
   todo acórdão que *menciona* um número. O índice lê o **cabeçalho** de cada acórdão (classe processual, número e
   UF, num formato por tribunal; tribunal ausente ou desconhecido é inferido pelo texto) e monta o dicionário
   número → registros. Súmulas e artigos são identificados pela primeira linha do registro com as mesmas funções
   que leem as citações nas peças, para que "Súmula nº 7 do STJ" e "Art. 5º da CF/88" gerem a mesma chave dos
   dois lados.
2. **Detecção** (`src/detectar.py`). Um número só vira citação de acórdão se houver uma classe processual à
   esquerda ("AgInt no REsp"), o que descarta autos, OAB, folhas e valores. Súmulas, artigos de lei
   (`src/leis.py`), Temas e referências sem número ("julgado do STF de 2024, Rel. Min. X") têm padrões próprios.
   A leitura tolera o ruído do nível 2: abreviações (`src/vocabulario.py`), número com espaços, quebrado ou sem
   pontos, separadores de UF, confusões de OCR (0↔O, 1↔l, 5↔S, m↔rn, e↔c) e quebras de linha.
   `src/normalizar.py` leva as variantes a uma forma canônica, inclusive o número CNJ.
3. **Resolução** (`src/resolver.py`). Conta os candidatos no índice: um → `real` (com o `id`); nenhum →
   `inventada`; vários sem desempate → `incompleta`; sem número → `incompleta`. O desempate usa o tribunal (dito
   na citação, implícito no número CNJ ou na classe), a classe e a UF.

**Confiança.** Cada decisão cai numa situação (por exemplo, "real: número único" ou "inventada: número ausente,
texto com ruído"), e a confiança vem do perfil `padrao` em `src/resolver.py`: 1,0 nas situações em que nenhum teste
registrou erro, 0,99 em "inventada com texto ruidoso" (4 erros em 40.960, ambiguidades entre sigla e número com
OCR) e valores menores onde o rótulo correto é incerto (súmula sem tribunal, número ambíguo, duplicata, Tema). A
tabela de evidências está no próprio arquivo. Perfis alternativos: `CONFIANCA_PERFIL=conservador|um bash run.sh ...`.

## Estrutura

```
run.sh              ponto de entrada (bash run.sh <db> <pasta_txt> <arquivo_saida>)
src/run.py          CLI: lê os .txt e grava um JSON por documento (schema 1.2)
src/indice.py       índice da base: número -> registros, lido do cabeçalho; chaves de súmulas e artigos
src/detectar.py     detecção de acórdãos, súmulas, temas, artigos e referências incompletas
src/resolver.py     classificação real / inventada / incompleta e confiança
src/normalizar.py   formas canônicas de texto e de número de processo; dígito verificador CNJ
src/vocabulario.py  classes processuais -> siglas canônicas ('Rec. Esp.' -> REsp)
src/leis.py         lei citada -> código ('CPC', 'Lei nº 13.105/2015' -> L13105)
oficial/            arquivos da organização, sem alteração (métrica e conversor JSON -> CSV)
scripts/            validação (não participam da execução)
data/               dados do desafio (não versionados)
```

## Testes

Os scripts de validação usam `pandas` e `numpy` (exigidos pela métrica oficial): `pip install -r requirements.txt`.

```bash
python -m src.run --input data/txt --output out/atual --db data/desafio1_bracis.db
python scripts/avaliar.py out/atual --erros      # nota com a métrica oficial e lista de erros
python scripts/estresse.py                       # citações sintéticas geradas da base: 0 falhas esperadas
python scripts/bancos_modificados.py             # 26 peças contra 7 cópias modificadas da base
python scripts/metamorfico.py                    # 26 peças sob transformações que não mudam a resposta
python scripts/estresse.py --situacoes           # casos e erros por situação do resolvedor
python scripts/calibracao.py                     # acerto por situação ao lado da confiança adotada
python scripts/cenarios_confianca.py             # Brier esperado de cada perfil por taxa de erro
python scripts/testar_indice.py                  # candidatos do índice para cada acórdão do gabarito
python scripts/oraculo.py out/oraculo            # JSONs do próprio gabarito (teto da nota)
```

- **Estresse**: cada acórdão da base em formatos fixos e com ruído de nível 2, inventadas sintéticas e
  aleatórias, incompletas em 14 moldes, leis e súmulas da base e fora dela, e distratores (autos, OAB, folhas,
  valores, frases sem citação) que não podem ser detectados. A seção F usa padrões de redação extraídos de
  decisões públicas do STJ, guardados só como moldes em `scripts/padroes_redacao_real.json` (números, UFs, datas e
  nomes trocados por marcadores) e instanciados com registros da base e números inexistentes.
- **Bases modificadas**: ids trocados, acórdãos e normas removidos, normas com cabeçalho em outro formato e normas
  novas, base sem normas, só com normas, com tribunal desconhecido ou nulo, sem a tabela FTS. O gabarito é ajustado
  conforme a definição do desafio (registro removido → `inventada`; id trocado → id novo).
- **Metamórfico**: reflow das linhas, espaços e quebras, caixa alta, rodapé de citação, parênteses, espaço
  inseparável, hifenização e, com `--prosa PASTA`, parágrafos de decisões reais intercalados.

## Resultados locais

- 26 peças: nota 1,1000 (macro-F1 = 1 nos dois níveis).
- Estresse sem falhas, inclusive gerado a partir de cada base modificada; macro-F1 = 1 nas 7 bases modificadas e
  nas transformações metamórficas.
- Saídas idênticas entre execuções e entre sementes de hash diferentes, em pasta limpa, sem rede e com Python sem
  pacotes instalados.
