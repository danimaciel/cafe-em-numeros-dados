"""Acrescenta à tabela `autores` (aba Café na Embrapa) as publicações sobre café que ainda não estão nela.

Fonte: exportação mensal de publicações do Redape (coletada por coletar_redape.py).
Regra do painel: nunca reduzir. As publicações que já estão na tabela ficam exatamente como estão; só entram
publicações novas (ids que a tabela ainda não tem).

Como cada coluna é preenchida para as publicações novas:
  relacionado_cafe  título + resumo + palavras-chave têm uma das palavras do dicionário do café
                    (mesma regra do cafe2_tudo.R: cafe, café, coffee, coffea, cafeicultura, cafeeiro, cafeeira, torra)
  score_cafe        soma do tf-idf das palavras do dicionário no texto (aproximação do cafe2_tudo.R)
  texto             título + resumo + palavras-chave em minúsculas, sem acento e sem palavras vazias
  decada            ano arredondado para baixo na dezena
  topicomax         tema (1 a 6) do grupo mais parecido: centróide de bigramas tf-idf das publicações que já
                    têm tema (o script original de agrupamento não está na pasta; ver --avaliar)
  autoria...        uma linha por publicação x palavra-chave x autor, como na tabela; os dados do autor da Embrapa
                    (id_autor, nome, afiliação, matrícula, ativado, gênero) vêm da própria tabela atual ou do
                    arquivo de pessoas atualizado (--pessoas), quando existir. Sem correspondência = Autor externo.

Uso:
  python scripts/atualizar_autores.py --redape dados/redape/AAAA-MM/publicacoes-da-embrapa-AAAA-MM.csv \
      --atual dados/bigquery/autores.parquet --saida dados/bigquery/autores.novo.parquet
  python scripts/atualizar_autores.py ... --avaliar 150    # tira 150 publicações e confere se o script as refaz
"""
import argparse
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

DIC_CAFE = {"cafe", "café", "coffee", "coffea", "cafeicultura", "cafeeiro", "cafeeira", "torra"}
VAZIAS = set("""a ao aos aquela aquelas aquele aqueles as com como da das de delas deles depois do dos e ela elas ele
eles em entre era eram essa essas esse esses esta estas estava estavam este esteja estejam estes esteve estiveram foi
for fora foram forem fossem haja houve isso isto mais mas mesmo muito na nao nas nem no nos nosso nossos num numa o
objetivo objetivou os ou para pela pelas pelo pelos pode por qual quando que quem se seja sejam sem ser seria seriam
seu seus sua suas tem tenha tenham teve tinha tiveram tivessem trabalho um uma""".split())
COLS_PESSOA = ["id_autor", "nome", "afiliacao", "matricula", "ativado", "primeiro_nome", "genero"]


