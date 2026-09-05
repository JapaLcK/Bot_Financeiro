# PLANO v5 — Pix anual (Asaas) coexistindo com Stripe

> v4 auditada pelo Manager: 10 contradições entre seções. Todas procedem. Duas delas
> (5 e 8) tiveram o **gatilho corrigido** pela retificação de fato do coordenador,
> confirmada na doc oficial do Asaas — a falha continua, o caminho muda.

## 0. Fato do Asaas que a v5 usa (confirmado pelo coordenador na doc oficial)

- Cada notificação de webhook tem **`id` próprio (`evt_…`)**; o **`payment.id` é estável** entre eventos do mesmo pagamento. É o que sustenta chavear efeito por pagamento.
- **No Pix o fluxo é `PAYMENT_CREATED → PAYMENT_RECEIVED`. O Pix PULA o `CONFIRMED`.** `PAYMENT_CONFIRMED` é de cartão (processado, valor ainda não disponível); `PAYMENT_RECEIVED` é valor creditado, e no Pix a liquidação é instantânea.

Consequência editorial que vale para o plano inteiro: **o par CONFIRMED+RECEIVED não ocorre no nosso fluxo** e não é usado como justificativa de nada. Onde o `CONFIRMED` aparece, ele está por **robustez**, e o texto diz isso — para ninguém escrever teste de um caminho que a plataforma não produz.

## 1. As 10 contradições e o que mudou

| # | contradição da v4 | correção na v5 | seções tocadas |
|---|---|---|---|
| 1 | `user_id is null` era linha da tabela de estados (casava com duas linhas), o dreno não testava isso, e `plan_grants.user_id not null` fazia o efeito `grant` estourar → `paid_orphan` **inalcançável** | linha **removida** da tabela; vira **guarda no dreno, antes do laço de efeitos**, que muda o destino da transição para `paid_orphan`, registra o efeito dispensado e sai | §8.2, §11, §13.4, testes 47–48 |
| 2 | grant `legacy` "cede por ser mais novo" — falso: a guarda do §6 é por linha, e `legacy` × `stripe` nunca colidem | **todo upsert aplicado de grant `stripe` revoga o `legacy` do mesmo usuário na mesma transação** (`superseded_by_stripe`) | §5.1, §6, testes 5–7, 14 |
| 3 | `stripe_cancel` depois do `grant`: falhando, o Stripe renova e cobra, contra a promessa do §9 | ordem passa a **`[stripe_cancel, grant, ga4, capi, email]`**; `stripe_period_end_at` gravado no efeito 1 e reusado pelo 2; promessa do §9 vira **condicional**, com fallback documentado | §8.2, §9, §3.2, testes 30–31 |
| 4 | reversão do 1a não era segura: `do nothing` congelava o `legacy` na re-aplicação, e a projeção escrevia `free` destrutivamente | backfill vira **resync idempotente** (`do update`, só estende); e **a projeção nunca escreve `free` para quem não tem grant nenhum** | §4.1, §5.1, §17, testes 4, 8–9 |
| 5 | antecipação não carimbava `event_version` → evento posterior repunha `starts_at` no futuro | **a antecipação passa a carimbar `event_version`** (o §6 continua sendo regra única: **toda** escrita em grant carrega versão) **e** grant `pix` vira **criação única** (`do nothing`) | §4.4, §6, teste 20 |
| 6 | "empilha pela regra de renovação" com planos diferentes; duas cobranças precificadas contra o mesmo crédito | substituição passa a **cancelar no Asaas ANTES de criar a nova** (precedente `:4028-4043`), 503 se não conseguir | §10, testes 37–39 |
| 7 | purga de 90 dias apagava cobrança que o §11 declara pagável; faltava célula para "cobrança inexistente" | **`PAGAMENTO_TARDIO_MAX_DIAS = 60`** (a varredura cancela no Asaas) < `RETENCAO_TENTATIVA_DIAS = 90`; nova célula **`orphan_unknown`** | §10, §11, §13.1, testes 41–42 |
| 8 | dedup por `event_id` e **laço de efeitos sem ramificar por tipo de evento** | **(a)** o dreno **seleciona os efeitos pelo tipo do evento e pelo resultado da transição**; **(b)** chave dos efeitos vira **`(asaas_payment_id, effect)`** | §3.4, §8.2, testes 28–29 |
| 9 | `set_account_plan` (reparo manual do admin) era zerado pela projeção | passa a escrever um **grant `source='admin'`**; plano `free` revoga todos os grants ativos | §14 (PR 1a), §15, testes 49–50 |
| 10 | `payload_enc` só era purgado após `processed_at`; evento travado guardava PII para sempre | purga conta de **`received_at`** e **anula o payload preservando a forense** | §13.1, §13.3, teste 46 |

**Sobre o nº 8, o caminho que importa:** o par CONFIRMED+RECEIVED não existe no Pix, então ele **não** é a justificativa. O caminho alcançável é outro e é pior: `PAYMENT_REFUNDED` ou `PAYMENT_OVERDUE` chega com `event_id` novo, não encontra par registrado e — no laço da v4, que não ramificava — **rodaria os cinco efeitos**, mandando `send_purchase` ao GA4 e Purchase à CAPI **num estorno**. As duas correções (a) e (b) são necessárias e independentes.

**Sobre o nº 5:** a cadeia do Manager passava pelo CONFIRMED→RECEIVED e não se aplica. A causa raiz é intacta e independente do gatilho — reentrega do próprio `RECEIVED`, um `PAYMENT_REFUNDED` ou uma reconciliação que reescreva o grant passariam na guarda do §6 e reporiam `starts_at` no futuro.

## 1.1 v5.1 — as seis correções da leitura do dono

| # | o que estava errado | correção | onde |
|---|---|---|---|
| 1 | `event["created"]` tem precisão de **segundos** e o `>` descartava o empate **em silêncio** — `invoice.paid` × `subscription.deleted` no mesmo segundo faziam o acesso depender da ordem de chegada | **no empate, a revogação ganha**; `last_event_id` torna o desempate observável; determinismo provado nas duas ordens | **§6.1**, §3.1, teste 12b |
| 2 | a antecipação alterava **todos** os grants Pix futuros | **só o primeiro elegível**, e **vinculado** à assinatura do evento (`pix_charges.stripe_subscription_id`) | **§4.4**, teste 20b |
| 3 | varredura apagava `draft` **por idade** | **expurgo só por confirmação**: cancelamento confirmado ou lista vazia no Asaas; `creating` nunca expurga; falha de consulta não decide nada | **§10.1**, §11, teste 36b |
| 4 | `qr_payload` em texto puro e `asaas_payment_id` em URL — e o `sid` **vai para o pixel da Meta** (`home.html:2151`) | QR **cifrado e apagado** no fim; **`public_token` opaco** no poll, no `sid`, no `transaction_id` do GA4 e no `event_id` da CAPI | **§13.6**, §3.2, §14, teste 42b |
| 5 | qualquer pagamento sem cobrança nossa virava `orphan_unknown` — **a conta Asaas é a do negócio** e recebe outros Pix | `orphan_unknown` **só** com `externalReference` casando `^pix:[0-9]+$`; fora do formato, **ignora em silêncio** com log de contagem | **§8.2 A**, §11, teste 42 |
| 6 | "PR 2 sem teste automatizado" | arquivo novo **`tests/frontend/precos_pix_anual.test.mjs`** com 6 casos; só a leitura física do QR continua manual | **§16.1**, §14 |

**Aprovado e inalterado por decisão do dono:** `RETENCAO_PAGAMENTO_DIAS` **sem valor** até validação jurídica/contábil — a LGPD não fixa prazo universal, e o prazo é função da finalidade (§13.1).

Fechado e não reaberto: afiliado fora · GA4 e Meta CAPI dentro · sem trial · flag só na venda · crédito monetário **só Pix→Pix** · `ASAAS_MIN_CHARGE_CENTS` sem default · cobertura contígua com tolerância de 120 s · `RETENCAO_PAGAMENTO_DIAS` **sem número** até aval jurídico · Asaas na lista nominal de subprocessadores · tarifa de R$ 0,00 como **nota operacional, nunca premissa** · `cpfCnpj` obrigatório e **não persistido**.

---

## 1.2 v6 — o bloqueio do PR 1a e a decisão do dono

O Tester provou (D4) que o resync ressuscitava grant `legacy` revogado. O conserto do Coder (`where status='active'`) fechou isso e **abriu dinheiro**: medido pelo Manager, `pro/2027-07-01 → free/None`, e o caminho provável nem é o revert — é a renovação cujo `upsert_grant` estourou dentro de um `except/print`.

**Decisão do dono, e ela supera as duas leituras anteriores:** o defeito não é a projeção nem o resync — é a **gravação do grant ser opcional**.

| # | o que muda | onde |
|---|---|---|
| 1 | **materializar o grant é obrigatório**: primeiro efeito do ramo, sem `except`, falha vira **5xx retryable**, reentrega idempotente e reparadora | **§4.1.1 A**, §14 item 4 |
| 2 | **a projeção só reduz cobertura quando a redução é confirmada** — por evento, ou consultando o **Stripe** na varredura; sem confirmação, não escreve e alerta. **Uma exceção, decidida pelo dono: `past_due` além de 15 dias reduz com a assinatura viva** (§4.1.1 C), senão a cauda é infinita | **§4.1.1 B/C/D**, §4.1 |
| 3 | o 5xx reentrega o evento **inteiro**: ordem + dedup por efeito, com **unique no funil** e **dedup de e-mail no `_fire_email`** | **§4.1.2** |
| 4 | o resync do boot vira **backfill inicial único** (`not exists` + `do nothing`): não repara, não copia projeção para grant, **sem rede no boot** | **§5.1** |
| 5 | reparo de grant só a partir de **estado autoritativo do Stripe**, fora do boot | §4.1.1 B |
| 6 | `checkout.session.completed` e `invoice.paid` usam a **mesma normalização** da referência de assinatura | §14 item 4, teste 9g |
| 7 | quatro blocos que o código já tinha e o plano não: `distinct on`/guarda do resync, `user_id` no upsert e a cláusula morta do desempate, varredura sem janela, revogação do admin em **toda** troca de plano | §5.1, §6, §4.3, §14 item 5 |
| 8 | a propriedade (b) da reversibilidade **mudou de mecanismo** — não é mais o boot | §17 |

**Descartada:** estender a guarda da projeção para "nenhum grant cobre agora, mas `auth_accounts` diz pago e vigente". Ela tratava o sintoma e mantinha a gravação do grant opcional.

---

## 2. Objetivo

Vender o plano anual por **uma cobrança Pix**, com acesso e dinheiro corretos em toda combinação com o Stripe — inclusive quando o processo morre no meio, quando os eventos chegam fora de ordem, quando a conta foi excluída e **no dia do deploy, com a base pagante já existente**.

---

## 3. Modelo de dados

Quatro tabelas. `auth_accounts.plan`/`plan_expires_at` continuam sendo o **modelo de leitura** de todo o app (nenhum dos ~30 leitores muda): viram **projeção**, escrita por uma função só.

### 3.1 `plan_grants` — o direito, como registro

```
id bigserial pk
user_id bigint not null references users(id) on delete cascade
source text not null                    -- 'stripe' | 'pix' | 'legacy' | 'admin'
external_ref text not null              -- sub id | pix_charges.id | 'legacy:<uid>' | 'admin:<uid>'
plan_stored text not null               -- valor legado JÁ resolvido
starts_at, ends_at timestamptz not null
status text not null default 'active'   -- 'active' | 'revoked'
event_version bigint not null default 0 -- §6
last_event_id text                      -- NOVO (§6.1): torna o desempate observável
revoked_reason text, revoked_at timestamptz
created_at, updated_at timestamptz not null default now()
unique (source, external_ref)
index (user_id, status, starts_at)
```

`user_id` continua **`not null`**: grant é direito de alguém. O caso órfão nunca chega aqui (§8.2).

### 3.2 `pix_charges` — snapshot financeiro (centavos inteiros, sem float)

```
id bigserial pk
user_id bigint references users(id) on delete set null      -- §13
external_reference text not null unique                     -- "pix:<id>"
asaas_payment_id text unique, asaas_customer_id text
plan text not null, plan_stored text not null               -- legado resolvido na CRIAÇÃO
price_cents, credit_cents, amount_cents bigint not null
currency text not null default 'BRL', duration_days int not null default 365
stripe_subscription_id text
stripe_cancel_scheduled_at timestamptz
stripe_period_end_at timestamptz            -- NOVO (nº 3): estimativa na CRIAÇÃO, reconfirmada no efeito stripe_cancel
public_token text not null unique           -- NOVO (correção 4): id OPACO, o único que sai do servidor
status text not null default 'draft'
qr_payload_enc text, due_date date, qr_expires_at timestamptz  -- QR CIFRADO, apagado no fim (§13.6)
access_starts_at, access_expires_at timestamptz              -- decididos NO PAGAMENTO
ga_client_id, fbp, fbc text                                  -- zerados na exclusão (§13)
created_at, paid_at, canceled_at, refunded_at, purged_at timestamptz

unique index uniq_pix_charge_ativa on pix_charges (user_id)
  where status in ('draft','creating','pending','canceling')
```

