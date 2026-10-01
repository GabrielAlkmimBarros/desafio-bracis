"""Ponto de entrada da solução (contrato de execução do desafio).

Uso:
    python -m src.run --input data/txt --output out [--db data/desafio1_bracis.db]

Lê cada .txt de --input e escreve um .json com o mesmo nome-base em --output,
no formato do Contrato de Entrada e Saída (schema 1.2).

Fluxo por documento:  texto --detectar--> candidatas --resolver(índice)--> citações --> JSON
O índice da base é montado uma vez só, no começo (≈1 s).

Robustez: um problema numa peça nunca derruba as outras. Arquivo que não é UTF-8 válido é lido trocando
só os bytes inválidos; um detector ou uma citação com erro é descartado sozinho; e se a peça inteira
falhar (ou passar do limite de tempo), ela sai com um JSON sem citações — continua presente na saída,
como o enunciado exige. Os erros são relatados em stderr e a execução termina normalmente.
"""
import argparse
import contextlib
import json
import signal
import sys
import traceback
from pathlib import Path

SCHEMA_VERSION = "1.2"


def ler_texto(caminho: Path) -> str:
    # newline="" impede o Python de converter quebras de linha: os offsets do
    # gabarito são contados sobre o texto exatamente como está no arquivo.
    try:
        with open(caminho, encoding="utf-8", newline="") as f:
            return f.read()
    except UnicodeDecodeError:
        # não deveria acontecer (a entrada é UTF-8); se acontecer, troca só os bytes inválidos por U+FFFD
        print(f"aviso: {caminho.name} não é UTF-8 válido; bytes inválidos substituídos", file=sys.stderr)
        with open(caminho, encoding="utf-8", errors="replace", newline="") as f:
            return f.read()


class TempoEsgotado(Exception):
    pass


@contextlib.contextmanager
def limite_de_tempo(segundos: int):
    """Interrompe o processamento de uma peça que passe de `segundos` (onde houver SIGALRM: Linux, macOS)."""
    if not segundos or not hasattr(signal, "SIGALRM"):
        yield
        return

    def estourou(_sinal, _quadro):
        raise TempoEsgotado(f"passou de {segundos} s")
    anterior = signal.signal(signal.SIGALRM, estourou)
    signal.alarm(segundos)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, anterior)


def encontrar_citacoes(texto: str, indice) -> list[dict]:
    """Recebe o texto de um documento e devolve as citações encontradas.

    Cada citação é um dict com: inicio, fim, tipo, classificacao,
    id_canonico (só para real), confianca e motivo (para depuração).
    """
    from .detectar import detectar
    from .resolver import resolver

    saida = []
    for c in detectar(texto):
        try:                       # uma citação com erro é descartada sozinha; as outras seguem
            r = resolver(c, indice)
        except Exception as e:     # noqa: BLE001
            print(f"aviso: citação em {c.inicio}-{c.fim} descartada ({type(e).__name__}: {e})", file=sys.stderr)
            continue
        saida.append(dict(inicio=c.inicio, fim=c.fim, tipo=c.tipo, **r))
    return saida


def montar_json(documento_id: str, texto: str, citacoes: list[dict], debug: bool = False) -> dict:
    saida = []
    for n, c in enumerate(sorted(citacoes, key=lambda c: c["inicio"]), start=1):
        item = {
            "id": f"c{n}",
            "inicio": c["inicio"],
            "fim": c["fim"],
            "trecho": texto[c["inicio"]:c["fim"]],   # sempre recortado do texto: nunca diverge do span
            "tipo": c["tipo"],
            "classificacao": c["classificacao"],
            "resolucao": (
                {"fonte": "jusbrasil", "id_canonico": str(c["id_canonico"])}
                if c["classificacao"] == "real" else None
            ),
        }
        if c.get("confianca") is not None:
            item["confianca"] = round(float(c["confianca"]), 4)
        if debug and c.get("motivo"):
            item["_motivo"] = c["motivo"]          # só para depuração; fora do contrato
        saida.append(item)
    return {"schema_version": SCHEMA_VERSION, "documento_id": documento_id, "citacoes": saida}


def main() -> None:
    ap = argparse.ArgumentParser(description="Caça-Alucinações: detecta e classifica citações.")
    ap.add_argument("--input", required=True, help="pasta com os .txt")
    ap.add_argument("--output", required=True, help="pasta onde gravar os .json")
    ap.add_argument("--db", default="data/desafio1_bracis.db", help="base canônica (SQLite)")
    ap.add_argument("--debug", action="store_true", help="inclui no JSON o motivo de cada decisão")
    ap.add_argument("--limite-segundos", type=int, default=120,
                    help="tempo máximo por peça; passou, a peça sai sem citações (0 = sem limite)")
    args = ap.parse_args()

    entrada, saida = Path(args.input), Path(args.output)
    saida.mkdir(parents=True, exist_ok=True)
    arquivos = sorted(entrada.glob("*.txt"))
    if not arquivos:
        raise SystemExit(f"nenhum .txt em {entrada}")

    from .indice import Indice
    indice = Indice(args.db)
    print(indice.resumo(), file=sys.stderr)          # diagnóstico da base recebida (não entra na saída)

    falhas = []
    for arq in arquivos:
        texto = ""
        try:
            texto = ler_texto(arq)
            with limite_de_tempo(args.limite_segundos):
                citacoes = encontrar_citacoes(texto, indice)
            doc = montar_json(arq.stem, texto, citacoes, debug=args.debug)
        except Exception as e:     # noqa: BLE001  a peça sai vazia, mas sai: nenhuma fica de fora da submissão
            falhas.append(arq.name)
            print(f"ERRO em {arq.name}: {type(e).__name__}: {e}; gravado sem citações", file=sys.stderr)
            traceback.print_exc(limit=3, file=sys.stderr)
            doc = montar_json(arq.stem, texto, [])
        (saida / f"{arq.stem}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(arquivos)} documentos processados -> {saida}/")
    if falhas:
        print(f"atenção: {len(falhas)} documento(s) com erro, gravados sem citações: {falhas}", file=sys.stderr)


if __name__ == "__main__":
    main()
