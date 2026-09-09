# Inadimplência de cartão — a máquina de estados, enumerada

Esta é a tabela de **estados × eventos** do relógio de inadimplência
(`auth_accounts.past_due_since`). Ela existe porque o subsistema levou **três
rodadas seguidas** de apontamentos e cada conserto foi feito como transição
isolada — o mecanismo 2 do registro do PR #60 no `CLAUDE.md` §4 ("remendar
transição em vez de enumerar a máquina"). A regra de lá é a razão deste arquivo:
*"se duas rodadas seguidas batem no mesmo subsistema, pare de remendar: enumere
estados × eventos por escrito e feche tudo num commit."*

Vocabulário e constantes: `core/services/billing_dunning.py`.
Escrita: `db/dunning.py`. Leitura: `core/services/payment_reminder.py`.
**Nada aqui tira acesso de ninguém** — o relógio alimenta o lembrete do 6º dia e
a janela de dedupe do e-mail de falha, e só.

---

## Os eixos

**Estado** = o par (`past_due_since`, `last_payment_status`). `IN` = status em
`PAST_DUE_PAYMENT_STATUSES` (`past_due`, `unpaid`, `incomplete`); `OUT` = fora.

| | relógio | status | nome | significado |
|---|---|---|---|---|
| **S1** | NULL | IN | *atraso sem ciclo* | o Stripe ainda diz atraso, mas nenhum ciclo nosso está aberto (pagou e o status não alcançou) |
| **S2** | NULL | OUT | *normal* | `active`, `trialing`, `canceled` |
| **S3** | T | IN | *ciclo aberto* | o estado da inadimplência; é o único que o lembrete lê |
| **S4** | T | OUT | **órfão** | a INVARIANTE declara impossível. Mantida na escrita por `db_support.set_payment_status_impl` e pelo SQL cru de `core/admin_dashboard.set_account_plan` — os **dois únicos** writers de `last_payment_status` em Python (varredura: `grep -rn "last_payment_status" --include="*.py" --include="*.sql" --exclude-dir=.venv .`; sobram dois `scripts/*.sql` de reparo manual, aceitos) |

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

| # | validade | estado antes | status LIVE | estado no clear | HOJE | DEVERIA |
|---|---|---|---|---|---|---|
| 1 | NOVO | S2 | qualquer | NULL | clear (no-op) | no-op ✔ |
| 2 | NOVO | S3 | OUT (`active`) | NULL — `set_payment_status` já zerou | clear (no-op) | no-op ✔ |
| 3 | NOVO | S3 | IN (consistência eventual, ou outra fatura aberta) | S3 | **clear** | clear ✔ — é a única razão de esta linha existir |
| 4 | NOVO | S4 | OUT | NULL | no-op | no-op ✔ |
| 5 | REENTREGA | S3, relógio carimbado **depois** deste evento | IN | S3 | **clear** ✗ | **NÃO limpar** |
| 6 | REENTREGA | S3, relógio carimbado **antes** deste evento (5xx entre o grant e o clear) | IN | S3 | clear | clear ✔ |
| 7 | VELHO | qualquer | — (nem consulta) | inalterado | no-op (gate `_decidiu_acesso`) | no-op ✔ |

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

---

## E3 — `invoice.payment_failed`

Duas guardas, as duas da rodada 2: (a) `Subscription.retrieve` — status LIVE
fora da lista ⇒ o ramo inteiro é ignorado e logado; (b) `_sub_id` ausente
(fatura avulsa) ⇒ não carimba. O `claim` é `past_due_since is null AND status
IN`.

| # | validade | estado antes | status LIVE | HOJE | DEVERIA |
|---|---|---|---|---|---|
| 8 | qualquer | qualquer | OUT | ignorado + `billing_payment_failed_obsoleto` | ✔ |
| 9 | qualquer | qualquer, sem `_sub_id` | IN | grava `past_due`, **não** carimba | ✔ (fatura avulsa não tem ciclo de assinatura) |
| 10 | NOVO | S2 | IN | carimba; `_abriu_ciclo=True` ⇒ e-mail com `dedup_days=0` | ✔ |
| 11 | NOVO | S3 | IN | `rowcount 0`; e-mail dedupado por `DUNNING_GRACE_DAYS` | ✔ (smart retry do mesmo ciclo) |
| 12 | NOVO | S4 | IN | preserva o relógio VELHO ⇒ ciclo novo nasce fora da janela | seria bug — **S4 é inalcançável**, ver o eixo de estados |
| 13 | REENTREGA | S3 | IN | `rowcount 0`, não reinicia | ✔ |
| 14 | REENTREGA | S1 | IN | carimba `now()`, `_abriu_ciclo=True` ⇒ **2º e-mail de falha** | ver a ressalva abaixo |
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

