"""Resolvedor: recebe uma citação detectada, consulta o índice e decide a classe.

Regra de contagem (do enunciado):
    exatamente 1 registro  -> real        (com o id desse registro)
    0 registros            -> inventada
    2+ sem desempate       -> incompleta
    sem número             -> incompleta  (tribunal + ano + relator batem com vários acórdãos)

Confiança: cada decisão cai numa SITUAÇÃO (tabela CONFIANCA abaixo), e a confiança é a taxa
de acerto esperada dessa situação em textos nunca vistos — não um ajuste às 26 peças. O Brier
é mínimo quando a confiança é igual à taxa de acerto; por isso nenhuma situação leva 1,0.
Cada valor combina três evidências (ver `python scripts/calibracao.py`):
  - as 26 peças de desenvolvimento: acerto de 100% em todas as situações, mas otimista
    (o sistema foi depurado nelas);
  - o estresse sintético rodado na versão ANTERIOR às correções dele: variações que o sistema
    nunca tinha visto — pessimista, porque o ruído gerado é mais agressivo que o do desafio;
  - o modo de falha de cada situação (o que precisaria dar errado para a decisão errar).
"""
from .indice import Ficha, Indice
from .normalizar import dv_cnj_valido, eh_cnj, justica_cnj
from .vocabulario import TRIBUNAIS_DA_CLASSE, classe_base

_TRIBUNAL_DA_JUSTICA = {"5": "TST", "6": "TSE", "7": "STM"}

CONFIANCA = {
    # ---- real
    # Número único no tribunal, classe e UF conferem. Uma leitura errada quase nunca cai por acaso
    # em outro registro existente (estresse: 0 erros em ~2.000 citações ruidosas detectadas), e um
    # número inventado coincide com um registro da mesma classe em ~0,04% dos sorteios.
    "real: número único": 0.98,
    # Número único, mas a classe-base ou a UF da citação diverge do registro (número simples de
    # STF/STJ, que é numerado por classe). Em geral é variação de redação ('Rcl' para 'AgRg na Rcl'),
    # mas também é onde cairia um número inventado que coincide com processo de outra classe.
    "real: número único, classe ou UF divergem": 0.90,
    # 2+ registros com o número; classe ou UF escolheram um. Depende de ter lido a classe inteira.
    "real: desempate por classe ou UF": 0.93,
    # Registros de texto idêntico com ids diferentes: a escolha entre eles é arbitrária (a organização
    # diz que nenhuma citação aponta para eles; se apontar, é cara ou coroa).
    "real: duplicata de texto idêntico": 0.50,
    # Lei ou súmula da base. Erra só se a lei/tribunal for mal identificado.
    "real: lei ou súmula da base": 0.97,
    # ---- inventada
    # Número ausente da base. O modo de falha é uma citação real lida pela metade (OCR, quebra de
    # linha) — raro em texto limpo, mais provável em texto ruidoso (estresse pré-correção: 99,4%).
    "inventada: número ausente, texto limpo": 0.95,
    "inventada: número ausente, texto com ruído": 0.90,
    # CNJ com dígito verificador VÁLIDO e ausente da base: leitura errada quase sempre quebra o DV,
    # e número inventado ao acaso tem DV válido só 1 vez em 97 (as 3 inventadas CNJ do
    # desenvolvimento têm DV inválido). Sobra a hipótese de falha do índice: incerto.
    "inventada: CNJ com DV válido, ausente": 0.60,
    # O número existe, mas só em tribunal incompatível com a classe citada: quase sempre coincidência,
    # mas pode ser lacuna no mapa classe -> tribunal. Não ocorre no desenvolvimento.
    "inventada: número só existe em outro tribunal": 0.85,
    # Artigo ou súmula fora da base (estresse pré-correção: 100% limpo, 93% ruidoso).
    "inventada: lei ou súmula fora da base, texto limpo": 0.93,
    "inventada: lei ou súmula fora da base, texto com ruído": 0.88,
    # A base não tem registros de Tema; um único exemplo no desenvolvimento, convenção incerta.
    "inventada: tema": 0.80,
    # ---- incompleta
    # Sem número: é incompleta pela forma, não pela contagem (há incompletas do gabarito com 0
    # candidatos no banco). Estresse: 100% das detectadas, limpas ou ruidosas.
    "incompleta: sem número": 0.95,
    # Número que casa com 2+ registros sem desempate. Pela definição é incompleta, mas citações reais
    # nunca apontam para esses registros — então também pode ser uma real cuja classe/UF não foi lida.
    "incompleta: número ambíguo": 0.60,
    # Súmula sem tribunal: no estresse, sempre foi um tribunal que não foi lido ('/STJ', '5TJ').
    # O que sobra depois das correções não tem rótulo conhecido: máxima incerteza.
    "incompleta: súmula sem tribunal": 0.50,
}


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
