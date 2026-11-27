"""Resumo do ZARC para a aba Clima, calculado direto nas tabelas oficiais (zarc e zarc_janela) do BigQuery.

As tabelas são grandes (uma linha por município x solo x fase x semana), então as contas são feitas no
BigQuery e só o resumo é baixado. Mesmas contas do painel oficial:
  semanas        % das linhas de cada semana em cada classe de risco
  janelas        % das combinações de zarc_janela em cada tipo de janela
  media_semanas  média de semanas de baixo risco por espécie
  duracao        municípios distintos por duração da janela (semanas) e tipo de janela
  combinacoes    linhas de zarc_janela

Saída: dados/bigquery/zarc_resumo.json (o exportar_site.py usa este arquivo no lugar do aproximado).

Também grava site/dados/zarc_comb.json: uma linha por combinação (município x espécie x fase x solo x manejo)
com a classe de risco das 36 semanas e a janela de zarc_janela, para a página filtrar como o painel oficial.
"""
import json
import os
import unicodedata
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
    rows = q(cli, f"""SELECT CAST(n_semanas_baixo AS INT64) w, categoria_janela c, COUNT(DISTINCT municipio) n
                      FROM {J} WHERE categoria_janela LIKE 'Janela%' GROUP BY 1, 2""")
    rows.sort(key=lambda r: -r["n"])
    top = rows[:9]
    # "Outros": municípios distintos nas demais durações, por tipo de janela
    fora = " OR ".join(f"(CAST(n_semanas_baixo AS INT64) = {r['w']} AND categoria_janela = '{r['c']}')" for r in top)
    outros = {r["c"].replace("Janela ", ""): r["n"] for r in q(cli, f"""SELECT categoria_janela c, COUNT(DISTINCT municipio) n
        FROM {J} WHERE categoria_janela LIKE 'Janela%' AND NOT ({fora}) GROUP BY 1""")}
    duracao = [[str(r["w"]), r["c"].replace("Janela ", ""), r["n"]] for r in top]
    duracao += [["Outros", k, n] for k, n in sorted(outros.items(), key=lambda z: -z[1])]

    comb = q(cli, f"SELECT COUNT(*) n FROM {J}")[0]["n"]
    out = {"aproximado": False, "nota": "Calculado nas tabelas zarc e zarc_janela do BigQuery (as mesmas do painel oficial).",
           "semanas": semanas, "janelas": janelas, "media_semanas": media, "duracao": duracao, "combinacoes": comb}
    Path("dados/bigquery").mkdir(parents=True, exist_ok=True)
    Path("dados/bigquery/zarc_resumo.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print("ZARC:", json.dumps({"janelas": janelas, "media": media, "comb": comb, "s1": semanas[:1], "diag": diag},
                              ensure_ascii=False, default=str))


def sem_acento(t):
    return unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode().lower().strip()


CLASSE_SQL = """CASE
      WHEN LOWER(classe_risco) LIKE '%rec%' OR STARTS_WITH(TRIM(classe_risco), '1') THEN 1
      WHEN LOWER(classe_risco) LIKE '%baix%' OR STARTS_WITH(TRIM(classe_risco), '2') THEN 2
      WHEN LOWER(classe_risco) LIKE '%m_dio%' OR LOWER(classe_risco) LIKE '%medio%' OR STARTS_WITH(TRIM(classe_risco), '3') THEN 3
      WHEN LOWER(classe_risco) LIKE '%alt%' OR STARTS_WITH(TRIM(classe_risco), '4') THEN 4
      ELSE 0 END"""


def centroides():
    """Centro de cada município (Base dos Dados, pública). Se não der, o mapa usa só os que a página já conhece."""
    try:
        cli = bigquery.Client(project=PROJETO, location="US")
        rows = q(cli, """SELECT d.nome n, d.sigla_uf uf, ST_Y(ST_CENTROID(m.geometria)) la, ST_X(ST_CENTROID(m.geometria)) lo
                         FROM `basedosdados.br_bd_diretorios_brasil.municipio` d
                         JOIN `basedosdados.br_geobr_mapas.municipio` m USING (id_municipio)""")
        return {(sem_acento(r["n"]), r["uf"]): (round(r["la"], 3), round(r["lo"], 3)) for r in rows}
    except Exception as e:  # noqa: BLE001
        print("::warning::centroides dos municípios indisponíveis:", str(e)[:200])
        return {}