O índice volta a cobrir `canceling` porque a substituição **deixou de criar as duas linhas juntas** (§10). Sem `credit_forfeited_cents`. `plan_stored` gravado na criação mata o fallback silencioso de `_stored_plan_for_price` (`:290`).

### 3.3 `pix_webhook_events` — outbox (payload minimizado e cifrado)

```
event_id text primary key              -- o evt_... do Asaas
event_type text not null               -- NOVO: roteia os efeitos (§8.2)
payload_enc text                       -- NULL depois da purga (§13.3)
event_version bigint not null          -- §6
received_at timestamptz not null default now()
processed_at timestamptz, purged_at timestamptz
attempts int not null default 0, last_error text
index (processed_at) where processed_at is null
index (received_at)
```

### 3.4 `pix_payment_effects` — um registro por efeito, **chaveado pelo pagamento**

```
asaas_payment_id text not null, effect text not null,
   -- 'stripe_cancel'|'grant'|'ga4'|'capi'|'email'|'revoke'|'orphan_notified'
event_id text not null,                -- qual evento executou (forense)
done_at timestamptz not null default now()
primary key (asaas_payment_id, effect)
```

Chave pelo **pagamento**, não pelo evento: reentrega e eventos irmãos do mesmo `payment.id` (`RECEIVED`, depois `REFUNDED`, depois uma reconciliação) não reexecutam nada. Junto com o roteamento por tipo (§8.2), são as duas metades da correção nº 8.

**A outbox e a tabela de efeitos são padrão novo neste repo** (`grep -rniE "outbox|dead_letter|retry_queue|pending_events|processed_at|delivery_attempts"` → vazio). São **duas tabelas e uma função de drenagem**, não mensageria: sem broker, sem DLQ, sem backoff configurável. `ponytail:` teto = varredura por loop; generalizar só com um segundo produtor de eventos.

---

## 4. Projeção: cobertura contígua a partir de agora

### 4.1 A regra (função pura, testável sem banco)

```
GAP_TOLERANCIA = 120 s

recompute_entitlement(user_id):
    conta = get_auth_user(user_id)
    se last_payment_status == 'grandfathered': return                 # vitalício
    se plan != 'free' e plan_expires_at is null:  return              # vitalício de fato (§5.3)

    se NÃO existe nenhum grant do usuário e a conta está paga e vigente:
        log_system_event("warning","projecao_sem_grants") + admin_notify (1x/dia)
        return                                                        # NÃO escreve nada (nº 4)

    ativos = grants status='active' e ends_at > now(), ordenados por starts_at
    vigentes = [g em ativos com starts_at <= now()]

    se não há vigentes:
        plan='free'; plan_expires_at=None                             # grant futuro NÃO conta
    senão:
        cobertura = max(ends_at) entre vigentes
        para g em ativos com starts_at > now(), em ordem:
            se g.starts_at <= cobertura + GAP_TOLERANCIA: cobertura = max(cobertura, g.ends_at)
            senão: break                                              # primeiro buraco
        plan_expires_at = cobertura
        plan = maior tier entre os grants que cobrem AGORA

    last_payment_status = 'active' se há grant Pix vigente AGORA; senão preserva o do Stripe
    grava em auth_accounts
```

Depois de projetar, **antes de gravar**, a regra da redução (§4.1.1):

```
    reduz = (plano == 'free') or (expira < plan_expires_at atual)
    se reduz e motivo == 'varredura':
        autoridade = consulta ao Stripe (a assinatura do usuário)
        se autoridade indisponível (erro/timeout):
            log "projecao_reducao_nao_confirmada" + admin_notify (1x/dia); return
        se autoridade mostra assinatura ativa mais longa que o grant:
            repara o grant a partir DELA (upsert_grant) e reprojeta
        senão:
            grava a redução                      # confirmada: não há assinatura
    grava em auth_accounts
```

### 4.1.1 Materialização obrigatória do grant + regra da redução (decisão do dono, v6)

**O que estava errado não era a projeção: era a gravação do grant ser OPCIONAL.** `_registrar_grant_stripe` (`frontend/finance_bot_websocket_custom.py:4751`) engolia a exceção com um `print`, então uma renovação em que o `update_user_plan` entrava e o `upsert_grant` estourava deixava o grant parado no período anterior — e a varredura seguinte rebaixava um pagante. O resync do boot mascarava isso copiando `auth_accounts` de volta para o grant, o que transformava a **projeção** em fonte de verdade e ressuscitava grant revogado (D4).

Duas decisões, nesta ordem:

**(A) Materializar o grant é parte obrigatória do processamento do evento.**

1. **`upsert_grant` roda ANTES** de qualquer escrita de projeção, e é o **primeiro** efeito do ramo;
2. **o `except`/`print` sai**: a exceção **propaga**;
3. a resposta ao Stripe vira **5xx**, que é **retryable** — mesmo tratamento que o `claim_trial_for_user` já tem no mesmo handler ("falha precisa propagar: resposta 5xx faz a Stripe repetir o webhook");
4. `auth_accounts` passa a ser recalculado **a partir dos grants** (`recompute_entitlement`), nunca escrito à mão em paralelo;
5. a reentrega é **idempotente e reparadora**: `upsert_grant` é `on conflict (source, external_ref)` com guarda de `event_version`, então o retry conclui a operação sem duplicar (§4.1.2 detalha efeito por efeito).

Com isso a única defasagem possível é a **oposta** — grant novo, `auth_accounts` velho —, que a projeção seguinte cura sozinha. **O modelo de escrita pode estar à frente do de leitura; nunca atrás.**

**(B) A projeção só REDUZ cobertura quando a redução é confirmada.**

Uma regra no lugar de três guardas. Reduzir (virar `free` ou encurtar `plan_expires_at`) exige uma de duas autoridades:

| origem da redução | o que a confirma |
|---|---|
| **evento** (webhook do Stripe/Asaas) | o próprio evento — ele *é* a autoridade, e chegou agora |
| **varredura** (60 s ou diária), sem evento | **consulta ao Stripe**: sem assinatura ativa → reduz; assinatura ativa mais longa que o grant → **repara o grant a partir dela** e reprojeta; Stripe indisponível → **não escreve**, loga, alerta |

O que cada caso vira, e por que nenhum legítimo é bloqueado:

| situação | resultado |
|---|---|
| grant defasado (materialização falhou) | varredura consulta o Stripe, vê a assinatura viva, **repara o grant** — o pagante nem chega a ser rebaixado |
| assinatura cancelada / lapsada | varredura consulta, não há assinatura ativa, **reduz** ✔ |
| `subscription.deleted` | redução por **evento**, não passa pela consulta ✔ |
| estorno/chargeback (1b) | redução por **evento** ✔ |
| Stripe fora do ar | **não reduz** + alerta — falha na direção reparável |
| downgrade agendado (1b) | não é redução: há grant vigente ✔ |
| **`past_due` (cobrança em atraso)** | **a ÚNICA exceção da regra acima: ver §4.1.1 C** |

### 4.1.1 C — `past_due`: carência de 15 dias, e depois reduz mesmo com assinatura viva

**Decisão de produto do dono (2026-09-04).** Esta seção existe porque a regra estava **só** num comentário de constante (`core/services/billing_access.py:27-42`), e o plano — o artefato que o dono revisa — dizia o **contrário** ("sem confirmação, não escreve"). Duas versões da mesma regra, e a errada era a revisável: §0.7 puro.

**A regra, com os dois lados do teto:**

```
status == 'past_due' na varredura:
    base = ends_at do grant ATIVO alvo, se houver; senão o plan_expires_at da conta
    se agora <= base + PAST_DUE_CARENCIA_DIAS (15):  → 'carencia'  (segura o acesso, NÃO escreve, só LOGA)
    senão:                                           → 'reduz'     (rebaixa, COM a assinatura viva no Stripe)
```

- **Dentro do teto, o acesso é segurado.** Durante o *dunning* a assinatura continua viva e o cliente ainda pode pagar; tirar o produto no primeiro dia de atraso cobra dele um erro que quase sempre é do cartão. O veredito é `carencia` e **não alerta** — atraso em dunning é estado **esperado**, e alerta diário é alerta que ninguém lê no dia em que importa. Loga, para ser contável.
- **Além do teto, reduz — e é a única situação em que a varredura tira acesso de alguém cuja assinatura o gateway diz estar viva.** Sem o teto a cauda é infinita: `_find_active_subscription` considera `past_due` viva, então o veredito nunca sairia de "não reduz" e a conta ficaria pendurada para sempre.
- **`PAST_DUE_CARENCIA_DIAS = 15` fica na borda**, de propósito e com o risco anotado: o dunning padrão do Stripe retenta por volta de **duas semanas**. Se as tentativas configuradas na conta forem além disso, o teto corta alguém que o Stripe ainda recuperaria. O retorno é automático (o `invoice.paid` da recuperação regrava o grant e a projeção devolve o acesso), então o pior caso é **intermitência**, não perda permanente. Aumentar o número é a mudança certa se o dunning da conta for mais longo.
- **A `base` do teto pode vir do `plan_expires_at`** quando não há grant ativo a usar como referência. Isso **não** contradiz o D4: ler a projeção para decidir um **prazo** é diferente de escrever um **grant** a partir dela. O que continua proibido é o segundo.

**Fato do Stripe registrado ao contrário (correção do Manager, conferida na doc oficial):** a Stripe define o período **ao criar a fatura**, não quando o pagamento entra — então assinatura `past_due` tem `current_period_end` **no FUTURO**, do período que ainda não foi pago. O plano e o código diziam "é o período já vencido". Hoje isso não muda comportamento (o ramo do `past_due` sai antes de qualquer leitura do `fim`), mas era a **justificativa escrita de uma regra de dinheiro**, e é assim que se produz o conserto errado.

> **Por que o `fim` NÃO pode ser usado no ramo `past_due`** — esta é a proteção, e ela vale mais que o fato: esticar o grant até o `current_period_end` de uma assinatura `past_due` seria **conceder um período que ninguém pagou**, e ainda por cima renovaria a carência a cada passada, tornando o teto inalcançável. O ramo `past_due` decide **só** entre `carencia` e `reduz`; quem estica grant é o ramo `active`/`trialing`, onde o período **foi faturado e pago**.

### 4.1.1 D — a matriz de vereditos (estados × situação)

Terceira rodada seguida em que o conserto abre defeito no mesmo trecho (`_e_reducao`/`_reparar_grant_pelo_stripe`). O `CLAUDE.md` §4 é explícito sobre o que fazer nessa hora: **enumerar por escrito em vez de remendar**. A tabela abaixo é o contrato — o código implementa exatamente estas células, e teste que não casar com uma delas está errado ou é célula nova.

| # | assinatura no Stripe | grant | veredito | efeito |
|---|---|---|---|---|
| 1 | conta **sem `stripe_customer_id`** | qualquer | `reduz` | não há relação com o gateway a confirmar |
| 2 | **consulta falhou** (rede, `StripeLookupError`, SDK) | qualquer | `indisponivel` | **não escreve** + alerta |
| 3 | **nenhuma ativa** (`None`) | qualquer | `reduz` | a assinatura acabou de verdade |
| 4 | viva, **sem `current_period_end` legível** | qualquer | `indisponivel` | **não escreve** + alerta |
| 5 | **`past_due`, dentro dos 15 d** | qualquer (usa `ends_at` do alvo ativo, senão `plan_expires_at`) | `carencia` | **não escreve**, **só loga** (sem alerta) |
| 6 | **`past_due`, além dos 15 d** | qualquer, **inclusive sem grant ativo** | `reduz` | única redução com assinatura viva — **corrigida**: antes o "sem alvo" saía por `nao_reduz` e o teto virava inalcançável |
| 7 | `active`/`trialing` | grant **ativo** daquela `sub_id`, `ends_at < fim` | `reparou` | estica **só o `ends_at`**; `plan_stored` intocado |
| 8 | `active`/`trialing` | grant **ativo** daquela `sub_id`, `ends_at >= fim` | `nao_reduz` | **não escreve** + alerta (o grant já cobre o que o Stripe promete) |
| 9 | `active`/`trialing` | sem grant da `sub_id`; **`legacy` ATIVO** e menor | `reparou` | repara no `legacy` — é a reconstrução do mesmo acesso de cartão |
| 10 | `active`/`trialing` | **nenhum grant ATIVO** (inclusive `legacy` **revogado**) | `nao_reduz` | **não escreve** + alerta. **Corrigida**: o alvo do reparo só pode ser grant `status='active'` — reparar um revogado o ressuscitaria (`upsert_grant` grava `status='active'` e zera `revoked_reason`), reintroduzindo o D4 pela porta do reparo. **Revogação é ato deliberado; só um evento a desfaz** |

