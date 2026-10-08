"""Sincroniza pastas locais com o S3: mapeia o disco, mapeia o bucket e envia só o que falta."""
from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
from datetime import date
from pathlib import Path

from . import config, mapeamento, sincronizacao

MARCADOR = ".sync-demo"


def _n(qtd: int, singular: str, plural: str) -> str:
    return f"{qtd} {singular if qtd == 1 else plural}"


def sincronizar(s3, base: Path, tipo: str, ano: int, saida: Path, dry_run: bool = False,
                desde: date | None = None, detalhar: bool = True) -> dict:
    cfg = config.carregar(tipo)
    locais, fora = mapeamento.mapear_local(base, cfg, ano, desde)
    mapeamento.gravar_csv(saida / cfg.csv("mapeamento_local", ano),
                          [{"chave": a.chave, "tamanho": a.tamanho, "modificado_em": a.modificado_em.isoformat()}
                           for a in locais] + [{"chave": c, "tamanho": "", "modificado_em": f"FORA DO PADRÃO: {m}"}
                                               for c, m in fora])
    nuvem = mapeamento.mapear_nuvem(s3, cfg, ano)
    mapeamento.gravar_csv(saida / cfg.csv("mapeamento_nuvem", ano),
                          [{"chave": k, "tamanho": v} for k, v in sorted(nuvem.items())])
    plano = sincronizacao.planejar(locais, nuvem)
    resultado = sincronizacao.executar(s3, cfg, plano, dry_run=dry_run)
    mapeamento.gravar_csv(saida / cfg.csv("sincronizacao", ano), resultado)
    resumo = {"locais": len(locais), "no_s3": len(nuvem), "novos": len(plano.novos), "alterados": len(plano.alterados),
              "ja_existiam": plano.ja_existem, "erros": sum(r["situacao"] == "erro" for r in resultado),
              "fora_do_padrao": fora}
    print(f"[{cfg.slug} {ano}]{' (simulação)' if dry_run else ''}  disco {resumo['locais']} | S3 {resumo['no_s3']} | "
          f"enviar {_n(resumo['novos'], 'novo', 'novos')} e {_n(resumo['alterados'], 'alterado', 'alterados')} | "
          f"já estavam {resumo['ja_existiam']} | erros {resumo['erros']}"
          + (f" | fora do padrão {len(fora)}" if fora and not detalhar else ""))
    for caminho, motivo in fora if detalhar else ():
        print(f"      fora do padrão: {caminho} ({motivo})")
    return resumo


def _arvore_ficticia(base: Path, rng: random.Random, clientes: int = 6) -> None:
    for c in range(1, clientes + 1):
        cliente = base / f"C{c:04d}"
        for ano, meses in ((2025, range(1, 13)), (2026, range(1, 4))):
            for m in meses:
                pasta = cliente / "EXTRATOS" / str(ano) / f"{m:02d}"
                pasta.mkdir(parents=True, exist_ok=True)
                (pasta / f"extrato_{ano}{m:02d}.pdf").write_bytes(rng.randbytes(rng.randint(800, 4000)))
        for ano in (2024, 2025):
            pasta = cliente / "INFORMES" / str(ano)
            pasta.mkdir(parents=True, exist_ok=True)
            (pasta / f"informe_{ano}.PDF").write_bytes(rng.randbytes(rng.randint(800, 4000)))  # extensão maiúscula
    # o que aparece numa pasta de rede de verdade
    (base / "C0002" / "EXTRATOS" / "2026" / "extrato_avulso.pdf").write_bytes(b"x" * 900)
    (base / "C0003" / "EXTRATOS" / "2026" / "13").mkdir(parents=True)
    (base / "C0003" / "EXTRATOS" / "2026" / "13" / "extrato_202613.pdf").write_bytes(b"x" * 900)
    (base / "C0004" / "EXTRATOS" / "2026" / "02" / "~extrato_202602.tmp").write_bytes(b"x" * 10)


def demo(pasta: Path) -> int:
    try:
        import boto3
        from moto import mock_aws
    except ImportError:
        print("a demo usa o moto para simular o S3: pip install -e \".[dev]\"")
        return 2
    if pasta.exists():
        if not (pasta / MARCADOR).exists():
            print(f"A pasta {pasta} já existe e não foi criada pela demo; escolha outra com --pasta.")
            return 2
        shutil.rmtree(pasta)
    base, saida = pasta / "rede", pasta / "saida"
    base.mkdir(parents=True)
    (pasta / MARCADOR).write_text("pasta da demo do sync\n", encoding="utf-8")
    rng = random.Random(6)
    _arvore_ficticia(base, rng)
    for variavel in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        os.environ.setdefault(variavel, "demo")
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=config.carregar("extratos").bucket)
        print("[S3]          bucket simulado em memória (moto)\n")
        r = [sincronizar(s3, base, "extratos", 2025, saida), sincronizar(s3, base, "extratos", 2026, saida),
             sincronizar(s3, base, "informes", 2025, saida)]

        print("\n[de novo]     a mesma pasta, sem nada novo")
        de_novo = sincronizar(s3, base, "extratos", 2026, saida, detalhar=False)

        print("\n[mudanças]    chegam os extratos de abril e um extrato de março é regerado")
        for c in range(1, 7):
            abril = base / f"C{c:04d}" / "EXTRATOS" / "2026" / "04"
            abril.mkdir(parents=True)
            (abril / "extrato_202604.pdf").write_bytes(rng.randbytes(1500))
        (base / "C0001" / "EXTRATOS" / "2026" / "03" / "extrato_202603.pdf").write_bytes(rng.randbytes(5000))
        simulado = sincronizar(s3, base, "extratos", 2026, saida, dry_run=True, detalhar=False)
        depois = sincronizar(s3, base, "extratos", 2026, saida, detalhar=False)
        tags = s3.get_object_tagging(Bucket="acervo-clientes-demo",
                                     Key="arquivo-clientes/C0001/EXTRATOS/2026/04/extrato_202604.pdf")["TagSet"]
        print(f"\n[conferência] tags do objeto enviado: {tags} | CSVs em {saida.name}/: "
              f"{len(list(saida.glob('*.csv')))}")
    ok = (r[0]["novos"] == 72 and de_novo["novos"] == de_novo["alterados"] == 0 and simulado["novos"] == 6
          and depois["novos"] == 6 and depois["alterados"] == 1 and all(x["erros"] == 0 for x in r + [depois]))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="sync-s3", description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)
    p = sub.add_parser("demo", help="roda contra um S3 simulado em memória")
    p.add_argument("--pasta", type=Path, default=Path("demo"))
    p = sub.add_parser("sincronizar", help="mapeia os dois lados e envia o que falta")
    p.add_argument("--tipo", required=True, help=f"um de: {', '.join(config.disponiveis())}, ou um .yaml")
    p.add_argument("--ano", type=int, required=True)
    p.add_argument("--base", type=Path, required=True, help="pasta local com as pastas dos clientes")
    p.add_argument("--saida", type=Path, default=Path("saida"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--modificados-desde", type=date.fromisoformat, help="AAAA-MM-DD, para cargas incrementais")
    a = parser.parse_args(argv)
    if a.comando == "demo":
        return demo(a.pasta)
    import boto3
    r = sincronizar(boto3.client("s3"), a.base, a.tipo, a.ano, a.saida, a.dry_run, a.modificados_desde)
    return 1 if r["erros"] else 0
