"""Testa o índice contra o gabarito: as citações de acórdão do gabarito são encontradas?

Para cada citação de acórdão (real ou inventada) do gabarito:
  1. extrai o número do trecho        (normalizar.extrair_numero)
  2. procura no índice                (Indice.buscar_numero)
  3. se vier mais de um candidato, tenta desempatar pela classe da citação

O que esperamos ver:
  - real      -> o id do gabarito está entre os candidatos (de preferência, um só)
  - inventada -> nenhum candidato (se houver, é uma armadilha que o resolvedor terá de tratar)

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
from src.normalizar import extrair_numero                               # noqa: E402
from src.vocabulario import TRIBUNAIS_DA_CLASSE, canonizar_classe, classe_base  # noqa: E402

JUSTICA = {"5": "TST", "6": "TSE", "7": "STM"}


def eh_sumula(trecho):
    return bool(re.search(r"s[úu]m(?:ula|\.)", trecho, re.I) or "5úmula" in trecho)


def classe_da_citacao(trecho):
    """Classe = o que vem antes do primeiro dígito do trecho."""
    antes = re.split(r"\d", trecho, maxsplit=1)[0]
    return tuple(s for s in canonizar_classe(antes) if not s.startswith("?"))


def filtrar(candidatos, numero, classe):
    """Protótipo do desempate (vira o resolvedor no próximo passo)."""
    passos = [("número", candidatos)]
    # 1) tribunal compatível com a classe (REsp só existe no STJ, RE só no STF...)
    trib_ok = TRIBUNAIS_DA_CLASSE.get(classe_base(classe) or "", None)
    if numero and "." in numero:
        trib_ok = {JUSTICA.get(numero[16])}
    if trib_ok:
        candidatos = [f for f in candidatos if f.tribunal in trib_ok]
        passos.append(("tribunal", candidatos))
    # 2) cadeia de classe idêntica
    if len(candidatos) > 1:
        iguais = [f for f in candidatos if f.classe == classe]
        if iguais:
            candidatos = iguais
            passos.append(("classe exata", candidatos))
    # 3) mesma classe-base
    if len(candidatos) > 1:
        mesma_base = [f for f in candidatos if classe_base(f.classe) == classe_base(classe)]
        if mesma_base:
            candidatos = mesma_base
            passos.append(("classe-base", candidatos))
    return candidatos, passos


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
        final, passos = filtrar(brutos, numero, classe)
        ids = {str(f.id) for f in final}
        linha = (f"{r.nivel} {r.documento_id} {r.citacao_id:>4} {r.classificacao:<9} "
                 f"{trecho.replace(chr(10), ' ')!r:58.58} num={numero} classe={classe} "
                 f"-> {' > '.join(f'{n}:{len(c)}' for n, c in passos)}")
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
        else:
            if final:
                cont["inv_com_candidato"] += 1
                problemas.append("INVENTADA COM CANDIDATO " + linha +
                                 f" {[(f.documento_id, f.tribunal, f.classe) for f in final]}")
            else:
                cont["inv_vazio"] += 1

    n_real = (alvo.classificacao == "real").sum()
    n_inv = (alvo.classificacao == "inventada").sum()
    print(f"CITAÇÕES DE ACÓRDÃO NO GABARITO: {n_real} reais, {n_inv} inventadas\n")
    print(f"  reais achadas com 1 candidato só ........ {cont['real_ok_unico']}")
    print(f"  reais achadas após desempate por classe .. {cont['real_ok_desempatado']}")
    print(f"  reais ainda ambíguas .................... {cont['real_ambiguo']}")
    print(f"  reais NÃO achadas ....................... {cont['real_nao_achado']}")
    print(f"  inventadas sem candidato (correto) ....... {cont['inv_vazio']}")
    print(f"  inventadas COM candidato (armadilha) ..... {cont['inv_com_candidato']}")
    if problemas:
        print("\nPROBLEMAS:")
        for p in problemas:
            print("  " + p)


if __name__ == "__main__":
    main()
