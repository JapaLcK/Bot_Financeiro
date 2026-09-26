# Plano do dashboard v2

Como o protótipo `webapp/src/dashboard/` (o "dashboard-v2") substitui o dashboard de
produção (`frontend/dashboard.html` + `frontend/dashboard.js`, servido em `/app`).

Decisões tomadas pelo dono em 2026-09-25, numa sessão de perguntas (Q1–Q35). Cada item
cita a pergunta que o decidiu. Sem prazo: o critério é qualidade (Q4). **Cada PR deste
plano marca aqui o que concluiu**, na seção "Andamento".

## 1. Objetivo e como a troca chega aos usuários

- **Meta final: tudo no v2.** O `dashboard.html` e o `dashboard.js` são apagados no fim
  (Q1). No caminho, a tela ainda não migrada abre no painel antigo por link, com um aviso
  discreto de "abre no painel antigo" (Q1, Q15).
- **Rota paralela `/painel`** para o v2; `/app` continua o antigo. Links nos dois sentidos
  ("Experimentar o novo painel" / "Voltar ao painel antigo"), visíveis só para quem está
  na lista de liberados; a escolha não é salva. No corte final, `/app` passa a abrir o v2
  (Q2, Q12).
- **Chave por usuário:** `dashboard_v2_enabled(user_id, email)`, no mesmo padrão das listas
  de liberados que já existem em `core/services/plan_service.py` (`agents_beta_tester`,
  `bank_list_ui_enabled`), com a lista numa variável de ambiente e o valor chegando ao
  navegador pelo `/auth/me` (Q11). A chave vale **no servidor**, não só nos links: a rota
  `/painel` confere a chave e manda para o `/app` quem estiver fora da lista, então abrir
  ou compartilhar o endereço direto não fura a liberação. Teste: usuário fora da lista
  abrindo `/painel` direto cai no `/app`; usuário da lista abre o v2. A rota também manda
  para o `/app` quem chega com o marcador `PigBankApp` no user agent, mesmo liberado: o
  WebView do app atual navega em `pigbankai.com`, e um link compartilhado abriria o v2 lá
  dentro. Aqui o user agent escolhe qual tela, não concede acesso. Teste: `PigBankApp`
  liberado abrindo `/painel` direto cai no `/app`.
- **O app atual (Capacitor) nunca mostra o v2.** Ele carrega o site ao vivo, então os links
  de troca ficam escondidos quando o user agent **contém** `PigBankApp` — a mesma checagem por
  trecho de `_is_pigbank_app` (`frontend/routes/shared.py`) e do `app-mode.js`: o WebView
  anexa o marcador ao user agent do Safari e a versão muda, então comparação exata falha.
  Isso só esconde interface; nunca concede nem nega acesso (o user agent é alegação do
  cliente) (Q10). O app novo (Expo)
  terá telas próprias e consome a mesma API nova (Q17, Q31).
- **Quando abrir para mais gente:** decisão do dono, sem critério automático (Q16).
- **Tema:** só escuro no primeiro corte; o tema claro vem numa fase seguinte (Q9).

### Fonte da verdade: Open Finance, e a carteira Piggy só para dinheiro físico (Q36)

Decisão do dono em 2026-09-25, depois das 35 primeiras: no app novo e no v2, **o único
lançamento manual é na carteira Piggy, e ela é só dinheiro físico** (espécie). Pix, contas,
cartões, investimentos, aportes, resgates, saques e todo o resto vêm **só do Open
Finance**, que passa a ser a fonte única da verdade para eles.

O que isso muda neste plano:
- **Investimento manual sai do v2.** Não se cria, não se aporta, não se resgata e não se
  desfaz investimento à mão no v2. Com isso sai do plano toda a maquinaria de rendimento
  dos investimentos manuais (lotes, fotos antes e depois do movimento, carteira-sombra,
  regras do desfazer): o Rendimento × CDI passa a ser só do Open Finance (seção 2).
- **Os defeitos de dinheiro achados na revisão deste plano ficam no código atual**, não no
  v2: o resgate que pula juro de índice atrasado, o desfazer que não devolve o cursor, o
  desfazer de resgate anterior que cria dinheiro e o desfazer sem a trava por usuário. Eles
  seguem valendo para quem usa o painel antigo e o WhatsApp, e são consertados num PR
  próprio (Q39, abaixo).
- **Recorrente só prevê.** Hoje o carregador de recorrentes (`run_recurring_charger_loop`,
  em `core/services/recurring_charger.py`) lança sozinho na carteira: o salário entra como
  crédito e a conta fixa como débito, sem olhar o Open Finance — que já traz a mesma
  transação do banco. Com a Q36 isso conta duas vezes — e, para quem não tem banco
  conectado, transforma uma previsão (salário que ainda não caiu, conta ainda não paga) em
  dinheiro na carteira. Então a recorrente **só prevê, para todo mundo** (decisão do dono,
  Q42, 2026-09-25 — contra a alternativa de manter o lançamento para quem não tem banco): alimenta a
  Previsão e o aviso de vencimento, e o lançamento automático do carregador é desligado
  **antes** do job da foto (etapa 0), para todos os usuários — também no painel antigo,
  porque a foto é de todos. Quem quiser registrar que pagou em dinheiro lança na carteira.
  Testes: salário recorrente com o crédito do banco já no Open Finance (conta uma vez);
  usuário sem banco conectado com salário e conta recorrentes (nada lançado, só previsto).
  O mesmo vale para **marcar uma conta como paga** à mão: hoje `mark_bill_paid`
  (`db/bills.py`) sempre lança na carteira, pela rota `/recurring-bills/.../pay` e pela
  ferramenta `pay_bill` da IA. Ela passa a usar o mesmo estado da forma de pagamento da
  Q40: paga pelo banco, marcar como paga só muda o estado da conta (o débito vem do Open
  Finance); paga em dinheiro, lança na carteira; sem saber, pergunta. `mark_bill_paid`
  passa a exigir a forma (sem valor padrão, que erraria para um dos lados), e **todo**
  chamador coleta e repassa a escolha — o inventário por `grep` é o primeiro passo: a rota
  HTTP, a ferramenta `pay_bill` da IA (`core/services/ai_chat/tools/bills.py`), o `payBill`
  do painel antigo (`frontend/dashboard.js`, que hoje manda só o valor), o WhatsApp
  (`adapters/whatsapp/wa_runtime.py`, dois pontos) e `core/handlers/bills.py` (dois
  pontos). Testes: marcar paga pelo banco (nada lançado) e em dinheiro (um lançamento), em
  cada um desses chamadores.
- **Lançar, no v2, é lançar na carteira.** "Lançamentos (ver, lançar, editar, apagar)" da
  primeira versão vira: ver tudo; lançar, editar e apagar só o que é da carteira Piggy.
  Transação do Open Finance não se cria nem se apaga à mão.