| # | validade | intercalamento | HOJE | DEVERIA |
|---|---|---|---|---|
| 29 | qualquer | o `invoice.paid` de OUTRA requisição escreve `active` e zera o relógio **depois** de o `retrieve` deste ramo responder `past_due` e **antes** de a corrotina retomar e escrever | grava `past_due` por cima do `active`, o `claim` acha status na lista + relógio nulo e **carimba ciclo novo**, e sai e-mail de "sua cobrança falhou" com `dedup_days=0` | não escrever nada: o evento pago é mais novo |

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

| # | validade | estado antes | HOJE | DEVERIA |
|---|---|---|---|---|
| 16 | NOVO | S3 | `canceled` ⇒ o próprio `set_payment_status` zera o relógio; o clear é no-op | ✔ |
| 17 | NOVO | S4 | idem | ✔ |
| 18 | REENTREGA/VELHO | S3 de OUTRA assinatura viva | `plan=free` + `canceled` + relógio zerado; `revoke_grant` é recusado por versão e `recompute_entitlement` devolve o plano, mas o status fica `canceled` | não deveria — **anterior a este PR** |

**O `subscription.deleted` sobrevive ao predicado**: nas três células o
`set_payment_status('canceled')` roda ANTES e já zerou o relógio no mesmo
UPDATE, então o clear daquele ramo é no-op em todo estado alcançável. Passar o
`created` do evento ali não muda comportamento nenhum — e é o que se faz, para
que a regra seja UMA só e nenhum call site futuro herde a versão incondicional.
A célula 18 continua aberta: quem a destrói é o `set_payment_status`, não o
clear, e gatear só o clear não fecharia nada (§0.3).

---

## E5 — `recompute_entitlement` (webhooks de cobrança e loop de 60 s)

Nunca escreve o relógio. Escreve `last_payment_status` num caso só: grant `pix`
vigente ⇒ `active` ⇒ status fora da lista ⇒ `set_payment_status_impl` zera o
relógio no mesmo UPDATE.

| # | origem | estado antes | HOJE | DEVERIA |
|---|---|---|---|---|
| 19 | evento | S3 + grant `pix` vigente | S2 | ✔ (era o órfão da rodada 1) |
| 20 | varredura | S3 + grant `pix` vigente | S2 | ✔ |
| 21 | qualquer | S3 sem grant `pix` | inalterado | ✔ — o webhook do cartão continua dono do status |

---

## E6 — `set_account_plan` (admin)

| # | plano destino | status antes | HOJE | DEVERIA |
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

| # | estado | HOJE | DEVERIA |
|---|---|---|---|
| 25 | S1 / S2 | não entra (relógio NULL) | ✔ |
| 26 | S4 | não entra (predicado de status no SQL) | ✔ |
| 27 | S3, na janela, sem grant `pix`/`admin`, sem dedupe | envia | ✔ |
| 28 | S3 no snapshot, **pagou durante o lote** (virou S1/S2) | **envia**, e grava a dedupe | **não enviar** |

**A célula 28 é o apontamento do Codex 5** (`core/services/payment_reminder.py:146`).
O funil é um snapshot único, o lote não tem `LIMIT`, e entre a query e o envio a
única checagem é a dedupe. Quem pagou no meio recebe "a cobrança continua
pendente" — e-mail errado para cliente pagante, que é a categoria que este PR
existe para consertar.

**O conserto**: revalidar o MESMO predicado do funil (relógio + status), por
conta, imediatamente antes do envio, com leitura direta ao banco. Não é
`get_auth_user` porque aquele tem cache de 10 s (`db_support._auth_user_cache`),
e não é um claim que grave a chave de dedupe antes do envio — foi exatamente o
bug que a rodada 1 consertou (`_fire_email` grava DEPOIS do sucesso de
propósito).

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
| **29 (corrida check/write da guarda)** | **Codex, rodada 9** | **ABERTA, com recusa fundamentada** — janela medida em 0,174 ms (mediana), sem perda de acesso, e todo predicado sobre dado existente ou deixa irmã aberta ou recusa falha legítima. Fechar exige marca d'água por usuário. Leia a seção da célula antes de tentar de novo. |

**Se você veio aqui para "finalmente consertar a 29"**, leia a seção dela
primeiro e responda a três coisas por escrito: (1) qual marca d'água por
**usuário** você vai usar; (2) como o seu predicado aceita a falha legítima do
usuário com duas assinaturas; (3) quantos writers de `last_payment_status`
passam a existir. Sem as três respostas, o conserto é o remendo que a rodada 9
recusou de propósito.
