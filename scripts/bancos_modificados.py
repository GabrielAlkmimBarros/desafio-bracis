"""Prova de que o sistema não depende do banco de desenvolvimento: roda as 26 peças contra cópias
MODIFICADAS do .db e confere, com a métrica oficial, se a classificação continua coerente.

Para cada variante, o gabarito é transformado do jeito que a definição do desafio manda:
    registro removido         -> a citação real que apontava para ele passa a ser 'inventada'
    id trocado                -> a citação real passa a esperar o id novo
    norma reescrita           -> mesma norma, cabeçalho escrito de outro jeito: continua 'real'
Variantes:
    ids_trocados       todos os ids e documento_id renumerados/embaralhados
    removidos          ~25% dos acórdãos de número único e 3 normas apagados
    normas_diferentes  cabeçalhos das normas reescritos em outros formatos, 2 normas apagadas,
                       3 normas novas (inclusive de lei que o vocabulário não conhece)
    sem_normas         nenhuma súmula nem artigo na base
    so_normas          nenhum acórdão na base
    tribunais_novos    acórdãos não citados com tribunal desconhecido ('TRF4') ou nulo
    sem_fts            sem a tabela documentos_fts
Nota esperada em toda variante: macro-F1 = 1 nos dois níveis (e nenhum erro de execução).

Uso:  python scripts/bancos_modificados.py [--db data/desafio1_bracis.db] [--variante X] [--manter PASTA]
"""
import argparse
import random
from collections import Counter, defaultdict
import sqlite3
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "oficial"))
sys.path.insert(0, str(RAIZ / "scripts"))
import kaggle_metric as km                                   # noqa: E402
from avaliar import carregar_gabarito, gabarito_para_solution  # noqa: E402
from json_to_submission import encode                        # noqa: E402
from src.indice import Indice                                # noqa: E402
from src.run import encontrar_citacoes, ler_texto, montar_json  # noqa: E402


# ============================================================================ variantes

def _ids(con):
    return {doc: id_ for doc, id_ in con.execute("SELECT documento_id, id FROM documentos")}


def ids_trocados(con, rng, ix):
    linhas = con.execute("SELECT documento_id, id FROM documentos").fetchall()
    novos = rng.sample(range(10 ** 9, 10 ** 10), len(linhas))
    nomes = [f"reg_{k:05d}" for k in rng.sample(range(len(linhas)), len(linhas))]
    mapa = {}
    for (doc, id_), novo, nome in zip(linhas, novos, nomes):
        con.execute("UPDATE documentos SET id = ?, documento_id = ? WHERE documento_id = ?", (novo, nome, doc))
        mapa[id_] = novo
    return mapa


