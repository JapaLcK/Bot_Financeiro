# QA piloto — vault WhatsApp (23 interações, 3 domínios)

Gerado em 2026-09-02_1738 por `scripts/whatsapp_qa_vault_harness.py`, chamando `core.handle_incoming.handle_incoming()` direto contra um Postgres isolado e descartável, sem mockar a IA (chamadas OpenAI reais).

**Sumário:** 11 interações — ✅ 7 · ❌ 4 · ⚠️ 0 · 🔍 0

**Caminho:** 10 turno(s) resolvido(s) pela IA · 12 por comando/regex (sem chamada nenhuma) — 46 chamadas OpenAI no total.

**Custo:** US$ 0.0126 nesta rodada · US$ 0.0238 acumulado no mês pelos harnesses. 151204 tokens de entrada, 138880 cacheados (92%), cobrados à metade. Preços da tabela de 2026-09-01, sem o whisper (cobrado por minuto) — reconfira antes de citar fora daqui.

**Guarda de afirmações:** 6 afirmação(ões) numérica(s) em resposta de IA · **0 não sustentada(s)** (número que não veio de nenhuma tool nem da mensagem do usuário).

## Discrepâncias entre vault e código

(nenhuma sinalizada)

---

## PAR — Saldo
**Domínio:** comando × IA · **Cenário/usuário:** dois users Pro com seed idêntico; caminho de cada lado é medido

> Você: saldo
> PigBank: 🏦 *Conta Corrente*: R$ 1.736,65

📋 *Hoje*
  • Gastei 50 no mercado: R$ 50,00
  • Gastei 123,45 na farmacia: R$ 123,45
  • Gastei 89,90 com uber: R$ 89,90

📊 *Gastos em Setembro*: R$ 263,35
> _caminho: comando (0 chamadas)_
>
> Você: qto sobrou pra mim
> PigBank: 🐷 Sobrou R$ *1.736,65* pra você! 

Se precisar de mais alguma coisa, é só falar!
> _caminho: IA (3 chamadas: gpt-4o-mini-2024-07-18)_
> _guarda: ✅ 1 afirmação(ões), todas sustentadas_
>

**Veredito:** ✅

**Checklist do vault:**
- [x] todo valor da forma solta aparece na resposta da forma curta
- [x] a forma solta trouxe o dado numérico que a curta traz
- [x] guarda: nenhum número da IA veio de fora das tools

---

## PAR — Saldo (2)
**Domínio:** comando × IA · **Cenário/usuário:** dois users Pro com seed idêntico; caminho de cada lado é medido

> Você: saldo
> PigBank: 🏦 *Conta Corrente*: R$ 1.736,65

📋 *Hoje*
  • Gastei 50 no mercado: R$ 50,00
  • Gastei 123,45 na farmacia: R$ 123,45
  • Gastei 89,90 com uber: R$ 89,90

📊 *Gastos em Setembro*: R$ 263,35
> _caminho: comando (0 chamadas)_
>
> Você: quanto eu tenho de dinheiro agora
> PigBank: 🏦 *Conta Corrente*: R$ 1.736,65

📋 *Hoje*
  • Gastei 50 no mercado: R$ 50,00
  • Gastei 123,45 na farmacia: R$ 123,45
  • Gastei 89,90 com uber: R$ 89,90

📊 *Gastos em Setembro*: R$ 263,35
> _caminho: IA (1 chamada: gpt-4o-mini-2024-07-18)_
>

**Veredito:** ✅

**Checklist do vault:**
- [x] todo valor da forma solta aparece na resposta da forma curta
- [x] a forma solta trouxe o dado numérico que a curta traz
- [x] guarda: nenhum número da IA veio de fora das tools

---

## PAR — Extrato
**Domínio:** comando × IA · **Cenário/usuário:** dois users Pro com seed idêntico; caminho de cada lado é medido

