"""Detector: encontra no texto os trechos que são citação e diz de que espécie são.

Quatro espécies, cada uma com a sua estratégia:

  acordao     'AgInt no AREsp nº 1.996.496/RJ'
              ÂNCORA NO NÚMERO: acha todo número do texto e olha para a esquerda.
              Só vira citação se à esquerda houver uma classe processual reconhecida
              ('AgInt no AREsp'). É isso que descarta 'fls. 790/829', 'OAB/BA 349745',
              'Processo nº 6706918-...' do cabeçalho: nenhum tem classe antes do número.
              O 'e' solto é a conjunção, nunca a classe 'Embargos' ('fls. 10 e 11'); a única
              exceção é 'REsp 1/SP e 2/RJ', em que o 2º número herda a classe do 1º.
  sumula      'Súmula 331 do TST' | 'Súmula Vinculante 10' | '5úmula 211 do STJ'
  artigo      'art. 373, I, do CPC' | 'artigo 7º, XXIX, da Constituição Fedcral'
  incompleta  'julgado do STF proferido em 2024 pela relatoria de Dias Toffoli'
              ÂNCORA NO ANO + RELATOR, e olha para a esquerda procurando a cabeça
              ('julgado', 'precedente', 'acórdão' ou uma classe processual).

O detector não consulta a base: só acha e descreve. Quem decide real/inventada é o resolvedor.
"""
import re
from dataclasses import dataclass, field

from .leis import ler_lei
from .normalizar import digitos, numero_canonico, sem_acento
from .vocabulario import canonizar_classe, classe_base


@dataclass
class Candidata:
    inicio: int
    fim: int
    especie: str                     # acordao | sumula | artigo | tema | incompleta
    tipo: str                        # jurisprudencia | lei
    numero: str | None = None        # acordao: número canônico
    classe: tuple = ()               # acordao: siglas da classe
    uf: str | None = None
    tribunal: str | None = None
    chave_norma: tuple | None = None  # sumula/artigo: chave para o índice
    extra: dict = field(default_factory=dict)


UFS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB",
       "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}
TRIBUNAIS = {"STF", "STJ", "TSE", "TST", "STM"}
_NOMES_TRIBUNAIS = {
    "SUPREMO TRIBUNAL FEDERAL": "STF", "SUPERIOR TRIBUNAL DE JUSTICA": "STJ",
    "TRIBUNAL SUPERIOR ELEITORAL": "TSE", "TRIBUNAL SUPERIOR DO TRABALHO": "TST",
    "SUPERIOR TRIBUNAL MILITAR": "STM",
}
_CONECTORES = r"(?:no|na|nos|nas|em|de|do|da|a|ao|o)"


# ============================================================================ números com posição

# separa pedaços: espaços e / ( ) , ; :  — e também 'ARR-1099' (letra-hífen-dígito),
# '1.632.479-RJ' (hífen antes de UF) e 'nº1.234' (º colado no número)
_SEPARADOR = re.compile(r"(?<=[A-Za-z]{2})-(?=[OolISsgGbBZz]?\d)|-(?=[A-Za-z]{2}\b)|(?<=[º°])(?=[\dOl])|[\s/(),;:]+")
_QUEBRA_CORRIDA = re.compile(r"[,;:()]")        # estes separadores encerram um número
_LETRAS_OCR = set("OoQDlIi|!SsgqGbBZz")


def _eh_pedaco_numerico(p: str) -> bool:
    miolo = re.sub(r"[.\-–—]", "", p)
    return bool(miolo) and any(c.isdigit() for c in miolo) and \
        all(c.isdigit() or c in _LETRAS_OCR for c in miolo)


def _eh_ligacao(p: str) -> bool:
    return bool(p) and not re.sub(r"[.\-–—]", "", p)


def _eh_talvez_numero(p: str) -> bool:
    """'l.' ou 'O' sozinhos: podem ser o começo de um número com OCR ('l.\n718.894').
    Só entram na corrida se um pedaço numérico vier logo depois."""
    miolo = re.sub(r"[.\-–—]", "", p)
    return 0 < len(miolo) <= 2 and miolo not in {"o", "a"} and all(c in _LETRAS_OCR for c in miolo)


