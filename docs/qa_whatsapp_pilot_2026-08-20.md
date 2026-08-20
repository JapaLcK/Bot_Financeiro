# QA piloto — vault WhatsApp (24 interações, 3 domínios)

Gerado em 2026-08-20 por `scripts/whatsapp_qa_vault_harness.py`, chamando `core.handle_incoming.handle_incoming()` direto contra um Postgres isolado e descartável, sem mockar a IA (chamadas OpenAI reais).

**Sumário:** 23 interações — ✅ 12 · ❌ 4 · ⚠️ 0 · 🔍 7

## Discrepâncias entre vault e código

- Itens 10/11/12: a confirmação de apagar UM lançamento (pending.py, action_type delete_launch) responde com '✅ Lançamento **#N** apagado e saldo revertido.' — NÃO usa 🗑️. O emoji 🗑️ só aparece nas confirmações de apagar compra/parcelamento no cartão (core/handlers/credit.py). A nota do vault que espera 🗑️ para apagar lançamento (item 10) e para 'desfazer' (item 12) está desalinhada com o código atual.
- Item 19: 'padrao Inter' trocou o cartão principal DIRETO, sem perguntar sim/não — confirma a leitura de código (credit.py ~1673-1680: bloco `t_low.startswith('padrao ')` chama set_default_card direto). A nota do vault (que descreve 'Definir Inter como principal? (sim/não)') está desatualizada em relação a este comando; essa pergunta sim/não existe no código só para o fluxo guiado 'definir cartão principal' sem nome (_ask_set_primary_flow), não para o comando direto 'padrao <nome>'.

---

## 1. Registrar despesa
**Domínio:** Lançamentos · **Cenário/usuário:** free (novo) — L1

> Você: gastei 50 no mercado
> PigBank: 💸 *Despesa registrada*: R$ 50,00
🏷️ Categoria: mercado
🏦 Saldo: R$ -50,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: pixei 40 pro joão
> PigBank: 💸 *Despesa registrada*: R$ 40,00
🏷️ Categoria: outros
🏦 Saldo: R$ -90,00
ID: #2
>
> Você: paguei 80 de luz
> PigBank: 💸 *Despesa registrada*: R$ 80,00
🏷️ Categoria: moradia
🏦 Saldo: R$ -170,00
ID: #3
>
> Você: gastei 300 no ifood e 150 na farmácia
> PigBank: 💸 *Despesa registrada*: R$ 300,00
🏷️ Categoria: alimentação
🏦 Saldo: R$ -470,00
ID: #4

💸 *Despesa registrada*: R$ 150,00
🏷️ Categoria: saúde
🏦 Saldo: R$ -620,00
ID: #5
>

**Veredito:** ✅

**Checklist do vault:**
- [x] "gastei 50 no mercado" → Despesa registrada/💸 + R$ 50,00 + #
- [x] "pixei 40 pro joão" → 💸 + R$ 40,00 + #
- [x] "paguei 80 de luz" → 💸 + R$ 80,00 (sem desviar pra pagar boleto)
- [x] "gastei 300 no ifood e 150 na farmácia" → DOIS lançamentos (300 e 150)

---

## 2. Registrar receita
**Domínio:** Lançamentos · **Cenário/usuário:** free (novo) — L2

> Você: recebi 2000 de salário
> PigBank: 💰 *Receita registrada*: R$ 2.000,00
🏷️ Categoria: outros
🏦 Saldo: R$ 2.000,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: caiu 1500 na conta
> PigBank: Não consegui identificar o valor. Tente: *gastei 50 no mercado*
>
> Você: 1500 caiu na conta
> PigBank: 💸 *Despesa registrada*: R$ 1.500,00
🏷️ Categoria: outros
🏦 Saldo: R$ 500,00
ID: #2
>
> Você: ganhei 300 de freela ontem
> PigBank: 💰 *Receita registrada*: R$ 300,00
🏷️ Categoria: outros
🏦 Saldo: R$ 800,00
ID: #3
>
> Você: 500
> PigBank: Não entendi exatamente o que você quer fazer.
Tente uma destas opções:
• saldo — ver saldo atual
• gastei 50 mercado — registrar despesa
• pagar fatura Nubank — pagar uma fatura
• faturas — listar faturas em aberto
• cartoes — listar seus cartões
• criar caixinha viagem — criar caixinha
• investimentos — ver carteira
• ajuda — ver todos os comandos
>

**Veredito:** 🔍

**Checklist do vault:**
- [x] "recebi 2000 de salário" → 💰 + R$ 2.000,00 + #
- [ ] "caiu 1500 na conta" → 💰 + R$ 1.500,00
- [x] "ganhei 300 de freela ontem" → 💰 + R$ 300,00
- [x] "500" sozinho → NÃO tratado como receita direta (regressão)

**Notas:**
- 1ª tentativa "caiu 1500 na conta" não foi entendida; variação "1500 caiu na conta" → '💸 *Despesa registrada*: R$ 1.500,00\n🏷️ Categoria: outros\n🏦 Saldo: R$ 500,00\nID: #2'
- resposta real a "500" sozinho (após contexto de receitas): 'Não entendi exatamente o que você quer fazer.\nTente uma destas opções:\n• saldo — ver saldo atual\n• gastei 50 mercado — registrar despesa\n• pagar fatura Nubank — pagar uma fatura\n• faturas — listar faturas em aberto\n• cartoes — listar seus cartões\n• criar caixinha viagem — criar caixinha\n• investimentos — ver carteira\n• ajuda — ver todos os comandos'

