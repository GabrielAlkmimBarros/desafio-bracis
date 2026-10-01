"""Resolvedor: consulta o índice e classifica a citação detectada.

    exatamente 1 registro  -> real (com o id do registro)
    0 registros            -> inventada
    2+ sem desempate       -> incompleta
    sem número             -> incompleta

Cada decisão cai numa situação, e a confiança vem do perfil ativo (situação -> valor). O Brier é mínimo quando
a confiança é a taxa de acerto da situação em textos novos.
"""
import os

from .indice import Ficha, Indice
from .normalizar import dv_cnj_valido, eh_cnj, justica_cnj
from .vocabulario import TRIBUNAIS_DA_CLASSE, classe_base

# dígito J do número CNJ (Resolução CNJ 65/2008) -> tribunal superior
_TRIBUNAL_DA_JUSTICA = {"1": "STF", "3": "STJ", "5": "TST", "6": "TSE", "7": "STM"}

# Perfil "padrao" (o usado): 1,0 nas situações observadas nos testes, cuja taxa de acerto é de 99,99% ou mais (o
# Brier é mínimo com confiança igual ao acerto, e 1,0 está mais perto de 0,9999 que 0,99); valor menor só nas
# situações nunca observadas com rótulo conhecido. Testes (citações / erros):
#   E = estresse (41 sorteios na base original + 7 bases modificadas, inclui redação real do STJ)
#   B = 26 peças x 7 bases    M = metamórfico, 26 peças x 8 transformações    D = 26 peças
#   situação                                          E           B        M       D
#   real: número único                          297074/0      436/0    608/0    76/0
#   real: número único, classe ou UF divergem     9916/0        -        -        -
#   real: desempate por classe ou UF              2961/0        6/0      8/0     1/0
#   real: lei ou súmula da base                  32170/0      109/0    152/0    19/0
#   incompleta: sem número                       92682/0      224/0    256/0    32/0
#   inventada: número ausente, texto limpo       91890/0      264/0    240/0    32/0
#   inventada: número ausente, com ruído         40767/4       87/0     96/0    10/0
#   inventada: lei/súmula fora, texto limpo      30369/0      124/0    116/0    15/0
#   inventada: lei/súmula fora, com ruído         4298/0       47/0     52/0     6/0
#   inventada: CNJ com DV válido, ausente          675/0       40/0       -        -
# Os 4 erros são ambiguidades genuínas entre sigla e número com OCR ('RHC SS. 413': 55.413 ou classe SS?;
# '68. 2GO': 68.260 ou UF GO?).
_PADRAO = {
    "real: número único": 1.0,
    "real: número único, classe ou UF divergem": 1.0,
    "real: desempate por classe ou UF": 1.0,
    "real: lei ou súmula da base": 1.0,
    "incompleta: sem número": 1.0,
    "inventada: número ausente, texto limpo": 1.0,
    "inventada: número ausente, texto com ruído": 1.0,
    "inventada: lei ou súmula fora da base, texto limpo": 1.0,
    "inventada: lei ou súmula fora da base, texto com ruído": 1.0,
    "inventada: CNJ com DV válido, ausente": 1.0,
    "inventada: tema": 1.0,                         # a base não tem registros de Tema
    # nunca observadas com rótulo conhecido
    "inventada: número só existe em outro tribunal": 0.85,
    "incompleta: número ambíguo": 0.60,
    "incompleta: súmula sem tribunal": 0.50,
    "real: duplicata de texto idêntico": 0.50,      # escolha entre textos idênticos é arbitrária
}

# Perfil "conservador": taxa de acerto estimada com margem para variações que nenhum teste cobre.
_CONSERVADOR = {
    # número único, classe e UF conferem: leitura errada quase nunca cai em outro registro existente
    "real: número único": 0.98,
    # classe ou UF divergem: em geral variação de redação, mas é onde cairia um número inventado que coincide
    # com processo de outra classe
    "real: número único, classe ou UF divergem": 0.90,
    "real: desempate por classe ou UF": 0.93,       # depende de ter lido a classe inteira
    "real: duplicata de texto idêntico": 0.50,
    "real: lei ou súmula da base": 0.97,
    # o modo de falha é uma citação real lida pela metade (OCR, quebra de linha), mais provável com ruído
    "inventada: número ausente, texto limpo": 0.95,
    "inventada: número ausente, texto com ruído": 0.90,
    # leitura errada quase sempre quebra o DV, e um número inventado ao acaso tem DV válido 1 vez em 97
    "inventada: CNJ com DV válido, ausente": 0.60,
    "inventada: número só existe em outro tribunal": 0.85,
    "inventada: lei ou súmula fora da base, texto limpo": 0.93,
    "inventada: lei ou súmula fora da base, texto com ruído": 0.88,
    "inventada: tema": 0.80,
    "incompleta: sem número": 0.95,
    # pode ser uma real cuja classe ou UF não foi lida
    "incompleta: número ambíguo": 0.60,
    "incompleta: súmula sem tribunal": 0.50,
}

PERFIS = {
    "padrao": _PADRAO,
    "conservador": _CONSERVADOR,
    "um": {k: 1.0 for k in _PADRAO},
}
# outro perfil: CONFIANCA_PERFIL=conservador|um
PERFIL = os.environ.get("CONFIANCA_PERFIL", "padrao")
CONFIANCA = PERFIS[PERFIL]


