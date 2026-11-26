"""Resumo do ZARC para a aba Clima, calculado direto nas tabelas oficiais (zarc e zarc_janela) do BigQuery.

As tabelas são grandes (uma linha por município x solo x fase x semana), então as contas são feitas no
BigQuery e só o resumo é baixado. Mesmas contas do painel oficial:
  semanas        % das linhas de cada semana em cada classe de risco
  janelas        % das combinações de zarc_janela em cada tipo de janela
  media_semanas  média de semanas de baixo risco por espécie
  duracao        municípios distintos por duração da janela (semanas) e tipo de janela
  combinacoes    linhas de zarc_janela

Saída: dados/bigquery/zarc_resumo.json (o exportar_site.py usa este arquivo no lugar do aproximado).
"""
import json
import os
import re
from pathlib import Path

from google.cloud import bigquery

PROJETO = os.environ.get("BQ_PROJETO", "embrapa-cafe")
DATASET = os.environ.get("BQ_DATASET", "ibge_cafe")
Z = f"`{PROJETO}.{DATASET}.zarc`"
J = f"`{PROJETO}.{DATASET}.zarc_janela`"


def q(cli, sql):
    return [dict(r) for r in cli.query(sql).result()]


def main():
    cli = bigquery.Client(project=PROJETO, location="southamerica-east1")
    diag = {}

    # semanas x classe
    rows = q(cli, f"SELECT CAST(semana AS INT64) s, classe_risco c, COUNT(*) n FROM {Z} GROUP BY 1, 2")
    diag["classes"] = sorted({r["c"] for r in rows if r["c"] is not None})
    chave = {}
    for c in diag["classes"]:
        t = c.lower()
        m = re.match(r"\s*(\d)", t)
        d = m.group(1) if m else ""
        t = t.replace("_", " ")
        chave[c] = ("nr" if d == "1" or "não rec" in t or "nao rec" in t else
                    "baixo" if d == "2" or "baixo" in t else
                    "medio" if d == "3" or "médio" in t or "medio" in t else
                    "alto" if d == "4" or "alto" in t else None)
    por_s = {}
    for r in rows:
        if r["s"] is None or chave.get(r["c"]) is None:
            continue
        por_s.setdefault(r["s"], {"nr": 0, "baixo": 0, "medio": 0, "alto": 0})[chave[r["c"]]] += r["n"]
    semanas = []
    for s in sorted(por_s):
        v = por_s[s]
        tot = sum(v.values()) or 1
        semanas.append({"s": s, **{k: round(x / tot * 100, 1) for k, x in v.items()}})

    # janelas
    rows = q(cli, f"SELECT categoria_janela c, COUNT(*) n FROM {J} GROUP BY 1")
    tot = sum(r["n"] for r in rows)
    diag["categorias_janela"] = {str(r["c"]): r["n"] for r in rows}
    janelas = {r["c"]: round(r["n"] / tot * 100, 1) for r in rows if r["c"] and r["c"].lower().startswith("janela")}

    # média de semanas por espécie (as duas leituras vão para o diagnóstico)
    rows = q(cli, f"""SELECT tipo_cafe t, AVG(n_semanas_baixo) m, AVG(IF(n_semanas_baixo > 0, n_semanas_baixo, NULL)) m_pos
                      FROM {J} GROUP BY 1""")
    diag["media"] = {r["t"]: [r["m"], r["m_pos"]] for r in rows}
    nome = lambda t: "Arábica" if "rab" in t.lower() else "Canephora" if ("aneph" in t.lower() or "conil" in t.lower()) else t
    media = {nome(r["t"]): round(r["m"], 2) for r in rows if r["t"]}

    # duração: municípios distintos por semanas de baixo risco e tipo de janela
    rows = q(cli, f"""SELECT CAST(n_semanas_baixo AS INT64) w, categoria_janela c, COUNT(DISTINCT municipio_uf) n
                      FROM {J} WHERE categoria_janela LIKE 'Janela%' GROUP BY 1, 2""")
    rows.sort(key=lambda r: -r["n"])
    top, outros = rows[:9], {}
    for r in rows[9:]:
        k = r["c"].replace("Janela ", "")
        outros[k] = outros.get(k, 0) + r["n"]
    duracao = [[str(r["w"]), r["c"].replace("Janela ", ""), r["n"]] for r in top]
    duracao += [["Outros", k, n] for k, n in sorted(outros.items(), key=lambda z: -z[1])]

    comb = q(cli, f"SELECT COUNT(*) n FROM {J}")[0]["n"]
    out = {"aproximado": False, "nota": "Calculado nas tabelas zarc e zarc_janela do BigQuery (as mesmas do painel oficial).",
           "semanas": semanas, "janelas": janelas, "media_semanas": media, "duracao": duracao, "combinacoes": comb}
    Path("dados/bigquery").mkdir(parents=True, exist_ok=True)
    Path("dados/bigquery/zarc_resumo.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("ZARC:", json.dumps({"janelas": janelas, "media": media, "comb": comb, "s1": semanas[:1], "diag": diag},
                              ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