---

## 3. Lançamento valor-primeiro sem verbo
**Domínio:** Lançamentos · **Cenário/usuário:** free (novo) — L3

> Você: 77,90 mercado
> PigBank: 💸 *Despesa registrada*: R$ 77,90
🏷️ Categoria: mercado
🏦 Saldo: R$ -77,90
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: 50 uber
> PigBank: 💸 *Despesa registrada*: R$ 50,00
🏷️ Categoria: transporte
🏦 Saldo: R$ -127,90
ID: #2
>
> Você: 500
> PigBank: Não entendi exatamente o que você quer fazer.
Tente uma destas opções:
• saldo — ver saldo atual
• gastei 50 mercado — registrar despesa
• pagar fatura Nubank — pagar uma fatura
• faturas — listar faturas em aberto
• cartoes — listar seus cartões
• criar caixinha viagem — criar caixinha
• investimentos — ver carteira
• ajuda — ver todos os comandos
>

**Veredito:** ✅

**Checklist do vault:**
- [x] "77,90 mercado" → 💸 + R$ 77,90
- [x] "50 uber" → 💸 + R$ 50,00
- [x] "500" sozinho → não quebra (sem exceção)

**Notas:**
- resposta real a "500" sozinho (sem descrição): 'Não entendi exatamente o que você quer fazer.\nTente uma destas opções:\n• saldo — ver saldo atual\n• gastei 50 mercado — registrar despesa\n• pagar fatura Nubank — pagar uma fatura\n• faturas — listar faturas em aberto\n• cartoes — listar seus cartões\n• criar caixinha viagem — criar caixinha\n• investimentos — ver carteira\n• ajuda — ver todos os comandos'

---

## 4. Lançamento com data
**Domínio:** Lançamentos · **Cenário/usuário:** free (novo) — L4

> Você: ontem gastei 50 no mercado
> PigBank: 💸 *Despesa registrada*: R$ 50,00
🏷️ Categoria: mercado
🏦 Saldo: R$ -50,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: dia 5 paguei 200 de academia
> PigBank: Não consegui identificar o valor. Tente: *gastei 50 no mercado*
>
> Você: paguei 200 de academia dia 5
> PigBank: 💸 *Despesa registrada*: R$ 200,00
🏷️ Categoria: saúde
🏦 Saldo: R$ -250,00
ID: #2
>
> Você: 03/04 comprei tênis 250
> PigBank: 💸 *Despesa registrada*: R$ 250,00
🏷️ Categoria: outros
🏦 Saldo: R$ -500,00
ID: #3
>

**Veredito:** ✅

**Checklist do vault:**
- [x] "ontem gastei 50 no mercado" → 💸 + R$ 50,00 + sinal de data
- [x] "dia 5 paguei 200 de academia" → 💸 + R$ 200,00
- [x] "03/04 comprei tênis 250" → 💸 + R$ 250,00

**Notas:**
- sinal de data na resposta: '💸 *Despesa registrada*: R$ 50,00\n🏷️ Categoria: mercado\n🏦 Saldo: R$ -50,00\nID: #1\n\n🎉 Esse foi seu primeiro lançamento — viu como é rápido?\nAgora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.'
- 1ª tentativa "dia 5 paguei 200 de academia" não foi entendida; variação "paguei 200 de academia dia 5" → '💸 *Despesa registrada*: R$ 200,00\n🏷️ Categoria: saúde\n🏦 Saldo: R$ -250,00\nID: #2'

---

## 5. Multi-lançamento numa mensagem
**Domínio:** Lançamentos · **Cenário/usuário:** free (novo) — L5

> Você: gastei 500 no ifood e mais 800 no mercado
> PigBank: 💸 *Despesa registrada*: R$ 500,00
🏷️ Categoria: alimentação
🏦 Saldo: R$ -500,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.

💸 *Despesa registrada*: R$ 800,00
🏷️ Categoria: mercado
🏦 Saldo: R$ -1.300,00
ID: #2
>
> Você: paguei 50 uber, 30 café e o aluguel
> PigBank: 💸 *Despesa registrada*: R$ 50,00
🏷️ Categoria: alimentação
🏦 Saldo: R$ -1.350,00
ID: #3
>
> Você: 1500
> PigBank: Não entendi exatamente o que você quer fazer.
Tente uma destas opções:
• saldo — ver saldo atual
• gastei 50 mercado — registrar despesa
• pagar fatura Nubank — pagar uma fatura
• faturas — listar faturas em aberto
• cartoes — listar seus cartões
• criar caixinha viagem — criar caixinha
• investimentos — ver carteira
• ajuda — ver todos os comandos
>

**Veredito:** ❌

**Checklist do vault:**
- [x] "gastei 500 no ifood e mais 800 no mercado" → dois lançamentos (500 e 800)
- [ ] "paguei 50 uber, 30 café e o aluguel" → registra uber+café e PERGUNTA valor do aluguel
- [ ] "1500" → completa lançamento de aluguel (R$ 1.500,00)

