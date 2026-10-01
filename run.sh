#!/usr/bin/env bash
# Uso: bash run.sh <caminho_db> <pasta_txt> <arquivo_saida>
#
# <arquivo_saida> terminado em .csv: grava ali o submission.csv e os JSONs (schema 1.2) em <arquivo_saida>_json/.
# Qualquer outro nome é tratado como pasta: os JSONs vão dentro dela, junto com submission.csv.
# Caminhos relativos valem a partir de onde o script é chamado. Sem rede e sem dependências além do Python 3.10+.
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