def candidatos_acordao(ix: Indice, numero: str, classe: tuple, tribunal: str | None = None,
                       uf: str | None = None) -> tuple[list[Ficha], list[str]]:
    """Filtra os registros com esse número até sobrar o mínimo possível. Devolve (candidatos, passos)."""
    cands = ix.buscar_numero(numero)
    passos = [f"número:{len(cands)}"]

    # 1) tribunal: dito na citação, implícito no CNJ (dígito J) ou na classe (mapa fixo somado ao que a base
    #    mostra); se nada se sabe, não filtra
    tribs = None
    if tribunal:
        tribs = {tribunal}
    elif eh_cnj(numero):
        t = _TRIBUNAL_DA_JUSTICA.get(justica_cnj(numero))
        tribs = {t} if t else None
    else:
        base = classe_base(classe)
        tribs = (TRIBUNAIS_DA_CLASSE.get(base, set()) | ix.tribunais_da_classe.get(base, set())) or None
    if tribs:                                   # registro sem tribunal na base não é descartado
        filtrados = [f for f in cands if f.tribunal in tribs or not f.tribunal]
        # número CNJ é único por construção: se o tribunal gravado na base diverge, vale o número. Número simples
        # coincide entre tribunais, e ali o filtro pode zerar os candidatos
        cands = filtrados if (filtrados or not eh_cnj(numero)) else cands
        passos.append(f"tribunal:{len(cands)}")

    # 2) desempates, só se ainda houver mais de um
    for nome, criterio in (
        ("classe exata", lambda f: f.classe == classe),
        ("classe-base", lambda f: classe_base(f.classe) == classe_base(classe)),
        ("UF", lambda f: uf is not None and f.uf == uf),
    ):
        if len(cands) > 1:
            filtrados = [f for f in cands if criterio(f)]
            if filtrados:
                cands = filtrados
                passos.append(f"{nome}:{len(cands)}")

    # 3) duplicatas exatas (mesmo texto com ids diferentes) contam como um só registro
    if len(cands) > 1 and len({f.assinatura for f in cands}) == 1:
        cands = cands[:1]
        passos.append("duplicata:1")
    return cands, passos


def _decisao(classificacao: str, situacao: str, motivo: str, id_canonico=None) -> dict:
    return dict(classificacao=classificacao, id_canonico=id_canonico, confianca=CONFIANCA[situacao],
                motivo=f"[{situacao}] {motivo}")


def _situacao_real(c, f: Ficha, passos: list[str]) -> str:
    nomes = [p.split(":")[0] for p in passos]
    if "duplicata" in nomes:
        return "real: duplicata de texto idêntico"
    if any(n in nomes for n in ("classe exata", "classe-base", "UF")):
        return "real: desempate por classe ou UF"
    classe_diverge = bool(f.classe) and bool(c.classe) and classe_base(f.classe) != classe_base(c.classe)
    uf_diverge = bool(c.uf) and bool(f.uf) and c.uf != f.uf
    if not eh_cnj(c.numero) and (classe_diverge or uf_diverge):
        return "real: número único, classe ou UF divergem"
    return "real: número único"


def resolver(c, ix: Indice) -> dict:
    """Devolve {classificacao, id_canonico, confianca, motivo}; o motivo começa pela situação."""
    ruido = c.extra.get("ruido", False)
    if c.especie == "incompleta":
        return _decisao("incompleta", "incompleta: sem número",
                        "sem número: tribunal/ano/relator não identificam um registro único")

    if c.especie == "tema":
        return _decisao("inventada", "inventada: tema", "a base não tem registros de Tema")

    if c.especie in ("sumula", "artigo"):
        if c.chave_norma is None:
            return _decisao("incompleta", "incompleta: súmula sem tribunal", "súmula sem tribunal")
        f = ix.buscar_norma(c.chave_norma)
        if f:
            return _decisao("real", "real: lei ou súmula da base", f"norma {c.chave_norma}", f.id)
        situacao = ("inventada: lei ou súmula fora da base, texto com ruído" if ruido else
                    "inventada: lei ou súmula fora da base, texto limpo")
        return _decisao("inventada", situacao, f"norma {c.chave_norma} não existe na base")

    # acórdão com número
    cands, passos = candidatos_acordao(ix, c.numero, c.classe, c.tribunal, c.uf)
    motivo = " > ".join(passos)
    if len(cands) == 1:
        return _decisao("real", _situacao_real(c, cands[0], passos), motivo, cands[0].id)
    if not cands:
        if passos[0] != "número:0":
            situacao = "inventada: número só existe em outro tribunal"
        elif eh_cnj(c.numero) and dv_cnj_valido(c.numero):
            situacao = "inventada: CNJ com DV válido, ausente"
        else:
            situacao = ("inventada: número ausente, texto com ruído" if ruido else
                        "inventada: número ausente, texto limpo")
        return _decisao("inventada", situacao, motivo)
    return _decisao("incompleta", "incompleta: número ambíguo",
                    motivo + f" (ambíguo: {[f.documento_id for f in cands]})")
