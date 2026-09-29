"""Teste de estresse: gera citações sintéticas a partir da própria base e confere o sistema.

O gabarito de desenvolvimento só cita ~80 acórdãos; o conjunto cego vai citar outros, escritos
de outros jeitos. Aqui cada registro da base é citado de vários modos e cada frase é conferida
de ponta a ponta — detecção (span com IoU >= 0,5, nada sobrando na frase) e classificação:

  A  acórdão real, 5 formatos fixos      (sigla, por extenso, sem pontuação, OCR, TST)
  A2 acórdão real, ruído de nível 2      abreviações, 'n°/No/Nº', número com espaços ou quebrado,
                                         separador de UF, OCR no número E na classe, quebra de linha
  B  acórdão inventado                   mesmo formato, número que não existe na base
  C  incompleta                          tribunal + ano + relator, sem número (vários moldes)
  D  lei e súmula                        artigos e súmulas da base (real) e fora dela (inventada)
  E  distratores                         autos do cabeçalho, OAB, fls., protocolo, valor, CPF/CNPJ:
                                         nada pode ser detectado

Resultados por frase:
    certo        -> classe certa (e, se real, o registro certo)
    ambígua      -> o número+classe existe em 2+ registros diferentes (a organização garante
                    que citações reais nunca apontam para esses; 'incompleta' é o esperado)
    falhou       -> não detectou, span errado, sobrou citação espúria ou classificou errado
    GRAVE        -> (dentro de falhou) inventada classificada como real — o erro que a métrica pune

Uso:  python scripts/estresse.py [--mostrar 20] [--secao A2]
"""
import argparse
import random
import re
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "oficial"))
from kaggle_metric import _iou                        # noqa: E402
from src.detectar import detectar                     # noqa: E402
from src.indice import Indice                         # noqa: E402
from src.normalizar import eh_cnj                     # noqa: E402
from src.resolver import resolver                     # noqa: E402
from src.vocabulario import classe_base               # noqa: E402

SIGLA_ESCRITA = {"ED": "EDcl", "EDiv": "EDv", "REspE": "REspe", "Apl": "APL",
                 "CauInomCrim": "Cautelar Inominada Criminal", "Incomp": "Incompatibilidade"}
OCR = {"0": "O", "1": "l", "5": "S", "6": "G", "9": "g", "8": "B"}

# frases de peça jurídica onde a citação é encaixada (o contexto também é testado)
MOLDURAS = [
    "Não destoa desse entendimento o {c}, de clareza solar quanto ao ponto.",
    "Confira-se, a propósito, o {c}, que trata de situação equivalente.",
    "A orientação firmada no {c} foi reafirmada em julgamentos posteriores.",
    "O acórdão recorrido diverge frontalmente do que assentado no {c}. Sob outro ângulo, a solução afronta a lei.",
    "Ao apreciar o {c}, o colegiado consolidou entendimento diametralmente oposto.",
    "Registre-se, por oportuno, o {c}, cuja fundamentação se pede vênia para transcrever.",
]


# ============================================================================ ruído

def numero_com_pontos(n: str) -> str:
    return n if "." in n else f"{int(n):,}".replace(",", ".")


def ruido_ocr(s: str, rng: random.Random, trocas: int = 1, quebra: bool = True) -> str:
    """Troca dígitos por letras parecidas e às vezes quebra a linha no meio do número.

    Sempre sobra ao menos um dígito e no máximo uma quebra de linha: é o ruído que a
    organização garante ser recuperável por normalização."""
    for _ in range(trocas):
        pos = [i for i, ch in enumerate(s) if ch in OCR]
        if pos and sum(ch.isdigit() for ch in s) > 1:
            i = rng.choice(pos)
            s = s[:i] + OCR[s[i]] + s[i + 1:]
    if quebra and len(s) > 6 and "\n" not in s and rng.random() < 0.5:
        k = rng.randrange(2, len(s) - 2)
        s = s[:k] + "\n" + s[k:]
    return s