def numeros_no_texto(texto: str):
    """Gera (inicio, fim) de cada número do texto, tolerando ruído de OCR e quebras."""
    pedacos, pos = [], 0
    for m in _SEPARADOR.finditer(texto):
        if m.start() > pos:
            pedacos.append((pos, m.start()))
        pos = m.end()
    if pos < len(texto):
        pedacos.append((pos, len(texto)))

    corrida = []                                 # pedaços da corrida atual
    for k, (ini, fim) in enumerate(pedacos):
        p = texto[ini:fim]
        separador = texto[corrida[-1][1]:ini] if corrida else ""
        if corrida and _QUEBRA_CORRIDA.search(separador):
            yield from _fechar(corrida, texto)
            corrida = []
        proximo = texto[pedacos[k + 1][0]:pedacos[k + 1][1]] if k + 1 < len(pedacos) else ""
        if (_eh_pedaco_numerico(p) or (corrida and _eh_ligacao(p)) or
                (not corrida and _eh_talvez_numero(p) and _eh_pedaco_numerico(proximo))):
            corrida.append((ini, fim))
        else:
            yield from _fechar(corrida, texto)
            corrida = []
    yield from _fechar(corrida, texto)


def _fechar(corrida, texto):
    while corrida and _eh_ligacao(texto[corrida[-1][0]:corrida[-1][1]]):
        corrida = corrida[:-1]                   # não termina em '-' ou '.' solto
    if corrida:
        ini, fim = corrida[0][0], corrida[-1][1]
        fim -= len(texto[ini:fim]) - len(texto[ini:fim].rstrip(".-–"))
        yield ini, fim


# ============================================================================ acórdãos

def _neutro(sigla: str) -> bool:
    """Palavra que pode aparecer junto da classe sem ser classe: tribunal, 'processo'."""
    return sigla.startswith("?") and sigla[1:] in TRIBUNAIS | {"PROCESSO", "PROC"}


def _classe_a_esquerda(texto: str, ini_num: int, janela: int = 130):
    """Procura, antes do número, a sequência de palavras mais longa que seja classe processual."""
    ini_jan = max(0, ini_num - janela)
    inicios = [ini_jan + m.start() for m in re.finditer(r"(?<!\S)\S", texto[ini_jan:ini_num])]
    # 'e' colado no número é a conjunção ('fls. 10 e 11', 'REsp e 5 outros'), não a classe 'Embargos'
    if inicios and re.fullmatch(r"[eE]\s*", texto[inicios[-1]:ini_num]):
        return None
    for s in inicios:                                    # do mais distante para o mais próximo
        if re.match(r"e(?!\S)", texto[s:ini_num]):       # 'e AREsp 12': a citação começa em 'AREsp'
            continue
        siglas = canonizar_classe(texto[s:ini_num])
        conhecidas = [x for x in siglas if not x.startswith("?")]
        if conhecidas == ["E"]:                          # 'E' sozinho não basta para ser classe
            continue
        if conhecidas and classe_base(tuple(conhecidas)) and all(
                not x.startswith("?") or _neutro(x) for x in siglas):
            # não começar a citação num conectivo solto ('no AgInt...' -> 'AgInt...')
            while True:
                m = re.match(rf"{_CONECTORES}\s+", texto[s:ini_num], re.I)
                if not m:
                    break
                s += m.end()
            trib = next((x[1:] for x in siglas if x.startswith("?") and x[1:] in TRIBUNAIS), None)
            return s, tuple(conhecidas), trib
    return None


def _uf_a_direita(texto: str, fim: int):
    m = re.match(r"\s*(?:/|[-–]|\()\s*([A-Z]{2})\)?(?![A-Za-z])", texto[fim:fim + 10])
    if m and m[1] in UFS:
        return fim + m.end(), m[1]
    return fim, None


_ENUMERACAO = re.compile(r"\s*,?\s+e\s+(?:n[º°o.]+s?\s*)?", re.I)


