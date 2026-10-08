"""Configuração por tipo de pasta. O motor não sabe nada de nenhum tipo: tudo que muda está no YAML."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PASTA = Path(__file__).with_name("configs")
OBRIGATORIAS = ("nome", "slug", "subpasta", "usa_mes", "extensoes", "bucket", "prefixo", "nomes_csv")


@dataclass(frozen=True)
class Config:
    nome: str
    slug: str
    subpasta: str
    usa_mes: bool
    extensoes: tuple[str, ...]
    bucket: str
    prefixo: str
    tags: dict
    nomes_csv: dict

    def csv(self, etapa: str, ano: int) -> str:
        return self.nomes_csv[etapa].format(slug=self.slug, ano=ano)


def disponiveis() -> list[str]:
    return sorted(p.stem for p in PASTA.glob("*.yaml"))


def carregar(tipo: str) -> Config:
    """`tipo` é o slug de uma configuração do pacote ou o caminho de um YAML."""
    arquivo = Path(tipo) if tipo.endswith((".yaml", ".yml")) else PASTA / f"{tipo}.yaml"
    if not arquivo.exists():
        raise ValueError(f"tipo de pasta desconhecido: {tipo!r} (disponíveis: {', '.join(disponiveis())})")
    dados = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
    faltando = [c for c in OBRIGATORIAS if c not in dados]
    if faltando:
        raise ValueError(f"{arquivo.name}: faltam as chaves {', '.join(faltando)}")
    return Config(nome=dados["nome"], slug=dados["slug"], subpasta=dados["subpasta"], usa_mes=bool(dados["usa_mes"]),
                  extensoes=tuple(e.lower().lstrip(".") for e in dados["extensoes"]), bucket=dados["bucket"],
                  prefixo=dados["prefixo"].strip("/"), tags=dados.get("tags") or {}, nomes_csv=dados["nomes_csv"])
