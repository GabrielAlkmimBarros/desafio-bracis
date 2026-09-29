"""Quanto cada perfil de confiança ganharia ou perderia no conjunto oculto, conforme a taxa de erro.

A classificação é a mesma em todos os perfis; só muda a confiança, que entra no bônus:
    score = s · (1 + 0,10 · (1 − Brier)),   Brier = média de (confiança − acertou)² nas citações casadas
Então a diferença entre perfis é s · 0,10 · (Brier_A − Brier_B) — aqui com s ≈ 1.

A composição de situações do conjunto oculto é aproximada pela das 26 peças (a organização diz que
a distribuição de classes é equivalente). Para cada taxa de erro e, dois modelos:
    uniforme     toda citação casada erra com probabilidade e
    concentrado  os mesmos e·N erros caem só nas inventadas (acórdão, lei e súmula) — onde as versões
                 que ainda não conheciam as variações do estresse erraram (ver scripts/calibracao.py)
Brier esperado por situação s com confiança c e taxa de erro e_s:  (1 − e_s)·(1 − c)² + e_s·c²

Uso:  python scripts/cenarios_confianca.py
"""
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from src.detectar import detectar             # noqa: E402
from src.indice import Indice                 # noqa: E402
from src.resolver import PERFIS, resolver     # noqa: E402

TAXAS = [0.0, 0.005, 0.01, 0.02, 0.05, 0.10]


def situacoes_por_nivel(ix):
    niveis = {1: Counter(), 2: Counter()}
    for arq in sorted((RAIZ / "data" / "txt").glob("*.txt")):
        with open(arq, encoding="utf-8", newline="") as fh:
            texto = fh.read()
        nivel = 1 if "_n1_" in arq.stem else 2
        for c in detectar(texto):
            m = resolver(c, ix)["motivo"]
            niveis[nivel][m[1:m.index("]")]] += 1
    return niveis


def brier_esperado(cont: Counter, conf: dict, e: float, modelo: str) -> float:
    n = sum(cont.values())
    arriscadas = {s for s in cont if s.startswith("inventada")}
    n_arr = sum(cont[s] for s in arriscadas)
    total = 0.0
    for s, k in cont.items():
        if modelo == "uniforme":
            es = e
        else:
            es = min(1.0, e * n / n_arr) if s in arriscadas else 0.0
        c = conf[s]
        total += k * ((1 - es) * (1 - c) ** 2 + es * c ** 2)
    return total / n


def main():
    ix = Indice(str(RAIZ / "data" / "desafio1_bracis.db"))
    niveis = situacoes_por_nivel(ix)
    print("situações nas 26 peças (aproximação da composição do conjunto oculto):")
    for s in sorted(set(niveis[1]) | set(niveis[2])):
        print(f"   {s:<56} N1 {niveis[1][s]:>3}   N2 {niveis[2][s]:>3}")
    for modelo in ("uniforme", "concentrado"):
        print(f"\nmodelo {modelo}: bônus final esperado (média 1·N1 + 2·N2) e diferença para o perfil 'um'")
        print(f"   {'taxa de erro':<14}" + "".join(f"{p:>22}" for p in PERFIS))
        for e in TAXAS:
            linha = []
            ref = None
            for p, conf in PERFIS.items():
                b = sum(peso * 0.10 * (1 - brier_esperado(niveis[nv], conf, e, modelo))
                        for nv, peso in ((1, 1), (2, 2))) / 3
                if p == "um":
                    ref = b
                linha.append((p, b))
            print(f"   {e:<14.1%}" + "".join(f"{b:>12.5f} ({b - ref:+.5f})" for _, b in linha))
    print("\n(diferenças em pontos da nota final; um erro de classe no nível 2 custa ~0,0066)")


if __name__ == "__main__":
    main()