def _herda_classe(texto: str, anterior: Candidata, ini_num: int, num_bruto: str) -> bool:
    """'REsp 1.234.567/SP e 1.234.568/RJ': o 2º número não repete a classe, herda a do 1º.
    Só se um 'e' o cola ao acórdão anterior e ele tem cara de processo (4+ dígitos, não um ano):
    é o que separa 'REsp 1.234.567 e 1.234.568' de 'REsp 1.234.567 e 10 dias'."""
    return bool(_ENUMERACAO.fullmatch(texto[anterior.fim:ini_num])
                and len(digitos(num_bruto)) >= 4
                and not re.fullmatch(r"(?:19|20)\d\d", num_bruto.strip()))


def detectar_acordaos(texto: str) -> list[Candidata]:
    achados = []
    for ini, fim in numeros_no_texto(texto):
        esq = _classe_a_esquerda(texto, ini)
        if esq:
            s, classe, trib = esq
        elif achados and _herda_classe(texto, achados[-1], ini, texto[ini:fim]):
            s, classe, trib = ini, achados[-1].classe, achados[-1].tribunal
        else:
            continue
        num_bruto = texto[ini:fim]
        # 'Rcl de 2021' é um ANO, não um número de processo -> quem cuida é o detector de incompletas
        if re.search(r"\b(?:de|em)\s*$", texto[s:ini], re.I) and re.fullmatch(r"(19|20)\d\d", num_bruto.strip()):
            continue
        numero = numero_canonico(num_bruto)
        if not numero:
            continue
        fim_uf, uf = _uf_a_direita(texto, fim)
        achados.append(Candidata(s, fim_uf, "acordao", "jurisprudencia", numero=numero,
                                 classe=classe, uf=uf, tribunal=trib))
    return achados


# ============================================================================ súmulas

_TRIB_SUMULA = (r"STF|STJ|TST|TSE|STM|Supremo Tribunal Federal|Superior Tribunal de Justi[çc]a|"
                r"Tribunal Superior (?:do Trabalho|Eleitoral)|Superior Tribunal Militar")
_SUMULA = re.compile(
    # cabeça: 'Súmula' (com OCR: '5úmula', 'Súrnula'), 'Súm.', 'Verbete', 'Enunciado' ou 'SV'
    rf"(?:(?P<cab>[S5$][úu](?:m|rn)(?:ula)?\.?|Verbete|Enunciado)\s+(?:(?P<vinc>Vinculante)\s+)?|\b(?P<sv>SV)\s+)"
    rf"(?:n\s*[º°o.]*\s*)?(?P<num>[\dOlI]*\d[\dOlI]*)"
    rf"(?:\s*,\s*(?:item\s+|inciso\s+)?[IVX]+\b\s*,?)?"                           # 'Súmula 331, IV, do TST'
    rf"(?:\s*(?:,?\s*d[oa]\s+|/\s*)(?P<trib>{_TRIB_SUMULA})"                     # 'do STJ' | '/STJ'
    rf"|\s+da\s+S[úu]mula\s+(?:de\s+Jurisprud[êe]ncia\s+)?(?:(?:Vinculante|Dominante)\s+)?"
    rf"d[oa]\s+(?P<trib2>{_TRIB_SUMULA}))?",                                      # 'Enunciado 83 da Súmula do STJ'
    re.I)


def detectar_sumulas(texto: str) -> list[Candidata]:
    achados = []
    for m in _SUMULA.finditer(texto):
        trib = m["trib"] or m["trib2"]
        if trib:
            trib = _NOMES_TRIBUNAIS.get(sem_acento(trib).upper(), trib.upper())
        vinc = bool(m["vinc"] or m["sv"])
        if vinc and not trib:
            trib = "STF"
        # 'Enunciado 5' / 'verbete 12' sem tribunal pode ser qualquer coisa (Jornada, FONAJE...): ignora
        if not trib and m["cab"] and m["cab"].lower().startswith(("enunciado", "verbete")):
            continue
        n = int(digitos(m["num"]))
        chave = ("sumula", trib, vinc, n) if trib else None
        achados.append(Candidata(m.start(), m.end(), "sumula", "jurisprudencia",
                                 tribunal=trib, chave_norma=chave))
    return achados