def removidos(con, rng, ix):
    unicos = [f for f in ix.fichas if f.natureza == "acordao" and f.numero and len(ix.buscar_numero(f.numero)) == 1]
    fora = rng.sample(unicos, len(unicos) // 4)
    normas = rng.sample([f for f in ix.fichas if f.natureza != "acordao"], 3)
    mapa = {}
    for f in fora + normas:
        con.execute("DELETE FROM documentos WHERE documento_id = ?", (f.documento_id,))
        mapa[f.id] = None
    return mapa


# (cabeçalho original, cabeçalho novo): a mesma norma escrita de outro jeito
REESCRITAS = [
    ("Súmula n. 83 do STJ", "Súmula nº 83 do Superior Tribunal de Justiça"),
    ("Súmula n. 211 do STJ", "Súmula 211"),                     # sem tribunal: vem da coluna `tribunal`
    ("Súmula n. 443 do STJ", "Enunciado 443 da Súmula do STJ"),
    ("Súmula Vinculante n. 10 do STF", "SÚMULA VINCULANTE 10"),
    ("Artigo 276 da Lei nº 4.737, de 15 de julho de 1965", "Art. 276 da Lei nº 4.737/1965 (Código Eleitoral)"),
    ("Artigo 290 do Decreto-Lei nº 1.001, de 21 de outubro de 1969", "Art. 290 do Código Penal Militar"),
    ("Artigo 14 da Lei nº 8.078, de 11 de setembro de 1990", "Artigo 14 do Código de Defesa do Consumidor"),
    ("Artigo 93 da Constituição Federal de 1988", "Art. 93 da CF/88"),
    ("Artigo 5º da Constituição Federal de 1988", "Art. 5º da Constituição da República Federativa do Brasil"),
    ("Artigo 818 do Decreto-Lei nº 5.452, de 1º de maio de 1943", "Artigo 818 da Consolidação das Leis do Trabalho"),
]
APAGAR = ["Artigo 186 da Lei nº 10.406", "Súmula n. 331 do TST"]
NOVAS = [
    (9000000001, "Súmula nº 7 do STJ\nA pretensão de simples reexame de prova não enseja recurso especial."),
    (9000000002, "Artigo 121 do Decreto-Lei nº 2.848, de 7 de dezembro de 1940\nArt. 121. Matar alguém."),
    (9000000003, "Artigo 116 da Lei nº 8.112, de 11 de dezembro de 1990\nArt. 116. São deveres do servidor."),
]


def normas_diferentes(con, rng, ix):
    mapa = {}
    for velho, novo in REESCRITAS:
        n = con.execute("UPDATE documentos SET texto = ? || substr(texto, ?) WHERE natureza != 'acordao' "
                        "AND texto LIKE ?", (novo, len(velho) + 1, velho + "%")).rowcount
        assert n == 1, f"cabeçalho não encontrado: {velho}"
    for prefixo in APAGAR:
        for (id_,) in con.execute("SELECT id FROM documentos WHERE natureza != 'acordao' AND texto LIKE ?",
                                  (prefixo + "%",)).fetchall():
            mapa[id_] = None
        con.execute("DELETE FROM documentos WHERE natureza != 'acordao' AND texto LIKE ?", (prefixo + "%",))
    for id_, texto in NOVAS:
        natureza = "sumula" if texto.startswith("Súmula") else "dispositivo"
        tipo = "jurisprudencia" if natureza == "sumula" else "lei"
        con.execute("INSERT INTO documentos (documento_id, id, tribunal, ano, relator, natureza, tipo, texto, "
                    "texto_len) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?)",
                    (f"nova_{id_}", id_, "STJ" if natureza == "sumula" else None, natureza, tipo, texto, len(texto)))
    return mapa


def sem_normas(con, rng, ix):
    mapa = {id_: None for (id_,) in con.execute("SELECT id FROM documentos WHERE natureza != 'acordao'")}
    con.execute("DELETE FROM documentos WHERE natureza != 'acordao'")
    return mapa


def so_normas(con, rng, ix):
    mapa = {id_: None for (id_,) in con.execute("SELECT id FROM documentos WHERE natureza = 'acordao'")}
    con.execute("DELETE FROM documentos WHERE natureza = 'acordao'")
    return mapa


def tribunais_novos(con, rng, ix, citados=frozenset()):
    # só registros de número único: reetiquetar um de dois registros com o mesmo número criaria uma base
    # contraditória, sem resposta certa definida
    livres = [f for f in ix.fichas if f.natureza == "acordao" and str(f.id) not in citados
              and f.numero and len(ix.buscar_numero(f.numero)) == 1]
    alvo = rng.sample(livres, 30)
    for k, f in enumerate(alvo):
        trib = "TRF4" if k < 20 else None                    # (o schema não admite natureza fora das 3)
        con.execute("UPDATE documentos SET tribunal = ? WHERE documento_id = ?", (trib, f.documento_id))
    return {}


def sem_fts(con, rng, ix):
    con.execute("DROP TABLE IF EXISTS documentos_fts")
    return {}


VARIANTES = {"ids_trocados": ids_trocados, "removidos": removidos, "normas_diferentes": normas_diferentes,
             "sem_normas": sem_normas, "so_normas": so_normas, "tribunais_novos": tribunais_novos,
             "sem_fts": sem_fts}


# ============================================================================ conferência

def gabarito_transformado(g, mapa):
    g = g.copy()
    for i, r in g.iterrows():
        if r["classificacao"] == "real" and r["id_canonico"]:
            velho = int(r["id_canonico"])
            if velho in mapa:
                novo = mapa[velho]
                if novo is None:
                    g.at[i, "classificacao"], g.at[i, "id_canonico"] = "inventada", ""
                else:
                    g.at[i, "id_canonico"] = str(novo)
    return g


def rodar(caminho_db, g, por_situacao):
    """Roda as 26 peças com a base dada; acumula em `por_situacao` acertos/erros por situação."""
    ix = Indice(str(caminho_db))
    linhas = []
    for arq in sorted((RAIZ / "data" / "txt").glob("*.txt")):
        texto = ler_texto(arq)
        citacoes = encontrar_citacoes(texto, ix)
        doc = montar_json(arq.stem, texto, citacoes)
        linhas.append({"documento_id": arq.stem, "citacoes": encode(doc)})
        golds = [dict(inicio=r.inicio, fim=r.fim, classe=r.classificacao, id=r.id_canonico)
                 for r in g[g["documento_id"] == arq.stem].itertuples()]
        pares, _, _ = km._casar(golds, citacoes)
        for gi, pi in pares:
            gd, c = golds[gi], citacoes[pi]
            ok = c["classificacao"] == gd["classe"] and (gd["classe"] != "real" or str(c["id_canonico"]) == gd["id"])
            por_situacao[c["motivo"][1:c["motivo"].index("]")]][ok] += 1
    import pandas as pd
    return km.avaliar(gabarito_para_solution(g), pd.DataFrame(linhas)), ix


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(RAIZ / "data" / "desafio1_bracis.db"))
    ap.add_argument("--variante", choices=list(VARIANTES))
    ap.add_argument("--manter", type=Path, help="pasta onde guardar os bancos gerados (senão, temporária)")
    args = ap.parse_args()
    g = carregar_gabarito()
    citados = frozenset(g.loc[g["id_canonico"] != "", "id_canonico"])
    ix0 = Indice(args.db)
    pasta_tmp = tempfile.TemporaryDirectory() if not args.manter else None
    pasta = args.manter or Path(pasta_tmp.name)
    pasta.mkdir(parents=True, exist_ok=True)
    falhou = False
    por_situacao = defaultdict(Counter)
    print(f"{'variante':<20}{'registros':>10}{'reais esperadas':>17}{'nota':>9}{'macroF1 N1':>12}{'macroF1 N2':>12}")
    for nome, fazer in VARIANTES.items():
        if args.variante and args.variante != nome:
            continue
        destino = pasta / f"{nome}.db"
        destino.unlink(missing_ok=True)
        with sqlite3.connect(args.db) as origem, sqlite3.connect(destino) as con:
            origem.backup(con)
            rng = random.Random(nome)
            mapa = fazer(con, rng, ix0, citados) if fazer is tribunais_novos else fazer(con, rng, ix0)
            con.commit()
            n = con.execute("SELECT COUNT(*) FROM documentos").fetchone()[0]
        gt = gabarito_transformado(g, mapa)
        try:
            res, _ = rodar(destino, gt, por_situacao)
        except Exception as e:                                # noqa: BLE001
            print(f"{nome:<20}{n:>10}  ERRO DE EXECUÇÃO: {type(e).__name__}: {e}")
            falhou = True
            continue
        n_reais = int((gt["classificacao"] == "real").sum())
        m1, m2 = res["niveis"][1]["macro_f1"], res["niveis"][2]["macro_f1"]
        print(f"{nome:<20}{n:>10}{n_reais:>17}{res['score_final']:>9.4f}{m1:>12.4f}{m2:>12.4f}")
        falhou |= min(m1, m2) < 1.0
    print(f"\n26 peças x todas as variantes, por situação (citações casadas):\n   {'situação':<56}{'n':>6}{'erros':>7}")
    for sit, c in sorted(por_situacao.items()):
        print(f"   {sit:<56}{c[True] + c[False]:>6}{c[False]:>7}")
    if pasta_tmp:
        pasta_tmp.cleanup()
    sys.exit(1 if falhou else 0)


if __name__ == "__main__":
    main()
