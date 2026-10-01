"""Ponto de entrada: lê cada .txt de --input e grava um .json (schema 1.2) com o mesmo nome em --output.

Uso:  python -m src.run --input data/txt --output out [--db data/desafio1_bracis.db]

Um problema numa peça não derruba as outras: a peça que falhar ou passar do limite de tempo sai com um JSON
sem citações, e o erro vai para stderr.
"""
import argparse
import contextlib
import json
import os
import signal
import sys
import traceback
from pathlib import Path

SCHEMA_VERSION = "1.2"


def ler_texto(caminho: Path) -> str:
    # newline="": os offsets são contados sobre as quebras de linha exatamente como estão no arquivo
    try:
        with open(caminho, encoding="utf-8", newline="") as f:
            return f.read()
    except UnicodeDecodeError:
        print(f"aviso: {caminho.name} não é UTF-8 válido; bytes inválidos substituídos", file=sys.stderr)
        with open(caminho, encoding="utf-8", errors="replace", newline="") as f:
            return f.read()


class TempoEsgotado(BaseException):
    """BaseException para não ser engolida pelas proteções por detector e por citação, que capturam Exception."""


@contextlib.contextmanager
def limite_de_tempo(segundos: int):
    """Interrompe a peça que passar de `segundos` (só onde há SIGALRM: Linux, macOS)."""
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
    """Citações do texto: dicts com inicio, fim, tipo, classificacao, id_canonico, confianca e motivo."""
    from .detectar import detectar
    from .resolver import resolver

    saida = []
    for c in detectar(texto):
        try:                       # uma citação com erro é descartada sozinha
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
            "trecho": texto[c["inicio"]:c["fim"]],
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
            item["_motivo"] = c["motivo"]          # fora do contrato
        saida.append(item)
    return {"schema_version": SCHEMA_VERSION, "documento_id": documento_id, "citacoes": saida}


def gravar_atomico(destino: Path, conteudo: str) -> None:
    """Grava num temporário e renomeia, para uma interrupção nunca deixar JSON pela metade."""
    tmp = destino.with_name(destino.name + ".tmp")
    tmp.write_text(conteudo, encoding="utf-8")
    os.replace(tmp, destino)


def main() -> None:
    # terminal sem UTF-8 (LC_ALL=C) não pode derrubar a execução por causa de um acento
    for fluxo in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            fluxo.reconfigure(errors="backslashreplace")
    ap = argparse.ArgumentParser(description="Caça-Alucinações: detecta e classifica citações.")
    ap.add_argument("--input", required=True, help="pasta com os .txt")
    ap.add_argument("--output", required=True, help="pasta onde gravar os .json")
    ap.add_argument("--db", default="data/desafio1_bracis.db", help="base canônica (SQLite)")
    ap.add_argument("--debug", action="store_true", help="inclui no JSON o motivo de cada decisão")
    ap.add_argument("--limite-segundos", type=int, default=120,
                    help="tempo máximo por peça; passou, a peça sai sem citações (0 = sem limite)")
    args = ap.parse_args()

    entrada, saida = Path(args.input), Path(args.output)
    if not entrada.is_dir():
        print(f"erro: pasta de entrada não encontrada: {entrada}", file=sys.stderr)
        raise SystemExit(2)
    try:
        saida.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"erro: não foi possível criar a pasta de saída {saida}: {e}", file=sys.stderr)
        raise SystemExit(2)
    # só .txt diretamente na pasta; ocultos ('._x.txt' do macOS) virariam documentos fantasmas
    arquivos = sorted(p for p in entrada.iterdir()
                      if p.is_file() and p.suffix.lower() == ".txt" and not p.name.startswith("."))
    if not arquivos:
        raise SystemExit(f"nenhum .txt em {entrada}")

    from .indice import BaseInvalida, Indice
    try:
        indice = Indice(args.db)
    except BaseInvalida as e:
        print(f"erro: {e}", file=sys.stderr)
        raise SystemExit(2)
    print(indice.resumo(), file=sys.stderr)

    # JSONs antigos sem peça nesta entrada só geram aviso: a métrica ignora documentos a mais
    atuais = {a.stem for a in arquivos}
    antigos = sorted(v.name for v in saida.glob("*.json") if v.stem not in atuais)
    if antigos:
        print(f"aviso: a pasta de saída já tem {len(antigos)} JSON(s) sem peça nesta entrada (mantidos): "
              f"{antigos[:5]}", file=sys.stderr)

    falhas, nao_gravados = [], []
    for arq in arquivos:
        texto = ""
        try:
            texto = ler_texto(arq)
            with limite_de_tempo(args.limite_segundos):
                citacoes = encontrar_citacoes(texto, indice)
            doc = montar_json(arq.stem, texto, citacoes, debug=args.debug)
        except (Exception, TempoEsgotado) as e:   # noqa: BLE001  a peça sai sem citações, mas sai
            falhas.append(arq.name)
            print(f"ERRO em {arq.name}: {type(e).__name__}: {e}; gravado sem citações", file=sys.stderr)
            traceback.print_exc(limit=3, file=sys.stderr)
            doc = montar_json(arq.stem, texto, [])
        try:
            gravar_atomico(saida / f"{arq.stem}.json", json.dumps(doc, ensure_ascii=False, indent=2))
        except OSError as e:       # sem permissão ou disco cheio: segue e avisa no fim
            nao_gravados.append(arq.name)
            print(f"ERRO: não foi possível gravar o JSON de {arq.name}: {e}", file=sys.stderr)
    print(f"{len(arquivos)} documentos processados -> {saida}/")
    if falhas:
        print(f"atenção: {len(falhas)} documento(s) com erro, gravados sem citações: {falhas}", file=sys.stderr)
    if nao_gravados:
        print(f"ERRO: {len(nao_gravados)} JSON(s) não gravado(s): {nao_gravados}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