# ============================================================================ temas

# 'Tema 2.680 da repercussão geral' | 'Tema Repetitivo 1.046 do STJ'. A base não tem registros
# de Tema — só acórdãos, súmulas e artigos —, então um Tema nunca resolve a um registro.
_TEMA = re.compile(
    r"\bT[eê]m[aã](?:\s+[Rr]epetitivo)?\s+(?:n\s*[º°o.]*\s*)?(?P<num>\d[\d.]*)"
    r"(?:\s*,?\s*d[ao]s?\s+(?:repercuss[ãa]o\s+geral|recursos?\s+repetitivos?|STF|STJ|TST|TSE))?")


def detectar_temas(texto: str) -> list[Candidata]:
    return [Candidata(m.start(), m.end(), "tema", "jurisprudencia", numero=digitos(m["num"]))
            for m in _TEMA.finditer(texto)]


# ============================================================================ artigos de lei

_ARTIGO = re.compile(
    r"\b(?:art(?:igo)?s?\.?)\s*(?P<num>\d{1,3}(?:\.\d{3})?)\s*[º°o]?(?:-[A-Z])?"
    r"(?P<compl>(?:\s*,\s*(?:(?:§|par[áa]grafo)\s*\d+\s*[º°o]?(?:-[A-Z])?|par[áa]grafo [úu]nico|"
    r"inciso\s+[IVXLC]+|[IVXLC]+(?:-[A-Z])?|al[íi]nea\s+['‘’\"]?[a-z]['‘’\"]?|['‘’\"][a-z]['‘’\"]|caput))*)"
    r"\s*,?\s*d[oa]s?\s+", re.I)


def detectar_artigos(texto: str) -> list[Candidata]:
    achados = []
    for m in _ARTIGO.finditer(texto):
        lei = ler_lei(texto[m.end():])
        if not lei:
            continue
        codigo, tamanho = lei
        n = int(m["num"].replace(".", ""))
        achados.append(Candidata(m.start(), m.end() + tamanho, "artigo", "lei",
                                 chave_norma=("artigo", codigo, n), extra={"lei": codigo}))
    return achados


# ============================================================================ incompletas

_NOME = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ'’]+(?:\s+(?:(?:d[aeo]s?|e)\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ'’]+)*"
# o relator: 'pela relatoria de X' | 'sob a relatoria da Ministra X' | 'de relatoria do Ministro X' |
#            'relatado pelo Ministro X' | 'Rel. Min. X' | '(Rel. Min. X)'   ('dc' = OCR de 'de')
_RELATOR = re.compile(
    rf"(?P<marca>(?:(?:pela|sob\s+a|sob|da|de)\s+r\w?lat\w{{2,5}}\s+d\w{{1,2}}\s+|"
    rf"relatad[oa]\s+pel[oa]\s+|"
    rf"\(?\s*Rel(?:ator|atora)?\.?\s*(?:Min(?:istr[oa])?\.?\s*)?))"
    rf"(?:(?:Min\.|Ministr[oa])\s+)?(?P<nome>{_NOME})\)?")
_ANO_ANTES = re.compile(r"(?P<ano>(?:19|20)\d\d)[\s,;(]*$")                          # '... de 2024, Rel.'
_ANO_DEPOIS = re.compile(r"^\s*,?\s*(?:\w{3,12}[aie]d[oa]\s+)?(?:em|de)\s+(?P<ano>(?:19|20)\d\d)")  # ', julgado em 2021'
_CABECAS = re.compile(r"(?:julgad[oa]|precedente|ac[óo]rd[ãa]o|aresto|decis[ãa]o|voto)$", re.I)
_TRIB_TXT = (r"(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justi[çc]a|"
             r"Tribunal Superior (?:do Trabalho|Eleitoral)|Superior Tribunal Militar)")
