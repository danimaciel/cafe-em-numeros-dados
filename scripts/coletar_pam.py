"""Coleta da PAM (IBGE/SIDRA, tabela 5457) para o café arábica e canephora.

Baixa Brasil, UFs e municípios (por UF, para ficar abaixo do limite de ~100 mil valores da API),
as 8 variáveis que o painel usa, e grava um arquivo longo em dados/ibge/pam_5457_bruto.csv.gz.

Símbolos do IBGE: "-" é zero; "X" é sigilo (fica vazio, marcado em `sigilo`); ".." e "..." ficam vazios.
O valor original fica sempre em `valor_bruto`.

Uso:
  python scripts/coletar_pam.py                 # últimos 3 anos publicados
  python scripts/coletar_pam.py --periodo all   # série inteira
  python scripts/coletar_pam.py --entrada-json pasta/   # lê respostas já salvas (teste sem internet)

Se a API falhar duas vezes seguidas por conexão, desiste e não grava nada (a versão anterior fica).
"""
import argparse
import gzip
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

API = "https://apisidra.ibge.gov.br/values"
CAB = {"User-Agent": "painel-cafe-em-numeros (atualizacao de dados publicos)"}
PRODUTOS = {"40140": "Arábica", "40141": "Canephora"}
VARIAVEIS = {
    "8331": "area_plantada",
    "1008331": "participacao_area_plantada",
    "216": "area_colhida",
    "1000216": "participacao_area_colhida",
    "214": "quantidade_produzida",
    "112": "rendimento_medio",
    "215": "valor_producao",
    "1000215": "participacao_valor_producao",
}
UFS = [11, 12, 13, 14, 15, 16, 17, 21, 22, 23, 24, 25, 26, 27, 28, 29, 31, 32, 33, 35, 41, 42, 43, 50, 51, 52, 53]


class ForaDoAr(Exception):
    pass


def pedir(url, tentativas=4):
    espera = 10
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=CAB)
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError(f"pedido recusado pelo IBGE ({e.code}): {url}") from e
            erro = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            erro = e
        if i < tentativas - 1:
            time.sleep(espera)
            espera *= 2
    raise ForaDoAr(f"{url}: {erro}")


def url(territorio, periodo):
    v = ",".join(VARIAVEIS)
    c = ",".join(PRODUTOS)
    return f"{API}/t/5457/{territorio}/v/{v}/p/{periodo}/c782/{c}?formato=json"


def converter(resposta, nivel):
    """Resposta JSON do SIDRA -> linhas. A primeira linha da resposta são os rótulos."""
    cab = resposta[0]
    # descobre qual Dn é território, variável, ano e produto pelos rótulos
    pos = {}
    for k, rotulo in cab.items():
        if not (k.startswith("D") and k.endswith("N")):
            continue
        r = rotulo.lower()
        if r.startswith("variável"):
            pos["var"] = k[:-1]
        elif r.startswith("ano"):
            pos["ano"] = k[:-1]
        elif "produto" in r:
            pos["prod"] = k[:-1]
        elif "brasil" in r or "unidade da federação" in r or "município" in r:
            pos["terr"] = k[:-1]
    linhas = []
    for r in resposta[1:]:
        bruto = r["V"]
        sigilo = bruto.strip() == "X"
        if bruto.strip() == "-":
            valor = 0.0
        else:
            try:
                valor = float(bruto)
            except ValueError:
                valor = None
        linhas.append({
            "nivel": nivel,
            "cod_territorio": r[pos["terr"] + "C"],
            "territorio": r[pos["terr"] + "N"],
            "ano": int(r[pos["ano"] + "C"]),
            "produto_cod": r[pos["prod"] + "C"],
            "tipo_cafe": PRODUTOS.get(r[pos["prod"] + "C"], r[pos["prod"] + "N"]),
            "variavel_cod": r[pos["var"] + "C"],
            "indicador": VARIAVEIS.get(r[pos["var"] + "C"], r[pos["var"] + "N"]),
            "unidade_ibge": r["MN"],
            "valor_bruto": bruto,
            "valor": valor,
            "sigilo": sigilo,
        })
    return linhas


def pedidos(periodo):
    yield "n1", f"n1/all", "brasil"
    yield "n3", f"n3/all", "uf"
    for uf in UFS:
        yield f"n6_{uf}", f"n6/in n3 {uf}", "municipio"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--periodo", default="last 3", help='"last 3", "all", "2025" ou "2024,2025"')
    ap.add_argument("--saida", default="dados/ibge/pam_5457_bruto.csv.gz")
    ap.add_argument("--entrada-json", help="pasta com respostas salvas (nome = chave do pedido + .json)")
    ap.add_argument("--salvar-json", help="pasta para guardar as respostas brutas")
    a = ap.parse_args()

    linhas, falhas_seguidas = [], 0
    for chave, terr, nivel in pedidos(a.periodo):
        try:
            if a.entrada_json:
                arq = Path(a.entrada_json) / f"{chave}.json"
                if not arq.exists():
                    continue
                resp = json.loads(arq.read_text(encoding="utf-8"))
            else:
                resp = pedir(url(terr.replace(" ", "%20"), a.periodo.replace(" ", "%20")))
                time.sleep(1)
            falhas_seguidas = 0
        except ForaDoAr as e:
            falhas_seguidas += 1
            print(f"::warning::IBGE sem resposta em {chave}: {e}")
            if falhas_seguidas >= 2:
                print("::warning::IBGE fora do ar; nada foi gravado e a versão anterior continua valendo.")
                sys.exit(3)
            continue
        if a.salvar_json:
            Path(a.salvar_json).mkdir(parents=True, exist_ok=True)
            (Path(a.salvar_json) / f"{chave}.json").write_text(json.dumps(resp, ensure_ascii=False), encoding="utf-8")
        linhas += converter(resp, nivel)
        print(f"{chave}: {len(resp) - 1} valores")

    d = pd.DataFrame(linhas)
    if d.empty:
        print("::error::a coleta não trouxe nenhum valor")
        sys.exit(2)
    faltam = [u for u in UFS if not d[(d.nivel == "municipio") & d.cod_territorio.str.startswith(str(u))].shape[0]]
    if faltam and not a.entrada_json:
        print(f"::error::faltaram municípios das UFs {faltam}; nada foi gravado")
        sys.exit(2)

    saida = Path(a.saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    tmp = saida.with_suffix(".tmp.gz")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as f:
        d.to_csv(f, index=False)
    tmp.replace(saida)
    anos = sorted(d.ano.unique())
    print(f"PAM 5457: {len(d)} valores, anos {anos[0]}–{anos[-1]}, {d.cod_territorio.nunique()} territórios -> {saida}")


if __name__ == "__main__":
    main()