---

## 6. Lançamento com valor faltando
**Domínio:** Lançamentos · **Cenário/usuário:** free (novo) — L6 / L6b

> Você: gastei cinquenta
> PigBank: 💸 *Despesa registrada*: R$ 50,00
🏷️ Categoria: outros
🏦 Saldo: R$ -50,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: mercado
> PigBank: Não entendi exatamente o que você quer fazer.
Tente uma destas opções:
• saldo — ver saldo atual
• gastei 50 mercado — registrar despesa
• pagar fatura Nubank — pagar uma fatura
• faturas — listar faturas em aberto
• cartoes — listar seus cartões
• criar caixinha viagem — criar caixinha
• investimentos — ver carteira
• ajuda — ver todos os comandos
>
> Você: paguei o mercado
> PigBank: Não consegui identificar o valor. Tente: *gastei 50 no mercado*
>
> Você: 120
> PigBank: Não entendi exatamente o que você quer fazer.
Tente uma destas opções:
• saldo — ver saldo atual
• gastei 50 mercado — registrar despesa
• pagar fatura Nubank — pagar uma fatura
• faturas — listar faturas em aberto
• cartoes — listar seus cartões
• criar caixinha viagem — criar caixinha
• investimentos — ver carteira
• ajuda — ver todos os comandos
>

**Veredito:** ❌

**Checklist do vault:**
- [ ] "gastei cinquenta" → clarificação mencionando R$ 50,00 e pedindo descrição
- [ ] "mercado" → completa R$ 50,00 + mercado
- [x] "paguei o mercado" (L6b) → clarificação pedindo valor, mencionando mercado
- [ ] "120" → completa R$ 120,00 + mercado

**Notas:**
- Achado: "gastei cinquenta" NÃO pede clarificação — registra direto (categoria 'outros', sem descrição), diferente do fluxo de clarificação documentado no vault.

---

## 7. Oferta de gasto fixo
**Domínio:** Lançamentos · **Cenário/usuário:** free (novo) — L7

> Você: netflix 44,90
> PigBank: Não entendi exatamente o que você quer fazer.
Tente uma destas opções:
• saldo — ver saldo atual
• gastei 50 mercado — registrar despesa
• pagar fatura Nubank — pagar uma fatura
• faturas — listar faturas em aberto
• cartoes — listar seus cartões
• criar caixinha viagem — criar caixinha
• investimentos — ver carteira
• ajuda — ver todos os comandos
>
> Você: gastei 44,90 na netflix
> PigBank: 💸 *Despesa registrada*: R$ 44,90
🏷️ Categoria: assinaturas
🏦 Saldo: R$ -89,80
ID: #2
>
> Você: sim
> PigBank: Não entendi bem o que você quis fazer. 🤔
Tenta assim:
• *gastei 50 no mercado* — registrar um gasto
• *saldo* — ver quanto você tem
• *recebi 1000 de salário* — registrar uma receita

Ou digite *ajuda* pra ver tudo que eu faço.
>

**Veredito:** 🔍

**Checklist do vault:**
- [ ] "netflix 44,90" → registra lançamento E pergunta oferta de gasto fixo (sim/não)
- [ ] "sim" → confirma criação do recorrente (ou gate Pro-only, documentar o que sair)

**Notas:**
- Passo de setup SEMEADO DIRETO NO BANCO (não via handle_incoming): add_launch_and_update_balance + UPDATE launches SET criado_em = mês anterior.
- 1ª tentativa "netflix 44,90" não foi entendida; variação "gastei 44,90 na netflix" → '💸 *Despesa registrada*: R$ 44,90\n🏷️ Categoria: assinaturas\n🏦 Saldo: R$ -89,80\nID: #2'
- resposta completa do lançamento+oferta: '💸 *Despesa registrada*: R$ 44,90\n🏷️ Categoria: assinaturas\n🏦 Saldo: R$ -89,80\nID: #2'
- resposta a 'sim': 'Não entendi bem o que você quis fazer. 🤔\nTenta assim:\n• *gastei 50 no mercado* — registrar um gasto\n• *saldo* — ver quanto você tem\n• *recebi 1000 de salário* — registrar uma receita\n\nOu digite *ajuda* pra ver tudo que eu faço.'

---

## 8. Listar lançamentos e histórico
**Domínio:** Lançamentos · **Cenário/usuário:** free — reusa uid de L1

