"""Comparação dos dois lados e envio só do que falta (ou mudou de tamanho), em paralelo."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlencode

from .config import Config
from .mapeamento import Arquivo


@dataclass
class Plano:
    novos: list[Arquivo] = field(default_factory=list)
    alterados: list[Arquivo] = field(default_factory=list)  # já existe no S3 com outro tamanho
    ja_existem: int = 0


def planejar(locais: list[Arquivo], nuvem: dict[str, int]) -> Plano:
    plano = Plano()
    for a in locais:
        if a.chave not in nuvem:
            plano.novos.append(a)
        elif nuvem[a.chave] != a.tamanho:
            plano.alterados.append(a)
        else:
            plano.ja_existem += 1
    return plano


def executar(s3, cfg: Config, plano: Plano, paralelo: int = 4, dry_run: bool = False) -> list[dict]:
    tarefas = [(a, "enviado") for a in plano.novos] + [(a, "reenviado") for a in plano.alterados]
    if dry_run:
        return [_linha(a, "simulado") for a, _ in tarefas]
    extra = {"Tagging": urlencode(cfg.tags)} if cfg.tags else {}

    def enviar(tarefa):
        arquivo, situacao = tarefa
        try:
            s3.upload_file(str(arquivo.caminho), cfg.bucket, arquivo.chave, ExtraArgs={
                **extra, "Metadata": {"modificado-em": arquivo.modificado_em.isoformat(timespec="seconds")}})
            return _linha(arquivo, situacao)
        except Exception as exc:  # um arquivo com problema não derruba os outros
            return _linha(arquivo, "erro", f"{type(exc).__name__}: {exc}")

    with ThreadPoolExecutor(max_workers=paralelo) as pool:
        return list(pool.map(enviar, tarefas))


def _linha(arquivo: Arquivo, situacao: str, detalhe: str = "") -> dict:
    return {"chave": arquivo.chave, "tamanho": arquivo.tamanho, "situacao": situacao, "detalhe": detalhe}
