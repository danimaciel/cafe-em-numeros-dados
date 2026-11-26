"""Resumo da execução para a página da Action (lê dados/metadados/*.json)."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

M = Path("dados/metadados")
agora = datetime.now(timezone(timedelta(hours=-3))).strftime("%d/%m/%Y %H:%M")
print(f"## Atualização do Café em Números ({agora}, Brasília)\n")
for nome, titulo in [("pam_relatorio", "PAM (IBGE 5457)"), ("autores_relatorio", "Publicações (Redape)")]:
    p = M / f"{nome}.json"
    if not p.exists():
        print(f"- **{titulo}:** não rodou (fonte fora do ar ou sem arquivo novo); versão anterior mantida")
        continue
    r = json.loads(p.read_text(encoding="utf-8"))
    print(f"- **{titulo}:** {r.get('situacao', '?')}")
    if r.get("linhas"):
        print(f"  - linhas: {r['linhas']['antes']:,} → {r['linhas']['depois']:,}".replace(",", "."))
    rev = r.get("revisoes_ibge_nao_aplicadas", {}).get("valores_diferentes")
    if rev:
        print(f"  - o IBGE revisou {rev} valores de anos que já estão no painel; não foram aplicados (ver pam_relatorio.json)")
    for e in r.get("problemas", []):
        print(f"  - ⚠️ {e}")
for nome in ["pam_conferencia", "autores_conferencia"]:
    p = M / f"{nome}.json"
    if p.exists():
        r = json.loads(p.read_text(encoding="utf-8"))
        ok = "ok" if not r["erros"] else "RECUSADA: " + "; ".join(r["erros"])
        print(f"- Conferência {nome.split('_')[0]}: {ok} (+{r['linhas_acrescentadas']} linhas)")