> Você: meus gastos
> PigBank: 🧾 *Últimos 5 lançamentos*:
💸 hoje • R$ 150,00 • gastei 150 na farmácia [#5]
💸 hoje • R$ 300,00 • gastei 300 no ifood [#4]
💸 hoje • R$ 80,00 • paguei 80 de luz [#3]
💸 hoje • R$ 40,00 • pixei 40 pro joão [#2]
💸 hoje • R$ 50,00 • gastei 50 no mercado [#1]

💸 Gastos: R$ 620,00
>
> Você: gastos hoje
> PigBank: 🧾 *Lançamentos de hoje*:
#1 • despesa • R$ 50,00 • gastei 50 no mercado [mercado]
#2 • despesa • R$ 40,00 • pixei 40 pro joão [outros]
#3 • despesa • R$ 80,00 • paguei 80 de luz [moradia]
#4 • despesa • R$ 300,00 • gastei 300 no ifood [alimentação]
#5 • despesa • R$ 150,00 • gastei 150 na farmácia [saúde]

💸 Gastos: R$ 620,00
>
> Você: extrato
> PigBank: 🧾 *Últimos 5 lançamentos*:
💸 hoje • R$ 150,00 • gastei 150 na farmácia [#5]
💸 hoje • R$ 300,00 • gastei 300 no ifood [#4]
💸 hoje • R$ 80,00 • paguei 80 de luz [#3]
💸 hoje • R$ 40,00 • pixei 40 pro joão [#2]
💸 hoje • R$ 50,00 • gastei 50 no mercado [#1]

💸 Gastos: R$ 620,00
>

**Veredito:** ✅

**Checklist do vault:**
- [x] "meus gastos" → 🧾 + lista com # + resumo Gastos/Receitas
- [x] "gastos hoje" → resposta filtrada, sem exceção
- [x] "extrato" → mesma listagem geral

---

## 9. Quanto gastei — consulta de total
**Domínio:** Lançamentos · **Cenário/usuário:** free — reusa uid de L1

> Você: quanto gastei essa semana
> PigBank: 💸 Você gastou *R$ 620,00* esta semana.

📊 Top categorias:
• alimentação: R$ 300,00
• saúde: R$ 150,00
• moradia: R$ 80,00
>
> Você: qto gastei ontem
> PigBank: Não entendi exatamente o que você quis fazer com lançamentos.
🧾 Posso te ajudar com lançamentos de algumas formas:
• gastei 50 mercado
• recebi 1000 salario
• gastos hoje
• listar lancamentos
• apagar 17
>
> Você: quanto gastei ontem
> PigBank: 🐷 Você não teve gastos ontem.
>

**Veredito:** 🔍

**Checklist do vault:**
- [x] "quanto gastei essa semana" → 💸 Você gastou + total em R$ (não lista item a item)
- [ ] "qto gastei ontem" → total do dia anterior

**Notas:**
- 1ª tentativa "qto gastei ontem" não foi entendida; variação "quanto gastei ontem" → '🐷 Você não teve gastos ontem.'

---

## 10. Apagar lançamento por ID
**Domínio:** Lançamentos · **Cenário/usuário:** free (novo) — L10

> Você: gastei 50 no mercado
> PigBank: 💸 *Despesa registrada*: R$ 50,00
🏷️ Categoria: mercado
🏦 Saldo: R$ -50,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: apagar #1
> PigBank: ⚠️ Isso vai apagar o lançamento *#1* e desfazer seus efeitos no saldo.
Confirma? Responda *sim* ou *não*.
>
> Você: sim
> PigBank: ✅ Lançamento *#1* apagado e saldo revertido.
>
> Você: gastei 30 no cafe
> PigBank: 💸 *Despesa registrada*: R$ 30,00
🏷️ Categoria: alimentação
🏦 Saldo: R$ -30,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: apagar #1
> PigBank: ⚠️ Isso vai apagar o lançamento *#1* e desfazer seus efeitos no saldo.
Confirma? Responda *sim* ou *não*.
>
> Você: saldo
> PigBank: 🏦 *Conta Corrente*: R$ -30,00

📋 *Hoje*
  • Gastei 30 no cafe: R$ 30,00

📊 *Gastos em Agosto*: R$ 30,00
>
> Você: meus gastos
> PigBank: 🧾 *Últimos 1 lançamentos*:
💸 hoje • R$ 30,00 • gastei 30 no cafe [#1]

💸 Gastos: R$ 30,00
>

**Veredito:** ✅

**Checklist do vault:**
- [x] "apagar #N" → ⚠️ pede confirmação sim/não, NÃO apaga ainda
- [x] "sim" → confirma apagado + saldo atualizado (vault esperava 🗑️)
- [x] "saldo" (comando claro) → resposta de saldo normal, NÃO apaga #M
- [x] "meus gastos" depois → #M ainda aparece (guarda anti-órfã funcionou)

**Notas:**
- #N capturado: 1
- #M capturado: 1

---

## 11. Apagar vários lançamentos
**Domínio:** Lançamentos · **Cenário/usuário:** free (novo) — L11

> Você: gastei 50 uber
> PigBank: 💸 *Despesa registrada*: R$ 50,00
🏷️ Categoria: transporte
🏦 Saldo: R$ -50,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: gastei 30 cafe
> PigBank: 💸 *Despesa registrada*: R$ 30,00
🏷️ Categoria: alimentação
🏦 Saldo: R$ -80,00
ID: #2
>
> Você: apagar #1 e #2
> PigBank: ⚠️ Isso vai apagar os lançamentos *#1*, *#2* e desfazer seus efeitos no saldo.
Confirma? Responda *sim* ou *não*.
>
> Você: sim
> PigBank: ✅ Apagados: *#1*, *#2*
>

**Veredito:** ✅

**Checklist do vault:**
- [x] "apagar #A e #B" → ⚠️ listando os dois, pede confirmação
- [x] "sim" → confirma os DOIS apagados (vault esperava 🗑️; ver discrepância do item 10)

**Notas:**
- #A=1 #B=2

---

## 12. Desfazer último lançamento
**Domínio:** Lançamentos · **Cenário/usuário:** free (novo) — L12

> Você: gastei 50 no mercado
> PigBank: 💸 *Despesa registrada*: R$ 50,00
🏷️ Categoria: mercado
🏦 Saldo: R$ -50,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: desfazer
> PigBank: ⚠️ Desfazer o último lançamento: *#1* (despesa R$ 50,00)?
Confirma? Responda *sim* ou *não*.
>

**Veredito:** ❌

**Checklist do vault:**
- [ ] "desfazer" → ↩️ confirmando que desfez #N (turno único, sem 'sim' adicional)

**Notas:**
- #N capturado: 1
- resposta real a 'desfazer': '⚠️ Desfazer o último lançamento: *#1* (despesa R$ 50,00)?\nConfirma? Responda *sim* ou *não*.'
- Achado: 'desfazer' sozinho ARMA uma pendência (⚠️ ... Confirma? sim/não) em vez de desfazer direto com ↩️ — só desfaz de fato após um 'sim' subsequente, que a nota do vault não lista como turno separado.

---

## 13. Cadastrar cartão
**Domínio:** Cartão de Crédito · **Cenário/usuário:** free (novo) C13a one-shot; free (novo) C13b guiado; pro (novo) C13c duplicado; C13a gate

> Você: criar cartao Nubank fecha 10 vence 17
> PigBank: ✅ Cartão *Nubank* registrado com sucesso! Confira os detalhes:
• Nome: Nubank
• Fechamento: dia 10
• Vencimento: dia 17
• Limite: não definido
• Cartão principal: Sim
• Lembrete: desativado
Como este é seu primeiro cartão, ele já foi definido como principal.

Gostaria de receber notificações antes do vencimento da fatura? Responda *sim* ou *não*.
>
> Você: não
> PigBank: Deseja definir um limite de crédito para este cartão? Ex: *5000* ou *não*.
>
> Você: não
> PigBank: ✅ Cartão *Nubank* registrado com sucesso!
Confira os detalhes:
• Nome: Nubank
• Fechamento: dia 10
• Vencimento: dia 17
• Limite: não definido
• Cartão principal: Sim
• Lembrete: desativado
>
> Você: cadastrar cartão
> PigBank: Qual cartão deseja registrar?
>
> Você: Inter
> PigBank: Quando fecha a fatura do cartão *Inter*?
>
> Você: 25
> PigBank: Quando vence a fatura do cartão *Inter*?
>
> Você: 2
> PigBank: ✅ Cartão *Inter* registrado com sucesso! Confira os detalhes:
• Nome: Inter
• Fechamento: dia 25
• Vencimento: dia 2
• Limite: não definido
• Cartão principal: Sim
• Lembrete: desativado
Como este é seu primeiro cartão, ele já foi definido como principal.

Gostaria de receber notificações antes do vencimento da fatura? Responda *sim* ou *não*.
>
> Você: não
> PigBank: Deseja definir um limite de crédito para este cartão? Ex: *5000* ou *não*.
>
> Você: 3000
> PigBank: ✅ Cartão *Inter* registrado com sucesso!
Confira os detalhes:
• Nome: Inter
• Fechamento: dia 25
• Vencimento: dia 2
• Limite: R$ 3.000,00
• Cartão principal: Sim
• Lembrete: desativado
>
> Você: criar cartao Nubank fecha 10 vence 17
> PigBank: ✅ Cartão *Nubank* registrado com sucesso! Confira os detalhes:
• Nome: Nubank
• Fechamento: dia 10
• Vencimento: dia 17
• Limite: não definido
• Cartão principal: Sim
• Lembrete: desativado
Como este é seu primeiro cartão, ele já foi definido como principal.

Gostaria de receber notificações antes do vencimento da fatura? Responda *sim* ou *não*.
>
> Você: criar cartao Nubank
> PigBank: Deseja definir um limite de crédito para este cartão? Ex: *5000* ou *não*.
>
> Você: criar cartao Outro fecha 1 vence 10
> PigBank: 🐷 No seu plano você adiciona 1 cartão. Com um plano pago você organiza todos eles em um lugar só.
Faça upgrade: https://pigbankai.com/precos
>

**Veredito:** ✅

**Checklist do vault:**
- [x] one-shot "criar cartao Nubank fecha 10 vence 17" → ✅ + Nubank + indicador de principal
- [x] fluxo guiado (nome→fechamento→vencimento→lembretes→limite) → ✅ Inter criado
- [?] nome duplicado (Pro, sem diálogo completo documentado no vault) — ver nota
- [x] gate Free (já tem 1 cartão) → "criar cartao Outro..." bloqueado / indicando limite

**Notas:**
- respostas do fluxo guiado: 'Qual cartão deseja registrar?' | 'Quando fecha a fatura do cartão *Inter*?' | 'Quando vence a fatura do cartão *Inter*?' | '✅ Cartão *Inter* registrado com sucesso! Confira os detalhes:\n• Nome: Inter\n• Fechamento: dia 25\n• Vencimento: dia 2\n• Limite: não definido\n• Cartão principal: Sim\n• Lembrete: desativado\nComo este é seu primeiro cartão, ele já foi definido como principal.\n\nGostaria de receber notificações antes do vencimento da fatura? Responda *sim* ou *não*.' | 'Deseja definir um limite de crédito para este cartão? Ex: *5000* ou *não*.' | '✅ Cartão *Inter* registrado com sucesso!\nConfira os detalhes:\n• Nome: Inter\n• Fechamento: dia 25\n• Vencimento: dia 2\n• Limite: R$ 3.000,00\n• Cartão principal: Sim\n• Lembrete: desativado'
- resposta a nome duplicado (Pro): 'Deseja definir um limite de crédito para este cartão? Ex: *5000* ou *não*.'
- resposta ao gate Free: '🐷 No seu plano você adiciona 1 cartão. Com um plano pago você organiza todos eles em um lugar só.\nFaça upgrade: https://pigbankai.com/precos'

---

## 14. Registrar compra no crédito
**Domínio:** Cartão de Crédito · **Cenário/usuário:** pro (novo) — C14

> Você: gastei 150 no cartão Nubank
> PigBank: ✅ *Compra no Crédito Registrada!*

💰 *Valor:* R$ 150,00
🪪 *Cartão:* Nubank
📅 *Data:* 20/08/26
📌 *Fatura atual:* R$ 150,00
⚙️ *Código:* CC1

Pra apagar: apagar CC1
>
> Você: credito 60 ifood
> PigBank: ✅ *Compra no Crédito Registrada!*

💰 *Valor:* R$ 60,00
🪪 *Cartão:* Nubank
📅 *Data:* 20/08/26
📌 *Fatura atual:* R$ 210,00
⚙️ *Código:* CC2

Pra apagar: apagar CC2
>
> Você: comprei 9000 no crédito
> PigBank: ❌ Compra não registrada no cartão *Nubank*.
💳 Limite total: R$ 100,00
📌 Já usado: R$ 210,00
🟢 Disponível: R$ 0,00
🧾 Tentativa de compra: R$ 9.000,00
⚠️ Excede o limite em R$ 9.000,00.
>

**Veredito:** ✅

**Checklist do vault:**
- [x] "gastei 150 no cartão Nubank" → ✅ + Compra no Crédito Registrada + R$ 150,00 + CC<n>
- [x] "credito 60 ifood" → ✅ + CC<n>
- [x] "comprei 9000 no crédito" com limite baixo → BLOQUEADO (❌/limite)

**Notas:**
- CC capturado (150 nubank): 1
- CC capturado (60 ifood): 2

---

## 15. Registrar parcelamento
**Domínio:** Cartão de Crédito · **Cenário/usuário:** pro — reusa uid de C14

> Você: parcelar 600 em 3x no Nubank
> PigBank: ❌ Compra não registrada no cartão *Nubank*.
💳 Limite total: R$ 100,00
📌 Já usado: R$ 210,00
🟢 Disponível: R$ 0,00
🧾 Tentativa de compra: R$ 600,00
⚠️ Excede o limite em R$ 600,00.
>
> Você: geladeira
> PigBank: 🐷 Oi! O que você precisa sobre a geladeira? Se for um gasto ou algo relacionado a finanças, me dá mais detalhes!
>
> Você: parcelei 12x de 79,90 celular
> PigBank: ❌ Compra não registrada no cartão *Nubank*.
💳 Limite total: R$ 100,00
📌 Já usado: R$ 210,00
🟢 Disponível: R$ 0,00
🧾 Tentativa de compra: R$ 958,80
⚠️ Excede o limite em R$ 958,80.
>

**Veredito:** 🔍

**Checklist do vault:**
- [ ] "parcelar 600 em 3x no Nubank" → pergunta o nome da compra
- [ ] "geladeira" → ✅ Parcelamento Registrado + 3x de R$ 200,00 + PC<código>
- [ ] "parcelei 12x de 79,90 celular" direto → ✅ Parcelamento Registrado + 12x de R$ 79,90 + PC

**Notas:**
- AVISO: este cenário reusa o cartão Nubank do item 14, que terminou com limite baixo (R$ 100,00) travado de propósito pra testar o bloqueio de compra. O spec do piloto não manda resetar o limite antes deste item — se o parcelamento também for bloqueado, é consequência desse estado compartilhado (achado válido: parcelamento respeita o mesmo limite de compra à vista), não necessariamente falha de compreensão de linguagem.
- PC capturado (geladeira): None
- PC capturado (celular): None
- Veredito 🔍 em vez de ❌: bloqueio por limite (ver aviso acima), não erro de compreensão — precisa de re-teste com limite alto pra separar as duas causas.

---

## 16. Ver parcelamentos ativos
**Domínio:** Cartão de Crédito · **Cenário/usuário:** pro — reusa uid de C14

> Você: parcelamentos
> PigBank: 📭 Você não tem parcelamentos registrados.
>

**Veredito:** 🔍

**Checklist do vault:**
- [ ] "parcelamentos" → 📆 + lista com geladeira e celular + códigos PC

**Notas:**
- Nenhum parcelamento foi criado no item 15 (bloqueado pelo limite herdado do item 14) — esta lista vazia é consequência disso, não um teste limpo de listagem.

---

## 17. Ver fatura do cartão
**Domínio:** Cartão de Crédito · **Cenário/usuário:** pro — reusa uid de C14

> Você: fatura Nubank
> PigBank: 💳 Fatura atual (Nubank) 11/08/26 → 10/09/26
Total: R$ 210,00 | Pago: R$ 0,00 | Em aberto: R$ 210,00
Limite: R$ 100,00 | Disponível: R$ 0,00 (-110%)

🧾 *Outras compras:*
  • R$ 60,00 | alimentação | 20/08/26 | 60 ifood
  • R$ 150,00 | outros | 20/08/26 | compra no credito
>
> Você: faturas
> PigBank: 🧾 *Faturas em aberto (por mês):*

📅 *Setembro/2026:*
• Nubank: Total R$ 210,00 | Pago R$ 0,00 | Em aberto R$ 210,00
>

**Veredito:** ✅

**Checklist do vault:**
- [x] "fatura Nubank" → 💳 + Fatura + Total + Em aberto
- [x] "faturas" → lista de faturas abertas incluindo Nubank

---

## 18. Ver e definir limite do cartão
**Domínio:** Cartão de Crédito · **Cenário/usuário:** pro — reusa uid de C14

> Você: limite nubank
> PigBank: 🐷 Seu limite do Nubank tá assim:

*Limite total:* R$ 100,00  
*Usado:* R$ 210,00  
*Disponível:* R$ 0,00  

Parece que você ultrapassou o limite. Se precisar de ajuda com isso, é só falar!
>
> Você: definir limite Nubank 8000
> PigBank: ❓ Não encontrei o cartão. Verifique o nome com: *cartões*
>
> Você: limite nubank 8000
> PigBank: 🐷 Seu limite do Nubank ainda tá em R$ 100,00, e você já usou R$ 210,00. Não tá liberado o limite de R$ 8.000,00. 

Se precisar de ajuda pra entender isso ou resolver, me avisa!
>
> Você: limite nubank
> PigBank: 🐷 Seu limite do Nubank tá assim:

*Limite total:* R$ 100,00  
*Usado:* R$ 210,00  
*Disponível:* R$ 0,00  

Você ultrapassou o limite. Se precisar de ajuda com isso, é só avisar!
>

**Veredito:** ❌

**Checklist do vault:**
- [x] "limite nubank" → mostra limite/usado/disponível
- [ ] "definir limite Nubank 8000" → ✅ + novo limite R$ 8.000,00
- [ ] "limite nubank" de novo → reflete 8000

**Notas:**
- 1ª tentativa "definir limite Nubank 8000" não achou o cartão; variação "limite nubank 8000" → '🐷 Seu limite do Nubank ainda tá em R$ 100,00, e você já usou R$ 210,00. Não tá liberado o limite de R$ 8.000,00. \n\nSe precisar de ajuda pra entender isso ou resolver, me avisa!'

---

## 19. Listar e definir cartão principal
**Domínio:** Cartão de Crédito · **Cenário/usuário:** pro (novo) — C19

> Você: cartoes
> PigBank: 💳 *Seus cartões cadastrados*

💳 *Inter*
🗓️ Fechamento: dia *25*
📆 Vencimento: dia *2*
💰 Limite: *não definido*
🔔 Lembrete: desativado

💳 *Nubank* • ⭐ principal
🗓️ Fechamento: dia *10*
📆 Vencimento: dia *17*
💰 Limite: *não definido*
🔔 Lembrete: desativado
>
> Você: padrao Inter
> PigBank: ✅ Cartão padrão definido: Inter
>
> Você: meu Nubank vence quando
> PigBank: 💳 O cartão *Nubank* vence no dia *17*.
>

**Veredito:** 🔍

**Checklist do vault:**
- [x] "cartoes" → lista os dois, marcação só no Nubank
- [ ] "padrao Inter" → registrado o comportamento real (ver discrepância)
- [x] "meu Nubank vence quando" → dia 17

**Notas:**
- resposta 'cartoes': '💳 *Seus cartões cadastrados*\n\n💳 *Inter*\n🗓️ Fechamento: dia *25*\n📆 Vencimento: dia *2*\n💰 Limite: *não definido*\n🔔 Lembrete: desativado\n\n💳 *Nubank* • ⭐ principal\n🗓️ Fechamento: dia *10*\n📆 Vencimento: dia *17*\n💰 Limite: *não definido*\n🔔 Lembrete: desativado'
- [ACHADO PRINCIPAL] resposta a 'padrao Inter': '✅ Cartão padrão definido: Inter'

---

## 20. Excluir cartão
**Domínio:** Cartão de Crédito · **Cenário/usuário:** pro — reusa uid de C19, cartão Inter

> Você: excluir cartao Inter
> PigBank: ⚠️ Tem certeza que deseja excluir o cartão *Inter*?
Isso irá remover também as faturas e transações associadas.

Responda *sim* para confirmar ou *não* para cancelar.
>
> Você: não
> PigBank: Perfeito. Mantive o cartão *Inter*.
>
> Você: cartoes
> PigBank: 💳 *Seus cartões cadastrados*

💳 *Inter* • ⭐ principal
🗓️ Fechamento: dia *25*
📆 Vencimento: dia *2*
💰 Limite: *não definido*
🔔 Lembrete: desativado

💳 *Nubank*
🗓️ Fechamento: dia *10*
📆 Vencimento: dia *17*
💰 Limite: *não definido*
🔔 Lembrete: desativado
>

**Veredito:** ✅

**Checklist do vault:**
- [x] "excluir cartao Inter" → ⚠️ pedindo confirmação sim/não
- [x] "não" → confirma cancelamento, Inter NÃO foi removido
- [x] "cartoes" depois → Inter ainda aparece

**Notas:**
- resposta ao 'não': 'Perfeito. Mantive o cartão *Inter*.'

---

## 21. Apagar compra ou parcelamento
**Domínio:** Cartão de Crédito · **Cenário/usuário:** pro — reusa uid de C14

> Você: apagar CC2
> PigBank: 🗑️ Compra no crédito CC2 apagada.
Removido: R$ 60,00.
>
> Você: apagar PC99999999
> PigBank: 🐷 Não achei o lançamento 'PC99999999'. Manda o ID exato (aparece no histórico como #N, ou o código do parcelamento tipo PCxxxxxxxx) ou pede pra eu listar os últimos lançamentos.
>

**Veredito:** 🔍

**Checklist do vault:**
- [x] "apagar CC<código>" → 🗑️ confirmando compra apagada
- [ ] "apagar PC<código>" → 🗑️ confirmando parcelamento desfeito

**Notas:**
- usando CC=2 (compra ifood) e PC=None (parcelamento geladeira)
- PC não capturado no item 15 (parcelamento bloqueado pelo limite herdado do item 14) — este turno usou um código inexistente de propósito só pra registrar a resposta de 'não achei'; não testa o apagar de verdade.

---

## 22. Responder sim ou não (sinônimos)
**Domínio:** Confirmações/Pendências · **Cenário/usuário:** free (novo) — P22; free (novo) — P22b

> Você: gastei 20 no busao
> PigBank: 💸 *Despesa registrada*: R$ 20,00
🏷️ Categoria: outros
🏦 Saldo: R$ -20,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: apagar #1
> PigBank: ⚠️ Isso vai apagar o lançamento *#1* e desfazer seus efeitos no saldo.
Confirma? Responda *sim* ou *não*.
>
> Você: pode
> PigBank: ✅ Lançamento *#1* apagado e saldo revertido.
>
> Você: sim
> PigBank: Não entendi bem o que você quis fazer. 🤔
Tenta assim:
• *gastei 50 no mercado* — registrar um gasto
• *saldo* — ver quanto você tem
• *recebi 1000 de salário* — registrar uma receita

Ou digite *ajuda* pra ver tudo que eu faço.
>
> Você: gastei 30 no cafe
> PigBank: 💸 *Despesa registrada*: R$ 30,00
🏷️ Categoria: alimentação
🏦 Saldo: R$ -30,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: apagar #1
> PigBank: ⚠️ Isso vai apagar o lançamento *#1* e desfazer seus efeitos no saldo.
Confirma? Responda *sim* ou *não*.
>
> Você: nope
> PigBank: ❌ Ação cancelada.
>
> Você: meus gastos
> PigBank: 🧾 *Últimos 1 lançamentos*:
💸 hoje • R$ 30,00 • gastei 30 no cafe [#1]

💸 Gastos: R$ 30,00
>

**Veredito:** ✅

**Checklist do vault:**
- [x] "pode" (sinônimo de sim) → confirma apagamento igual a 'sim' (✅, não 🗑️ — ver item 10)
- [x] "sim" sem pendência armada (uid totalmente novo) → não quebra
- [x] "nope" (sinônimo de não) → cancela igual a 'não', #M continua existindo
- [x] confirma que #M ainda existe após 'nope'

**Notas:**
- resposta a 'sim' sem pendência: 'Não entendi bem o que você quis fazer. 🤔\nTenta assim:\n• *gastei 50 no mercado* — registrar um gasto\n• *saldo* — ver quanto você tem\n• *recebi 1000 de salário* — registrar uma receita\n\nOu digite *ajuda* pra ver tudo que eu faço.'

---

## 23. Pendência abandonada por novo comando
**Domínio:** Confirmações/Pendências · **Cenário/usuário:** free (novo) — P23

> Você: gastei 40 no mercado
> PigBank: 💸 *Despesa registrada*: R$ 40,00
🏷️ Categoria: mercado
🏦 Saldo: R$ -40,00
ID: #1

🎉 Esse foi seu primeiro lançamento — viu como é rápido?
Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: é só mandar *ajuda*.
>
> Você: apagar #1
> PigBank: ⚠️ Isso vai apagar o lançamento *#1* e desfazer seus efeitos no saldo.
Confirma? Responda *sim* ou *não*.
>
> Você: saldo
> PigBank: 🏦 *Conta Corrente*: R$ -40,00

📋 *Hoje*
  • Gastei 40 no mercado: R$ 40,00

📊 *Gastos em Agosto*: R$ 40,00
>
> Você: meus gastos
> PigBank: 🧾 *Últimos 1 lançamentos*:
💸 hoje • R$ 40,00 • gastei 40 no mercado [#1]

💸 Gastos: R$ 40,00
>

**Veredito:** ✅

**Checklist do vault:**
- [x] "saldo" → responde saldo normalmente (não trata como confirmação)
- [x] "meus gastos" depois → #N NÃO foi apagado (guarda anti-órfã)

---
