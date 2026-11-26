"""Coleta das exportações da Embrapa no Redape: publicações, soluções tecnológicas e projetos.

Detecta a inserção mais recente de cada base pela DATA DE CRIAÇÃO do arquivo no Redape (não pelo nome, que já
veio errado: em 2026-11 os arquivos saíram como -2026-11/-2026-13 e -2026-12), compara com o que o projeto já
coletou (registro em <destino>/redape_coleta.json), baixa com retomada, confere o tamanho informado pelo Redape
e grava com nome padrão <prefixo>-AAAA-MM.csv, sendo AAAA-MM o mês da criação.
Se houver mais de um arquivo do mesmo mês numa base, baixa todos, confere se têm os mesmos ids e fica com o de
menos caracteres invisíveis (espaço não separável, hífen oculto, largura zero); os demais vão para _versoes/.

Só biblioteca padrão do Python (roda em qualquer projeto, sem instalar nada).

Uso:
  python coletar_redape.py verificar --destino data/raw            o que há de novo (não baixa)
  python coletar_redape.py coletar   --destino data/raw            baixa o que for novo
  python coletar_redape.py coletar   --destino data/raw --mes 2026-11 --bases publicacoes,projetos
  python coletar_redape.py coletar   --destino data/raw --forcar   baixa de novo mesmo se já coletado
Saída: <destino>/AAAA-MM/<prefixo>-AAAA-MM.csv, <destino>/AAAA-MM/manifesto.json e <destino>/redape_coleta.json.
"""
import argparse
import csv
import hashlib
import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

URL = "https://www.redape.dados.embrapa.br"
BASES = {
    "publicacoes": ("doi:10.48432/TRBT0S", "publicacoes-da-embrapa"),
    "projetos": ("doi:10.48432/EZDXWF", "projetos-da-embrapa"),
    "solucoes": ("doi:10.48432/ZQE5FV", "solucoes-tecnologicas-da-embrapa"),
}
CAB = {"User-Agent": "coleta-redape (skill embrapa; dados publicos da Embrapa)"}
INVISIVEIS = " ­​‌‍﻿"
csv.field_size_limit(2**31 - 1)


def api(doi):
    req = urllib.request.Request(f"{URL}/api/datasets/:persistentId/?persistentId={doi}", headers=CAB)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["data"]


def inventario(bases):
    """Arquivos de cada base agrupados pelo mês de criação."""
    out = {}
    for b in bases:
        doi, prefixo = BASES[b]
        d = api(doi)
        v = d["latestVersion"]
        por_mes = defaultdict(list)
        for f in v["files"]:
            df = f["dataFile"]
            if not df["filename"].startswith(prefixo):
                continue
            por_mes[df["creationDate"][:7]].append({
                "id": df["id"], "nome_redape": df.get("originalFileName") or df["filename"],
                "tamanho": int(df.get("originalFileSize") or df["filesize"]), "criacao": df["creationDate"],
                "md5_tab": df.get("md5")})
        out[b] = {"versao": f'{v["versionNumber"]}.{v["versionMinorNumber"]}', "liberada": v.get("releaseTime"),
                  "por_mes": dict(por_mes)}
    return out


def baixar(id_arq, tamanho, destino, tentativas=8):
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists() and destino.stat().st_size == tamanho:
        return
    parte = destino.with_suffix(destino.suffix + ".part")
    for i in range(tentativas):
        ja = parte.stat().st_size if parte.exists() else 0
        if ja == tamanho:
            break
        req = urllib.request.Request(f"{URL}/api/access/datafile/{id_arq}?format=original",
                                     headers={**CAB, "Range": f"bytes={ja}-"} if ja else CAB)
        try:
            with urllib.request.urlopen(req, timeout=900) as r, open(parte, "ab" if ja and r.status == 206 else "wb") as f:
                while bloco := r.read(1 << 20):
                    f.write(bloco)
        except Exception as ex:  # retoma de onde parou
            print(f"    tentativa {i + 1} interrompida ({type(ex).__name__}); retomando…", flush=True)
            time.sleep(5 * (i + 1))
    obtido = parte.stat().st_size if parte.exists() else 0
    if obtido != tamanho:
        raise SystemExit(f"Download incompleto de {destino.name}: {obtido} de {tamanho} bytes. Rode de novo para retomar.")
    parte.replace(destino)


def perfil(arq):
    """Linhas, ids (1ª coluna), colunas e caracteres invisíveis de um CSV."""
    with open(arq, encoding="utf-8", newline="") as f:
        texto = f.read()
    linhas = list(csv.reader(texto.splitlines(True)))
    return {"colunas": linhas[0], "linhas": len(linhas) - 1, "ids": {l[0] for l in linhas[1:] if l},
            "invisiveis": sum(texto.count(c) for c in INVISIVEIS),
            "sha1": hashlib.sha1(texto.encode("utf-8")).hexdigest()}


