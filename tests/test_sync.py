import os
from datetime import date, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from sync_s3 import config, mapeamento, sincronizacao

EXTRATOS = config.carregar("extratos")
INFORMES = config.carregar("informes")


@pytest.fixture
def s3():
    for variavel in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        os.environ.setdefault(variavel, "teste")
    with mock_aws():
        cliente = boto3.client("s3", region_name="us-east-1")
        cliente.create_bucket(Bucket=EXTRATOS.bucket)
        yield cliente


def arquivo(base, relativo, tamanho=100):
    caminho = base / relativo
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(b"x" * tamanho)
    return caminho


def test_estrutura_e_fora_do_padrao(tmp_path):
    arquivo(tmp_path, "C1/EXTRATOS/2026/03/a.pdf")
    arquivo(tmp_path, "C1/EXTRATOS/2026/03/b.TXT")            # extensão aceita em maiúscula
    arquivo(tmp_path, "C1/EXTRATOS/2026/solto.pdf")
    arquivo(tmp_path, "C1/EXTRATOS/2026/13/c.pdf")
    arquivo(tmp_path, "C1/EXTRATOS/2026/03/x/d.pdf")
    arquivo(tmp_path, "C1/EXTRATOS/2026/03/e.docx")
    arquivo(tmp_path, "C1/EXTRATOS/2025/03/f.pdf")            # outro ano: fica de fora sem ser erro
    arquivo(tmp_path, "C2/INFORMES/2026/g.pdf")               # outro tipo: idem
    locais, fora = mapeamento.mapear_local(tmp_path, EXTRATOS, 2026)
    assert [a.chave for a in locais] == ["arquivo-clientes/C1/EXTRATOS/2026/03/a.pdf",
                                         "arquivo-clientes/C1/EXTRATOS/2026/03/b.TXT"]
    assert dict(fora) == {
        "C1/EXTRATOS/2026/solto.pdf": "arquivo direto na pasta do ano, sem a pasta do mês",
        "C1/EXTRATOS/2026/13/c.pdf": "pasta de mês inválida: 13",
        "C1/EXTRATOS/2026/03/x/d.pdf": "subpasta a mais dentro do mês",
        "C1/EXTRATOS/2026/03/e.docx": "extensão não aceita: .docx",
    }


def test_tipo_sem_mes(tmp_path):
    arquivo(tmp_path, "C1/INFORMES/2026/i.pdf")
    arquivo(tmp_path, "C1/INFORMES/2026/01/j.pdf")
    locais, fora = mapeamento.mapear_local(tmp_path, INFORMES, 2026)
    assert [a.chave for a in locais] == ["arquivo-clientes/C1/INFORMES/2026/i.pdf"]
    assert fora == [("C1/INFORMES/2026/01/j.pdf", "subpasta dentro do ano, e este tipo não usa mês")]


def test_nuvem_so_do_tipo_e_do_ano(s3):
    for chave in ("arquivo-clientes/C1/EXTRATOS/2026/03/a.pdf", "arquivo-clientes/C1/EXTRATOS/2025/03/a.pdf",
                  "arquivo-clientes/C1/INFORMES/2026/i.pdf", "outro-prefixo/C1/EXTRATOS/2026/03/a.pdf"):
        s3.put_object(Bucket=EXTRATOS.bucket, Key=chave, Body=b"12345")
    assert mapeamento.mapear_nuvem(s3, EXTRATOS, 2026) == {"arquivo-clientes/C1/EXTRATOS/2026/03/a.pdf": 5}
    assert list(mapeamento.mapear_nuvem(s3, INFORMES, 2026)) == ["arquivo-clientes/C1/INFORMES/2026/i.pdf"]


