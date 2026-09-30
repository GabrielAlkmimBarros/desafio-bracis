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
from .vocabulario import canonizar_classe, classe_base, palavra_conhecida


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
_CONECTORES = r"(?:no|na|nos|nas|em|de|do|da|a|ao|o)"

# letras que o OCR do nível 2 confunde (e acentos que somem ou aparecem): 'profcrido', 'relãtoria',
# 'Superi0r', '5uperior', 'Minlstro', 'Complernentar'
_OCR_LETRA = {"a": "[aáàâã]", "e": "[eéêc]", "i": "[iíl1]", "o": "[oóôõ0]", "u": "[uú]", "l": "[l1I]",
              "s": "[s5]", "m": "(?:m|rn)", "ç": "[çc]", " ": r"\s+"}


def _ocr(texto: str) -> str:
    """Regex que casa `texto` (sem acento, minúsculo) com as confusões de OCR. Use com re.I."""
    return "".join(_OCR_LETRA.get(c, re.escape(c)) for c in texto)


_TRIB_EXTENSO = "|".join(_ocr(n) for n in (
    "supremo tribunal federal", "superior tribunal de justiça", "tribunal superior do trabalho",
    "tribunal superior eleitoral", "superior tribunal militar"))
_TRIB_SIGLA = r"(?-i:[S5][TＴ][FJM]|T[S5][TE])"        # STF/STJ/STM/TST/TSE, também '5TJ'


def sigla_tribunal(txt: str) -> str:
    """'Superi0r Tribunal de Justiça' | '5TJ' | 'STJ' -> 'STJ'."""
    t = sem_acento(txt).upper().translate(str.maketrans("051Ｔ", "OSLT"))
    for chave, sigla in (("SUPREMO", "STF"), ("JUSTI", "STJ"), ("TRABA", "TST"), ("ELEITORA", "TSE"),
                         ("MILITA", "STM")):
        if chave in t:
            return sigla
    return t


# ============================================================================ números com posição

# separa pedaços: espaços e / ( ) , ; :  — e também 'ARR-1099' (letra-hífen-dígito),
# '1.632.479-RJ' (hífen antes de UF) e 'nº1.234' (º colado no número)
_SEPARADOR = re.compile(
    r"(?<=[A-Za-z]{2})-(?=[OolISsgGbBZz]{0,2}[\-.]?\d)|-(?=[A-Za-z]{2}\b)|(?<=[º°])(?=[\dOl])"
    r"|(?<=\d)-(?=[A-Za-z]{2})"                                 # 'RE 1.492.256-AgR' (sufixo no estilo do STF)
    r"|(?<![A-Za-z])(?<=[nN]\.)(?=[\dOl])"                        # 'HC n.785.562' (número colado no 'n.')
    r"|(?<=\d)(?=(?:AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)\b(?!\s*[/\-–(]\s*[A-Z]{2}\b))"   # '109.956PR' (UF colada, sem outra UF depois)
    r"|[\s/(),;:]+")
_QUEBRA_CORRIDA = re.compile(r"[,;:()/]")       # estes separadores encerram um número ('.../50000' não emenda)
_LETRAS_OCR = set("OoQDlLIi|!SsgqGbBZz")


def _eh_pedaco_numerico(p: str) -> bool:
    miolo = re.sub(r"[.\-–—]", "", p)
    return bool(miolo) and any(c.isdigit() for c in miolo) and \
        all(c.isdigit() or c in _LETRAS_OCR for c in miolo)


def _eh_ligacao(p: str) -> bool:
    return bool(p) and not re.sub(r"[.\-–—]", "", p)


