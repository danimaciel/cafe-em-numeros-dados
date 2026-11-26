"""Monta a nova versão da tabela base_dashboard_3 (PAM) a partir da versão atual e da coleta do SIDRA.

Regra do painel: nunca reduzir o que já aparece. Por isso:
  - os anos que já estão na tabela ficam exatamente como estão (as revisões do IBGE só vão para o relatório);
  - só entram anos MAIS NOVOS que o último ano da tabela;
  - a geografia do ano novo segue o molde do último ano (mesmas linhas, mesmos nomes); município novo no IBGE
    entra como linha a mais; município do molde que o IBGE não trouxe fica com valor 0.
  - vazio do IBGE ("..", "...", "X") vira 0, como no resto da tabela; o relatório conta quantos foram.

Entrada:  dados/bigquery/base_dashboard_3.csv.gz (versão atual, igual à do BigQuery)
          dados/ibge/pam_5457_bruto.csv.gz      (saída de coletar_pam.py)
Saída:    dados/bigquery/base_dashboard_3.novo.csv.gz (só se houver ano novo e ele passar nas conferências)
          dados/metadados/pam_relatorio.json
"""
import argparse
import gzip
import json
import sys
from pathlib import Path

import pandas as pd

UF = {  # código: (sigla, nome, região)
    "11": ("RO", "Rondônia", "Norte"), "12": ("AC", "Acre", "Norte"), "13": ("AM", "Amazonas", "Norte"),
    "14": ("RR", "Roraima", "Norte"), "15": ("PA", "Pará", "Norte"), "16": ("AP", "Amapá", "Norte"),
    "17": ("TO", "Tocantins", "Norte"), "21": ("MA", "Maranhão", "Nordeste"), "22": ("PI", "Piauí", "Nordeste"),
    "23": ("CE", "Ceará", "Nordeste"), "24": ("RN", "Rio Grande do Norte", "Nordeste"),
    "25": ("PB", "Paraíba", "Nordeste"), "26": ("PE", "Pernambuco", "Nordeste"), "27": ("AL", "Alagoas", "Nordeste"),
    "28": ("SE", "Sergipe", "Nordeste"), "29": ("BA", "Bahia", "Nordeste"), "31": ("MG", "Minas Gerais", "Sudeste"),
    "32": ("ES", "Espírito Santo", "Sudeste"), "33": ("RJ", "Rio de Janeiro", "Sudeste"),
    "35": ("SP", "São Paulo", "Sudeste"), "41": ("PR", "Paraná", "Sul"), "42": ("SC", "Santa Catarina", "Sul"),
    "43": ("RS", "Rio Grande do Sul", "Sul"), "50": ("MS", "Mato Grosso do Sul", "Centro-Oeste"),
    "51": ("MT", "Mato Grosso", "Centro-Oeste"), "52": ("GO", "Goiás", "Centro-Oeste"),
    "53": ("DF", "Distrito Federal", "Centro-Oeste"),
}
COLUNAS = ["ano", "uf", "tipo_cafe", "indicador", "unidade", "valor", "municipio", "sigla_uf", "estado", "regiao",
           "localizacao"]
GEO = ["uf", "municipio", "sigla_uf", "estado", "regiao", "localizacao"]


def chave_sidra(linha):
    """Nome do território no formato da coluna `uf` da tabela: 'Cruzília (MG)', 'Minas Gerais', 'Brasil'."""
    if linha.nivel != "municipio":
        return linha.territorio
    nome, sigla = linha.territorio.rsplit(" - ", 1)
    return f"{nome} ({sigla})"


def geo_nova(linha):
    if linha.nivel != "municipio":
        n = linha.territorio
        return dict(uf=n, municipio=n, sigla_uf=None, estado=None, regiao=None, localizacao=f"{n}, NA, Brasil")
    nome, sigla = linha.territorio.rsplit(" - ", 1)
    s, estado, regiao = UF[str(linha.cod_territorio)[:2]]
    return dict(uf=f"{nome} ({sigla})", municipio=nome, sigla_uf=s, estado=estado, regiao=regiao,
                localizacao=f"{nome}, {s}, Brasil")


