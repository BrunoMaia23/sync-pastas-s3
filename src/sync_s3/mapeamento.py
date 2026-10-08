"""Os dois lados: o que existe no disco e o que já está no S3, para um tipo de pasta e um ano.

No disco a estrutura é <cliente>/<SUBPASTA>/<ano>/[<mes>/]<arquivo>. Arquivo fora dela (sem a pasta do
mês, mês 13, extensão não aceita, subpasta a mais) não é enviado e aparece num relatório próprio, em
vez de sumir em silêncio. A chave no S3 repete a mesma estrutura debaixo do prefixo.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .config import Config

MESES = {f"{m:02d}" for m in range(1, 13)}


@dataclass(frozen=True)
class Arquivo:
    chave: str
    caminho: Path
    tamanho: int
    modificado_em: datetime


def chave_s3(cfg: Config, cliente: str, ano: int, mes: str | None, nome: str) -> str:
    return "/".join(p for p in (cfg.prefixo, cliente, cfg.subpasta, str(ano), mes, nome) if p)


def mapear_local(base: Path, cfg: Config, ano: int, desde: date | None = None
                 ) -> tuple[list[Arquivo], list[tuple[str, str]]]:
    """(arquivos válidos, [(caminho relativo, motivo)] fora do padrão). `desde` limita pela data de modificação."""
    validos, fora = [], []
    for pasta_cliente in sorted(p for p in base.iterdir() if p.is_dir()):
        pasta_ano = pasta_cliente / cfg.subpasta / str(ano)
        if not pasta_ano.is_dir():
            continue
        for item in sorted(pasta_ano.rglob("*")):
            if not item.is_file():
                continue
            relativo = item.relative_to(pasta_ano).parts
            motivo = _motivo(cfg, relativo, item.suffix)
            if motivo:
                fora.append((item.relative_to(base).as_posix(), motivo))
                continue
            info = item.stat()
            modificado = datetime.fromtimestamp(info.st_mtime)
            if desde and modificado.date() < desde:
                continue
            mes = relativo[0] if cfg.usa_mes else None
            validos.append(Arquivo(chave_s3(cfg, pasta_cliente.name, ano, mes, item.name), item, info.st_size, modificado))
    return validos, fora


def _motivo(cfg: Config, relativo: tuple[str, ...], sufixo: str) -> str | None:
    if cfg.usa_mes:
        if len(relativo) == 1:
            return "arquivo direto na pasta do ano, sem a pasta do mês"
        if len(relativo) > 2:
            return "subpasta a mais dentro do mês"
        if relativo[0] not in MESES:
            return f"pasta de mês inválida: {relativo[0]}"
    elif len(relativo) > 1:
        return "subpasta dentro do ano, e este tipo não usa mês"
    if sufixo.lower().lstrip(".") not in cfg.extensoes:
        return f"extensão não aceita: {sufixo or '(sem extensão)'}"
    return None


def mapear_nuvem(s3, cfg: Config, ano: int) -> dict[str, int]:
    """{chave: tamanho} dos objetos do tipo e ano, listando o prefixo em páginas."""
    resultado = {}
    inicio = f"{cfg.prefixo}/"
    tamanho = 5 if cfg.usa_mes else 4  # cliente, subpasta, ano, [mês], arquivo
    for pagina in s3.get_paginator("list_objects_v2").paginate(Bucket=cfg.bucket, Prefix=inicio):
        for obj in pagina.get("Contents", []):
            partes = obj["Key"][len(inicio):].split("/")
            if len(partes) == tamanho and partes[1] == cfg.subpasta and partes[2] == str(ano):
                resultado[obj["Key"]] = obj["Size"]
    return resultado


def gravar_csv(caminho: Path, linhas: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        if not linhas:
            f.write("")
            return
        escritor = csv.DictWriter(f, fieldnames=list(linhas[0]))
        escritor.writeheader()
        escritor.writerows(linhas)