# grafias de nível 2 para cada sigla canônica (as da aba Data e as vistas no desenvolvimento)
GRAFIAS = {
    "REsp": ["REsp", "RESP", "Rec. Esp.", "R.Esp.", "Recurso Especial", "RECURSO ESPECIAL"],
    "AREsp": ["AREsp", "ARESP", "A.REsp", "Agravo em Recurso Especial"],
    "AgInt": ["AgInt", "AGINT", "Ag. Int.", "Agravo Interno"],
    "AgRg": ["AgRg", "AGRG", "AgR", "AG.REG.", "Agravo Regimental"],
    "ED": ["EDcl", "ED", "EDs", "Embargos de Declaração"],
    "HC": ["HC", "H.C.", "Habeas Corpus"],
    "RHC": ["RHC", "Recurso em Habeas Corpus", "Recurso Ordinário em Habeas Corpus"],
    "Rcl": ["Rcl", "RCL", "Recl.", "Reclamação", "RECLAMAÇÃO"],
    "RE": ["RE", "RE.", "Recurso Extraordinário"],
    "ARE": ["ARE", "Recurso Extraordinário com Agravo"],
    "RMS": ["RMS", "Recurso em Mandado de Segurança"],
    "MS": ["MS", "Mandado de Segurança"],
    "REspE": ["REspe", "REspe.", "RESPE", "Recurso Especial Eleitoral"],
    "AREspE": ["AREspE", "AREspEl", "Agravo em Recurso Especial Eleitoral"],
    "AI": ["AI", "Agravo de Instrumento"],
    "Apl": ["APL", "Apl", "Apelação", "Apelação Criminal"],
    "RSE": ["RSE", "Recurso em Sentido Estrito"],
}
NUMERO_ORDINAL = ["nº", "n°", "Nº", "No", "n.", "N.", "nº\xa0", "n°  ", "", "n.º"]
SEP_UF = ["/{}", "/ {}", " - {}", "-{}", " ({})", " – {}", "({})"]


def ocr_em_palavras(s: str, rng: random.Random) -> str:
    """Uma confusão de OCR nas LETRAS da classe: m->rn, o->0, l->1, S->5 (a aba Data lista essas)."""
    opcoes = [(m.start(), m.group()) for m in re.finditer(r"[moOlS](?=[a-zçãA-Z])", s)]
    if not opcoes:
        return s
    i, ch = rng.choice(opcoes)
    troca = {"m": "rn", "o": "0", "O": "0", "l": "1", "S": "5"}[ch]     # rn<->m é confusão de minúscula
    return s[:i] + troca + s[i + 1:]


def numero_nivel2(num: str, rng: random.Random) -> str:
    """Número simples em um dos formatos de nível 2: 1.741.784 | 1741784 | 1 741 784 | 1.741. 784 | 1.741.-⏎784."""
    pontos = numero_com_pontos(num)
    grupos = pontos.split(".")
    forma = rng.choice(["pontos", "cru", "espacos", "ponto-espaco", "hifen-quebra", "quebra"])
    if forma == "cru" or len(grupos) == 1:
        s = num
    elif forma == "espacos":
        s = " ".join(grupos)
    elif forma == "ponto-espaco":
        k = rng.randrange(1, len(grupos))
        s = ".".join(grupos[:k]) + ". " + ".".join(grupos[k:])
    elif forma == "hifen-quebra":
        k = rng.randrange(1, len(grupos))
        s = ".".join(grupos[:k]) + rng.choice([".-\n", "-\n.", "-\n"]) + ".".join(grupos[k:])
    elif forma == "quebra":
        k = rng.randrange(1, len(grupos))
        s = ".".join(grupos[:k]) + ".\n" + ".".join(grupos[k:])
    else:
        s = pontos
    return ruido_ocr(s, rng, trocas=rng.choice([0, 1, 2])) if rng.random() < 0.5 else s