> Você: extrato
> PigBank: 🧾 *Últimos 4 lançamentos*:
💸 hoje • R$ 89,90 • gastei 89,90 com uber [#4]
💰 hoje • R$ 2.000,00 • recebi 2000 de salario [#3]
💸 hoje • R$ 123,45 • gastei 123,45 na farmacia [#2]
💸 hoje • R$ 50,00 • gastei 50 no mercado [#1]

💸 Gastos: R$ 263,35  |  💰 Receitas: R$ 2.000,00
> _caminho: comando (0 chamadas)_
>
> Você: me mostra meus ultimos lancamentos
> PigBank: 🐷 Aqui estão seus últimos lançamentos:

1. *R$ 89,90* - Gastei com uber (transporte)
2. *R$ 2.000,00* - Recebi de salário (rendimentos)
3. *R$ 123,45* - Gastei na farmácia (saúde)
4. *R$ 50,00* - Gastei no mercado (mercado)

Se precisar de mais alguma coisa, é só avisar!
> _caminho: IA (2 chamadas: gpt-4o-mini-2024-07-18)_
> _guarda: ✅ 4 afirmação(ões), todas sustentadas_
>

**Veredito:** ✅

**Checklist do vault:**
- [x] todo valor da forma solta aparece na resposta da forma curta
- [x] a forma solta trouxe o dado numérico que a curta traz
- [x] guarda: nenhum número da IA veio de fora das tools

---

## PAR — Gasto do mês
**Domínio:** comando × IA · **Cenário/usuário:** dois users Pro com seed idêntico; caminho de cada lado é medido

> Você: quanto gastei
> PigBank: 💸 Você gastou *R$ 263,35* neste mês.

📊 Top categorias:
• saúde: R$ 123,45
• transporte: R$ 89,90
• mercado: R$ 50,00
> _caminho: comando (0 chamadas)_
>
> Você: quanto eu torrei esse mes
> PigBank: Você poderia especificar o mês que deseja consultar?
> _caminho: IA (1 chamada: gpt-4o-mini-2024-07-18)_
>

**Veredito:** ❌

**Checklist do vault:**
- [x] todo valor da forma solta aparece na resposta da forma curta
- [ ] a forma solta trouxe o dado numérico que a curta traz
- [x] guarda: nenhum número da IA veio de fora das tools

**Notas:**
- a forma curta trouxe 4 valor(es) e a solta não trouxe nenhum

---

## PAR — Gasto do mês (2)
**Domínio:** comando × IA · **Cenário/usuário:** dois users Pro com seed idêntico; caminho de cada lado é medido

> Você: quanto gastei
> PigBank: 💸 Você gastou *R$ 263,35* neste mês.

📊 Top categorias:
• saúde: R$ 123,45
• transporte: R$ 89,90
• mercado: R$ 50,00
> _caminho: comando (0 chamadas)_
>
> Você: somando tudo, quanto saiu da minha conta
> PigBank: Você gostaria de saber quanto saiu da sua conta em qual período?
> _caminho: IA (1 chamada: gpt-4o-mini-2024-07-18)_
>

**Veredito:** ❌

**Checklist do vault:**
- [x] todo valor da forma solta aparece na resposta da forma curta
- [ ] a forma solta trouxe o dado numérico que a curta traz
- [x] guarda: nenhum número da IA veio de fora das tools

**Notas:**
- a forma curta trouxe 4 valor(es) e a solta não trouxe nenhum

---

## PAR — Registrar gasto
**Domínio:** comando × IA · **Cenário/usuário:** dois users Pro com seed idêntico; caminho de cada lado é medido

> Você: gastei 30 no ifood
> PigBank: 💸 *Despesa registrada*: R$ 30,00
🏷️ Categoria: alimentação
🏦 Saldo: R$ 1.706,65
ID: #5
> _caminho: comando (0 chamadas)_
>
> Você: torrei trinta reais no ifood hoje
> PigBank: Não consegui identificar o valor. Tente: *gastei 50 no mercado*
> _caminho: comando (0 chamadas)_
>

**Veredito:** ❌

**Checklist do vault:**
- [x] todo valor da forma solta aparece na resposta da forma curta
- [ ] a forma solta trouxe o dado numérico que a curta traz
- [x] guarda: nenhum número da IA veio de fora das tools

**Notas:**
- a forma curta trouxe 2 valor(es) e a solta não trouxe nenhum

---

## PAR — Registrar receita
**Domínio:** comando × IA · **Cenário/usuário:** dois users Pro com seed idêntico; caminho de cada lado é medido

> Você: recebi 500 freela
> PigBank: 💰 *Receita registrada*: R$ 500,00
🏷️ Categoria: rendimentos
🏦 Saldo: R$ 2.236,65
ID: #5
> _caminho: IA (1 chamada: gpt-4o-mini-2024-07-18)_
>
> Você: caiu quinhentos de freela na conta
> PigBank: 💰 *Receita registrada*: R$ 500,00
🏷️ Categoria: outros
🏦 Saldo: R$ 2.236,65
ID: #5
> _caminho: IA (1 chamada: gpt-4o-mini-2024-07-18)_
>

**Veredito:** ✅

**Checklist do vault:**
- [x] todo valor da forma solta aparece na resposta da forma curta
- [x] a forma solta trouxe o dado numérico que a curta traz
- [x] guarda: nenhum número da IA veio de fora das tools

---

## PAR — Gasto por categoria
**Domínio:** comando × IA · **Cenário/usuário:** dois users Pro com seed idêntico; caminho de cada lado é medido

> Você: gastos com saúde
> PigBank: 🐷 Olha só, você gastou R$ *123,45* com saúde só hoje. 

Se precisar de mais detalhes ou quiser ver outros gastos, é só avisar!
> _caminho: IA (2 chamadas: gpt-4o-mini-2024-07-18)_
> _guarda: ✅ 1 afirmação(ões), todas sustentadas_
>
> Você: quanto eu gastei com remedio e farmacia
> PigBank: 🐷 Você não teve gastos em *remedio e farmacia* neste mês.
> _caminho: comando (0 chamadas)_
>

**Veredito:** ❌

**Checklist do vault:**
- [x] todo valor da forma solta aparece na resposta da forma curta
- [ ] a forma solta trouxe o dado numérico que a curta traz
- [x] guarda: nenhum número da IA veio de fora das tools

**Notas:**
- a forma curta trouxe 1 valor(es) e a solta não trouxe nenhum

---

## PAR — Limite do cartão
**Domínio:** comando × IA · **Cenário/usuário:** dois users Pro com seed idêntico; caminho de cada lado é medido

> Você: limite nubank
> PigBank: 🐷 Não tenho o limite do seu Nubank cadastrado. Pra ver quanto sobra, você precisa cadastrar o limite do cartão no dashboard. Se precisar de ajuda com isso, é só avisar!
> _caminho: IA (3 chamadas: gpt-4o-mini-2024-07-18)_
>
> Você: quanto ainda posso gastar no nubank
> PigBank: 🐷 Não consegui pegar o limite do seu Nubank porque ele não tá cadastrado. 

Pra ver quanto você ainda pode gastar, dá uma olhada no dashboard e cadastra o limite do cartão. Se precisar de ajuda pra acessar, é só pedir!
> _caminho: IA (3 chamadas: gpt-4o-mini-2024-07-18)_
>

**Veredito:** ✅

**Checklist do vault:**
- [x] todo valor da forma solta aparece na resposta da forma curta
- [x] a forma solta trouxe o dado numérico que a curta traz
- [x] guarda: nenhum número da IA veio de fora das tools

---

## PAR — Fatura
**Domínio:** comando × IA · **Cenário/usuário:** dois users Pro com seed idêntico; caminho de cada lado é medido

> Você: fatura nubank
> PigBank: 💳 Fatura atual (Nubank) 11/08/26 → 10/09/26
Total: R$ 0,00 | Pago: R$ 0,00 | Em aberto: R$ 0,00

> _caminho: comando (0 chamadas)_
>
> Você: quanto ta a fatura do nubank
> PigBank: 💳 Fatura atual (Nubank) 11/08/26 → 10/09/26
Total: R$ 0,00 | Pago: R$ 0,00 | Em aberto: R$ 0,00

> _caminho: comando (0 chamadas)_
>

**Veredito:** ✅

**Checklist do vault:**
- [x] todo valor da forma solta aparece na resposta da forma curta
- [x] a forma solta trouxe o dado numérico que a curta traz
- [x] guarda: nenhum número da IA veio de fora das tools

---

## PAR — Listar cartões
**Domínio:** comando × IA · **Cenário/usuário:** dois users Pro com seed idêntico; caminho de cada lado é medido

> Você: cartoes
> PigBank: 💳 *Seus cartões cadastrados*

💳 *Nubank* • ⭐ principal
🗓️ Fechamento: dia *10*
📆 Vencimento: dia *17*
💰 Limite: *não definido*
🔔 Lembrete: desativado
> _caminho: comando (0 chamadas)_
>
> Você: quais cartoes eu tenho cadastrados
> PigBank: 💳 *Seus cartões cadastrados*

💳 *Nubank* • ⭐ principal
🗓️ Fechamento: dia *10*
📆 Vencimento: dia *17*
💰 Limite: *não definido*
🔔 Lembrete: desativado
> _caminho: comando (0 chamadas)_
>

**Veredito:** ✅

**Checklist do vault:**
- [x] todo valor da forma solta aparece na resposta da forma curta
- [x] a forma solta trouxe o dado numérico que a curta traz
- [x] guarda: nenhum número da IA veio de fora das tools

---
