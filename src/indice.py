"""Índice da base canônica: uma "ficha" de identidade para cada registro.

Por que não usar a busca de texto (FTS) direto? Porque ela devolve todo documento
que MENCIONA um número, e acórdãos citam uns aos outros o tempo todo. O que
identifica um registro é o cabeçalho dele — então lemos o cabeçalho de cada um,
uma vez, e montamos um dicionário número -> fichas.

Cada tribunal escreve o cabeçalho de um jeito:
  STJ  'AgInt no AGRAVO EM RECURSO ESPECIAL Nº 1.996.496 - RJ (2021/...)'
  STF  '22/04/2026 PRIMEIRA TURMA AG.REG. NA RECLAMAÇÃO 76.532 RIO DE JANEIRO RELATOR...'
  TSE  'TRIBUNAL SUPERIOR ELEITORAL ACÓRDÃO RECURSO ESPECIAL ELEITORAL Nº 0600530-94.2020.6.26.0171 - ...'
  STM  '... APELAÇÃO CRIMINAL Nº 7000449-40.2023.7.00.0000/RS RELATOR: ...'
  TST  o número NÃO está no topo; aparece em 'Vistos, relatados e discutidos estes
       autos de Recurso de Revista nº TST-RR-79500-16.2009.5.15.0016 ...'
Súmulas e artigos de lei começam com uma linha de identificação:
       'Súmula n. 83 do STJ'  |  'Artigo 373 da Lei nº 13.105, de 16 de março de 2015'
"""
import hashlib
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .normalizar import digitos, dv_cnj_valido, eh_cnj, justica_cnj, numero_canonico, sem_acento
from .vocabulario import canonizar_classe


@dataclass
class Ficha:
    id: int                    # doc_id Jusbrasil — é o que vai em id_canonico
    documento_id: str
    tribunal: str | None
    natureza: str              # acordao | sumula | dispositivo
    numero: str | None = None  # número canônico (ver normalizar.numero_canonico)
    classe: tuple = ()         # siglas canônicas, ex.: ('AgInt', 'AREsp')
    classe_bruta: str = ""     # como estava escrito no cabeçalho
    uf: str | None = None
    ano: int | None = None
    relator: str | None = None
    apelidos: list = field(default_factory=list)   # outros números do mesmo processo
    assinatura: str = ""       # hash do texto: registros com texto idêntico são duplicatas
    chave_norma: tuple | None = None   # súmula: ('sumula', 'STJ', False, 83); artigo: ('artigo', 'L13105', 373)
    avisos: list = field(default_factory=list)


# ----------------------------------------------------------------------------- padrões

_CNJ = r"(?P<cnj>\d{1,7}\s*-\s*\d{2}\s*\.\s*\d{4}\s*\.\s*(?P<j>\d)\s*\.\s*\d{2}\s*\.\s*\d{4})"
_NUM_SIMPLES = r"(\d{1,3}(?:\.\d{3})+|\d{1,7})"
_N = r"n\s*\.?\s*[º°o]?\s*\.?\s*"       # nº | n° | no | n. | n . º

_ESTADOS = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM", "BAHIA": "BA",
    "CEARA": "CE", "DISTRITO FEDERAL": "DF", "ESPIRITO SANTO": "ES", "GOIAS": "GO",
    "MARANHAO": "MA", "MATO GROSSO DO SUL": "MS", "MATO GROSSO": "MT", "MINAS GERAIS": "MG",
    "PARA": "PA", "PARAIBA": "PB", "PARANA": "PR", "PERNAMBUCO": "PE", "PIAUI": "PI",
    "RIO DE JANEIRO": "RJ", "RIO GRANDE DO NORTE": "RN", "RIO GRANDE DO SUL": "RS",
    "RONDONIA": "RO", "RORAIMA": "RR", "SANTA CATARINA": "SC", "SAO PAULO": "SP",
    "SERGIPE": "SE", "TOCANTINS": "TO",
}

_RUIDO_STJ = re.compile(
    r"Superior Tribunal de Justiça|Revista Eletrônica de Jurisprudência|"
    r"Exportação de Auto Texto do Word para o Editor de Documentos do STJ", re.I)


