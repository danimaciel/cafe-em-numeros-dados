# Entradas fixas da página própria

Estes arquivos não mudam a cada mês. O `scripts/exportar_site.py` os junta à PAM e às publicações conferidas.

| Arquivo | Origem | Quando trocar |
|---|---|---|
| `municipios.csv` | cadastro de municípios do IBGE com coordenadas da sede (cod_mun, nome, sigla_uf, lat, lon) | município novo criado pelo IBGE |
| `uf.geojson` | malha estadual do IBGE simplificada | não muda |
| `censo2017_6955_estabelecimentos.csv` | Censo Agro 2017, Tabela 6955 (mesma base de `estabelecimentos2`/`cafe_socio_prod_municipio`) | próximo Censo |
| `censo2017_6768_produtores.csv.gz` | Censo Agro 2017, Tabela 6768 (grupo de atividade Total, tipologia Total), por município, idade e sexo | próximo Censo |
| `zarc.json` | ZARC/MAPA: valores **aproximados**, lidos das telas do painel | quando houver a tabela `zarc` exportada do BigQuery |
| `solucoes_tecnologicas_cafe.csv` | soluções tecnológicas da Embrapa (Redape, fev/2026) com café no nome ou nas palavras-chave | quando a planilha de soluções for atualizada |
