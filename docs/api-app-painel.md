# API do painel nativo

`/api/app` reaproveita os routers e modelos de dados de `/api/v2`. A coorte beta
continua exclusiva de `/api/v2`; nenhum header de cliente ou User-Agent concede
acesso. O sub-app mantém o envelope `{error:{code,message,details?}}` de V2.
Erros dos middlewares do monólito, incluindo CSRF, continuam `{detail:...}`.

Toda rota resolve o dono pela sessão Bearer ou cookie, aplica exclusão agendada,
assinatura e o marco bancário persistente de `get_open_finance_onboarding`.
Usuário sem o marco recebe 403 `open_finance_onboarding_required`. O helper
reconhece a sincronização válida do site; remover o último banco conserva o
marco. Falha de infraestrutura sobe como erro, sem conceder acesso.

Escritas com Bearer e JSON sem cookies mantêm o caminho nativo de CSRF existente.
Escrita com cookie mantém CSRF obrigatório. O app deve usar seu cliente com
`credentials:omit`. Não há exceção por prefixo nem por alegação de cliente.

Os contratos compartilhados incluem `me`, `perfil` GET/PUT, `contas`,
`resumo-do-mes`, `lancamentos`, `categorias`, `assinaturas`, `guia` e `previsao`,
com os mesmos feature gates. SSE `eventos` permanece apenas no namespace web:
revalida a coorte web durante o stream; o app revalida por foco e foreground.

`GET /lancamentos` sem `q` respeita o mês solicitado. Com termos de busca,
`q` consulta todo o histórico permitido pelo plano, mantendo os filtros de
categoria, origem e tipo. O campo `mes` da resposta conserva o mês solicitado;
ele não restringe os resultados da busca. A UI explicita esse escopo, conserva
as datas civis e permite percorrer o cursor até o fim, inclusive com filtro local por dia.

## Complementos exclusivos do app

`GET /patrimonio` retorna `{total,partes,motivos,historico,historico_desde}`.
`partes` contém `carteira`, `bancos`, `investimentos_banco`, `caixinhas` e
`investimentos_manuais`. O cálculo é `db.patrimonio.calcular`: caixinhas
espelhadas não são somadas de novo. Cada foto traz `{dia,total,partes,motivos}`,
somente quando existe na tabela e dentro da janela do plano. Sem fotos, a lista
é vazia e `historico_desde` é null. Nunca expõe `base`, raw ou ids do provedor.
Partes públicas e total oficial são arredondados HALF_UP em centavos; o total
permanece o calculado/fotografado oficialmente. Diferença entre sua projeção e
a soma das partes exibidas sai com `arredondamento_por_grupo`. Leitura atual e
fotos compartilham transação read-only repeatable read.

`GET /rendimento` retorna `{tipo:"contratado",itens,motivos}`. Cada item tem
`{nome,instituicao,taxa,tipo_taxa,observado_em,motivos}`. Lê posições BRL próprias
da conexão mais nova, exclui pausadas/resgatadas e busca a foto confirmada mais
recente nessa conexão. Taxa ausente, não finita ou sem unidade sai null com
`taxa_contratada_ausente`. Preserva a unidade informada pelo banco, incluindo
taxas não CDI. Não usa annualRate/lastMonthRate como rendimento recebido.
Fotos anteriores à posição atual são identificadas por motivo, assim como
posição fora da última geração e conexão desatualizada. Não lista ids privados.

`GET /mes-detalhes?mes=AAAA-MM` usa a mesma validação e janela mensal do resumo.
Retorna `mes`, `ate`, `entrou`, `saiu`, `categorias`, `dias`, `guardado`, `motivos`,
`fora_do_total` e `criterio_dias`. Categoria é `{categoria,valor,quantidade}`;
dia é `{dia,entrou,saiu}`. Lê todas as linhas pelo leitor oficial de lançamentos,
sem página 1 ou top-N, e conserva conciliação/fatura/janela de plano. O dia de
cartão é o da compra, podendo estar em mês anterior à fatura selecionada:
`criterio_dias="data_do_lancamento_ou_compra_na_fatura_do_mes"` declara a regra.

`guardado` é `{aportes,saques,liquido,cobertura:"movimentos_registrados"}`:
depósitos/aportes e saques/resgates registrados em `launches`, separados da
despesa. A diferença Entrou − Saiu não é aporte. Não infere aporte bancário de
variação de saldo ou foto de posição.

Dinheiro dos complementos é texto Decimal com duas casas e HALF_UP. Taxa
contratada preserva precisão. Os contratos compartilhados conservam a precisão
Decimal existente; o cliente deve converter por texto, sem float intermediário.
Quando a soma das categorias ou dos dias arredondados difere do arredondamento
da soma real desses grupos, `motivos` inclui `arredondamento_por_grupo`.
O mesmo motivo é publicado no patrimônio e em cada foto cujas partes exibidas
divergem do total oficial arredondado. Os totais financeiros oficiais e os
valores verdadeiros dos grupos são preservados; nenhum centavo é redistribuído.
A UI deve explicar que os valores de cada grupo são arredondados e a soma
exibida pode diferir em centavos do total.

## Cobertura conhecida

A regra oficial `TOTAIS_SQL` preserva a soma legada inclusive quando uma linha
importada possui outra moeda. Não é uma mudança de regra financeira desta Home.
Categorias e dias excluem valores não BRL e movimentos internos; guardar também
exclui aportes e saques registrados em outra moeda. Nesses casos,
`fora_do_total` conta as linhas excluídas por moeda e `motivos` inclui
`outra_moeda`. A UI deve mostrar essa cobertura e não apresentar o gráfico como
decomposição exata do total legado. Dados ausentes de contrato, histórico e
rentabilidade nunca são sintetizados.