def ler_registro(destino):
    p = destino / "redape_coleta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def verificar(a):
    destino = Path(a.destino)
    inv = inventario(a.bases)
    reg = ler_registro(destino)
    novidade = {}
    for b, x in inv.items():
        mes = a.mes or max(x["por_mes"])
        arqs = x["por_mes"].get(mes, [])
        ja = reg.get(b, {})
        novo = bool(arqs) and (a.forcar or ja.get("mes") != mes or ja.get("ids_redape") != sorted(f["id"] for f in arqs))
        novidade[b] = {"mes": mes, "arquivos": arqs, "novo": novo}
        nomes = ", ".join(f'{f["nome_redape"]} (id {f["id"]}, {f["tamanho"] / 1e6:.1f} MB, criado {f["criacao"]})' for f in arqs)
        aviso = ""
        if any(not f["nome_redape"].removesuffix(".csv").removesuffix(".tab").endswith(mes) for f in arqs):
            aviso = "  ⚠ nome do arquivo não corresponde ao mês de criação"
        if len(arqs) > 1:
            aviso += f"  ⚠ {len(arqs)} arquivos no mesmo mês"
        print(f"{b:12} versão {x['versao']} (liberada {x['liberada'][:10] if x['liberada'] else '?'}) · mês {mes} · "
              f"{'NOVO' if novo else 'já coletado'}\n    {nomes or 'nenhum arquivo nesse mês'}{aviso}")
    meses = {v["mes"] for v in novidade.values()}
    if len(meses) > 1:
        print(f"⚠ As bases estão em meses diferentes: {sorted(meses)}. Confira antes de seguir.")
    return novidade


def coletar(a):
    destino = Path(a.destino)
    novidade = verificar(a)
    reg = ler_registro(destino)
    for b, n in novidade.items():
        if not n["novo"]:
            continue
        mes, prefixo = n["mes"], BASES[b][1]
        pasta = destino / mes
        final = pasta / f"{prefixo}-{mes}.csv"
        cands = []
        for f in n["arquivos"]:
            alvo = final if len(n["arquivos"]) == 1 else pasta / "_versoes" / f["nome_redape"].replace(".tab", ".csv")
            print(f"  ↓ {b}: {f['nome_redape']} ({f['tamanho'] / 1e6:.1f} MB)", flush=True)
            baixar(f["id"], f["tamanho"], alvo)
            cands.append((f, alvo, perfil(alvo)))
        escolha, motivo = cands[0], "único arquivo do mês"
        if len(cands) > 1:
            ref = cands[0][2]
            for f, _, p in cands[1:]:
                if p["ids"] != ref["ids"] or p["colunas"] != ref["colunas"]:
                    raise SystemExit(f"{b}: os arquivos de {mes} têm ids ou colunas diferentes. Decidir à mão "
                                     f"(estão em {pasta / '_versoes'}).")
            escolha = min(cands, key=lambda c: c[2]["invisiveis"])
            motivo = ("mesmos ids e colunas; escolhido o de menos caracteres invisíveis ("
                      + ", ".join(f"{c[0]['nome_redape']}: {c[2]['invisiveis']:,}" for c in cands) + ")")
            final.write_bytes(escolha[1].read_bytes())
        f, _, p = escolha
        man = {"base": b, "mes": mes, "arquivo": final.name, "origem_redape": f["nome_redape"], "id_redape": f["id"],
               "criacao_redape": f["criacao"], "tamanho": f["tamanho"], "linhas": p["linhas"], "sha1": p["sha1"],
               "motivo": motivo, "coletado_em": datetime.now().isoformat(timespec="seconds"),
               "alternativas": [c[0]["nome_redape"] for c in cands if c is not escolha]}
        mp = pasta / "manifesto.json"
        todos = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
        todos[b] = man
        mp.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")
        reg[b] = {"mes": mes, "ids_redape": sorted(x["id"] for x in n["arquivos"]), "arquivo": str(final),
                  "linhas": p["linhas"], "coletado_em": man["coletado_em"]}
        print(f"  ✔ {final} · {p['linhas']:,} linhas · {motivo}")
    (destino / "redape_coleta.json").write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # console do Windows (cp1252)
    ap = argparse.ArgumentParser()
    ap.add_argument("acao", choices=["verificar", "coletar"])
    ap.add_argument("--destino", default="data/raw")
    ap.add_argument("--mes", help="AAAA-MM pela data de criação no Redape (padrão: o mais recente de cada base)")
    ap.add_argument("--bases", default="publicacoes,projetos,solucoes")
    ap.add_argument("--forcar", action="store_true")
    a = ap.parse_args()
    a.bases = [b.strip() for b in a.bases.split(",") if b.strip()]
    if bad := set(a.bases) - set(BASES):
        sys.exit(f"Bases desconhecidas: {bad}. Use {list(BASES)}.")
    verificar(a) if a.acao == "verificar" else coletar(a)
