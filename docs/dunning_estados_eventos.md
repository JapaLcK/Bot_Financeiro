# Inadimplência de cartão — a máquina de estados, enumerada

Esta é a tabela de **estados × eventos** do relógio de inadimplência
(`auth_accounts.past_due_since`). Ela existe porque o subsistema levou
**rodadas seguidas** de apontamentos e cada conserto foi feito como transição
isolada — o mecanismo 2 do registro do PR #60 no `CLAUDE.md` §4 ("remendar
transição em vez de enumerar a máquina"). A regra de lá é a razão deste arquivo:
*"se duas rodadas seguidas batem no mesmo subsistema, pare de remendar: enumere
estados × eventos por escrito e feche tudo num commit."*

Quais rodadas apontaram o quê está na coluna "quem achou" da tabela "O que a
enumeração fechou", no fim deste arquivo. **A contagem de rodadas não vem
escrita aqui de propósito**: ela sobe a cada rodada e envelhece em silêncio
(§2) — este parágrafo já dizia "três" quando eram mais.

Vocabulário e constantes: `core/services/billing_dunning.py`.
Escrita: `db/dunning.py`. Leitura: `core/services/payment_reminder.py`.
**Nada aqui tira acesso de ninguém** — o relógio alimenta TRÊS coisas: o
lembrete do 6º dia, a janela de dedupe do e-mail de falha e, desde o PR do
aviso de corte, o predicado `core.services.billing_dunning.carencia_aberta`.

Esse terceiro é o lado DIREITO do OR de `plan_service.tem_direito_hoje`
(`_tem_plano_pago_vigente(user) or carencia_aberta(...)`), consumido por
`scripts/aviso_fim_do_gratis.py`. **A direção do OR é o que mantém as células
18, 29 e 30 fora daquele trabalho**: a autoridade é o direito pago e o relógio
só CONCEDE tempo a quem já o perdeu. Quem for escrever o gate de acesso (o PR
seguinte) reusa aquele predicado em vez de ler o status como autoridade — do
contrário a célula 29 bloqueia cliente pagante por um ciclo de retentativa.

