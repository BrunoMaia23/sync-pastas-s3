# sync-pastas-s3

[![testes](https://github.com/BrunoMaia23/sync-pastas-s3/actions/workflows/testes.yml/badge.svg)](https://github.com/BrunoMaia23/sync-pastas-s3/actions/workflows/testes.yml)

Uma pasta de rede com os documentos de cada cliente (extratos todo mês, informes todo ano) precisava ir
para o S3, e cada tipo de documento tinha ganhado o seu script de cópia, quase igual ao do outro. No
trabalho, este foi um projeto do time em que eu trabalhei: juntar os scripts num motor só, em que o que
muda entre um tipo e outro fica num arquivo de configuração. Este repositório refaz a ideia do zero,
com dados fictícios e um S3 simulado.

*In English: a config-driven local-folder-to-S3 sync. One engine, one YAML per document type; it maps
the disk and the bucket, uploads only what is missing or changed, reports files outside the expected
layout instead of silently skipping them, and writes a CSV for each step. Tested against moto.*

## Um motor, uma configuração por tipo

```yaml
# src/sync_s3/configs/extratos.yaml
nome: Extratos mensais
slug: extratos
subpasta: EXTRATOS
usa_mes: true                 # <cliente>/EXTRATOS/<ano>/<mes>/<arquivo>
extensoes: [pdf, txt]
bucket: acervo-clientes-demo
prefixo: arquivo-clientes
tags:
  tipo: extrato
```

O tipo "informes" é outro YAML, com `usa_mes: false`. Um tipo novo de documento é um arquivo novo em
`configs/`; o código não muda.

## Como decide o que enviar

1. **Disco.** Percorre `<cliente>/<SUBPASTA>/<ano>/` e valida a estrutura. Arquivo solto na pasta do ano
   quando o tipo usa mês, pasta de mês "13", subpasta a mais ou extensão não aceita não são enviados:
   vão para o relatório de fora do padrão, porque numa pasta de rede preenchida à mão eles sempre
   aparecem. A extensão é comparada sem diferenciar maiúscula de minúscula.
2. **Bucket.** Lista o prefixo em páginas e fica só com as chaves do tipo e do ano.
3. **Plano.** Chave que não existe no S3 é nova; que existe com outro tamanho foi alterada e vai de
   novo; o resto já está lá.
4. **Envio.** Em paralelo, com as tags da configuração e a data de modificação no metadado do objeto.
   Um arquivo com erro (apagado entre o mapeamento e o envio, por exemplo) não derruba os outros.

Cada etapa grava um CSV (mapeamento local, mapeamento da nuvem e resultado), que é o que se olha
quando alguém pergunta se um arquivo subiu. `--dry-run` monta o plano sem enviar, e
`--modificados-desde` limita o disco aos arquivos mexidos depois de uma data, para cargas incrementais.

## A demo

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m sync_s3 demo
```

A demo cria uma pasta de rede fictícia, com alguns arquivos fora do padrão de propósito, e sincroniza
contra um S3 simulado em memória pelo moto:

```
[S3]          bucket simulado em memória (moto)

[extratos 2025]  disco 72 | S3 0 | enviar 72 novos e 0 alterados | já estavam 0 | erros 0
[extratos 2026]  disco 18 | S3 0 | enviar 18 novos e 0 alterados | já estavam 0 | erros 0
      fora do padrão: C0002/EXTRATOS/2026/extrato_avulso.pdf (arquivo direto na pasta do ano, sem a pasta do mês)
      fora do padrão: C0003/EXTRATOS/2026/13/extrato_202613.pdf (pasta de mês inválida: 13)
      fora do padrão: C0004/EXTRATOS/2026/02/~extrato_202602.tmp (extensão não aceita: .tmp)
[informes 2025]  disco 6 | S3 0 | enviar 6 novos e 0 alterados | já estavam 0 | erros 0

[de novo]     a mesma pasta, sem nada novo
[extratos 2026]  disco 18 | S3 18 | enviar 0 novos e 0 alterados | já estavam 18 | erros 0 | fora do padrão 3

[mudanças]    chegam os extratos de abril e um extrato de março é regerado
[extratos 2026] (simulação)  disco 24 | S3 18 | enviar 6 novos e 1 alterado | já estavam 17 | erros 0 | fora do padrão 3
[extratos 2026]  disco 24 | S3 18 | enviar 6 novos e 1 alterado | já estavam 17 | erros 0 | fora do padrão 3

[conferência] tags do objeto enviado: [{'Key': 'tipo', 'Value': 'extrato'}] | CSVs em saida/: 9
```

Contra o S3 de verdade é o mesmo código, com as credenciais do ambiente:
`python -m sync_s3 sincronizar --tipo extratos --ano 2026 --base /mnt/rede`.

## No projeto real

As pastas são de documentos que os clientes acessam por um portal, e o processo roda no Airflow,
parametrizado por tipo e ano, com uma rotina separada para as cargas retroativas de anos anteriores.

## Testes

`pytest` usa o moto e cobre a estrutura válida e cada motivo de fora do padrão, o tipo sem mês, o filtro
da listagem do bucket por tipo e ano, o envio só do que falta com tags e metadado, o reenvio por
tamanho diferente, a simulação, o erro isolado num arquivo e o filtro por data de modificação.
