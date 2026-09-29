"""Teste de estresse: cita CADA acórdão da base de vários jeitos e confere se o sistema acha o registro.

O gabarito de desenvolvimento só cita ~80 acórdãos; o conjunto cego vai citar outros. Aqui
geramos citações sintéticas para os 996 acórdãos, com e sem ruído, e medimos:
    real certo   -> achou o registro certo
    ambígua      -> o número+classe existe em 2+ registros diferentes (a organização garante
                    que citações reais nunca apontam para esses; 'incompleta' é o esperado)
    falhou       -> não detectou, ou classificou errado  <- é isso que precisa ir a zero

Uso:  python scripts/estresse.py [--mostrar 20]
"""
import argparse
import random
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from src.detectar import detectar         # noqa: E402
from src.indice import Indice             # noqa: E402
from src.resolver import resolver         # noqa: E402

SIGLA_ESCRITA = {"ED": "EDcl", "EDiv": "EDv", "REspE": "REspe", "Apl": "APL",
                 "CauInomCrim": "Cautelar Inominada Criminal", "Incomp": "Incompatibilidade"}
OCR = {"0": "O", "1": "l", "5": "S", "6": "G", "9": "g", "8": "B"}


def numero_com_pontos(n: str) -> str:
    return n if "." in n else f"{int(n):,}".replace(",", ".")


def ruido_ocr(s: str, rng: random.Random) -> str:
    """Troca UM dígito por uma letra parecida e às vezes quebra a linha no meio do número."""
    pos = [i for i, ch in enumerate(s) if ch in OCR]
    if pos:
        i = rng.choice(pos)
        s = s[:i] + OCR[s[i]] + s[i + 1:]
    if len(s) > 6 and rng.random() < 0.5:
        k = rng.randrange(2, len(s) - 2)
        s = s[:k] + "\n" + s[k:]
    return s


def formatos(f, rng):
    siglas = [SIGLA_ESCRITA.get(s, s) for s in f.classe]
    uf = f"/{f.uf}" if f.uf else ""
    if f.tribunal == "TST":
        base = f"{'-'.join(siglas)}-{f.numero.lstrip('0')}"
        yield "sigla", f"processo nº TST-{base}"
        yield "sem 'processo nº'", base
        yield "ocr", f"processo nº TST-{'-'.join(siglas)}-{ruido_ocr(f.numero.lstrip('0'), rng)}"
        return
    num = numero_com_pontos(f.numero)
    yield "sigla", " no ".join(siglas) + f" nº {num}{uf}"
    if f.classe_bruta:
        yield "por extenso", f"{f.classe_bruta.title()} nº {num}{uf}"
    yield "sem pontuação", " no ".join(siglas) + f" n. {f.numero.replace('.', '').replace('-', '')}" + \
        (f" - {f.uf}" if f.uf else "")
    yield "ocr", " no ".join(siglas) + f" nº {ruido_ocr(num, rng)}" + (f" ({f.uf})" if f.uf else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mostrar", type=int, default=15)
    args = ap.parse_args()
    rng = random.Random(0)
    ix = Indice(str(RAIZ / "data" / "desafio1_bracis.db"))
    por_formato = {}
    falhas = []
    for f in ix.fichas:
        if f.natureza != "acordao" or not f.numero or not f.classe:
            continue
        mesmo_texto = {g.id for g in ix.fichas if g.assinatura == f.assinatura}
        for nome, cit in formatos(f, rng):
            texto = f"Não destoa desse entendimento o {cit}, de clareza solar quanto ao ponto."
            cs = [c for c in detectar(texto) if c.especie == "acordao"]
            cont = por_formato.setdefault(nome, Counter())
            if not cs:
                cont["falhou"] += 1
                falhas.append((nome, f.documento_id, cit, "não detectada"))
                continue
            r = resolver(cs[0], ix)
            if r["classificacao"] == "real" and r["id_canonico"] in mesmo_texto:
                cont["real certo"] += 1
            elif r["classificacao"] == "incompleta" and "ambíguo" in r["motivo"]:
                cont["ambígua"] += 1
            else:
                cont["falhou"] += 1
                falhas.append((nome, f.documento_id, cit, f"{r['classificacao']} | {r['motivo']}"))

    print(f"{'formato':<18}{'real certo':>11}{'ambígua':>9}{'falhou':>8}")
    for nome, c in por_formato.items():
        print(f"{nome:<18}{c['real certo']:>11}{c['ambígua']:>9}{c['falhou']:>8}")
    if falhas:
        print(f"\nprimeiras {args.mostrar} falhas:")
        for x in falhas[:args.mostrar]:
            print("  ", x)


if __name__ == "__main__":
    main()