def _espacos(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _uf_por_nome(nome: str) -> str | None:
    return _ESTADOS.get(sem_acento(nome).upper().strip())


# ----------------------------------------------------------------------------- por tribunal

def _cabecalho_stj(texto):
    h = _espacos(_RUIDO_STJ.sub(" ", texto[:600]))
    m = re.search(rf"^(?P<classe>.{{2,160}}?)\s*N\s*[º°o]\s*\.?\s*(?P<num>[\d\.]{{1,12}})\s*[-–]\s*(?P<uf>[A-Z]{{2}})\b", h)
    if m:
        return dict(classe=m["classe"], numero=numero_canonico(m["num"]), uf=m["uf"], apelidos=[])
    return None


def _cabecalho_stf(texto):
    h = _espacos(texto[:800])
    m = re.search(rf"(?:TURMA|PLENÁRIO|PLENO)\s+(?P<classe>.{{3,200}}?)\s+(?P<num>{_NUM_SIMPLES})\s+"
                  rf"(?P<estado>[A-ZÁÉÍÓÚÂÊÔÃÕÇ ]{{4,30}}?)\s+(?:RELATOR|REDATOR)", h)
    if m:
        return dict(classe=m["classe"], numero=numero_canonico(m["num"]), uf=_uf_por_nome(m["estado"]), apelidos=[])
    return None


def _cabecalho_cnj(texto, justica):
    """TSE e STM: a classe vem logo antes de 'Nº <número>' no topo do documento.

    TSE antigo traz dois números: 'Nº 36.038 ( 43342-43.2009.6.00.0000)'. O CNJ é o
    principal; o antigo vira um apelido (a peça pode citar qualquer um dos dois).
    """
    h = _espacos(texto[:1500])
    for m in re.finditer(r"(?P<classe>[A-ZÁÉÍÓÚÂÊÔÃÕÇ .\-]{3,160}?)\s*(?:N\s*[º°o‚]?\s*\.?\s*)?"
                         r"(?P<resto>\d[\d\s.\-–()]{3,60})", h):
        resto, antigo = m["resto"], None
        if "(" in resto:
            antigo, _, resto = resto.partition("(")
            resto = resto.partition(")")[0]
        numero = numero_canonico(resto)
        if not (eh_cnj(numero) and justica_cnj(numero) == justica):
            continue
        classe = re.split(r"ACÓRDÃO|ACORDAO|Pleno|STM|\d{2}/\d{2}/\d{4}", m["classe"])[-1]
        uf = re.match(r"\s*/\s*([A-Z]{2})\b", h[m.end():])
        apelidos = [numero_canonico(antigo)] if antigo and digitos(antigo) else []
        return dict(classe=classe, numero=numero, uf=uf[1] if uf else None, apelidos=apelidos)
    # TSE bem antigo: só o número "de classe", sem CNJ ('AÇÃO CAUTELAR Nº 3.334')
    m = re.search(rf"(?P<classe>[A-ZÁÉÍÓÚÂÊÔÃÕÇ .\-]{{3,160}}?)\s*N\s*[º°o]\s*\.?\s*(?P<num>{_NUM_SIMPLES})\b", h)
    if m:
        classe = re.split(r"ACÓRDÃO|ACORDAO|Pleno|STM|\d{2}/\d{2}/\d{4}", m["classe"])[-1]
        return dict(classe=classe, numero=numero_canonico(m["num"]), uf=None, apelidos=[])
    return None


def _cabecalho_tst(texto):
    """TST: o número do próprio processo está em 'Vistos, relatados e discutidos estes
    autos de <classe> nº TST-<siglas>-<número CNJ>' (ou no rodapé 'PROCESSO Nº TST-...')."""
    t = _espacos(texto)
    padroes = [
        rf"Vistos,?\s+relatados\s+e\s+discutidos\s+(?:estes|os\s+presentes|esses)?\s*autos\s+de\s+"
        rf".{{0,200}}?{_N}\s*TST\s*-\s*(?P<siglas>[A-Za-z\-\s]*?)\s*-?\s*{_CNJ}",
        rf"PROCESSO\s+{_N}\s*TST\s*-\s*(?P<siglas>[A-Za-z\-\s]*?)\s*-?\s*{_CNJ}",
    ]
    for p in padroes:
        m = re.search(p, t, re.I)
        if m:
            return dict(classe=m["siglas"], numero=numero_canonico(m["cnj"]), uf=None, apelidos=[])
    return None


def _cnj_mais_frequente(texto, justica):
    """Plano B: o número CNJ válido (dígito verificador ok) que mais aparece no texto.
    O próprio processo costuma ser repetido em rodapés e na certidão de julgamento."""
    nums = [numero_canonico(x["cnj"]) for x in re.finditer(_CNJ, _espacos(texto)) if x["j"] == justica]
    nums = [n for n in nums if n and dv_cnj_valido(n)]
    return Counter(nums).most_common(1)[0][0] if nums else None


# ----------------------------------------------------------------------------- normas

def _chave_sumula(texto):
    m = re.match(r"\s*Súmula\s+(Vinculante\s+)?n\.?\s*(\d+)\s+do\s+(STF|STJ|TST|TSE)", texto, re.I)
    if m:
        return ("sumula", m[3].upper(), bool(m[1]), int(m[2]))
    return None


def lei_canonica(descricao: str) -> str | None:
    """'Lei nº 13.105, de 16 de março de 2015' -> 'L13105'; 'Constituição Federal de 1988' -> 'CF'."""
    d = sem_acento(descricao).upper()
    if "CONSTITUICAO" in d:
        return "CF"
    m = re.search(r"(LEI COMPLEMENTAR|DECRETO-?LEI|LEI)\s*(?:N\s*[O°º.]*\s*)?([\d\.]+)", d)
    if m:
        tipo = {"LEI COMPLEMENTAR": "LC", "LEI": "L"}.get(m[1], "DL")
        return f"{tipo}{int(m[2].replace('.', ''))}"
    return None


def _chave_artigo(texto):
    m = re.match(r"\s*Artigo\s+(\d+)\s*[º°o]?\s+d[oa]\s+([^\n]+)", texto)
    if m:
        lei = lei_canonica(m[2])
        if lei:
            return ("artigo", lei, int(m[1]))
    return None


# ----------------------------------------------------------------------------- índice

class Indice:
    def __init__(self, caminho_db: str):
        self.fichas: list[Ficha] = []
        self.por_numero: dict[str, list[Ficha]] = defaultdict(list)
        self.por_norma: dict[tuple, Ficha] = {}
        con = sqlite3.connect(caminho_db)
        linhas = con.execute("SELECT id, documento_id, tribunal, natureza, ano, relator, texto "
                             "FROM documentos ORDER BY documento_id").fetchall()
        con.close()
        for id_, doc, trib, natureza, ano, relator, texto in linhas:
            f = Ficha(id=id_, documento_id=doc, tribunal=trib, natureza=natureza, ano=ano, relator=relator,
                      assinatura=hashlib.md5(texto.encode("utf-8")).hexdigest())
            if natureza == "acordao":
                self._ler_acordao(f, texto)
                for n in [f.numero, *f.apelidos]:
                    if n:
                        self.por_numero[n].append(f)
            else:
                f.chave_norma = _chave_sumula(texto) if natureza == "sumula" else _chave_artigo(texto)
                if f.chave_norma:
                    self.por_norma[f.chave_norma] = f
                else:
                    f.avisos.append("norma sem linha de identificação")
            self.fichas.append(f)

    def _ler_acordao(self, f: Ficha, texto: str):
        leitor = {
            "STJ": _cabecalho_stj,
            "STF": _cabecalho_stf,
            "TSE": lambda t: _cabecalho_cnj(t, "6"),
            "STM": lambda t: _cabecalho_cnj(t, "7"),
            "TST": _cabecalho_tst,
        }[f.tribunal]
        r = leitor(texto) or dict(classe="", numero=None, uf=None, apelidos=[])
        justica = {"TST": "5", "TSE": "6", "STM": "7"}.get(f.tribunal)
        cnj_quebrado = r["numero"] and eh_cnj(r["numero"]) and not dv_cnj_valido(r["numero"])
        if justica and (not r["numero"] or cnj_quebrado):
            plano_b = _cnj_mais_frequente(texto, justica)
            if plano_b:
                f.avisos.append(f"número do cabeçalho {r['numero']} inválido/ausente; usando o mais frequente {plano_b}")
                if r["numero"]:
                    r["apelidos"].append(r["numero"])      # guarda o do cabeçalho mesmo assim
                r["numero"] = plano_b
            elif r["numero"]:
                f.avisos.append(f"dígito verificador inválido em {r['numero']}; mantido")
        if not r["numero"]:
            f.avisos.append("número não encontrado")
            return
        f.numero, f.uf, f.apelidos = r["numero"], r["uf"], [a for a in r["apelidos"] if a]
        f.classe_bruta = _espacos(r["classe"])
        siglas = canonizar_classe(f.classe_bruta)
        f.classe = tuple(s for s in siglas if not s.startswith("?"))
        desconhecidas = [s[1:] for s in siglas if s.startswith("?")]
        if desconhecidas:
            f.avisos.append(f"palavras não reconhecidas na classe: {desconhecidas}")

    # ---- consultas
    def buscar_numero(self, numero_canonico_: str) -> list[Ficha]:
        return self.por_numero.get(numero_canonico_, [])

    def buscar_norma(self, chave: tuple) -> Ficha | None:
        return self.por_norma.get(chave)

    def resumo(self) -> str:
        acord = [f for f in self.fichas if f.natureza == "acordao"]
        sem_num = [f for f in acord if not f.numero]
        colisoes = {n: fs for n, fs in self.por_numero.items() if len(fs) > 1}
        linhas = [f"{len(self.fichas)} registros: {len(acord)} acórdãos, {len(self.por_norma)} normas identificadas",
                  f"acórdãos sem número extraído: {len(sem_num)}",
                  f"números com mais de um registro: {len(colisoes)}"]
        return "\n".join(linhas)
