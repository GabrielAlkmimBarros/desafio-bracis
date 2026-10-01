"""Formas canônicas de texto e de números de processo, usadas tanto ao indexar a base quanto ao ler as peças."""
import re
import unicodedata

# Letras que o OCR troca por dígitos dentro de números. Como a organização garante que um dígito nunca
# vira outro dígito, toda letra no meio de um número é ruído recuperável.
_OCR_DIGITO = str.maketrans({
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "l": "1", "L": "1", "I": "1", "i": "1", "|": "1", "!": "1",
    "S": "5", "s": "5",
    "g": "9", "q": "9",
    "G": "6", "b": "6",
    "B": "8",
    "Z": "2", "z": "2",
})

_INVISIVEIS = set("​‌‍⁠﻿­")              # largura zero, BOM, hífen flexível
_TROCAS_1A1 = {**{c: "-" for c in "‐‑‒–—―−﹣－"},
               "“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "´": "'"}
_ESPECIAIS = re.compile("[" + "".join(_INVISIVEIS | set(_TROCAS_1A1)) + "\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def preparar_texto(texto: str):
    """Normaliza o texto para a detecção: NFC, sem caracteres invisíveis, hífens e aspas unificados,
    controles trocados por espaço.

    Devolve (texto_preparado, ini, fim), em que o caractere j do texto preparado veio de texto[ini[j]:fim[j]];
    os spans detectados são levados de volta às posições do arquivo original. Se não há nada a preparar,
    devolve (texto, None, None).
    """
    if not _ESPECIAIS.search(texto) and unicodedata.is_normalized("NFC", texto):
        return texto, None, None
    saida, ini, fim = [], [], []
    i, n = 0, len(texto)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(texto[j]):          # letra seguida de acentos combinantes
            j += 1
        pedaco = texto[i:j]
        if pedaco in _INVISIVEIS:
            novo = ""
        elif pedaco in _TROCAS_1A1:
            novo = _TROCAS_1A1[pedaco]
        elif len(pedaco) == 1 and (ord(pedaco) < 32 and pedaco not in "\t\n\r" or pedaco == "\x7f"):
            novo = " "
        else:
            novo = unicodedata.normalize("NFC", pedaco)
        for ch in novo:
            saida.append(ch)
            ini.append(i)
            fim.append(j)
        i = j
    return "".join(saida), ini, fim


def sem_acento(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def digitos(numero_bruto: str) -> str:
    """Corrige o OCR e devolve só os dígitos: '1.528.4S5' -> '1528455'."""
    return re.sub(r"\D", "", numero_bruto.translate(_OCR_DIGITO))


def numero_canonico(numero_bruto: str) -> str | None:
    """Forma canônica de um número de processo.

    Número simples (STF, STJ): só os dígitos, sem zeros à esquerda ('1.741.784' -> '1741784').
    Número CNJ (NNNNNNN-DD.AAAA.J.TR.OOOO): lido da direita para a esquerda, porque só o sequencial
    tem tamanho variável ('65-63.2010.5.01.0075' -> '0000065-63.2010.5.01.0075').
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
    """Dígito J do número CNJ (segmento de justiça)."""
    return numero[16]


def dv_cnj_valido(numero: str) -> bool:
    """Confere o dígito verificador do número CNJ (módulo 97, Resolução CNJ 65/2008)."""
    seq, resto = numero.split("-")
    dv, ano, j, tr, org = resto.split(".")
    return 98 - int(f"{seq}{ano}{j}{tr}{org}00") % 97 == int(dv)