def cnj_nivel2(num: str, rng: random.Random) -> str:
    """Número CNJ em um dos formatos de nível 2 (sem pontos, com espaços, quebrado, sem zeros à esquerda)."""
    seq, resto = num.split("-")
    dv, ano, j, tr, org = resto.split(".")
    if rng.random() < 0.5:
        seq = seq.lstrip("0") or "0"
    forma = rng.choice(["padrao", "cru", "espacos", "quebra-hifen", "quebra-fim", "ponto-espaco"])
    s = {
        "padrao": f"{seq}-{dv}.{ano}.{j}.{tr}.{org}",
        "cru": f"{seq}-{dv}{ano}{j}{tr}{org}",
        "espacos": f"{seq}-{dv} {ano} {j} {tr} {org}",
        "quebra-hifen": f"{seq}-{dv}.{ano}-\n.{j}.{tr}.{org}",
        "quebra-fim": f"{seq}-{dv}.{ano}.{j}.{tr}.\n{org}",
        "ponto-espaco": f"{seq}-{dv}. {ano}.{j}.{tr}.{org}",
    }[forma]
    return ruido_ocr(s, rng) if rng.random() < 0.4 else s


def classe_nivel2(classe: tuple, tribunal: str, rng: random.Random) -> str:
    partes = [rng.choice(GRAFIAS.get(s, [SIGLA_ESCRITA.get(s, s)])) for s in classe]
    if tribunal == "TSE" and len(partes) > 1 and rng.random() < 0.5:
        s = "-".join(partes)                                   # estilo TSE: 'AgR-REspe'
    else:
        s = partes[0]
        for p in partes[1:]:
            s += rng.choice([" no ", " na ", " nos "]) + p
    if rng.random() < 0.35:
        s = ocr_em_palavras(s, rng)
    return s


def quebrar_linha(s: str, rng: random.Random) -> str:
    espacos = [m.start() for m in re.finditer(r" ", s)]
    if espacos and rng.random() < 0.35:
        i = rng.choice(espacos)
        s = s[:i] + "\n" + s[i + 1:]
    return s


# ============================================================================ geradores por seção

def formatos_fixos(f, rng):
    """Seção A: os 5 formatos originais."""
    siglas = [SIGLA_ESCRITA.get(s, s) for s in f.classe]
    uf = f"/{f.uf}" if f.uf else ""
    if f.tribunal == "TST":
        base = f"{'-'.join(siglas)}-{f.numero.lstrip('0')}"
        yield "sigla", f"processo nº TST-{base}"
        yield "sem 'processo nº'", base
        yield "ocr", f"processo nº TST-{'-'.join(siglas)}-{ruido_ocr(f.numero.lstrip('0'), rng)}"
        return
    num = numero_com_pontos(f.numero)
    yield "sigla", " no ".join(siglas) + f" nº {num}{uf}"
    if f.classe_bruta:
        yield "por extenso", f"{f.classe_bruta.title()} nº {num}{uf}"
    yield "sem pontuação", " no ".join(siglas) + f" n. {f.numero.replace('.', '').replace('-', '')}" + \
        (f" - {f.uf}" if f.uf else "")
    yield "ocr", " no ".join(siglas) + f" nº {ruido_ocr(num, rng)}" + (f" ({f.uf})" if f.uf else "")


def citacao_nivel2(classe, tribunal, numero, uf, rng):
    """Uma citação de acórdão com ruído de nível 2 combinado."""
    if tribunal == "TST":
        siglas = "-".join(SIGLA_ESCRITA.get(s, s) for s in classe)
        num = cnj_nivel2(numero, rng)
        prefixo = rng.choice(["TST-", "processo nº TST-", "Processo n° TST- ", "", "TST - "])
        return prefixo + siglas + "-" + num
    classe_txt = classe_nivel2(classe, tribunal, rng)
    num = cnj_nivel2(numero, rng) if eh_cnj(numero) else numero_nivel2(numero, rng)
    ordinal = rng.choice(NUMERO_ORDINAL)
    s = f"{classe_txt} {ordinal} {num}" if ordinal else f"{classe_txt} {num}"
    if uf and rng.random() < 0.85:
        s += rng.choice(SEP_UF).format(uf)
    return quebrar_linha(s, rng)


