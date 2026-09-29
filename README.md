# Caça-Alucinações — BRACIS 2026 × Jusbrasil

Detecta citações de jurisprudência e de lei em peças jurídicas e classifica cada uma
como `real`, `inventada` ou `incompleta`, resolvendo as reais para o `id` da base canônica.

## Estrutura

```
data/        dados do desafio (NÃO versionados): txt/, desafio1_bracis.db, goldenset_offsets.csv
oficial/     arquivos da organização, sem alteração: kaggle_metric.py, json_to_submission.py
src/run.py          ponto de entrada da solução (contrato --input/--output)
src/normalizar.py   formas canônicas: número de processo (com correção de OCR), dígito verificador CNJ
src/vocabulario.py  classes processuais -> siglas canônicas ('Rec. Esp.' -> REsp)
src/indice.py       ficha de identidade de cada registro da base, lida do cabeçalho
src/leis.py         reconhece a lei citada ('CPC', 'Lei nº 13.105/2015' -> L13105)
src/detectar.py     acha as citações no texto: acórdãos, súmulas, temas, artigos, incompletas
src/resolver.py     consulta o índice e decide real / inventada / incompleta (+ confiança)
scripts/            ferramentas de desenvolvimento (avaliação, oráculo, teste do índice, estresse)
out/         saídas geradas (NÃO versionadas)
```

## Requisitos

Python 3.11+ e `pip install -r requirements.txt`.

## Comandos

```bash
# 1. rodar a solução sobre os documentos (--debug grava o motivo de cada decisão no JSON)
python -m src.run --input data/txt --output out/atual --db data/desafio1_bracis.db

# 2. medir com a métrica oficial (e listar os erros)
python scripts/avaliar.py out/atual
python scripts/avaliar.py out/atual --erros

# 3. gerar o CSV de submissão do Kaggle
python oficial/json_to_submission.py out/atual submission.csv
```

## Estado atual

- [x] Estrutura, avaliador local com a métrica oficial, oráculo para testar o encanamento
- [x] Índice da base canônica (`python scripts/testar_indice.py`: 77/77 reais achadas, 0 inventadas com candidato)
- [x] Detecção de citações (níveis 1 e 2)
- [x] Classificação (real / inventada / incompleta) — nota local 1,0989 (teto 1,100)
- [x] Teste de estresse: os 996 acórdãos citados em 5 formatos, 0 falhas (`python scripts/estresse.py`)
- [ ] Confiança calibrada
- [ ] Dockerfile + entrypoint
