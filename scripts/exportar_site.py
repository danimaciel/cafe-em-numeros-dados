"""Gera os arquivos da página própria (site/dados/*.json) a partir das tabelas conferidas.

Roda a partir da raiz do repositório:  python scripts/exportar_site.py

Entradas (atualizadas todo mês pela Action):
  dados/bigquery/base_dashboard_3.csv.gz   PAM 5457, 2014 até o ano mais novo (mesmo esquema do BigQuery)
  dados/bigquery/autores.parquet           publicação × palavra-chave × autor (mesmo esquema do BigQuery)
Entradas fixas (não mudam até o próximo Censo ou nova portaria do ZARC):
  dados/site_fixos/municipios.csv                       código IBGE, nome, UF e coordenadas dos municípios
  dados/site_fixos/uf.geojson                           malha simplificada dos estados
  dados/site_fixos/censo2017_6955_estabelecimentos.csv  Censo Agro 2017, Tabela 6955, por município
  dados/site_fixos/censo2017_6768_produtores.csv.gz     Censo Agro 2017, Tabela 6768, por município, idade e sexo
  dados/site_fixos/zarc.json                            ZARC (valores aproximados até haver a tabela do MAPA)
  dados/site_fixos/solucoes_tecnologicas_cafe.csv       soluções tecnológicas da Embrapa sobre café
Saídas:
  site/dados/pam_uf.json, mun.json, embrapa.json, zarc.json, uf.geojson, meta.json

Regra do painel: nunca mostrar menos. Se a versão nova tiver menos anos, municípios ou publicações do que a
que já está em site/dados/, nada é gravado e o script sai com erro.
"""
import argparse
import gzip
import json
import math
import re
import shutil
import sys
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

CODUF = {11: 'RO', 12: 'AC', 13: 'AM', 14: 'RR', 15: 'PA', 16: 'AP', 17: 'TO', 21: 'MA', 22: 'PI', 23: 'CE',
         24: 'RN', 25: 'PB', 26: 'PE', 27: 'AL', 28: 'SE', 29: 'BA', 31: 'MG', 32: 'ES', 33: 'RJ', 35: 'SP',
         41: 'PR', 42: 'SC', 43: 'RS', 50: 'MS', 51: 'MT', 52: 'GO', 53: 'DF'}
# grafias da PAM que diferem do cadastro de municípios
ALIAS = {'barao de monte alto|MG': 'barao do monte alto|MG', 'sao thome das letras|MG': 'sao tome das letras|MG'}
IND = ['quantidade_produzida', 'area_colhida', 'valor_producao', 'area_plantada',
       'participacao_area_plantada', 'participacao_area_colhida', 'participacao_valor_producao']
IND_MUN = {'quantidade_produzida': 'q', 'area_colhida': 'h', 'area_plantada': 'l', 'valor_producao': 'v',
           'participacao_area_plantada': 'pl', 'participacao_area_colhida': 'ph', 'participacao_valor_producao': 'pv'}
TIPOS = {'Arábica': 'a', 'Canephora': 'c'}
IDADES = ['Menor de 25 anos', 'De 25 a menos de 35 anos', 'De 35 a menos de 45 anos', 'De 45 a menos de 55 anos',
          'De 55 a menos de 65 anos', 'De 65 a menos de 75 anos', 'De 75 anos e mais']
IDADES_CURTAS = ['< 25 anos', '25–35 anos', '35–45 anos', '45–55 anos', '55–65 anos', '65–75 anos', '75+ anos']
TEMAS = {1: 'Qualidade do café e pós-colheita', 2: 'Manejo agronômico e crescimento do cafeeiro',
         3: 'Geografia e análise territorial da cafeicultura', 4: 'Sistemas de produção e desenvolvimento regional',
         5: 'Genética e diversidade do cafeeiro', 6: 'Melhoramento genético e resistência a doenças'}


def norm(s):
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower() \
        .replace("'", '').replace('-', ' ').strip()


