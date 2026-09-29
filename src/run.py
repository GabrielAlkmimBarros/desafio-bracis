"""Ponto de entrada da solução (contrato de execução do desafio).

Uso:
    python -m src.run --input data/txt --output out [--db data/desafio1_bracis.db]

Lê cada .txt de --input e escreve um .json com o mesmo nome-base em --output,
no formato do Contrato de Entrada e Saída (schema 1.2).

Fluxo por documento:  texto --detectar--> candidatas --resolver(índice)--> citações --> JSON
O índice da base é montado uma vez só, no começo (≈1 s).
"""
import argparse
import json
from pathlib import Path

SCHEMA_VERSION = "1.2"


def ler_texto(caminho: Path) -> str:
    # newline="" impede o Python de converter quebras de linha: os offsets do
    # gabarito são contados sobre o texto exatamente como está no arquivo.
    with open(caminho, encoding="utf-8", newline="") as f:
        return f.read()


def encontrar_citacoes(texto: str, indice) -> list[dict]:
    """Recebe o texto de um documento e devolve as citações encontradas.

    Cada citação é um dict com: inicio, fim, tipo, classificacao,
    id_canonico (só para real), confianca e motivo (para depuração).
    """
    from .detectar import detectar
    from .resolver import resolver

    saida = []
    for c in detectar(texto):
        r = resolver(c, indice)
        saida.append(dict(inicio=c.inicio, fim=c.fim, tipo=c.tipo, **r))
    return saida


def montar_json(documento_id: str, texto: str, citacoes: list[dict], debug: bool = False) -> dict:
    saida = []
    for n, c in enumerate(sorted(citacoes, key=lambda c: c["inicio"]), start=1):
        item = {
            "id": f"c{n}",
            "inicio": c["inicio"],
            "fim": c["fim"],
            "trecho": texto[c["inicio"]:c["fim"]],   # sempre recortado do texto: nunca diverge do span
            "tipo": c["tipo"],
            "classificacao": c["classificacao"],
            "resolucao": (
                {"fonte": "jusbrasil", "id_canonico": str(c["id_canonico"])}
                if c["classificacao"] == "real" else None
            ),
        }
        if c.get("confianca") is not None:
            item["confianca"] = round(float(c["confianca"]), 4)
        if debug and c.get("motivo"):
            item["_motivo"] = c["motivo"]          # só para depuração; fora do contrato
        saida.append(item)
    return {"schema_version": SCHEMA_VERSION, "documento_id": documento_id, "citacoes": saida}


def main() -> None:
    ap = argparse.ArgumentParser(description="Caça-Alucinações: detecta e classifica citações.")
    ap.add_argument("--input", required=True, help="pasta com os .txt")
    ap.add_argument("--output", required=True, help="pasta onde gravar os .json")
    ap.add_argument("--db", default="data/desafio1_bracis.db", help="base canônica (SQLite)")
    ap.add_argument("--debug", action="store_true", help="inclui no JSON o motivo de cada decisão")
    args = ap.parse_args()

    entrada, saida = Path(args.input), Path(args.output)
    saida.mkdir(parents=True, exist_ok=True)
    arquivos = sorted(entrada.glob("*.txt"))
    if not arquivos:
        raise SystemExit(f"nenhum .txt em {entrada}")

    from .indice import Indice
    indice = Indice(args.db)

    for arq in arquivos:
        texto = ler_texto(arq)
        citacoes = encontrar_citacoes(texto, indice)
        doc = montar_json(arq.stem, texto, citacoes, debug=args.debug)
        (saida / f"{arq.stem}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(arquivos)} documentos processados -> {saida}/")


if __name__ == "__main__":
    main()