Duas invariantes que a matriz protege, e que valem para qualquer célula nova:

1. **O reparo nunca ressuscita.** Só grant `active` é alvo; `revoked` é decisão registrada, não defasagem.
2. **O reparo nunca escolhe tier.** Só a **data** se move. `/billing/change-plan` troca o price mantendo o mesmo `sub_id`, então inferir plano do price aqui exigiria duplicar o `_stored_plan_for_price` do monólito — e um reparo que chuta tier **concede tier que ninguém comprou**. O tier continua sendo o que o último **evento** escreveu.

Isso satisfaz "nunca rebaixar enquanto houver falha conhecida de materialização" sem inventar flag: **falha de materialização = grant que não bate com o Stripe**, e é exatamente o que a consulta descobre. E satisfaz "reparar só a partir de estado autoritativo": o único lugar que repara grant lê o **Stripe**, nunca `auth_accounts`.

> `ponytail:` a consulta só acontece no caminho de **redução por varredura** — punhado de contas por dia (as que expiram de fato), não a base. O custo é uma chamada por conta reduzida. Se um dia doer, o upgrade é agrupar por `stripe_customer_id` e consultar em lote.

### 4.1.2 O 5xx reentrega o evento INTEIRO — efeito por efeito

Passar a devolver 5xx tem preço, e ele tem de estar escrito: a Stripe reentrega **o evento todo**, não o pedaço que falhou. O mecanismo tem duas partes.

**Parte 1 — ordem.** O grant é o **primeiro** efeito do ramo. Se ele falha, **nada depois rodou**: sem e-mail, sem funil, sem GA4/CAPI, sem `mark_plan_selected`. A primeira reentrega, portanto, não repete nada — ela *executa* pela primeira vez. Duplicata só existe quando o grant passa e algo **depois** falha, que é o mesmo comportamento que o handler já tem hoje para qualquer exceção inesperada.

**Parte 2 — dedup por efeito**, para a reentrega desse segundo caso:

| efeito | na reentrega | mecanismo |
|---|---|---|
| `upsert_grant` | idempotente | `on conflict (source, external_ref)` + guarda de `event_version` |
| `recompute_entitlement` | idempotente | é função do estado, não do evento |
| `update_user_plan` / `set_payment_status` | idempotente | escrita do mesmo valor |
| `mark_plan_selected` | idempotente | `where plan_selected_at is null` (já era) |
| `claim_trial_for_user` | idempotente | 1 por telefone, na vida (já era) |
| **`record_checkout_completed`** (funil) | **duplicava** | **unique parcial só em `kind='completed'`** (`uniq_checkout_funnel_sessao_completed`, `db/schema.py:1880`). **Sem `on conflict`**: o `except` que o `_record` já tem faz o mesmo trabalho e **deixa rastro no log**. A versão com `on conflict do nothing` foi removida por duas razões medidas — não era medida por teste nenhum (tirá-la deixava a suíte verde, porque o `except` cobre) e, **sem alvo de conflito**, engoliria em silêncio qualquer unique futura desta tabela. A unique é só no `completed` porque o `started` **repete de propósito** quando o checkout reaproveita uma sessão do Stripe, e a versão no par `(session_id, kind)` quebrava o teste que cobre esse reaproveitamento |
| **e-mail** (`send_pro_welcome_email`, `send_pro_charged_email`) | **duplicava** | **dedup no `_fire_email`**, que é o ponto único por onde os dois passam: `recent_event_exists(<chave>, user_id, 1.0)` antes de enviar e `log_system_event` depois — o mesmo padrão do `trial_ending_email_sent` que já existe no repo |
| GA4 `send_purchase` | dedup do outro lado | `transaction_id` (sessão/fatura) |
| Meta CAPI | dedup do outro lado | `event_id` (`purchase_<id>`) |
| `admin_notify` (`notify_new_pro`) | **pode duplicar, aceito** | é ping interno para o dono; dedup aqui custaria mais do que a linha repetida no Discord. Registrado, não silenciado |

**Entrega "pelo menos uma vez" para e-mail no fluxo do Stripe fica RESTRITA à janela residual** (cair entre enviar e registrar o envio), igual ao que o §8.2 já decidiu para o Pix. O que não se aceita é e-mail novo **a cada retry** — isso o dedup fecha.

### 4.2 Precedência entre gateways (consequência da projeção)

| combinação | resultado |
|---|---|
| Pix vigente + `subscription.deleted` | Pix mantém plano e data; status `active`; e-mail de cancelamento não sai (`billing_cancel_ignorado_pix`) |
| Pix vigente + `invoice.paid` de tier menor | tier maior vence enquanto vigente |
| evento Stripe fora de ordem | não aplica (§6) |
| `legacy` + primeiro evento `stripe` | **`legacy` é revogado** na mesma transação (§5.1) |
| downgrade Pix agendado | grants contíguos → cobertura ininterrupta; o tier vira na data |
| buraco (revogação antecipada, estorno do meio) | cobertura para no buraco; sem vigente agora → `free` |
| conta sem nenhum grant | **nada é escrito** + alerta |
| sem grant Pix | Stripe, idêntico a hoje |

### 4.3 Latência da entrada de grant futuro

O loop de 60 s que drena a outbox roda também:

```sql
-- passada de 60 s: só quem transicionou desde a última
select distinct user_id from plan_grants
 where status='active' and (starts_at between :ultima_passada and now()
                         or ends_at   between :ultima_passada and now())

-- varredura diária (`desde = None`): TODOS os usuários com grant ativo
select distinct user_id from plan_grants where status='active'
```

A passada de 60 s é indexada e devolve zero linhas quase sempre. **Nenhum job novo.**

A varredura diária **não tem janela** (conserto D8): a versão com janela de 25 h só consertava o que transicionou *dentro* dela, então processo fora do ar por mais tempo que a janela deixava `plan` e `status` velhos **para sempre**. Sem janela não existe "mais tempo que a janela" — é auto-curativa por construção.
> `ponytail:` custo = um `recompute` por usuário com grant ativo por dia; o pior caso mede o número de **pagantes**. Se doer, o upgrade é comparar projeção × grants em SQL e reprojetar só quem divergir.

### 4.4 Antecipação no cancelamento manual do Stripe

> **ESCOPO: PR 1b, não 1a** (decisão do dono, 2026-09-04). O SQL desta seção junta
> `pix_charges` pelo `stripe_subscription_id`, e `pix_charges` nasce no 1b — a antecipação
> não tem o que antecipar enquanto não existir grant `pix` no mundo. Os testes **20** e **20b**
> acompanham. Antes, §14 e §17 se contradiziam aqui: o §14 listava a antecipação no 1a e o
> §17 cobrava os casos 1–24 como prova do 1a.

Em `customer.subscription.deleted`, depois de revogar o grant do Stripe e antes de projetar:

```sql
update plan_grants
   set starts_at = now(), event_version = :versao_do_evento,
       last_event_id = :event_id, updated_at = now()
 where id = (
   select g.id
     from plan_grants g
     join pix_charges c on c.id::text = g.external_ref
    where g.user_id = %s and g.source = 'pix' and g.status = 'active'
      and g.starts_at > now()
      and c.stripe_subscription_id = :sub_id_do_evento   -- o VÍNCULO
    order by g.starts_at asc
    limit 1)
```

`ends_at` **não muda** — nenhum dia extra. Idempotente.

**Restrição (correção 2):** o UPDATE anterior antecipava **todos** os grants Pix futuros do usuário. Agora antecipa **um só**, e só o certo:

- **`limit 1` com `order by starts_at`** — o primeiro elegível, nunca um downgrade agendado que está na fila atrás dele. Antecipar um grant que não é o da migração desloca uma vigência que ninguém pediu para deslocar.
- **O vínculo é a assinatura**: `pix_charges.stripe_subscription_id` = a assinatura do `subscription.deleted` que está sendo processado. A coluna é gravada na criação da cobrança (§9, passo 3), então o par cobrança↔assinatura existe desde antes do pagamento.
- **Sem correspondência, não antecipa nada** — grant futuro que não nasceu daquela migração não se mexe quando aquela assinatura cai.

**Correção nº 5, e a decisão que o coordenador pediu explícita: o §6 continua sendo regra única — toda escrita em grant carrega `event_version`, inclusive esta.** Sem o carimbo, a linha ficava com conteúdo novo e versão velha, e **qualquer** escrita posterior sobre `('pix', charge_id)` a repunha no futuro: reentrega do `RECEIVED`, um `PAYMENT_REFUNDED`, ou uma reconciliação. Como cinto adicional, grant `pix` é **criação única** (§6): eventos de pagamento nunca reescrevem a janela, só `revoke` e esta antecipação a alteram.

Continua valendo a copy do PR 2: **"não cancele pela sua conta no Stripe"**.

---

## 5. Backfill/resync dos assinantes Stripe

**Problema:** no deploy, a base pagante tem `plan`/`plan_expires_at` e nenhum grant.

### 5.1 O backfill inicial (roda no boot, sem rede, NÃO é reparador)

No bloco DDL idempotente de `db/schema.py`, depois do `create table plan_grants`, no estilo dos backfills que já existem ali (`plan_selected_at`, `onboarding_completed_at`, `:1709-1730`):

```sql
insert into plan_grants (user_id, source, external_ref, plan_stored,
                         starts_at, ends_at, status, event_version)
select distinct on (a.user_id)
       a.user_id, 'legacy', 'legacy:' || a.user_id, a.plan,
       now(), a.plan_expires_at, 'active', 0
  from auth_accounts a
 where coalesce(a.plan, 'free') <> 'free'
   and a.plan_expires_at is not null
   and a.plan_expires_at > now()
   and coalesce(a.last_payment_status, '') <> 'grandfathered'
   and not exists (select 1 from plan_grants g where g.user_id = a.user_id)
 order by a.user_id, a.plan_expires_at desc, a.id desc
on conflict (source, external_ref) do nothing
```

**v6: o resync deixou de reparar e virou backfill inicial ÚNICO** (decisão do dono). O `not exists` + `do nothing` dizem a mesma coisa por dois caminhos: **linha de grant existente nunca é tocada pelo boot**. Consequências, todas desejadas:

- **Grant revogado não ressuscita** — não porque uma cláusula o exclui, mas porque o comando **não atualiza nada**. Fecha o D4 pela raiz, e o artefato deixa de depender de um `where` sutil.
- **`auth_accounts` deixa de ser fonte de verdade de grant.** Ela alimenta *apenas* a criação inicial de quem não tem grant nenhum — a população que existia antes desta tabela.
- **O reparo saiu do boot.** Grant defasado é reparado pela **regra da redução** (§4.1.1 B), que lê o **Stripe**, fora do boot. `plan_grants` não é mais realimentado por cópia da projeção.
- **`distinct on (a.user_id)` é DISPONIBILIDADE, não estilo.** `auth_accounts` não tem unique em `user_id` (`\d auth_accounts`): duas linhas pagas e vigentes do mesmo usuário gerariam dois `legacy:<uid>` no mesmo comando e o Postgres recusa com `CardinalityViolation`. Isto roda dentro do `init_db`: o erro não degradaria a migração, **derrubaria a subida da aplicação**. Desempate determinístico pela maior validade.
- **Sem rede, e agora sem contradição.** O §5.1 sempre proibiu chamar o Stripe no boot ("rede em migração é modo de falha"), e o boot passou a não precisar: quem consulta o Stripe é a varredura (§4.1.1 B), fora do `init_db`, fora do advisory lock, e sem o container novo do Railway esperando por ela.