- **Dinheiro que muda de lugar não é gasto nem receita.** Sacar no caixa eletrônico: o Open
  Finance vê só o débito no banco; sem nada mais, o patrimônio cai e o relatório mostra um
  gasto que não houve. Depositar dinheiro vivo é o inverso. Por isso existe uma
  **transferência entre banco e carteira**: o saque (ou depósito em espécie) que o Open
  Finance trouxer é casado com uma entrada (ou saída) da carteira Piggy, e o par fica fora
  dos relatórios de gasto e receita e não mexe no patrimônio. O casamento é automático,
  com um aviso que o usuário pode desfazer (Q41). Testes: saque e depósito, olhando patrimônio (não muda) e relatórios (não entra).

Decidido pelo dono na mesma data (Q37–Q41):
- **Q37 — o que já existe de manual fica só para leitura**, como "registro manual antigo",
  com convite para conectar o banco. Não se aporta, resgata nem desfaz mais nada nele pelo
  v2; aparece com o saldo, sem comparação com o CDI. Duas heranças que a foto do patrimônio
  não pode somar às cegas:
  - **investimento manual que o banco também traz:** não há ligação entre `investments` e
    `open_finance_investments`, então o CDB lançado à mão e o mesmo CDB vindo do banco
    contariam duas vezes. Quando o usuário tem investimento manual e investimento do Open
    Finance, o v2 pergunta, para cada manual, **qual** posição do banco ele é (aí o manual
    sai da soma) ou se é outro; a resposta guarda a identidade dessa posição (a mesma chave
    estável na reconexão usada no saque). Ela é refeita quando:
    - **a posição some porque o banco foi desconectado:** o manual volta para a soma (o
      investimento existe, só não é mais visto) e a pergunta reabre;
    - **a posição some com o banco conectado e sincronizando bem** (o banco a liquidou): o
      manual **continua fora** da soma, marcado "a conferir" — o dinheiro do resgate já
      pode estar no saldo do banco — até o usuário responder de novo;
    - **aparece posição nova** em qualquer banco, mesmo sem mudar o conjunto de bancos: as
      respostas "outro" reabrem, porque a nova pode ser a mesma. Enquanto houver algum sem resposta, a
    foto sai marcada como incerta;
  - **carteira que não é só dinheiro vivo:** hoje a carteira é dinheiro mais contas de banco
    não conectadas (o painel antigo pede para "Ajustar Carteira" depois de conectar), então
    somá-la ao saldo do banco conta o mesmo dinheiro duas vezes — a correção de fusão só
    cobre lançamentos casados, não o saldo acumulado. E ela também pode ter dentro salário
    ou conta que o carregador de recorrentes lançou sozinho sem terem acontecido
    (`recurring_income_credits`, `recurring_charges`) — desligar o carregador só para os
    lançamentos futuros. Por isso, **sem exceção**, na primeira vez no v2 todo usuário
    confirma quanto da carteira é dinheiro vivo, tenha banco conectado ou não; até
    confirmar, a foto dele sai marcada como incerta. A confirmação não corrige só o
    saldo: os lançamentos automáticos antigos da recorrente continuam como linhas comuns
    (ligadas em `recurring_income_credits` e `recurring_charges`) e apareceriam em
    Lançamentos e em Para onde vai como receita e gasto reais. Por isso, no mesmo passo, o
    usuário vê esses lançamentos e responde, para cada um, como ele aconteceu — a mesma
    lógica da Q40: **em dinheiro** (a linha fica, é da carteira), **pelo banco** ou **não
    aconteceu** (sai). "Pelo banco" só tira a linha dos relatórios **depois de casada com
    uma transação específica do Open Finance** (o v2 sugere a candidata, o usuário
    confirma) — manter as duas contaria o mesmo salário duas vezes, mas tirar sem a outra
    sumiria com um salário que existiu. Sem transação para casar (banco não conectado,
    importação incompleta, nada parecido), a linha continua contando. O casamento guarda a
    identidade da transação do banco estável na reconexão (a mesma do saque), e segue a
    vida dela: se a transação some porque o banco foi desconectado (o desconectar desfaz o
    lançamento importado e apaga a transação) **ou porque o banco a apagou** com a conexão
    ativa (o caminho `transactions/deleted`, que também desfaz o lançamento importado), a
    linha antiga **volta** aos relatórios, para o fato não sumir; se o banco a traz de novo
    com a mesma identidade, ou manda uma substituta que o usuário casa, a linha antiga sai
    de novo, sem contar duas vezes. Sair dos
    relatórios não apaga a linha. Até ele revisar, eles aparecem com a marca "lançado
    automaticamente, a conferir" e ficam fora dos totais. A confirmação vale enquanto a
    carteira só receber dinheiro vivo, e isso se garante pela classe, não por lista de
    rotas: **toda escrita no saldo da carteira (`accounts.balance`) que não venha de uma
    escrita de dinheiro vivo confiável derruba a confirmação**. Confiáveis são só duas: o
    lançamento em dinheiro do v2, a transferência automática entre banco e carteira da
    Q41 (com as correções que o banco fizer nela) e o depositar e retirar entre carteira e
    caixinha manual do v2 (Q38) — todas dinheiro vivo mudando de lugar ou entrando. Mas
    retirar da caixinha manual hoje credita também o rendimento simulado
    (`pocket_withdraw_to_account` soma o ganho no que volta para a carteira): enquanto a
    Q43 não for decidida, só o principal conta como escrita confiável, e a retirada que
    trouxer ganho simulado derruba a confirmação. As
    outras derrubam: lançar ou apagar no painel antigo,
    ajuste de saldo, importar extrato OFX (`import_ofx_launches_bulk`), desfazer
    (`delete_launch_and_rollback`) e qualquer caminho futuro. A regra mora na camada `db/`,
    junto do aviso em tempo real, e o mesmo teste que varre as escritas nas tabelas
    financeiras falha se alguma escrita no saldo da carteira não passar por ela. Caída a
    confirmação, a foto volta a ser incerta até o usuário confirmar de novo;

  Testes: CDB manual e o mesmo CDB do banco (incerta até responder; depois, uma vez só;
  desconectar o banco devolve o manual à soma e reabre a pergunta); lançar pelo painel
  antigo depois de confirmar a carteira, importar OFX e apagar lançamento antigo (volta a
  incerta nos três); último ponto incompleto e último ponto incerto (a variação do título
  não é calculada);
  carteira com saldo de banco antigo e banco conectado, e carteira com salário recorrente
  lançado antes do desligamento sem banco conectado (incerta até confirmar, nos dois);
  salário automático antigo que não caiu (fora de Lançamentos e de Para onde vai depois
  de marcado como não aconteceu, e fora dos totais enquanto não revisado); salário
  automático antigo que caiu no banco e já veio pelo Open Finance (marcado "pelo banco" e
  casado, conta uma vez só); "pelo banco" sem transação para casar (a linha continua
  contando); posição nova num banco já conectado com um manual respondido "outro" (a
  pergunta reabre); posição casada que o banco liquida com a conexão ativa (o manual segue
  fora da soma, "a conferir"); recorrente antiga casada, banco desconectado e depois
  reconectado (o fato aparece uma vez em cada momento, nunca zero nem duas); carteira
  confirmada seguida de um saque sincronizado e de depositar e retirar numa caixinha manual
  (a confirmação continua); caixinha manual e a mesma caixinha vinda do banco (incerta até
  responder, depois uma vez só); caixinha manual com rendimento simulado (a foto conta só o
  principal); retirar de caixinha manual com rendimento simulado e tirar a foto (a
  confirmação cai); recorrente antiga casada cuja transação o banco apaga com a conexão
  ativa (a linha antiga volta); conta corrigida de BRL para moeda desconhecida e de volta
  (a linha quebra nas duas, sem perda nem ganho falsos).
