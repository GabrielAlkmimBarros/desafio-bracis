"""Gera JSONs a partir do próprio gabarito — um "sistema perfeito".

Serve para testar o encanamento (JSON → submission → métrica) e para ver o teto
da nota. Não é solução: no conjunto cego não existe gabarito.

Uso:
    python scripts/oraculo.py out_oraculo/                  # sem confiança
    python scripts/oraculo.py out_oraculo/ --confianca 1.0  # com confiança fixa
"""
import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from scripts.avaliar import carregar_gabarito          # noqa: E402
from src.run import ler_texto, montar_json             # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("saida", type=Path)
    ap.add_argument("--confianca", type=float, default=None)
    args = ap.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)

    g = carregar_gabarito()
    for arq in sorted((RAIZ / "data" / "txt").glob("*.txt")):
        texto = ler_texto(arq)
        grupo = g[g["documento_id"] == arq.stem]
        citacoes = [dict(inicio=r.inicio, fim=r.fim, tipo=r.tipo, classificacao=r.classificacao,
                         id_canonico=r.id_canonico or None, confianca=args.confianca)
                    for r in grupo.itertuples()]
        doc = montar_json(arq.stem, texto, citacoes)
        (args.saida / f"{arq.stem}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"oráculo gravado em {args.saida}/")


if __name__ == "__main__":
    main()
