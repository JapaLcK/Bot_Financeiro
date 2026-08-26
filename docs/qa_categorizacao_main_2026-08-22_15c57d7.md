# QA de categorização — 2026-08-22

Gerado por `scripts/category_qa_harness.py --label main` contra a árvore `/Users/lucaskuramoti/Desktop/bot/bot_wa/.claude/worktrees/worktree-question-d612ba`, num Postgres isolado e descartável.

## O que exatamente foi medido (lido em runtime)

- **árvore desta execução:** `/Users/lucaskuramoti/Desktop/bot/bot_wa/.claude/worktrees/worktree-question-d612ba` — **15c57d7 (worktree-question-d612ba, dirty)**
- **kill switch de rede:** LIGADO (tests/conftest.py::_block_outbound_network)
- **`OPENAI_API_KEY` no processo, VERIFICADO agora:** `False` ← passo 6 do infer_category inalcançável
- **variáveis que o import da árvore injetou no processo:** ['APP_ENV']
- **chamadas contadas:** {'category_service.learn_from_signals': 66, 'categories.learn_from_signals': 12, 'category_service.learn_from_inference': 0, 'launches.learn_from_inference': 100, 'launches.list_launches': 0, 'launches.spend_query': 0}

**Sumário:** 112 casos — ✅ 93 · ❌ 17 · ⚠️ 0 · 🔍 2

> **✅ quer dizer “igual ao comportamento atual”, não “correto”.** 4 dos 93 verdes documentam comportamento que a própria nota chama de indesejável: `B1.44`, `B2.10`, `B2.12`, `B2.13`.

**IA:** DESLIGADA — `OPENAI_API_KEY` ausente do processo (verificado em runtime, ver acima); passo 6 do `infer_category` inalcançável; casos de IA saem 🔍

## Matriz categoria × acertos (B1)

| categoria esperada | acertos / casos |
|---|---|
| `alimentação` | 4/4 |
| `assinaturas` | 3/3 |
| `beleza` | 3/3 |
| `compras online` | 3/3 |
| `criptomoedas` | 2/2 |
| `educação` | 3/3 |
| `investimento_aporte` | 3/3 |
| `investimento_resgate` | 1/1 |
| `investimentos` | 1/1 |
| `lazer` | 4/4 |
| `mercado` | 4/4 |
| `moradia` | 3/3 |
| `outros` | 4/4 |
| `pets` | 3/3 |
| `rendimentos` | 2/2 |
| `saúde` | 3/3 |
| `transporte` | 4/4 |

## B8 — o input REAL do usuário (por classe)

A categoria esperada destes casos vem do **comportamento pretendido**, não do que o código devolve. ❌ aqui é **achado de QA** — nenhum arquivo de produção foi alterado por causa deles.

| classe | ✅ medidos | ❌ medidos | 🔍 sem esperado único | casos |
|---|---|---|---|---|
| valor-primeiro | 3 | 0 | 0 | 3 |
| wake-word | 0 | 2 — `B8.04`, `B8.05` | 0 | 2 |
| gíria/abreviação | 0 | 2 — `B8.06`, `B8.07` | 0 | 2 |
| gíria já no dicionário | 1 | 0 | 0 | 1 |
| escrita errada/sem acento | 2 | 1 — `B8.10` | 0 | 3 |
| CAIXA ALTA | 2 | 0 | 0 | 2 |
| transcrição de áudio | 0 | 2 — `B8.14`, `B8.15` | 0 | 2 |
| multi-transação | 3 | 3 — `B8.19`, `B8.20`, `B8.21` | 0 | 6 |

| caso | mensagem | esperado | obtido | reason | veredito |
|---|---|---|---|---|---|
| `B8.01` | `77,90 mercado` | `mercado` | `mercado` | `local_rule` | ✅ |
| `B8.02` | `25 uber` | `transporte` | `transporte` | `local_rule` | ✅ |
| `B8.03` | `1500 aluguel` | `moradia` | `moradia` | `local_rule` | ✅ |
| `B8.04` | `pig gastei 45 no ifood` | `alimentação` | `None` | `None` | ❌ |
| `B8.05` | `pig 60 cinema` | `lazer` | `None` | `None` | ❌ |
| `B8.06` | `40 no rango` | `alimentação` | `outros` | `default` | ❌ |
| `B8.07` | `paguei 25 de cerva` | `alimentação` | `outros` | `default` | ❌ |
| `B8.08` | `gastei 30 no mercadinho` | `mercado` | `mercado` | `local_rule` | ✅ |
| `B8.09` | `gastei 60 no acai` | `alimentação` | `alimentação` | `local_rule` | ✅ |
| `B8.10` | `paguei 39,90 da netflis` | `assinaturas` | `outros` | `default` | ❌ |
| `B8.11` | `gastei 120 na farmacia de manha` | `saúde` | `saúde` | `local_rule` | ✅ |
| `B8.12` | `PAGUEI 180 DE GASOLINA` | `transporte` | `transporte` | `local_rule` | ✅ |
| `B8.13` | `COMPREI 150 NA SHOPEE` | `compras online` | `compras online` | `local_rule` | ✅ |
| `B8.14` | `entao eu gastei 45 reais la no ifood hoje de manha` | `alimentação` | `None` | `None` | ❌ |
| `B8.15` | `ahn paguei 180 de gasolina acho que foi ontem` | `transporte` | `None` | `None` | ❌ |
| `B8.16` | `gastei 30 no uber e 45 no ifood` | `[('transporte', 'despesa', 30.0), ('alimentação', 'despesa', 45.0)]` | `[('transporte', 'despesa', 30.0), ('alimentação', 'despesa', 45.0)]` | `local_rule · local_rule` | ✅ |
| `B8.17` | `paguei 120 de internet e 180 de luz` | `[('moradia', 'despesa', 120.0), ('moradia', 'despesa', 180.0)]` | `[('moradia', 'despesa', 120.0), ('moradia', 'despesa', 180.0)]` | `local_rule · local_rule` | ✅ |
| `B8.18` | `35 padaria e 80 farmacia` | `[('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]` | `[('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]` | `local_rule · local_rule` | ✅ |
| `B8.19` | `35 padaria 80 farmacia` | `[('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]` | `[('alimentação', 'despesa', 35.0)]` | `local_rule` | ❌ |
| `B8.20` | `gastei 35 na padaria 80 na farmacia` | `[('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]` | `[('alimentação', 'despesa', 35.0)]` | `local_rule` | ❌ |
| `B8.21` | `gastei 30 no uber e no ifood` | `[('transporte', 'despesa', 30.0)]` | `[('alimentação', 'despesa', 30.0)]` | `local_rule` | ❌ |

