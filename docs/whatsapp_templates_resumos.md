# Templates WhatsApp — Resumos semanal e mensal

Rascunhos para criar/aprovar no **WhatsApp Manager (Meta)**. Enquanto os
templates não existirem e as env vars não estiverem setadas, o bot apenas ignora
o envio periódico no WhatsApp (não envia nada, não dá erro).

## Regras que o código espera

O código envia os seguintes **parâmetros nomeados** no corpo (body) do template
(`adapters/whatsapp/wa_app.py` → `_periodic_template_named_body_params`):

| Variável         | Exemplo de valor        | O que é                          |
|------------------|-------------------------|----------------------------------|
| `periodo`        | `27/07/2026 a 02/08/2026` | intervalo do resumo            |
| `saldo`          | `R$ 1.000,00`           | saldo atual (já formatado)       |
| `gastos`         | `R$ 150,00`             | total de despesas do período     |
| `receita`        | `R$ 20,00`              | total de receitas do período     |
| `lancamentos`    | `3`                     | nº de lançamentos no período     |

> Os valores já vêm formatados (com `R$`). **Não** coloque `R$` fixo no template
> antes de `{{saldo}}` / `{{gastos}}` / `{{receita}}`, senão duplica.

Env vars que ligam o envio (o **nome** do template na Meta deve ser idêntico ao
valor da env var):

```
WA_WEEKLY_TEMPLATE_NAME=resumo_semanal
WA_MONTHLY_TEMPLATE_NAME=resumo_mensal
WA_PROACTIVE_TEMPLATE_LANGUAGE=pt_BR       # já usado pelo report diário
WA_PERIODIC_TEMPLATE_STOP_BUTTON=1         # opcional: ativa o botão "Desligar" (ver abaixo)
```

## Botão "Desligar" na própria mensagem (opcional)

Igual ao report diário, os resumos semanal/mensal podem ter um botão de
**resposta rápida (quick reply)** "Desligar" na mensagem. Ao tocar, o bot
desliga aquele resumo (semanal ou mensal) para o cliente.

Para usar:
1. Ao criar cada template, adicione **1 botão do tipo "Resposta rápida (Quick
   reply)"** com o texto que quiser (ex.: `Desligar resumo`).
2. Setar `WA_PERIODIC_TEMPLATE_STOP_BUTTON=1`.

O código envia o *payload* correto automaticamente (`weekly_report_disable` /
`monthly_report_disable`) — você só precisa criar o botão no template; o texto
dele é livre. O cliente também pode desligar respondendo *"desligar resumo
semanal"* / *"desligar resumo mensal"* por texto, ou pelo painel de Configurações.

---

## Template 1 — Semanal

- **Nome (Name):** `resumo_semanal`
- **Categoria (Category):** `Utility` (utilidade — é atualização de conta, não marketing)
- **Idioma (Language):** `Portuguese (BR)` → `pt_BR`
- **Cabeçalho (Header):** *(opcional)* Texto estático — `📊 Resumo semanal`
- **Rodapé (Footer):** *(opcional)* `Bot Financeiro`
- **Botões (Buttons):** *(opcional)* 1 botão **Resposta rápida** — ex.: `Desligar resumo` (ver seção "Botão Desligar")

**Corpo (Body)** — cole exatamente isto (as variáveis são nomeadas):

```
📊 *Resumo semanal do Bot Financeiro*
📅 Período: {{periodo}}

🏦 Saldo atual: {{saldo}}
📉 Gastos da semana: {{gastos}}
📈 Receitas da semana: {{receita}}
📊 Lançamentos da semana: {{lancamentos}}
```

**Valores de exemplo (Sample values)** — a Meta pede um exemplo por variável:

| Variável       | Exemplo                   |
|----------------|---------------------------|
| `periodo`      | `27/07/2026 a 02/08/2026` |
| `saldo`        | `R$ 1.000,00`             |
| `gastos`       | `R$ 150,00`               |
| `receita`      | `R$ 20,00`                |
| `lancamentos`  | `3`                       |

---

## Template 2 — Mensal

- **Nome (Name):** `resumo_mensal`
- **Categoria (Category):** `Utility`
- **Idioma (Language):** `Portuguese (BR)` → `pt_BR`
- **Cabeçalho (Header):** *(opcional)* Texto estático — `📊 Resumo mensal`
- **Rodapé (Footer):** *(opcional)* `Bot Financeiro`
- **Botões (Buttons):** *(opcional)* 1 botão **Resposta rápida** — ex.: `Desligar resumo` (ver seção "Botão Desligar")

**Corpo (Body):**

```
📊 *Resumo mensal do Bot Financeiro*
📅 Período: {{periodo}}

🏦 Saldo atual: {{saldo}}
📉 Gastos do mês: {{gastos}}
📈 Receitas do mês: {{receita}}
📊 Lançamentos do mês: {{lancamentos}}
```

**Valores de exemplo (Sample values):**

| Variável       | Exemplo                   |
|----------------|---------------------------|
| `periodo`      | `01/07/2026 a 31/07/2026` |
| `saldo`        | `R$ 1.000,00`             |
| `gastos`       | `R$ 999,90`               |
| `receita`      | `R$ 500,00`               |
| `lancamentos`  | `1`                       |

---

## Checklist de ativação

1. Criar os 2 templates acima no WhatsApp Manager e aguardar **aprovação** da Meta.
2. Setar as env vars (`WA_WEEKLY_TEMPLATE_NAME`, `WA_MONTHLY_TEMPLATE_NAME`) —
   o `WA_PROACTIVE_TEMPLATE_LANGUAGE` já existe.
3. Deploy. O envio ocorre automaticamente: semanal toda **segunda**, mensal todo
   **dia 1º**, no mesmo horário que o cliente já recebe o report diário.

> Dica: use **parâmetros nomeados** ao criar (não posicionais `{{1}}`/`{{2}}`).
> O código envia por nome (`periodo`, `saldo`, ...). Se a Meta/sua conta ainda
> usar só posicionais, me avise que eu troco o código para enviar posicional.
