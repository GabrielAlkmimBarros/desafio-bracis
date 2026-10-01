"""Testes metamórficos: transformações das 26 peças que não podem mudar a resposta (reflow, caixa alta, nbsp...).

Uso:  python scripts/metamorfico.py [--transformacao X] [--prosa PASTA_COM_TXT]
"""
import argparse
import random
import re
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "oficial"))
sys.path.insert(0, str(RAIZ / "scripts"))
import kaggle_metric as km                                    # noqa: E402
from avaliar import carregar_gabarito, gabarito_para_solution  # noqa: E402
from json_to_submission import encode                         # noqa: E402
from src.indice import Indice                                 # noqa: E402
from src.run import encontrar_citacoes, ler_texto, montar_json  # noqa: E402


class Editor:
    """Monta o texto novo pedaço a pedaço e guarda o mapa posição antiga -> posição nova."""

    def __init__(self, texto):
        self.texto, self.saida, self.mapa = texto, [], {}

    def copiar(self, ini, fim, trocar=None):
        for i in range(ini, fim):
            self.mapa[i] = sum(len(x) for x in self.saida)
            self.saida.append(trocar(self.texto[i]) if trocar else self.texto[i])

    def inserir(self, s):
        self.saida.append(s)

    def fechar(self):
        self.mapa[len(self.texto)] = sum(len(x) for x in self.saida)
        return "".join(self.saida), self.mapa


def aplicar(texto, spans, regra):
    """regra(i, ch, dentro_de_citacao, especie) -> string que substitui o caractere i (pode inserir)."""
    dentro = {}
    for ini, fim, esp in spans:
        for i in range(ini, fim):
            dentro[i] = esp
    ed = Editor(texto)
    for i, ch in enumerate(texto):
        ed.mapa[i] = sum(len(x) for x in ed.saida)
        ed.saida.append(regra(i, ch, i in dentro, dentro.get(i)))
    return ed.fechar()