def _eh_talvez_numero(p: str, anterior: str = "") -> bool:
    """'l.' ou 'O' sozinhos: podem ser o começo de um número com OCR ('l.\n718.894').
    Só entram na corrida se um pedaço numérico vier logo depois."""
    miolo = re.sub(r"[.\-–—]", "", p)
    depois_de_ordinal = re.fullmatch(r"[nN][º°.]+[º°]?|N[º°o.]*|No", anterior or "") is not None
    no_meio_do_numero = _eh_pedaco_numerico(anterior or "")          # '1 2 Sl 698': dentro de um número
    if not (depois_de_ordinal or no_meio_do_numero) and any(not s.startswith("?") for s in canonizar_classe(miolo)):
        return False                                     # 'SS 2.888', 'SL 1.234': sigla de classe, não número
                                                         # (mas 'nº SS. 624' é OCR de 55.624)
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

    def proximo_util(k):
        """O próximo pedaço que não seja só '-' ou '.' de ligação nem outra letra solta de OCR ('l S 99 372')."""
        for j in range(k + 1, len(pedacos)):
            p = texto[pedacos[j][0]:pedacos[j][1]]
            if not _eh_ligacao(p) and not _eh_talvez_numero(p, "x"):
                return p
        return ""

    corrida = []                                 # pedaços da corrida atual
    for k, (ini, fim) in enumerate(pedacos):
        p = texto[ini:fim]
        separador = texto[corrida[-1][1]:ini] if corrida else ""
        if corrida and _QUEBRA_CORRIDA.search(separador):
            yield from _fechar(corrida, texto)
            corrida = []
        # 'l.' ou 'O' soltos entram no começo OU no meio do número ('2.O2 O. 005', '2018 G 26 0000'),
        # desde que venha um pedaço numérico depois
        if (_eh_pedaco_numerico(p) or (corrida and _eh_ligacao(p)) or
                (_eh_talvez_numero(p, texto[pedacos[k - 1][0]:pedacos[k - 1][1]] if k else "")
                 and _eh_pedaco_numerico(proximo_util(k)))):
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
    # começos possíveis: início de palavra ou logo depois de '(' ('desprovido.(AREsp 1.234')
    inicios = [ini_jan + m.start() for m in re.finditer(r"(?<!\S)\S|(?<=[(\[,;])\S", texto[ini_jan:ini_num])]
    for s in inicios:                                    # do mais distante para o mais próximo
        if texto[s:ini_num].strip() == "e":
            continue                                     # '2019 e 2020', 'itens 3 e 4': conjunção, não Embargos
        if texto[s] in "([":
            s += 1                                       # o parêntese de abertura não faz parte da citação
        if re.search(r"[()\[\]]", texto[s:ini_num]):
            continue                                     # a classe não atravessa parênteses ('do CP) (HC 1')
        if not all(palavra_conhecida(w) for w in texto[s:ini_num].split()):
            continue                                     # alguma palavra fora do vocabulário: não é classe
        siglas = canonizar_classe(texto[s:ini_num])
        conhecidas = [x for x in siglas if not x.startswith("?")]
        if conhecidas and classe_base(tuple(conhecidas)) and all(
                not x.startswith("?") or _neutro(x) for x in siglas):
            # não começar a citação num conectivo solto ('no AgInt...' -> 'AgInt...'), no 'e' de uma lista
            # ('..., e AgInt no REsp'), em pontuação, nem num tribunal seguido de pontuação ('STJ. AgInt')
            while True:
                m = re.match(rf"(?:(?i:{_CONECTORES})|e|[-–—.,;:'\"“”‘’«»]+)\s+|[-–—.,;:'\"“”‘’«»]+(?=\S)|"
                             rf"(?:{'|'.join(TRIBUNAIS)})[.,;:]+\s*", texto[s:ini_num])
                if not m:
                    break
                s += m.end()
            siglas = canonizar_classe(texto[s:ini_num])      # a classe é relida depois de ajustar o início
            conhecidas = [x for x in siglas if not x.startswith("?")]
            palavra = texto[s:ini_num].strip()
            if re.fullmatch(r"[a-zà-ú]{1,4}", palavra):
                return None                              # 'ado', 'mi', 'sec' minúsculos: palavra, não sigla
            trib = next((x[1:] for x in siglas if x.startswith("?") and x[1:] in TRIBUNAIS), None)
            return s, tuple(conhecidas), trib
    return None


