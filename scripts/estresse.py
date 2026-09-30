"""Teste de estresse: cria citações sintéticas, com redações que NÃO estão no gabarito, e confere
se o sistema detecta e classifica cada uma direito.

O gabarito de desenvolvimento cita só ~100 registros e usa poucas redações; o conjunto cego vai
citar outros registros, possivelmente de outros jeitos. Seções:

  acordaos     cada um dos 996 acórdãos da base, em 5 formatos (sigla, extenso, sem pontuação, OCR...)
  normas       as 5 súmulas e os 13 artigos, em várias redações + versões inexistentes (inventadas)
  incompletas  referências sem número (tribunal + ano + relator) em redações variadas
  inventadas   números aleatórios que não existem na base
  enumeracoes  'AgInt no AREsp nº 1/RJ e 2/SP': o 2º número, sem classe, tem de herdar a do 1º
  falsos       frases com números que NÃO são citação (autos, OAB, fls., valores, datas, 'fls. 10 e 11'...)

Resultados por formato:
  certo    -> detectou (sobreposição >= 0,5 com o trecho) e classificou certo (com o id certo, se real)
  ambígua  -> (só acórdãos) número+classe em 2+ registros diferentes: 'incompleta' é o esperado
  falhou   -> não detectou, ou classificou errado  <- é isso que precisa ir a zero

Uso:  python scripts/estresse.py [--secao acordaos|normas|incompletas|inventadas] [--mostrar 20]
"""
import argparse
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from src.detectar import detectar         # noqa: E402
from src.indice import Indice             # noqa: E402
from src.resolver import resolver         # noqa: E402

SIGLA_ESCRITA = {"ED": "EDcl", "EDiv": "EDv", "REspE": "REspe", "Apl": "APL",
                 "CauInomCrim": "Cautelar Inominada Criminal", "Incomp": "Incompatibilidade"}
OCR = {"0": "O", "1": "l", "5": "S", "6": "G", "9": "g", "8": "B"}
TRIB_EXTENSO = {"STF": "Supremo Tribunal Federal", "STJ": "Superior Tribunal de Justiça",
                "TST": "Tribunal Superior do Trabalho", "TSE": "Tribunal Superior Eleitoral",
                "STM": "Superior Tribunal Militar"}


def numero_com_pontos(n: str) -> str:
    return n if "." in n else f"{int(n):,}".replace(",", ".")


def ruido_ocr(s: str, rng: random.Random) -> str:
    """Troca UM dígito por uma letra parecida e às vezes quebra a linha no meio do número."""
    pos = [i for i, ch in enumerate(s) if ch in OCR]
    if pos:
        i = rng.choice(pos)
        s = s[:i] + OCR[s[i]] + s[i + 1:]
    if len(s) > 6 and rng.random() < 0.5:
        k = rng.randrange(2, len(s) - 2)
        s = s[:k] + "\n" + s[k:]
    return s


# ============================================================================ motor comum

def conferir(ix, citacao, classe_esperada, ids_esperados=None, antes="Não destoa desse entendimento o ",
             depois=", de clareza solar quanto ao ponto."):
    """Monta uma frase com a citação, roda detector+resolvedor e diz se deu certo."""
    texto = antes + citacao + depois
    ini, fim = len(antes), len(antes) + len(citacao)
    melhor, melhor_iou = None, 0.0
    for c in detectar(texto):
        inter = max(0, min(fim, c.fim) - max(ini, c.inicio))
        iou = inter / ((fim - ini) + (c.fim - c.inicio) - inter) if inter else 0.0
        if iou > melhor_iou:
            melhor, melhor_iou = c, iou
    if melhor is None or melhor_iou < 0.5:
        return "falhou", f"não detectada (melhor IoU {melhor_iou:.2f})"
    r = resolver(melhor, ix)
    if r["classificacao"] == classe_esperada and (ids_esperados is None or r["id_canonico"] in ids_esperados):
        return "certo", ""
    if classe_esperada == "real" and r["classificacao"] == "incompleta" and "ambíguo" in r["motivo"]:
        return "ambígua", ""
    return "falhou", f"{r['classificacao']} {r['id_canonico']} | {r['motivo']}"


# ============================================================================ acórdãos

