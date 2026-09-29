"""Avalia uma pasta de JSONs contra o gabarito usando a MÉTRICA OFICIAL.

Uso:
    python scripts/avaliar.py out/                 # nota + quadro por nível e classe
    python scripts/avaliar.py out/ --erros         # + lista de cada erro, citação por citação
    python scripts/avaliar.py out/ --erros --nivel 2

Toda a contagem (alinhamento por IoU, TP/FP/FN, penalidade, bônus) vem das funções
de oficial/kaggle_metric.py — este script só monta as tabelas de entrada no formato
do Kaggle e imprime os resultados de um jeito legível.
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "oficial"))
import kaggle_metric as km                     # noqa: E402
from json_to_submission import encode          # noqa: E402

GABARITO = RAIZ / "data" / "goldenset_offsets.csv"


def carregar_gabarito(caminho=GABARITO) -> pd.DataFrame:
    g = pd.read_csv(caminho, dtype=str, encoding="utf-8-sig").fillna("")
    g["inicio"] = g["inicio"].astype(int)
    g["fim"] = g["fim"].astype(int)
    g["nivel"] = g["nivel"].astype(int)
    return g


def gabarito_para_solution(g: pd.DataFrame) -> pd.DataFrame:
    """Converte o goldenset (1 linha por citação) na solution do Kaggle (1 linha por documento)."""
    linhas = []
    for doc, grupo in g.groupby("documento_id", sort=True):
        cel = "|".join(f"{r.inicio},{r.fim},{r.classificacao},{r.id_canonico or '-'}"
                       for r in grupo.itertuples())
        linhas.append({"documento_id": doc, "nivel": int(grupo["nivel"].iloc[0]), "citacoes": cel})
    return pd.DataFrame(linhas)


def carregar_predicoes(pasta: Path) -> tuple[pd.DataFrame, dict]:
    """Lê os JSONs e devolve (submission no formato Kaggle, JSONs por documento)."""
    linhas, jsons = [], {}
    for arq in sorted(pasta.glob("*.json")):
        doc = json.loads(arq.read_text(encoding="utf-8"))
        doc_id = doc.get("documento_id") or arq.stem
        jsons[doc_id] = doc
        linhas.append({"documento_id": doc_id, "citacoes": encode(doc)})
    if not linhas:
        raise SystemExit(f"nenhum .json em {pasta}")
    return pd.DataFrame(linhas), jsons


def quadro(solution, submission):
    """Recalcula por nível com as funções oficiais, guardando TP/FP/FN para exibir."""
    sub = submission.drop_duplicates("documento_id").set_index("documento_id")
    accs = {}
    for _, linha in solution.iterrows():
        doc = linha["documento_id"]
        golds = km._parse_solution_cell(linha["citacoes"], doc)
        preds = km._parse_submission_cell(sub.loc[doc, "citacoes"] if doc in sub.index else "", doc)
        acc = accs.setdefault(int(linha["nivel"]), km._novo_acumulador())
        km._acumular_documento(acc, golds, preds)
    return accs


def imprimir_quadro(accs, resultado):
    print(f"\nNOTA FINAL: {resultado['score_final']:.5f}    (= (1·N1 + 2·N2) / 3)\n")
    for nivel, acc in sorted(accs.items()):
        r = resultado["niveis"][nivel]
        print(f"NÍVEL {nivel}  score {r['score']:.5f}  = macroF1 {r['macro_f1']:.4f}"
              f" × (1 − 0,5·τ={r['tau']:.3f}) × (1 + bônus {r['b']:.4f})")
        print(f"   {'classe':<11}{'F1':>7}{'TP':>6}{'FP':>6}{'FN':>6}{'gabarito':>10}")
        for c in km.CLASSES:
            f1 = r["f1_por_classe"].get(c)
            f1s = f"{f1:.3f}" if f1 is not None else "  —  "
            print(f"   {c:<11}{f1s:>7}{acc['tp'][c]:>6}{acc['fp'][c]:>6}{acc['fn'][c]:>6}"
                  f"{acc['suporte'][c]:>10}")
        print()


def listar_erros(g, jsons, nivel=None):
    """Mostra cada erro: o que o gabarito esperava x o que foi previsto."""
    print("ERROS (citação por citação)")
    n = 0
    for doc, grupo in g.groupby("documento_id", sort=True):
        if nivel and int(grupo["nivel"].iloc[0]) != nivel:
            continue
        golds = [dict(inicio=r.inicio, fim=r.fim, classe=r.classificacao,
                      id=r.id_canonico, trecho=r.trecho, cid=r.citacao_id)
                 for r in grupo.itertuples()]
        preds = []
        for c in jsons.get(doc, {}).get("citacoes", []):
            res = c.get("resolucao") or {}
            preds.append(dict(inicio=c["inicio"], fim=c["fim"], classe=c["classificacao"],
                              id=str(res.get("id_canonico") or ""), trecho=c.get("trecho", "")))
        pares, g_sem, p_sem = km._casar(golds, preds)
        linhas = []
        for gi, pi in pares:
            gd, pd_ = golds[gi], preds[pi]
            if gd["classe"] != pd_["classe"]:
                grave = "  <<< ERRO GRAVE" if (gd["classe"], pd_["classe"]) == ("inventada", "real") else ""
                linhas.append((gd["inicio"], f"[CLASSE] {gd['cid']:>4} esperado {gd['classe']:<10}"
                               f" previsto {pd_['classe']:<10} | {gd['trecho']!r}{grave}"))
            elif gd["classe"] == "real" and pd_["id"].lstrip("0") != gd["id"].lstrip("0"):
                linhas.append((gd["inicio"], f"[LINK]   {gd['cid']:>4} esperado id {gd['id']}"
                               f" previsto {pd_['id']} | {gd['trecho']!r}"))
        for gi in g_sem:
            gd = golds[gi]
            linhas.append((gd["inicio"], f"[FALTOU] {gd['cid']:>4} {gd['classe']:<10}"
                           f" não extraída | {gd['trecho']!r}"))
        casados = [golds[gi] for gi, _ in pares]
        for pi in p_sem:
            p = preds[pi]
            if any(km._contida(p, gd) for gd in casados):
                continue   # regra EXTRA: pedaço de uma citação já casada não conta
            linhas.append((p["inicio"], f"[SOBROU]      previsto {p['classe']:<10}"
                           f" sem par no gabarito | {p['trecho']!r} @{p['inicio']}"))
        if linhas:
            print(f"\n{doc}")
            for _, t in sorted(linhas):
                print("  " + t)
                n += 1
    print(f"\ntotal de erros listados: {n}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pasta", type=Path, help="pasta com os JSONs previstos")
    ap.add_argument("--erros", action="store_true", help="listar cada erro")
    ap.add_argument("--nivel", type=int, choices=[1, 2], help="filtrar a lista de erros por nível")
    args = ap.parse_args()

    g = carregar_gabarito()
    solution = gabarito_para_solution(g)
    submission, jsons = carregar_predicoes(args.pasta)
    resultado = km.avaliar(solution, submission)          # nota oficial
    imprimir_quadro(quadro(solution, submission), resultado)
    if args.erros:
        listar_erros(g, jsons, args.nivel)


if __name__ == "__main__":
    main()