def limpo(o):
    if isinstance(o, dict):
        return {k: limpo(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [limpo(v) for v in o]
    if isinstance(o, float) and math.isnan(o):
        return ''
    return o


def gravar(o, p):
    p.write_text(json.dumps(limpo(o), ensure_ascii=False, separators=(',', ':'), allow_nan=False), encoding='utf-8')


def texto(v):
    return v if isinstance(v, str) else ''


# ---------------------------------------------------------------- PAM
def ler_pam(arq):
    d = pd.read_csv(arq, dtype={'sigla_uf': str}, low_memory=False)
    d['ano'] = d.ano.astype(int)
    d = d[d.indicador.isin(IND) & d.tipo_cafe.isin(TIPOS)]
    d['valor'] = pd.to_numeric(d.valor, errors='coerce').fillna(0)
    return d


def pam_uf(d, nomes_uf):
    agg = d[(d.uf == d.municipio) & d.uf.isin(set(nomes_uf) | {'Brasil'})]
    faltam = (set(nomes_uf) | {'Brasil'}) - set(agg.uf)
    if faltam:
        sys.exit(f'PAM sem linhas de: {sorted(faltam)}')
    res = {}
    for (a, l, t), g in agg.groupby(['ano', 'uf', 'tipo_cafe']):
        m = dict(zip(g.indicador, g.valor))
        res.setdefault(str(a), {}).setdefault(l, {})[t] = [float(m.get(k, 0)) for k in IND]
    return {'campos': IND, 'fonte': 'IBGE/SIDRA 5457 (PAM)', 'dados': res}


def pam_municipal(d, M, anos):
    mun = d[(d.uf != d.municipio) & (d.valor > 0)].copy()
    sig = mun.uf.str.extract(r'\((\w\w)\)$')[0]
    mun = mun.assign(k=(mun.municipio.map(norm) + '|' + sig).replace(ALIAS))
    sem = sorted(mun[~mun.k.isin(M)].uf.unique())
    for k, g in mun.groupby('k'):
        if k not in M:
            continue
        p = {}
        for t, tt in TIPOS.items():
            for ind, ii in IND_MUN.items():
                s = g[(g.tipo_cafe == t) & (g.indicador == ind)].groupby('ano').valor.sum()
                arr = [(round(float(s.get(y, 0)), 2) if ii.startswith('p') else int(round(s.get(y, 0)))) for y in anos]
                if any(arr):
                    p[tt + ii] = arr
        if p:
            M[k]['p'] = p
    return sem


# ---------------------------------------------------------------- Censo 2017
def censo(M, fixos):
    C2K = {v['c']: k for k, v in M.items()}
    e = pd.read_csv(fixos / 'censo2017_6955_estabelecimentos.csv')
    for r in e.itertuples():
        k = C2K.get(int(r.cod_mun))
        if k and r.estabelecimentos_cafe_total > 0:
            M[k]['e'] = [int(r.estabelecimentos_arabica), int(r.estabelecimentos_conilon), int(r.estabelecimentos_familiar),
                         int(r.estabelecimentos_nao_familiar), int(r.estabelecimentos_cafe_total)]
    s = pd.read_csv(fixos / 'censo2017_6768_produtores.csv.gz')
    for cod, g in s.groupby('cod_mun'):
        k = C2K.get(int(cod))
        if not k or 'e' not in M[k]:
            continue  # só municípios com estabelecimentos de café, como no painel
        g = g.set_index('classe_idade')
        if 'Total' not in g.index:
            continue
        T = g.loc['Total']
        ee = M[k]['e']
        pm = T.Mulheres / T.Total if T.Total else 0
        M[k]['s'] = dict(t=int(T.Total), h=int(T.Homens), m=int(T.Mulheres),
                         a=[[int(g.loc[a].Homens), int(g.loc[a].Mulheres)] if a in g.index else [0, 0] for a in IDADES],
                         wa=int(math.floor(pm * ee[0] + 0.5)), wc=int(math.floor(pm * ee[1] + 0.5)))  # .5 para cima, como no Looker


# ---------------------------------------------------------------- Embrapa
def embrapa(arq, fixos):
    a = pd.read_parquet(arq)
    a = a[a.id.notna()]
    pa = a.drop_duplicates(['id', 'autoria'])
    u = a.drop_duplicates('id').set_index('id')
    # todas as palavras-chave de cada publicação (a tabela tem uma linha por palavra-chave e autor)
    kw_pub = a.dropna(subset=['palavras_chave']).groupby('id').palavras_chave \
        .agg(lambda s: list(dict.fromkeys(x.strip() for x in s if x.strip())))
    kw = {}
    for ws in kw_pub:
        for w in dict.fromkeys(x.strip('.').capitalize() for x in ws):
            if 2 < len(w) < 40:
                kw[w] = kw.get(w, 0) + 1
    # mesmas contas do painel oficial (Looker): gênero por empregado distinto (id_autor)
    emb = a[a.tipo_autor == 'Autor Embrapa'].drop_duplicates('id_autor')
    u = u.assign(ano=pd.to_numeric(u.ano_de_publicacao, errors='coerce'), tema=pd.to_numeric(u.topicomax, errors='coerce'))
    u = u[u.ano.notna()]
    rec = u.sort_values('ano', ascending=False)
    t = pd.read_csv(fixos / 'solucoes_tecnologicas_cafe.csv')
    temas = u.tema.dropna().astype(int).value_counts().sort_index()
    return {
        'kpi': {'pubs': int(len(u)), 'autores': int(pa.autoria.nunique()),
                'autores_embrapa': int(pa[pa.tipo_autor == 'Autor Embrapa'].autoria.nunique()),
                # como no painel oficial: listas de autores distintas das publicações com autor externo
                'autores_externos': int(a[a.tipo_autor == 'Autor externo'].autores.nunique()),
                # como no painel oficial: autores distintos / publicações
                'media_autores': round(pa.autoria.nunique() / len(u), 2),
                'unidades': int(u.unidade.nunique()), 'ano_max': int(u.ano.max())},
        'anos': {str(int(k)): int(v) for k, v in u.ano.value_counts().sort_index().items()},
        'genero': {'Homem': int((emb.genero == 'Male').sum()), 'Mulher': int((emb.genero == 'Female').sum())},
        'tipos': {k: int(v) for k, v in u.tipo_de_publicacao.value_counts().items()},
        'unidades': {k: int(v) for k, v in u.unidade.value_counts().items()},
        # como no painel oficial: linhas da tabela (publicação x palavra-chave x autor) por autoria
        'autores_top': {k: int(v) for k, v in a.autoria.value_counts().head(12).items()},
        'clusters': [dict(id=int(k), nome=TEMAS.get(int(k), f'Tema {int(k)}'), n=int(v)) for k, v in temas.items()],
        'palavras': sorted(kw.items(), key=lambda z: -z[1])[:45],
        'pubs': [dict(t=texto(r.titulo), a=texto(r.autores), u=texto(r.unidade), y=int(r.ano),
                      c=int(r.tema) if pd.notna(r.tema) else 0, l=texto(r.url_do_arquivo),
                      k='; '.join(kw_pub.get(i, []))) for i, r in rec.iterrows()],
        'tecnologias': [dict(n=texto(r['Nome']), tipo=texto(r['Tipo']), u=texto(r['Unidade responsável']),
                             url=texto(r['Página da tecnologia no Portal Embrapa'])) for _, r in t.iterrows()],
    }


# ---------------------------------------------------------------- conferência
def conferir(novo, pasta):
    """Recusa se a versão nova mostrar menos que a publicada."""
    erros = []
    def antigo(nome):
        p = pasta / nome
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None
    v = antigo('pam_uf.json')
    if v:
        faltam = set(v['dados']) - set(novo['pam_uf.json']['dados'])
        if faltam:
            erros.append(f'pam_uf: anos a menos {sorted(faltam)}')
    v = antigo('mun.json')
    if v:
        n = novo['mun.json']
        if set(v['anos']) - set(n['anos']):
            erros.append(f"mun: anos a menos {sorted(set(v['anos']) - set(n['anos']))}")
        cods = {m['c'] for m in v['mun']} - {m['c'] for m in n['mun']}
        if cods:
            erros.append(f'mun: {len(cods)} municípios a menos')
        for campo in ('p', 'e', 's'):
            a, b = sum(campo in m for m in v['mun']), sum(campo in m for m in n['mun'])
            if b < a:
                erros.append(f'mun: {a - b} municípios a menos com "{campo}"')
    v = antigo('embrapa.json')
    if v and novo['embrapa.json']['kpi']['pubs'] < v['kpi']['pubs']:
        erros.append(f"embrapa: {v['kpi']['pubs']} → {novo['embrapa.json']['kpi']['pubs']} publicações")
    return erros


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pam', default='dados/bigquery/base_dashboard_3.csv.gz')
    ap.add_argument('--autores', default='dados/bigquery/autores.parquet')
    ap.add_argument('--fixos', default='dados/site_fixos')
    ap.add_argument('--saida', default='site/dados')
    ap.add_argument('--forcar', action='store_true', help='grava mesmo se a conferência recusar (não usar na Action)')
    a = ap.parse_args()
    fixos, saida = Path(a.fixos), Path(a.saida)

    geo = json.loads((fixos / 'uf.geojson').read_text(encoding='utf-8'))
    nomes_uf = [f['properties']['uf'] for f in geo['features']]
    mc = pd.read_csv(fixos / 'municipios.csv')
    M = {}
    for r in mc.itertuples():
        k = ALIAS.get(norm(r.nome) + '|' + r.sigla_uf, norm(r.nome) + '|' + r.sigla_uf)
        M[k] = dict(c=int(r.cod_mun), n=r.nome, uf=r.sigla_uf, la=float(r.lat), lo=float(r.lon))

    d = ler_pam(a.pam)
    anos = sorted(int(x) for x in d.ano.unique())
    novo = {'pam_uf.json': pam_uf(d, nomes_uf)}
    sem = pam_municipal(d, M, anos)
    censo(M, fixos)
    out = [v for v in M.values() if 'p' in v or 'e' in v]
    novo['mun.json'] = {
        'anos': anos, 'idades': IDADES_CURTAS,
        'legenda': 'p: PAM por espécie (a=arábica, c=canephora) e indicador (q t, h área colhida ha, l área plantada ha, '
                   'v valor mil R$, pl/ph/pv participação %); e: [estab arábica, canephora, familiar, não familiar, total] '
                   'Censo 2017; s: produtores Censo 2017 (t,h,m, a=idade [h,m], wa/wc mulheres estimadas)',
        'mun': out}
    if Path(a.autores).exists():
        novo['embrapa.json'] = embrapa(a.autores, fixos)
    else:  # repositório público não guarda a tabela de autores; sem credencial do BigQuery, mantém a versão publicada
        print(f'{a.autores} não existe; site/dados/embrapa.json fica como está')
        novo['embrapa.json'] = json.loads((saida / 'embrapa.json').read_text(encoding='utf-8'))

    erros = conferir(novo, saida)
    if erros and not a.forcar:
        print('RECUSADO: a versão nova mostraria menos que a publicada.')
        for e in erros:
            print(' -', e)
        sys.exit(1)

    saida.mkdir(parents=True, exist_ok=True)
    for nome, obj in novo.items():
        gravar(obj, saida / nome)
    shutil.copyfile(fixos / 'uf.geojson', saida / 'uf.geojson')
    shutil.copyfile(fixos / 'zarc.json', saida / 'zarc.json')
    agora = datetime.now(timezone(timedelta(hours=-3)))
    k = novo['embrapa.json']['kpi']
    gravar({'atualizado_em': agora.strftime('%d/%m/%Y'),
            'fontes': {'PAM/IBGE': f'{anos[0]}–{anos[-1]}', 'Censo Agropecuário': '2017',
                       'Publicações Embrapa': f"{k['pubs']:,} publicações até {k['ano_max']}".replace(',', '.')}}, saida / 'meta.json')

    # resumo (vai para o log da Action)
    br = novo['pam_uf.json']['dados'][str(anos[-1])]['Brasil']
    sacas = sum(v[0] for v in br.values()) * 1000 / 60 / 1e6
    print(f'PAM {anos[0]}–{anos[-1]}: Brasil {anos[-1]} = {sacas:.2f} mi sacas')
    print(f"Municípios: {len(out)} (PAM {sum('p' in m for m in out)}, estabelecimentos {sum('e' in m for m in out)}, "
          f"produtores {sum('s' in m for m in out)})")
    if sem:
        print(f'Aviso: {len(sem)} municípios da PAM sem coordenada no cadastro (ficam fora do mapa): {sem[:10]}')
    print(f"Embrapa: {k['pubs']} publicações, {k['autores']} autores, até {k['ano_max']}")
    for e in erros:
        print('Gravado com --forcar apesar de:', e)


if __name__ == '__main__':
    main()