def transformar(nome, texto, gold, rng, prosa):
    """Devolve (texto_novo, mapa, regioes_ignoradas)."""
    spans = [(r.inicio, r.fim, "acordao" if re.search(r"\d{3}", r.trecho) and r.tipo == "jurisprudencia"
              and not re.match(r"(?i)s[úu]m|5úm|tem", r.trecho) else r.tipo) for r in gold.itertuples()]
    fins = {f: e for _, f, e in spans}
    inis = {i: e for i, _, e in spans}
    if nome == "reflow":
        plano = re.sub(r"(?<!\n)\n(?!\n)", " ", texto)           # desfaz as quebras simples...
        largura = rng.choice([55, 70, 95])                        # ...e quebra em outra largura
        out, col = [], 0
        for ch in plano:
            if ch == " " and col >= largura:
                out.append("\n")
                col = 0
                continue
            out.append(ch)
            col = 0 if ch == "\n" else col + 1
        novo = "".join(out)
        return novo, {i: i for i in range(len(texto) + 1)}, []      # mesmo tamanho: mapa identidade
    if nome == "espacos":
        def regra(i, ch, dentro, esp):
            # dentro da citação, no máximo uma quebra seguida: linha em branco abriria outro parágrafo
            vizinho_quebra = "\n" in texto[max(0, i - 2):i] + texto[i + 1:i + 3]
            if ch == " ":
                return rng.choice([" ", "  ", " "] if dentro and vizinho_quebra else [" ", "  ", "\n", " "])
            if ch == "\n":
                return rng.choice([" ", "\n", "\n"])
            return ch
        return (*aplicar(texto, spans, regra), [])
    if nome == "caixa_alta":
        # letras de OCR coladas a dígitos ('1.45g.779', '21737l8') são glifos de número, não letras:
        # em maiúscula mudariam de identidade ('g' = 9, 'G' = 6), e a transformação deixaria de preservar o sentido
        novo = re.sub(r"[^\W\d_]+", lambda m: m.group() if re.search(r"\d", texto[max(0, m.start() - 1):m.end() + 1])
                      and len(m.group()) <= 2 else m.group().upper(), texto)
        return novo, {i: i for i in range(len(texto) + 1)}, []
    if nome == "nbsp":
        return (*aplicar(texto, spans, lambda i, ch, d, e: "\xa0" if d and ch == " " else ch), [])
    if nome == "rodape":
        cauda = ", relator Ministro Herman Benjamin, Terceira Turma, julgado em 12/3/2024, DJe de 20/3/2024"
        return (*aplicar(texto, spans, lambda i, ch, d, e: (cauda + ch) if fins.get(i) == "acordao" else ch), [])
    if nome == "parenteses":
        def regra(i, ch, d, e):
            pre = "(STJ, " if inis.get(i) == "acordao" else ""
            pos = ")" if fins.get(i) == "acordao" else ""
            return pos + pre + ch
        return (*aplicar(texto, spans, regra), [])
    if nome == "hifenizacao":
        partir = {}
        for m in re.finditer(r"[a-záéíóúâêôãõç]{10,}", texto):
            if not any(a <= m.start() < b for a, b, _ in spans) and rng.random() < 0.3:
                partir[m.start() + len(m.group()) // 2] = True
        return (*aplicar(texto, spans, lambda i, ch, d, e: ("-\n" + ch) if i in partir else ch), [])
    if nome == "prosa_externa":
        ed = Editor(texto)
        ignorar, pos = [], 0
        for m in re.finditer(r"\n\n", texto):
            ed.copiar(pos, m.end())
            bloco = rng.choice(prosa) + "\n\n"
            ini = sum(len(x) for x in ed.saida)
            ed.inserir(bloco)
            ignorar.append((ini, ini + len(bloco)))
            pos = m.end()
        ed.copiar(pos, len(texto))
        return (*ed.fechar(), ignorar)
    raise ValueError(nome)


TRANSFORMACOES = ["reflow", "espacos", "caixa_alta", "rodape", "parenteses", "nbsp", "hifenizacao", "prosa_externa"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prosa", type=Path, help="pasta com .txt de decisões reais (só para desenvolvimento)")
    ap.add_argument("--transformacao", choices=TRANSFORMACOES)
    args = ap.parse_args()
    prosa = []
    if args.prosa:
        for p in sorted(args.prosa.glob("*.txt"))[:300]:
            t = re.sub(r"<[^>]+>", "\n", p.read_text(encoding="utf-8", errors="replace"))
            prosa += [b.strip() for b in re.split(r"\n\s*\n|\n", t) if 200 < len(b.strip()) < 1200]
    g = carregar_gabarito()
    ix = Indice(str(RAIZ / "data" / "desafio1_bracis.db"))
    falhou = False
    por_situacao = {}
    print(f"{'transformação':<16}{'nota':>8}{'macroF1 N1':>12}{'macroF1 N2':>12}{'detecções fora do gabarito':>28}")
    for nome in TRANSFORMACOES:
        if (args.transformacao and nome != args.transformacao) or (nome == "prosa_externa" and not prosa):
            continue
        rng = random.Random(nome)
        linhas, gt, extras = [], [], []
        for arq in sorted((RAIZ / "data" / "txt").glob("*.txt")):
            texto = ler_texto(arq)
            gold = g[g["documento_id"] == arq.stem]
            novo, mapa, ignorar = transformar(nome, texto, gold, rng, prosa)
            gnovo = gold.copy()
            gnovo["inicio"] = [mapa[i] for i in gold["inicio"]]
            gnovo["fim"] = [mapa[f - 1] + 1 for f in gold["fim"]]
            cits = [c for c in encontrar_citacoes(novo, ix)
                    if not any(a <= c["inicio"] < b for a, b in ignorar)]
            doc = montar_json(arq.stem, novo, cits)
            linhas.append({"documento_id": arq.stem, "citacoes": encode(doc)})
            gt.append(gnovo)
            golds = list(zip(gnovo["inicio"], gnovo["fim"]))
            gds = [dict(inicio=r.inicio, fim=r.fim, classe=r.classificacao, id=r.id_canonico) for r in gnovo.itertuples()]
            for gi, pi in km._casar(gds, cits)[0]:
                gd, c = gds[gi], cits[pi]
                ok = c["classificacao"] == gd["classe"] and (gd["classe"] != "real" or str(c["id_canonico"]) == gd["id"])
                sit = c["motivo"][1:c["motivo"].index("]")]
                por_situacao.setdefault(sit, [0, 0])[0] += 1
                por_situacao[sit][1] += not ok
            extras += [(arq.stem, novo[c["inicio"]:c["fim"]]) for c in cits
                       if not any(km._iou({"inicio": a, "fim": b}, c) >= 0.5 for a, b in golds)]
        res = km.avaliar(gabarito_para_solution(pd.concat(gt)), pd.DataFrame(linhas))
        m1, m2 = res["niveis"][1]["macro_f1"], res["niveis"][2]["macro_f1"]
        print(f"{nome:<16}{res['score_final']:>8.4f}{m1:>12.4f}{m2:>12.4f}{len(extras):>28}")
        for doc, t in extras[:6]:
            print(f"      extra: {doc} {t!r}")
        falhou |= min(m1, m2) < 1.0
    print(f"\n{'situação (citações casadas, todas as transformações)':<56}{'n':>7}{'erros':>7}")
    for sit, (n, e) in sorted(por_situacao.items()):
        print(f"{sit:<56}{n:>7}{e:>7}")
    sys.exit(1 if falhou else 0)


if __name__ == "__main__":
    main()
