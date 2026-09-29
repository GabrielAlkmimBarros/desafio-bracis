"""Vocabulário jurídico: classes processuais e seus nomes/abreviações.

`canonizar_classe` transforma qualquer forma de escrever a classe numa tupla de
siglas canônicas. Exemplos:

    'AgInt no AGRAVO EM RECURSO ESPECIAL'        -> ('AgInt', 'AREsp')
    'EDcl nos EDcl no AgInt no ARESP'            -> ('ED', 'ED', 'AgInt', 'AREsp')
    'AG.REG. NA RECLAMAÇÃO'                      -> ('AgRg', 'Rcl')
    'Rec. Esp.'  |  'R.Esp.'  |  'REsp'          -> ('REsp',)
    'TST-ED-E-ED-RR'                             -> ('ED', 'E', 'ED', 'RR')

A primeira parte da tupla são os recursos internos (agravos, embargos); a última
é a classe-base do processo. É isso que desempata dois registros com o mesmo número.
"""
import re

from .normalizar import sem_acento

# (padrão sobre texto normalizado, siglas). A ordem importa: nomes mais longos
# primeiro, para "AGRAVO EM RECURSO ESPECIAL" não ser lido como "AGRAVO" + "RESP".
_PADROES = [
    # ---- classes-base com nome composto (antes das partes que as compõem)
    (r"AGRAVO EM RECURSO ESPECIAL ELEITORAL|ARESPE(?:L)?", ("AREspE",)),
    (r"RECUR(?:SO)? ESPECIAL ELEITORAL|RESPE(?:L)?", ("REspE",)),
    (r"EMBARGOS DE DIVERGENCIA EM AGRAVO EM RECURSO ESPECIAL|EARESP", ("EDiv", "AREsp")),
    (r"EMBARGOS DE DIVERGENCIA EM RESP|EMBARGOS DE DIVERGENCIA EM RECURSO ESPECIAL|ERESP", ("EDiv", "REsp")),
    (r"AGRAVO EM RECURSO ESPECIAL|ARESP|AG RESP|AGRESP", ("AREsp",)),
    (r"RECURSO ESPECIAL|RESP|REC ESP|R ESP", ("REsp",)),
    (r"RECURSO EXTRAORDINARIO COM AGRAVO|ARE", ("ARE",)),
    (r"RECURSO EXTRAORDINARIO|RE", ("RE",)),
    (r"RECURSO EM HABEAS CORPUS|RECURSO ORDINARIO EM HABEAS CORPUS|RHC", ("RHC",)),
    (r"HABEAS CORPUS|H C|HC", ("HC",)),
    (r"RECURSO (?:ORD(?:INARIO)? )?EM MANDADO DE SEGURANCA|RMS", ("RMS",)),
    (r"MANDADO DE SEGURANCA|MS", ("MS",)),
    (r"RECLAMACAO|RECL|RCL", ("Rcl",)),
    (r"ACAO RESCISORIA|AR", ("AR",)),
    (r"CONFLITO DE COMPETENCIA|CC", ("CC",)),
    (r"SUSPENSAO DE LIMINAR E DE SENTENCA|SLS", ("SLS",)),
    (r"SUSPENSAO DE SEGURANCA|SS", ("SS",)),
    (r"ACAO PENAL|APN", ("APn",)),
    (r"ACAO DIRETA DE INCONSTITUCIONALIDADE|ADI", ("ADI",)),
    (r"CAUTELAR INOMINADA CRIMINAL", ("CauInomCrim",)),
    (r"PETICAO|PET", ("Pet",)),
    # TST
    (r"AGRAVO DE INSTRUMENTO EM RECURSO DE REVISTA|AIRR", ("AIRR",)),
    (r"RECURSO DE REVISTA COM AGRAVO|ARR|RRAG", ("ARR",)),     # RRAg é o nome novo do ARR
    (r"RECURSO ORDINARIO TRABALHISTA|ROT", ("RO",)),
    (r"RECURSO DE REVISTA|RR", ("RR",)),
    # TSE
    (r"RECURSO NA REPRESENTACAO|R RP", ("R", "Rp")),
    (r"REPRESENTACAO|RP", ("Rp",)),
    (r"RECURSO ORDINARIO(?: ELEITORAL)?|RO", ("RO",)),
    (r"RECURSO CONTRA EXPEDICAO DE DIPLOMA|RCED", ("RCED",)),
    (r"ACAO DE INVESTIGACAO JUDICIAL ELEITORAL|AIJE", ("AIJE",)),
    (r"PRESTACAO DE CONTAS|PC", ("PC",)),
    (r"TUTELA CAUTELAR ANTECEDENTE|TUTCAUTANT", ("TutCautAnt",)),
    (r"ACAO CAUTELAR|AC", ("AC",)),
    (r"LISTA TRIPLICE|LT", ("LT",)),
    (r"AGRAVO DE INSTRUMENTO|AI", ("AI",)),
    # STM
    (r"APELACAO(?: CRIMINAL)?|APL|APELACAO", ("Apl",)),
    (r"RECURSO EM SENTIDO ESTRITO|RSE", ("RSE",)),
    (r"EMBARGOS INFRINGENTES E DE NULIDADE|EIN", ("EIN",)),
    (r"CONFLITO DE JURISDICAO|CJ", ("CJ",)),
    (r"CORREICAO PARCIAL(?: MILITAR)?|CP", ("CP",)),
    (r"INCOMPATIBILIDADE", ("Incomp",)),
    # ---- recursos internos (prefixos)
    (r"AGRAVO INTERNO|AGINT|AG INT", ("AgInt",)),
    (r"AGRAVO REGIMENTAL|AGRG|AG REG|AGR", ("AgRg",)),
    (r"EMBARGOS DE DECLARACAO(?: CRIMINAL)?|EMB DECL|EDCL|EDCIV|EDS|ED", ("ED",)),
    (r"EMBARGOS DE DIVERGENCIA|EMB DIV|EDV", ("EDiv",)),
    (r"EMBARGOS|EMB|E", ("E",)),
    (r"AGRAVO|AG", ("Ag",)),
    (r"QUESTAO DE ORDEM|QO", ("QO",)),
    (r"REFERENDO", ("Ref",)),
    (r"PEXT", ("PExt",)),
]

