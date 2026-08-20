---
name: baseline-testes
description: Como rodar a suíte do PigBank e ler o resultado — qual interpretador usar, quais variáveis a suíte exige, qual número esperar, e como distinguir regressão sua de falha preexistente. Use ANTES de rodar pytest neste repositório, antes de afirmar que "os testes passam/falham", e antes de abrir PR. Também cobre o ambiente remoto (Claude Code na web), que tem procedimento diferente do local.
---

# Rodar a suíte do PigBank

## O comando

```bash
export DATABASE_URL=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | tr -d "\"'")
export PYTHONPATH=.
.venv/bin/python -m pytest -q        # suíte inteira, sem exclusão nenhuma
```

**Esperado: `1202 passed` em ~58s.** Medido quatro vezes seguidas em 2026-08-20,
sempre idêntico.

Extrai só o `DATABASE_URL` em vez de `set -a; source .env` de propósito: o `.env`
tem uma linha com valor não-quotado contendo espaço (`EMAIL_FROM_NAME`, linha
223), e sourcear o arquivo inteiro imprime `command not found: Financeiro` no
meio da saída. Não quebra a suíte — mas um erro espúrio no meio do log é
exatamente o que faz alguém aprender a ignorar erro de verdade.

## O `DATABASE_URL` precisa apontar para um Postgres descartável

**A suíte apaga linhas.** O `_auto_cleanup_orphan_users` é `autouse`, roda em
todo teste, e o `_cleanup_user` faz `DELETE` em `credit_transactions`,
`credit_bills`, `credit_cards`, `launches`, `pockets`, `investments`, `accounts`
e `users`.

O `conftest.py` protege criando um database próprio da execução
(`pytest_<uuid>`) e derrubando no fim. Se essa criação falhar — papel sem
`CREATEDB`, host errado, credencial inválida — ele **aborta** com `UsageError`
em vez de cair de volta no `DATABASE_URL` que você passou. Se você vir essa
mensagem, o conserto é o banco, nunca contornar a guarda.

Para rodar sem isolamento de propósito (inspecionar o banco depois da rodada):
`PYTEST_DB_ISOLATION=0`. Aí a responsabilidade é sua.

Neste repositório o `.env` já aponta para `localhost:5432/pigbank_ci_test` — um
banco de teste dedicado. Se o seu não apontar, arrume antes de rodar.

Durante o desenvolvimento, um arquivo só:

```bash
.venv/bin/python -m pytest tests/test_x.py -q
```

## As três armadilhas do comando

**Use `.venv/bin/python`, não `python3`.** O Python do sistema desta máquina não
tem pytest, psycopg nem nada — `python3 -m pytest` morre no import e o erro *não*
é falha de teste. Todas as dependências estão no `.venv`, inclusive `ofxparse`,
`reportlab` e `pypdf`.

**`DATABASE_URL` é a única variável que você precisa fornecer.** O
`tests/conftest.py` define sozinho, via `setdefault`, o `JWT_SECRET`, o
`PII_ENCRYPTION_KEY` (Fernet gerada na hora), o `PII_HASH_PEPPER`, o
`PII_AUDIT_DISABLED` e o `PLANS_V2_ENABLED=0`. Não exporte essas à mão — você só
sobrescreveria o default com um valor pior.

**Não passe `--ignore`.** Nenhum. A suíte roda inteira, com zero erros de coleta.

## Se você leu que precisa ignorar 9 arquivos, isso está velho

O `CLAUDE.md` §6 descreve um procedimento com 9 `--ignore` por causa do
`ofxparse` ausente, uma guarda de coleta que pareia arquivo e causa, e 7 falhas
fixas em `tests/test_statement_import.py`. **Nada disso vale mais nesta máquina.**
O `.venv` tem os três pacotes, a coleta é limpa e não há falha nenhuma.

E, onde os pacotes de fato faltarem, o `conftest.py` já resolve sozinho: ele traz
a própria lista dos 9 arquivos (`_OFXPARSE_DEPENDENTES`) e mais 4 testes de
import tardio (`_OFXPARSE_IMPORT_TARDIO`), aplicados **só** quando
`PYTEST_ALLOW_MISSING_OPTIONAL_DEPS=1`. Essa variável é opt-in de propósito: se o
alívio fosse automático, o `ofxparse` caindo do `requirements.txt` deixaria a
suíte verde com 9 arquivos silenciados e `core.handle_incoming` quebrado em
produção.

