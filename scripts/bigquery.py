"""Leitura e gravação no BigQuery (projeto embrapa-cafe), sempre com cópia de segurança antes de trocar.

  baixar   copia a tabela atual do BigQuery para um arquivo (é a "versão atual" que as conferências usam)
  teste    grava o arquivo novo em ibge_cafe_teste.<tabela>_teste (dataset de teste, expira em 59 dias)
  real     só com GRAVAR_TABELA_REAL=sim: faz backup em ibge_cafe_teste.<tabela>_backup_AAAAMMDD,
           troca o conteúdo de ibge_cafe.<tabela> (mesmo esquema, mesmas colunas) e confere as linhas;
           se a contagem não bater, devolve o backup.

Credencial: a do ambiente (GOOGLE_APPLICATION_CREDENTIALS ou a do google-github-actions/auth).
O Looker Studio continua apontando para ibge_cafe.<tabela>; nada muda nele.

Uso:
  python scripts/bigquery.py baixar --tabela base_dashboard_3 --arquivo dados/bigquery/base_dashboard_3.csv.gz
  python scripts/bigquery.py teste  --tabela base_dashboard_3 --arquivo dados/bigquery/base_dashboard_3.novo.csv.gz
  GRAVAR_TABELA_REAL=sim python scripts/bigquery.py real --tabela base_dashboard_3 --arquivo ...
"""
import argparse
import os
import sys
from datetime import date

import pandas as pd
from google.cloud import bigquery

PROJETO = os.environ.get("BQ_PROJETO", "embrapa-cafe")
DATASET = os.environ.get("BQ_DATASET", "ibge_cafe")
TESTE = os.environ.get("BQ_DATASET_TESTE", "ibge_cafe_teste")
LOCAL = "southamerica-east1"


def ler(arquivo, esquema):
    if arquivo.endswith(".parquet"):
        d = pd.read_parquet(arquivo)
    else:
        tipos = {c.name: str for c in esquema if c.field_type == "STRING"}
        d = pd.read_csv(arquivo, dtype=tipos, keep_default_na=True)
    for c in esquema:
        if c.field_type == "STRING":
            d[c.name] = d[c.name].astype("string")
        elif c.field_type in ("FLOAT", "FLOAT64"):
            d[c.name] = pd.to_numeric(d[c.name], errors="coerce").astype("float64")
        elif c.field_type in ("INTEGER", "INT64"):
            d[c.name] = pd.to_numeric(d[c.name], errors="coerce").astype("Int64")
        elif c.field_type in ("BOOLEAN", "BOOL"):
            d[c.name] = d[c.name].astype("boolean")
    return d[[c.name for c in esquema]]


def gravar(cli, d, destino, esquema):
    cfg = bigquery.LoadJobConfig(schema=esquema, write_disposition="WRITE_TRUNCATE")
    cli.load_table_from_dataframe(d, destino, job_config=cfg, location=LOCAL).result()
    return cli.get_table(destino).num_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("acao", choices=["baixar", "teste", "real"])
    ap.add_argument("--tabela", required=True)
    ap.add_argument("--arquivo", required=True)
    a = ap.parse_args()
    cli = bigquery.Client(project=PROJETO, location=LOCAL)
    oficial = f"{PROJETO}.{DATASET}.{a.tabela}"
    esquema = cli.get_table(oficial).schema

    if a.acao == "baixar":
        origem = oficial
        # Enquanto a tabela oficial não recebe as atualizações, a versão de teste é a mais completa:
        # parte dela (se ainda existir e não tiver menos linhas que a oficial) para não perder o que já foi acrescentado.
        if os.environ.get("GRAVAR_TABELA_REAL") != "sim":
            try:
                t = cli.get_table(f"{PROJETO}.{TESTE}.{a.tabela}_teste")
                if t.num_rows >= cli.get_table(oficial).num_rows:
                    origem = f"{PROJETO}.{TESTE}.{a.tabela}_teste"
            except Exception:
                pass
        d = cli.query(f"SELECT * FROM `{origem}`").to_dataframe()
        if a.arquivo.endswith(".parquet"):
            d.to_parquet(a.arquivo, index=False)
        else:
            d.to_csv(a.arquivo, index=False)
        print(f"{origem}: {len(d)} linhas -> {a.arquivo}")
        return

    d = ler(a.arquivo, esquema)
    antes = cli.get_table(oficial).num_rows
    if len(d) < antes:
        print(f"::error::o arquivo tem {len(d)} linhas e a tabela oficial {antes}; nada foi gravado")
        sys.exit(1)

    if a.acao == "teste":
        n = gravar(cli, d, f"{PROJETO}.{TESTE}.{a.tabela}_teste", esquema)
        print(f"{TESTE}.{a.tabela}_teste: {n} linhas (oficial tem {antes})")
        return

    if os.environ.get("GRAVAR_TABELA_REAL") != "sim":
        print("::warning::GRAVAR_TABELA_REAL não está ligado; a tabela oficial não foi tocada")
        return
    backup = f"{PROJETO}.{TESTE}.{a.tabela}_backup_{date.today():%Y%m%d}"
    cli.copy_table(oficial, backup, job_config=bigquery.CopyJobConfig(write_disposition="WRITE_TRUNCATE"),
                   location=LOCAL).result()
    print(f"backup: {backup} ({cli.get_table(backup).num_rows} linhas)")
    n = gravar(cli, d, oficial, esquema)
    if n != len(d):
        cli.copy_table(backup, oficial, job_config=bigquery.CopyJobConfig(write_disposition="WRITE_TRUNCATE"),
                       location=LOCAL).result()
        print(f"::error::a tabela oficial ficou com {n} linhas em vez de {len(d)}; o backup foi devolvido")
        sys.exit(1)
    print(f"{oficial}: {antes} -> {n} linhas")


if __name__ == "__main__":
    main()