- ~~**`do update` em vez de `do nothing`**: reverter e re-aplicar o 1a reconcilia o `legacy` com o modelo de leitura~~ — **removido na v6**. Era esse `do update` que fazia o boot copiar a projeção para dentro do grant.
- ~~Ressuscitar `legacy` revogado é inofensivo: se a revogação foi legítima, `auth_accounts` não diz "pago e vigente"~~ — **afirmação PROVADA FALSA pelo Tester (D4)**, riscada aqui de propósito em vez de apagada: `legacy` revogado por `superseded_by_stripe` + grant `stripe` vigente ⇒ `auth_accounts` **diz** pago e vigente ⇒ o `where` externo **não** excluía e o boot reanimava o revogado. `auth_accounts` é projeção dos grants; deixá-la recriar grant morto é transformar projeção de volta em direito.
- **Onde foi parar a proteção do pagante:** não no resync. Ela é o §4.1.1 — materialização obrigatória (o grant não pode mais ficar para trás) e regra da redução (ninguém é rebaixado por varredura sem o Stripe confirmar).
- **Sem chamar o Stripe.** Rede em migração é modo de falha, não de leitura.
- **`event_version = 0`**: qualquer evento real supera o legado.
- **Supersessão (nº 2):** todo upsert **aplicado** de grant `source='stripe'` executa, na mesma transação:
  ```sql
  update plan_grants set status='revoked', revoked_reason='superseded_by_stripe',
         revoked_at=now(), event_version=:versao
   where user_id=%s and source='legacy' and status='active'
  ```
  "Aplicado" = o `insert … returning id` do §6 devolveu linha; evento velho bloqueado pela guarda **não** supersede. Fecha os três caminhos: grant `stripe` que **encurta**, de **tier menor**, e assinatura que **lapsa sem `deleted`** (`unpaid`).
- Revogar grant `stripe` continua revogando o `legacy` do mesmo usuário.

### 5.2 `grandfathered`

Projeção sai antes de qualquer escrita e o resync os exclui. Vitalício **não é grant**: `plan_expires_at is null` é "sem data", e virar grant exigiria inventar `ends_at`. Intocados.

### 5.3 Vitalício "de fato" (sem o status)

`plan` pago + `plan_expires_at is null` + sem `grandfathered`: fora do resync, protegido pela segunda saída antecipada do §4.1. A migração conta e loga (`backfill_grants_sem_data`), sem decisão automática.

---

## 6. Ordem de eventos: `event_version` (regra única)

**Origem:** Stripe → `event["created"]`. Asaas → `dateCreated` do evento/pagamento; ausente → `received_at`.

**Grants `stripe`, `legacy` e `admin` — upsert com guarda de versão:**

```sql
insert into plan_grants (...) values (...)
on conflict (source, external_ref) do update
   set plan_stored=excluded.plan_stored, starts_at=excluded.starts_at,
       ends_at=excluded.ends_at, status=excluded.status,
       event_version=excluded.event_version, last_event_id=excluded.last_event_id,
       revoked_reason=null, revoked_at=null, updated_at=now()
 where plan_grants.user_id = excluded.user_id
   and excluded.event_version > plan_grants.event_version
returning id
```

Duas diferenças em relação à v5, e as duas estão no código:

- **`plan_grants.user_id = excluded.user_id`** é o isolamento do CLAUDE.md §0 dentro do upsert: a unique é `(source, external_ref)`, que é **global**, e sem esta linha um upsert com o `external_ref` de outra conta cairia no `do update` e reescreveria plano e validade **do dono errado**.
- **A cláusula de desempate por `excluded.status='revoked'` SAIU** — ela era inalcançável: este `insert` nasce sempre `status='active'`, então `excluded.status` nunca é `'revoked'`. Removê-la não deixou um teste vermelho, que é a definição de código morto — e código morto num caminho de acesso pago faz a próxima pessoa achar que a regra está coberta quando não está. **A metade "a revogação que empata APLICA" mora no `revoke_grant`** (`event_version <= %s`), que é UPDATE, não upsert.

### 6.1 Empate de segundo — regra determinística (correção 1)

`event["created"]` do Stripe tem precisão de **segundos**, então `invoice.paid` e `subscription.deleted` **podem** empatar. Com o `>` sozinho, o segundo a chegar era descartado **em silêncio** e o acesso passava a depender da ordem de entrega. A v5 dizia que isso não ocorria; ocorre, e a linha de `ponytail:` que afirmava o contrário saiu.

**A regra: no empate, a REVOGAÇÃO ganha.** Ela é implementada em **dois lugares complementares**, e não numa cláusula só: o `>` **estrito** do upsert descarta a concessão que empata, e o **`event_version <= %s`** do `revoke_grant` faz a revogação que empata aplicar. Nada mais aplica no empate.

- **É determinística nos dois sentidos de chegada.** `paid` → `deleted`: o `deleted` empata, é revogação, aplica → `revoked`. `deleted` → `paid`: o `paid` empata, não é revogação, é descartado → continua `revoked`. **Mesmo estado final nas duas ordens** — e é essa comutatividade que o teste 12b mede, não o texto.
- **É o erro conservador certo.** No empate não há como saber qual é o mais novo; conceder acesso indevido é irreversível na direção que dói (produto entregue, dinheiro não), enquanto negar acesso indevidamente é visível, reclamável e reparável pelo grant `admin` (§15).
- **É observável:** `last_event_id` guarda o `evt_…`/`sub_…` que produziu a linha, então "por que este grant está revogado" se responde com um `select`.

> `ponytail:` teto real (o anterior estava errado): **dois eventos de mesma semântica no mesmo segundo** — duas concessões com `ends_at` diferentes — mantêm a **primeira**. Não é caso conhecido (a Stripe não emite duas faturas pagas no mesmo segundo para a mesma assinatura); se aparecer, o upgrade é ordenar por `(created, id)`.

**Grants `pix` — criação única:** `on conflict (source, external_ref) do nothing`. Depois de criado, um grant Pix só muda por operação explícita — `revoke` (estorno/chargeback) ou `antecipar` (§4.4) — e **cada uma carimba `event_version`**. Não existe caso legítimo de "evento posterior recalcula a janela do mesmo pagamento": a janela é decidida uma vez, no pagamento.

Consequências:
- `invoice.paid` antigo depois de `subscription.deleted`: versão menor → o `where` bloqueia. **A revogação não é desfeita.**
- evento velho não estica `ends_at`, não restaura tier e **não supersede `legacy`** (o `returning` vem vazio).
- reativação legítima (versão maior) aplica.
- **empate de segundo APLICA, e tem regra própria: §6.1.**

---

## 7. Preço, crédito e vigência

Função pura `plano_da_cobranca(grants_ativos, plano_novo, preco_novo_cents, min_cents)`:

| relação | vigência | preço | crédito |
|---|---|---|---|
| mesmo plano (renovação) | `max(now, fim dos grants)` | cheio | — |
| **upgrade, acesso atual PIX** | `now` | `preço − crédito` | `round(amount_cents × dias_restantes / 365)` |
| **upgrade, acesso atual STRIPE** | fim do período do cartão | **cheio** | **nenhum** |
| downgrade | fim do período atual | cheio | — |

**Crédito monetário existe só no caminho Pix → Pix** — repetido aqui e no §9 de propósito.

`access_expires_at = access_starts_at + 365 dias`; crédito nunca vira tempo. **`ASAAS_MIN_CHARGE_CENTS` não tem default**: sem a env, venda 503. Quando `preço − crédito < mínimo`, **não se cobra o mínimo**: a compra vira **agendada** (starts no vencimento, preço cheio, crédito 0), e a tela diz isso antes do pagamento. Sem `credit_forfeited_cents` — caminho inalcançável.

**Preço e crédito congelam na criação; `access_starts_at` é decidido NO PAGAMENTO.** Só existe **uma** cobrança ativa por usuário (§3.2 + §10), então dois QRs jamais são precificados contra o mesmo crédito.

---

## 8. Webhook, outbox e efeitos

### 8.1 O handler

Precedente do Pluggy (`open_finance.py:~1105` e `~1119`):

| situação | resposta |
|---|---|
| `ASAAS_WEBHOOK_TOKEN` ausente | **503** + `log_system_event("error","asaas_webhook_nao_configurado")` |
| token inválido (`hmac.compare_digest`) | **401** + warning com IP truncado, sem corpo |
| corpo não-JSON / sem id de evento | **400** |
| `insert … on conflict (event_id) do nothing` **commitado** | **200** |
| duplicata | **200**, sem trabalho |
| qualquer falha antes do commit | **5xx** — o Asaas retenta |

O handler não concede acesso, não chama GA4/CAPI, não manda e-mail, não fala com o Stripe. Depois do 200: `background_tasks.add_task(drenar_evento, event_id)` + o loop de 60 s. A flag de venda **não** é consultada aqui.

### 8.2 O dreno — roteamento por tipo de evento (correção nº 8a)

```
drenar_evento(event_id):
    evt = select … where event_id=%s and processed_at is null for update skip locked
    se evt.payload_enc is null: marca processed_at e sai            # purgado (§13.3)
    dados = decifra(payload_enc)
    cobranca = por asaas_payment_id, senão por externalReference

    # A) sem cobrança nossa — e a CONTA ASAAS É DO NEGÓCIO, recebe outros Pix (correção 5)
    se cobranca is None:
        se NÃO casa ^pix:[0-9]+$ no externalReference:
            log "asaas_evento_fora_do_escopo" (contagem)   # SEM alerta, SEM linha
            marca processed_at; sai
        insere linha 'orphan_unknown' + admin_notify
        marca processed_at; sai

    # B) GUARDA DE ÓRFÃO — antes do laço (nº 1)
    se cobranca.user_id is null:
        transição do §11, com destino de pagamento = 'paid_orphan'
        insert em pix_payment_effects (payment_id, 'orphan_notified')
        admin_notify; marca processed_at; sai       # NENHUM efeito de compra

    # C) transição PRIMEIRO, efeitos DEPOIS e SÓ os que o evento autoriza
    aplicou = transição condicional do §11 (UPDATE … WHERE status = <esperado> RETURNING)
    efeitos = EFEITOS_POR_EVENTO[evt.event_type]
    se não aplicou: efeitos = []                    # reentrega/no-op não dispara nada
    para cada efeito em efeitos (na ordem da lista):
        se (asaas_payment_id, efeito) já existe: pula
        executa; insert em pix_payment_effects (na mesma transação quando houver)
    marca processed_at
```

**`EFEITOS_POR_EVENTO` — a tabela que faltava:**

| evento | transição | efeitos disparados |
|---|---|---|
| `PAYMENT_RECEIVED` (**o do Pix**) | → `paid` (ou `paid_orphan`) | `[stripe_cancel, grant, ga4, capi, email]` |
| `PAYMENT_CONFIRMED` (**não ocorre no Pix**; aceito por robustez) | idem | idem |
| `PAYMENT_CREATED` | nenhuma | **nenhum** |
| `PAYMENT_OVERDUE` | → `expired` | **nenhum** |
| `PAYMENT_DELETED` | → `canceled` | **nenhum** |
| `PAYMENT_REFUNDED` total | → `refunded` | `[revoke]` |
| `PAYMENT_REFUNDED` parcial | → `refunded_partial` | **nenhum** (só alerta) |
| `PAYMENT_CHARGEBACK_*` | → `chargeback` | `[revoke]` |
| desconhecido | nenhuma | **nenhum** (log) |

Sem essa tabela, um estorno mandava `purchase` ao GA4 e `Purchase` à CAPI — receita inventada em cima de dinheiro devolvido. **`aplicou == False` zera a lista**: reentrega do mesmo `RECEIVED` não reexecuta nada nem mesmo se a tabela de efeitos tiver sido purgada.

**Ordem dos efeitos de pagamento (correção nº 3):** começa por **`stripe_cancel`**, que lê o `current_period_end` **agora**, reconfirma `pix_charges.stripe_period_end_at` e chama `Subscription.modify(cancel_at_period_end=True)`. Só depois o `grant` roda, usando **aquele** valor — nada de `starts_at` congelado em leitura anterior.

**Compra Pix comum (`stripe_subscription_id is null`) — `stripe_cancel` é NO-OP REGISTRADO.** A esmagadora maioria das compras não vem de migração. Nesse caso o efeito **não chama o Stripe, não conta `attempts`, não alerta**: registra o par `(payment_id, 'stripe_cancel')` e o laço segue para o `grant`. Está escrito porque efeito não definido é efeito que alguém implementa errado — e a versão errada aqui seria acumular `attempts` num efeito que não tem o que fazer, até disparar o alerta de `attempts > 5` em toda venda normal.

**`stripe_period_end_at` é gravada DUAS vezes**, e é isso que tira o `NULL` do caminho comum: uma **estimativa** na criação da cobrança (o valor já foi lido ali para o 409 `stripe_active` e para o modal — nenhuma chamada extra ao Stripe) e a **reconfirmação** no efeito `stripe_cancel`.

**Fallback do `stripe_cancel` (correção do defeito que a nº 3 criou):** falhando, `attempts += 1` e o dreno sai (o grant ainda não existe). Em `attempts > 5`, `admin_notify` **e** o dreno concede o grant assim mesmo, com