**O gate FOI escrito (PR A, #274/#354)** e fez exatamente isto: `has_app_access`
consulta `tem_direito_hoje` em vez de devolver `True` incondicional, e
`needs_plan_selection` continua intocado. Os dois recados do PR do aviso viram,
então, registro do que foi cumprido: (a) o predicado foi REUSADO, não recriado —
o status nunca virou autoridade, e `tests/test_access_gate.py` amarra isso com
controle negativo próprio; (b) o dono decidiu
cortar SEM aviso prévio a população só-WhatsApp (sem linha em `auth_accounts`,
"a maioria" segundo `core/handle_incoming.py`), então a **mensagem de bloqueio
do bot é a única comunicação que ela recebe** — ela tem de fazer sentido para
quem nunca viu o dashboard e não tem conta web.

---

## Os eixos

**Estado** = o par (`past_due_since`, `last_payment_status`). `IN` = status em
`PAST_DUE_PAYMENT_STATUSES` (`past_due`, `unpaid`, `incomplete`); `OUT` = fora.

| | relógio | status | nome | significado |
|---|---|---|---|---|
| **S1** | NULL | IN | *atraso sem ciclo* | o Stripe ainda diz atraso, mas nenhum ciclo nosso está aberto (pagou e o status não alcançou) |
| **S2** | NULL | OUT | *normal* | `active`, `trialing`, `canceled` |
| **S3** | T | IN | *ciclo aberto* | o estado da inadimplência; é o único que o lembrete lê |
| **S4** | T | OUT | **órfão** | a INVARIANTE declara impossível. Mantida na escrita por `db_support.set_payment_status_impl` e pelo SQL cru de `core/admin_dashboard.set_account_plan` — os **dois únicos** writers de `last_payment_status` em Python (varredura: `grep -rn "last_payment_status" --include="*.py" --include="*.sql" --exclude-dir=.venv .`; dos dois `scripts/*.sql` que a varredura acusa, só `backfill_pro_grandfather.sql` ESCREVE a coluna — `reset_plan_trials_for_stripe_trial_model.sql` apenas FILTRA por ela num `where`; ambos de reparo manual, aceitos) |

**A coluna `COMPORTAMENTO` não é "hoje" em toda linha, e o marcador diz qual
é qual.** Ela se chamava `HOJE` e passou a mentir nas células que foram
consertadas depois de escritas — o registro discordava de si mesmo dentro do
mesmo arquivo (apontado pelo Codex na rodada 12). Três marcadores, e eles valem
para as SETE tabelas abaixo:

| marcador | leitura |
|---|---|
| (sem marcador) | comportamento ATUAL, e a coluna `DEVERIA` confirma com ✔ |
| **`FECHADA (rodada N) — era:`** | comportamento ANTERIOR ao conserto. O que roda hoje é o da coluna `DEVERIA`; a descrição antiga fica porque o defeito é a parte que ensina |
| **`✗ ABERTA —`** | comportamento ATUAL e ERRADO, de propósito. A razão de continuar aberta está na seção da célula |

**Validade do evento**, o terceiro eixo. Ela sai do contrato de retorno de
`db.plan_grants.upsert_grant`, que é o **único** produtor de `event_version` de
grant `stripe` em produção (`_materializar_assinatura` é o único chamador):

| validade | `upsert_grant` | `_materializar_assinatura` |
|---|---|---|
| **NOVO** — versão estritamente maior | `id` | `True` |
| **REENTREGA** — versão IGUAL (mesmo evento, ou irmão do mesmo segundo) | `id` | `True` |
| **VELHO** — versão estritamente menor | `None` | `False` |

O `invoice.payment_failed` **não escreve grant**, então ele **não avança a
marca d'água de versão**. Essa assimetria é a origem do defeito do Codex 4 e
está isolada na célula E2/REENTREGA abaixo.

---

## E1 — `checkout.session.completed` · E2 — `invoice.paid` / `invoice.payment_succeeded`

Os dois ramos têm a **mesma forma**: `_materializar_assinatura` (que grava o
grant, depois `set_payment_status(status LIVE do Stripe)`, depois
`recompute_entitlement`) e só então o clear. **O estado que o clear vê já passou
pelo `set_payment_status`** — é por isso que a coluna "estado no clear" existe.

| # | validade | estado antes | status LIVE | estado no clear | COMPORTAMENTO | DEVERIA |
|---|---|---|---|---|---|---|
| 1 | NOVO | S2 | qualquer | NULL | clear (no-op) | no-op ✔ |
| 2 | NOVO | S3 | OUT (`active`) | NULL — `set_payment_status` já zerou | clear (no-op) | no-op ✔ |
| 3 | NOVO | S3 | IN (consistência eventual, ou outra fatura aberta) | S3 | **clear** | clear ✔ — é a única razão de esta linha existir |
| 4 | NOVO | S4 | OUT | NULL | no-op | no-op ✔ |
| 5 | REENTREGA | S3, relógio carimbado **depois** deste evento | IN | S3 | **FECHADA (rodada 3) — era: clear** | **NÃO limpar** — e é o que roda hoje, pelo predicado `nao_mais_novo_que` |
| 6 | REENTREGA | S3, relógio carimbado **antes** deste evento (5xx entre o grant e o clear) | IN | S3 | clear | clear ✔ |
| 7 | VELHO | qualquer | — (nem consulta) | inalterado | no-op (gate `_decidiu_acesso`) | no-op ✔ |
| 30 | NOVO | S3, relógio da assinatura **A** — e o evento é de **B** | OUT (`active`, de B) | NULL — o `CASE` de `set_payment_status_impl` já zerou | **✗ ABERTA —** o relógio de A morre no `paid` de B | ver a seção da célula |

**As linhas 2 e 30 têm as três colunas do meio IGUAIS e vereditos opostos**, e
isso não é erro de tabela: o que as separa é **de qual assinatura o evento é**,
e esse eixo não é nenhum dos três declarados (estado, evento, validade). Não é
por descuido — é o próprio defeito. O estado é o par de colunas de
`auth_accounts`, que são **por USUÁRIO**, então o registro não tem onde guardar
"o relógio é da assinatura A". É a mesma raiz das células 29 e 30, escrita na
gramática da tabela: **o eixo que falta na tabela é o eixo que falta no
schema.** Enquanto ele faltar, a distinção mora na coluna "estado antes", em
texto, como na linha 30.

**A célula 5 é o apontamento do Codex 4** (`frontend/finance_bot_websocket_custom.py:5201`).
O gate `_decidiu_acesso` fecha a 7 e **não** fecha a 5: `upsert_grant` devolve o
`id` de propósito para versão igual, e o `payment_failed` que abriu o ciclo novo
não moveu a versão do grant. A célula 6 é o que impede "resolver" isto trocando
o gate por "o upsert APLICOU?" — nela o evento é o mesmo, a versão é igual, e o
clear **precisa** rodar.

**A célula 5 vale igual no `checkout.session.completed`** (E1), que o
apontamento não cita. É a classe, não a instância (§2).

**O conserto**: o predicado sai do call site e entra na escrita, como já se fez
com o `claim` na rodada 2 —
`clear_past_due_since(user_id, nao_mais_novo_que=<created do evento>)`, que vira
`where user_id = %s and past_due_since <= to_timestamp(%s)`. Ele separa a 5 da
6 pela ÚNICA coisa que as distingue (a idade do relógio contra a idade do
evento), e de quebra continua fechando a 7.

> **Teto conhecido, declarado**: o relógio é carimbado com o `now()` do **nosso**
> banco e o evento traz o `created` do **Stripe**. Se processarmos o
> `payment_failed` com atraso MAIOR que o intervalo entre a falha e o pagamento
> (endpoint fora do ar por horas), a célula 3 vira no-op e o relógio sobrevive.
> Direção do erro: o relógio FICA, e some sozinho no próximo status fora da
> lista (`set_payment_status_impl`); o pior caso é um lembrete verdadeiro
> ("a cobrança está pendente") para uma conta que o Stripe ainda chama de
> `past_due`. A alternativa — carimbar o relógio com o `created` do evento falho
> — troca isso por um erro PIOR: um falho reentregue com `created` de dias atrás
> carimbaria um relógio já fora da janela do lembrete, e o lembrete daquele
> ciclo nunca sairia. A coluna serve dois papéis (âncora da JANELA e âncora da
> ORDEM) e `now()` é a resposta certa para o primeiro.

### Célula 30 — o relógio é por CONTA e a assinatura em atraso é OUTRA. **ABERTA, com recusa fundamentada**

**Estado e evento.** A conta tem DUAS assinaturas na Stripe: **A** em atraso —
`last_payment_status` ∈ {`unpaid`, `incomplete`} — com o relógio carimbado pelo
`payment_failed` dela, e **B** saudável. Chega o `invoice.paid` de **B**:
`_materializar_assinatura` grava o grant de B e escreve
`set_payment_status(user_id, 'active')`. O relógio de A morre, e o lembrete
daquele ciclo nunca sai.

**Alcançabilidade, por LEITURA DE CÓDIGO** — não por medição, e a distinção
importa porque o bloco ao lado traz medição de verdade: o lado Stripe não é
exercitável neste ambiente, então o que sustenta este parágrafo é o código
citado, não uma saída. `POST /billing/create-checkout` TEM guarda contra
segunda assinatura (`_billing_checkout_for_user` → `_find_active_subscription`
→ 409 `already_subscribed`), mas ela consulta a Stripe só em `active`,
`trialing` e `past_due`. `PAST_DUE_PAYMENT_STATUSES` é (`past_due`,
`unpaid`, `incomplete`). **A interseção é `past_due` e nada mais** — logo uma
assinatura em `unpaid` ou `incomplete` não aparece para a guarda e **não
bloqueia checkout novo**. A sobreposição é alcançável pelo próprio produto, sem
corrida e sem painel da Stripe: a pessoa deixa a cobrança vencer até `unpaid` e
assina de novo pela /precos.

**Quem apaga o relógio neste caminho é o `CASE`, não o clear** — e a ordem é o
oposto do que uma primeira leitura sugere. O apontamento é o `CASE` de
`db_support.set_payment_status_impl` (o que preserva o relógio só quando o
status novo está na lista). Em `_materializar_assinatura` o
`set_payment_status(uid, 'active')` roda **antes** do
`clear_past_due_since(user_id, nao_mais_novo_que=_event_version(event))` do
ramo, então o `CASE` zera primeiro e o clear opera sobre NULL. Medido
(2026-09-09, conta em S3, os dois writers chamados na ordem do ramo; remedir
antes de reusar):

    apos set_payment_status('active') : past_due_since=None, status='active'
    apos clear_past_due_since         : past_due_since=None, status='active'

É a coluna "estado no clear" da linha 30 dizendo o que já dizia, e é o
vocabulário da célula 2: **clear (no-op)**. Mesmo par no
`checkout.session.completed`.

> **Uma versão anterior desta seção afirmava o contrário** — "gatear o `CASE` é
> no-op, porque o clear explícito apaga igual" — e a medição derrubou a
> afirmação em três frentes. Fica registrada porque o erro é a parte que ensina,
> e porque ele é o de sempre: concluir da instância para a linha. **(i)** A
> ordem inverte quem é o no-op, como acima. **(ii)** O clear **não** apaga em
> todo caminho: o predicado é `past_due_since <= to_timestamp(%s)`, então
> relógio mais NOVO que o `created` do evento sobrevive a ele (medido com
> `created` 120 s antes do relógio: o relógio fica) — é o "Teto conhecido,
> declarado" acima, cuja última cláusula ("some sozinho no próximo status fora
> da lista") já era a admissão de que quem apaga ali é o `CASE`. **(iii)** A
> linha apontada serve MAIS call sites do que este ramo, e um deles não tem
> clear nenhum: `core.services.billing_access.recompute_entitlement` escreve
> `'active'` com grant Pix vigente (na passada de 60 s do loop, a mesma do
> cabeçalho do E5 — não numa varredura diária, como esta frente já disse
> errado), e `clear_past_due_since` só aparece nas ramificações do webhook
> (`grep -rn "clear_past_due_since" --include="*.py" .`). Ali o `CASE` decide
> sozinho, sempre, sem corrida — são as **células 19 e 20**. E isto vem com o
> vermelho que o prova: forçar `preserva_relogio = True` em
> `set_payment_status_impl` (o `CASE` gateado na sua forma mais crua) derruba
> `test_billing_dunning_webhook.py::test_T4_grant_pix_nao_deixa_relogio_orfao`
> mais `test_billing_dunning.py::test_invariante_set_payment_status[active-False]`
> e `[canceled-False]`.

**O que sustenta a recusa é uma coisa só: gatear os dois produz S4.** O estado
resultante
(`past_due_since` = T de A, `last_payment_status` = `active`) é **S4**, o órfão
que o próprio `CASE` existe para impedir. E ele não produz o
lembrete que se queria salvar: o funil e `lembrete_ainda_vale` exigem status na
lista (**célula 26**), então o relógio preservado fica invisível. Pior, ele
envenena o ciclo seguinte — é a **célula 12**: o próximo `payment_failed` acha
`rowcount 0` e o ciclo novo herda a data velha. **Aqui o "e o lembrete daquele
ciclo também nunca sai" vale para ESTA célula e não como universal**, e a
diferença foi medida (2026-09-09; remedir antes de reusar): forjando S4 e
repondo o status na lista, uma data velha de −6,5 d cai DENTRO da janela
`[6,9)` e `list_payment_reminder_candidates(7)` devolve a conta. O que mata o
lembrete é a data herdada estar FORA da janela — que é o caso da célula 30, onde
o relógio de A tem semanas quando B renova. Trocaríamos um lembrete perdido por
um órfão mais um ciclo cuja janela passa a depender da idade do relógio velho.

**Dano.** Um lembrete de pagamento perdido. **Nenhuma perda de acesso** — este
PR não tem gate, e o `plan`/`plan_expires_at` de B é legítimo. E a copy de hoje
(`core.services.email_service.send_payment_reminder_email`: "a cobrança do
**seu plano** não passou… atualizar o cartão leva menos de um minuto") seria
**FALSA** nesse estado: a conta tem plano pago e cartão bom, via B. Consertar o
relógio sem trocar a copy trocaria lembrete perdido por lembrete mentiroso.

**Relação com a célula 29** — mesma raiz, gatilho diferente, e é por isso que
esta é célula própria e não um rodapé daquela:

| | célula 29 | célula 30 |
|---|---|---|
| raiz | `last_payment_status` por USUÁRIO × `plan_grants.event_version` por ASSINATURA | a mesma |
| gatilho | precisa de duas requisições INTERCALADAS (o `paid` escrevendo dentro da suspensão do ramo falho) | **nenhum intercalamento** — é estritamente sequencial |
| escrita culpada | o `set_payment_status('past_due')` do ramo falho | o `set_payment_status('active')` do ramo pago (o clear que vem depois já acha NULL) |
| dano | e-mail de falha errado numa conta paga | lembrete de cobrança que nunca sai |

Fechar de verdade é o mesmo trabalho que a 29 pede: marca d'água **por
usuário**, ou relógio por assinatura. Ver as perguntas-portão no fim deste
arquivo.

---

## E3 — `invoice.payment_failed`

Duas guardas, as duas da rodada 2: (a) `Subscription.retrieve` — status LIVE
fora da lista ⇒ o ramo inteiro é ignorado e logado; (b) `_sub_id` ausente
(fatura avulsa) ⇒ não carimba. O `claim` é `past_due_since is null AND status
IN`.

| # | validade | estado antes | status LIVE | COMPORTAMENTO | DEVERIA |
|---|---|---|---|---|---|
| 8 | qualquer | qualquer | OUT | ignorado + `billing_payment_failed_obsoleto` | ✔ |
| 9 | qualquer | qualquer, sem `_sub_id` | IN | grava `past_due`, **não** carimba | ✔ (fatura avulsa não tem ciclo de assinatura) |
| 10 | NOVO | S2 | IN | carimba; `_abriu_ciclo=True` ⇒ e-mail com `dedup_days=0` | ✔ |
| 11 | NOVO | S3 | IN | `rowcount 0`; e-mail dedupado por `DUNNING_GRACE_DAYS` | ✔ (smart retry do mesmo ciclo) |
| 12 | NOVO | S4 | IN | preserva o relógio VELHO ⇒ ciclo novo nasce fora da janela | seria bug — **S4 é inalcançável**, ver o eixo de estados |
| 13 | REENTREGA | S3 | IN | `rowcount 0`, não reinicia | ✔ |
| 14 | REENTREGA | S1 | IN | **✗ ABERTA —** carimba `now()`, `_abriu_ciclo=True` ⇒ **2º e-mail de falha** | ver a ressalva abaixo |
| 15 | VELHO | S1/S2 | IN | carimba `now()` | ✔ — o Stripe diz que há atraso AGORA; a data é de hoje e o lembrete sai no prazo |

> **Ressalva da célula 14, aberta de propósito.** Para chegar nela a conta tem
> de estar em S1 (pagou, o clear rodou, e o status ficou na lista) e o Stripe
> tem de continuar respondendo um status da lista no `retrieve` da reentrega —
> que é uma leitura MAIS NOVA que a do `invoice.paid` que acabou de fechar o
> ciclo. A guarda (a) é quem fecha isso em produção. O custo se ela falhar é
> **um** e-mail "sua cobrança falhou" a mais; o conserto seria um segundo eixo
> de ordenação no `payment_failed`, que hoje não tem nenhum. Fica documentado,
> não codado.

O `payment_failed` **não tem guarda de versão** — o `_event_version` do ramo só
vai para o log. Quem faz o papel dela é a guarda (a), que consulta o estado
REAL em vez da ordem dos eventos. As células 13/15 mostram por que isso basta
para ordem de eventos — e a célula 29 mostra onde não basta.

### Célula 29 — a corrida check/write da guarda (a). **ABERTA, com recusa fundamentada**

| # | validade | intercalamento | COMPORTAMENTO | DEVERIA |
|---|---|---|---|---|
| 29 | qualquer | o `invoice.paid` de OUTRA requisição escreve `active` e zera o relógio **depois** de o `retrieve` deste ramo responder `past_due` e **antes** de a corrotina retomar e escrever | **✗ ABERTA —** grava `past_due` por cima do `active`, o `claim` acha status na lista + relógio nulo e **carimba ciclo novo**, e sai e-mail de "sua cobrança falhou" com `dedup_days=0` | não escrever nada: o evento pago é mais novo |

A guarda (a) é **snapshot**, não é atômica com a escrita. O predicado de status
do `claim` (rodada 2) protege a ordenação **oposta** — quando o pago escreve
DEPOIS do nosso `set_payment_status`, o `claim` vê status fora da lista e não
carimba (é o `test_R1_*` de `tests/test_billing_dunning_eventos.py`). Nesta
ordenação o pago escreve ANTES, e o nosso próprio `set_payment_status` repõe o
status na lista — então o predicado não tem como distinguir "está `past_due`
porque eu acabei de escrever por cima de um `active` mais fresco".

**Janela, medida** (2026-09-08, `perf_counter` nos dois extremos, 30 entregas,
Stripe de latência zero — remedir antes de reusar): do `retrieve` retornar até o
`set_payment_status` executar, **mediana 0,174 ms, máx 0,273 ms**. Essa é só a
parcela em processo; a janela real soma a meia-volta de rede do `retrieve`,
porque o valor fica velho no instante em que o Stripe o avalia.

**Gatilho.** Exige duas entregas do MESMO usuário em voo ao mesmo tempo, com a
escrita do ramo pago caindo dentro da suspensão do ramo falho. Smart retry do
Stripe são separados por horas ou dias, então sobram dois caminhos: reentrega de
`payment_failed` por 5xx coincidindo com um `paid`, e **usuário com DUAS
assinaturas** (uma pagando, outra falhando) — caso que este repositório
contempla explicitamente, ver o comentário do ramo `subscription.deleted`
("quem tem uma assinatura nova já paga e recebe o `deleted` da ANTIGA não pode
perder as duas").

**Dano.** `last_payment_status='past_due'` + relógio novo numa conta paga, mais
um e-mail errado. **Acesso NÃO é afetado** — este PR não tem gate, e
`plan`/`plan_expires_at` não são tocados por este ramo. O que doi é a duração:
**não existe ramo `customer.subscription.updated`** neste handler (são cinco:
`checkout.session.completed`, `invoice.paid`/`payment_succeeded`,
`trial_will_end`, `payment_failed`, `subscription.deleted`), e
`recompute_entitlement` só reescreve o status quando há grant **pix** vigente —
logo conta só-de-cartão **não se auto-cura até o próximo `invoice.paid`**, ou
seja até a renovação seguinte. E, com `PAYMENT_REMINDER_ENABLED` ligada, o
estado errado é consistente (S3 dentro da janela), então a revalidação de
`lembrete_ainda_vale` passa e sai **um segundo** e-mail errado no 6º dia.

**Por que fica aberta, e não é "custou complexidade demais".** É um
descasamento de tipo que torna errado todo predicado disponível:
`last_payment_status` é **por USUÁRIO** e a única marca d'água de ordenação do
schema é `plan_grants.event_version`, que é **por ASSINATURA** (`unique (source,
external_ref)`). Consequência:

* predicado com a ref DESTA assinatura ("não escreva se existe grant desta ref
  com `event_version` maior") fecha a corrida de uma assinatura e **deixa aberta
  a de duas** — seria um predicado com cara de atômico que não é, o erro de
  instância que este PR já pagou cinco vezes;
* predicado com QUALQUER ref ("não escreva se existe grant mais novo") fecha as
  duas e passa a **recusar falha legítima**: usuário com assinatura X renovando
  e assinatura Y falhando de verdade teria a falha de Y recusada porque o pago
  de X é mais novo.

Fechar de verdade exige marca d'água **por usuário** para
`last_payment_status` — coluna nova, invariante nova, e um **terceiro** writer
daquela coluna fora de `db_support.set_payment_status_impl`, cuja própria
docstring existe para enumerar e conter essa categoria. Em código de cobrança
ATIVO, na nona rodada, o remendo aumenta a superfície mais do que o achado
custa (`CLAUDE.md` §4).

**Divulgação honesta: a rodada 6 CRIOU esta corrida — não a alargou.** Antes
dela o `retrieve` era síncrono e não havia `await` entre a checagem e a escrita.
O `to_thread` (que consertou um bloqueio de event loop de **segundos**) criou o
ponto de intercalação em processo.

Isto foi escrito primeiro como "alargou", com a ressalva de que dependia de
quantos workers a produção roda, e a resposta foi conferida depois:
**`launch.py:26-33` sobe o uvicorn SEM `--workers`, ou seja um worker** (o
default). Logo não havia corrida entre processos para alargar — com um worker e
sem `await` entre checagem e escrita, o handler ia até o fim sem ceder, e
nenhuma outra requisição se intercalava. **Fomos de zero pontos de intercalação
para um.** A corrida é nossa, não é condição pré-existente.

A troca continua favorável, e é por isso que ela fica: o bloqueio custava o loop
inteiro congelado por uma chamada de API a CADA `payment_failed` (frequente, um
por smart retry de cada inadimplente), atingindo toda requisição e todo webhook
do processo; a corrida custa uma conta errada, sem perda de acesso, e exige
entrega simultânea de `paid` e `failed` do MESMO usuário. Mas quem lê isto
depois precisa saber que o defeito foi introduzido, e não herdado.

Se algum dia a produção passar a rodar `--workers > 1`, esta célula muda de
natureza: a corrida passa a existir também entre processos, onde nem a
serialização do event loop ajudaria, e o conserto por marca d'água **por
usuário** (abaixo) deixa de ser opcional.

**A categoria maior é "lê estado externo, escreve no nosso banco", e ela tem
três membros neste handler**, os três `Subscription.retrieve`:
`checkout.session.completed` (:5001) e `invoice.paid` (:5190) escrevem via
`_materializar_assinatura`, onde o `upsert_grant` recusa evento estritamente
mais velho e o `set_payment_status` **só roda se ele não recusou** — ordenação
imperfeita (versão igual passa, e o `expires_dt` continua vindo do snapshot),
mas não nula. O `payment_failed` é o **único cuja escrita de status não tem
ordenação nenhuma**, e é por isso que a corrida aparece aqui. Os dois primeiros
são anteriores a este PR e ficam como estão (§0.3).

---

## E4 — `customer.subscription.deleted`

| # | validade | estado antes | COMPORTAMENTO | DEVERIA |
|---|---|---|---|---|
| 16 | NOVO | S3, motivo NÃO-terminal (ou ausente/desconhecido) | `canceled` ⇒ o próprio `set_payment_status` zera o relógio; o clear é no-op | ✔ |
| 17 | NOVO | S4, motivo NÃO-terminal | idem | ✔ |
| 32 | NOVO | S3, `cancellation_details.reason == 'payment_failure'` | **PR A** — grava `unpaid` (PRESERVA o motivo, que `canceled` apagaria) e então `db.dunning.encerrar_ciclo_de_atraso`, INCONDICIONAL. Nesta perna o `CASE` de `set_payment_status_impl` NÃO zera (o status fica na lista): o clear é a ÚNICA coisa que tira o relógio | ✔ — evento terminal: não há cobrança a recuperar, logo não há ciclo a preservar |
| 18 | REENTREGA/VELHO | S3 de OUTRA assinatura viva | **✗ ABERTA —** `plan=free` + `canceled` + relógio zerado; `revoke_grant` é recusado por versão e `recompute_entitlement` devolve o plano, mas o status fica `canceled` | não deveria — **anterior a este PR** |

**O `subscription.deleted` sobrevive ao predicado**: nas três células o
`set_payment_status('canceled')` roda ANTES e já zerou o relógio no mesmo
UPDATE, então o clear daquele ramo é no-op em todo estado alcançável. Passar o
`created` do evento ali não muda comportamento nenhum — e é o que se faz, para
que a regra seja UMA só e nenhum call site futuro herde a versão incondicional.

### O ramo TERMINAL, e o par que ele grava

**Decisão do dono, verbatim:** *"Quando a Stripe encerrar definitivamente a
assinatura por inadimplência, grave `unpaid` e limpe **incondicionalmente** o
relógio de carência."* O motivo de gravar `unpaid` em vez de `canceled` é
PRESERVAR a razão da perda de acesso — `canceled` a apaga, e é ela que o painel
e o suporte precisam ler depois.

**Na célula 32 o clear é o OPOSTO das outras três**, e o comentário do ramo
chamava aquele clear de "REDUNDANTE hoje" sem qualificar a perna — afirmava o
contrário das duas, e foi reescrito. Com `unpaid` o status FICA na lista, o
`CASE` PRESERVA o relógio, e `encerrar_ciclo_de_atraso` é a única escrita que o
apaga. A ORDEM (`set_payment_status` antes, clear depois) é o que faz o par não
deixar órfão; invertida, o `CASE` apagaria antes e o clear viraria no-op.

Função IRMÃ, e não `clear_past_due_since(..., nao_mais_novo_que=None)`: dar
significado ao `None` transformaria o valor que um descuido produz no valor que
DESLIGA a proteção que o parâmetro obrigatório comprou.
`tests/test_dunning_encerramento_terminal.py` prende a contagem de call sites
da irmã em 1, e o caso dele tem o `created` do evento ANTERIOR ao carimbo do
relógio — a ÚNICA configuração em que as duas escritas divergem (com um
`deleted` em ordem a versão antiga limparia igual e o grupo não mediria nada).

#### O par `(plan, last_payment_status)` é lido pelo PAR em DOIS lugares

`unpaid` sozinho continua significando **"assinatura VIVA na Stripe, em
dunning"** — é a definição certa enquanto a assinatura existir, e é por isso que
ele **não** saiu de `core.admin_dashboard._LIVE_PAYMENT_STATUSES` (varredura:
`grep -rn "_LIVE_PAYMENT_STATUSES"` — UM leitor de produção, mais a definição e
o teste de paridade). O que muda o significado é o `plan`:

| par | leitura | painel | `/trial-reset` |
|---|---|---|---|
| `unpaid` + plano PAGO | assinatura viva em dunning | `past_due` | **409** — dar trial a quem tem assinatura viva é o que a guarda existe para impedir |
| `unpaid` + `plan='free'` | **COMPATÍVEL com** "a Stripe encerrou" (esta célula) — não prova disso | `canceled` | **libera** — decisão do dono: *"pode, libero caso a caso"* |

**A segunda linha diz "compatível", e não "é", de propósito.** Uma versão
anterior desta seção afirmava a implicação, e ela é falsa: o par tem **outros
produtores**, e neles a assinatura pode continuar viva na Stripe.

| produtor | como chega em `(free, unpaid)` | assinatura na Stripe |
|---|---|---|
| ramo terminal do `customer.subscription.deleted` (célula 32) | grava `unpaid` de propósito | **deletada** |
| `core.services.billing_access.recompute_entitlement` | `update_user_plan(uid, 'free', …)` sob veredito `reduz`, **sem tocar** em `last_payment_status`. A matriz depende de `_find_active_subscription`, que consulta só `active`/`trialing`/`past_due` — assinatura em `unpaid` é **invisível** para ela e conta como "nenhuma ativa". Loop de 60 s, sem admin e sem webhook | **pode estar viva** |
| `core.admin_dashboard.set_account_plan('free')` | **célula 24**: "não mexe no par" | **pode estar viva** |

**Medido** (2026-09-10, conta com grant vencido + `last_payment_status='unpaid'`,
`recompute_entitlement(origem="varredura")`, **nenhum** evento `deleted`;
remedir antes de reusar):

    antes  : pro  | unpaid
    depois : free | unpaid

Amarrado por
`tests/test_admin_users_panel.py::test_o_par_terminal_nao_prova_que_a_stripe_encerrou`,
que é executável de propósito: a ressalva escrita só em prosa envelheceria em
silêncio, e esta é a premissa de uma trava anti-abuso.

**O que sustenta a liberação, então, não é o par — é o HUMANO NO LAÇO.** É ação
de admin, uma conta por vez, e o dono disse "caso a caso". O custo de errar está
declarado: a trava cai, o checkout seguinte também não enxerga `unpaid` (mesma
cegueira do `_find_active_subscription`) e nasce a **segunda assinatura da
célula 30**. Agrava que o painel rotula o par como "Cancelado", que o admin lê
como "a Stripe encerrou".

**Um discriminador honesto existe e ficou de FORA**: o grant revogado com motivo
`stripe_subscription_deleted`, que só o ramo terminal escreve. É query nova num
caminho de admin, com a decisão já tomada e humano no laço — fica nomeado aqui
para quem quiser estreitar depois não ter de redescobrir.

**Estreitou-se a CONDIÇÃO, não a lista** (`encerrada_por_inadimplencia =
plan_atual == "free" and pay == "unpaid"`). Alargar/encurtar
`_LIVE_PAYMENT_STATUSES` consertaria o `/trial-reset` e estragaria a DEFINIÇÃO,
que o próximo leitor herdaria.

E a guarda **não** reusa `_derive_account_status`, apesar de ele já computar o
par: aquela função dobra `plan_expires_at` no veredito, e vencimento é do nosso
ENTITLEMENT, não de "a Stripe ainda tem assinatura". Reusá-la liberaria também o
pago VENCIDO em `unpaid` — dunning com assinatura viva, que ninguém autorizou.
Amarrado por `test_trial_reset_recusa_unpaid_com_plano_pago_vencido`.

**O espelho do painel veio junto e é obrigatório**: sem ele,
`_derive_account_status({'plan':'free','last_payment_status':'unpaid'})` devolve
`'free'` — o rótulo de quem NUNCA assinou — e o motivo se perde exatamente onde
o dono quis preservá-lo. O teste de paridade de `tests/test_admin_users_panel.py`
passou a ler o CASE **por território** (`plan='free'` × plano pago), porque
`unpaid` é o único status que os dois classificam diferente. Medido: mudar o SQL
sem o Python, ou o Python sem o SQL, continua ficando VERMELHO — que é o efeito
que aquele teste existe para ter.

A célula 18 continua aberta: quem a destrói é o `set_payment_status`, não o
clear, e gatear só o clear não fecharia nada (§0.3).

---

## E5 — `recompute_entitlement` (webhooks de cobrança e loop de 60 s)

Nunca escreve o relógio. Escreve `last_payment_status` num caso só: grant `pix`
vigente ⇒ `active` ⇒ status fora da lista ⇒ `set_payment_status_impl` zera o
relógio no mesmo UPDATE.

| # | origem | estado antes | COMPORTAMENTO | DEVERIA |
|---|---|---|---|---|
| 19 | evento | S3 + grant `pix` vigente | S2 | ✔ (era o órfão da rodada 1) |
| 20 | varredura | S3 + grant `pix` vigente | S2 | ✔ |
| 21 | qualquer | S3 sem grant `pix` | inalterado | ✔ — o webhook do cartão continua dono do status |

---

## E6 — `set_account_plan` (admin)

| # | plano destino | status antes | COMPORTAMENTO | DEVERIA |
|---|---|---|---|---|
| 22 | pago | `canceled` / `incomplete_expired` / `unpaid` | ⇒ `inactive` + relógio zerado no mesmo UPDATE | ✔ |
| 23 | pago | `past_due` ou `incomplete` | status e relógio **intactos** | ✔ — o ajuste grava um grant `source='admin'`, e `_pago_por_outro_caminho` pula o lembrete dessa conta |
| 24 | `free` | qualquer | não mexe no par | ✔ |

A célula 23 parece um buraco e não é: a proteção não está no SQL, está no
filtro fino do lembrete. Se algum dia o grant do admin deixar de ser gravado
(há alerta `admin_grant_nao_gravado` para isso), esta célula passa a mandar
lembrete de cartão para quem o admin acabou de liberar.

---

## E7 — o tick do lembrete de pagamento

`list_payment_reminder_candidates` devolve **só S3**, dentro da janela
`[GRACE-1, GRACE-1+WINDOW)`, com e-mail e sem opt-out.

| # | estado | COMPORTAMENTO | DEVERIA |
|---|---|---|---|
| 25 | S1 / S2 | não entra (relógio NULL) | ✔ |
| 26 | S4 | não entra (predicado de status no SQL) | ✔ |
| 27 | S3, na janela, sem grant `pix`/`admin`, sem dedupe | envia | ✔ |
| 28 | S3 no snapshot, **pagou durante o lote** (virou S1/S2) | **FECHADA (rodada 3) — era: envia, e grava a dedupe** | **não enviar** — e é o que roda hoje: `lembrete_ainda_vale` devolve `None` e o laço pula |
| 31 | S3 na revalidação, **pagou durante o `send_payment_reminder_email`** (o HTTP entre a revalidação e o dispatch do WhatsApp) | **FECHADA (rodada 13) — era: o e-mail sai (correto) e o WhatsApp sai depois dizendo que a cobrança está pendente, com `whatsapp: true` no registro** | e-mail sai, WhatsApp não, `whatsapp: false`, dedupe gravada — e é o que roda hoje, com a sobra da linha abaixo |

**A célula 28 foi o apontamento do Codex 5** (`core/services/payment_reminder.py:146`),
e está FECHADA. O defeito era: o funil é um snapshot único, o lote não tem
`LIMIT`, e entre a query e o envio a única checagem era a dedupe — quem pagava
no meio recebia "a cobrança continua pendente", e-mail errado para cliente
pagante, que é a categoria que este PR existe para consertar. **Hoje**
`lembrete_ainda_vale` faz leitura fresca imediatamente antes do envio e o laço
pula quando ela devolve `None`.

**A célula 31 é a 28 no canal IRMÃO** — a instância que a rodada anterior não
fechou (§2: "achei um caso" ≠ "resolvi a categoria"). A revalidação do lote roda
antes do e-mail, mas o e-mail é uma requisição HTTP a serviço externo e o
WhatsApp sai DEPOIS dela
com o veredito daquela leitura; quem paga nesse intervalo recebia um e-mail
correto e, em seguida, um template dizendo que a cobrança está pendente. O
conserto é `db.dunning.ciclo_de_atraso_aberto`, lido DENTRO de
`core.services.payment_reminder_wa._wa_lembrete` — mesmo lugar onde o
consentimento do canal já é lido fresco, e o call site não muda uma linha. Ele
é o PRIMEIRO TERMO de `lembrete_ainda_vale` e não a função inteira: o segundo
termo dela é o `engagement_opt_out`, o consentimento do canal de E-MAIL, e um
canal não decide pela preferência do outro.

**A dedupe continua gravada, de propósito.** O lembrete FOI entregue, por
e-mail — não gravar reenviaria o e-mail no tick seguinte, regressão pior que o
WhatsApp errado. É a metade do apontamento que NÃO foi implementada: ele tratava
"registrar o lembrete como entregue" como parte do defeito, e não é.

> **Sobra de janela dentro do próprio `_wa_lembrete`**: a leitura do gate é
> UMA, antes do laço de destinos, e cada `send_template` é uma requisição HTTP.
> Numa conta com vários números, o ÚLTIMO destino sai mais de uma volta de rede
> depois da leitura. Ler por destino fecharia a sobra e não vale: o dano dela é
> uma mensagem a mais no MESMO lembrete, não um lembrete a mais. O código diz
> isso no comentário do gate; o registro passou a dizer também.
>
> **Ressalva da 31: o gate fecha "pagou PELO CARTÃO", e a célula diz "pagou".**
> O predicado é (relógio, `last_payment_status`), e **um grant `pix`/`admin` que
> passa a vigorar durante o envio não toca nenhuma das duas colunas** — ele
> chega por `plan_grants`, e quem reprojeta o status a partir dele é o
> `recompute_entitlement` na passada de 60 s, tarde demais para este tick.
>
> **Das duas metades, só a do `admin` é alcançável nesta árvore, e ela roda
> hoje.** A do Pix é mecanismo, não caminho: **não existe escritor de grant
> `source='pix'` fora dos testes** (`db/plan_grants.py` diz que "'pix' e as
> tabelas do Asaas chegam no **PR 1b**"), e a medição que a demonstrou criou o
> grant à mão. A do `admin` usa ferramenta de PRODUÇÃO — o painel e o
> `/admin/grant-pro` passam por `core.admin_dashboard.set_account_plan`, que
> grava `source='admin'` em `_gravar_grant_do_admin`. Medido (2026-09-09,
> `set_account_plan("pro", 12, user_id=uid)` chamado durante o
> `send_payment_reminder_email`; remedir antes de reusar):
>
>     par no momento do gate  : past_due_since=<T>, last_payment_status='past_due'
>     _pago_por_outro_caminho : True
>     destinos tentados       : 1
>     details                 : {'email': True, 'whatsapp': True}
>
> **É a célula 23 sendo furada por POSIÇÃO.** Aquela célula declara que o
> lembrete de quem o admin acabou de liberar é pulado pelo filtro fino — e é,
> quando o grant já existe no início da iteração. Se ele nasce durante o envio
> do e-mail, ninguém mais olha.
>
> **Por que não foi consertado aqui, e não é preguiça:** o canal de E-MAIL tem a
> MESMA lacuna — `db.dunning.lembrete_ainda_vale` também não olha grant —, então
> gatear só o WhatsApp seria de novo a instância em vez da categoria (§2), o
> erro que este arquivo inteiro existe para não repetir. É categoria de DOIS
> canais e pede PR próprio, com a pergunta de copy junto (quem pagou por Pix não
> tem "cartão para atualizar").
>
> **O que já existe e NÃO fecha isto:** `core.services.payment_reminder._pago_por_outro_caminho`
> pula quem tem grant `pix`/`admin` vigente, e ele **não** lê do snapshot do
> funil — é leitura viva do banco (medido: devolve `False` antes do grant e
> `True` logo depois). O que o deixa cego é a POSIÇÃO, não a frescura: ele roda
> no início da iteração, antes da revalidação e antes do e-mail, então um grant
> que passa a vigorar durante o envio não é visto por ninguém. É a mesma classe
> da 28 e da 31 num terceiro valor, e some quando a categoria for fechada.

**O conserto**: revalidar o MESMO predicado do funil (relógio + status), por
conta, imediatamente antes do envio, com leitura direta ao banco. Não é
`get_auth_user` porque aquele tem cache de 10 s (`db_support._auth_user_cache`),
e não é um claim que grave a chave de dedupe antes do envio — foi exatamente o
bug que a rodada 1 consertou (`_fire_email` grava DEPOIS do sucesso de
propósito).

**O ENDEREÇO também é lido no ponto do envio** (rodada 10). Ele vinha decifrado
do lote do funil, e a rodada 8 registrou isso como decisão de NÃO consertar, com
a razão "não é consentimento, o endereço era da mesma pessoa". **Essa razão
estava errada**: a remoção do e-mail é exatamente o que desfaz a premissa — um
endereço que a pessoa tirou da conta pode não ser mais dela (e-mail de trabalho
de um emprego que ela deixou), e aí mandar "sua cobrança está pendente" é
divulgar situação de pagamento a TERCEIRO. E a segunda razão de então ("consertar
poluiria a trilha de auditoria") estava INVERTIDA: `lembrete_ainda_vale` já lia a
linha, então as colunas de e-mail não custaram query nova e a decriptação
passou de uma por CANDIDATO para uma por ENVIADO. **É uma TROCA, e o registro
tem de mostrar os dois lados** (medido em 2026-09-09; remedir antes de reusar):

| desenho | linhas de auditoria | tempo |
|---|---|---|
| lote, N=200 candidatos | 200 | 14,1 ms |
| ponto de envio, M=10 | 10 | 13,1 ms |
| ponto de envio, M=20 | 20 | 20,9 ms |
| ponto de envio, M=60 | 60 | 56,9 ms |

Ganha minimização de PII e veracidade da trilha; **paga tempo acima do ponto de
equilíbrio, que é M ≈ 6,5 % de N**. Vale porque são dezenas de ms dentro do
executor, uma vez por tick de 24 h — e porque o lote registrava acesso ao
e-mail de gente que nunca recebeu nada.

**O `LIMIT` fica de fora**, e não por esquecimento: o dano do lote grande era a
janela de staleness, e ela passa a ser fechada no ponto de uso. Truncar o lote
só reordena quem é servido em qual tick, e a invariante
`PAYMENT_REMINDER_WINDOW_DAYS < PAYMENT_REMINDER_DEDUPE_DAYS` já garante que
quem sobrar volta no tick seguinte ainda dentro da janela.
`ponytail: sem LIMIT; medir o tempo do lote contra a janela antes de acrescentar um.`

---

## O que a enumeração fechou

| célula | quem achou | estado |
|---|---|---|
| 5 (`invoice.paid`) | Codex, rodada 3 | fechada — predicado dentro da escrita |
| 5 (`checkout.session.completed`) | esta enumeração | fechada — mesmo predicado, o irmão que o apontamento não citava |
| 28 (lembrete) | Codex, rodada 3 | fechada — revalidação antes do envio |
| 6 (reentrega dentro do ciclo) | esta enumeração | passou a ser **coberta por teste**; era o caso que um gate por "o upsert aplicou?" teria quebrado |
| 14 (2º e-mail de falha) | esta enumeração | **aberta de propósito**, ressalva acima |
| 18 (`deleted` fora de ordem) | rodada 2 | **aberta**, anterior a este PR |
| 23 (admin sobre `past_due`) | esta enumeração | sem defeito — a proteção é o grant `admin` |
| 28-b (endereço do snapshot) | Codex, rodada 10 | fechada — o endereço sai da revalidação fresca; a recusa da rodada 8 usava duas razões, uma errada e uma invertida |
| 31 (WhatsApp depois do e-mail) | Codex, rodada 13 | fechada **para pagamento por cartão** — `ciclo_de_atraso_aberto` dentro do `_wa_lembrete`; a dedupe continua gravada de propósito, e essa metade do apontamento foi recusada. **Resíduo declarado**: grant `pix`/`admin` que passa a vigorar DURANTE o envio não move o par (relógio, status) e continua passando pelo gate — a metade `admin` roda hoje (fura a célula 23 por posição), a metade `pix` depende do PR 1b. É lacuna dos DOIS canais e fica para PR próprio — ver a ressalva na seção do E7 |
| **30 (`paid` de OUTRA assinatura)** | **Codex, rodada 13** | **ABERTA, com recusa fundamentada** — quem apaga o relógio ali é o `CASE` de `set_payment_status_impl` (ele roda ANTES do clear, que fica no-op), e gateá-lo cria o órfão S4: não produz lembrete (célula 26), envenena o ciclo seguinte (célula 12) e quebra as células 19/20, onde o `CASE` é o único que apaga. É essa consequência que sustenta a recusa, e ela é a ÚNICA — a razão "gatear ali é no-op" foi publicada nesta mesma rodada e a medição a derrubou. Custo do defeito: um lembrete perdido, sem perda de acesso. Leia a seção da célula antes de tentar de novo. |
| **29 (corrida check/write da guarda)** | **Codex, rodada 9** | **ABERTA, com recusa fundamentada** — janela medida em 0,174 ms (mediana), sem perda de acesso, e todo predicado sobre dado existente ou deixa irmã aberta ou recusa falha legítima. Fechar exige marca d'água por usuário. Leia a seção da célula antes de tentar de novo. |

**Se você veio aqui para "finalmente consertar a 29"**, leia a seção dela
primeiro e responda a três coisas por escrito: (1) qual marca d'água por
**usuário** você vai usar; (2) como o seu predicado aceita a falha legítima do
usuário com duas assinaturas; (3) quantos writers de `last_payment_status`
passam a existir. Sem as três respostas, o conserto é o remendo que a rodada 9
recusou de propósito.

**Se você veio aqui para "finalmente consertar a 30"**, o par do bloco acima, e
são QUATRO perguntas — porque ali o relógio muda de dono:

1. **Onde o relógio passa a morar** para ser por ASSINATURA: coluna nova em
   `plan_grants`? Tabela nova? E **quem passa a ser o dono da invariante** que
   hoje `db_support.set_payment_status_impl` mantém no MESMO UPDATE do status
   ("relógio não nulo só existe com status na lista")? Ela deixa de ser uma
   linha de SQL no mesmo `set` e passa a ser coisa entre duas tabelas.
2. **Qual vira o predicado do funil.** `last_payment_status` é por USUÁRIO e não
   distingue A de B, então como se pergunta "esta conta tem ALGUMA assinatura em
   atraso há 6 dias" sem o par (relógio, status) que o funil e
   `db.dunning.lembrete_ainda_vale` usam hoje?
3. **O que a copy passa a dizer** para quem tem B ativa e A em atraso. A de hoje
   afirma que a cobrança do "seu plano" não passou, e nesse estado o plano está
   pago — sem copy nova, o conserto entrega lembrete mentiroso.
4. **Quantos writers de `last_payment_status` passam a existir** (hoje dois **em
   Python**: `db_support.set_payment_status_impl` e o SQL cru de
   `core/admin_dashboard.set_account_plan`; fora deles sobram `scripts/*.sql` de
   reparo manual, aceitos — o eixo de estados traz a varredura e o qualificador
   completo, que esta pergunta já publicou sem).

Sem as quatro, o conserto é a regressão que a rodada 13 recusou: o órfão S4, que
não produz lembrete nenhum e estraga o ciclo seguinte.