def combinacoes(cli, saida=Path("site/dados/zarc_comb.json")):
    chave = "uf, municipio, tipo_cafe, fase, solo, outros_manejos"
    sem = q(cli, f"""SELECT {chave}, ARRAY_AGG(STRUCT(s, c, n) ORDER BY s) w FROM (
          SELECT {chave}, CAST(semana AS INT64) s, MAX({CLASSE_SQL}) c, COUNT(*) n FROM {Z}
          GROUP BY {chave}, s) GROUP BY {chave}""")
    jan = q(cli, f"""SELECT {chave}, ANY_VALUE(categoria_janela) cat, ANY_VALUE(CAST(n_semanas_baixo AS INT64)) ns,
                     ANY_VALUE(CAST(semana_inicio_baixo AS INT64)) ini, COUNT(*) n FROM {J} GROUP BY {chave}""")
    k = lambda r: tuple(r[x] for x in ["uf", "municipio", "tipo_cafe", "fase", "solo", "outros_manejos"])
    jd = {k(r): r for r in jan}
    dims = {d: sorted({r[c] or "" for r in sem}) for d, c in
            [("esp", "tipo_cafe"), ("fase", "fase"), ("solo", "solo"), ("man", "outros_manejos")]}
    dims["cat"] = sorted({r["cat"] or "" for r in jan})
    cen = centroides()
    try:
        import csv
        for r in csv.DictReader(open("dados/site_fixos/municipios.csv", encoding="utf-8")):
            cen.setdefault((sem_acento(r.get("nome") or r.get("municipio") or ""), r.get("uf") or r.get("sigla_uf")),
                           (float(r["lat"]), float(r["lon"])))
    except Exception as e:  # noqa: BLE001
        print("::warning::municipios.csv:", str(e)[:120])
    mun, mi = [], {}
    c = []
    dup = sem_jan = 0
    for r in sorted(sem, key=lambda r: (r["uf"] or "", r["municipio"] or "")):
        m = (r["municipio"], r["uf"])
        if m not in mi:
            la, lo = cen.get((sem_acento(r["municipio"]), r["uf"]), (None, None))
            mi[m] = len(mun)
            mun.append([r["municipio"], r["uf"], la, lo])
        w = ["0"] * 36
        for x in r["w"]:
            if x["s"] and 1 <= x["s"] <= 36:
                w[x["s"] - 1] = str(x["c"])
            dup += (x["n"] or 0) > 1
        j = jd.get(k(r))
        sem_jan += j is None
        c.append([mi[m], dims["esp"].index(r["tipo_cafe"] or ""), dims["fase"].index(r["fase"] or ""),
                  dims["solo"].index(r["solo"] or ""), dims["man"].index(r["outros_manejos"] or ""), "".join(w),
                  dims["cat"].index(j["cat"] or "") if j else -1, j["ns"] if j else None])
    out = {"v": 1, "dims": dims, "mun": mun, "c": c, "janela_linhas": sum(r["n"] for r in jan)}
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("ZARC combinações:", json.dumps({"combinacoes": len(c), "municipios": len(mun),
          "com_coordenada": sum(1 for x in mun if x[2] is not None), "semanas_duplicadas": dup,
          "sem_janela": sem_jan, "janela_linhas": out["janela_linhas"], "dims": dims}, ensure_ascii=False))


if __name__ == "__main__":
    main()
    try:
        combinacoes(bigquery.Client(project=PROJETO, location="southamerica-east1"))
    except Exception as e:  # noqa: BLE001
        print("::warning::combinações do ZARC não foram geradas:", str(e)[:300])