```
starts_at = max(now, stripe_period_end_at)   se a coluna tiver valor
starts_at = now()                            se a coluna estiver NULL
# uma expressão: greatest(now(), coalesce(stripe_period_end_at, now()))
```

O `NULL` é alcançável **exatamente quando o fallback é acionado**: o cenário que produz as 6 falhas é o Stripe fora do ar, e aí a leitura do `current_period_end` também falhou. Sem o `coalesce`, `max(now, None)` levanta `TypeError` em Python e `greatest(now(), null)` devolve nulo contra um `starts_at not null` — o fallback que existe para não reter o acesso de quem pagou estouraria justamente na hora de agir. A estimativa da criação faz o `NULL` ficar raro (só quando o Stripe já estava fora **no checkout**), e o `coalesce` cobre o resto.

`now()` é a escolha conservadora e é o **mesmo raciocínio que justifica o fallback**: quem pagou entra imediatamente, e a sobreposição com um período de cartão eventualmente renovado é **estornável** — acesso negado a quem pagou não é recuperável. O custo é o cliente queimar dias do ano Pix durante um período de cartão que ele já tinha pago; é menor que o da estimativa ausente, e menor ainda que o de reter acesso.

Instabilidade do Stripe não pode reter indefinidamente o acesso de quem pagou; a cobrança fica marcada para a varredura continuar tentando o `modify`.

**`grant`** roda numa transação com a transição, `insert … on conflict do nothing` (§6), `recompute_entitlement`, `record_checkout_completed` e o registro do efeito. **`ga4`/`capi`** deduplicam do outro lado também. **`email`**: o registro fecha a janela normal; a residual (cair **entre** enviar e registrar) é **formalmente "pelo menos uma vez"**, mitigada pelo `recent_event_exists("pix_paid_email_sent", user_id, 1.0)` já existente — e-mail duplicado é o pior caso aceitável, cobrança duplicada não seria.

Falha em qualquer efeito: `attempts += 1`, `last_error`, sai; a próxima passada retoma **no efeito que faltou**. `attempts > 5` → `admin_notify`.

**É isto que fecha "o processo morre entre `paid` e a concessão":** o que autoriza parar é o registro do efeito **por pagamento**, nunca a transição de status.

---

## 9. Migração Stripe → Pix

1. `POST /billing/pix/checkout` com Stripe ativo e sem `confirm_cancel_stripe` → **409 `stripe_active`** com `current_period_end`.
2. Modal: cancelamento no fim do período já pago, sem cobrança dupla, e o aviso de **não cancelar pelo painel do Stripe** (§4.4).
3. Com `confirm_cancel_stripe: true`: cria só a cobrança Pix, guardando `stripe_subscription_id`. **Nada é tocado no Stripe agora.**
4. **No pagamento**, primeiro efeito: lê `current_period_end`, grava `stripe_period_end_at`, agenda `cancel_at_period_end=True`. O grant nasce depois, com `starts_at = stripe_period_end_at`.

QR abandonado não mexe em nada — **não existe compensação**.

**A promessa é condicional, e assim está escrito:** *enquanto o agendamento entrar*, ninguém paga duas vezes o mesmo período. Se o `modify` não entrar, o passo 4 alerta, a varredura continua tentando, e **se uma renovação for cobrada nesse intervalo o caminho é estorno manual dessa fatura** — o grant Pix já terá começado depois do período pago, então o cliente não fica sem acesso.

Pix → Stripe não tem fluxo: `_billing_checkout_for_user` (`:3963`) recusa com grant Pix vigente.

---

## 10. Saga de criação e substituição

**Postgres não torna a chamada HTTP atômica.** A transação garante só a contabilidade local; o mundo remoto é fechado pela varredura.

| passo | estado | processo morre aqui → |
|---|---|---|
| insert local (sob `_billing_user_lock`, `:3921`) | `draft` | varredura **reconcilia** aos 15 min (§10.1); só apaga com prova de inexistência remota |
| antes do POST ao Asaas | `creating` | **ambíguo**: varredura consulta `GET /payments?externalReference=pix:<id>`; achou → `attach` + `pending`; não achou → volta a `draft` |
| resposta salva | `pending` | normal |
| **substituição 1**: `update … set status='canceling'` | `canceling` | varredura repete o `DELETE` remoto |
| **substituição 2**: `DELETE` no Asaas | — | falhou → **503 ao cliente, nada é criado**; varredura reconcilia |
| **substituição 3**: `canceled` + insere o novo `draft` | `draft` | fluxo normal |

**Mudança (nº 6):** a substituição **cancela remotamente ANTES de criar a nova cobrança**, como o caminho do Stripe já faz (`Session.expire` antes de criar, com 503 quando falha, `:4028-4043`). Consequências:

- **Nunca existem dois QRs pagáveis do mesmo usuário** — logo, nunca duas cobranças precificadas contra o mesmo crédito. O furo financeiro morre na origem.
- O índice parcial volta a cobrir `canceling` (§3.2), porque `canceling` e `draft` não coexistem mais.
- Resíduo remoto (`DELETE` que erra mas efetiva, ou pagamento em voo): vale a célula `canceled` + `RECEIVED` do §11 — **o dinheiro entrou, o acesso sai** (uma cobrança só, sem crédito duplicado), com `admin_notify`; estorno manual se o cliente reclamar.
- **Pagamento tardio tem teto:** a varredura cancela no Asaas toda cobrança `pending`/`expired` com mais de **`PAGAMENTO_TARDIO_MAX_DIAS = 60`** dias do vencimento. Por isso a retenção de 90 dias (§13.1) nunca apaga algo ainda pagável (nº 7).

### 10.1 Expurgo é por CONFIRMAÇÃO, nunca por relógio (correção 3)

O texto anterior dizia "apaga `draft` com mais de 15 min". **Idade não é prova:** um `draft` velho pode ter ganhado id remoto num POST cuja resposta se perdeu, e apagá-lo cria dinheiro sem linha — exatamente o caso que o `orphan_unknown` só consegue chorar depois.

**A idade decide QUANDO reconciliar. Só a reconciliação decide o que apagar.** Uma cobrança sai da base em duas situações, e nenhuma delas é temporal:

| condição | prova exigida |
|---|---|
| **(a) cancelamento confirmado** | o `DELETE` respondeu sucesso **ou** `GET /payments?externalReference=pix:<id>` mostra a cobrança como `DELETED` → `canceled` |
| **(b) nunca ganhou id remoto** | `asaas_payment_id is null` **e** `GET /payments?externalReference=pix:<id>` devolve **lista vazia** → só então apaga |

O que **nunca** é decidido no escuro:

- **`creating` não expurga nunca.** É o estado ambíguo por definição: reconcilia; achou → `attach` + `pending`; não achou → volta a `draft`, que na passada seguinte cai na regra (b).
- **`canceling` não vira `canceled` por tempo** — só com a prova (a).
- **Consulta ao Asaas falhou?** A linha fica **como está**; a passada seguinte tenta de novo. Indisponibilidade do provedor não é evidência de inexistência.
- Mesma linha irreconciliável por mais de **24 h** → `admin_notify`: aí o problema é de integração, não de cobrança.

---

## 11. Máquina de estados da cobrança

Estados: `draft` · `creating` · `pending` · `canceling` · `canceled` · `paid` · `paid_orphan` · `orphan_unknown` · `expired` · `refunded` · `refunded_partial` · `chargeback`.

> **Coluna "RECEIVED / CONFIRMED":** no Pix **só o `PAYMENT_RECEIVED` ocorre** — a liquidação é instantânea e a plataforma pula o `CONFIRMED`, que é de cartão. As duas ficam na mesma coluna **por robustez** (se um dia vendermos cartão pelo Asaas, ou se a plataforma mudar), **não porque façam parte do fluxo**. Teste que exercitar `CONFIRMED` está exercitando robustez, e o §16 diz isso onde importa.

| estado \ evento | RECEIVED / *CONFIRMED* | OVERDUE | DELETED | REFUND total | REFUND parcial | CHARGEBACK | varredura |
|---|---|---|---|---|---|---|---|
| `draft` | (sem id remoto) | — | — | — | — | — | reconcilia aos 15 min; apaga **só** com lista vazia no Asaas (§10.1) |
| `creating` | reconcilia e cai em `pending` | — | — | — | — | — | consulta por `externalReference` |
| `pending` | → **`paid`** + efeitos de pagamento | → `expired` | → `canceled` | — | — | — | reconcilia; cancela no Asaas após 60 d |
| `canceling` | → **`paid`** + efeitos + `admin_notify` | → `expired` | → `canceled` | — | — | — | repete o `DELETE` |
| `canceled` | → **`paid`** + efeitos + `admin_notify` | no-op | no-op | — | — | — | — |
| `expired` | → **`paid`** + efeitos (**tardio válido até 60 d**) | no-op | no-op | — | — | — | cancela no Asaas após 60 d |
| `paid` | **no-op** (nenhum efeito, §8.2) | no-op | no-op | → `refunded` + `[revoke]` | → `refunded_partial`, não revoga, alerta | → `chargeback` + `[revoke]` | confere pago × esperado |
| `paid_orphan` | no-op | no-op | no-op | → `refunded` | → alerta | → `chargeback` | lista de conciliação |
| `orphan_unknown` | no-op | no-op | no-op | → `refunded` | → alerta | → `chargeback` | lista de conciliação |
| `refunded`/`chargeback` | só por reconciliação manual | no-op | no-op | no-op | no-op | no-op | log |

**A linha `user_id is null` saiu da tabela** (nº 1): não é estado, é **condição do titular**, e virou guarda do dreno (§8.2 B). Assim uma cobrança `pending` de conta excluída casa com **uma** linha só.

`orphan_unknown` é a célula para **evento sem cobrança correspondente cujo `externalReference` é NOSSO** (§8.2 A): dinheiro do PigBank entrou e não havia linha — isso nunca se descarta em silêncio.

**Mas "desconhecido" não é sinônimo de "nosso" (correção 5).** A conta Asaas é a **conta do negócio** e recebe outros Pix: cobrança criada à mão no painel, transferência avulsa, pagamento de um cliente por fora. Classificar tudo isso como `orphan_unknown` geraria alerta e fila de conciliação sobre **dinheiro de terceiros** — e alerta que dispara por dinheiro que não é problema é alerta que ninguém lê no dia em que for.

**O filtro é o formato do `externalReference`:** `^pix:[0-9]+$` (o prefixo que só o `POST /billing/pix/checkout` escreve, §3.2).

| `externalReference` | decisão |
|---|---|
| casa `^pix:[0-9]+$` e a linha existe | fluxo normal |
| casa `^pix:[0-9]+$` e a linha **não** existe | **`orphan_unknown` + `admin_notify`** — é nosso e sumiu |
| ausente, vazio, ou fora do formato | **ignora em silêncio**: `processed_at` marcado, `log_system_event("info","asaas_evento_fora_do_escopo")` com contagem, **sem alerta e sem linha** |

O log de contagem existe para o caso em que o filtro esteja errado demais: se ele crescer, alguém vê. `ponytail:` regex fixa, não env — o formato é escrito por nós, num lugar só.

---

## 12. Estorno e chargeback

Efeito `revoke`: `revoke_grant('pix', charge_id, reason, event_version)` + `recompute_entitlement`, registrado em `pix_payment_effects` como qualquer outro. **Não existe restauração de snapshot**: o acesso passa a ser o que os grants restantes sustentam, então compra feita depois do estorno continua valendo. Estorno **parcial** nunca revoga sozinho: alerta e para. **Nenhum evento de estorno dispara efeito de compra** (§8.2).

---

## 13. Retenção, minimização e privacidade

### 13.1 Categorias (parâmetros nomeados em `db/pix_charges.py`)

| categoria | prazo | finalidade |
|---|---|---|
| `draft`/`creating`/`pending`/`expired`/`canceled` **sem pagamento** | **`RETENCAO_TENTATIVA_DIAS = 90`** | suporte e conciliação da tentativa. Número **operacional**, não jurídico. **Só é seguro porque `PAGAMENTO_TARDIO_MAX_DIAS = 60` < 90** e a varredura cancela no Asaas aos 60 (§10) |
| `paid`/`refunded`/`refunded_partial`/`chargeback`/`paid_orphan`/`orphan_unknown` | **`RETENCAO_PAGAMENTO_DIAS = None` — A DEFINIR** | obrigação legal/fiscal. Sem número no plano e sem número na política até aval de contador/jurídico. Com `None`, **nada é apagado** |
| `pix_webhook_events` | **`RETENCAO_OUTBOX_DIAS = 7` a partir de `received_at`** | reprocessamento e depuração |

