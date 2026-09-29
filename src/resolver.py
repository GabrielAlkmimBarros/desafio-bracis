"""Resolvedor: recebe uma citação detectada, consulta o índice e decide a classe.

Regra de contagem (do enunciado):
    exatamente 1 registro  -> real        (com o id desse registro)
    0 registros            -> inventada
    2+ sem desempate       -> incompleta
    sem número             -> incompleta  (tribunal + ano + relator batem com vários acórdãos)

A confiança é um palpite de quão certa está a decisão. Por enquanto são valores fixos
por situação; depois calibramos com o gabarito (o bônus mede isso).
"""
from .indice import Ficha, Indice
from .normalizar import dv_cnj_valido, eh_cnj, justica_cnj
from .vocabulario import TRIBUNAIS_DA_CLASSE, classe_base

_TRIBUNAL_DA_JUSTICA = {"5": "TST", "6": "TSE", "7": "STM"}


def candidatos_acordao(ix: Indice, numero: str, classe: tuple, tribunal: str | None = None,
                       uf: str | None = None) -> tuple[list[Ficha], list[str]]:
    """Filtra os registros com esse número até sobrar o mínimo possível.
    Devolve (candidatos, passos aplicados) — os passos ajudam a entender cada decisão."""
    cands = ix.buscar_numero(numero)
    passos = [f"número:{len(cands)}"]

    # 1) tribunal: dito na citação, implícito no CNJ (dígito J) ou implícito na classe
    tribs = None
    if tribunal:
        tribs = {tribunal}
    elif eh_cnj(numero):
        tribs = {_TRIBUNAL_DA_JUSTICA.get(justica_cnj(numero))}
    elif classe_base(classe) in TRIBUNAIS_DA_CLASSE:
        tribs = TRIBUNAIS_DA_CLASSE[classe_base(classe)]
    if tribs:
        cands = [f for f in cands if f.tribunal in tribs]
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


def resolver(c, ix: Indice) -> dict:
    """Devolve {classificacao, id_canonico, confianca, motivo}."""
    if c.especie == "incompleta":
        return dict(classificacao="incompleta", id_canonico=None, confianca=0.85,
                    motivo="sem número: tribunal/ano/relator não identificam um registro único")

    if c.especie == "tema":
        return dict(classificacao="inventada", id_canonico=None, confianca=0.8,
                    motivo="a base não tem registros de Tema")

    if c.especie in ("sumula", "artigo"):
        if c.chave_norma is None:
            return dict(classificacao="incompleta", id_canonico=None, confianca=0.6,
                        motivo="súmula sem tribunal")
        f = ix.buscar_norma(c.chave_norma)
        if f:
            return dict(classificacao="real", id_canonico=f.id, confianca=0.95, motivo=f"norma {c.chave_norma}")
        return dict(classificacao="inventada", id_canonico=None, confianca=0.9,
                    motivo=f"norma {c.chave_norma} não existe na base")

    # acórdão com número
    cands, passos = candidatos_acordao(ix, c.numero, c.classe, c.tribunal, c.uf)
    motivo = " > ".join(passos)
    if len(cands) == 1:
        exato = passos[0] == "número:1"
        return dict(classificacao="real", id_canonico=cands[0].id,
                    confianca=0.95 if exato else 0.8, motivo=motivo)
    if not cands:
        # número CNJ com dígito verificador inválido é um forte sinal de número inventado
        conf = 0.95 if eh_cnj(c.numero) and not dv_cnj_valido(c.numero) else 0.85
        return dict(classificacao="inventada", id_canonico=None, confianca=conf, motivo=motivo)
    return dict(classificacao="incompleta", id_canonico=None, confianca=0.5,
                motivo=motivo + f" (ambíguo: {[f.documento_id for f in cands]})")