- **Q38 — a caixinha manual continua**, como exceção à Q36: ela é dinheiro separado pelo
  próprio usuário, e depositar e retirar nela segue existindo no v2. A caixinha espelhada
  do banco continua vindo do Open Finance. Consequências:
  - **caixinha manual que o banco também traz:** a sincronização
    (`sync_open_finance_caixinhas`) não adota a manual sozinha — cria outra espelhada ou
    deixa a posição do banco sem ligação quando o nome colide —, então a mesma caixinha
    contaria duas vezes. Ela entra no mesmo fluxo de reconciliação do investimento manual
    (Q37, abaixo): a pergunta "é esta do banco?", a resposta ligada à posição, e a foto
    incerta enquanto houver pergunta aberta;
  - **mover entre carteira e caixinha manual é escrita confiável:** depositar e retirar
    (`pocket_deposit_from_account`, `pocket_withdraw_to_account`) mexem no saldo da
    carteira, mas é dinheiro vivo mudando de lugar, então entram na lista das escritas que
    não derrubam a confirmação da carteira;
  - **rendimento simulado:** a caixinha manual hoje rende CDI simulado (`interest_enabled`
    ligado por padrão, `accrue_all_pockets`), e esse ganho vai para a carteira no resgate.
    Com a Q36 (rendimento só do Open Finance), isso é a **Q43**, abaixo. Até ela ser
    decidida, a foto conta a caixinha manual **pelo principal**, e o rendimento simulado
    aparece à parte, como estimativa, fora do patrimônio.
- **Q43 — a decidir: a caixinha manual continua rendendo?** Sugestão: não — ela vira só
  dinheiro separado, sem rendimento simulado, e o ganho já acumulado vira parte do saldo
  dela uma última vez, com aviso. A alternativa é manter o rendimento como estimativa
  visível, mas nunca somado ao patrimônio.
- **Q39 — os defeitos de dinheiro do código atual são consertados**, não congelados, num PR
  próprio (faixa Completo, com o time): o resgate que pula juro de índice atrasado, o
  desfazer que não devolve o cursor, o desfazer de resgate anterior que cria dinheiro, e o
  desfazer e o apagar investimento sem a trava por usuário. A regra combinada: só se desfaz
  o último movimento do investimento, e apagar o investimento conta como movimento.
- **Q40 — WhatsApp, três caminhos**, na forma que o dono mostrou numa conversa real:
  1. **Não dá para saber como foi pago** ("gastei 500"): o Piggy pergunta antes de
     registrar qualquer coisa — "Esse R$ 500 foi em dinheiro vivo ou passou pelo banco
     (PIX, cartão, débito)?".
  2. **Em dinheiro:** vira lançamento na carteira Piggy.
  3. **Pix, cartão ou débito:** **não registra nada**. O Piggy explica que o Open Finance já
     traz essa transação e oferece procurá-la no extrato do banco conectado para confirmar
     que ela já apareceu ("Quer que eu busque essa transação no seu extrato do Nubank?").

  Hoje isso não é regra escrita no código: é o modelo respondendo por conta própria, então
  pode mudar de uma mensagem para outra. Vira regra **no código, não nas instruções da IA**:
  a forma de pagamento é um estado estruturado do lançamento pendente (`dinheiro | banco |
  desconhecida`), e a ferramenta que grava na carteira **recusa** gravar enquanto ele não
  for `dinheiro` — a IA pode reformular a pergunta, mas não decide sozinha se o dinheiro
  sai da carteira. Teste de conversa pelo
  `handle_incoming` e rodada no harness da IA (o pytest não fala com o modelo): "gastei 500"
  → pergunta; "pix" → nada lançado e a oferta de buscar; "dinheiro" → um lançamento na
  carteira; "gastei 50 no mercado no cartão" → nada lançado, sem perguntar.
- **Q41 — o saque entra na carteira sozinho**, com um aviso que o usuário pode desfazer
  (se o dinheiro não foi para o bolso). O depósito em espécie é o inverso. Como é o Open
  Finance que cria esse lançamento, ele segue a transação de origem pela vida toda:
  - **ligação durável:** o lançamento da carteira guarda a identidade da transação do banco
    que o criou — não a linha local (desconectar o banco apaga conexão, contas e transações
    em cascata) e não só o `provider_transaction_id` (ele é único só dentro da conta:
    `(account_id, provider_transaction_id)` em `db/schema.py`). A identidade é a chave
    natural da conta no provedor mais o id da transação; o PR mede na API real se ela
    sobrevive a desconectar e reconectar. Desconectar **não** apaga nem desfaz o lançamento
    da carteira (o dinheiro vivo continua no bolso). Na reconexão, o banco reimporta o
    histórico: saque cuja identidade já tem lançamento ou recusa gravados é pulado; saque
    nunca visto — inclusive o feito enquanto o banco estava desconectado — segue o caminho
    normal (lançamento automático com aviso). Se o PR medir que a identidade **não**
    sobrevive à reconexão, o saque anterior à conexão nova que não se consegue provar
    inédito vai para **confirmação** do usuário em vez de lançar sozinho — nunca é
    descartado pela data, que apagaria um saque real feito no intervalo;
  - **o banco corrige, a carteira acompanha:** valor ou data corrigidos numa sincronização
    (`save_open_finance_sync`) atualizam o lançamento; e a cada correção a transação é
    **reavaliada**: se deixou de ser saque ou depósito em espécie (o banco reclassificou
    como compra ou transferência), o lançamento da carteira é desfeito; transação apagada pelo banco (o
    caminho `transactions/deleted`, em `frontend/routes/open_finance.py`) desfaz o
    lançamento — senão um saque de R$ 100 apagado deixa R$ 100 de dinheiro que não existe;
  - **desfazer é para sempre:** quando o usuário desfaz, fica gravada a recusa ligada ao id
    da transação do banco, e a próxima sincronização não recria o lançamento.

  Testes: saque corrigido em valor e em data; saque apagado pelo banco; desfazer e
  sincronizar de novo (o dinheiro não volta); saque reclassificado pelo banco como compra
  (o lançamento da carteira sai); desconectar, reconectar e o banco trazer o
  mesmo saque de novo (nenhum crédito a mais, e o dinheiro da carteira fica); saque feito
  enquanto o banco estava desconectado (entra na carteira depois da reconexão).