# entre a cabeça e o ANO: 'do STF proferido em', 'do STM de', ', de', 'julgado em'
_MEIO_ANO = re.compile(rf"^(?P<cabeca>.+?)\s*,?\s*(?:d[oa]\s+(?P<trib>{_TRIB_TXT})\s*,?\s*)?"
                       rf"(?:\w{{3,12}}[aie]d[oa]\s+)?(?:de|em)\s*$", re.S)
# entre a cabeça e o RELATOR (quando o relator vem antes do ano): 'acórdão do STM, '
_MEIO_REL = re.compile(rf"^(?P<cabeca>.+?)\s*,?\s*(?:d[oa]\s+(?P<trib>{_TRIB_TXT})\s*,?\s*)?$", re.S)


def _cabeca_valida(cabeca: str) -> bool:
    if _CABECAS.fullmatch(cabeca.strip()):
        return True
    siglas = canonizar_classe(cabeca)
    return bool(siglas) and all(not x.startswith("?") for x in siglas) and classe_base(siglas) is not None


def _achar_cabeca(texto: str, ancora: int, meio_rx):
    """Procura, à esquerda da âncora, a cabeça da referência ('julgado', 'precedente', classe...)."""
    ini_jan = max(0, ancora - 110)
    for x in re.finditer(r"(?<!\S)\S", texto[ini_jan:ancora]):      # do mais distante para o mais próximo
        s = ini_jan + x.start()
        meio = meio_rx.match(texto[s:ancora])
        if not meio:
            continue
        cabeca = meio["cabeca"]
        c = re.match(rf"(?:{_CONECTORES}\s+)+", cabeca, re.I)       # 'no julgado' -> 'julgado'
        desloc = c.end() if c else 0
        if _cabeca_valida(cabeca[desloc:]):
            trib = meio["trib"]
            if trib:
                trib = _NOMES_TRIBUNAIS.get(sem_acento(trib).upper(), trib.upper())
            return s + desloc, trib
    return None


def detectar_incompletas(texto: str) -> list[Candidata]:
    """Referência a uma decisão concreta, sem número: CABEÇA [do TRIBUNAL] + ANO + RELATOR
    (o ano pode vir antes ou depois do relator)."""
    achados = []
    for m in _RELATOR.finditer(texto):
        ini_rel, fim = m.start(), m.end()
        antes = _ANO_ANTES.search(texto[max(0, ini_rel - 40):ini_rel])
        if antes:                                              # '... de 2024, Rel. Min. X'
            ancora = ini_rel - 40 + antes.start("ano") if ini_rel >= 40 else antes.start("ano")
            ano = int(antes["ano"])
            achou = _achar_cabeca(texto, ancora, _MEIO_ANO)
        else:
            depois = _ANO_DEPOIS.match(texto[fim:fim + 40])
            if not depois:
                continue                                       # sem ano: não é a referência que buscamos
            ano = int(depois["ano"])
            fim = fim + depois.end()
            achou = _achar_cabeca(texto, ini_rel, _MEIO_REL)  # '... do STM, de relatoria do Min. X, julgado em 2025'
        if achou:
            inicio, trib = achou
            achados.append(Candidata(inicio, fim, "incompleta", "jurisprudencia", tribunal=trib,
                                     extra={"ano": ano, "relator": m["nome"]}))
    return achados


# ============================================================================ tudo junto

_PRIORIDADE = {"incompleta": 0, "sumula": 1, "artigo": 1, "tema": 1, "acordao": 2}


def detectar(texto: str) -> list[Candidata]:
    todas = (detectar_incompletas(texto) + detectar_sumulas(texto) + detectar_temas(texto) +
             detectar_artigos(texto) + detectar_acordaos(texto))
    # sobreposição: fica a de maior prioridade (e, empatando, a mais longa)
    todas.sort(key=lambda c: (_PRIORIDADE[c.especie], -(c.fim - c.inicio)))
    escolhidas: list[Candidata] = []
    for c in todas:
        if all(c.fim <= e.inicio or c.inicio >= e.fim for e in escolhidas):
            escolhidas.append(c)
    return sorted(escolhidas, key=lambda c: c.inicio)
