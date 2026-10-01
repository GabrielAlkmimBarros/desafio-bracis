"""Acerto por situação do resolvedor nas 26 peças e no estresse (seções A2, B, C, D), ao lado da confiança adotada.

Uso:  python scripts/calibracao.py [--db BASE]
"""
import argparse
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]


def situacao(r) -> str:
    """A situação vem no começo do motivo: '[real: número único] ...'."""
    m = r["motivo"]
    return m[1:m.index("]")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=RAIZ / "data" / "desafio1_bracis.db",
                    help="base (as 26 peças só entram com a original)")
    args = ap.parse_args()
    sys.path.insert(0, str(RAIZ))
    from src.detectar import detectar                   # noqa: E402
    from src.indice import Indice                       # noqa: E402
    from src.resolver import CONFIANCA, resolver        # noqa: E402
    sys.path.insert(0, str(RAIZ / "oficial"))
    sys.path.insert(0, str(RAIZ / "scripts"))
    import estresse as E                                # noqa: E402
    import kaggle_metric as km                          # noqa: E402
    from avaliar import carregar_gabarito              # noqa: E402

    ix = Indice(str(args.db))
    dev, est = defaultdict(Counter), defaultdict(Counter)
    base_original = args.db.resolve() == (RAIZ / "data" / "desafio1_bracis.db").resolve()

    # ---- 26 peças
    g = carregar_gabarito()
    for arq in sorted((RAIZ / "data" / "txt").glob("*.txt")) if base_original else []:
        with open(arq, encoding="utf-8", newline="") as fh:
            texto = fh.read()
        golds = [dict(inicio=r.inicio, fim=r.fim, classe=r.classificacao, id=r.id_canonico)
                 for r in g[g["documento_id"] == arq.stem].itertuples()]
        cs = detectar(texto)
        rs = [resolver(c, ix) for c in cs]
        preds = [dict(inicio=c.inicio, fim=c.fim) for c in cs]
        pares, _, _ = km._casar(golds, preds)
        for gi, pi in pares:
            gd, r = golds[gi], rs[pi]
            ok = r["classificacao"] == gd["classe"] and (gd["classe"] != "real" or str(r["id_canonico"]) == gd["id"])
            dev[situacao(r)][ok] += 1

    # ---- estresse (mesmos geradores e sementes do scripts/estresse.py)
    rngs = {s: random.Random(s) for s in ("A2", "B", "C", "D")}

    def conta(cit, esperado, ids, rng):
        molde = rng.choice(E.MOLDURAS)
        a = molde.index("{c}")
        texto = molde.format(c=cit)
        alvo = {"inicio": a, "fim": a + len(cit)}
        cs = [c for c in detectar(texto) if km._iou(alvo, {"inicio": c.inicio, "fim": c.fim}) >= 0.5]
        if not cs:
            return
        r = resolver(cs[0], ix)
        if esperado == "real" and r["classificacao"] == "incompleta" and "ambíguo" in r["motivo"]:
            return                                   # registro ambíguo: não há resposta certa conhecida
        ok = r["classificacao"] == esperado and (esperado != "real" or r["id_canonico"] in ids)
        est[situacao(r)][ok] += 1

    for f in [f for f in ix.fichas if f.natureza == "acordao" and f.numero and f.classe]:
        ids = {x.id for x in ix.fichas if x.assinatura == f.assinatura}
        for _, cit in E.formatos_nivel2(f, rngs["A2"]):
            conta(cit, "real", ids, rngs["A2"])
        for _, cit in E.formatos_inventada(f, ix, rngs["B"]):
            conta(cit, "inventada", set(), rngs["B"])
        for _, cit in E.formatos_incompleta(f, rngs["C"]):
            conta(cit, "incompleta", set(), rngs["C"])
    rng = rngs["D"]
    for chave, f in sorted(ix.por_norma.items(), key=str):
        if chave[0] == "artigo":
            falsos = [(E.formatos_artigo(chave[1], n, rng), "inventada", set()) for n in (399,)]
            for gerador, esperado, ids in [(E.formatos_artigo(chave[1], chave[2], rng), "real", {f.id})] + falsos:
                for _, cit in gerador:
                    conta(cit, esperado, ids, rng)
        else:
            falsos = [(E.formatos_sumula(chave[1], chave[2], 998, rng), "inventada", set())]
            for gerador, esperado, ids in [(E.formatos_sumula(chave[1], chave[2], chave[3], rng), "real", {f.id})] + falsos:
                for _, cit in gerador:
                    conta(cit, esperado, ids, rng)

    todas = sorted(set(dev) | set(est) | set(CONFIANCA))
    print(f"{'situação':<56}{'conf.':>6}{'dev n':>8}{'acerto':>8}{'estr. n':>9}{'acerto':>8}")
    for s in todas:
        d, e = dev.get(s, Counter()), est.get(s, Counter())
        nd, ne = d[True] + d[False], e[True] + e[False]
        conf = f"{CONFIANCA[s]:.2f}" if s in CONFIANCA else "—"
        print(f"{s:<56}{conf:>6}{nd:>8}{(d[True] / nd if nd else float('nan')):>8.3f}"
              f"{ne:>9}{(e[True] / ne if ne else float('nan')):>8.3f}")


if __name__ == "__main__":
    main()