### O que a primeira versão precisa ter (Q3)

Resumo, Lançamentos (ver tudo; lançar, editar e apagar só na carteira — Q36), Previsão,
Metas e caixinhas (depositar e retirar na caixinha manual — Q38), Para onde vai, Patrimônio e o chat do Piggy com IA real e blocos (Q6).
Pix, conexão do Open Finance, MFA e notificações já moram em `settings.html` /
`precos.html` e continuam lá; o v2 só aponta para elas. O resto (orçamentos, orçamento
doméstico, cartões, categorias, agentes, afiliados, exportar, importar OFX, ajuste de
saldo, vínculo do WhatsApp) abre no antigo até ser migrado, um de cada vez.

## 2. A API nova: `/api/v2`

Escolha do dono: API nova, pensada para manutenção de longo prazo, simplicidade e
tecnologia, podendo refazer o que for preciso, com calma (Q5).

- **Quem usa:** o dashboard v2 e o app novo (Expo). O WhatsApp não chama HTTP: roda no
  mesmo servidor e usa as mesmas regras de negócio direto (Q17).
- **Organização:** pasta própria `api/v2/` com `rotas/`, `esquemas/` (Pydantic) e
  `dominio/` (regras), em arquivos pequenos por assunto. Nada entra no
  `frontend/finance_bot_websocket_custom.py`; ele só registra o roteador (Q26).
- **Regras de negócio num lugar só (Q18).** Cada regra reescrita para a API nova passa a ser
  usada também pelo WhatsApp, e a versão antiga sai no mesmo PR. Nunca duas versões da
  mesma conta. (Hoje saldo e gasto do mês já estão duplicados entre o WhatsApp e o
  dashboard; ver o comentário perto de `get_financial_data` no monólito.)
- **Segurança da migração do WhatsApp (Q30):** todo PR de regra roda os testes de conversa
  (`handle_incoming`, com estado real) e compara os números antes e depois para um conjunto
  fixo de usuários de teste. Número que muda: o PR explica (conserto) ou não entra.
- **Isolamento por construção (Q20, Q23):** nenhuma rota recebe `user_id` de fora. Uma
  dependência única (`Depends(usuario_atual)`) aceita o cookie de sessão (web, com CSRF) e
  o Bearer (app), barra plano inativo e entrega o usuário. Um teste varre todas as rotas da
  `/api/v2` e falha se alguma não tiver a dependência. Cada rota tem teste de "o usuário B
  não recebe dado do A". Sem `user_id` no caminho, o id do recurso (lançamento, caixinha…)
  ainda vem de fora: toda query de escrita filtra também por `user_id` do `usuario_atual`, e
  cada rota que altera ou apaga tem teste de B mandando o id de A — B recebe recusa (404,
  para não revelar que o id existe) e o registro de A continua igual.
- **Plano por recurso (Q20):** estar logado com plano ativo não basta. Rota de recurso pago
  declara também `Depends(recurso("forecast"))` (ou `insights`, `simulator`, `ai_chat`…),
  que chama o `plan_gate_ok` de `core/services/plan_service.py` — a matriz
  `FEATURE_MIN_TIER_V2` continua a fonte única, sem cópia na `/api/v2`. Como o `plan_gate_ok`
  trata nome desconhecido como plano de entrada, o `recurso()` recusa, **ao montar a rota**
  (o servidor nem sobe), nome que não esteja na matriz nem nos casos especiais que ele
  conhece (hoje só `ai_chat`, que é cota mensal e não plano) — um erro de digitação não vira
  acesso liberado em silêncio. Recusa sai no envelope de erro (`plano_insuficiente`, ou
  `cota_esgotada` no `ai_chat`). O teste que varre as rotas exige, em cada uma, ou a
  dependência de recurso ou a presença numa lista explícita de rotas do plano de entrada. Cada
  rota paga tem teste na fronteira: o plano abaixo recebe a recusa, o plano mínimo recebe o
  dado; as do `ai_chat` têm também o teste da cota esgotada. O gate pelo `/auth/me` na tela
  (seção 3) só decide o que mostrar; quem protege é o servidor.
- **Limites por valor, além do sim/não (Q20):** o plano também limita *quanto* se vê, não só
  *se* se vê — a janela de histórico (`history_days`, `history_current_month_only` em
  `core/services/plan_limits.py`) e os tetos (`launches_month_max`, `pockets_max`…). Toda
  rota de leitura com histórico corta a consulta no servidor pelo `history_earliest_date`
  (`core/services/plan_service.py`), como as rotas atuais já fazem, e toda rota de escrita
  com teto confere o teto. Teste por rota: o plano de entrada não recebe nada antes do
  corte, o plano sem corte recebe; e a escrita acima do teto é recusada.
- **Contrato (Q22):** request e response declarados em Pydantic (`response_model` em toda
  rota) → especificação OpenAPI → tipos TypeScript gerados para o v2 e para o app (e o zod
  do app, gerado da mesma especificação). **Exceção: a rota de eventos (SSE)**, que devolve
  um stream e não tem `response_model` útil. O formato de cada evento (`{"mudou": [...]}`) é
  um modelo Pydantic próprio, registrado nos componentes da especificação para os tipos
  saírem gerados como os outros. O teste que varre as rotas (Q23) aceita essa exceção pelo
  nome, e só ela. A especificação fica disponível só em
  desenvolvimento; em produção continua desligada, como hoje.
- **Erro (Q25):** envelope único `{"erro": {"codigo": "...", "mensagem": "...",
  "campo": null}}`, com os códigos listados no contrato.