def _uf_a_direita(texto: str, fim: int):
    """'/PR' | ' - PR' | ' (PR)' | 'PR' colado no número. O ')' só é consumido se houver '('."""
    m = re.match(r"(?P<sep>\s*(?:/|[-–])\s*|\s*\(\s*|)(?P<uf>[A-Z]{2})(?![A-Za-z])", texto[fim:fim + 10])
    if m and m["uf"] in UFS:
        fim_uf = fim + m.end()
        if "(" in m["sep"]:
            fecha = re.match(r"\s*\)", texto[fim_uf:])
            fim_uf += fecha.end() if fecha else 0
        return fim_uf, m["uf"]
    return fim, None


def _sufixo_stf(texto: str, fim: int):
    """'RE 1.492.256-AgR-EDv-AgR', 'AI 742.460-RG': recursos internos escritos DEPOIS do número."""
    siglas = []
    while (m := re.match(r"-([A-Z][A-Za-z]{1,5})\b", texto[fim:])) and m[1] not in UFS:
        siglas += [x for x in canonizar_classe(m[1]) if not x.startswith("?")]
        fim += m.end()
    return fim, tuple(siglas)


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
        if re.search(r"\n[ \t\xa0]*\n", texto[s:ini]):
            continue                                     # classe e número em parágrafos diferentes
        if len(digitos(num_bruto)) <= 2 and re.match(r"/\d{1,2}\b", texto[fim:fim + 4]):
            continue                                     # '12/3/2024': dia de uma data, não processo
        # 'não conheceu do habeas corpus. 2. O embargante...': número de parágrafo, não de processo
        if len(digitos(num_bruto)) <= 2 and re.search(r"[A-Za-zÀ-ú]{3,}\.\s+$", texto[s:ini]):
            continue
        numero = numero_canonico(num_bruto)
        if not numero:
            continue
        fim, internos = _sufixo_stf(texto, fim)
        fim_uf, uf = _uf_a_direita(texto, fim)
        achados.append(Candidata(s, fim_uf, "acordao", "jurisprudencia", numero=numero,
                                 classe=internos[::-1] + classe, uf=uf, tribunal=trib))
    return achados


# ============================================================================ súmulas

# número com OCR ('B3', '4O4', '2ll'): letras maiúsculas/'l' casadas sem re.I, com ao menos um dígito
_NUM_OCR = r"(?-i:[OlISBG]*\d[\dOlISBG]*)"      # sem ambiguidade: o primeiro dígito ancora
_SUMULA_PALAVRA = r"[S5$][úu](?:m|rn)(?:u[l1I|]a)?\.?"          # Súmula | Súm. | 5úmula | Súrnula | Súmu1a
_SUMULA = re.compile(
    rf"(?:(?P<cab>{_SUMULA_PALAVRA}|Verbete|Enunciado)\s+(?:(?P<vinc>{_ocr('vinculante')})\s+)?|\b(?-i:(?P<sv>[S5]V))\s+)"
    rf"(?:n\s*[º°o.]*\s*)?(?P<num>{_NUM_OCR})"
    rf"(?:\s*,\s*(?:item\s+|inciso\s+)?[IVX]+\b\s*,?)?"                            # 'Súmula 331, IV, do TST'
    rf"(?:\s+d[ao]\s+(?P<sum>{_SUMULA_PALAVRA})(?:\s+de\s+{_ocr('jurisprudencia')})?"   # 'Enunciado 83 da Súmula'
    rf"(?:\s+(?:(?P<vinc2>{_ocr('vinculante')})|{_ocr('dominante')}))?)?"
    rf"(?:(?:\s*,?\s*d[oa]\s+(?:(?:[ce]\.|egr[ée]gio|colendo)\s+)?|\s*/\s*)"         # 'do STJ' | 'do c. STJ' | '/STJ'
    rf"(?P<trib>{_TRIB_SIGLA}|{_TRIB_EXTENSO}))?",
    re.I)