**Quando o número jurídico chegar:** setar `RETENCAO_PAGAMENTO_DIAS`; a varredura já existente passa a apagar, e a política troca "pelo prazo exigido pela legislação aplicável" pelo prazo. Nada mais muda.

### 13.2 Pseudonimização (molde exato da `plan_trials`)

- `pix_charges.user_id` → **FK `on delete set null`**, criada por `ensure_pix_charges_user_fk(cur)` em `db/schema_repairs.py`, espelhando `ensure_plan_trials_user_fk` (`:95-120`), inclusive o índice do lado que referencia.
- `pix_charges` fica **fora** de `user_owned_tables` (`db/privacy.py:766`), com comentário citando `:889`: o vínculo some **pelo banco, não por UPDATE**, porque UPDATE perde a corrida com um commit concorrente e a varredura pós-commit nunca revisita a tabela.
- Colunas zeradas na exclusão: `ga_client_id`, `fbp`, `fbc`, `qr_payload_enc`, `asaas_customer_id`; `purged_at` marcado. O UPDATE **não é a garantia**: a varredura diária re-zera qualquer linha com `user_id is null and purged_at is null`.
- **Sobra para reconciliar:** `external_reference`, `asaas_payment_id`, valores, plano, status e datas.
- `plan_grants` segue o usuário (`on delete cascade`).

### 13.3 O `payload` da outbox

1. **Minimização na escrita**: só `event_id`, `event`, `payment.id`, `externalReference`, `value`, `netValue`, `status`, `dateCreated`, `customer`. O resto é descartado antes do insert.
2. **Cifra**: `core.crypto.encrypt_pii_optional`; leitura com `decrypt_pii_optional` + `PiiAccessContext(purpose="asaas_webhook_drain", actor="system:pix_outbox")`.
3. **Purga em 7 dias a partir de `received_at`, processado ou não** (nº 10): `update pix_webhook_events set payload_enc = null, purged_at = now() where received_at < now() - :dias and purged_at is null`. A linha **fica** (`event_id`, `event_type`, `event_version`, `attempts`, `last_error`) para forense; o dado pessoal sai. Evento travado perde o reprocesso — aceitável e **já alertado** em `attempts > 5`; o dreno que encontrar `payload_enc is null` marca `processed_at` e sai, sem laço infinito.

Isso importa porque o evento que trava é justamente o órfão — dado de alguém que **pediu exclusão da conta**.

### 13.6 O QR é instrumento de pagamento (correção 4)

`qr_payload` é o "copia e cola" que **move dinheiro**: em texto puro no banco é um instrumento ao portador esperando um dump.

- **Cifrado em repouso** como `qr_payload_enc`, mesmo padrão do payload da outbox (`core.crypto.encrypt_pii_optional`; leitura com `PiiAccessContext(purpose="pix_qr_read", actor="system:billing_pix")`).
- **Apagado** (`= null`) assim que a cobrança chega a `paid`, `canceled` ou `expired` **confirmado** — depois disso não há uso legítimo; a varredura limpa o que escapar.
- **Nunca** em log, em `details` de auditoria ou em mensagem de erro.

**E o identificador que sai do servidor é o `public_token`, nunca o `asaas_payment_id`.** O motivo não é teórico: `frontend/home.html:2151` faz `fbq("track","Purchase", …, { eventID: "purchase_" + sid })` — o `sid` da URL de sucesso **vai para o pixel da Meta**, e o mesmo valor alimenta o GA4. Com o desenho anterior, o id de pagamento do provedor sairia do nosso domínio para um terceiro de publicidade.

| onde | passa a usar | por que continua servindo para deduplicar |
|---|---|---|
| `GET /billing/pix/{token}` (poll) | `public_token` | chave pública da cobrança; o `asaas_payment_id` não aparece em URL nenhuma |
| `sid` do `/home?upgrade=success&sid=…` e o `eventID` do pixel | `public_token` | 1:1 e **imutável** com a cobrança — `secrets.token_urlsafe(16)` gerado uma vez na criação, nunca reemitido |
| GA4 `transaction_id` (efeito `ga4`) | `public_token` | mesma propriedade: um token por cobrança, estável entre reentregas, então retry de webhook não vira receita dobrada |
| Meta CAPI `event_id` (efeito `capi`) | `purchase_<public_token>` | idêntico ao `eventID` que o navegador manda — é o que **casa os dois lados** |
| `pix_payment_effects` | **continua** `asaas_payment_id` | tabela de servidor, nunca sai daqui, e é o id que o webhook traz |

### 13.4 Pagamento depois da exclusão da conta

Guarda do dreno (§8.2 B), não estado da tabela: transição com destino **`paid_orphan`**, **nenhum grant**, `admin_notify`, linha na lista de conciliação. Caminho previsto: **estorno manual pelo painel do Asaas**. Nunca se recria conta a partir de webhook.

### 13.5 Página de privacidade

`frontend/privacy.html:109` ganha `<li><strong>Asaas</strong>: pagamentos via Pix, cobrança e eventos financeiros relacionados.</li>` na lista **nominal**, e o trecho de retenção passa a dizer que registros de pagamento são mantidos, de forma pseudonimizada, **pelo prazo exigido pela legislação aplicável** — **sem número** até a validação.

---

## 14. Arquivos, na ordem

**PR 1a** — 1) `db/schema.py`: `plan_grants` + resync (§5). 2) `db/plan_grants.py` (novo): upsert com guarda de versão, criação-única para `pix`, supersessão do `legacy`, revoke. **A antecipação (§4.4) NÃO entra no 1a** — vai para o 1b junto com `pix_charges`. 3) `core/services/billing_access.py` (novo): `recompute_entitlement` (§4). **`plano_da_cobranca` (§7) NÃO entra no 1a** — função pura sem chamador no 1a é código especulativo (CLAUDE.md §0.2); vai para o 1b. 4) `frontend/finance_bot_websocket_custom.py`: os quatro sites (`:4699`, `:4852`, `:5032`, `:5033`) passam a grant + recompute, com o **grant como PRIMEIRO efeito e sem `except`** (§4.1.1 A) — falha vira 5xx retryable; mais o helper único de **normalização da referência de assinatura** (`_sub_ref`: id simples **e** objeto expandido produzem a MESMA `external_ref`), usado por `checkout.session.completed` e por `invoice.paid`; e `revoke_grant` no `deleted` **com o `external_ref` da assinatura que o evento nomeia**. 4b) `db/checkout_funnel.py` + DDL: **unique parcial só em `kind='completed'`** (`db/schema.py:1880`), **sem `on conflict`** — quem absorve a reentrega é o `except` que a função já tinha, e ele loga (§4.1.2); e dedup de e-mail no `_fire_email`. 5) **`core/admin_dashboard.py::set_account_plan`**: escreve grant `source='admin'`, `external_ref='admin:<uid>'`, `event_version = epoch(now)`. **A revogação dos grants ativos vale para TODA troca manual de plano** (conserto D3), não só para `free`: sem isso, baixar de Pro para Essencial na mão deixava o grant de Pro vivo e a projeção devolvia o Pro na passada seguinte. Para `free`, o resultado é o mesmo de antes — revoga e não cria grant novo. Um lugar só — `/admin/api/users/{id}/plan` e `/admin/grant-pro` compartilham a escrita (nº 9). 6) loop de 60 s de re-projeção + varredura diária.

**PR 1b** — 7) `db/schema.py`: `pix_charges`, `pix_webhook_events`, `pix_payment_effects`, índices. 8) `db/schema_repairs.py`: `ensure_pix_charges_user_fk`. 9) `db/pix_charges.py`, `db/webhook_outbox.py` (novos). 10) `core/services/asaas.py` (novo, molde de `core/services/pluggy.py` com `httpx`; erros **sem corpo da resposta**, que traz PII). 11) `frontend/routes/billing_pix.py` (novo): `POST /billing/pix/checkout`, `GET /billing/pix/{id}` (404 para dono errado), `POST /billing/asaas/webhook` + `EFEITOS_POR_EVENTO`. 12) monólito: `CSRF_EXEMPT_PATHS` +1 (`:1892`), `include_router`, `/billing/subscription` (`:4318`) reconhece Pix **antes** do Stripe (`gateway:"pix"`), `_billing_checkout_for_user` (`:3963`) recusa com Pix vigente, `plans-config` (`:4163`) + `pix_annual_available`, dreno no loop de 60 s. 13) `core/services/engagement_scheduler.py`: reconciliação da saga, cancelamento remoto aos 60 d, retenção/purga/pseudonimização, e `_check_pix_annual_ending()` — **função irmã** do `_check_trial_ending` (`:206`), não reuso (aquela filtra `plan='pro' and last_payment_status='trialing'`); SQL próprio com `join plan_grants … source='pix'`, janelas 6,5–7,5 e 2,5–3,5 dias, dedup `recent_event_exists(…, 2.0)`. 14) `core/services/email_service.py`: `send_pix_annual_ending_email`. 15) `db/privacy.py` (export + comentário) e `frontend/privacy.html`.

**PR 2** — 16) `frontend/precos.html`: botão "Pagar no Pix (à vista)" só no Anual; modal com **"Copiar código Pix" como ação primária**; linha de crédito/vigência; modal do §9 com o aviso do §4.4; poll com teto; sucesso reaproveita `/home?upgrade=success&sid=<public_token>&ev=purchase&td=0&pl=…&ia=…` (`home.html:2120`), que dedupe o pixel com a CAPI pelo `eventID`. **O `sid` é o `public_token`, nunca o `asaas_payment_id`** (§13.6) — ele vai para a Meta no `fbq(… eventID: "purchase_" + sid)` de `home.html:2151`. 17) **`tests/frontend/precos_pix_anual.test.mjs`** (novo, §16.1).

---

## 15. Observabilidade

`log_system_event` em toda transição. `admin_notify._send` em: pago-depois-de-cancelado, estorno parcial, `attempts > 5` (inclusive o `stripe_cancel` que não entra), pago-não-concedido na reconciliação (**detector de fila pausada**, sem sonda separada), `paid_orphan`, `orphan_unknown` e `projecao_sem_grants`.

**Reparo manual do admin (nº 9):** `set_account_plan` **agora persiste**, porque escreve um grant `source='admin'` que a projeção respeita. É a ferramenta de conciliação de `paid_orphan`, `orphan_unknown` e pago-não-concedido, e **não pode ser desligada pelo PR que cria os casos que ela repara**.

---

## 16. Testes

`tests/test_billing_grants_projecao.py`, `tests/test_billing_grants_backfill.py`, `tests/test_pix_outbox.py`, `tests/test_billing_pix_asaas.py`. Infra do `tests/test_billing_webhook_lifecycle.py` (`TestClient`, banco de teste, `_FakeStripe`, `_FakeAsaas`; rede bloqueada no `conftest.py:214`). Projeção e `plano_da_cobranca` são puras.

**Todo evento de pagamento nos testes é `PAYMENT_RECEIVED`** — é o que o Pix produz. Os dois casos com `CONFIRMED` estão marcados como **robustez**, não como fluxo.

> Os casos abaixo são o contrato do PR 1a. **Corte por arquivo** (o Manager pediu): `tests/test_billing_grants_backfill.py` fica com 1–11 e 9f; `tests/test_billing_grants_projecao.py` fica com 12–24, 9a–9e, 9g, 9h e **9i–9o** (a matriz do §4.1.1 D). Um arquivo por assunto, nenhum arquivo com dois assuntos.

**Backfill inicial / deploy (§5)**
1. Assinante ativo preexistente **sem** grants: `recompute` **não escreve nada** e alerta; roda o resync; `recompute` → plano e data idênticos aos de `auth_accounts`. *Negativo: remova o resync → segunda metade vermelha.*
2. Resync 2× → **um** grant `legacy` por usuário.
3. `grandfathered` → nenhum grant, projeção não escreve.
4. **[SUBSTITUÍDO na v6 — ver 9c e 9f]** ~~Simula revert+re-aplicação: o resync atualiza o `ends_at`.~~
   **Este caso descrevia o contrato ANTIGO e foi anulado pela decisão do dono (§4.1.1, §5.1, §17).**
   O boot deixou de reparar (`not exists` + `on conflict do nothing`), então o resync **não** atualiza
   `ends_at` de linha existente — quem prova isso agora é o **9f** (revogado não é recriado em dois boots).
   E o negativo dele virou factualmente falso: com a regra da redução (§4.1.1 B), grant defasado
   **não rebaixa mais ninguém** — quem prova a reconciliação é o **9c** (varredura repara pelo Stripe).
   Mantido riscado, e não apagado, pelo mesmo motivo da bullet do §5.1: apagar deixa a próxima pessoa
   reintroduzir o `do update` achando que o caso 4 ainda o exigia.