- **Tempo real (Q19, Q27–Q29):** SSE, só servidor → cliente. O aviso carrega só *o que*
  mudou (`{"mudou": ["lancamentos", "saldo"]}`), nunca o dado; a tela pede de novo à API.
  Um lançamento feito pelo WhatsApp atualiza o painel aberto. **Toda escrita de dado
  financeiro avisa**, não só criar lançamento: editar e apagar lançamento, depositar e
  retirar de caixinha, aporte e resgate, fatura, contas fixas, metas, sincronização do Open
  Finance. Como as regras moram num lugar só (Q18), o aviso sai de dentro da regra, que
  declara os tipos que mudou; cada PR que migra uma regra traz o teste de que ela avisa os
  tipos certos. Mas durante a convivência o código antigo também escreve (o ajuste de saldo
  em `adjust_balance_route`, no monólito, só invalida o cache do painel antigo), e testar
  "cada tipo tem alguma escrita que avisa" passa com o caminho novo enquanto o antigo fica
  mudo. Então na etapa 0 o aviso entra **nas funções que escrevem nas tabelas financeiras**
  (a camada `db/`, por onde passam o painel antigo, o WhatsApp, o v2 e a sincronização), e
  as escritas que hoje estão direto numa rota passam para essa camada. O teste enumera
  **os caminhos de escrita, não os tipos**: varre o código atrás de `insert`, `update` e
  `delete` nas tabelas financeiras e falha se algum estiver fora de uma função que avisa. O aviso não se guarda: se a
  conexão cair, o que mudou nesse meio-tempo se perde. Por isso, **a cada (re)conexão** o
  cliente invalida todas as consultas e a tela pede tudo de novo — cobre a queda, a volta do
  sono do computador e a janela entre a primeira carga e a conexão abrir. A sessão expira em
  15 minutos e só o `fetch` sabe renová-la (`frontend/static/auth-refresh.js`); o
  `EventSource` que recebe 401 fecha de vez. Então, quando a conexão fecha, o cliente faz uma
  chamada leve pela API (o `fetch` renova a sessão se precisar) e recria o stream; se a
  sessão acabou de fato, segue o caminho normal de sessão encerrada. O inverso também vale: a
  dependência de usuário só roda na abertura, então um stream aberto não perceberia sozinho
  a sessão vencida ou revogada. Por isso o servidor **fecha o stream quando vence o token
  que o abriu** (no máximo 15 minutos, e o cliente reconecta pelo caminho acima) e quando
  a sessão é revogada. A revogação não é avisada por quem revoga: há vários caminhos que
  gravam `auth_sessions.revoked_at` (logout e "sair de todos" em `core/sessions.py`, replay
  de refresh e ociosidade em `core/refresh_tokens.py`, o painel de admin), e avisar em cada
  um seria remendo por instância. Em vez disso, **o próprio stream confere a sua sessão no
  banco a cada 30 segundos** (junto do keepalive) e **antes de mandar cada aviso**, e fecha
  se ela foi revogada — cobre todo caminho, os de hoje e os futuros, em qualquer processo,
  sem canal extra (avisos são raros; uma consulta por aviso é barata). A garantia exata:
  nenhum aviso sai depois que a conferência viu a revogação. Sobra uma janela de
  milissegundos entre conferir e mandar em que um aviso pode passar — e ele não carrega
  dado nenhum (`{"mudou": [...]}`); o dado só vem pedindo à API, que recusa a sessão
  revogada. Serializar essa janela entre processos custaria mais do que protege, então não
  se faz. Teste: revogar por um caminho que não é endpoint (replay de refresh) com o stream
  aberto em outro processo e lançar depois — nenhum aviso chega, o stream fecha, e o pedido
  de dado com a sessão revogada é recusado. O teste de ponta a ponta
  do aviso (Q32) inclui derrubar a conexão, lançar e reconectar, e reconectar com a sessão
  vencida. O aviso sai de uma função única, e **todo processo que grava dado financeiro tem
  de alcançá-la**. Hoje produção roda dois: o `launch.py` sobe o uvicorn e também o
  `bot.py` (o bot do Discord, morto como produto mas vivo no deploy, e que grava pelo
  `core_handle_incoming`). A etapa 0 começa confirmando com o dono se o `bot.py` sai do
  `launch.py`. Se sair e sobrar um processo só, o aviso fica dentro dele; se ficar, ou com
  uma segunda instância, a função usa `LISTEN/NOTIFY` do Postgres desde a etapa 0.
  Duas regras valem nos dois casos:
  - **o aviso só chega depois do commit, e chega sempre.** Aviso antes do commit faz a
    tela pedir de novo e ler o estado velho, sem outro aviso depois; aviso depois do commit
    feito à parte pode não sair se o processo cair entre os dois. Por isso cada caso usa o
    seu mecanismo: com `NOTIFY`, ele é emitido **dentro** da transação da escrita (o
    Postgres só entrega quando ela confirma, e nada se ela desfaz); com o aviso dentro do
    processo, a função roda **depois** do commit com sucesso — ali, se o processo cai, cai
    junto o stream, e a reconexão refaz tudo. Mas o aviso pode falhar sem o processo cair
    (erro na função, tarefa cancelada): então qualquer falha ao avisar **fecha os streams
    daquele usuário**, e a reconexão refaz tudo. Teste: a função do aviso falha depois de
    um commit com sucesso, e a tela se atualiza pela reconexão. Com `NOTIFY` há mais uma queda possível: a
    conexão de `LISTEN` do servidor cai e volta enquanto o navegador segue conectado, e o
    Postgres não reenvia o que foi avisado nesse intervalo. Por isso, quando o `LISTEN`
    cai, o servidor **fecha todos os streams** que dependem dele; cada navegador reconecta e
    refaz tudo pelo caminho normal. Enquanto o `LISTEN` não voltar, **nenhum stream novo
    abre**: a rota responde 503 com um tempo de nova tentativa, e o cliente tenta de novo
    com espera crescente — senão um stream aberto durante a queda pediria tudo uma vez e
    perderia as escritas seguintes. Teste separado: derrubar só a conexão de `LISTEN`,
    fazer duas escritas durante a queda, e ver a tela atualizar com as duas quando ela
    voltar. Teste: uma escrita com o commit atrasado de
    propósito — a tela só pede de novo depois dele e vê o dado novo; e uma escrita que
    desfaz não gera aviso.
  - **só para o dono.** O aviso interno leva o `user_id` de quem teve o dado mudado (o que
    vai para o navegador continua só `{"mudou": [...]}`), e cada stream assina só o usuário
    do `usuario_atual`. Um aviso global deixaria B saber quando A mexe no dinheiro e faria
    todo painel aberto pedir de novo a cada escrita de qualquer um. Teste: B conectado não
    recebe nada quando A lança.

  O `/ws` antigo sai junto com o dashboard antigo.
- **Processo (Q21):** todo PR que cria ou muda endpoint da `/api/v2` é **faixa Completo**,
  com o time inteiro, os testes de isolamento e o Codex.

### Dados que não existem hoje (Q14, Q35)