# plural: 'Súmulas n. 5 e 7/STJ', 'Súmulas 282 e 356 do STF', 'Súmulas n. 5, 7 e 83/STJ' — uma citação por número
_SUMULAS = re.compile(
    rf"(?P<cab>{_SUMULA_PALAVRA[:-4]}s|Enunciados|Verbetes)\s+(?:n[º°os.]*\s*)?"
    rf"(?P<nums>{_NUM_OCR}(?:\s*(?:,|\be\b)\s*{_NUM_OCR})+)"
    rf"(?:(?:\s*,?\s*d[oa]\s+(?:(?:[ce]\.|egr[ée]gio|colendo)\s+)?|\s*/\s*)(?P<trib>{_TRIB_SIGLA}|{_TRIB_EXTENSO}))?",
    re.I)


def _sumulas_plural(texto: str) -> list[Candidata]:
    achados = []
    for m in _SUMULAS.finditer(texto):
        trib = sigla_tribunal(m["trib"]) if m["trib"] else None
        nums = list(re.finditer(_NUM_OCR, m["nums"]))
        for k, x in enumerate(nums):
            ini = m.start() if k == 0 else m.start("nums") + x.start()
            fim = m.end() if k == len(nums) - 1 else m.start("nums") + x.end()
            n = int(digitos(x.group()))
            achados.append(Candidata(ini, fim, "sumula", "jurisprudencia", tribunal=trib,
                                     chave_norma=("sumula", trib, False, n) if trib else None))
    return achados


def detectar_sumulas(texto: str) -> list[Candidata]:
    achados = _sumulas_plural(texto)
    for m in _SUMULA.finditer(texto):
        cab = (m["cab"] or "").lower()
        if cab in ("enunciado", "verbete") and not (m["sum"] or m["trib"]):
            continue                                  # 'Enunciado 5 da Jornada', 'verbete 12': podem ser qualquer coisa
        trib = m["trib"]
        if trib:
            trib = sigla_tribunal(trib)
        vinc = bool(m["vinc"] or m["vinc2"] or m["sv"])
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
    r"\b(?i:t[eê]m[aã](?:\s+(?:repetitivo|RG))?\s+(?:n\s*[º°o.]*\s*)?)(?P<num>\d(?:[\d.]*\d)?)"
    r"(?i:\s*,?\s*d[ao]s?\s+(?:repercuss[ãa]o\s+geral|recursos?\s+repetitivos?|STF|STJ|TST|TSE)|\s*/\s*(?:STF|STJ))?")


def detectar_temas(texto: str) -> list[Candidata]:
    return [Candidata(m.start(), m.end(), "tema", "jurisprudencia", numero=digitos(m["num"]))
            for m in _TEMA.finditer(texto)]


# ============================================================================ artigos de lei

_ARTIGO = re.compile(
    r"\b(?:art(?:igo)?s?\.?)\s*(?P<num>(?-i:(?=[\dOlISBG]*\d)[\dOlISBG]{1,3}(?:\.[\dOlISBG]{3})?))"   # '3l2', '29O'
    r"\s*[º°o]?(?:-[A-Z])?"
    r"(?P<compl>(?:\s*,\s*(?:(?:§|par[áa]grafo)\s*\d+\s*[º°o]?(?:-[A-Z])?|par[áa]grafo\s+[úu]nico|"
    r"inciso\s+[IVXLC]+|(?!CC\b)[IVXLC]+\b(?:-[A-Z])?|al[íi]nea\s+['‘’\"]?[a-z]['‘’\"]?|['‘’\"][a-z]['‘’\"]|caput))*)"
    r"\s*,?\s*(?:d[oa]s?\s+)?", re.I)                        # 'art. 206, § 3º, V, CC' (sem 'do')