def ler_csv(p):
    return pd.read_csv(p, dtype={"ano": str, "sigla_uf": str}, keep_default_na=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atual", default="dados/bigquery/base_dashboard_3.csv.gz")
    ap.add_argument("--coleta", default="dados/ibge/pam_5457_bruto.csv.gz")
    ap.add_argument("--saida", default="dados/bigquery/base_dashboard_3.novo.csv.gz")
    ap.add_argument("--relatorio", default="dados/metadados/pam_relatorio.json")
    ap.add_argument("--min-municipios", type=float, default=0.95,
                    help="fração mínima de municípios do molde que o IBGE precisa trazer (resposta parcial = recusa)")
    a = ap.parse_args()

    atual = ler_csv(a.atual)
    bruto = pd.read_csv(a.coleta, dtype={"cod_territorio": str})
    bruto["chave"] = bruto.apply(chave_sidra, axis=1)
    anos_atuais = sorted(int(x) for x in atual.ano.unique())
    ultimo = anos_atuais[-1]
    rel = {"anos_na_tabela": [anos_atuais[0], ultimo], "anos_coletados": sorted(int(x) for x in bruto.ano.unique())}

    # 1) revisões do IBGE nos anos que já estão na tabela (só relatório; nada muda)
    revis = []
    comum = bruto[bruto.ano.isin(anos_atuais) & bruto.nivel.isin(["brasil", "uf"])]
    if not comum.empty:
        m = comum.merge(atual.assign(ano=atual.ano.astype(int)), left_on=["ano", "chave", "tipo_cafe", "indicador"],
                        right_on=["ano", "uf", "tipo_cafe", "indicador"], how="inner")
        m["dif"] = m.valor_x.fillna(0) - m.valor_y.fillna(0)
        dif = m[m.dif.abs() > 0.011]
        for r in dif[dif.indicador == "quantidade_produzida"].itertuples():
            revis.append({"ano": int(r.ano), "local": r.chave, "tipo": r.tipo_cafe, "tabela": r.valor_y,
                          "ibge": r.valor_x})
        rel["revisoes_ibge_nao_aplicadas"] = {"valores_diferentes": int(len(dif)), "producao": revis[:60]}

    # 2) anos novos
    novos = sorted(int(x) for x in bruto.ano.unique() if int(x) > ultimo)
    rel["anos_novos"] = novos
    if not novos:
        rel["situacao"] = "sem ano novo no IBGE; tabela mantida"
        salvar(rel, a.relatorio)
        print(rel["situacao"])
        return

    molde = atual[atual.ano == str(ultimo)]
    unidade = molde.drop_duplicates("indicador").set_index("indicador").unidade.to_dict()
    geo_molde = molde.drop_duplicates("uf").set_index("uf")[GEO[1:]]
    blocos, problemas = [], []
    for ano in novos:
        b = bruto[bruto.ano == ano]
        vazios = int(b.valor.isna().sum())
        sig = int(b.sigilo.sum())
        val = b.set_index(["chave", "tipo_cafe", "indicador"]).valor
        # linhas do molde
        n = molde[["uf", "tipo_cafe", "indicador"] + GEO[1:]].copy()
        n["valor"] = [val.get((u, t, i)) for u, t, i in zip(n.uf, n.tipo_cafe, n.indicador)]
        sem_ibge = int(n.valor.isna().sum())
        n["valor"] = n.valor.fillna(0.0)
        # municípios novos no IBGE (fora do molde)
        extra = b[~b.chave.isin(geo_molde.index)]
        ext_rows = []
        for r in extra.itertuples():
            g = geo_nova(r)
            ext_rows.append(dict(g, tipo_cafe=r.tipo_cafe, indicador=r.indicador,
                                 valor=0.0 if pd.isna(r.valor) else float(r.valor)))
        n = pd.concat([n, pd.DataFrame(ext_rows)], ignore_index=True) if ext_rows else n
        n["ano"] = str(ano)
        n["unidade"] = n.indicador.map(unidade)
        blocos.append(n[COLUNAS])

        # conferências do ano novo
        br = b[(b.nivel == "brasil") & (b.indicador == "quantidade_produzida")].set_index("tipo_cafe").valor
        ufs = b[(b.nivel == "uf") & (b.indicador == "quantidade_produzida")].groupby("tipo_cafe").valor.sum()
        for t in ["Arábica", "Canephora"]:
            if not (br.get(t, 0) or 0) > 0:
                problemas.append(f"{ano}: produção Brasil de {t} vazia ou zero")
            elif abs(ufs.get(t, 0) - br[t]) > 0.005 * br[t]:
                problemas.append(f"{ano}: soma das UFs de {t} ({ufs.get(t, 0):,.0f}) não bate com Brasil ({br[t]:,.0f})")
        n_mun = b[b.nivel == "municipio"].chave.nunique()
        n_mun_molde = molde[molde.sigla_uf.notna()].uf.nunique()
        if n_mun < a.min_municipios * n_mun_molde:
            problemas.append(f"{ano}: IBGE trouxe {n_mun} municípios, o molde tem {n_mun_molde}")
        rel[str(ano)] = {
            "linhas": int(len(n)), "municipios_ibge": int(n_mun), "municipios_novos_fora_do_molde":
                sorted(extra.chave.unique().tolist()), "linhas_do_molde_sem_valor_no_ibge": sem_ibge,
            "valores_vazios_ibge_viraram_0": vazios, "valores_em_sigilo": sig,
            "producao_brasil_t": {t: float(br.get(t, 0) or 0) for t in ["Arábica", "Canephora"]},
        }

    if problemas:
        rel["situacao"] = "ano novo recusado; tabela mantida"
        rel["problemas"] = problemas
        salvar(rel, a.relatorio)
        for p in problemas:
            print(f"::error::{p}")
        sys.exit(1)

    nova = pd.concat([atual[COLUNAS]] + blocos, ignore_index=True)
    saida = Path(a.saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(saida, "wt", encoding="utf-8", newline="") as f:
        nova.to_csv(f, index=False)
    rel["situacao"] = f"anos acrescentados: {novos}"
    rel["linhas"] = {"antes": int(len(atual)), "depois": int(len(nova))}
    salvar(rel, a.relatorio)
    print(rel["situacao"], rel["linhas"])


def salvar(rel, p):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(rel, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