- **Patrimônio em 12 meses:** um job diário grava uma "foto" do patrimônio de cada usuário.
  Ele entra **na etapa 0**, antes de qualquer tela, para o histórico começar a encher o quanto
  antes; enquanto enche, o gráfico diz que se completa com o tempo. Nada de reconstruir o
  passado (mostraria número errado com cara de certo). O que entra na foto sai de **uma
  função só** de patrimônio, criada na etapa 0 e usada também pela tela de Patrimônio (Q18)
  — hoje não existe uma. Duas regras que ela herda do código atual:
  - **sem contar duas vezes:** caixinha ligada a um investimento do Open Finance espelha o
    saldo dele (`pockets.of_investment_id`), e `list_of_fixed_income` (`db/rv.py`) já tira
    essas posições da lista; a função reusa essa exclusão, não a reescreve;
  - **só reais:** `open_finance_investments` guarda a moeda, e o código de análise já
    filtra BRL. A foto soma só BRL e guarda quantas posições **e contas** em outra moeda
    ficaram fora (`BANK_ACCOUNTS_SQL` só pega contas em BRL, então conta em dólar também
    some em silêncio); o gráfico diz isso, em vez de somar dólar como real. Conversão com câmbio datado fica para
    quando alguém pedir. A caixinha espelhada não guarda moeda (`sync_open_finance_caixinhas`
    e `bind_pocket_to_caixinha` não levam a moeda para ela), então a moeda da caixinha
    ligada vem do investimento de origem: caixinha espelhando posição em dólar fica fora
    como a posição. E "BRL" gravado não prova BRL: a ingestão grava `BRL` quando o conector
    não manda moeda (`inv.get("currency") or "BRL"` e o mesmo para contas, em
    `db/open_finance.py`), e `BANK_ACCOUNTS_SQL` trata nulo como BRL. O primeiro passo do PR
    é conferir no `raw` guardado se algum conector omite a moeda; se omitir, a ingestão passa
    a guardar a moeda como desconhecida **e as linhas já gravadas são reclassificadas pelo
    `raw`** (senão as antigas seguem como reais até a próxima sincronização, ou para sempre
    numa conexão parada), e a foto trata a moeda desconhecida como fora, com o mesmo aviso;
  - **uma leitura só:** a foto lê tudo numa única transação `REPEATABLE READ` (uma visão só
    do banco), para um aporte que confirma no meio não ser contado duas vezes nem nenhuma.
    "Tudo" inclui a marca de pendência abaixo, lida **no mesmo cursor** (hoje
    `bank_movement_summary` abre conexão própria e veria outro momento);
  - **o caixa é o do painel de hoje:** carteira Piggy (a manual) mais as contas do banco em BRL
    (`BANK_ACCOUNTS_SQL`), com a correção de lançamento fundido
    (`MERGED_WALLET_DELTA_SQL`) — as duas em `db/open_finance.py` — para a transação já refletida no banco não ser
    debitada duas vezes. A função reusa essas consultas numa versão que recebe o cursor,
    dentro da mesma transação, sem reescrever a regra;
  - **reset no meio:** a visão consistente não impede o "Recomeçar do zero" de apagar o
    histórico entre a leitura e a gravação da foto, e aí a foto velha voltaria. O job pega
    uma trava consultiva do usuário em modo compartilhado; o reset a pega em modo
    exclusivo. Movimentos comuns não passam por ela, então o job não os segura. A ordem
    importa: numa transação `REPEATABLE READ` a visão do banco é fixada no primeiro
    comando, que seria o próprio pedido da trava — se ele esperasse o reset, a foto leria o
    estado de antes. Por isso a trava é **de sessão, pega antes de abrir a transação**, e
    solta **em qualquer saída** (num `finally`, com sucesso ou erro): trava de sessão
    sobrevive ao rollback, e a conexão volta ao pool (`db/connection.py`) — esquecida ali,
    ela seguraria o "Recomeçar do zero" daquele usuário para sempre. Se soltar falhar, a
    conexão é descartada em vez de voltar ao pool. Teste: a foto falha no meio e o reset
    seguinte do mesmo usuário roda;
  - **conexão parada não vira queda:** com uma conexão pausada, apagada, com
    sincronização parcial ou desatualizada, a conta daquele banco some da soma
    (`BANK_ACCOUNTS_SQL` já tira conexões pausadas e apagadas) enquanto os investimentos em
    cache continuam — o gráfico mostraria uma queda que não houve. A foto confere o estado
    de cada conexão e de cada produto e, se algum não estiver em dia, grava o ponto marcado
    como incompleto, dizendo qual banco. "Em dia" não pode vir só do provedor
    (`PARTIAL_SUCCESS`, `isUpdated`): quando a busca ou a gravação dos investimentos falha
    num item cujas contas sincronizaram, `_sync_pluggy_item_confirmado`
    (`core/services/pluggy_sync.py`) marca `investments_ok=False` mas grava a conexão como
    `ACTIVE`, sem motivo. Então a sincronização passa a gravar, **por conexão e por
    produto** (contas, investimentos), a hora do último sucesso local, e a foto usa isso;
  - **o conjunto de bancos muda, a linha quebra:** conectar, desconectar ou pausar um banco
    muda o que entra na soma, e o gráfico mostraria um salto que não é ganho nem perda.
    Desconectar apaga a conexão (`disconnect_open_finance_connection`), então no dia
    seguinte não sobra conexão para conferir. Por isso cada foto guarda **a sua
    cobertura**: quais conexões e quais contas entraram nela, e quais itens ficaram fora por
    moeda. O gráfico quebra a linha (com a legenda do que mudou: "banco conectado",
    "desconectado", "conta em outra moeda") quando a cobertura muda de um ponto para o
    outro, em vez de ligar os dois pontos como se fosse variação — corrigir a moeda de uma
    conta de BRL para desconhecida tiraria o saldo dela, e isso não é perda. O mesmo vale para **todo número derivado da série**
    (a variação no título do bloco, o texto acessível do período — hoje
    `widgets/NetWorth.tsx` faz `último − primeiro` sem olhar nada): ele só é calculado
    dentro do trecho sem quebra, e o bloco diz que o banco X entrou ou saiu no período.
    Ponto marcado como incompleto ou incerto também quebra o trecho: variação que começaria
    ou terminaria nele não é calculada, e o bloco diz por quê;
  - **"a conferir" não vira número certo:** com movimento de banco pendente
    (`bank_movements.pending_count` > 0), o painel antigo já troca o patrimônio por "A
    conferir" (`frontend/dashboard.js`), porque o dinheiro pode estar nos dois lados. A foto
    desse dia é gravada marcada como incerta, e o gráfico a mostra assim — nunca como ponto
    exato.

  **Uma foto por usuário por dia:** restrição única em `(user_id, dia)`, e a foto guarda
  **a hora da leitura** — tirada no **primeiro comando** da transação (`select
  clock_timestamp()`), que é o que fixa a visão `REPEATABLE READ`; a hora do `BEGIN` não
  serve, porque a visão só nasce no primeiro comando. Em `REPEATABLE READ`, a gravação que
  esbarra numa linha que outra instância acabou de confirmar aborta com erro de
  serialização (`40001`): nesse caso a foto **refaz a leitura inteira** numa transação nova
  e tenta de novo, poucas vezes, antes de desistir daquele usuário até a rodada seguinte. A gravação só substitui a do
  mesmo dia se a leitura dela for mais nova — senão uma instância que leu antes e terminou
  depois gravaria o saldo velho por cima do novo.

  Testes: duas rodadas do job ao mesmo tempo, a que leu antes terminando depois, e a
  que leu antes confirmando primeiro com a mais nova esbarrando nela (nos dois fica um ponto
  só, com o valor da leitura mais nova); caixinha ligada a um CDB do
  Open Finance (conta uma vez só); posição em dólar,
  solta e ligada a uma caixinha (fica fora e aparece o aviso); foto no meio de um aporte
  (conta uma vez); foto com transferência de banco pendente (marcada como incerta), e a
  sincronização resolvendo a pendência no meio da foto (continua incerta); lançamento
  fundido entre carteira e banco (não debita duas vezes); reset começando antes da foto e
  a foto esperando por ele (a foto velha não volta); conta ou posição sem moeda informada,
  gravada antes e depois do conserto da ingestão (fica fora e aparece o aviso); conexão
  que fica pausada ou parcial entre duas rodadas (o ponto sai marcado como incompleto, sem
  queda falsa); falha só em `/investments` com as contas em dia (incompleto); conta de
  banco em dólar sem nenhum investimento em dólar (fica fora e aparece o aviso); desconectar e
  conectar um banco entre duas rodadas (a linha quebra, sem salto, e a variação do título
  não atravessa a quebra).
