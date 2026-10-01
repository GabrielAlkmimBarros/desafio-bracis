"""Confere o índice contra o gabarito: cada acórdão real deve ter um único candidato, e cada inventado nenhum.

Uso:  python scripts/testar_indice.py [--detalhes]
"""
import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from scripts.avaliar import carregar_gabarito                           # noqa: E402
from src.indice import Indice                                           # noqa: E402
from src.normalizar import digitos, numero_canonico                     # noqa: E402
from src.resolver import candidatos_acordao                             # noqa: E402
from src.vocabulario import canonizar_classe                            # noqa: E402

_LETRAS_OCR = set("OoQDlLIi|!SsgqGbBZz")


def _tipo_pedaco(p):
    miolo = re.sub(r"[.\-–—]", "", p)
    if not miolo:
        return "ligacao"
    if any(c.isdigit() for c in miolo) and all(c.isdigit() or c in _LETRAS_OCR for c in miolo):
        return "numero"
    return None


def extrair_numero(trecho):
    """Número de processo dentro do trecho do gabarito, na forma canônica ('AgInt no RESP 21737l8 - SP' -> '2173718')."""
    t = re.sub(r"(?<=[A-Za-z])-(?=\d)", " ", trecho)
    t = re.sub(r"-(?=[A-Za-z]{2}\b)", " ", t)
    corridas, atual = [], []
    for p in re.split(r"[\s/(),;:]+", t):
        if not p:
            continue
        tipo = _tipo_pedaco(p)
        if tipo == "numero" or (tipo == "ligacao" and atual):
            atual.append(p)
        elif atual:
            corridas.append(atual)
            atual = []
    if atual:
        corridas.append(atual)
    if not corridas:
        return None
    return numero_canonico("".join(max(corridas, key=lambda c: len(digitos("".join(c))))))


def eh_sumula(trecho):
    return bool(re.search(r"s[úu]m(?:ula|\.)", trecho, re.I) or "5úmula" in trecho)


def classe_da_citacao(trecho):
    antes = re.split(r"\d", trecho, maxsplit=1)[0]
    return tuple(s for s in canonizar_classe(antes) if not s.startswith("?"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detalhes", action="store_true")
    args = ap.parse_args()

    ix = Indice(str(RAIZ / "data" / "desafio1_bracis.db"))
    print(ix.resumo(), "\n")
    g = carregar_gabarito()
    alvo = g[(g.tipo == "jurisprudencia") & g.classificacao.isin(["real", "inventada"])]
    alvo = alvo[~alvo.trecho.apply(eh_sumula)]

    cont = {"real_ok_unico": 0, "real_ok_desempatado": 0, "real_ambiguo": 0, "real_nao_achado": 0,
            "inv_vazio": 0, "inv_com_candidato": 0}
    problemas = []
    for r in alvo.itertuples():
        trecho = r.trecho.replace("\\n", "\n")
        numero = extrair_numero(trecho)
        classe = classe_da_citacao(trecho)
        brutos = ix.buscar_numero(numero) if numero else []
        final, passos = candidatos_acordao(ix, numero, classe) if numero else ([], ["número:0"])
        ids = {str(f.id) for f in final}
        linha = (f"{r.nivel} {r.documento_id} {r.citacao_id:>4} {r.classificacao:<9} "
                 f"{trecho.replace(chr(10), ' ')!r:58.58} num={numero} classe={classe} -> {' > '.join(passos)}")
        if r.classificacao == "real":
            if r.id_canonico in ids and len(ids) == 1:
                cont["real_ok_unico" if len(brutos) == 1 else "real_ok_desempatado"] += 1
                if len(brutos) > 1 and args.detalhes:
                    print("  desempatado:", linha)
            elif r.id_canonico in ids:
                cont["real_ambiguo"] += 1
                problemas.append("AMBÍGUO   " + linha + f" candidatos={[(f.documento_id, f.classe) for f in final]}")
            else:
                cont["real_nao_achado"] += 1
                problemas.append("NÃO ACHOU " + linha + f" brutos={[(f.documento_id, f.tribunal, f.classe) for f in brutos]}")
        elif final:
            cont["inv_com_candidato"] += 1
            problemas.append("INVENTADA COM CANDIDATO " + linha + f" {[(f.documento_id, f.tribunal, f.classe) for f in final]}")
        else:
            cont["inv_vazio"] += 1

    n_real = (alvo.classificacao == "real").sum()
    n_inv = (alvo.classificacao == "inventada").sum()
    print(f"CITAÇÕES DE ACÓRDÃO NO GABARITO: {n_real} reais, {n_inv} inventadas\n")
    print(f"  reais achadas com 1 candidato só ........ {cont['real_ok_unico']}")
    print(f"  reais achadas após desempate ............ {cont['real_ok_desempatado']}")
    print(f"  reais ainda ambíguas .................... {cont['real_ambiguo']}")
    print(f"  reais NÃO achadas ....................... {cont['real_nao_achado']}")
    print(f"  inventadas sem candidato (correto) ....... {cont['inv_vazio']}")
    print(f"  inventadas COM candidato ................. {cont['inv_com_candidato']}")
    if problemas:
        print("\nPROBLEMAS:")
        for p in problemas:
            print("  " + p)


if __name__ == "__main__":
    main()