def test_envia_so_o_que_falta_com_tags_e_metadado(tmp_path, s3):
    arquivo(tmp_path, "C1/EXTRATOS/2026/03/a.pdf", 100)
    arquivo(tmp_path, "C1/EXTRATOS/2026/03/b.pdf", 100)
    s3.put_object(Bucket=EXTRATOS.bucket, Key="arquivo-clientes/C1/EXTRATOS/2026/03/a.pdf", Body=b"x" * 100)
    locais, _ = mapeamento.mapear_local(tmp_path, EXTRATOS, 2026)
    plano = sincronizacao.planejar(locais, mapeamento.mapear_nuvem(s3, EXTRATOS, 2026))
    assert ([a.chave.rsplit("/", 1)[1] for a in plano.novos], plano.alterados, plano.ja_existem) == (["b.pdf"], [], 1)
    resultado = sincronizacao.executar(s3, EXTRATOS, plano)
    assert [r["situacao"] for r in resultado] == ["enviado"]
    chave = "arquivo-clientes/C1/EXTRATOS/2026/03/b.pdf"
    assert s3.get_object_tagging(Bucket=EXTRATOS.bucket, Key=chave)["TagSet"] == [{"Key": "tipo", "Value": "extrato"}]
    assert "modificado-em" in s3.head_object(Bucket=EXTRATOS.bucket, Key=chave)["Metadata"]


def test_tamanho_diferente_e_reenviado_e_dry_run_nao_envia(tmp_path, s3):
    arquivo(tmp_path, "C1/EXTRATOS/2026/03/a.pdf", 300)
    s3.put_object(Bucket=EXTRATOS.bucket, Key="arquivo-clientes/C1/EXTRATOS/2026/03/a.pdf", Body=b"x" * 100)
    locais, _ = mapeamento.mapear_local(tmp_path, EXTRATOS, 2026)
    plano = sincronizacao.planejar(locais, mapeamento.mapear_nuvem(s3, EXTRATOS, 2026))
    assert len(plano.alterados) == 1
    assert [r["situacao"] for r in sincronizacao.executar(s3, EXTRATOS, plano, dry_run=True)] == ["simulado"]
    assert mapeamento.mapear_nuvem(s3, EXTRATOS, 2026) == {"arquivo-clientes/C1/EXTRATOS/2026/03/a.pdf": 100}
    assert [r["situacao"] for r in sincronizacao.executar(s3, EXTRATOS, plano)] == ["reenviado"]


def test_um_arquivo_com_erro_nao_para_os_outros(tmp_path, s3):
    arquivo(tmp_path, "C1/EXTRATOS/2026/03/a.pdf")
    sumiu = arquivo(tmp_path, "C1/EXTRATOS/2026/03/b.pdf")
    locais, _ = mapeamento.mapear_local(tmp_path, EXTRATOS, 2026)
    plano = sincronizacao.planejar(locais, {})
    sumiu.unlink()                                       # apagado entre o mapeamento e o envio
    situacoes = sorted(r["situacao"] for r in sincronizacao.executar(s3, EXTRATOS, plano))
    assert situacoes == ["enviado", "erro"]


def test_modificados_desde(tmp_path):
    velho = arquivo(tmp_path, "C1/EXTRATOS/2026/01/velho.pdf")
    arquivo(tmp_path, "C1/EXTRATOS/2026/03/novo.pdf")
    antigo = (datetime.now() - timedelta(days=40)).timestamp()
    os.utime(velho, (antigo, antigo))
    locais, _ = mapeamento.mapear_local(tmp_path, EXTRATOS, 2026, desde=date.today() - timedelta(days=7))
    assert [a.chave.rsplit("/", 1)[1] for a in locais] == ["novo.pdf"]


def test_config_desconhecida_ou_incompleta(tmp_path):
    with pytest.raises(ValueError, match="disponíveis: extratos, informes"):
        config.carregar("boletos")
    incompleta = tmp_path / "x.yaml"
    incompleta.write_text("nome: X\nslug: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="faltam as chaves"):
        config.carregar(str(incompleta))