def formatos_nivel2(f, rng, n=3):
    """Seção A2: n variantes ruidosas por registro."""
    for _ in range(n):
        yield "nível 2 misto", citacao_nivel2(f.classe, f.tribunal, f.numero, f.uf, rng)


def numero_inexistente(f, ix, rng):
    """Um número do mesmo formato que o do registro, mas que não existe na base."""
    for _ in range(50):
        if eh_cnj(f.numero):
            seq, resto = f.numero.split("-")
            _, ano, j, tr, org = resto.split(".")
            tam = len(seq.lstrip("0")) or 1
            novo = f"{rng.randrange(10 ** (tam - 1), 10 ** tam):07d}-{rng.randrange(100):02d}.{ano}.{j}.{tr}.{org}"
        else:
            tam = len(f.numero)
            novo = str(rng.randrange(10 ** (tam - 1), 10 ** tam))
        if not ix.buscar_numero(novo):
            return novo
    return None


def formatos_inventada(f, ix, rng):
    """Seção B: a citação do registro com o número trocado por um inexistente."""
    novo = numero_inexistente(f, ix, rng)
    if not novo:
        return
    yield "inventada limpa", citacao_limpa(f.classe, f.tribunal, novo, f.uf)
    yield "inventada nível 2", citacao_nivel2(f.classe, f.tribunal, novo, f.uf, rng)


def citacao_limpa(classe, tribunal, numero, uf):
    siglas = [SIGLA_ESCRITA.get(s, s) for s in classe]
    if tribunal == "TST":
        return f"processo nº TST-{'-'.join(siglas)}-{numero.lstrip('0')}"
    num = numero if eh_cnj(numero) else numero_com_pontos(numero)
    return " no ".join(siglas) + f" nº {num}" + (f"/{uf}" if uf else "")


# ---- incompletas

_EXTENSO = {"REsp": "Recurso Especial", "AREsp": "Agravo em Recurso Especial", "HC": "Habeas Corpus",
            "RHC": "Recurso em Habeas Corpus", "Rcl": "Reclamação", "RE": "Recurso Extraordinário",
            "ARE": "Recurso Extraordinário com Agravo", "RMS": "Recurso em Mandado de Segurança",
            "REspE": "Recurso Especial Eleitoral", "Apl": "Apelação", "RSE": "Recurso em Sentido Estrito",
            "RR": "Recurso de Revista", "AIRR": "Agravo de Instrumento em Recurso de Revista"}
MOLDES_INCOMPLETA = [
    # vistos no desenvolvimento
    "julgado do {T} proferido em {A} pela relatoria de {R}",
    "precedente do {T} de {A}, da relatoria de {R}",
    "{E} do {T}, de {A}, Rel. Min. {R}",
    "{S} de {A}, Rel. Min. {R}",
    "acórdão do {T} julgado em {A} sob relatoria de {R}",
    # redações novas (a primeira é o exemplo do próprio enunciado)
    "acórdão do {T} de {A}, relatado pelo Ministro {R}",
    "julgado do {T}, de {A}, de relatoria do Min. {R}",
    "precedente do {T} julgado em {A}, Relator Ministro {R}",
    "{E} julgado pelo {T} em {A}, sob a relatoria do Ministro {R}",
    "decisão do {T} de {A}, Rel. {R}",
]


def relator_limpo(relator: str) -> str | None:
    r = re.sub(r"^(?:Min(?:istr[oa])?\.?|Des(?:embargador[a]?)?\.?)\s+", "", (relator or "").strip(), flags=re.I)
    return r if re.match(r"[A-ZÁÉÍÓÚÂÊÔÃÕÇ]", r) else None


def formatos_incompleta(f, rng):
    relator = relator_limpo(f.relator)
    base = classe_base(f.classe)
    if not (relator and f.ano and base):
        return
    molde = rng.choice(MOLDES_INCOMPLETA)
    if ("{E}" in molde and base not in _EXTENSO):
        molde = MOLDES_INCOMPLETA[0]
    nome = rng.choice([relator, relator.upper(), relator.title()])
    s = molde.format(T=f.tribunal, A=f.ano, R=nome, E=_EXTENSO.get(base, ""), S=SIGLA_ESCRITA.get(base, base))
    yield "incompleta limpa", s
    yield "incompleta nível 2", quebrar_linha(ocr_texto(s, rng), rng)