5. **[REVISTO — nº 2]** Primeiro `invoice.paid` depois do resync: grant `stripe` criado **e** `legacy` revogado (`superseded_by_stripe`); cobertura contínua.
6. **[NOVO — nº 2]** `invoice.paid` com `period_end` **anterior** ao `ends_at` do `legacy` → `plan_expires_at` **encurta** para o do Stripe. *Negativo: remova a supersessão → o legado sustenta acesso além do que o Stripe diz.*
7. **[NOVO — nº 2]** Assinatura que lapsa (`payment_failed` → `past_due`, sem `deleted`) depois de um `invoice.paid`: quando o grant `stripe` vence, o acesso **acaba**.
8. **[NOVO — nº 4b]** Conta paga e vigente, zero grants: `recompute` **não escreve**, loga, alerta. *Negativo: deixe escrever `free` → rebaixamento destrutivo.*
9. **[NOVO]** Conta com grants **todos vencidos** → `recompute` por **evento** **escreve** `free` (a guarda do 8 não pode virar "nunca rebaixa").

**Materialização obrigatória e regra da redução (§4.1.1 — os 6 bloqueadores do dono)**

9a. **[BLOQUEADOR 1]** `upsert_grant` levantando no ramo pago → a resposta do webhook é **5xx** (nunca 2xx), a exceção **não é engolida**, e **nada** depois do grant rodou: `auth_accounts` intacto, zero e-mail, zero linha de funil, `plan_selected_at` intacto. *Negativo: reponha o `except/print` → resposta 200 e o defeito volta inteiro (grant defasado com `auth_accounts` fresco).*
9b. **[BLOQUEADOR 2]** Reentrega do **mesmo** evento depois do 5xx → conclui: **um** grant (`on conflict` + versão), **uma** linha de funil (unique `(session_id, kind)`), **um** e-mail (dedup do `_fire_email`), `plan_selected_at` preservado, e um `send_purchase`. *Negativo: tire a unique do funil **ou** o dedup do e-mail → duplicata por retry.*
9c. **[BLOQUEADOR 3]** **A classe "grant defasado", que é o caso que derrubava pagante:** grant `stripe` congelado no período anterior (`ends_at` no passado, `status='active'`), `auth_accounts` `pro / hoje+330d`, assinatura **viva** no Stripe. Roda a **varredura** → o Stripe é consultado, o grant é **reparado a partir dele**, e `auth_accounts` continua `pro / hoje+330d`. **Em nenhum instante a conta fica `free`.** *Negativo: tire a regra da redução (§4.1.1 B) → a varredura escreve `free/None`, que é exatamente a medição do Manager (`pro/2027-07-01 → free/None`).*
9d. **[BLOQUEADOR 3, variante]** Mesmo cenário com o **Stripe indisponível** → **não escreve nada**, loga `projecao_reducao_nao_confirmada`, alerta 1x/dia. *Negativo: deixe reduzir sem confirmação → pagante vira `free` num timeout de rede.*
9e. **[BLOQUEADOR 3, positivo]** Assinatura **de fato encerrada** (o Stripe não devolve assinatura ativa) → a varredura **reduz** para `free`. Sem ele, o grupo passaria num código que **nunca** rebaixa, que é pior que o bug.
9f. **[BLOQUEADOR 4]** Grant `legacy` **revogado** + `auth_accounts` pago e vigente (sustentado pelo grant `stripe`) → **dois boots seguidos** do `init_db` e o `legacy` **continua `revoked`**; nenhum grant é criado ou alterado. *Negativo: volte o `do update` do resync → o revogado ressuscita (D4).*
9g. **[BLOQUEADOR 5]** `checkout.session.completed` com `subscription` **string** e com `subscription` **objeto expandido** → **a mesma `external_ref`** e **um** grant (o segundo evento cai no `on conflict`, não cria linha nova). *Negativo: leia `_g(session,"subscription")` cru → o objeto expandido vira `external_ref` diferente e o usuário fica com dois grants da mesma assinatura.*
9h. **[BLOQUEADOR 6]** Usuário com **duas** assinaturas (a antiga e uma nova já paga); chega `subscription.deleted` **da antiga** → só o grant da antiga é revogado, o da nova continua `active`, e a projeção mantém o acesso. *Negativo: revogue por `source` sem `external_ref` → a assinatura nova morre junto e quem está em dia é rebaixado.*

**`past_due` e a matriz de vereditos (§4.1.1 C e D — um caso por célula que decide dinheiro)**

9i. **[B-5, dentro do teto]** Grant vencido há 3 dias, `auth_accounts` pago, Stripe devolve a assinatura **`past_due`** → veredito `carencia`: **nada é escrito**, a conta **mantém** plano e data, o evento `projecao_past_due_em_carencia` é logado e **nenhum alerta** sai. *Negativo: tire o ramo do `past_due` → a conta cai para `free` no primeiro dia de atraso, tirando o produto de quem o Stripe ainda está cobrando.*
9j. **[B-5, além do teto]** Mesmo cenário com o grant vencido há **16 dias** → veredito `reduz`: a conta vai para `free` **mesmo com a assinatura viva no Stripe**. *Negativo: remova o teto (`carencia` sempre) → a conta fica pendurada para sempre, que é a cauda infinita que o teto existe para cortar.*
9k. **[B-5 + célula 6, a que estava errada]** `past_due` além do teto e **nenhum grant ativo** (o `legacy` foi revogado por `superseded_by_stripe`) → **`reduz`**, com a `base` do teto vindo do `plan_expires_at` da conta. *Negativo: devolva `nao_reduz` quando não há alvo → o teto vira inalcançável justamente para quem já passou por um `invoice.paid`.*
9l. **[célula 10, a outra que estava errada]** Assinatura **`active`** no Stripe, `legacy` **revogado** e nenhum outro grant ativo → veredito `nao_reduz`: **não escreve**, alerta, e o `legacy` **continua `revoked`** (nem `status`, nem `revoked_reason`, nem `ends_at` mudam). *Negativo: deixe o reparo aceitar grant revogado como alvo → ele ressuscita (o `upsert_grant` grava `active` e zera `revoked_reason`) e o D4 volta pela porta do reparo.*
9m. **[célula 7, positivo do reparo]** Assinatura `active` com grant **ativo** daquela `sub_id` e `ends_at` menor → **`reparou`**: só o `ends_at` muda; `plan_stored` **idêntico** ao de antes. *Negativo: faça o reparo inferir o plano do price → um `change-plan` agendado concede tier que ninguém comprou.*
9n. **[célula 8]** Grant ativo já cobrindo o que o Stripe promete (`ends_at >= fim`) e ainda assim a projeção quis reduzir → `nao_reduz`, **não escreve**, alerta.
9o. **[célula 4]** Assinatura viva **sem `current_period_end` legível** → `indisponivel`: **não escreve** + alerta (nunca "não tem assinatura").
10. Vitalício sem data → fora do resync, projeção sai antes, acesso intacto, log de contagem. *Negativo: tire a segunda saída antecipada → vermelho.*
11. `subscription.deleted` revoga `stripe` **e** `legacy` → `free`.

**Ordem de eventos (§6)**
12. `deleted` (T) + `invoice.paid` (T−100) → grant continua revogado; `plan='free'`. *Negativo: remova o `where excluded.event_version > …` → vermelho.*
12b. **[NOVO — correção 1]** `invoice.paid` e `subscription.deleted` com **o mesmo `event["created"]`**, entregues nas **duas ordens** em execuções separadas → o estado final é `revoked` nas duas, `plan='free'` nas duas, e `last_event_id` aponta para o `deleted` nas duas. *Negativo: volte a guarda para só `>` → uma das ordens fica com acesso concedido, e o teste vira dependente da ordem de entrega.*
13. `invoice.paid` antigo não estica `ends_at` nem restaura tier.
14. **[NOVO — nº 2]** `invoice.paid` antigo (bloqueado pela versão) **não** supersede o `legacy` — `returning` vazio.
15. Evento mais novo legítimo (reativação) aplica.

**Projeção (§4)**
16. Grant futuro sem vigente → `free`. *Negativo: reponha `max(ends_at)` → vermelho.*
17. Buraco no meio corta a cobertura.
18. Positivo — renovação contígua ininterrupta.
19. Positivo — downgrade agendado: data do futuro, tier do vigente; após avançar o relógio, o tier vira.
20. **[PR 1b — REVISTO — nº 5, gatilho corrigido]** Migração + `deleted` → antecipação põe `starts_at = now()` **com `event_version` carimbada**. Em seguida, **três gatilhos alcançáveis no Pix**, um por vez: (a) **reentrega do mesmo `PAYMENT_RECEIVED`**, (b) `PAYMENT_REFUNDED` (que revoga, mas não pode mexer em `starts_at`), (c) reconciliação da varredura reescrevendo o grant. Nos três, `starts_at` **permanece** `now()`. *Negativo: tire o carimbo de `event_version` do UPDATE → (a) e (c) ficam vermelhos (o acesso cai pela segunda vez). O caminho CONFIRMED→RECEIVED da auditoria **não é testado**: o Pix não o produz.*
20b. **[PR 1b — NOVO — correção 2]** Usuário com **dois** grants Pix futuros (o da migração + um downgrade agendado) e `subscription.deleted` da assinatura vinculada ao primeiro → **só o primeiro** é antecipado; o segundo mantém `starts_at`. E `deleted` de uma assinatura **sem** cobrança vinculada → **nenhum** grant é antecipado. *Negativo: reponha o UPDATE amplo → o downgrade agendado é puxado para hoje.*
21. Positivo — tolerância: 1 s emenda, 10 min não. *Negativo: zere `GAP_TOLERANCIA` → o de 1 s fica vermelho.*
22. Positivo — sobreposição: cobertura até o maior `ends_at`, tier maior entre os vigentes.
23. Latência (§4.3): grant que começou há 90 s entra na passada de 60 s.
24. Positivo de não-regressão: Stripe puro com renovações sucessivas → `plan_expires_at` igual ao `current_period_end`; `deleted` sem Pix → `free`, como hoje.

**Outbox, roteamento e efeitos (§8)**
25. Token inválido → **401**; sem token → **503**; corpo sem id → **400**; nenhuma linha na outbox.
26. Falha do banco no insert → **5xx**.
27. Evento novo → 200 + `processed_at is null`; **reentrega do mesmo `event_id`** → 200 + uma linha, e **zero** efeitos novos.
28. **[NOVO — nº 8a, o caminho real]** `PAYMENT_REFUNDED` e `PAYMENT_OVERDUE` de um pagamento já pago → **nenhum** `send_purchase`, **nenhum** Purchase de CAPI, **nenhum** e-mail de compra, **nenhum** `Subscription.modify`; o `REFUNDED` executa **só** `revoke`. *Negativo: remova o `EFEITOS_POR_EVENTO` (laço fixo da v4) → vermelho, com receita de estorno indo ao GA4.*
29. **[NOVO — nº 8b]** Dois eventos **distintos** do mesmo `payment.id` que autorizariam efeitos (ex.: `RECEIVED` reentregue com `event_id` novo pela plataforma) → efeitos rodam **uma vez**. *Negativo: volte a chave para `event_id` → efeitos duplicados.* **[robustez]** variante com `CONFIRMED` + `RECEIVED`, marcada como caminho que o Pix não produz.
30. **[REVISTO — nº 3]** `stripe_cancel` **falha** e o dreno sai: **nenhum grant criado**; ao passar, `stripe_period_end_at` está gravado e o grant nasce com **aquele** valor. *Negativo: reponha a ordem `[grant, stripe_cancel, …]` → grant existe com a assinatura ainda renovando.*
31. **[NOVO — nº 3]** `stripe_cancel` falhando **6 vezes**, em duas variantes que diferem só pela coluna:
    - **(a) `stripe_period_end_at` PREENCHIDA** (estimativa gravada na criação) → `admin_notify` **e** grant com `starts_at = max(now, stripe_period_end_at)`; cobrança marcada para a varredura.
    - **(b) `stripe_period_end_at` NULL** — o Stripe estava fora **desde o checkout**, que é o cenário que produz as 6 falhas → `admin_notify` **e** grant com `starts_at = now()`, **sem exceção** e com `starts_at` não nulo. *Negativo: tire o `coalesce` → `TypeError`/violação de `not null`, e o fallback estoura na hora exata em que é acionado.*
