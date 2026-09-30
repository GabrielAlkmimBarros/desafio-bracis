"""Evidência da calibração: acerto por situação, ao lado da confiança adotada.

Para cada situação do resolvedor (src/resolver.py, tabela CONFIANCA) mostra:
    dev      citações das 26 peças casadas com o gabarito (IoU >= 0,5) e a fração correta
    estresse citações sintéticas do scripts/estresse.py (seções A2, B, C, D) detectadas e a
             fração correta
Só entram citações casadas/detectadas, porque é sobre elas que a métrica calcula o Brier.

O gabarito é usado aqui só para MEDIR; os valores da tabela não são ajustados a ele.

Com --raiz, roda o mesmo levantamento sobre outra cópia do código (ex.: um worktree do commit
anterior às correções do estresse) — é assim que se mede o acerto em variações que o sistema
ainda não tinha visto:
    git worktree add /tmp/pre 61cedf0 && ln -s "$PWD/data" /tmp/pre/data
    python scripts/calibracao.py --raiz /tmp/pre

Com --db, o estresse é gerado a partir de outra base (ex.: as bases modificadas de
scripts/bancos_modificados.py --manter PASTA); as 26 peças só entram com a base original.

Uso:  python scripts/calibracao.py [--raiz CAMINHO] [--db BASE]
"""
import argparse
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
# mesmo critério de ruído do detector atual (src/detectar.py, tem_ruido), para versões antigas
_RUIDO = re.compile(r"\n|\d\s+\d|\d\s*[.\-]\s+\d|\d[.\-]\s*[.\-]|(?<=\d)[^\W\doaºª°_]|[^\W\d_ºª°](?=\d)")


def situacao(c, r, trecho="") -> str:
    """A situação vem no começo do motivo ('[real: número único] ...'). Versões antigas do resolvedor
    não a escrevem; aí ela é deduzida do caminho da decisão, com os mesmos nomes das atuais sempre que
    possível (real de acórdão fica agregado: a versão antiga não separa único/desempate)."""
    m = r.get("motivo", "")
    if m.startswith("["):
        return m[1:m.index("]")]
    cl, ruido = r["classificacao"], ("texto com ruído" if _RUIDO.search(trecho) else "texto limpo")
    if c.especie in ("sumula", "artigo"):
        if cl == "incompleta":
            return "incompleta: súmula sem tribunal"
        return "real: lei ou súmula da base" if cl == "real" else f"inventada: lei ou súmula fora da base, {ruido}"
    if c.especie == "tema":
        return "inventada: tema"
    if c.especie == "incompleta":
        return "incompleta: sem número"
    if cl == "real":
        return "real: acórdão (versão antiga, agregado)"
    return f"inventada: número ausente, {ruido}" if cl == "inventada" else "incompleta: número ambíguo"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raiz", type=Path, default=RAIZ, help="cópia do código a medir (padrão: esta)")
    ap.add_argument("--db", type=Path, default=RAIZ / "data" / "desafio1_bracis.db", help="base (padrão: a original)")
    args = ap.parse_args()
    # o código medido vem da --raiz e é importado PRIMEIRO: o estresse, importado depois,
    # reaproveita esse mesmo pacote `src` já carregado
    sys.path.insert(0, str(args.raiz.resolve()))
    from src.detectar import detectar                  # noqa: E402
    from src.indice import Indice                       # noqa: E402
    from src.resolver import resolver                   # noqa: E402
    sys.path.insert(0, str(RAIZ / "oficial"))
    sys.path.insert(0, str(RAIZ / "scripts"))           # geradores do estresse ATUAL, sempre
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
            dev[situacao(cs[pi], r, texto[cs[pi].inicio:cs[pi].fim])][ok] += 1

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
        est[situacao(cs[0], r, texto[cs[0].inicio:cs[0].fim])][ok] += 1

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

    try:
        from src.resolver import CONFIANCA             # noqa: E402
    except ImportError:
        CONFIANCA = {}
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