def ocr_texto(s: str, rng: random.Random) -> str:
    """Ruído de OCR em palavras comuns (e->c, i->l, a->ã), como 'profcrido', 'dc', 'Júnlor'."""
    alvos = [m.start() for m in re.finditer(r"(?<=[a-z])[eia](?=[a-z])", s)]
    if alvos:
        i = rng.choice(alvos)
        s = s[:i] + {"e": "c", "i": "l", "a": "ã"}[s[i]] + s[i + 1:]
    return s


# ---- normas

NOMES_LEI = {
    "L13105": ["CPC", "Código de Processo Civil", "CPC/2015", "Lei nº 13.105/2015", "NCPC",
               "Código de Processo Civil de 2015"],
    "L10406": ["CC", "Código Civil", "CC/2002", "Lei nº 10.406/2002", "Código Civil de 2002"],
    "DL5452": ["CLT", "Consolidação das Leis do Trabalho", "Decreto-Lei nº 5.452/1943"],
    "CF": ["CF", "CF/88", "Constituição Federal", "Constituição da República", "CRFB/88",
           "Constituição Federal de 1988", "Carta Magna"],
    "DL3689": ["CPP", "Código de Processo Penal", "Decreto-Lei nº 3.689/1941"],
    "DL1001": ["CPM", "Código Penal Militar", "Decreto-Lei nº 1.001/1969"],
    "L8078": ["CDC", "Código de Defesa do Consumidor", "Lei nº 8.078/1990", "Lei 8.078/90"],
    "L4737": ["Código Eleitoral", "Lei nº 4.737/1965"],
    "LC64": ["LC 64/90", "Lei Complementar nº 64/1990", "LC nº 64/1990", "Lei de Inelegibilidades"],
}
COMPLEMENTOS = ["", ", I", ", inciso I", ", § 1º", ", caput", ", parágrafo único", ", IX", ", I, 'g'"]
TRIB_EXTENSO = {"STJ": "Superior Tribunal de Justiça", "STF": "Supremo Tribunal Federal",
                "TST": "Tribunal Superior do Trabalho"}


def artigo_escrito(n: int, rng) -> str:
    num = f"{n}{rng.choice(['º', '°', 'o'])}" if n < 10 else numero_com_pontos(str(n))
    return f"{rng.choice(['art.', 'artigo', 'art', 'Art.'])} {num}"


def formatos_artigo(lei, n, rng):
    for nome in NOMES_LEI[lei]:
        s = f"{artigo_escrito(n, rng)}{rng.choice(COMPLEMENTOS)}, {rng.choice(['do', 'da'])} {nome}"
        yield "artigo", s.replace(", do", " do").replace(", da", " da") if rng.random() < 0.5 else s
        yield "artigo nível 2", quebrar_linha(ruido_ocr_curto(s, rng), rng)


def ruido_ocr_curto(s: str, rng) -> str:
    """OCR num dígito do número do artigo/súmula ou numa letra do nome da lei."""
    if rng.random() < 0.5:
        # só em número com 2+ dígitos: 'art. 5º' -> 'art. Sº' não deixaria dígito nenhum
        pos = [m.start() for m in re.finditer(r"(?<=\d)[0158]|[0158](?=\d)", s)]
        if pos:
            i = rng.choice(pos)
            return s[:i] + OCR[s[i]] + s[i + 1:]
    return ocr_em_palavras(s, rng)


def formatos_sumula(trib, vinc, n, rng):
    if vinc:
        formas = [f"Súmula Vinculante {n}", f"Súmula Vinculante nº {n}", f"Súmula Vinculante n. {n} do STF",
                  f"SÚMULA VINCULANTE {n}", f"Enunciado {n} da Súmula Vinculante"]
    else:
        formas = [f"Súmula {n} do {trib}", f"Súmula nº {n} do {trib}", f"Súmula n. {n}/{trib}",
                  f"Súmula {n}/{trib}", f"Súm. {n} do {trib}", f"SÚMULA {n} DO {trib}",
                  f"Súmula {n} do {TRIB_EXTENSO[trib]}", f"Enunciado {n} da Súmula do {trib}",
                  f"verbete {n} da Súmula do {trib}"]
    for s in formas:
        yield "súmula", s
        yield "súmula nível 2", quebrar_linha(ruido_ocr_curto(s, rng), rng)


