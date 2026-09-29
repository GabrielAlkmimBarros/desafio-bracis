"""Normalização: levar variantes de escrita a UMA forma canônica.

A mesma função é usada dos dois lados — ao indexar a base e ao ler a citação —
porque só assim "1.741.784", "1741784" e "1 741 784" viram a mesma chave.
"""
import re
import unicodedata

# Confusões de OCR que o nível 2 aplica DENTRO de números. A organização garante
# que um dígito nunca vira outro dígito, então toda letra no meio de um número
# é ruído recuperável.
_OCR_DIGITO = str.maketrans({
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "l": "1", "I": "1", "i": "1", "|": "1", "!": "1",
    "S": "5", "s": "5",
    "g": "9", "q": "9",
    "G": "6", "b": "6",
    "B": "8",
    "Z": "2", "z": "2",
})


def sem_acento(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def digitos(numero_bruto: str) -> str:
    """Corrige OCR e devolve só os dígitos: '1.528.4S5' -> '1528455'."""
    return re.sub(r"\D", "", numero_bruto.translate(_OCR_DIGITO))


def numero_canonico(numero_bruto: str) -> str | None:
    """Forma canônica de um número de processo.

    - Número simples (STF/STJ): sem pontuação e sem zeros à esquerda.
          '1.741.784' | '1741784' | '1 741 784' -> '1741784'
    - Número CNJ (NNNNNNN-DD.AAAA.J.TR.OOOO), usado por TST, TSE e STM: lido da
      direita para a esquerda, porque só o sequencial tem tamanho variável.
          '65-63.2010.5.01.0075'        -> '0000065-63.2010.5.01.0075'
          '7000171-3920237000000'       -> '7000171-39.2023.7.00.0000'
          '533-80. 2012.6.13.0029'      -> '0000533-80.2012.6.13.0029'
    """
    d = digitos(numero_bruto)
    if not d:
        return None
    if len(d) >= 14:                       # 1+2+4+1+2+4: o menor CNJ possível
        seq, dv, ano, j, tr, org = d[:-13], d[-13:-11], d[-11:-7], d[-7], d[-6:-4], d[-4:]
        if len(seq) > 7:
            return None
        return f"{int(seq):07d}-{dv}.{ano}.{j}.{tr}.{org}"
    return str(int(d))


def eh_cnj(numero: str | None) -> bool:
    return bool(numero) and "." in numero


def justica_cnj(numero: str) -> str:
    """Dígito J do CNJ: 5 = Trabalho (TST), 6 = Eleitoral (TSE), 7 = Militar (STM)."""
    return numero[16]


def dv_cnj_valido(numero: str) -> bool:
    """Confere o dígito verificador do número CNJ (módulo 97, Resolução CNJ 65/2008).

    Útil para detectar número quebrado por OCR no cabeçalho de um registro.
    """
    seq, resto = numero.split("-")
    dv, ano, j, tr, org = resto.split(".")
    return 98 - int(f"{seq}{ano}{j}{tr}{org}00") % 97 == int(dv)


# ----------------------------------------------------------------------------- extrair número de um trecho

_LETRAS_OCR = set("OoQDlIi|!SsgqGbBZz")


def _tipo_pedaco(p: str):
    miolo = re.sub(r"[.\-–—]", "", p)
    if not miolo:
        return "ligacao"                      # um '-' ou '.' solto entre dois grupos de dígitos
    if any(c.isdigit() for c in miolo) and all(c.isdigit() or c in _LETRAS_OCR for c in miolo):
        return "numero"
    return None


def extrair_numero(trecho: str) -> str | None:
    """Acha o número de processo dentro de um trecho de citação e devolve a forma canônica.

        'AgRg no Rec. Esp. n. 1.522.200 (SC)'            -> '1522200'
        'AgInt no RESP 21737l8 - SP'                      -> '2173718'   (l -> 1)
        'TST-ED-E-ED-ARR-1099-66.2011.5.02.\\n0251'       -> '0001099-66.2011.5.02.0251'
        'REspe. n° 0600216-46.2020- .6.14.0022'           -> '0600216-46.2020.6.14.0022'

    Letras só são lidas como dígito quando o pedaço inteiro é "quase número"
    (tem dígito e só tem dígitos ou letras típicas de OCR) — assim 'SP' continua 'SP'.
    """
    t = re.sub(r"(?<=[A-Za-z])-(?=\d)", " ", trecho)      # 'ARR-1099'  -> 'ARR 1099'
    t = re.sub(r"-(?=[A-Za-z]{2}\b)", " ", t)              # '1.632.479-RJ' -> '1.632.479 RJ'
    corridas, atual = [], []
    for p in re.split(r"[\s/(),;:]+", t):
        if not p:
            continue
        tipo = _tipo_pedaco(p)
        if tipo == "numero" or (tipo == "ligacao" and atual):
            atual.append(p)
        elif atual:
            corridas.append(atual)
            atual = []
    if atual:
        corridas.append(atual)
    if not corridas:
        return None
    melhor = max(corridas, key=lambda c: len(digitos("".join(c))))
    return numero_canonico("".join(melhor))