def casos_acordaos(ix, rng):
    for f in ix.fichas:
        if f.natureza != "acordao" or not f.numero or not f.classe:
            continue
        ids = {g.id for g in ix.fichas if g.assinatura == f.assinatura}
        siglas = [SIGLA_ESCRITA.get(s, s) for s in f.classe]
        uf = f"/{f.uf}" if f.uf else ""
        if f.tribunal == "TST":
            base = f"{'-'.join(siglas)}-{f.numero.lstrip('0')}"
            yield "sigla", f"processo nº TST-{base}", "real", ids
            yield "sem 'processo nº'", base, "real", ids
            yield "ocr", f"processo nº TST-{'-'.join(siglas)}-{ruido_ocr(f.numero.lstrip('0'), rng)}", "real", ids
            continue
        num = numero_com_pontos(f.numero)
        yield "sigla", " no ".join(siglas) + f" nº {num}{uf}", "real", ids
        if f.classe_bruta:
            yield "por extenso", f"{f.classe_bruta.title()} nº {num}{uf}", "real", ids
        yield "sem pontuação", " no ".join(siglas) + f" n. {f.numero.replace('.', '').replace('-', '')}" + \
            (f" - {f.uf}" if f.uf else ""), "real", ids
        yield "ocr", " no ".join(siglas) + f" nº {ruido_ocr(num, rng)}" + (f" ({f.uf})" if f.uf else ""), "real", ids


# ============================================================================ súmulas e artigos

LEIS_ESCRITAS = {   # código -> jeitos de escrever a lei
    "L13105": ["CPC", "Código de Processo Civil", "Lei nº 13.105/2015", "Lei 13.105/15", "CPC/2015"],
    "L10406": ["CC", "Código Civil", "Lei nº 10.406/2002", "CC/2002"],
    "DL5452": ["CLT", "Consolidação das Leis do Trabalho", "Decreto-Lei nº 5.452/1943"],
    "CF": ["CF", "Constituição Federal", "Constituição da República", "CF/88", "Constituição Federal de 1988"],
    "DL3689": ["CPP", "Código de Processo Penal", "Decreto-Lei nº 3.689/1941"],
    "DL1001": ["CPM", "Código Penal Militar", "Decreto-Lei nº 1.001/1969"],
    "L8078": ["CDC", "Código de Defesa do Consumidor", "Lei nº 8.078/1990"],
    "L4737": ["Código Eleitoral", "Lei nº 4.737/1965"],
    "LC64": ["LC 64/90", "Lei Complementar nº 64/1990", "Lei Complementar 64/90"],
}
COMPLEMENTOS = ["", ", I", ", inciso II", ", § 1º", ", caput", ", parágrafo único"]


def _artigo(n):
    return f"{n}º" if n < 10 else numero_com_pontos(str(n))


def casos_normas(ix, rng):
    for chave, f in ix.por_norma.items():
        if chave[0] == "sumula":
            _, trib, vinc, n = chave
            if vinc:
                formas = [f"Súmula Vinculante {n}", f"Súmula Vinculante nº {n}", f"Súmula Vinculante {n} do STF",
                          f"Súmula Vinculante n. {n}/STF", f"SV {n}", f"súmula vinculante {n}"]
                falsas = [f"Súmula Vinculante {n + 90}", f"Súmula Vinculante nº {n + 91} do STF"]
            else:
                ext = TRIB_EXTENSO[trib]
                formas = [f"Súmula {n} do {trib}", f"Súmula nº {n} do {trib}", f"Súmula n. {n}/{trib}",
                          f"Súmula {n}/{trib}", f"Súm. {n} do {trib}", f"Súmula {n} do {ext}",
                          f"súmula {n} do {trib}", f"5úmula {n} do {trib}", f"Súmula {n}, I, do {trib}",
                          f"Súmula nº {n}, item I, do {trib}", f"Enunciado nº {n} da Súmula do {trib}",
                          f"verbete nº {n} da Súmula do {trib}"]
                falsas = [f"Súmula {n + 1000} do {trib}", f"Súmula nº {n + 1001}/{trib}"]
            for forma in formas:
                yield "súmula real", forma, "real", {f.id}
            for forma in falsas:
                yield "súmula inventada", forma, "inventada", None
        else:
            _, lei, n = chave
            for escrita in LEIS_ESCRITAS.get(lei, []):
                comp = rng.choice(COMPLEMENTOS)
                prefixo = rng.choice(["art.", "artigo", "Art.", "art"])
                yield "artigo real", f"{prefixo} {_artigo(n)}{comp}, da {escrita}" if escrita[0] in "CL" and \
                    escrita.startswith(("Constitui", "Consolida", "Lei")) else \
                    f"{prefixo} {_artigo(n)}{comp}, do {escrita}", "real", {f.id}
            escrita = rng.choice(LEIS_ESCRITAS.get(lei, ["CPC"]))
            artigo_falso = n + 777
            yield "artigo inventado", f"art. {_artigo(artigo_falso)} " + \
                ("da " if escrita.startswith(("Constitui", "Consolida", "Lei")) else "do ") + escrita, "inventada", None
    # lei que existe no mundo mas não tem nenhum artigo na base
    for cit in ["art. 172 da Lei nº 9.504/1997", "art. 60 da Lei nº 13.467/2017", "art. 927 do Código Civil",
                "art. 121 do Código Penal", "art. 3º do CTN"]:
        yield "artigo inventado", cit, "inventada", None