**Placar do B8: 11 ✅ · 10 ❌ em 21 casos.**

Para comparar: o B1, que usa só frases canônicas começando em `gastei|paguei|comprei|recebi`, acerta 50/50.

## Todos os casos

| caso | mensagem / cena | esperado | obtido (banco) | reason | veredito |
|---|---|---|---|---|---|
| `B1.01` | gastei 45 no ifood | `alimentação` | `alimentação` | `local_rule` | ✅ |
| `B1.02` | paguei 32 no almoço | `alimentação` | `alimentação` | `local_rule` | ✅ |
| `B1.03` | gastei 28 na PADARIA | `alimentação` | `alimentação` | `local_rule` | ✅ |
| `B1.04` | gastei 60 no açaí | `alimentação` | `alimentação` | `local_rule` | ✅ |
| `B1.05` | gastei 250 no supermercado | `mercado` | `mercado` | `local_rule` | ✅ |
| `B1.06` | comprei material de limpeza 80 | `mercado` | `mercado` | `local_rule` | ✅ |
| `B1.07` | gastei 120 num jogo de cama | `mercado` | `mercado` | `local_rule` | ✅ |
| `B1.08` | gastei 200 no atacadão | `mercado` | `mercado` | `local_rule` | ✅ |
| `B1.09` | gastei 25 no uber | `transporte` | `transporte` | `local_rule` | ✅ |
| `B1.10` | paguei 180 de gasolina | `transporte` | `transporte` | `local_rule` | ✅ |
| `B1.11` | paguei 90 de passagem de ônibus | `transporte` | `transporte` | `local_rule` | ✅ |
| `B1.12` | paguei 900 de IPVA | `transporte` | `transporte` | `local_rule` | ✅ |
| `B1.13` | gastei 120 na farmácia | `saúde` | `saúde` | `local_rule` | ✅ |
| `B1.14` | paguei 150 na academia | `saúde` | `saúde` | `local_rule` | ✅ |
| `B1.15` | paguei 300 no dentista | `saúde` | `saúde` | `local_rule` | ✅ |
| `B1.16` | paguei 1500 de aluguel | `moradia` | `moradia` | `local_rule` | ✅ |
| `B1.17` | paguei 180 de conta de luz | `moradia` | `moradia` | `local_rule` | ✅ |
| `B1.18` | paguei 120 de internet | `moradia` | `moradia` | `local_rule` | ✅ |
| `B1.19` | gastei 400 com aluguel de carro | `lazer` | `lazer` | `local_rule` | ✅ |
| `B1.20` | gastei 60 no cinema | `lazer` | `lazer` | `local_rule` | ✅ |
| `B1.21` | gastei 250 no show | `lazer` | `lazer` | `local_rule` | ✅ |
| `B1.22` | paguei 400 de hotel | `lazer` | `lazer` | `local_rule` | ✅ |
| `B1.23` | paguei 800 de faculdade | `educação` | `educação` | `local_rule` | ✅ |
| `B1.24` | comprei um livro 45 | `educação` | `educação` | `local_rule` | ✅ |
| `B1.25` | gastei 300 no curso de inglês | `educação` | `educação` | `local_rule` | ✅ |
| `B1.26` | paguei 39,90 de netflix | `assinaturas` | `assinaturas` | `local_rule` | ✅ |
| `B1.27` | paguei 55 do amazon prime | `assinaturas` | `assinaturas` | `local_rule` | ✅ |
| `B1.28` | paguei 34,90 de spotify | `assinaturas` | `assinaturas` | `local_rule` | ✅ |
| `B1.29` | gastei 180 no petshop | `pets` | `pets` | `local_rule` | ✅ |
| `B1.30` | comprei ração 120 | `pets` | `pets` | `local_rule` | ✅ |
| `B1.31` | gastei 90 com vacina do cachorro | `pets` | `pets` | `local_rule` | ✅ |
| `B1.32` | comprei 150 na shopee | `compras online` | `compras online` | `local_rule` | ✅ |
| `B1.33` | gastei 89 na amazon | `compras online` | `compras online` | `local_rule` | ✅ |
| `B1.34` | comprei 200 no mercado livre | `compras online` | `compras online` | `local_rule` | ✅ |
| `B1.35` | gastei 90 no cabeleireiro | `beleza` | `beleza` | `local_rule` | ✅ |
| `B1.36` | paguei 60 na manicure | `beleza` | `beleza` | `local_rule` | ✅ |
| `B1.37` | gastei 45 no barbeiro | `beleza` | `beleza` | `local_rule` | ✅ |
| `B1.38` | comprei 500 de bitcoin | `criptomoedas` | `criptomoedas` | `local_rule` | ✅ |
| `B1.39` | comprei 200 em ethereum | `criptomoedas` | `criptomoedas` | `local_rule` | ✅ |
| `B1.40` | gastei 300 no mercado bitcoin | `investimento_aporte` | `investimento_aporte` | `local_rule` | ✅ |
| `B1.41` | gastei 1000 com tesouro direto | `investimento_aporte` | `investimento_aporte` | `local_rule` | ✅ |
| `B1.42` | comprei 5000 de PETR4 | `investimento_aporte` | `investimento_aporte` | `ticker_match` | ✅ |
| `B1.43` | comprei 5000 de petr4 | `outros` | `outros` | `default` | ✅ |
| `B1.44` | gastei 1000 em investimentos | `investimentos` | `investimentos` | `local_rule` | ✅ |
| `B1.45` | recebi 2000 de resgate | `investimento_resgate` | `investimento_resgate` | `local_rule` | ✅ |
| `B1.46` | recebi 120 de dividendos | `rendimentos` | `rendimentos` | `local_rule` | ✅ |
| `B1.47` | recebi 80 de juros | `rendimentos` | `rendimentos` | `local_rule` | ✅ |
| `B1.48` | paguei 90 de juros do cheque especial | `outros` | `outros` | `default` | ✅ |
| `B1.49` | gastei 70 com zzqwx | `outros` | `outros` | `default` | ✅ |
| `B1.50` | gastei 50 numa sexta-feira | `outros` | `outros` | `default` | ✅ |
| `B2.01` | aprender rifa como lazer → confirmação + regra no banco | `True` | `True` | `None` | ✅ |
| `B2.02` | gastei 20 na rifa | `lazer` | `lazer` | `user_rule` | ✅ |
| `B2.03` | gastei 30 no uber | `lazer` | `lazer` | `user_rule` | ✅ |
| `B2.04` | `categorias` lista a regra criada | `True` | `True` | `None` | ✅ |
| `B2.05` | remover regra <keyword> | `True` | `True` | `None` | ✅ |
| `B2.06` | gastei 20 na rifa | `outros` | `outros` | `default` | ✅ |
| `B2.07` | remover regra <categoria> apaga as N regras dela | `True` | `True` | `None` | ✅ |
| `B2.08` | remover regra inexistente → aviso, sem estourar | `True` | `True` | `None` | ✅ |
| `B2.09` | paguei 39,90 de netflix | `lazer` | `lazer` | `user_rule` | ✅ |
| `B2.10` | gastei 18 na cafeteria | `beleza` | `beleza` | `user_rule` | ✅ |
| `B2.11` | comprei 1000 de PETR4 | `mercado` | `investimento_aporte` | `ticker_match` | ❌ |
| `B2.12` | `criar categoria viagens` (sem ' linkar ') cria linha em user_categori | `0` | `0` | `None` | ✅ |
| `B2.13` | `criar categoria viagens linkar decolar` cria linha em user_categories | `0` | `0` | `None` | ✅ |
| `B2.14` | gastei 300 na decolar | `viagens` | `viagens` | `user_rule` | ✅ |
| `B3.01` | gastei 400 com a namorada | `gastos com namorada` | `gastos com namorada` | `user_category` | ✅ |
| `B3.02` | gastei 200 com a família | `saúde da família` | `saúde da família` | `user_category` | ✅ |
| `B3.03` | gastei 70 com zzqwx | `outros` | `outros` | `default` | ✅ |
| `B3.04` | gastei 50 com o cachorro do vizinho | `cachorro do vizinho` | `cachorro do vizinho` | `user_category` | ✅ |
| `B3.05` | gastei 50 com o cachorro | `cachorro` | `cachorro do vizinho` | `user_category` | ❌ |
| `B3.06` | create_user_category(uid, "Saúde") com a system "saúde" semeada | `CATEGORIA_DUPLICADA` | `CATEGORIA_DUPLICADA` | `None` | ✅ |
| `B3.07` | gastei 100 com a namorada | `gastos com namorada` | `gastos com namorada` | `user_category` | ✅ |
| `B3.08` | gastei 100 com a namorada | `outros` | `outros` | `default` | ✅ |
| `B3.09` | renomear categoria custom faz cascata em launches.categoria | `gastos com a esposa` | `gastos com a esposa` | `None` | ✅ |
| `B3.10` | gastei 90 com a esposa | `gastos com a esposa` | `gastos com a esposa` | `user_category` | ✅ |
| `B3.11` | gastei 90 com a namorada | `outros` | `outros` | `default` | ✅ |
| `B3.12` | apagar categoria custom que tem lançamentos | `CATEGORIA_COM_LANCAMENTOS` | `CATEGORIA_COM_LANCAMENTOS` | `None` | ✅ |
| `B4.01` | custom JÁ existe: 'gastei 80 com a namorada no cinema' grava alguma re | `False` | `False` | `None` | ✅ |
| `B4.02` | gastei 400 com a namorada | `gastos com namorada` | `gastos com namorada` | `user_category` | ✅ |
| `B4.03` | 200 namorada cinema | `gastos com namorada` | `lazer` | `user_rule` | ❌ |
| `B4.04` | confirmação de foto de cupom (add_from_entities com category_reason='i | `False` | `True` | `None` | ❌ |
| `B4.05` | 300 namorada cinema | `gastos com namorada` | `lazer` | `user_rule` | ❌ |
| `B5.01` | lançamento no WhatsApp grava pending recategorize_launch_offer | `recategorize_launch_offer` | `recategorize_launch_offer` | `None` | ✅ |
| `B5.02` | _apply_recategorize(..., 'lazer') grava lazer | `lazer` | `lazer` | `None` | ✅ |
| `B5.03` | _apply_recategorize com rótulo acentuado 'saúde' | `saúde` | `saúde` | `None` | ✅ |
| `B5.04` | _apply_recategorize com nome de categoria CUSTOM acentuada | `saúde da família` | `saude da familia` | `None` | ❌ |
| `B5.05` | a lista interativa de recategorização inclui a categoria custom? | `True` | `False` | `None` | ❌ |
| `B6.01` | gastei 70 com zzqwx | `lazer` | `lazer` | `user_rule` | ✅ |
| `B6.02` | gastei 70 com zzqwx | `outros` | `outros` | `default` | ✅ |
| `B6.03` | gastei 400 com a namorada | `outros` | `outros` | `default` | ✅ |
| `B7.01` | frase sem keyword nenhuma, usuário Pro → passo 6 (GPT) | `categoria plausível` | `None` | `None` | 🔍 |
| `B7.02` | cross-check IA × determinístico (launches.py:418) | `regra determinística vence a IA quando contradiz` | `None` | `None` | 🔍 |
| `B8.01` | 77,90 mercado | `mercado` | `mercado` | `local_rule` | ✅ |
| `B8.02` | 25 uber | `transporte` | `transporte` | `local_rule` | ✅ |
| `B8.03` | 1500 aluguel | `moradia` | `moradia` | `local_rule` | ✅ |
| `B8.04` | pig gastei 45 no ifood | `alimentação` | `None` | `None` | ❌ |
| `B8.05` | pig 60 cinema | `lazer` | `None` | `None` | ❌ |
| `B8.06` | 40 no rango | `alimentação` | `outros` | `default` | ❌ |
| `B8.07` | paguei 25 de cerva | `alimentação` | `outros` | `default` | ❌ |
| `B8.08` | gastei 30 no mercadinho | `mercado` | `mercado` | `local_rule` | ✅ |
| `B8.09` | gastei 60 no acai | `alimentação` | `alimentação` | `local_rule` | ✅ |
| `B8.10` | paguei 39,90 da netflis | `assinaturas` | `outros` | `default` | ❌ |
| `B8.11` | gastei 120 na farmacia de manha | `saúde` | `saúde` | `local_rule` | ✅ |
| `B8.12` | PAGUEI 180 DE GASOLINA | `transporte` | `transporte` | `local_rule` | ✅ |
| `B8.13` | COMPREI 150 NA SHOPEE | `compras online` | `compras online` | `local_rule` | ✅ |
| `B8.14` | entao eu gastei 45 reais la no ifood hoje de manha | `alimentação` | `None` | `None` | ❌ |
| `B8.15` | ahn paguei 180 de gasolina acho que foi ontem | `transporte` | `None` | `None` | ❌ |
| `B8.16` | gastei 30 no uber e 45 no ifood | `[('transporte', 'despesa', 30.0), ('alimentação', 'despesa', 45.0)]` | `[('transporte', 'despesa', 30.0), ('alimentação', 'despesa', 45.0)]` | `local_rule · local_rule` | ✅ |
| `B8.17` | paguei 120 de internet e 180 de luz | `[('moradia', 'despesa', 120.0), ('moradia', 'despesa', 180.0)]` | `[('moradia', 'despesa', 120.0), ('moradia', 'despesa', 180.0)]` | `local_rule · local_rule` | ✅ |
| `B8.18` | 35 padaria e 80 farmacia | `[('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]` | `[('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]` | `local_rule · local_rule` | ✅ |
| `B8.19` | 35 padaria 80 farmacia | `[('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]` | `[('alimentação', 'despesa', 35.0)]` | `local_rule` | ❌ |
| `B8.20` | gastei 35 na padaria 80 na farmacia | `[('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]` | `[('alimentação', 'despesa', 35.0)]` | `local_rule` | ❌ |
| `B8.21` | gastei 30 no uber e no ifood | `[('transporte', 'despesa', 30.0)]` | `[('alimentação', 'despesa', 30.0)]` | `local_rule` | ❌ |

## Casos falhos (17)

### B2.11 — comprei 1000 de PETR4

- **esperado:** `mercado`
- **obtido (linha 🏷️ da resposta):** `investimento_aporte`
- **obtido (`launches.categoria`):** `investimento_aporte`
- **reason do `infer_category`:** `ticker_match`
- **diff de `user_category_rules`:** (nenhuma regra nova)
- **nota:** ordem REAL do infer_category: ticker (passo 2) roda ANTES da regra do usuário (passo 3) — regra para PETR4 nunca vence
- **resposta crua:** `'💸 *Despesa registrada*: R$ 1.000,00\n🏷️ Categoria: investimento_aporte\n🏦 Saldo: R$ -1.000,00\nID: #1'`

### B3.05 — gastei 50 com o cachorro

- **esperado:** `cachorro`
- **obtido (linha 🏷️ da resposta):** `cachorro do vizinho`
- **obtido (`launches.categoria`):** `cachorro do vizinho`
- **reason do `infer_category`:** `user_category`
- **diff de `user_category_rules`:** (nenhuma regra nova)
- **nota:** mesma dupla, texto sem 'vizinho'; list_custom_category_names ordena por length desc
- **resposta crua:** `'💸 *Despesa registrada*: R$ 50,00\n🏷️ Categoria: cachorro do vizinho\n🏦 Saldo: R$ -50,00\nID: #1\n\n🎉 Esse foi seu primeiro lançamento — viu como é rápido?\nAgora tenta *saldo*. Quando quiser, tem caixinhas,'`

### B4.03 — 200 namorada cinema

- **esperado:** `gastos com namorada`
- **obtido (linha 🏷️ da resposta):** `lazer`
- **obtido (`launches.categoria`):** `lazer`
- **reason do `infer_category`:** `user_rule`
- **diff de `user_category_rules`:** (nenhuma regra nova)
- **nota:** regra aprendida ANTES da custom existir: ['cinema→lazer', 'namorada cinema→lazer']. O passo 3 (user_rule) roda antes do passo 4 (user_category) e sequestra o lançamento
- **resposta crua:** `'💸 *Despesa registrada*: R$ 200,00\n🏷️ Categoria: lazer\n🏦 Saldo: R$ -280,00\nID: #2'`

### B4.04 — confirmação de foto de cupom (add_from_entities com category_reason='image_confirmed', categoria 'lazer', alvo 'namorada cinema') com a custom já criada — grava regra com 'namorada'?

- **esperado:** `False`
- **obtido (linha 🏷️ da resposta):** `True`
- **obtido (`launches.categoria`):** `True`
- **reason do `infer_category`:** `None`
- **diff de `user_category_rules`:** + cinema→lazer, namorada cinema→lazer
- **nota:** ESTE é o caso que separa as duas árvores: o guard novo do #123 (category_service.learn_from_signals, dentro do guard_local_conflict) recusa candidatos cujo token pertence a uma categoria custom do usuário. Regras gravadas nesta árvore: ['cinema→lazer', 'namorada cinema→lazer']

### B4.05 — 300 namorada cinema

- **esperado:** `gastos com namorada`
- **obtido (linha 🏷️ da resposta):** `lazer`
- **obtido (`launches.categoria`):** `lazer`
- **reason do `infer_category`:** `user_rule`
- **diff de `user_category_rules`:** (nenhuma regra nova)
- **nota:** regras vivas antes deste lançamento: ['cinema→lazer', 'namorada cinema→lazer'] — o lançamento caiu em `lazer` por `user_rule`. A regra com 'namorada' sequestrou o lançamento.
- **resposta crua:** `'💸 *Despesa registrada*: R$ 300,00\n🏷️ Categoria: lazer\n🏦 Saldo: R$ -380,00\nID: #2'`

### B5.04 — _apply_recategorize com nome de categoria CUSTOM acentuada

- **esperado:** `saúde da família`
- **obtido (linha 🏷️ da resposta):** `saude da familia`
- **obtido (`launches.categoria`):** `saude da familia`
- **reason do `infer_category`:** `None`
- **diff de `user_category_rules`:** —
- **nota:** canonicalize_category_label normaliza (tira acento) qualquer rótulo fora de CATEGORY_LABELS; user_categories guarda 'saúde da família' e o usage_count casa por lower(categoria)=uc.name
- **resposta crua:** `'✅ Categoria do lançamento #1 atualizada para *saude da familia*.'`

### B5.05 — a lista interativa de recategorização inclui a categoria custom?

- **esperado:** `True`
- **obtido (linha 🏷️ da resposta):** `False`
- **obtido (`launches.categoria`):** `False`
- **reason do `infer_category`:** `None`
- **diff de `user_category_rules`:** —
- **nota:** rows oferecidas (10): ['alimentação', 'mercado', 'transporte', 'lazer', 'moradia', 'saúde', 'educação', 'assinaturas', 'outros', '✏️ Outra (digitar)']

### B8.04 — pig gastei 45 no ifood

- **esperado:** `alimentação`
- **obtido (linha 🏷️ da resposta):** `None`
- **obtido (`launches.categoria`):** `None`
- **reason do `infer_category`:** `None`
- **diff de `user_category_rules`:** (nenhuma regra nova)
- **nota:** [wake-word] `pig` É a wake-word do bot — não é requisito inventado aqui: `utils_text.py:572` (`MEMORY_STOP_TOKENS`) a põe na stoplist de ruído do auto-aprendizado e `tests/test_category_learn_noise.py:30` cita `pig eu mercado mais` como frase-ruído REAL colhida em produção. O aprendizado sabe limpar o `pig`; a pergunta é se a mensagem chega até lá — ⚠️→❌: a mensagem NÃO virou lançamento nenhum. O bot respondeu: 'Não entendi exatamente o que você quis fazer com lançamentos.\n🧾 Posso te ajudar com lançamentos de algumas formas:\n• gas'
- **resposta crua:** `'Não entendi exatamente o que você quis fazer com lançamentos.\n🧾 Posso te ajudar com lançamentos de algumas formas:\n• gastei 50 mercado\n• recebi 1000 salario\n• gastos hoje\n• listar lancamentos\n• apagar'`

### B8.05 — pig 60 cinema

- **esperado:** `lazer`
- **obtido (linha 🏷️ da resposta):** `None`
- **obtido (`launches.categoria`):** `None`
- **reason do `infer_category`:** `None`
- **diff de `user_category_rules`:** (nenhuma regra nova)
- **nota:** [wake-word] wake-word + valor-primeiro na mesma mensagem — ⚠️→❌: a mensagem NÃO virou lançamento nenhum. O bot respondeu: 'Não entendi exatamente o que você quer fazer.\nTente uma destas opções:\n• saldo — ver saldo atual\n• gastei 50 mercado — r'
- **resposta crua:** `'Não entendi exatamente o que você quer fazer.\nTente uma destas opções:\n• saldo — ver saldo atual\n• gastei 50 mercado — registrar despesa\n• pagar fatura Nubank — pagar uma fatura\n• faturas — listar fat'`

### B8.06 — 40 no rango

- **esperado:** `alimentação`
- **obtido (linha 🏷️ da resposta):** `outros`
- **obtido (`launches.categoria`):** `outros`
- **reason do `infer_category`:** `default`
- **diff de `user_category_rules`:** (nenhuma regra nova)
- **nota:** [gíria/abreviação] `rango` é gíria corrente pra comida; não está nas LOCAL_RULES — só o passo 6 (GPT) acertaria isto, e ele é inalcançável para este usuário por PLANO, não pela flag `--ai`: o gate em `core/services/category_service.py:219-222` exige `OPENAI_API_KEY` **E** `is_pro`, e o usuário aqui é grátis. ❌ = o que um usuário do plano grátis recebe hoje em produção
- **resposta crua:** `'💸 *Despesa registrada*: R$ 40,00\n🏷️ Categoria: outros\n🏦 Saldo: R$ -40,00\nID: #1\n\n🎉 Esse foi seu primeiro lançamento — viu como é rápido?\nAgora tenta *saldo*. Quando quiser, tem caixinhas, cartões e da'`

### B8.07 — paguei 25 de cerva

- **esperado:** `alimentação`
- **obtido (linha 🏷️ da resposta):** `outros`
- **obtido (`launches.categoria`):** `outros`
- **reason do `infer_category`:** `default`
- **diff de `user_category_rules`:** (nenhuma regra nova)
- **nota:** [gíria/abreviação] `cerva` = cerveja; abreviação de bar/boteco — só o passo 6 (GPT) acertaria isto, e ele é inalcançável para este usuário por PLANO, não pela flag `--ai`: o gate em `core/services/category_service.py:219-222` exige `OPENAI_API_KEY` **E** `is_pro`, e o usuário aqui é grátis. ❌ = o que um usuário do plano grátis recebe hoje em produção
- **resposta crua:** `'💸 *Despesa registrada*: R$ 25,00\n🏷️ Categoria: outros\n🏦 Saldo: R$ -25,00\nID: #1\n\n🎉 Esse foi seu primeiro lançamento — viu como é rápido?\nAgora tenta *saldo*. Quando quiser, tem caixinhas, cartões e da'`

### B8.10 — paguei 39,90 da netflis

- **esperado:** `assinaturas`
- **obtido (linha 🏷️ da resposta):** `outros`
- **obtido (`launches.categoria`):** `outros`
- **reason do `infer_category`:** `default`
- **diff de `user_category_rules`:** (nenhuma regra nova)
- **nota:** [escrita errada/sem acento] erro de digitação de polegar; LOCAL_RULES casa por substring exata — só o passo 6 (GPT) acertaria isto, e ele é inalcançável para este usuário por PLANO, não pela flag `--ai`: o gate em `core/services/category_service.py:219-222` exige `OPENAI_API_KEY` **E** `is_pro`, e o usuário aqui é grátis. ❌ = o que um usuário do plano grátis recebe hoje em produção
- **resposta crua:** `'💸 *Despesa registrada*: R$ 39,90\n🏷️ Categoria: outros\n🏦 Saldo: R$ -39,90\nID: #1\n\n🎉 Esse foi seu primeiro lançamento — viu como é rápido?\nAgora tenta *saldo*. Quando quiser, tem caixinhas, cartões e da'`

### B8.14 — entao eu gastei 45 reais la no ifood hoje de manha

- **esperado:** `alimentação`
- **obtido (linha 🏷️ da resposta):** `None`
- **obtido (`launches.categoria`):** `None`
- **reason do `infer_category`:** `None`
- **diff de `user_category_rules`:** (nenhuma regra nova)
- **nota:** [transcrição de áudio] sem pontuação, com marcador de fala e ruído em volta — ⚠️→❌: a mensagem NÃO virou lançamento nenhum. O bot respondeu: 'Não entendi exatamente o que você quis fazer com lançamentos.\n🧾 Posso te ajudar com lançamentos de algumas formas:\n• gas'
- **resposta crua:** `'Não entendi exatamente o que você quis fazer com lançamentos.\n🧾 Posso te ajudar com lançamentos de algumas formas:\n• gastei 50 mercado\n• recebi 1000 salario\n• gastos hoje\n• listar lancamentos\n• apagar'`

### B8.15 — ahn paguei 180 de gasolina acho que foi ontem

- **esperado:** `transporte`
- **obtido (linha 🏷️ da resposta):** `None`
- **obtido (`launches.categoria`):** `None`
- **reason do `infer_category`:** `None`
- **diff de `user_category_rules`:** (nenhuma regra nova)
- **nota:** [transcrição de áudio] hesitação no começo e incerteza no fim — ⚠️→❌: a mensagem NÃO virou lançamento nenhum. O bot respondeu: 'Não entendi exatamente o que você quis fazer com cartões.\n🧾 Posso te ajudar com fatura/pagamento assim:\n• pagar fatura —'
- **resposta crua:** `'Não entendi exatamente o que você quis fazer com cartões.\n🧾 Posso te ajudar com fatura/pagamento assim:\n• pagar fatura — paga a fatura atual do cartão padrão\n• pagar Nubank — paga a fatura do Nubank ('`

### B8.19 — 35 padaria 80 farmacia

- **esperado:** `[('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]`
- **obtido (linha 🏷️ da resposta):** `alimentação`
- **obtido (`launches.categoria`):** `[('alimentação', 'despesa', 35.0)]`
- **reason do `infer_category`:** `local_rule`
- **diff de `user_category_rules`:** + padaria→alimentacao, padaria farmacia→alimentacao
- **nota:** [multi-transação] sem ` e ` entre os valores: o segundo par some e a resposta ainda diz sucesso — perda SILENCIOSA de lançamento — esperado 2 lançamento(s) [('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]; nasceram 1: [('alimentação', 'despesa', 35.0)]
- **resposta crua:** `'💸 *Despesa registrada*: R$ 35,00\n🏷️ Categoria: alimentação\n🏦 Saldo: R$ -35,00\nID: #1\n\n🎉 Esse foi seu primeiro lançamento — viu como é rápido?\nAgora tenta *saldo*. Quando quiser, tem caixinhas, cartões'`

### B8.20 — gastei 35 na padaria 80 na farmacia

- **esperado:** `[('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]`
- **obtido (linha 🏷️ da resposta):** `alimentação`
- **obtido (`launches.categoria`):** `[('alimentação', 'despesa', 35.0)]`
- **reason do `infer_category`:** `local_rule`
- **diff de `user_category_rules`:** + padaria→alimentacao, padaria farmacia→alimentacao
- **nota:** [multi-transação] mesma perda com verbo e preposições: não é a ausência do verbo que quebra, é a ausência do separador — esperado 2 lançamento(s) [('alimentação', 'despesa', 35.0), ('saúde', 'despesa', 80.0)]; nasceram 1: [('alimentação', 'despesa', 35.0)]
- **resposta crua:** `'💸 *Despesa registrada*: R$ 35,00\n🏷️ Categoria: alimentação\n🏦 Saldo: R$ -35,00\nID: #1\n\n🎉 Esse foi seu primeiro lançamento — viu como é rápido?\nAgora tenta *saldo*. Quando quiser, tem caixinhas, cartões'`

### B8.21 — gastei 30 no uber e no ifood

- **esperado:** `[('transporte', 'despesa', 30.0)]`
- **obtido (linha 🏷️ da resposta):** `alimentação`
- **obtido (`launches.categoria`):** `[('alimentação', 'despesa', 30.0)]`
- **reason do `infer_category`:** `local_rule`
- **diff de `user_category_rules`:** + ifood→alimentacao, uber ifood→alimentacao
- **nota:** [multi-transação] UM valor e DOIS destinos: o ` e ` está lá, o segundo valor não. O mínimo aceitável é registrar os R$30 no primeiro destino (`uber`) ou pedir esclarecimento; o que NÃO pode é o segundo destino escolher a categoria do dinheiro do primeiro — esperado 1 lançamento(s) [('transporte', 'despesa', 30.0)]; nasceram 1: [('alimentação', 'despesa', 30.0)]
- **resposta crua:** `'💸 *Despesa registrada*: R$ 30,00\n🏷️ Categoria: alimentação\n🏦 Saldo: R$ -30,00\nID: #1\n\n🎉 Esse foi seu primeiro lançamento — viu como é rápido?\nAgora tenta *saldo*. Quando quiser, tem caixinhas, cartões'`

## Diff de `user_category_rules` por cena

| caso | regras gravadas pela cena |
|---|---|
| `B1.01` | + ifood→alimentacao |
| `B1.02` | + almoco→alimentacao |
| `B1.03` | + padaria→alimentacao |
| `B1.04` | + acai→alimentacao |
| `B1.05` | + mercado→mercado, supermercado→mercado |
| `B1.06` | (nenhuma regra nova) |
| `B1.07` | (nenhuma regra nova) |
| `B1.08` | + atacadao→mercado |
| `B1.09` | + uber→transporte |
| `B1.10` | + gasolina→transporte |
| `B1.11` | + onibus→transporte |
| `B1.12` | + ipva→transporte |
| `B1.13` | + farmacia→saude |
| `B1.14` | + academia→saude |
| `B1.15` | + dentista→saude |
| `B1.16` | + aluguel→moradia |
| `B1.17` | + conta luz→moradia, luz→moradia |
| `B1.18` | + internet→moradia |
| `B1.19` | (nenhuma regra nova) |
| `B1.20` | + cinema→lazer |
| `B1.21` | + show→lazer |
| `B1.22` | + hotel→lazer |
| `B1.23` | + faculdade→educacao |
| `B1.24` | + livro→educacao |
| `B1.25` | + curso→educacao, curso ingles→educacao |
| `B1.26` | + netflix→assinaturas |
| `B1.27` | + amazon prime→assinaturas |
| `B1.28` | + spotify→assinaturas |
| `B1.29` | + petshop→pets |
| `B1.30` | + racao→pets |
| `B1.31` | + cachorro→pets, vacina cachorro→pets |
| `B1.32` | + shopee→compras online |
| `B1.33` | + amazon→compras online |
| `B1.34` | + mercado livre→compras online |
| `B1.35` | + cabeleireiro→beleza |
| `B1.36` | + manicure→beleza |
| `B1.37` | + barbeiro→beleza |
| `B1.38` | + bitcoin→criptomoedas |
| `B1.39` | + ethereum→criptomoedas |
| `B1.40` | (nenhuma regra nova) |
| `B1.41` | (nenhuma regra nova) |
| `B1.42` | (nenhuma regra nova) |
| `B1.43` | (nenhuma regra nova) |
| `B1.44` | + investimentos→investimentos |
| `B1.45` | (nenhuma regra nova) |
| `B1.46` | + dividendo→rendimentos, dividendos→rendimentos |
| `B1.47` | + juros→rendimentos |
| `B1.48` | (nenhuma regra nova) |
| `B1.49` | (nenhuma regra nova) |
| `B1.50` | (nenhuma regra nova) |
| `B2.02` | (nenhuma regra nova) |
| `B2.03` | (nenhuma regra nova) |
| `B2.05` | [] |
| `B2.06` | (nenhuma regra nova) |
| `B2.07` | [] |
| `B2.09` | (nenhuma regra nova) |
| `B2.10` | (nenhuma regra nova) |
| `B2.11` | (nenhuma regra nova) |
| `B2.13` | + decolar→viagens |
| `B2.14` | (nenhuma regra nova) |
| `B3.01` | (nenhuma regra nova) |
| `B3.02` | (nenhuma regra nova) |
| `B3.03` | (nenhuma regra nova) |
| `B3.04` | (nenhuma regra nova) |
| `B3.05` | (nenhuma regra nova) |
| `B3.07` | (nenhuma regra nova) |
| `B3.08` | (nenhuma regra nova) |
| `B3.10` | (nenhuma regra nova) |
| `B3.11` | (nenhuma regra nova) |
| `B4.01` | (nenhuma) |
| `B4.02` | (nenhuma regra nova) |
| `B4.03` | (nenhuma regra nova) |
| `B4.04` | + cinema→lazer, namorada cinema→lazer |
| `B4.05` | (nenhuma regra nova) |
| `B6.01` | (nenhuma regra nova) |
| `B6.02` | (nenhuma regra nova) |
| `B6.03` | (nenhuma regra nova) |
| `B8.01` | + mercado→mercado |
| `B8.02` | + uber→transporte |
| `B8.03` | + aluguel→moradia |
| `B8.04` | (nenhuma regra nova) |
| `B8.05` | (nenhuma regra nova) |
| `B8.06` | (nenhuma regra nova) |
| `B8.07` | (nenhuma regra nova) |
| `B8.08` | + mercadinho→mercado |
| `B8.09` | + acai→alimentacao |
| `B8.10` | (nenhuma regra nova) |
| `B8.11` | + farmacia→saude, farmacia manha→saude |
| `B8.12` | + gasolina→transporte |
| `B8.13` | + shopee→compras online |
| `B8.14` | (nenhuma regra nova) |
| `B8.15` | (nenhuma regra nova) |
| `B8.16` | + ifood→alimentacao, uber→transporte |
| `B8.17` | + internet→moradia, luz→moradia |
| `B8.18` | + farmacia→saude, padaria→alimentacao |
| `B8.19` | + padaria→alimentacao, padaria farmacia→alimentacao |
| `B8.20` | + padaria→alimentacao, padaria farmacia→alimentacao |
| `B8.21` | + ifood→alimentacao, uber ifood→alimentacao |

## Discrepâncias entre vault e código

Estado de cada uma RECONFERIDO no vault em `/Users/lucaskuramoti/Desktop/bot/Obsidian/PigBank` nesta execução.

- A nota prometia “ao escolher, atualiza (e pode virar regra)” e tinha “Oferece virar regra permanente” no checklist. **Medido (B5.02/B5.03):** a resposta é só `✅ Categoria do lançamento #N atualizada para *X*.` — `_apply_recategorize` não chama nenhum `learn_*` e não oferece nada.
  - **estado agora:** ✅ já corrigida no vault (conferido em runtime: `Interacoes/Corrigir categoria pelo botão.md` traz `Duas coisas que esta nota afirmava e o código NÃ`)
- A mesma nota mostrava o usuário digitando a categoria. **Medido (B5.05):** a lista interativa tem 10 rows FIXAS (8 comuns + `outros` + `✏️ Outra (digitar)`) e nenhuma categoria custom do usuário aparece.
  - **estado agora:** ✅ já corrigida no vault (conferido em runtime: `Interacoes/Corrigir categoria pelo botão.md` traz `A lista não mostra as categorias personalizadas`)
- A nota mostrava `🏷️ Suas regras: • ifood → alimentação`. **Medido (B2.04):** o cabeçalho é `🧠 *Regras de categoria*` e o agrupamento é por CATEGORIA (`• *lazer* (1 regras)` + `└ rifa`), não por keyword.
  - **estado agora:** ✅ já corrigida no vault (conferido em runtime: `Interacoes/Listar categorias e regras.md` traz `Correção de 2026-08-22`)
- A nota mostrava `✅ Aprendido! Agora *ifood* vai pra *alimentação*.`. **Medido (B2.01):** `✅ Aprendido: sempre que aparecer *rifa*, vou usar *lazer*`.
  - **estado agora:** ✅ já corrigida no vault (conferido em runtime: `Interacoes/Ensinar regra de categoria.md` traz `✅ Aprendido: sempre que aparecer *rifa*, vou usa`)
- A mesma nota listava `criar categoria X` como gatilho de criação. **Medido (B2.12/B2.13):** nem `criar categoria viagens` nem `criar categoria viagens linkar decolar` criam linha em `user_categories` — só regra. Criar categoria de verdade é só pelo dashboard.
  - **estado agora:** ✅ já corrigida no vault (conferido em runtime: `Interacoes/Ensinar regra de categoria.md` traz ``criar categoria X` NÃO cria categoria`)
- A nota mostrava `🗑️ Regra *ifood → alimentação* removida.`. **Medido (B2.05):** `✅ Regra removida: *rifa*` (sem 🗑️ e sem a categoria). E há um caminho não documentado: remover por CATEGORIA apaga N regras de uma vez (B2.07: `✅ 2 regras da categoria *lazer* foram removidas.`).
  - **estado agora:** ✅ já corrigida no vault (conferido em runtime: `Interacoes/Remover regra de categoria.md` traz `Correções de 2026-08-22`)
- A nota de domínio listava 4 interações; o domínio tem ≥10 (criar/renomear/arquivar/excluir categoria custom, categoria custom reconhecida no lançamento, categoria automática por palavra-chave, recategorizar pela IA).
  - **estado agora:** ✅ já corrigida no vault (conferido em runtime: `Dominios/Categorias e Regras.md` traz `Só pelo dashboard`)

## Fala do bot no vault × resposta medida

Cada `> **PigBank:** …` das notas que citam este harness tem que ser uma mensagem INTEIRA e VERBATIM capturada nesta execução. **Denominador, para o número não sair nu:** conferidas **30 de 266** bolhas do vault, nas **13 de 109** notas que têm bolha (as outras citam outra bateria e não é este relatório que responde por elas). As notas reescritas nesta bateria são **14** (as 13 com `medido: 2026-08-22` no frontmatter + `Referencia/Checklist QA.md`); a única fora desta conferência é `Interacoes/Recategorizar pela IA.md`, que não tem bolha nenhuma — o que ela afirma é prosa, e prosa esta seção não alcança.

**0** bolha(s) sem respaldo · **3** com atribuição não conferível.

Nenhuma sem respaldo. (Isto reprova bolha inventada, cortada pela metade, quebrada em uma bolha por linha, ou verbatim mas atribuída ao caso errado — os defeitos reais encontrados em 2026-08-22, quando uma cena mandava a mensagem e descartava a saída.)

Bolhas que passam no verbatim mas cuja ATRIBUIÇÃO não dá pra conferir (antes passavam caladas):

| nota | bolha | aviso |
|---|---|---|
| `Interacoes/Corrigir categoria pelo botão.md` | `✅ Categoria do lançamento #1 atualizada para *saude da familia*.` | sem anotação de caso — verbatim conferido, ATRIBUIÇÃO não conferível |
| `Interacoes/Ensinar regra de categoria.md` | `💸 *Despesa registrada*: R$ 18,00 ⏎ 🏷️ Categoria: beleza ⏎ 🏦 Saldo: R$ -18,00 ⏎ ID: #1 ⏎  ⏎ 🎉 Esse foi seu primeiro lançamento — viu como é rápido? ⏎ Ago` | sem anotação de caso — verbatim conferido, ATRIBUIÇÃO não conferível |
| `Interacoes/Multi-lançamento numa mensagem.md` | `💸 *Despesa registrada*: R$ 30,00 ⏎ 🏷️ Categoria: alimentação ⏎ 🏦 Saldo: R$ -30,00 ⏎ ID: #1 ⏎  ⏎ 🎉 Esse foi seu primeiro lançamento — viu como é rápido` | sem anotação de caso — verbatim conferido, ATRIBUIÇÃO não conferível |

## Classes de bug que esta bateria NUNCA pega

- **Dois dos três hunks de produção do #123 não são exercitados.** O PR tem (a) o guard em `core/services/category_service.py` — coberto pelo B4.04 —, (b) a delegação `list_launches → spend_query` e (c) o `date_filter` do `spend_query` (`core/handlers/launches.py`, +61 linhas). Medido nesta execução: `list_launches` 0 chamada(s), `spend_query` 0 — nenhuma mensagem de CONSULTA passa por esta bateria, que só faz lançamentos e comandos de regra. (b) e (c) estão **sem nenhum caso**. O `scripts/cleanup_poisoned_category_rules.py` do mesmo PR também não é executado em lugar nenhum daqui.
- **Contadores desta execução** (o que a bateria de fato chamou): {'category_service.learn_from_signals': 66, 'categories.learn_from_signals': 12, 'category_service.learn_from_inference': 0, 'launches.learn_from_inference': 100, 'launches.list_launches': 0, 'launches.spend_query': 0}.
- **Renderização no dashboard e agrupamento por `lower(categoria)`** — a bateria lê `launches.categoria` cru; não exercita a agregação nem a tela.
- **A lista interativa do WhatsApp** só é inspecionada estruturalmente (B5.05); o envio real pela API do WhatsApp não é exercitado.
- **O que a conferência de bolhas do vault (seção acima) pega e o que não pega.** PEGA, automaticamente: bolha que não existe em resposta nenhuma; e bolha VERBATIM porém atribuída ao caso ERRADO (a anotação `*(B5.02)*` é casada com a resposta daquele caso). AVISA, sem conseguir verificar: bolha sem anotação de caso, e anotação apontando para um caso que não existe nesta bateria — nos dois a atribuição fica sem oráculo (antes passavam caladas). NÃO PEGA, e só leitura linha a linha alcança: (a) as linhas `**Você:**`, que ninguém confere contra a mensagem realmente enviada; (b) tudo que a nota afirma FORA de um bloco de citação — tabelas, prosa, checklist; (c) nota que descreve um comportamento sem citar bolha nenhuma (é o caso de `Interacoes/Recategorizar pela IA.md`).
- **Qualidade do GPT** — com `--ai` desligado o passo 6 é inalcançável; B7 sai 🔍 (não roda caso nenhum). Para o B8 há uma SEGUNDA tranca, independente da flag: todo usuário do B8 nasce de `new_uid()` sem `pro=True`, e o gate (`core/services/category_service.py:219-222`) exige `OPENAI_API_KEY` **e** `is_pro`. Por isso `B8.06` (`rango`), `B8.07` (`cerva`) e `B8.10` (`netflis`) contam como ❌ e não como 🔍: ligar `--ai` não mudaria nada para eles, e o `outros` medido é o que o plano grátis entrega hoje em produção. O que continua NÃO medido é o teto do produto: o mesmo input num usuário Pro com chave real. Para medir isso faltam `--ai` + `new_uid(pro=True)` + rede — nada disso existe nesta execução.
- **Isolamento entre usuários só é medido no caminho da INFERÊNCIA.** B6.01–B6.03 provam que a regra (`zzqwx`) e a categoria custom (`gastos com namorada`) do usuário A não alcançam o B — e o par B6.01×B6.02 é o controle: a MESMA frase dá `lazer` para A e `outros` para B. O que NÃO tem caso nenhum: listar/remover regra de outro usuário, o botão “categoria errada?” num lançamento alheio, consulta de gastos cruzada, e o sentido B→A.
- **Divergência entre as 3 listas** (`SYSTEM_CATEGORIES_SEED` 15 · `ALLOWED_CATEGORIES` 15 · rótulos de `LOCAL_RULES` 16) — isso é enumeração escrita, nenhum teste alcança.
- **Cartão de crédito, endpoints do dashboard e import de OFX/PDF** — fora do escopo combinado; nenhuma medição aqui.