# ordem inversa: 'CC, art. 885', 'CPC, art. 1.022, § 2º'
_ARTIGO_INVERSO = re.compile(
    r"\b(?P<lei>[A-Z][A-Za-z]{1,5}(?:/\d{2,4})?)\s*,\s*arts?\.\s*(?P<num>(?-i:(?=[\dOlISBG]*\d)[\dOlISBG]{1,3}"
    r"(?:\.[\dOlISBG]{3})?))\s*[º°o]?(?:-[A-Z])?")


def detectar_artigos(texto: str) -> list[Candidata]:
    achados = []
    for m in _ARTIGO.finditer(texto):
        lei = ler_lei(texto[m.end():])
        if not lei:
            continue
        codigo, tamanho = lei
        n = int(digitos(m["num"]))
        achados.append(Candidata(m.start(), m.end() + tamanho, "artigo", "lei",
                                 chave_norma=("artigo", codigo, n), extra={"lei": codigo}))
    for m in _ARTIGO_INVERSO.finditer(texto):
        lei = ler_lei(m["lei"])
        if not lei or lei[1] != len(m["lei"]):
            continue
        achados.append(Candidata(m.start(), m.end(), "artigo", "lei",
                                 chave_norma=("artigo", lei[0], int(digitos(m["num"]))), extra={"lei": lei[0]}))
    return achados


# ============================================================================ incompletas

_NOME = r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ'’]+(?:\s+(?:(?:d[aeo]s?|e)\s+)?[A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ'’]+)*"
_MIN = rf"(?:{_ocr('min')}(?:{_ocr('istr')}[oa])?\.?\s*)?"                   # 'Min.' | 'Ministro' | 'Minlstro'
# o relator: 'pela relatoria de X' (tolera 'dc', 'pcla'), 'de relatoria do Min. X', 'sob a relatoria da
# Ministra X', 'relatado pelo Ministro X', 'Rel. Min. X', 'Relator Ministro X', '(Rel. Min. X)'
_RELATOR = re.compile(
    rf"(?P<marca>(?i:(?:(?:p{_ocr('el')}[ao]|sob(?:\s+a)?|d[ao]|de)\s+)?r{_ocr('elat')}\w{{2,6}}\s+"
    rf"(?:d\w{{1,2}}|p{_ocr('el')}[oa])\s+{_MIN}|"
    rf"\(?\s*r{_ocr('el')}(?:{_ocr('ator')}a?)?\.?\s*{_MIN}))"
    rf"(?P<nome>{_NOME})\)?")
_ANO_ANTES = re.compile(r"(?P<ano>(?:19|20)\d\d)[\s,;(]*$")                        # '... de 2024, Rel. Min. X'
_ANO_DEPOIS = re.compile(rf"^(?:\s*\([^()\n]{{0,80}}\)|[^.;()\n]{{0,60}}?)?\s*,?\s*"   # '(Desembargador convocado...)'
                         rf"(?:\w{{3,12}}[aieãl1]d[oa0]\s+)?(?:em|d[eéc])\s+(?P<ano>(?:19|20)\d\d)", re.I)                                                         # 'Rel. Min. X, julgado em 2021'
_CABECAS = re.compile("(?:" + "|".join(_ocr(p) for p in (
    "julgado", "julgada", "precedente", "acordao", "aresto", "decisao", "voto")) + ")$", re.I)
_TRIB_TXT = rf"(?:{_TRIB_SIGLA}|{_TRIB_EXTENSO})"
# entre a cabeça e o ANO: 'do STF proferido em', 'do STM de', ', de', 'julgado em', 'julgado pelo STF em'
_MEIO_ANO = re.compile(rf"^(?P<cabeca>.+?)\s*,?\s*(?:d[oa0]\s+(?P<trib>{_TRIB_TXT})\s*,?\s*)?"
                       rf"(?:\w{{3,12}}[aieãl1]d[oa0]\s+(?:p{_ocr('el')}[oa]\s+(?P<trib2>{_TRIB_TXT})\s+)?)?"
                       rf"(?:d[eéc]|em)\s*$", re.S | re.I)