# Palavras que ligam as partes da classe e não carregam informação
_IGNORAR = re.compile(
    r"^(?:NO|NA|NOS|NAS|EM|DE|DO|DA|N|O|A|AO|TST|CRIMINAL|CIVEL|SUPERIOR|TRIBUNAL|MILITAR|"
    r"SEGUND[OA]S?|TERCEIR[OA]S?|QUART[OA]S?|QUINT[OA]S?|DECIM[OA]S?)$")

_REGEX = [(re.compile(rf"^(?:{p})\b"), s) for p, s in _PADROES]

# Todas as palavras que o vocabulário conhece (as dos padrões, com as partes opcionais
# por extenso, os conectivos e os tribunais). Serve para desfazer OCR: uma palavra
# desconhecida só é corrigida se a correção cair numa palavra conhecida.
_TODOS = "|".join([p for p, _ in _PADROES] + [_IGNORAR.pattern, "STF STJ TSE TST STM PROCESSO PROC"])
_CONHECIDAS = (set(re.findall(r"[A-Z]+", re.sub(r"\(\?:|\)\??|\?", "", _TODOS))) |      # 'RESPE(?:L)?' -> RESPEL
               set(re.findall(r"[A-Z]+", re.sub(r"\(\?:[^()]*\)\?", " ", _TODOS))))     # e -> RESPE


# confusões de OCR do nível 2 (as da aba Data e as vistas no desenvolvimento: 'profcrido', 'Júnlor')
_TROCAS_OCR = {"0": "O", "5": "S", "1": "LI", "L": "I", "I": "L", "C": "E", "RN": "M"}