- **Dado do Open Finance desatualizado nunca aparece como exato**, em bloco nenhum — não só
  na foto. Pausar a conexão (`pause_open_finance_connection`) mantém o espelho, e a caixinha
  espelhada para de ser atualizada (`sync_open_finance_caixinhas`), então qualquer número
  feito dela (a reserva em meses, o saldo, o patrimônio de hoje) seguiria com cara de
  certo. Por isso toda resposta da `/api/v2` que usa dado do Open Finance leva, por
  conexão e produto, a hora do último sucesso local (a mesma que a foto usa) e um estado
  `em_dia | desatualizado`. Desatualizado não depende de pausa ou erro explícito: é
  **último sucesso mais velho que o dobro do intervalo de atualização daquele produto**
  (a sincronização automática do provedor é diária, então 48 horas; o PR confere o
  intervalo real de cada produto e guarda o limite numa constante só, que a foto também
  usa). Como o estado muda com o relógio, sem escrita nenhuma, a resposta traz também
  **até quando ela vale** (`em_dia_ate`), e o cliente agenda uma nova busca para esse
  instante — senão uma tela aberta antes do limite continuaria dizendo "em dia" depois
  dele, já que o aviso em tempo real só dispara com escrita. Teste: a tela fica aberta
  enquanto o limite passa e muda para desatualizado sozinha. Desatualizado: o número vem acompanhado do aviso, e onde ele
  seria uma conclusão (meses de reserva, % do CDI) a API devolve `null` com o motivo
  `banco_desatualizado`. Teste por bloco: conexão pausada, falha só em `/investments`, e
  conexão ativa cujo último sucesso passou do limite sem erro nenhum, incluindo a reserva
  numa caixinha espelhada.
- **Tabela nova por usuário entra no ciclo de privacidade.** As fotos do patrimônio e das
  posições são histórico financeiro do usuário, e `db/privacy.py` enumera as tabelas à mão.
  Toda tabela nova com dado de usuário entra, no mesmo PR que a cria, na exportação
  (`build_user_export_zip`), no "Recomeçar do zero" (`_RESET_TABLES` — senão o painel
  reconstruído mostra pontos velhos) e na exclusão de conta (`delete_user_data`, com a
  chave estrangeira em cascata). Testes do ciclo: o dado aparece na exportação e some no
  reset e na exclusão.
- **Rendimento × CDI:** a série do CDI já existe (`db/investments.py`), mas o lado da
  carteira não tem histórico: `investments`, `investment_lots` e `open_finance_investments`
  guardam só o saldo atual, sobrescrito a cada juro ou sincronização. A regra: **nenhum
  número inferido**, e a fonte é só o Open Finance (Q36):
  - **Open Finance:** a rentabilidade que o próprio banco calcula por posição
    (`lastMonthRate` e `lastTwelveMonthsRate`, que o código já lê em `db/rv.py`). Das fotos
    não dá para tirar isso: dois movimentos que se anulam entre duas sincronizações somem
    no fluxo líquido. A taxa é gravada **a cada sincronização com sucesso**, por posição e
    **pelo mês a que ela se refere** — não pelo mês da sincronização: uma sincronização de
    outubro traz o `lastMonthRate` de setembro. Hoje o código não lê nenhuma data de
    referência do Pluggy; o primeiro passo do PR é medir, na API real, se o conector
    informa o período. Se informar, a chave é esse mês. Se não informar, a série mensal
    dessa posição não existe: o bloco mostra a taxa como "último mês informado pelo banco",
    sem comparar com um mês de CDI, em vez de adivinhar o mês. Teste: sincronização logo
    depois da virada não sobrescreve o mês anterior nem joga a taxa no mês novo. A gravação
    é dentro de `save_open_finance_investments`
    (`db/open_finance.py`) e antes de sobrescrever ou apagar a linha do espelho — não pelo
    job diário, que chegaria tarde para a posição liquidada entre duas rodadas. O histórico
    fica numa tabela própria, que a reconciliação não apaga, e **cada observação guarda
    junto as datas que o banco mandou naquele momento** — o período da taxa e as datas da
    posição do usuário. A linha viva do espelho é apagada quando a posição some ou o banco é
    desconectado, e a comparação precisa dessas datas depois; tirá-las da linha viva
    misturaria vidas diferentes do mesmo id. No histórico, a posição que o banco deixou de
    mandar continua na série dos meses em que existiu, marcada como encerrada. Desconectar
    o banco (`disconnect_open_finance_connection`, em `db/open_finance.py`) não passa pela
    sincronização — ele apaga a conexão e a cascata leva as posições —, então a mesma
    transação marca o histórico de cada posição daquela conexão como encerrado, com a data
    da desconexão, e o histórico fica. Teste: desconectar e consultar o histórico. A cascata do histórico fica só para o
    usuário, pela regra de privacidade abaixo.
  - **Manuais:** não entram (Q36): no v2 não existe investimento manual. O que já existe
    de manual fica só para leitura (Q37): aparece com o saldo, sem comparação com o CDI.

  **O bloco compara cada investimento com o CDI, e não mostra número da carteira inteira**
  (decisão do dono, 2026-09-25). No Open Finance o banco não diz quando o dinheiro entrou ou
  saiu, então não existe rentabilidade exata da carteira somada; um número aproximado com
  cara de exato é o que este plano proíbe. No Open Finance vale **uma regra só**, para o mês
  e para os 12 meses: a taxa do banco só é comparada com o CDI de um período que **se sabe
  exatamente qual é** e em que **a posição esteve o tempo todo**. Saber o período: o banco
  informa o mês de referência (para `lastMonthRate`) ou o início e o fim exatos (para
  `lastTwelveMonthsRate`, cujo fim pode ser o dia da sincronização, o fim do mês anterior…).
  Estar o tempo todo: **só o banco prova** — pelas datas **da posição deste usuário**
  (quando ele aplicou e, se for o caso, quando resgatou). Datas do título (emissão,
  vencimento) não servem: um título emitido em janeiro e comprado em março não prova nada
  sobre fevereiro. Sincronizações nas pontas do
  período não provam nada: a posição pode sumir e voltar com o mesmo id entre duas delas sem
  deixar rastro. O primeiro passo do PR é medir, na API real, quais datas o conector manda.
  Sem as datas do período e da posição, a taxa aparece sem comparação, com o motivo. Investimento sem rentabilidade informada (o banco não mandou a
  taxa, renda variável, cripto) aparece sem a comparação, com o motivo. Como o patrimônio,
  nada de reconstruir o passado: enquanto o histórico enche, o bloco diz que se completa com
  o tempo. O widget do protótipo (`widgets/Yield.tsx`) mostra a carteira somada; ele passa a
  ser por investimento quando for ligado à API, na etapa do Resumo.

  **Testes do PR do job** — Open Finance:
  investimento sem taxa; conector sem mês de referência ou sem as datas dos 12 meses (sem
  comparação); posição sem datas próprias do banco (sem comparação, mesmo vista em várias
  sincronizações); posição com datas que não cobrem o período inteiro — aberta ou resgatada
  no meio (sem comparação); sincronização logo depois da virada (a taxa vai para o mês de
  referência, não para o da sincronização); posição com só emissão e vencimento do título
  (sem comparação); posição
  liquidada entre duas rodadas do job (a taxa da última sincronização fica no histórico).