# ---- distratores (números que parecem citação e não são)

def formatos_distrator(rng):
    cnj = f"{rng.randrange(10 ** 6, 10 ** 7):07d}-{rng.randrange(100):02d}.{rng.randrange(2010, 2026)}." \
          f"{rng.choice('3458')}.{rng.randrange(1, 27):02d}.{rng.randrange(10 ** 4):04d}"
    uf = rng.choice(["SP", "MG", "RJ", "PR", "BA", "GO"])
    formas = [
        f"Autos nº {cnj}", f"Processo nº {cnj}", f"Processo n° {cnj.replace('.', '')}",
        f"(OAB/{uf} {rng.randrange(10 ** 4, 10 ** 6)})", f"OAB/{uf} nº {rng.randrange(10 ** 5):,}".replace(",", "."),
        f"às fls. {rng.randrange(10, 900)}/{rng.randrange(10, 900)}", f"fls. {rng.randrange(10, 900)}-{rng.randrange(10, 900)}",
        f"Protocolo nº {rng.randrange(2015, 2026)}.{rng.randrange(10 ** 6):06d}",
        f"Valor da causa: R$ {rng.randrange(10 ** 3, 10 ** 6):,},{rng.randrange(100):02d}".replace(",", "."),
        f"CNPJ {rng.randrange(10, 99)}.{rng.randrange(100, 999)}.{rng.randrange(100, 999)}/0001-{rng.randrange(10, 99)}",
        f"CPF {rng.randrange(100, 999)}.{rng.randrange(100, 999)}.{rng.randrange(100, 999)}-{rng.randrange(10, 99)}",
        f"em {rng.randrange(1, 28)} de março de {rng.randrange(2015, 2026)}",
    ]
    for f in formas:
        yield "distrator", f
        yield "distrator com ruído", quebrar_linha(ruido_ocr(f, rng, quebra=False), rng)
    # prosa com número depois de 'e' (a conjunção não é a classe 'E' de Embargos)
    yield "distrator prosa", f"nos exercícios de {rng.randrange(2015, 2025)} e {rng.randrange(2015, 2026)}"
    yield "distrator prosa", f"conforme os itens {rng.randrange(1, 9)} e {rng.randrange(1, 9)} do contrato"


# ============================================================================ conferência

def conferir(cit: str, rng, ix):
    """Encaixa a citação numa frase, roda detector + resolvedor e devolve (resultado, citação achada)."""
    molde = rng.choice(MOLDURAS)
    antes = molde.index("{c}")
    texto = molde.format(c=cit)
    alvo = {"inicio": antes, "fim": antes + len(cit)}
    cs = detectar(texto)
    casadas = [c for c in cs if _iou(alvo, {"inicio": c.inicio, "fim": c.fim}) >= 0.5]
    sobras = [texto[c.inicio:c.fim] for c in cs if c not in casadas]
    if not casadas:
        achadas = [texto[c.inicio:c.fim] for c in cs]
        return None, f"não detectada (achou {achadas})" if achadas else "não detectada"
    r = resolver(casadas[0], ix)
    if sobras:
        return r, f"sobrou {sobras}"
    return r, ""