def _variantes_ocr(palavra: str, max_trocas: int = 2):
    """Leituras alternativas de uma palavra, desfazendo até `max_trocas` confusões de OCR.
    Troca de letra por letra só em palavras de 4+ caracteres; dígito dentro de palavra, sempre."""
    fronteira, vistas = [palavra], {palavra}
    for _ in range(max_trocas):
        nova = []
        for p in fronteira:
            for de, paras in _TROCAS_OCR.items():
                if len(p) < 4 and not de.isdigit():
                    continue
                for i in (m.start() for m in re.finditer(de, p)):
                    for para in paras if de != "RN" else [paras]:
                        v = p[:i] + para + p[i + len(de):]
                        if v not in vistas:
                            vistas.add(v)
                            nova.append(v)
                            yield v
        fronteira = nova


def _corrigir_ocr(t: str) -> str:
    palavras = t.split(" ")
    for k, p in enumerate(palavras):
        if p and p not in _CONHECIDAS:
            palavras[k] = next((v for v in _variantes_ocr(p) if v in _CONHECIDAS), p)
    return " ".join(palavras)


def _preparar(texto: str) -> str:
    t = sem_acento(texto.replace("°", "º")).upper()     # 'n°' (grau) -> 'nº' -> 'NO'
    t = re.sub(r"\bA\s*\.\s*RESP", "ARESP", t)            # 'A.REsp' é AREsp (o 'A' solto seria artigo)
    t = re.sub(r"[.\-–—/()]", " ", t)
    t = re.sub(r"\b(NOS|NAS|NO|NA)(?=EMBARGOS|AGRAVO|RECURSO)", r"\1 ", t)   # 'nosEMBARGOS' colado
    t = re.sub(r"\bAG(?=(?:AIRR|ARR|RRAG|RR)\b)", "AG ", t)                     # TST 'AgARR' -> 'Ag ARR'
    return _corrigir_ocr(re.sub(r"\s+", " ", t).strip())    # 'Reclarnação', 'Rec1amação', 'C0RPUS'


def canonizar_classe(texto: str) -> tuple[str, ...]:
    """Lê a classe da esquerda para a direita e devolve as siglas canônicas.

    Palavras desconhecidas são ignoradas (não derrubam a leitura do resto).
    """
    t = _preparar(texto)
    # siglas grudadas do TST ("AgARR", "EEDRR") não são separadas aqui de propósito:
    # 'AGARR' é tentado inteiro; se não casar, cai como desconhecido.
    siglas: list[str] = []
    while t:
        for rx, s in _REGEX:
            m = rx.match(t)
            if m:
                siglas.extend(s)
                t = t[m.end():].lstrip()
                break
        else:
            palavra, _, t = t.partition(" ")
            if not _IGNORAR.match(palavra):
                siglas.append("?" + palavra)       # marca o desconhecido para inspeção
    return tuple(siglas)


def classe_base(siglas: tuple[str, ...]) -> str | None:
    """A última sigla conhecida é a classe-base do processo."""
    conhecidas = [s for s in siglas if not s.startswith("?")]
    return conhecidas[-1] if conhecidas else None


# Tribunal implícito em cada classe-base (quando a citação não diz o tribunal)
TRIBUNAIS_DA_CLASSE = {
    "REsp": {"STJ"}, "AREsp": {"STJ"}, "RHC": {"STJ", "STF"}, "RMS": {"STJ", "STF"},
    "HC": {"STJ", "STF", "STM"}, "MS": {"STJ", "STF"}, "Rcl": {"STF", "STJ"},
    "AR": {"STJ", "STF"}, "CC": {"STJ"}, "SLS": {"STJ"}, "SS": {"STJ"}, "APn": {"STJ", "STF"},
    "Pet": {"STJ", "STF"}, "CauInomCrim": {"STJ"},
    "RE": {"STF"}, "ARE": {"STF"}, "ADI": {"STF"},
    "RR": {"TST"}, "AIRR": {"TST"}, "ARR": {"TST"},
    "REspE": {"TSE"}, "AREspE": {"TSE"}, "Rp": {"TSE"}, "RO": {"TSE"}, "AI": {"TSE"},
    "Apl": {"STM"}, "RSE": {"STM"}, "EIN": {"STM"},
}