def sem_acento(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def palavras(s):
    return re.findall(r"\w+", s.lower())


def eh_cafe(t):
    return any(w in DIC_CAFE for w in palavras(t))


def bigramas(texto):
    w = [x for x in re.findall(r"[a-z]+", sem_acento(texto.lower())) if x not in VAZIAS and len(x) > 2]
    return [a + " " + b for a, b in zip(w, w[1:])]


def ler_redape(p):
    r = pd.read_csv(p, dtype=str, keep_default_na=False)
    r = r.rename(columns={"ID": "id", "Título": "titulo", "Resumo": "resumo", "Ano de publicação": "ano",
                          "Tipo de publicação": "tipo_de_publicacao", "Unidade": "unidade", "Autores": "autores",
                          "Palavras-chave": "palavras_chave", "URL do arquivo": "url_do_arquivo"})
    r["bruto"] = r.titulo + " " + r.resumo + " " + r.palavras_chave
    return r


class Temas:
    """Centróides de bigramas tf-idf por tema, a partir das publicações que já têm tema."""

    def __init__(self, textos, temas):
        docs = [Counter(bigramas(t)) for t in textos]
        df = Counter(b for d in docs for b in d)
        n = len(docs)
        self.idf = {b: math.log(n / c) for b, c in df.items() if c >= 3}
        soma = defaultdict(Counter)
        for d, t in zip(docs, temas):
            for b, v in self.vetor(d).items():
                soma[t][b] += v
        self.cent = {t: self.norm(v) for t, v in soma.items()}

    def vetor(self, d):
        tot = sum(d.values()) or 1
        return self.norm({b: c / tot * self.idf[b] for b, c in d.items() if b in self.idf})

    @staticmethod
    def norm(v):
        s = math.sqrt(sum(x * x for x in v.values())) or 1
        return {k: x / s for k, x in v.items()}

    def prever(self, texto, padrao):
        v = self.vetor(Counter(bigramas(texto)))
        if not v:
            return padrao
        return max(self.cent, key=lambda t: sum(x * self.cent[t].get(b, 0) for b, x in v.items()))


def score_cafe(textos_corpus, texto):
    """tf-idf das palavras do dicionário, com idf calculado sobre o corpus completo do Redape."""
    w = palavras(texto)
    if not w:
        return 0.0
    c = Counter(w)
    return sum(c[p] / len(w) * textos_corpus[p] for p in DIC_CAFE if p in c and p in textos_corpus)


def montar(novas, atual, temas, idf_cafe, pessoas):
    tema_padrao = int(atual.topicomax.mode().iloc[0])
    linhas = []
    for r in novas.itertuples():
        kws = [k.strip() for k in r.palavras_chave.split(",") if k.strip()] or [None]
        aut = [a.strip() for a in r.autores.split(";") if a.strip()] or [None]
        ano = float(r.ano) if r.ano.strip().isdigit() else None
        texto = " ".join(w for w in sem_acento(r.bruto.lower()).split() if w not in VAZIAS)
        base = dict(id=r.id, score_cafe=round(score_cafe(idf_cafe, r.bruto), 6), titulo=r.titulo,
                    autores=r.autores, ano_de_publicacao=ano, url_do_arquivo=r.url_do_arquivo or None,
                    tipo_de_publicacao=r.tipo_de_publicacao, unidade=r.unidade, texto=texto,
                    relacionado_cafe=True, decada=(math.floor(ano / 10) * 10 if ano else None),
                    topicomax=float(temas.prever(r.bruto, tema_padrao)))
        for k in kws:
            for a in aut:
                ps = pessoas.get(a) if a else None
                for p in (ps or [None]):
                    linha = dict(base, palavras_chave=k, autoria=a)
                    if p:
                        linha.update(p, tipo_autor="Autor Embrapa")
                    else:
                        linha.update({c: None for c in COLS_PESSOA}, tipo_autor="Autor externo")
                    linhas.append(linha)
    return pd.DataFrame(linhas, columns=list(atual.columns)).astype(atual.dtypes.to_dict(), errors="ignore")


def mapa_pessoas(atual, arquivo=None):
    emb = atual[atual.tipo_autor == "Autor Embrapa"][["autoria"] + COLS_PESSOA].drop_duplicates()
    if arquivo and Path(arquivo).exists():
        extra = pd.read_csv(arquivo, dtype=str)
        extra = extra[[c for c in ["autoria"] + COLS_PESSOA if c in extra.columns]]
        emb = pd.concat([emb, extra[~extra.autoria.isin(emb.autoria)]], ignore_index=True)
    m = defaultdict(list)
    for r in emb.to_dict("records"):
        a = r.pop("autoria")
        m[a].append(r)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--redape", required=True)
    ap.add_argument("--atual", default="dados/bigquery/autores.parquet")
    ap.add_argument("--saida", default="dados/bigquery/autores.novo.parquet")
    ap.add_argument("--pessoas", default="dados/embrapa/pessoas_autoria.csv",
                    help="autoria -> dados do empregado (gerado pelo fluxo autores_folha); opcional")
    ap.add_argument("--relatorio", default="dados/metadados/autores_relatorio.json")
    ap.add_argument("--avaliar", type=int, default=0)
    a = ap.parse_args()

    atual = pd.read_parquet(a.atual)
    red = ler_redape(a.redape)
    red["cafe"] = red.bruto.map(eh_cafe)
    n = len(red)
    df = Counter(p for t in red.bruto for p in set(palavras(t)) if p in DIC_CAFE)
    idf_cafe = {p: math.log(n / c) for p, c in df.items()}

    if a.avaliar:
        avaliar(a.avaliar, atual, red, idf_cafe, mapa_pessoas(atual))
        return

    ja = set(atual.id.astype(str))
    novas = red[red.cafe & ~red.id.isin(ja)]
    sumiram = sorted(ja - set(red.id))
    rel = {"redape": a.redape, "registros_redape": n, "publicacoes_cafe_redape": int(red.cafe.sum()),
           "publicacoes_na_tabela": len(ja), "publicacoes_novas": int(len(novas)),
           "na_tabela_mas_fora_do_redape": len(sumiram), "exemplos_fora_do_redape": sumiram[:20]}
    if len(sumiram) > 0.05 * len(ja):
        rel["situacao"] = "exportação do Redape parece incompleta; tabela mantida"
        salvar(rel, a.relatorio)
        print(f"::error::{len(sumiram)} publicações da tabela não estão no Redape; exportação suspeita")
        sys.exit(1)
    if novas.empty:
        rel["situacao"] = "sem publicação nova; tabela mantida"
        salvar(rel, a.relatorio)
        print(rel["situacao"])
        return
    temas = Temas(*zip(*[(t, int(x)) for t, x in atual.drop_duplicates("id").merge(
        red[["id", "bruto"]], on="id")[["bruto", "topicomax"]].itertuples(index=False)]))
    nov = montar(novas, atual, temas, idf_cafe, mapa_pessoas(atual, a.pessoas))
    nova = pd.concat([atual, nov], ignore_index=True)
    Path(a.saida).parent.mkdir(parents=True, exist_ok=True)
    nova.to_parquet(a.saida, index=False)
    rel.update(situacao=f"{len(novas)} publicações acrescentadas", linhas={"antes": len(atual), "depois": len(nova)},
               por_ano=nov.drop_duplicates("id").ano_de_publicacao.value_counts().sort_index().to_dict(),
               autores_embrapa_novos=int(nov[nov.tipo_autor == "Autor Embrapa"].autoria.nunique()))
    salvar(rel, a.relatorio)
    print(rel["situacao"], rel["linhas"])


def avaliar(k, atual, red, idf_cafe, pessoas):
    """Tira k publicações da tabela, refaz com o script e compara com o que estava lá."""
    ids = atual.id.drop_duplicates().sample(k, random_state=1)
    resto = atual[~atual.id.isin(ids)]
    base = resto.drop_duplicates("id").merge(red[["id", "bruto"]], on="id")
    temas = Temas(base.bruto.tolist(), base.topicomax.astype(int).tolist())
    nov = montar(red[red.id.isin(ids)], resto, temas, idf_cafe, pessoas)
    orig = atual[atual.id.isin(ids)]
    cafe_ok = red[red.id.isin(ids)].cafe.mean()
    t_or = orig.drop_duplicates("id").set_index("id").topicomax
    t_nv = nov.drop_duplicates("id").set_index("id").topicomax
    chave = ["id", "palavras_chave", "autoria"]
    so, sn = orig.groupby("id").size(), nov.groupby("id").size().reindex(orig.groupby("id").size().index)
    lin = (so == sn).mean()
    m = orig.merge(nov, on=chave, suffixes=("", "_n"))
    res = {"publicacoes": k, "reconhecidas_como_cafe": round(cafe_ok, 3),
           "mesmo_numero_de_linhas": round(float(lin), 3),
           "tipo_autor_igual": round(float((m.tipo_autor == m.tipo_autor_n).mean()), 3),
           "dados_do_autor_iguais": round(float(
               (orig.fillna("").groupby(chave)[COLS_PESSOA].apply(lambda g: sorted(map(tuple, g.astype(str).values)))
                == nov.fillna("").groupby(chave)[COLS_PESSOA].apply(lambda g: sorted(map(tuple, g.astype(str).values)))
                ).mean()), 3),
           "tema_igual": round(float((t_or == t_nv.reindex(t_or.index)).mean()), 3)}
    print(json.dumps(res, ensure_ascii=False))


def salvar(rel, p):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(rel, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