# ============================================================================ incompletas

MODELOS_INCOMPLETA = {
    # redações que aparecem no gabarito
    "gabarito: julgado ... pela relatoria": "julgado do {T} proferido em {A} pela relatoria de {R}",
    "gabarito: precedente ... da relatoria": "precedente do {T} de {A}, da relatoria de {R}",
    "gabarito: Classe do T, de A, Rel. Min.": "{C} do {T}, de {A}, Rel. Min. {R}",
    "gabarito: acórdão ... sob relatoria": "acórdão do {T} julgado em {A} sob relatoria de {R}",
    "gabarito: Sigla de A, Rel. Min.": "{S} de {A}, Rel. Min. {R}",
    # redações novas
    "nova: relator antes do ano": "acórdão do {T}, de relatoria do Ministro {R}, julgado em {A}",
    "nova: relatado pelo": "precedente do {T} de {A}, relatado pelo Ministro {R}",
    "nova: entre parênteses": "julgado do {T} de {A} (Rel. Min. {R})",
    "nova: tribunal por extenso": "precedente do {TE} de {A}, da relatoria de {R}",
    "nova: sob a relatoria da Ministra": "decisão do {T} proferida em {A}, sob a relatoria da Ministra {R}",
}
CLASSE_POR_TRIB = {"STF": ("Reclamação", "Rcl"), "STJ": ("Recurso Especial", "REsp"),
                   "TSE": ("Recurso Especial Eleitoral", "REspe"), "TST": ("Recurso de Revista", "RR"),
                   "STM": ("Apelação", "APL")}


def _nome_relator(r):
    r = re.sub(r"^(?:Min\.|Ministr[oa]|Desembargador[a]?)\s+", "", r.strip(), flags=re.I)
    return r.title() if r.isupper() else r


def casos_incompletas(ix, rng):
    grupos = defaultdict(list)
    for f in ix.fichas:
        if f.natureza == "acordao" and f.relator and f.ano:
            grupos[(f.tribunal, f.ano, _nome_relator(f.relator))].append(f)
    combos = [k for k, v in grupos.items() if len(v) >= 2]
    rng.shuffle(combos)
    for trib, ano, rel in combos[:40]:
        cext, sig = CLASSE_POR_TRIB[trib]
        for nome, modelo in MODELOS_INCOMPLETA.items():
            cit = modelo.format(T=trib, TE=TRIB_EXTENSO[trib], A=ano, R=rel, C=cext, S=sig)
            yield nome, cit, "incompleta", None


# ============================================================================ inventadas

def casos_inventadas(ix, rng):
    def livre(n):
        return not ix.buscar_numero(n)
    for _ in range(60):
        n = str(rng.randint(1_000_000, 2_300_000))
        if livre(n):
            yield "REsp aleatório", f"REsp nº {numero_com_pontos(n)}/SP", "inventada", None
        n = str(rng.randint(30_000, 90_000))
        if livre(n):
            yield "Rcl aleatória", f"Rcl {numero_com_pontos(n)}/DF", "inventada", None
        seq = rng.randint(7_000_000, 7_001_500)
        cnj = f"{seq}-{rng.randint(10, 99)}.{rng.randint(2018, 2026)}.7.00.0000"
        if livre(cnj):
            yield "STM aleatório", f"APL nº {cnj}/RJ", "inventada", None
        cnj = f"{rng.randint(1, 99999)}-{rng.randint(10, 99)}.{rng.randint(2008, 2020)}.5.{rng.randint(1, 24):02d}.{rng.randint(1, 999):04d}"
        if livre(re.sub(r"^(\d+)", lambda m: f"{int(m[1]):07d}", cnj)):
            yield "TST aleatório", f"RR-{cnj}", "inventada", None


# ============================================================================ enumerações