31b. **[NOVO — B]** Compra Pix **comum** (`stripe_subscription_id is null`): o efeito `stripe_cancel` é **no-op registrado** — nenhuma chamada ao Stripe, `attempts` **permanece 0**, nenhum alerta, o par fica em `pix_payment_effects` e os efeitos seguintes rodam normalmente. *Negativo: trate o no-op como falha → `attempts` sobe e toda venda normal dispara o alerta de `attempts > 5`.*
32. Queda entre `ga4` e `capi` → nova passada manda só a CAPI; **um** `send_purchase`.
33. **[REVISTO]** Queda entre `email` e o registro → segundo e-mail é **possível** (declarado) e o `recent_event_exists` o suprime em 1 dia; queda **antes** do envio → o e-mail **sai** na passada seguinte.
34. `attempts > 5` → `admin_notify`.

**Saga (§10)**
35. Morte em `creating` → varredura acha por `externalReference` e vira `pending`, sem segunda cobrança.
36. Morte em `creating` sem cobrança remota → volta a `draft` e é apagada.
37. **[REVISTO — nº 6]** Substituição com `DELETE` remoto **falhando** → **503**, **nenhuma** cobrança nova, a antiga fica `canceling`, varredura reconcilia. *Negativo: reponha a ordem da v4 → duas cobranças ativas do mesmo usuário.*
38. **[NOVO — nº 6, o financeiro]** Substituição Essencial→Pro com crédito: **exatamente uma** cobrança com `credit_cents > 0`; a soma dos créditos concedidos **nunca excede** o disponível medido antes. *Negativo: permita as duas vivas → crédito em dobro.*
39. **[NOVO — nº 6]** Cobrança `canceled` paga mesmo assim → **um** grant, plano e centavos conferidos contra o §7 no instante do pagamento, `admin_notify`.
36b. **[NOVO — correção 3]** Expurgo só por confirmação: (a) `draft` com 2 h e o Asaas devolvendo **lista vazia** → apagada; (b) mesma idade e o Asaas devolvendo **a cobrança** → `attach` + `pending`, **não** apagada; (c) mesma idade e a consulta ao Asaas **falhando** → linha **intacta**, tentada de novo na passada seguinte; (d) `creating` **nunca** é apagada por idade; (e) `canceling` sem confirmação do `DELETE` **não** vira `canceled`. *Negativo: reponha "apaga `draft` com mais de 15 min" → (b) e (c) ficam vermelhos, e (b) é dinheiro sem linha.*
40. Duas chamadas concorrentes → uma cobrança.

**Pagamento tardio e desconhecido (§10/§11)**
41. **[NOVO — nº 7]** `expired` com 30 dias: `RECEIVED` → **concede**. Com 70 dias: já cancelada no Asaas aos 60 e a linha ainda existe (90 > 60).
42. **[REVISTO — nº 7 + correção 5]** Evento de pagamento sem cobrança correspondente, três variantes: (a) `externalReference = "pix:99999"` (nosso formato, linha inexistente) → **`orphan_unknown` + `admin_notify`**, 200; (b) `externalReference` de terceiro (`"boleto-loja-42"`, vazio, ou ausente) → **200, nenhuma linha, nenhum alerta**, só `log "asaas_evento_fora_do_escopo"`; (c) `"pix:abc"` (prefixo certo, formato errado) → tratado como (b). *Negativo 1: remova a célula do (a) → dinheiro nosso descartado em silêncio. Negativo 2: aceite qualquer `externalReference` → o Pix avulso do negócio vira alerta e fila de conciliação sobre dinheiro de terceiros.*
42b. **[NOVO — correção 4]** `GET /billing/pix/{public_token}` responde; `GET /billing/pix/{asaas_payment_id}` → **404**. E o `qr_payload_enc` é ilegível sem a chave e fica **nulo** depois de `paid`/`canceled`/`expired`. *Negativo: guarde o QR em texto puro → o teste lê o instrumento de pagamento direto da linha.*

**Retenção e privacidade (§13)**
43. `delete_user_data` de quem tem cobrança → linha sobrevive com `user_id is null`, rastreio/QR/customer nulos, valores e ids preservados; exclusão conclui **sem `leftovers`**. *Negativo: FK `cascade` → vermelho.*
44. Varredura re-pseudonimiza a linha que perdeu a corrida do UPDATE.
45. **[REVISTO]** `RETENCAO_TENTATIVA_DIAS` expira `draft`/`expired` antigas **e nunca apaga cobrança ainda pagável**; `RETENCAO_PAGAMENTO_DIAS=None` não apaga nenhuma paga. *Negativo: baixe a retenção para 30 dias → o caso 41 fica vermelho.*
46. **[REVISTO — nº 10]** Outbox: `payload_enc` sem campos fora da lista minimizada e ilegível sem a chave; **evento travado tem o payload anulado aos 7 dias** e a linha permanece com `event_id`/`event_type`/`last_error`; dreno com `payload_enc is null` marca `processed_at` e sai. *Negativo: conte a purga de `processed_at` → PII de conta excluída retida indefinidamente.*
47. **[REVISTO — nº 1]** `paid_orphan`: webhook de cobrança de conta excluída → **200**, status `paid_orphan`, **nenhum grant** (nem violação de NOT NULL), `orphan_notified` registrado, alerta, linha na conciliação. *Negativo: tire a guarda e deixe o efeito `grant` rodar → estoura NOT NULL, a cobrança fica `pending` para sempre e o alerta nunca sai.*
48. **[NOVO — nº 1]** Cobrança `pending` de conta excluída casa com **uma** linha da tabela de estados; o destino vem da guarda. Sem ambiguidade.

**Reparo manual (§15)**
49. **[NOVO — nº 9]** `set_account_plan` concede Pro → grant `admin` criado; a projeção seguinte **preserva**. *Negativo: volte a escrever só `auth_accounts` → o ajuste some na primeira reprojeção.*
50. **[NOVO]** `set_account_plan` para `free` → todos os grants ativos revogados (`admin_override`), projeção → `free`.

**Preço e venda**
51. Upgrade Pix→Pix: cobra `preço − crédito`, `starts=now`, `expires=now+365`.
52. Upgrade a partir do Stripe: preço cheio, crédito 0, `starts` no fim do período.
53. `preço − crédito < mínimo` → **agenda** pelo preço cheio; nunca cobra o mínimo.
54. `ASAAS_MIN_CHARGE_CENTS` ausente → venda **503**.
55. Price anual não configurado → **503** antes de qualquer escrita ou chamada; nenhum usuário virando `'pro'`.
56. Flag `ASAAS_PIX_ANNUAL_ENABLED=0` + evento de cobrança já emitida → **acesso concedido**.

### 16.1 Frontend do PR 2 — `tests/frontend/precos_pix_anual.test.mjs` (correção 6)

"Sem teste automatizado" estava errado: o repo tem `npm run test:frontend` (`node --test tests/frontend/*.test.mjs`) e 21 arquivos na pasta. Arquivo novo, no estilo do `precos_sem_plano_gratis.test.mjs` — `startServer()` de `./_server.mjs` (porta efêmera), `chromium` do Playwright, backend inteiro por `page.route`, e contagem **por interceptação**, não por efeito visível.

```
PT1. Ciclo MENSAL: nenhum [data-pix-cta] no DOM.  →  setCycle("annual"): os 3 aparecem.
     →  setCycle("monthly") de novo: somem. (o bug barato é aparecer e não sumir na volta)
PT2. Poll com TETO: /billing/pix/checkout devolve o token; /billing/pix/{token} responde
     sempre {status:"pending"}. Com o relógio adiantado além do teto, a contagem de
     interceptações PARA de crescer e a UI mostra o estado de expirado.
PT3. Modal de migração (409 stripe_active): o texto contém o aviso de NÃO cancelar pelo
     painel do Stripe, e a data do current_period_end aparece formatada.
PT4. refreshPlanButtons com /billing/subscription = {active:true, gateway:"pix", plan:"plus"}:
     o card do plano atual diz "Renovar no Pix"; os outros dizem "Pagar no Pix"; nenhum
     botão chama /billing/change-plan (contador em ZERO).
PT5. POSITIVO: "Assinar Plus" no ciclo ANUAL continua disparando EXATAMENTE 1
     POST /billing/create-checkout — o caminho do cartão não foi quebrado pelo Pix.
PT6. O sid do redirect de sucesso é o public_token que o checkout devolveu, e NÃO
     contém "pay_" nem o asaas_payment_id (§13.6).
```

Controles do grupo: **negativo** — tire a condição de ciclo do render do botão e PT1 fica vermelho; tire o teto do poll e PT2 fica vermelho. **positivo** — PT5, que reprova a página com todos os botões quebrados.

**Só a leitura física do QR no aparelho continua manual** (§18): o navegador headless não lê câmera, e dentro do app o aparelho não se escaneia — é por isso que "Copiar código Pix" é a ação primária.

---

## 17. Corte em PRs e reversão

| PR | entrega | prova | como reverter sozinho |
|---|---|---|---|
| **1a — grants, projeção, resync, grant do admin** (nenhuma linha de Asaas) | modelo de escrita novo com comportamento observável idêntico ao de hoje, base pagante migrada, reparo manual preservado | casos 1–24, **9a–9h** (os seis bloqueadores do dono) e **9i–9o** (matriz §4.1.1 D + `past_due`), 49–50 (**20 e 20b são 1b**, §4.4) | `git revert`. `plan_grants` fica no banco, inerte. **Seguro por três propriedades, e a (b) MUDOU na v6:** (a) a projeção **nunca rebaixa por ausência de grant** (§4.1) — o código novo não destrói os valores de que o código antigo depende; (b) ~~o resync reconcilia sozinho no boot~~ **não vale mais**: o backfill virou inserção única e **não repara** (§5.1). A reconciliação depois de um revert passa a ter **dois caminhos, os dois automáticos**: o próximo evento do Stripe daquela assinatura regrava o grant (e agora **tem** de regravar, §4.1.1 A), e a **varredura confirma contra o Stripe antes de qualquer redução** (§4.1.1 B) — então, na janela em que os grants ficaram congelados, ninguém é rebaixado; (c) o modelo de **leitura** nunca muda de formato, então o código antigo volta a operar sobre `auth_accounts` como sempre. **O que se perdeu**: a reconciliação deixou de acontecer no instante do boot; ela acontece no primeiro evento ou na primeira varredura. **O que se ganhou**: o boot parou de poder escrever grant a partir da projeção, que era a origem do D4 |
| **1b — Asaas, outbox, saga** (flag **off**) | fluxo Pix inteiro no backend, sem venda ligada | casos 25–48, 51–56 | nesta ordem: **(1)** `ASAAS_PIX_ANNUAL_ENABLED=0` — para venda nova **sem deploy**; **(2)** para reverter o código, **desativar antes o webhook no painel do Asaas** — a rota some, o Asaas toma 404 e **15 falhas seguidas pausam a fila**; **(3)** cancelar no painel as cobranças emitidas e não pagas |
| **2 — frontend** | a venda visível na /precos | só depois do deploy e no aparelho | reverter o HTML; o backend continua aceitando webhook de cobrança já emitida |

**Fronteira de rollback segura é o 1a**, e é onde mora o risco do §5 — por isso ele sobe sozinho.

---

## 18. Não verificável aqui, e o que falta decidir

**Não se prova neste ambiente:** chamada real ao Asaas (customer com `cpfCnpj`, cobrança, `pixQrCode`, `DELETE`, busca por `externalReference`), o header real do webhook, a pausa da fila após 15 falhas, pagamento Pix e pagamento tardio reais, cancelamento manual no painel do Stripe, o GA4 ter aceitado o `purchase` (o endpoint responde 204 para quase tudo, `ga4_mp.py:147`), a **leitura física do QR** no aparelho (o headless não lê câmera, e dentro do app o aparelho não se escaneia). A lógica da `precos.html` **passa a ser testada** no `precos_pix_anual.test.mjs` (§16.1); o que sobra de manual ali é só o que precisa de câmera e de deploy.

**Fato que eu NÃO verifiquei e que veio do coordenador** (doc oficial): ids de evento próprios, `payment.id` estável, e o fluxo Pix `CREATED → RECEIVED` sem `CONFIRMED`. O desenho não depende de o `CONFIRMED` existir; se a plataforma passar a emiti-lo, a coluna fundida do §11 e o caso 29 de robustez já cobrem.

**Pendências do dono, todas bloqueantes do 1b (nenhuma bloqueia o 1a):**
1. **`ASAAS_MIN_CHARGE_CENTS`** medido no Sandbox — sem a env a venda fica 503.
2. **`RETENCAO_PAGAMENTO_DIAS`** validado com contador/jurídico — enquanto for `None`, nada de pagamento é apagado e a política diz "pelo prazo exigido pela legislação aplicável", sem número.
3. **Tarifa efetiva por cobrança** — R$ 0,00 no Pix dinâmico é **condição atual da conta**, registrada como nota operacional; nenhuma linha de lógica a assume.
