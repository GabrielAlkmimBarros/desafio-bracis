"""Identifica a lei citada: 'CPC', 'Código de Processo Civil', 'Lei nº 13.105/2015' -> 'L13105'."""
import re
from difflib import SequenceMatcher
from functools import lru_cache

from .normalizar import digitos, sem_acento

# Nome ou sigla -> código da lei (tipo + número). A lista é conhecimento público, não vem da base: uma lei
# fora dela continua resolvendo quando citada pelo número; citada só pelo nome, não é detectada.
APELIDOS = {
    "cpc": "L13105", "ncpc": "L13105", "codigo de processo civil": "L13105",
    "novo codigo de processo civil": "L13105",
    "cc": "L10406", "codigo civil": "L10406",
    "clt": "DL5452", "consolidacao das leis do trabalho": "DL5452",
    "cf": "CF", "cf/88": "CF", "cf/1988": "CF", "crfb": "CF", "crfb/88": "CF",
    "constituicao federal": "CF", "constituicao da republica": "CF", "constituicao": "CF",
    "carta magna": "CF", "lei maior": "CF", "constituicao federal de 1988": "CF",
    "cpp": "DL3689", "codigo de processo penal": "DL3689",
    "cpm": "DL1001", "codigo penal militar": "DL1001",
    "cppm": "DL1002", "codigo de processo penal militar": "DL1002",
    "cp": "DL2848", "codigo penal": "DL2848",
    "cdc": "L8078", "codigo de defesa do consumidor": "L8078",
    "codigo eleitoral": "L4737",
    "ctn": "L5172", "codigo tributario nacional": "L5172",
    "eca": "L8069", "estatuto da crianca e do adolescente": "L8069",
    "lei das eleicoes": "L9504", "lei de inelegibilidades": "LC64", "lei de inelegibilidade": "LC64",
    "lei de execucao penal": "L7210", "lep": "L7210", "lei maria da penha": "L11340",
    "lei de improbidade administrativa": "L8429", "lei de improbidade": "L8429",
    "lindb": "DL4657", "lei de introducao as normas do direito brasileiro": "DL4657",
    "estatuto do idoso": "L10741", "estatuto da pessoa idosa": "L10741", "lei de drogas": "L11343",
    "lei dos juizados especiais": "L9099", "lei do mandado de seguranca": "L12016",
    "estatuto da advocacia": "L8906", "estatuto da oab": "L8906",
    "ctb": "L9503", "codigo de transito brasileiro": "L9503", "lei da acao civil publica": "L7347",
    "lgpd": "L13709", "lei geral de protecao de dados": "L13709", "marco civil da internet": "L12965",
    "estatuto do desarmamento": "L10826", "lei de falencias": "L11101", "lei do inquilinato": "L8245",
    "loman": "LC35", "lei organica da magistratura nacional": "LC35",
    "lei dos partidos politicos": "L9096", "estatuto dos militares": "L6880",
    "estatuto dos servidores publicos": "L8112", "regime juridico unico": "L8112",
}

# Número da lei com OCR ('13.l05', 'B.078'). As letras de OCR são casadas sem re.I para que 'nº' e
# palavras comuns não virem número.
_NUM_LEI = r"(?-i:[\dOlISBG]{1,3}(?:\.\s?[\dOlISBG]{3})+|[\dOlISBG]+)"
_LEI_NUMERADA = re.compile(
    r"(?P<tipo>Lei\s+C[o0](?:m|rn)p[l1I][ec](?:m|rn)[ec]ntar|LC|Decreto[\s\-]*Lei|DL|Lei)\s*"
    rf"(?:n\s*[º°o.]*\s*)?(?P<num>{_NUM_LEI})(?:\s*/\s*(?P<ano>\d{{2,4}}))?",
    re.I)


def _limpar(s: str) -> str:
    s = sem_acento(s).lower()
    s = s.replace("rn", "m")                       # OCR: rn <-> m
    return re.sub(r"\s+", " ", s).strip()


def ler_lei(texto: str) -> tuple[str, int] | None:
    """Lê a lei no início de `texto`. Devolve (código, quantos caracteres a lei ocupa) ou None."""
    return _ler_lei(texto[:200])


@lru_cache(maxsize=50000)
def _ler_lei(texto: str) -> tuple[str, int] | None:
    m = _LEI_NUMERADA.match(texto)
    if m and any(c.isdigit() for c in m["num"]):
        tipo = re.sub(r"[\s\-]", "", sem_acento(m["tipo"]).upper()).replace("0", "O")
        prefixo = "LC" if tipo.startswith("LEICO") or tipo == "LC" else \
            {"DECRETOLEI": "DL", "DL": "DL"}.get(tipo, "L")
        numero = int(digitos(m["num"]))
        return f"{prefixo}{numero}", m.end()

    # Nome por extenso ou sigla, do trecho mais longo (7 palavras) para o mais curto, exato ou parecido
    # ('Constituição Fedcral'). O parecido precisa ter o mesmo número de palavras do nome: OCR troca letras,
    # não acrescenta palavras, e assim 'Código Penal e' não é lido como 'Código Penal' mais um 'e'.
    palavras = list(re.finditer(r"\S+", texto[:120]))
    for k in range(min(7, len(palavras)), 0, -1):
        bruto = texto[:palavras[k - 1].end()].rstrip(".,;:)]\"'”’»")
        cand = _limpar(bruto)
        sem_ano = re.sub(r"\s*/\s*\w{2,4}$", "", cand)          # 'CPC/2015', 'CC/2002', 'CRFB/8B'
        for c in (cand, sem_ano):
            if c in APELIDOS:
                return APELIDOS[c], len(bruto)
        if len(cand) < 10:                             # semelhança só para nomes longos
            continue
        melhor = None
        for nome, cod in APELIDOS.items():
            if len(nome) < 10 or len(nome.split()) != len(cand.split()):
                continue
            sm = SequenceMatcher(None, cand, nome)
            if sm.real_quick_ratio() < 0.9 or sm.quick_ratio() < 0.9:
                continue                               # limites superiores baratos da semelhança
            r = sm.ratio()
            if r >= 0.9 and (melhor is None or r > melhor[0]):
                melhor = (r, cod)
        if melhor:
            return melhor[1], len(bruto)
    return None