def julgar(r, problema, esperado, ids_ok):
    if esperado is None:                                   # distrator: nada deveria ter sido achado
        return "certo" if problema.startswith("não detectada") and "achou" not in problema else "falhou"
    if problema:
        return "falhou"
    if r["classificacao"] == esperado and (esperado != "real" or r["id_canonico"] in ids_ok):
        return "certo"
    if esperado == "real" and r["classificacao"] == "incompleta" and "ambíguo" in r["motivo"]:
        return "ambígua"
    return "falhou"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mostrar", type=int, default=15)
    ap.add_argument("--secao", choices=["A", "A2", "B", "C", "D", "E"], help="rodar só uma seção")
    args = ap.parse_args()
    # um gerador por seção: rodar só uma seção (--secao) reproduz exatamente as mesmas frases
    rngs = {s: random.Random(s) for s in ("A", "A2", "B", "C", "D", "E")}
    ix = Indice(str(RAIZ / "data" / "desafio1_bracis.db"))
    placar: dict[tuple, Counter] = {}
    falhas = []

    def registrar(secao, nome, cit, esperado, ids_ok):
        r, problema = conferir(cit, rngs[secao], ix)
        res = julgar(r, problema, esperado, ids_ok)
        cont = placar.setdefault((secao, nome), Counter())
        cont[res] += 1
        if res == "falhou":
            grave = esperado == "inventada" and r is not None and r["classificacao"] == "real"
            if grave:
                cont["GRAVE"] += 1
            detalhe = problema or (f"{r['classificacao']} | {r['motivo']}" if r else "")
            falhas.append((secao, nome, cit, ("GRAVE " if grave else "") + detalhe))

    acordaos = [f for f in ix.fichas if f.natureza == "acordao" and f.numero and f.classe]
    quer = (lambda s: args.secao in (None, s))
    for f in acordaos:
        mesmo_texto = {g.id for g in ix.fichas if g.assinatura == f.assinatura}
        if quer("A"):
            for nome, cit in formatos_fixos(f, rngs["A"]):
                registrar("A", nome, cit, "real", mesmo_texto)
        if quer("A2"):
            for nome, cit in formatos_nivel2(f, rngs["A2"]):
                registrar("A2", nome, cit, "real", mesmo_texto)
        if quer("B"):
            for nome, cit in formatos_inventada(f, ix, rngs["B"]):
                registrar("B", nome, cit, "inventada", set())
        if quer("C"):
            for nome, cit in formatos_incompleta(f, rngs["C"]):
                registrar("C", nome, cit, "incompleta", set())
    if quer("D"):
        for chave, f in sorted(ix.por_norma.items(), key=str):
            rng = rngs["D"]
            if chave[0] == "artigo":
                _, lei, n = chave
                for nome, cit in formatos_artigo(lei, n, rng):
                    registrar("D", nome + " real", cit, "real", {f.id})
                existentes = {k[2] for k in ix.por_norma if k[0] == "artigo" and k[1] == lei}
                falso = rng.choice([x for x in range(2, 400) if x not in existentes])
                for nome, cit in formatos_artigo(lei, falso, rng):
                    registrar("D", nome + " inventado", cit, "inventada", set())
            else:
                _, trib, vinc, n = chave
                for nome, cit in formatos_sumula(trib, vinc, n, rng):
                    registrar("D", nome + " real", cit, "real", {f.id})
                existentes = {k[3] for k in ix.por_norma if k[0] == "sumula" and k[1] == trib}
                falso = rng.choice([x for x in range(100, 999) if x not in existentes])
                for nome, cit in formatos_sumula(trib, vinc, falso, rng):
                    registrar("D", nome + " inventada", cit, "inventada", set())

    if quer("E"):
        for _ in range(100):
            for nome, cit in formatos_distrator(rngs["E"]):
                registrar("E", nome, cit, None, set())

    print(f"{'seção':<6}{'formato':<28}{'certo':>7}{'ambígua':>9}{'falhou':>8}{'GRAVE':>7}")
    total_falhas = 0
    for (secao, nome), c in placar.items():
        total_falhas += c["falhou"]
        print(f"{secao:<6}{nome:<28}{c['certo']:>7}{c['ambígua']:>9}{c['falhou']:>8}{c['GRAVE']:>7}")
    print(f"\ntotal de falhas: {total_falhas}")
    if falhas:
        print(f"\nprimeiras {args.mostrar} falhas:")
        for x in falhas[:args.mostrar]:
            print("  ", x)
    sys.exit(1 if total_falhas else 0)


if __name__ == "__main__":
    main()