# entre a cabeça e o RELATOR, quando o ano vem depois: 'acórdão do STM, de relatoria do Min. X, julgado em 2025'
_MEIO_REL = re.compile(rf"^(?P<cabeca>.+?)\s*,?\s*(?:d[oa0]\s+(?P<trib>{_TRIB_TXT})\s*,?\s*)?$", re.S | re.I)


def _cabeca_valida(cabeca: str) -> bool:
    if _CABECAS.fullmatch(cabeca.strip()):
        return True
    siglas = canonizar_classe(cabeca)
    return bool(siglas) and all(not x.startswith("?") for x in siglas) and classe_base(siglas) is not None


def _achar_cabeca(texto: str, ancora: int, meio_rx):
    """Procura, à esquerda da âncora, a cabeça da referência ('julgado', 'precedente', classe...).
    Devolve (início, tribunal) ou None."""
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
            trib = meio["trib"] or meio.groupdict().get("trib2")
            return s + desloc, sigla_tribunal(trib) if trib else None
    return None


def detectar_incompletas(texto: str) -> list[Candidata]:
    """Referência a uma decisão concreta, sem número: CABEÇA [do TRIBUNAL] + ANO + RELATOR.
    Âncora no relator; o ano pode vir antes ('de 2024, Rel. Min. X') ou depois ('Rel. Min. X, julgado em 2024')."""
    achados = []
    for m in _RELATOR.finditer(texto):
        ini_rel, fim = m.start(), m.end()
        antes = _ANO_ANTES.search(texto[max(0, ini_rel - 40):ini_rel])
        if antes:
            ano = int(antes["ano"])
            achou = _achar_cabeca(texto, max(0, ini_rel - 40) + antes.start("ano"), _MEIO_ANO)
        else:
            depois = _ANO_DEPOIS.match(texto[fim:fim + 120])
            if not depois:
                continue                                       # sem ano: não é a referência que buscamos
            ano = int(depois["ano"])
            fim += depois.end()
            achou = _achar_cabeca(texto, ini_rel, _MEIO_REL)
        if achou:
            inicio, trib = achou
            achados.append(Candidata(inicio, fim, "incompleta", "jurisprudencia", tribunal=trib,
                                     extra={"ano": ano, "relator": m["nome"]}))
    return achados


# ============================================================================ tudo junto

_PRIORIDADE = {"incompleta": 0, "sumula": 1, "artigo": 1, "tema": 1, "acordao": 2}

# sinais de ruído de nível 2 no trecho: quebra de linha, letra colada em dígito ('4S5', 'RE5PE',
# '170076O'; ordinal 'o'/'a'/'º' não conta), número com espaço ('1 741 784') ou hífen/ponto partido ('33.-')
_RUIDO = re.compile(r"\n|\d\s+\d|\d\s*[.\-]\s+\d|\d[.\-]\s*[.\-]|(?<=\d)[^\W\doaºª°_]|[^\W\d_ºª°](?=\d)"
                    r"|\b[OlLISBGZ5]{1,3}\.?\s+\d")   # grupo de dígitos todo em letras de OCR: 'SS. 413', '5LS 2.883'


def tem_ruido(trecho: str) -> bool:
    return bool(_RUIDO.search(trecho))


def detectar(texto: str) -> list[Candidata]:
    todas = (detectar_incompletas(texto) + detectar_sumulas(texto) + detectar_temas(texto) +
             detectar_artigos(texto) + detectar_acordaos(texto))
    # sobreposição: fica a de maior prioridade (e, empatando, a mais longa)
    todas.sort(key=lambda c: (_PRIORIDADE[c.especie], -(c.fim - c.inicio)))
    escolhidas: list[Candidata] = []
    for c in todas:
        if all(c.fim <= e.inicio or c.inicio >= e.fim for e in escolhidas):
            c.extra["ruido"] = tem_ruido(texto[c.inicio:c.fim])
            escolhidas.append(c)
    return sorted(escolhidas, key=lambda c: c.inicio)