Se algum dia a coleta voltar a estourar, o conserto é instalar a dependência no
`.venv` — não recriar a lista de `--ignore` à mão.

## Ambiente remoto (Claude Code na web) é outro mundo

Lá o `.claude/hooks/session-start.sh` faz tudo no `SessionStart`: instala as
dependências (menos `audioop-lts` e `ofxparse`, que não constroem), sobe um
Postgres exclusivo daquela sessão, e exporta `PYTHONPATH`, `DATABASE_URL`,
`JWT_SECRET`, as chaves PII e `PYTEST_ALLOW_MISSING_OPTIONAL_DEPS=1`. O comando
lá é só:

```bash
python3 -m pytest -q
```

Na máquina do dev o hook sai na terceira linha (`CLAUDE_CODE_REMOTE != true`) e
não faz nada — o setup local é o `.venv` + `.env` acima.

Se o hook avisar que zerou o `DATABASE_URL`, **não contorne**. Ele zera de
propósito quando não conseguiu banco isolado, porque a suíte apaga usuários,
lançamentos, caixinhas e investimentos; herdar uma URL compartilhada faria o
pytest destruir dado de verdade. A falha é segura por design.

## Ler o resultado

**Tire a baseline ANTES de mexer.** Falha que já existia não é regressão sua.
Sem baseline não dá para separar as duas, e sobra "os testes estão vermelhos"
sem conclusão.

**A baseline precisa ser da MESMA árvore.** Se você rebasear no meio do trabalho,
a baseline anterior deixa de valer — commits novos trazem testes novos e o número
sobe sozinho. Já aconteceu: uma baseline de 1109 comparada com 1141 depois de um
rebase parecia +32 de ganho e era só 3 arquivos de teste que chegaram no rebase.
Refaça a baseline depois de trocar de base.

**Compare por nome de teste, não por contagem.** Contagem igual não prova ausência
de regressão (um teste novo pode mascarar um quebrado). Hoje a suíte é estável em
1202, mas a regra vale de qualquer forma.

**Isolar um arquivo corta a interferência dos outros, e só isso.** Os testes de
dentro do arquivo continuam no mesmo processo, na ordem de definição. Para
descartar ordem interna, rode o teste sozinho e depois varie a ordem passando os
node IDs na linha de comando (o pytest respeita a ordem que você lista):

```bash
.venv/bin/python -m pytest -v "tests/test_x.py::test_b" "tests/test_x.py::test_a"
```

"Passou isolado" nunca é prova de que não há efeito de ordem.

## Antes de dizer "pronto": prove que o teste falha sem o fix

Reverta a correção, rode o teste, veja **vermelho**; reponha, veja **verde**. Um
teste escrito junto com o código costuma ser tautológico — afirma o que o código
faz e é verde por construção. Se ele passa com e sem a correção, conserte o teste
antes de reportar o resultado.

Depois pergunte: **que classe de bug esta verificação nunca pegaria?** Se existe
uma classe cega, ela precisa de outro método — mais do mesmo teste não alcança.

## O que a suíte NÃO prova

Verde aqui não é "funciona no WhatsApp". Os testes de WhatsApp
(`test_whatsapp_simulation.py` e irmãos) mockam o LLM/NLP e o envio, então cobrem
a lógica intermediária, não o comando ponta a ponta. Bug na interpretação real ou
no roteamento real passa batido.

Frontend também não: mudança de CSS/JS só aparece depois do deploy, porque o app
iOS carrega o site ao vivo. E `env(safe-area-inset-*)` vale 0 no Chromium
headless, então regra de área segura é inerte em teste local.

No relato, separe **"verificado aqui"** de **"só no aparelho/deploy"**. Silêncio
sobre o não-verificado lê-se como verificado.

## CI

`.github/workflows/tests.yml` sobe o próprio Postgres 16, instala o
`requirements.txt` inteiro (com `ofxparse`) e roda `pytest` (bloqueante) e `audit`
de CVEs (não-bloqueante), em push na `main` e em todo PR. O CI é confirmação, não
descoberta — não use como primeiro teste.
