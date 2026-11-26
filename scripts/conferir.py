"""Conferência final antes de trocar uma tabela: a versão nova não pode mostrar MENOS que a atual.

Falha (código 1) se:
  - a versão nova tiver menos linhas que a atual;
  - faltar alguma linha da versão atual (pela chave);
  - algum valor da versão atual tiver mudado;
  - algum ano/categoria (coluna de grupo) tiver menos linhas que antes;
  - colunas tiverem sumido ou mudado de ordem (o Looker Studio depende delas).

Uso:
  python scripts/conferir.py --atual A.csv.gz --nova B.csv.gz --chave ano,uf,tipo_cafe,indicador --valor valor --grupo ano
  python scripts/conferir.py --atual A.parquet --nova B.parquet --chave id,palavras_chave,autoria --grupo ano_de_publicacao
"""
import argparse
import json
import sys

import pandas as pd


def ler(p):
    if p.endswith(".parquet"):
        return pd.read_parquet(p)
    return pd.read_csv(p, dtype=str, keep_default_na=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--atual", required=True)
    ap.add_argument("--nova", required=True)
    ap.add_argument("--chave", required=True)
    ap.add_argument("--valor", default="")
    ap.add_argument("--grupo", default="")
    ap.add_argument("--saida", help="json com o resultado")
    a = ap.parse_args()
    chave = a.chave.split(",")
    at, nv = ler(a.atual), ler(a.nova)
    erros, info = [], {"linhas_atual": len(at), "linhas_nova": len(nv)}

    if list(nv.columns) != list(at.columns):
        erros.append(f"colunas mudaram: {list(at.columns)} -> {list(nv.columns)}")
    if len(nv) < len(at):
        erros.append(f"a versão nova tem menos linhas ({len(nv)}) que a atual ({len(at)})")
    for c in chave:
        at[c] = at[c].astype(str)
        nv[c] = nv[c].astype(str)
    m = at.merge(nv, on=chave, how="left", suffixes=("", "__novo"), indicator=True)
    sumiram = m[m._merge == "left_only"]
    if len(sumiram):
        erros.append(f"{len(sumiram)} linhas da versão atual sumiram (ex.: {sumiram[chave].head(3).to_dict('records')})")
    if a.valor:
        b = m[m._merge == "both"]
        x = pd.to_numeric(b[a.valor], errors="coerce").fillna(0)
        y = pd.to_numeric(b[a.valor + "__novo"], errors="coerce").fillna(0)
        mud = b[(x - y).abs() > 1e-9]
        if len(mud):
            erros.append(f"{len(mud)} valores da versão atual mudaram (ex.: {mud[chave].head(3).to_dict('records')})")
    if a.grupo:
        ga = at[a.grupo].astype(str).value_counts()
        gn = nv[a.grupo].astype(str).value_counts()
        menor = {k: (int(v), int(gn.get(k, 0))) for k, v in ga.items() if gn.get(k, 0) < v}
        if menor:
            erros.append(f"grupos ({a.grupo}) com menos linhas: {dict(list(menor.items())[:10])}")
        info["grupos_novos"] = sorted(set(gn.index) - set(ga.index))
    info["linhas_acrescentadas"] = len(nv) - len(at)
    info["erros"] = erros
    if a.saida:
        with open(a.saida, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
    for e in erros:
        print(f"::error::{e}")
    if erros:
        sys.exit(1)
    print(f"ok: {info['linhas_acrescentadas']} linhas acrescentadas, nada removido nem alterado; "
          f"novos {a.grupo or 'grupos'}: {info.get('grupos_novos', [])}")


if __name__ == "__main__":
    main()
