#!/usr/bin/env bash
# Ponto de entrada único (formato sugerido pela organização):
#
#     bash run.sh <caminho_db> <pasta_txt> <arquivo_saida>
#
# <arquivo_saida>:
#   - terminado em .csv  -> grava ali o submission.csv (1 linha por documento, formato das submissões)
#                           e os JSONs completos do contrato (schema 1.2) em <arquivo_saida sem .csv>_json/
#   - qualquer outro     -> tratado como PASTA: os JSONs vão dentro dela, junto com submission.csv
# Nos dois casos saem as duas formas: o CSV que a métrica consome e o JSON completo exigido na entrega.
#
# Sem caminhos absolutos: os caminhos recebidos valem a partir de onde o script é chamado, e o código
# é localizado pela posição deste arquivo. Sem rede, sem modelos, só a biblioteca padrão do Python 3.10+.
# Resultado determinístico (nenhuma aleatoriedade; PYTHONHASHSEED fixado por garantia).
set -euo pipefail

if [ "$#" -ne 3 ]; then
    echo "uso: bash run.sh <caminho_db> <pasta_txt> <arquivo_saida>" >&2
    exit 2
fi
DB="$1"; TXT="$2"; SAIDA="$3"
[ -f "$DB" ]  || { echo "erro: base não encontrada: $DB" >&2; exit 2; }
[ -d "$TXT" ] || { echo "erro: pasta de .txt não encontrada: $TXT" >&2; exit 2; }

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || { echo "erro: é preciso Python 3.10 ou mais novo ($PY)" >&2; exit 2; }

case "$SAIDA" in
    *.csv) CSV="$SAIDA"; JSONS="${SAIDA%.csv}_json" ;;
    *)     JSONS="${SAIDA%/}"; CSV="$JSONS/submission.csv" ;;
esac
mkdir -p "$JSONS" "$(dirname "$CSV")"

export PYTHONPATH="$RAIZ${PYTHONPATH:+:$PYTHONPATH}" PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1
"$PY" -m src.run --input "$TXT" --output "$JSONS" --db "$DB"
"$PY" "$RAIZ/oficial/json_to_submission.py" "$JSONS" "$CSV"
