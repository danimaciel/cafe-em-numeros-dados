# Café em Números: atualização automática dos dados

Este repositório atualiza todo mês os dados do painel Café em Números (Embrapa Café + Embrapa Territorial). Ele cobre dois caminhos:

- as tabelas do BigQuery que o Looker Studio lê (`embrapa-cafe.ibge_cafe`);
- os arquivos da página própria (`site/dados/*.json`), quando ela entrar no ar.

## Regra principal: nunca mostrar menos

Uma atualização só pode **acrescentar** dados. Nada que o painel já mostra é apagado ou alterado.

- **PAM:** só entram anos mais novos que o último da tabela. Se o IBGE revisar anos antigos, a revisão aparece no relatório, mas não é aplicada.
- **Publicações:** só entram publicações que a tabela ainda não tem.
- **`scripts/conferir.py`:** roda antes de qualquer troca. Ele recusa a versão nova se faltar alguma linha, se algum valor antigo mudar, se algum ano ficar com menos linhas ou se as colunas mudarem.
- **BigQuery:** a gravação vai primeiro para `ibge_cafe_teste.<tabela>_teste`. A tabela oficial só é trocada com `GRAVAR_TABELA_REAL=sim`. Antes de trocar, o script guarda uma cópia em `ibge_cafe_teste.<tabela>_backup_AAAAMMDD`. Se a contagem de linhas não bater depois da troca, a cópia é devolvida.

## O que é atualizado

Veja `config/fontes.csv`. Em resumo:

| Aba | Fonte | Atualização |
|---|---|---|
| Visão Geral, Geografia, Distribuição | IBGE PAM 5457 | automática; ano novo, uma vez por ano |
| Café na Embrapa | Redape (publicações) | automática; publicações novas, todo mês |
| Estabelecimentos, Socioeconômicos | Censo Agro 2017 | não muda |
| Zarc | MAPA | manual por enquanto |

## Como roda

O arquivo `.github/workflows/atualizar_dados.yml` roda todo dia 5, às 6h de Brasília. Também dá para rodar pelo botão **Run workflow**, na aba Actions. A execução segue estes passos:

1. Pega a versão atual das tabelas. Ela vem do BigQuery, se houver credencial, ou de `dados/bigquery/`.
2. Coleta a PAM no SIDRA: Brasil, UFs e municípios, últimos 3 anos.
3. Monta `base_dashboard_3` com o ano novo e confere.
4. Coleta as publicações do Redape, pela data de criação do arquivo e não pelo nome.
5. Monta `autores` com as publicações novas e confere.
6. Grava no BigQuery: sempre no teste, e na oficial só se estiver ligado.
7. Exporta os arquivos da página, se houver `scripts/exportar_site.py`.
8. Escreve um resumo na página da execução e guarda os dados no repositório.

Se o IBGE ou o Redape estiverem fora do ar, aquela parte é pulada e tudo continua como estava.

## Rodar no computador

```
pip install -r requirements.txt
python scripts/coletar_pam.py --periodo "last 3"
python scripts/atualizar_pam.py
python scripts/conferir.py --atual dados/bigquery/base_dashboard_3.csv.gz --nova dados/bigquery/base_dashboard_3.novo.csv.gz --chave ano,uf,tipo_cafe,indicador --valor valor --grupo ano
python scripts/coletar_redape.py coletar --destino dados/redape --bases publicacoes
python scripts/atualizar_autores.py --redape dados/redape/AAAA-MM/publicacoes-da-embrapa-AAAA-MM.csv
```

## Configuração (uma vez)

0. **Repositório público.** A tabela `autores` tem nome e matrícula de empregados, por isso nunca vai para o git (`.gitignore`). A parte das publicações só roda com a credencial do Google, que baixa a tabela do BigQuery a cada execução.
1. **Versão atual das tabelas.** Exporte do BigQuery `base_dashboard_3` para `dados/bigquery/base_dashboard_3.csv.gz` e `autores` para `dados/bigquery/autores.parquet`. Esse passo não é necessário se houver credencial, porque a Action baixa sozinha.
2. **Credencial do Google (opcional).** Crie uma conta de serviço no projeto `embrapa-cafe` com os papéis *BigQuery Data Editor* e *BigQuery Job User*. Guarde a chave JSON como segredo `GCP_SA_KEY` do repositório. Sem ela, a Action só atualiza os arquivos do repositório e da página.
3. **Gravar na tabela oficial.** Exige faturamento ativo no projeto, porque o modo Sandbox não deixa gravar no dataset `ibge_cafe`. Depois disso, crie a variável `GRAVAR_TABELA_REAL=sim` em Settings > Secrets and variables > Actions > Variables. Também dá para escolher "sim" ao rodar pelo botão.

## Testes feitos (26/11/2026)

- **PAM.** Com as respostas do SIDRA de 2025, o script refez as 89.472 linhas de 2025 **idênticas** ao arquivo carregado à mão em `ibge_cafe_teste.base_dashboard_3_teste`, com os mesmos valores, nomes e colunas.
- **Publicações.** Tiramos 200 publicações da tabela e o script as refez. Todas foram reconhecidas como café, com o mesmo número de linhas e os mesmos dados de autor (tipo, nome, unidade, gênero). O tema (`topicomax`) coincidiu em 77%, porque o script original de agrupamento não está na pasta e o tema das novas vem do grupo mais parecido.
- **Conferência.** Ela recusou uma versão com 10 linhas a menos e um valor alterado.

## Pontos de atenção

- **Publicações que faltam.** A exportação do Redape de fev/2026 tem **69 publicações sobre café** que não estão na tabela `autores`. Provavelmente ficaram de fora no agrupamento original. Na primeira execução elas entram, o que aumenta o total de 3.166 para 3.235.
- **Autor novo.** Autor da Embrapa que ainda não aparece na tabela entra como "Autor externo" até existir `dados/embrapa/pessoas_autoria.csv`. Esse arquivo sai do fluxo `autores_folha`, com as colunas autoria, id_autor, nome, afiliacao, matricula, ativado, primeiro_nome e genero. O arquivo interno `AutorPessoalEmbrapa.xls` não vai para o repositório.
- **Texto e pontuação.** A coluna `texto` e o `score_cafe` das publicações novas seguem as mesmas regras de forma aproximada. As antigas ficam como estão.
