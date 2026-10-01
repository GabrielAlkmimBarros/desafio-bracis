# Caça-Alucinações — BRACIS 2026 x Jusbrasil
#
# A solução usa só a biblioteca padrão do Python (sem rede, sem modelos, sem pip): numpy e pandas do
# requirements.txt servem apenas às ferramentas de avaliação em scripts/, que não rodam na entrega.
#
#   docker build -t bracis-solution .
#   docker run --rm \
#       -v "/caminho/desafio1_bracis.db:/data/desafio1_bracis.db:ro" \
#       -v "/caminho/txt:/data/txt:ro" \
#       -v "/caminho/saida:/saida" \
#       bracis-solution /data/desafio1_bracis.db /data/txt /saida
#
# Os argumentos são os do run.sh: <caminho_db> <pasta_txt> <arquivo_saida>. Em /saida ficam um JSON por
# documento (schema 1.2) e o submission.csv.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0

WORKDIR /app
COPY run.sh ./
COPY src/ ./src/
COPY oficial/ ./oficial/

ENTRYPOINT ["bash", "/app/run.sh"]
