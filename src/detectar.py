"""Detector: encontra no texto os trechos que são citação e diz de que espécie são.

Quatro espécies, cada uma com a sua estratégia:

  acordao     'AgInt no AREsp nº 1.996.496/RJ'
              ÂNCORA NO NÚMERO: acha todo número do texto e olha para a esquerda.
              Só vira citação se à esquerda houver uma classe processual reconhecida
              ('AgInt no AREsp'). É isso que descarta 'fls. 790/829', 'OAB/BA 349745',
              'Processo nº 6706918-...' do cabeçalho: nenhum tem classe antes do número.
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
    for s in inicios:                                    # do mais distante para o mais próximo
        siglas = canonizar_classe(texto[s:ini_num])
        conhecidas = [x for x in siglas if not x.startswith("?")]
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


def detectar_acordaos(texto: str) -> list[Candidata]:
    achados = []
    for ini, fim in numeros_no_texto(texto):
        esq = _classe_a_esquerda(texto, ini)
        if not esq:
            continue
        s, classe, trib = esq
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

_SUMULA = re.compile(
    r"(?:[S5$][úu](?:m|rn)(?:ula)?\.?|Verbete)\s+(?:(?P<vinc>Vinculante)\s+)?(?:n\s*[º°o.]*\s*)?"
    r"(?P<num>[\dOlI]*\d[\dOlI]*)"
    r"(?:\s*,?\s*d[oa]\s+(?P<trib>STF|STJ|TST|TSE|STM|Supremo Tribunal Federal|Superior Tribunal de Justi[çc]a|"
    r"Tribunal Superior (?:do Trabalho|Eleitoral)|Superior Tribunal Militar|S[TＴ][FJ]))?",
    re.I)


def detectar_sumulas(texto: str) -> list[Candidata]:
    achados = []
    for m in _SUMULA.finditer(texto):
        trib = m["trib"]
        if trib:
            trib = _NOMES_TRIBUNAIS.get(sem_acento(trib).upper(), trib.upper())
        vinc = bool(m["vinc"])
        if vinc and not trib:
            trib = "STF"
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
_ANO_RELATOR = re.compile(
    rf"(?P<ano>(?:19|20)\d\d)\s*,?\s*"
    rf"(?:(?:pela|sob|da|sob\s+a)\s+r\w?lat\w{{2,5}}\s+d\w\s+|"            # 'pela relatoria de' (tolera 'dc')
    rf"Rel(?:ator|atora)?\.?\s*(?:Min(?:istr[oa])?\.?\s*)?)"                # 'Rel. Min.'
    rf"(?P<nome>{_NOME})")
_CABECAS = re.compile(r"(?:julgad[oa]|precedente|ac[óo]rd[ãa]o|aresto|decis[ãa]o|voto)$", re.I)
_TRIB_TXT = (r"(?:STF|STJ|TSE|TST|STM|Supremo Tribunal Federal|Superior Tribunal de Justi[çc]a|"
             r"Tribunal Superior (?:do Trabalho|Eleitoral)|Superior Tribunal Militar)")
# o que pode haver entre a cabeça e o ano: 'do STF proferido em', 'do STM de', ', de', 'julgado em'
_MEIO = re.compile(rf"^(?P<cabeca>.+?)\s*,?\s*(?:d[oa]\s+(?P<trib>{_TRIB_TXT})\s*,?\s*)?"
                   rf"(?:\w{{3,12}}[aie]d[oa]\s+)?(?:de|em)\s*$", re.S)


def _cabeca_valida(cabeca: str) -> bool:
    if _CABECAS.fullmatch(cabeca.strip()):
        return True
    siglas = canonizar_classe(cabeca)
    return bool(siglas) and all(not x.startswith("?") for x in siglas) and classe_base(siglas) is not None


def detectar_incompletas(texto: str) -> list[Candidata]:
    achados = []
    for m in _ANO_RELATOR.finditer(texto):
        ini_ano = m.start("ano")
        ini_jan = max(0, ini_ano - 110)
        inicios = [ini_jan + x.start() for x in re.finditer(r"(?<!\S)\S", texto[ini_jan:ini_ano])]
        for s in inicios:                                   # do mais distante para o mais próximo
            meio = _MEIO.match(texto[s:ini_ano])
            if not meio:
                continue
            cabeca = meio["cabeca"]
            # descarta conectivos no começo ('no julgado' -> 'julgado')
            c = re.match(rf"(?:{_CONECTORES}\s+)+", cabeca, re.I)
            deslocamento = c.end() if c else 0
            if _cabeca_valida(cabeca[deslocamento:]):
                trib = meio["trib"]
                if trib:
                    trib = _NOMES_TRIBUNAIS.get(sem_acento(trib).upper(), trib.upper())
                achados.append(Candidata(s + deslocamento, m.end("nome"), "incompleta", "jurisprudencia",
                                         tribunal=trib, extra={"ano": int(m["ano"]), "relator": m["nome"]}))
                break
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