- **Reserva em meses:** reserva dividida pelo custo mensal das contas fixas ativas. O custo
  mensal converte cada frequência de `db/recurring.py` (`VALID_FREQUENCIES`): diária × 365/12,
  semanal × 52/12, mensal × 1, anual ÷ 12; pagamento único (`once`) não entra. Conta de valor
  variável sem estimativa (guardada com valor 0) fica fora da soma, e o bloco diz quantas
  ficaram. A condição do vazio é o **total**, e ela se avalia **depois** de tirar o que não
  entra na soma: com total zero a API devolve `meses: null` com o motivo —
  `sem_contas_fixas` (nenhuma conta recorrente ativa; pagamento único não conta) ou
  `sem_valor` (há contas recorrentes, mas nenhuma com valor) —, nunca infinito nem erro, e
  o bloco pede o que falta. Testes de contrato e de tela: cada frequência, a conta variável
  sem estimativa, só pagamento único ativo (`sem_contas_fixas`), e os dois motivos do
  vazio. Hoje nada marca qual caixinha
  é a reserva: só há o palpite pelo nome em `core/services/piggy_agents.py` (`_is_reserva`).
  Por isso a caixinha de reserva passa a ser **designada pelo usuário** (um campo na
  caixinha, no máximo uma por usuário). Sem designação, o bloco pede para escolher, e o
  palpite pelo nome só sugere. Pela regra de uma fonte só (Q18), o agente que usa o palpite
  passa a ler o campo no mesmo PR (etapa 4, Metas). Só caixinha em reais pode ser a
  reserva: a espelhada de uma posição em outra moeda, ou de moeda desconhecida (regra do
  patrimônio acima), não pode ser escolhida, e se já estiver escolhida a API devolve
  `meses: null` com o motivo `moeda_estrangeira` — dólar não vira real na conta da reserva.
  Teste: reserva numa caixinha ligada a posição em dólar.
- **Perfil e layout do Resumo:** perfil no servidor (coluna com `CHECK` nos ids
  `economizar|investir|controlar|dividas|autonomo|padrao`); layout segue no navegador.

## 3. O front

- **Busca de dados:** TanStack Query (cache, recarregar ao voltar para a aba, repetição,
  atualização otimista ao lançar) (Q24). O app novo pode usar a mesma biblioteca.
- **Bundle:** artefato commitado em `frontend/` com trava de rebuild no CI, como
  `precos-app.*` e `chat-app.*` (Q7). Os caminhos de asset passam a ser absolutos.
- **Plano real:** o gate de plano vem do `/auth/me` (hoje o protótipo lê `?plano=` da URL).
- **Um PR por tela** (Q8). Tela que só consome a API é faixa Leve; desde 2026-09-25 a
  faixa Leve vai **com o time, na versão leve** (decisão do dono que substituiu o
  experimento com × sem).
- **Testes (Q32):** pytest com Postgres real (isolamento e contrato por rota); Playwright
  com respostas simuladas a partir de exemplos gerados pelo contrato; ponta a ponta com
  servidor e banco de teste só nos fluxos críticos (abrir o Resumo, lançar um gasto, o aviso
  em tempo real chegar).

## 4. Ordem

**Antes de tudo (Q34):** terminar o protótipo do chat: o PR #584 (página do chat) e o PR 3
dos blocos que expandem na conversa (estado por resposta, "Abrir no painel"). A interface
fica; só as respostas prontas saem quando a IA real entrar.

| Etapa | O que entra | Faixa |
|---|---|---|
| 0 | Esqueleto da `/api/v2` (dependência de usuário, envelope de erro, contrato + tipos gerados, SSE), `/painel` servido com a chave e os links, plano real pelo `/auth/me`, cliente TanStack Query, job da foto diária do patrimônio e o histórico da taxa do Open Finance por sincronização | Completo |
| 1 | Resumo (perfil no servidor entra aqui) | API Completo, tela Leve |
| 2 | Lançamentos: ver tudo; lançar, editar e apagar na carteira Piggy (Q36) | idem |
| 3 | Previsão | idem |
| 4 | Metas e caixinhas: a manual com depositar e retirar, a espelhada pelo Open Finance (Q38) | idem |
| 5 | Para onde vai | idem |
| 6 | Patrimônio (com o histórico que o job da etapa 0 já vem gravando) | idem |
| 7 | Chat com IA real e blocos: o `/ai/chat` passa a devolver "texto + blocos" a partir das tools que usou | Completo |

**Depois:** migrar as demais telas uma a uma (as "em breve" de Ferramentas e as que abrem
no antigo), tema claro, abrir para mais gente, e o corte final em dois passos, porque o app
atual (Capacitor) carrega o `/app` ao vivo e nunca pode mostrar o v2:

1. `/app` passa a abrir o v2 no navegador; com o marcador `PigBankApp` no user agent ele
   continua servindo o painel antigo. Aqui o user agent escolhe *qual tela* (os dois são
   dados do próprio usuário), não concede acesso — segue valendo a regra de acima.
2. Apagar o `dashboard.html`, o `dashboard.js` e o `/ws` **só depois de o app atual sair
   de circulação**: o app novo publicado e uma versão mínima obrigatória que tire o
   Capacitor de uso. Até lá, o painel antigo e os assets dele ficam. Teste: user agent com
   `PigBankApp` abrindo `/app` recebe o painel antigo; sem ele, o v2.

## 5. Andamento

- [x] Protótipo: perfis do Resumo (#573, #575), faixa do Piggy (#579), navegação com o
  Piggy no meio e Ferramentas (#582).
- [x] Protótipo: página do chat (#584).
- [ ] Protótipo: blocos que expandem na conversa (em outro chat).
- [ ] Etapa 0 · [ ] 1 · [ ] 2 · [ ] 3 · [ ] 4 · [ ] 5 · [ ] 6 · [ ] 7