def casos_enumeracoes(ix, rng):
    """'AgInt no AREsp nº 1.996.496/RJ e 1.996.497/SP': o 2º número não repete a classe e herda a do 1º.
    Pares de mesmo tribunal e classe-base, porque o 'e' liga processos da mesma espécie."""
    grupos = defaultdict(list)
    for f in ix.fichas:
        if f.natureza == "acordao" and f.numero and f.classe and "." not in f.numero \
                and len(f.numero) >= 4 and len(ix.buscar_numero(f.numero)) == 1:
            grupos[(f.tribunal, f.classe[-1])].append(f)
    for fichas in grupos.values():
        for a, b in zip(fichas[::2], fichas[1::2]):
            siglas = [SIGLA_ESCRITA.get(s, s) for s in a.classe]
            primeira = " no ".join(siglas) + f" nº {numero_com_pontos(a.numero)}" + (f"/{a.uf}" if a.uf else "")
            segunda = numero_com_pontos(b.numero) + (f"/{b.uf}" if b.uf else "")
            ids = {g.id for g in ix.fichas if g.assinatura == b.assinatura}
            yield "2º número sem classe", segunda, "real", ids, f"Nesse sentido, {primeira} e "


# ============================================================================ falsos positivos

FRASES_SEM_CITACAO = [
    "Processo nº 6706918-56.2019.4.17.3043",
    "Autos nº 9426435-30.2024.8.08.5965",
    "Valor da causa: R$ 111.452,72",
    "por seu advogado que esta subscreve (OAB/BA 349745), vem, respeitosamente",
    "Contrarrazões apresentadas às fls. 790/829. Vieram os autos conclusos.",
    "Protocolo nº 2022.3657393",
    "Memorial nº 255/2021",
    "honorários advocatícios fixados em 10% sobre o valor atualizado da causa",
    "firmado em 3 de janeiro de 2025, por meio do qual a contratada se obrigou",
    "nos termos da Lei nº 13.467/2017, que alterou a CLT",
    "conforme o entendimento sumulado sobre a matéria",
    "Registre-se que o tema comporta enfrentamento sob dupla perspectiva.",
    "a jurisprudência pacífica desta Corte e os precedentes desta Casa",
    "o dispositivo constitucional invocado na origem, de aplicação cogente",
    "Sessão virtual de 20/06/2022 a 24/06/2022, julgamento unânime.",
    "o laudo pericial de fls. 591/977 concluiu pela materialidade",
    "Enunciado 5 da I Jornada de Direito Civil",
    "Resolução TSE nº 23.610/2019",
    "a decisão de 2019, proferida pelo juízo de origem, foi mantida",
    "o recurso foi interposto em 2021 pelo Ministério Público",
    "CEP 01310-100, telefone (11) 3333-4444",
    # 'e' + número: a conjunção não é a classe 'Embargos'
    "o pedido de fls. 10 e 11 foi indeferido",
    "condenação ao pagamento de R$ 1.000 e 2.000 reais a título de multa",
    "os artigos 5 e 7 tratam da matéria",
    "nos prazos de 5 e 15 dias, respectivamente",
    "FLS. 10 E 11 DOS AUTOS",
]


def casos_falsos(ix, rng):
    for frase in FRASES_SEM_CITACAO:
        yield "frase sem citação", frase, None, None


SECOES = {"acordaos": casos_acordaos, "normas": casos_normas,
          "incompletas": casos_incompletas, "inventadas": casos_inventadas,
          "enumeracoes": casos_enumeracoes, "falsos": casos_falsos}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secao", choices=list(SECOES), help="rodar só uma seção")
    ap.add_argument("--mostrar", type=int, default=15)
    args = ap.parse_args()
    ix = Indice(str(RAIZ / "data" / "desafio1_bracis.db"))
    total_falhas = 0
    for nome_secao, gerador in SECOES.items():
        if args.secao and args.secao != nome_secao:
            continue
        rng = random.Random(0)
        por_formato, falhas = {}, []
        for formato, cit, esperado, ids, *contexto in gerador(ix, rng):   # contexto: 'antes' opcional
            if esperado is None:                       # não pode detectar NADA na frase
                achadas = detectar(cit)
                res, motivo = ("certo", "") if not achadas else \
                    ("falhou", f"detectou {[(cit[c.inicio:c.fim], c.especie) for c in achadas]}")
            else:
                res, motivo = conferir(ix, cit, esperado, ids, *contexto)
            por_formato.setdefault(formato, Counter())[res] += 1
            if res == "falhou":
                falhas.append((formato, cit, motivo))
        print(f"\n=== {nome_secao.upper()}")
        print(f"{'formato':<42}{'certo':>7}{'ambígua':>9}{'falhou':>8}")
        for formato, c in por_formato.items():
            print(f"{formato:<42}{c['certo']:>7}{c['ambígua'] or '':>9}{c['falhou']:>8}")
        for x in falhas[:args.mostrar]:
            print("   FALHA", x)
        total_falhas += len(falhas)
    print(f"\nTOTAL DE FALHAS: {total_falhas}")


if __name__ == "__main__":
    main()
