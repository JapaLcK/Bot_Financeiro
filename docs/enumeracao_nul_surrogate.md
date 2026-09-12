# NUL e surrogate solitário — enumeração das rotas ANÔNIMAS (issue #321)

Sequência do PR #320 (issue #317), que fechou **só** o webhook da Pluggy e o
`note` dos saques. A issue #321 pede o app inteiro; **o dono cortou esta fatia
em "rotas anônimas primeiro"** — é onde o custo do 500 é maior, porque quem
dispara não precisa de conta. Autenticadas, admin e os `Jsonb` de resposta da
Pluggy ficam para as fatias seguintes (§6).

> **Regra do `CLAUDE.md` §2 aplicada a este arquivo:** todo número aqui vem com
> **a data em que foi medido** e **o comando que o produziu — colado no §8, para
> ser rodado, não descrito**. Contagem envelhece em silêncio a cada commit —
> **remeça antes de reusar**.
>
> A primeira versão deste arquivo citava scripts `sonda/t321/*.py` que nunca
> foram versionados. Nomear um script que não existe é prosa com cara de
> comando: ninguém remede a partir dela. As cinco estão coladas no §8.

**Duas colunas, sempre** (`CLAUDE.md` §3, "varredura combinatória se roda em
DUAS COLUNAS") — e as duas são **commit**, nunca uma árvore de trabalho:

| coluna | o que é | como reproduzir |
|---|---|---|
| **`main`** | `12d643c` — baseline histórico medido em 2026-09-10; não é o pai deste documento nem uma afirmação sobre a produção atual. | `git worktree add --detach /tmp/enum-main-12d643c 12d643c`; rodar as sondas nessa árvore |
| **`leva`** | `1f1b57b` — ponta de `fix/321-rotas-anonimas-500`, que **contém** `fix/369-corpo-venenoso-auth` (`62b500d`) como ancestral. | `git worktree add /tmp/wt321 1f1b57b --detach` e rodar a sonda do §8 lá dentro |

A coluna `leva` **não** é "o que estará na `main`": ela vale para aqueles dois
commits, e morre se a revisão mudar os PRs ou o merge sair fora de ordem. É por
isso que ela é um commit nomeado e não "a árvore de trabalho" — a versão
anterior deste documento media um worktree não commitado, que não existe mais em
lugar nenhum e que dizia `500 → 500` para três rotas que o #369 já fechava.

**Quem fecha o quê nesta leva** (medido com `git diff --name-only 12d643c <branch>`):

- **`fix/369-corpo-venenoso-auth`** (`62b500d`) — as 7 rotas anônimas de auth com
  modelo Pydantic (§3.1) e a classe app-wide do 422 (§3.4). Toca `core/pg_text.py`,
  `frontend/finance_bot_websocket_custom.py` e o teste.
- **`fix/321-rotas-anonimas-500`** (`1f1b57b`, empilhado sobre o #369) — os quatro
  path params da §3.2, mais `POST /auth/dashboard-link`, que herdou de carona a
  mesma guarda de `db.reports`. Toca `db/affiliates.py`, `db/google_auth.py`,
  `db/privacy.py`, `db/reports.py` e `core/pg_text.py`.
- **`fix/372-payout-sem-mandato`** (`ad69c7b`) e **`fix/ofx-route-ambiente-reduzido`**
  (`83c51c0`) andam junto nesta leva e **não mudam nenhuma célula desta
  enumeração**: o primeiro toca `core/admin_dashboard.py` (payout do admin) e o
  segundo só `tests/conftest.py`.
- A **#375 não é desta leva**: foi fechada pelo **PR #378** (mergeado em
  2026-09-10T22:13Z, já na `main`; a issue fechou no mesmo instante — conferido
  com `gh pr view 378` / `gh issue view 375`). A versão anterior deste documento
  a citava como parte da coluna medida, e ela nunca esteve lá.

---

## 1. O mecanismo, e os DOIS sinks

String vinda do usuário com **NUL** (`U+0000`) ou **surrogate solitário**
(`U+D800`–`U+DFFF` sem par) chega a um destino que não a aceita. `json.loads`
aceita os dois (`"\u0000"` e `"\ud800"` são JSON válido), então o veneno
atravessa o parse intacto e só estoura no destino.

São **duas fronteiras diferentes**, e um saneador não cobre a outra:

| sink | NUL | surrogate solitário | onde estoura |
|---|---|---|---|
| **Postgres** (`text`/`jsonb` via psycopg) | `DataError` | `UnicodeEncodeError` | NUL em parâmetro `text`: no adaptador `_StrDumper` do psycopg, antes do envio. Surrogate: **na codificação do parâmetro, antes do envio**; o caminho de `jsonb` depende da serialização |
| **`hash_pii`/`encrypt_pii`** (`core/crypto.py`) | **passa** (HMAC/Fernet aceitam byte zero) | `UnicodeEncodeError` | `.encode("utf-8")` estrito, `core/crypto.py:165` e `:190` |

Medido em 2026-09-10 contra `12d643c`, chamando as funções direto, sem HTTP
(script do **§8.4**):

```
RAISE consume_password_reset_token(NUL)        -> DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes
RAISE consume_password_reset_token(surrogate)  -> UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800'…
OK    hash_pii(NUL)                            -> b61fc7de857…   <- NUL PASSA no hash_pii
RAISE hash_pii(surrogate)                      -> UnicodeEncodeError: … (core/crypto.py:165)
OK    encrypt_pii(NUL)                         -> v1:gAAAAABqo…  <- NUL PASSA no encrypt_pii
RAISE encrypt_pii(surrogate)                   -> UnicodeEncodeError: … (core/crypto.py:190)
```

O que importa nas duas linhas `OK` é o **OK**, não o dígito: o hash depende do
`PII_HASH_PEPPER` e o ciphertext do `PII_ENCRYPTION_KEY` do ambiente, então eles
mudam a cada execução e **não são número para conferir**.

Consequência prática: **`core/pg_text.py` diz de si mesmo que trata "o que o
Postgres aceita"** e o `hash_pii` fica de fora por construção, não por descuido
— o docstring dele é que não descreve essa segunda fronteira.

**A raiz da classe "vaza detalhe de implementação":** `UnicodeEncodeError` **é
subclasse de `ValueError`**. Todo `except ValueError as exc: raise
HTTPException(4xx, detail=str(exc))` captura o erro de codificação e **ecoa a
mensagem interna do CPython** para o cliente. `DataError` **não** é `ValueError`
— por isso o NUL vira 500 e o surrogate vira 400-com-vazamento na mesma rota.
Ver §5.

---

## 2. Tabela de sinks (coluna 1 — AST)

Varredura por AST, ignorando `.venv/`, `tests/`, `mobile/`, `node_modules/`.
**Medido em 2026-09-10 contra `12d643c`**, com o script do **§8.1** — o critério
é `Call` cujo `func.attr/id` seja `hash_pii*`/`encrypt_pii*`/`Jsonb`, `.encode(`
sem `errors=`, ou `.execute(` com 2+ args posicionais:

```bash
PYTHONPATH=. python sinks_ast.py .    # o script está no §8.1
```

| tipo de sink | total | `core/` | `db/` | `db_support.py` | `frontend/` | `scripts/` | outros |
|---|---:|---:|---:|---:|---:|---:|---:|
| `execute` com params | 908 | 93 | 679 | 44 | 37 | 47 | 8 |
| `hash_pii`/`encrypt_pii` | 78 | 5 | 22 | 22 | 7 | 22 | 0 |
| `.encode(` sem `errors=` | 44 | 19 | 12 | 0 | 7 | 0 | 6 |
| `Jsonb(` | 30 | 6 | 24 | 0 | 0 | 0 | 0 |

> **Remeça antes de reusar.** O número que importa não é o total — é *quais*
> desses são alcançáveis sem credencial. Esses estão na §3.
>
> A linha do `.encode(` dizia **36** na primeira versão, e nenhum critério que
> consegui reconstruir devolve 36 — o script do §8.1 devolve **44** (e 31 se
> exigir argumento explícito de codificação). O número foi remedido, não
> defendido: era exatamente o caso de contagem sem comando que o §2 do
> `CLAUDE.md` proíbe. As outras três linhas o script reproduz célula a célula.

### Os sinks alcançáveis SEM credencial (os que esta fatia enumera)

| sink | arquivo:linha | função | chegou por |
|---|---|---|---|
| `select ... from password_reset_tokens where token = %s` | `db_support.py:1124` | `consume_password_reset_token_impl` | `POST /auth/reset-password` |
| `update mfa_login_challenges ... where token = %s` | `db/mfa.py:356` | `consume_login_challenge` | `POST /auth/mfa/verify-login` |
| `select ... from pending_google_signups where token = %s` | `db/google_auth.py:139` | `get_pending_google_signup` | `POST /auth/google/complete-signup` e `GET /auth/google/pending/{token}` |
| `update data_export_tokens ... where token = %s` | `db/privacy.py:132` | `consume_data_export_token` | `GET /auth/account/export/download/{token}` |
| `delete from dashboard_sessions where code = %s` | `db/reports.py:139` | `consume_dashboard_session` | `GET /d/{code}` |
| `select * from affiliates where upper(code) = %s` | `db/affiliates.py:96` | `get_affiliate_by_code` | `GET /r/{code}` |
| `hash_pii` (`.encode` estrito) | `core/crypto.py:165` | `hash_pii` | `POST /auth/{register,login,forgot-password,verify-email}` — **fechado na `leva` pelo #369** |
| `INSERT INTO auth_rate_limits (bucket, identifier, ...)` | `finance_bot_websocket_custom.py` (`2308` em `12d643c`) | `_check_persistent_rate_limit` | idem — **fechado na `leva` pelo #369** |

> **Procure pelo nome, não pelo número.** `frontend/finance_bot_websocket_custom.py`
> é o monólito do app (`wc -l` responde o tamanho; ele não é fato de documentação)
> e o próprio #369 desloca tudo depois da linha 2300 em **+53** (medido entre
> `12d643c` e `62b500d`); um `arquivo:linha` dele apodrece a cada merge. A chave desta tabela e da §5 é o par
> **(arquivo, função)**; o número entre parênteses vale para o commit ao lado e se
> reconfere com `grep -n "def <função>" <arquivo>`. Esta versão do documento nasceu
> de cinco referências medidas numa árvore intermediária que não virou commit
> nenhum — erravam por +53 e ninguém teria como saber contra o quê.

---

## 3. Tabela das rotas anônimas (colunas 2 e 3 — introspecção + sonda)

**Classificação** (medida em 2026-09-10 contra `12d643c`, com o script do
**§8.2**: introspecção de `dashboard.app.routes` + leitura do corpo
desembrulhado com `inspect.unwrap`, porque `@limiter.limit` esconde a função
real). Ele também imprime a lista nominal das 15 anônimas:

```
111 rotas de escrita (POST/PUT/PATCH/DELETE)
  56 com modelo Pydantic (128 campos anotados `str`)
 105 recebem `Request` cru
  10 com parâmetro `str` de query/path
classificacao: auth 83 · ANONIMA 15 · admin 9 · segredo 4
```

Critério de classificação, explícito: `admin` se o corpo/dependências citam
`_get_current_admin`; `auth` se citam `resolve_dashboard_user_id`,
`authorize_dashboard_access`, `_get_current_user` ou `require_pro_feature`;
`segredo` se citam `PROSPECT_API_KEY`, `WEBHOOK_SECRET` ou assinatura;
`ANONIMA` se nenhum dos anteriores. **A primeira rodada errou 8 rotas** por não
conhecer `resolve_dashboard_user_id` (é o que `/api/push/*`, `/onboarding/state`,
`/billing/pix/checkout` e `/api/affiliate/payout` usam) — um classificador de
auth por *lista de nomes* precisa da lista completa, e ela se levanta com grep,
não de memória.

### 3.1 Rotas de ESCRITA anônimas — o que devolvem hoje

Sonda do **§8.3**, rodada duas vezes em 2026-09-10 — uma na árvore deste branch
(`12d643c`) e outra num `git worktree` de `1f1b57b` —, com o MESMO script:
TestClient, CSRF correto, database próprio `sonda321_*` criado e derrubado pela
própria sonda, `dashboard.limiter._storage.reset()` **e** `delete from
auth_rate_limits` a cada caso (resetar só o storage não basta — o teto
persistente derruba a sonda com 429).

Duas armadilhas de método que a sonda cobre e que mudam o resultado: o veneno
entra **dentro do valor legítimo** (`"senhaforte123"` → `"sen\x00haforte123"`),
senão o que se mede é a validação de formato; e `send_email`/senha de admin são
mockados, senão `/contact` responde 502 e `/admin/auth/login` 503 **para
qualquer corpo**, medindo o ambiente em vez da rota.

`✅` = a rota trata; `500` = erro interno; `400⚠` = 400 **com a mensagem interna
da exceção no `detail`**.

| rota | campo | NUL `main` → `leva` | surrogate `main` → `leva` | vaza? |
|---|---|---|---|---|
| `POST /auth/register` | `email` | 500 → **422** | 500 → **422** | não (na `leva`) |
| `POST /auth/register` | `password` | **200 (cadastro pendente de verificação de e-mail)** → 422 | 409⚠ → 422 | `main` vaza no 409 |
| `POST /auth/register` | `phone` | 200 → 422 | 200 → 422 | não |
| `POST /auth/register` | `name` | 500 → 422 | 409⚠ → 422 | `main` vaza no 409 |
| `POST /auth/verify-email` | `email` | 500 → 422 | 500 → 422 | não |
| `POST /auth/verify-email` | `code` | 500 → 422 | 400⚠ → 422 | `main` vaza no 400 |
| `POST /auth/login` | `email` | 500 → 422 | 500 → 422 | não |
| `POST /auth/login` | `password` | 401 → 422 | 401 → 422 | não |
| `POST /auth/forgot-password` | `email` | 500 → 422 | 500 → 422 | não |
| **`POST /auth/reset-password`** | **`token`** | **500 → 422** | **500 → 422** | não |
| `POST /auth/reset-password` | `new_password` | 400 ✅ → 422 | 400 ✅ → 422 | não |
| **`POST /auth/mfa/verify-login`** | **`challenge`** | **500 → 422** | **500 → 422** | não |
| `POST /auth/mfa/verify-login` | `code` | 400 ✅ → 422 | 400 ✅ → 422 | não |
| **`POST /auth/google/complete-signup`** | **`token`** | **500 → 422** | **400⚠ → 422** | **SIM na `main`** |
| `POST /auth/google/complete-signup` | `name`, `phone` | 400 ✅ → 422 | 400 ✅ → 422 | não |
| `POST /contact` | `name`,`email`,`subject`,`message` | 200 → 200 | 200 → 200 | não — ver 3.3 |
| `POST /admin/auth/login` | `username`, `password` | 401 ✅ → 401 ✅ | 401 ✅ → 401 ✅ | não |
| `POST /unsubscribe` | `token` (query) | 400 ✅ → 400 ✅ | 400 ✅ → 400 ✅ | não |
| `POST /auth/logout`, `/auth/refresh`, `/admin/auth/logout` | (sem corpo) | n/a | n/a | não |
| `POST /wa/webhook`, `/webhook` | corpo | 403 (assinatura) | 403 | não — é `segredo`, não anônima |

**As três linhas em negrito eram o defeito desta enumeração.** A versão anterior
marcava `500 → 500` para `reset-password.token` e `mfa/verify-login.challenge` e
`400⚠ → 400⚠` para `google/complete-signup.token`: a coluna da direita era uma
foto de um worktree intermediário anterior à 3ª rodada do #369. Remedido contra
`1f1b57b`: as três respondem **422**, como todas as outras 13.

Na `leva` a coluna inteira é 422 porque a recusa acontece **antes do handler**
(`_CorpoSemVeneno`), então campo legítimo e campo envenenado não se distinguem —
inclusive onde a `main` já respondia certo (`new_password`, `code`, `name`): o
status muda de 400 para 422 nesses casos, e isso é mudança de contrato aceita
pelo PR do #369, não efeito colateral.

O corpo exato do vazamento, medido na `main` (na `leva` esta requisição é 422):

```
POST /auth/google/complete-signup  {"token":"t\ud800x", ...}
  -> 400 {"detail":"'utf-8' codec can't encode character '\ud800' in position 1: surrogates not allowed"}
```

### 3.2 Rotas de LEITURA anônimas com parâmetro `str` — quatro 500 a mais

Não estavam no escopo original ("rotas de escrita"), e é aí que estava metade
do buraco. Mesma sonda do §8.3, mesmas duas colunas, 2026-09-10.

| rota | `%00` no path `main` → `leva` | `%ED%A0%80` (surrogate) `main` → `leva` | sink |
|---|---|---|---|
| **`GET /d/{code}`** (magic link do bot) | **500 → 401 ✅** | 401 ✅ → 401 ✅ | `db/reports.py:139` |
| **`GET /r/{code}`** (link de afiliado) | **500 → 302 ✅** | 302 ✅ → 302 ✅ | `db/affiliates.py:96` |
| **`GET /auth/account/export/download/{token}`** | **500 → 410 ✅** | 410 ✅ → 410 ✅ | `db/privacy.py:132` |
| **`GET /auth/google/pending/{token}`** | **500 → 404 ✅** | 404 ✅ → 404 ✅ | `db/google_auth.py:139` |
| `GET /i/{code}` (link de prospecção) | 302 ✅ → 302 ✅ | 302 ✅ → 302 ✅ | protegido — ver abaixo |
| `GET /blog/{slug}` | 302 ✅ → 302 ✅ | 302 ✅ → 302 ✅ | — |

Os quatro 500 caem no status que a rota **já dava** para "não existe", nunca num
422 novo — é a decisão da §4.1, e foi assim que `fix/321-rotas-anonimas-500`
(`1f1b57b`) os fechou.

**Por que o surrogate não morde no path e morde no corpo:** o decodificador de
URL do Starlette normaliza `%ED%A0%80` antes de o parâmetro chegar ao handler;
o `json.loads` do corpo **preserva** `"\ud800"` como surrogate solitário. Ou
seja: em path param só o NUL é alcançável; no corpo JSON, os dois.

**`/i/{code}` × `/r/{code}` são gêmeas que divergiram** — e a proteção que falta
já existe no repositório (`CLAUDE.md` §0.1):

- `db/prospects.py:25` — `is_valid_prospect_code` faz `_CODE_RE.fullmatch`
  **antes** de qualquer SQL. `/i/` responde 302 com NUL.
- `db/affiliates.py:30` tem o `_CODE_RE` **idêntico**, usado em `create_affiliate`
  (`:52`) e **não** em `get_affiliate_by_code` (`:90-97`) — o caminho de LEITURA
  vai direto ao `cur.execute`. `/r/` responde 500 com NUL.
- Bônus da mesma comparação: `db/affiliates.py:52` usa `.match`, e
  `db/prospects.py:23` documenta por que trocou para `.fullmatch`. Medido:
  `_CODE_RE.match("ABC12345\n")` → `True`; `.fullmatch(...)` → `False`.

### 3.3 `/contact` — não é 500, é perda silenciosa

`core/services/email_service.py:97` tem `except Exception: return False`, então
o veneno **nunca** vira 500. Na sonda o `send_email` está mockado e o resultado
é 200 para os quatro campos. **NÃO MEDIDO**: o que acontece com a Resend real —
pelo código, ou o e-mail sai com o byte podre, ou a exceção é engolida e a rota
responde **502** ("Não conseguimos enviar agora"), perdendo a mensagem do
usuário. Precisa de `RESEND_API_KEY` e rede, que este ambiente não tem.

### 3.4 A classe app-wide que o #369 fechou de carona

O handler padrão de `RequestValidationError` do FastAPI ecoa o campo `input`.
Quando o erro é `type: "missing"`, o `input` é o **corpo inteiro** — então um
surrogate em QUALQUER campo estoura a serialização e o **422 vira 500, em
qualquer rota do app com modelo Pydantic**, tenha ela validação de veneno ou
não. Medido, 2026-09-10, na mesma sonda do §8.3:

| corpo | rota | `main` | `leva` |
|---|---|---|---|
| `{"token":"a\ud800b"}` (falta `new_password`) | `/auth/reset-password` | **500** | 422 |
| `{"challenge":"a\ud800b"}` (falta `code`) | `/auth/mfa/verify-login` | **500** | 422 |
| `{"token":"a\ud800b"}` (faltam `name`,`phone`) | `/auth/google/complete-signup` | **500** | 422 |
| `{"email":"a\ud800b"}` (falta `password`) | `/auth/login` | **500** | 422 |
| `{"token":NaN}` (família #310) | `/auth/reset-password` | **500** | 422 |
| `{"token":"a\u0000b"}` (falta `new_password`) | `/auth/reset-password` | 422 | 422 |
| `{"token":"ab"}` — **controle limpo** | `/auth/reset-password` | 422 | 422 |

O NUL passa neste caminho porque `json.dumps` o serializa sem problema — é só o
surrogate (e o não finito, e o aninhamento fundo) que estoura na resposta.

**Contrato preservado, medido:** no caminho feliz o 422 continua com `input`
(`{"detail":[{"type":"missing","loc":["body","new_password"],"msg":"Field
required","input":{"token":"ab"}}]}`, idêntico nas duas colunas — a sonda do
§8.3 imprime `input=True` nas duas). Só o ramo de falha responde sem `input`.

### 3.5 Estado e amplificação

- **Não há envenenamento de estado.** Requisição limpa logo depois de uma
  envenenada funciona normalmente (medido na `main`: 500 → 400/401 na sequência,
  mesmo processo). Cada request abre a própria conexão, então a transação
  abortada não vaza para a próxima. **Não há comando colado para este**: era um
  script de uma sequência de duas requisições, e a afirmação vale como leitura de
  arquitetura mais do que como número — trate-a como não remedível sem refazê-la.
- **Cada 500 anônimo grava uma linha em `system_event_logs`** pelo
  `admin_error_logging_middleware`, com `message = "PostgreSQL text fields
  cannot contain NUL (0x00) bytes"`. Medido na `main`: 25 `GET /d/abc%00N` →
  **25/25 em 500 e 25 linhas em `system_event_logs`**. E `GET /d/{code}` **não
  tem `@limiter.limit`** (medido: `"limiter.limit" in inspect.getsource(...)` é
  `False`). É o pior da lista: anônimo, sem teto, e escreve no banco a cada tiro.
  Na `leva` a inundação some junto com o 500 — a medição de 25 tiros nas duas
  colunas está no corpo do commit `1f1b57b` (25 → 0 linhas novas), e o teste que
  a prende é `tests/test_rotas_anonimas_venenosas.py`, versionado naquele branch.

### 3.6 As duas âncoras da issue — CONFIRMADAS

Medidas de novo em 2026-09-10 contra `12d643c`, **resultado idêntico ao que a
issue registrava**. Esta tabela tem **uma coluna só, de propósito**: a `leva` não
toca nenhum arquivo do caminho destas duas rotas — `git diff --name-only 12d643c
1f1b57b` devolve `core/pg_text.py`, `db/{affiliates,google_auth,privacy,reports}.py`,
o monólito e dois testes, e nem `frontend/routes/pockets.py` nem o caminho de
`/admin/api/affiliates` estão aí (`core/admin_dashboard.py` só importa
`limpa_para_pg`, que a leva não altera). Isso é **leitura de diff, não medição**:
para afirmar o status das âncoras na `leva` seria preciso rodá-las lá.

| caso | limpo | NUL | surrogate |
|---|---|---|---|
| `POST /pockets/{user_id}` (autenticado) | 200 | **500** | **400 com `"'utf-8' codec can't encode character '\ud800' in position 5: surrogates not allowed"`** |
| `POST /admin/api/affiliates` `email` (admin) | 404 | 404 ✅ | **500** |
| `POST /admin/api/affiliates` `code`/`plan` | — | — | 404/422 ✅ (regex e whitelist seguram) |

Uma ressalva sobre reprodutibilidade: a primeira tentativa de reproduzir a
âncora 1 deu **403 `pro_required`** nos três venenos, porque a caixinha limpa do
caso anterior já tinha consumido o limite de 1 do plano. Um caso positivo que
consome cota **mascara** os negativos seguintes — a sonda criava um `user_id`
novo por caso.

**Esta tabela não tem comando colado no §8** — ela e a §3.5 são as duas exceções
do documento. A sonda das âncoras precisa de sessão autenticada e de admin
configurado, e não foi reescrita nesta revisão. Enquanto ela não estiver no §8,
trate estes três valores como **não remedíveis sem refazer a sonda**: é uma
medição registrada, não um número que alguém confere em um comando.

---

## 4. Política recomendada, por tipo de campo

| tipo de campo | política | evidência medida |
|---|---|---|
| **identificador de corpo** (e-mail, telefone, `code`, `token`, chave Pix) | **recusar 422 na borda** | Sanear inventa identidade: `"<token-real>\x00"` com o NUL apagado **casa com a linha de verdade** (é o argumento que `core/pg_text.py` já escreve para o id da Pluggy). `recusa_veneno` já existe e faz isso; nenhum e-mail/token legítimo tem NUL ou surrogate — o navegador não os produz. |
| **campo livre curto reenviável** (`name` de caixinha, `subject`, `message`) | **recusar 422** | Medido: `POST /pockets` com `name="cofre\x00x"` → 500 hoje. Gravar `cofre�x` no que a pessoa digitou é pior que dizer "caractere inválido", e o campo está na tela para ser reenviado. |
| **blob forense / log / payload de terceiro** (`details`, `message`, `note`, webhook) | **sanear** com `limpa_para_pg` | É o que o #320 fez. Perder a linha inteira é pior que perder um byte: sem o `item/error` da Pluggy o usuário não sabe que precisa reconectar o banco. |
| **resposta de erro** | **sanear/omitir** | Não é dado do usuário, é eco de diagnóstico. É o que o #369 fez ao montar o 422 sem `input`. |
| **identificador de PATH em rota de navegação** (`/r/{code}`, `/d/{code}`, `/i/{code}`) | **tratar como inválido no formato, NÃO 422** | ⚠️ **discordo do Arquiteto aqui.** Ver abaixo. |

### 4.1 Onde a recomendação do Arquiteto não se sustenta

A linha "identificador → recusar 422" está certa para **corpo JSON** e errada
para **path param de rota de navegação**, e a medição mostra por quê:

- `GET /r/{code}` responde **302 para código inexistente de propósito** — o
  docstring diz "inválido → só redireciona (sem vazar se o código existe)". Um
  422 para o código envenenado criaria um **oráculo novo**: 422 = formato
  recusado, 302 = formato aceito. Trocaria um 500 por um vazamento de forma.
- A resposta certa já está implementada uma pasta ao lado: `/i/{code}` valida
  com `_CODE_RE.fullmatch` **antes** do SQL e devolve 302 igual. Medido: `/i/`
  com `%00` → 302; `/r/` com `%00` → 500. **A correção de `/r/` é reusar o
  `_CODE_RE` que já está em `db/affiliates.py:30`** — não é validação nova, é
  chamar a que o próprio arquivo já tem (`CLAUDE.md` §0.1).
- Mesma lógica para `GET /d/{code}`, `/auth/account/export/download/{token}` e
  `/auth/google/pending/{token}`: esses três já têm uma resposta "não existe"
  desenhada (401 com página, 410, 404). O veneno deve cair nela, não num status
  novo.

### 4.2 Por que um `except psycopg.DataError` genérico numa camada só continua fora

Confirmado por medição, e por dois motivos independentes:

1. **Ele não vê o surrogate.** O `UnicodeEncodeError` sai de
   `psycopg/types/string.pyx` — na codificação do parâmetro, **antes** de o
   Postgres receber qualquer coisa. Não é `DataError`, é `ValueError`.
2. **Ele não vê o `hash_pii`.** `core/crypto.py:165` estoura sem psycopg
   nenhum no caminho — e o NUL nem estoura ali (medido: `hash_pii('a\x00')`
   devolve hash). Um catch de `DataError` deixaria `hash_pii` inteiramente
   descoberto e ainda daria a sensação de categoria fechada.

**O que NÃO consegui reproduzir** do argumento original: a parte "transforma
NUL em 400 depois de a transação já estar abortada". Nesta aplicação a conexão
é por requisição e a seguinte funciona normalmente (§3.5). O argumento
continua valendo pelos motivos 1 e 2, que estão medidos; o da transação
abortada não está, e não deve ser usado como se estivesse.

---

## 5. A classe "vaza detalhe de implementação"

Todo `except` que captura `ValueError` (direto ou via `Exception`) e faz
`detail=str(exc)` **ecoa a mensagem do `UnicodeEncodeError`** — porque
`UnicodeEncodeError` é subclasse de `ValueError`. Varredura por AST, 2026-09-10
contra `12d643c`, com o script do **§8.5** (o critério é o TIPO CAPTURADO, não o
texto, para não perder os `detail=f"...{exc}"`): **21 sites**, e o script imprime
os 21 com arquivo, linha, tipo, status e função.

**A chave desta tabela é (arquivo, função)**; a coluna `linha` vale **só em
`12d643c`** e se reconfere rodando o script. Quatro das cinco referências ao
monólito estavam erradas em +53 linhas na primeira versão deste documento, por
terem sido medidas num worktree que nunca virou commit — e o próprio
`fix/369-corpo-venenoso-auth` desloca esse arquivo outra vez. Número de linha de
`finance_bot_websocket_custom.py` **não sobrevive a um merge**; nome de função
sobrevive.

| arquivo | função | linha em `12d643c` | captura | status | alcance |
|---|---|---|---|---|---|
| `frontend/finance_bot_websocket_custom.py` | `auth_google_complete_signup` | 4091 | `ValueError` | 400 | **ANÔNIMA — vaza na `main`**; na `leva` o veneno não chega |
| `frontend/finance_bot_websocket_custom.py` | `auth_register` | 2838 | `ValueError` | 400 | anônima; fechado na `leva` pelo #369 |
| `frontend/finance_bot_websocket_custom.py` | `auth_register` | 2859 | `ValueError` | 409 | idem — **vaza na `main`** (§3.1) |
| `frontend/finance_bot_websocket_custom.py` | `auth_verify_email` | 2889 | `ValueError` | 400 | idem — **vaza na `main`** (§3.1) |
| `frontend/routes/pockets.py` | `create_pocket_route` | 79 | `ValueError` | 400 | autenticada — **é a âncora 1, vaza hoje** |
| `frontend/routes/pockets.py` | `update_pocket_meta_route` | 131 | `ValueError` | 400 | autenticada |
| `frontend/finance_bot_websocket_custom.py` | `auth_delete_account` | 3801 | `ValueError` | 400 | autenticada |
| `frontend/finance_bot_websocket_custom.py` | `create_launch_route` | 6403, 6444, 6491 | `ValueError` | 400 | autenticada |
| `frontend/finance_bot_websocket_custom.py` | `create_launch_route` | 6446, 6493 | `Exception` | 500 | autenticada — `f"...{exc}"` |
| `frontend/finance_bot_websocket_custom.py` | `export_email` | 6988 | `ValueError` | 400 | autenticada |
| `frontend/routes/cards.py` | `pay_bill_route` | 712 | `Exception` | 500 | autenticada — `f"...{exc}"` |
| `frontend/routes/affiliates.py` | `affiliate_request_payout` | 138 | `ValueError` | 400 | autenticada |
| `frontend/routes/settings.py` | `update_security_contact_route` | 305 | `ValueError` | 400 | autenticada |
| `frontend/routes/open_finance.py` | `open_finance_pluggy_item_route` | 1460 | `ValueError` | 400 | autenticada |
| `core/admin_dashboard.py` | `admin_api_user_set_plan`, `admin_affiliate_create`, `admin_affiliate_set_status`, `admin_affiliate_payout_pix` | 1839, 2080, 2108, 2146 | `ValueError` | 400/422 | admin — `fix/372-payout-sem-mandato` mexe neste arquivo e vai deslocar estas quatro |

**Nota de método:** um grep por `detail=str(exc)` acha 37 linhas, mas 16 delas
estão em `except` que **não** captura `ValueError` (`CoberturaJaPaga`,
`PluggyUnavailable` etc.) e não são desta classe. O grep serve para localizar;
quem decide é o tipo capturado — e é o que o §8.5 faz.

---

## 6. O que ficou de fora, e por quê

- **Rotas autenticadas (83 de escrita) e de admin (9).** Decisão do dono: esta
  fatia é a anônima. As duas âncoras da issue (§3.6) entram como âncora, não
  como escopo.
- **`Jsonb` de resposta da API da Pluggy** — `db/open_finance.py:1181` e `:1212`,
  `core/services/pluggy.py:90` e `:106`. Mesma classe, outra fronteira de
  confiança (dado de terceiro, não do usuário). A própria #321 diz que vira
  issue própria.
- **`/api/prospect/status`** (`codes: list[str]`) — é `segredo`
  (`X-Prospect-Key`), não anônima. Vale registrar porque encosta no teto
  documentado do `recusa_veneno`: ele só olha campo `str` de primeiro nível, e
  `list[str]` passa. Não é alcançável sem a chave.
- **`/wa/webhook`, `/webhook`, `/billing/webhook`, `/billing/asaas/webhook`,
  `/open-finance/pluggy/webhook`** — `segredo`. Medido: `/wa/webhook` responde
  403 (assinatura) antes de olhar o corpo.

---

## 7. O que NÃO foi verificado — com todas as letras

- **Nada foi medido em produção nem no aparelho.** Tudo é TestClient + Postgres
  local (`pigbank_ci_test` como base, cada sonda num database `sonda_*` próprio,
  derrubado no fim).
- **`/contact` com a Resend real** (§3.3): não medido — falta `RESEND_API_KEY` e
  rede. O 502 é leitura de código, não medição.
- **A suíte não foi rodada.** Este documento não altera código; o que prende o
  comportamento da coluna `leva` são os testes que viajam nos próprios PRs
  (`tests/test_auth_corpo_venenoso.py` no #369, `tests/test_rotas_anonimas_venenosas.py`
  no #321), e é lá que a suíte tem de estar verde — não aqui.
- **A coluna `main` é `12d643c`**, que em 2026-09-10 era `origin/main`
  (`git rev-list --count HEAD..origin/main` → 0, depois de `git fetch`). Isso vale
  para aquele instante: a `main` mexe durante a sessão, e remedir começa por
  refazer essa conta.
- **A coluna `leva` é `1f1b57b`, que não está na `main`.** Ela mede dois PRs
  ainda abertos; se a revisão mudar qualquer um deles, a coluna deixa de valer e
  não existe conserto automático — tem de rodar a sonda de novo no novo commit.
- **A §3.6 não foi medida na `leva`** — é leitura de `git diff` (nenhum arquivo do
  caminho mudou), não medição.
- **A §3.5 (estado) e a §3.6 (âncoras) não têm comando colado.** São as duas
  exceções: para remedir, tem de reescrever a sequência de duas requisições e a
  sonda autenticada. Todo o resto do documento tem script no §8.
- **Concorrência não foi atacada.** Duas requisições envenenadas simultâneas não
  foram medidas; o que foi medido é a sequência (§3.5).
- **A sonda do §8.3 dispara DOIS venenos** (`\u0000` e `\ud800`); o surrogate
  baixo (`\udc00`) só aparece nos testes versionados da leva
  (`tests/test_auth_corpo_venenoso.py`, `tests/test_rotas_anonimas_venenosas.py`).
  Pares de surrogate válidos, `U+FFFE`, `U+FEFF` (BOM), byte não-UTF-8 cru no corpo
  e sequências overlong **não** entraram nesta varredura.
- **A enumeração é de rotas HTTP.** O WhatsApp (`handle_incoming`), os jobs e os
  consumidores de fila não foram tocados — e o WhatsApp é justamente onde
  entrada real e esquisita chega.

---

## 8. As sondas, coladas

Nenhuma delas é versionada (nada de sonda em `tests/` nem em `scripts/`) — é
exatamente por isso que elas estão **aqui dentro**. Número cujo "comando" é a
frase "rodei um script descartável" não se remede: a primeira versão deste
documento citava `sonda/t321/*.py` e `scripts_descartavel/sinks_ast.py`, dois
caminhos para o mesmo arquivo inexistente.

Todas rodam de cópia e cola, **da raiz do repositório**, com o interpretador do
projeto. As que sobem o app precisam de `DATABASE_URL` (um Postgres onde o papel
tenha `CREATEDB`) e das chaves de PII; as de AST não precisam de nada.

```bash
export DATABASE_URL=...            # Postgres descartavel, papel com CREATEDB
export JWT_SECRET=qualquer-coisa-com-32-bytes-para-teste
export PII_AUDIT_DISABLED=1 PII_HASH_PEPPER=qualquer-pepper-com-32-chars-ok!
export PII_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
export PYTHONPATH=.
```

Para medir a coluna `leva`, as mesmas sondas rodam dentro de
`git worktree add /tmp/wt321 1f1b57b --detach` (foi assim que a coluna foi
medida; `git worktree remove /tmp/wt321` no fim).

### 8.1 `sinks_ast.py` — a tabela de sinks do §2

```python
"""Conta os sinks que recusam NUL/surrogate, por area. Uso: python sinks_ast.py <raiz>"""
import ast, collections, pathlib, sys
PULA = {".venv", "tests", "mobile", "node_modules", ".git"}
PII = ("hash_pii", "encrypt_pii")
def area(p):
    t = p.parts[0]
    return t if t in ("core", "db", "frontend", "scripts") else (
        "db_support.py" if p.name == "db_support.py" else "outros")
cont = collections.Counter()
for f in sorted(pathlib.Path(sys.argv[1]).rglob("*.py")):
    rel = f.relative_to(sys.argv[1])
    if PULA & set(rel.parts):
        continue
    for n in ast.walk(ast.parse(f.read_text(encoding="utf-8", errors="replace"))):
        if not isinstance(n, ast.Call):
            continue
        nome = getattr(n.func, "attr", None) or getattr(n.func, "id", None) or ""
        if nome == "execute" and len(n.args) >= 2:
            k = "execute com params"
        elif nome.startswith(PII):
            k = "hash_pii/encrypt_pii"
        elif nome == "encode" and not any(kw.arg == "errors" for kw in n.keywords):
            k = ".encode( sem errors="
        elif nome == "Jsonb":
            k = "Jsonb("
        else:
            continue
        cont[(k, area(rel))] += 1
for k in ("execute com params", "hash_pii/encrypt_pii", ".encode( sem errors=", "Jsonb("):
    linha = {a: cont[(k, a)] for a in ("core", "db", "db_support.py", "frontend", "scripts", "outros")}
    print(f"{k}\ttotal={sum(linha.values())}\t" + "\t".join(f"{a}={v}" for a, v in linha.items()))
```

### 8.2 `rotas.py` — a classificação do §3

Reprodução da revisão: executado em `12d643c`, com FastAPI 0.141.1 e psycopg 3.3.5, em banco local isolado. Resultado: **111 rotas**, **56 modelos**, **128 campos str**, **105 Request**, **10 parâmetros str**; **83 auth / 15 anônimas / 9 admin / 4 segredo**. Estes números descrevem esse commit histórico, não o HEAD atual.

```python
"""Classifica as rotas de ESCRITA por quem consegue chamá-las.
Uso:  DATABASE_URL=... JWT_SECRET=... PYTHONPATH=. python rotas.py"""
import inspect, re
from collections import Counter
from fastapi.routing import APIRoute
from pydantic import BaseModel
import frontend.finance_bot_websocket_custom as dashboard

ESCRITA = {"POST", "PUT", "PATCH", "DELETE"}
# A lista de nomes de auth PRECISA estar completa: sem `resolve_dashboard_user_id`
# a 1a rodada classificou 8 rotas autenticadas como anonimas.
AUTH = re.compile(r"resolve_dashboard_user_id|authorize_dashboard_access|"
                  r"_get_current_user|require_pro_feature|_require_pro\(|authorize_user_access")
ADMIN = re.compile(r"_get_current_admin|ADMIN_DASHBOARD_PASSWORD|_admin_session|require_admin")
SEGREDO = re.compile(r"PROSPECT_API_KEY|_authorize_pluggy_webhook|WEBHOOK_SECRET|verify_token|"
                     r"hub\.verify|asaas.*token|access_token|_verify_signature|signature", re.I)

def deps(d, acc=None):
    acc = [] if acc is None else acc
    for x in d.dependencies:
        if x.call is not None:
            acc.append(getattr(x.call, "__qualname__", str(x.call)))
        deps(x, acc)
    return acc

def rotas(routes):
    # FastAPI pode manter include_router como _IncludedRouter, sem achatá-lo.
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        else:
            router = getattr(route, "original_router", None)
            if router is not None:
                yield from rotas(router.routes)

classes, modelos, campos_str, req_cru, param_str, total = Counter(), 0, 0, 0, 0, 0
for r in rotas(dashboard.app.routes):
    if not isinstance(r, APIRoute) or not (r.methods & ESCRITA):
        continue
    total += 1
    fn = inspect.unwrap(r.endpoint)      # @limiter.limit esconde a funcao real
    try:
        src = inspect.getsource(fn)
    except Exception:
        src = ""
    modelo, str_params = None, []
    for p in inspect.signature(fn).parameters.values():
        if isinstance(p.annotation, type) and issubclass(p.annotation, BaseModel):
            modelo = p.annotation
        elif getattr(p.annotation, "__name__", "") == "str":
            str_params.append(p.name)
    modelos += modelo is not None
    campos_str += sum("str" in str(f.annotation) for f in
                      (modelo.model_fields.values() if modelo else []))
    req_cru += "request: Request" in src
    param_str += bool(str_params)
    blob = src + " " + " ".join(deps(r.dependant))
    classes[("admin" if ADMIN.search(blob) else "auth" if AUTH.search(blob)
             else "segredo" if SEGREDO.search(blob) else "ANONIMA")] += 1
    if not (ADMIN.search(blob) or AUTH.search(blob) or SEGREDO.search(blob)):
        print("ANONIMA", sorted(r.methods & ESCRITA), r.path, fn.__name__, sep="\t")
print(f"{total} rotas de escrita | {modelos} com modelo Pydantic ({campos_str} campos str) | "
      f"{req_cru} recebem Request cru | {param_str} com parametro str")
print(" · ".join(f"{k} {v}" for k, v in classes.most_common()))
```

### 8.3 `sonda_321.py` — as tabelas 3.1, 3.2 e 3.4

```python
"""Sonda das tabelas 3.1, 3.2 e 3.4. Cria um database proprio e o derruba.
Uso:  DATABASE_URL=<postgres com CREATEDB> PYTHONPATH=. python sonda_321.py"""
import json, os, uuid
from urllib.parse import urlsplit, urlunsplit
import psycopg
from cryptography.fernet import Fernet

BASE, NOME = os.environ["DATABASE_URL"], f"sonda321_{uuid.uuid4().hex[:8]}"
with psycopg.connect(BASE, autocommit=True) as c:
    c.execute(f'create database "{NOME}"')
os.environ.update(  # antes de importar o app: modulos leem no import
    DATABASE_URL=urlunsplit(urlsplit(BASE)._replace(path=f"/{NOME}")),
    JWT_SECRET="sonda-321-jwt-secret-com-32-bytes-ok", PII_AUDIT_DISABLED="1",
    PII_ENCRYPTION_KEY=Fernet.generate_key().decode(), PLANS_V2_ENABLED="0",
    PII_HASH_PEPPER="sonda-321-pepper-com-32-chars-ok!", RUN_BACKGROUND_TASKS="0")
try:
    import asyncio
    from fastapi.testclient import TestClient
    from db import init_db, get_conn
    init_db()
    import core.admin_dashboard as admin
    import core.services.email_service as email_service
    import frontend.finance_bot_websocket_custom as dashboard
    asyncio.run(admin.ensure_admin_tables())  # system_event_logs nao vem do init_db
    # Sem estes dois a sonda mede o AMBIENTE e nao a rota: sem RESEND_API_KEY
    # /contact da 502 em qualquer corpo, e sem senha de admin /admin/auth/login da 503.
    async def _ok(*a, **k): return True
    email_service.send_email = _ok
    admin.ADMIN_DASHBOARD_PASSWORD, admin.ADMIN_DASHBOARD_PASSWORD_HASH = "secret-admin", ""

    CSRF, D, NUL, SURR = "sonda-csrf-321", "sonda321.example.com", "\x00", "\ud800"
    CORPOS = {
        "/auth/register": {"email": f"reg@{D}", "password": "senhaforte123",
                           "phone": "+5511999990000", "name": "Fulano"},
        "/auth/verify-email": {"email": f"ver@{D}", "code": "123456"},
        "/auth/login": {"email": f"log@{D}", "password": "senhaforte123"},
        "/auth/forgot-password": {"email": f"esq@{D}"},
        "/auth/reset-password": {"token": "tok-inexistente", "new_password": "senhaforte123"},
        "/auth/mfa/verify-login": {"challenge": "ch-inexistente", "code": "123456",
                                   "use_backup": False},
        "/auth/google/complete-signup": {"token": "tok-inexistente", "name": "Fulano",
                                         "phone": "+5511999990000", "accepted_terms": True},
        "/contact": {"name": "Fulano", "email": f"ctt@{D}", "subject": "oi", "message": "texto"},
        "/admin/auth/login": {"username": "admin", "password": "secret-admin"},
    }

    def cliente():
        c = TestClient(dashboard.app, raise_server_exceptions=False)  # senao o 500 vira excecao
        c.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
        return c

    def post(url, corpo_texto):
        dashboard.limiter._storage.reset()          # teto em memoria
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("delete from auth_rate_limits")   # teto persistente: sem isto, 429
            conn.commit()
        return cliente().post(url, content=corpo_texto,  # texto cru: json.dumps escapa o surrogate
                              headers={"Content-Type": "application/json",
                                       dashboard.CSRF_HEADER_NAME: CSRF})

    def marca(r):
        vaza = "codec can't encode" in (r.text or "")
        return f"{r.status_code}{'/VAZA' if vaza else ''}"

    print("### 3.1 corpo JSON")
    for rota, corpo in CORPOS.items():
        for campo, valor in corpo.items():
            if isinstance(valor, str):
                # veneno DENTRO do valor legitimo: um campo invalido por formato
                # mediria a validacao de formato, nao o veneno
                envenena = lambda v: {**corpo, campo: (f"a{v}b@{D}" if campo == "email"
                                                       else valor[:3] + v + valor[3:])}
                print(rota, campo, *(marca(post(rota, json.dumps(envenena(v))))
                                     for v in (NUL, SURR)), sep="\t")
    for nome, v in (("nul", "a%00b"), ("surrogate", "a%ED%A0%80b")):
        print("/unsubscribe", f"token({nome})",
              marca(cliente().post(f"/unsubscribe?uid=1&token={v}",
                                   headers={dashboard.CSRF_HEADER_NAME: CSRF})), sep="\t")

    print("### 3.2 path param")
    for tpl in ("/d/{}", "/r/{}", "/i/{}", "/auth/account/export/download/{}",
                "/auth/google/pending/{}", "/blog/{}"):
        print(tpl, *(marca(cliente().get(tpl.format(v), follow_redirects=False))
                     for v in ("a%00b", "a%ED%A0%80b")), sep="\t")

    print("### 3.4 campo faltando (o 422 que estoura ao serializar)")
    for texto, rota in [('{"token":"a\\ud800b"}', "/auth/reset-password"),
                        ('{"challenge":"a\\ud800b"}', "/auth/mfa/verify-login"),
                        ('{"token":"a\\ud800b"}', "/auth/google/complete-signup"),
                        ('{"email":"a\\ud800b"}', "/auth/login"),
                        ('{"token":NaN}', "/auth/reset-password"),
                        ('{"token":"a\\u0000b"}', "/auth/reset-password"),
                        ('{"token":"ab"}', "/auth/reset-password")]:
        r = post(rota, texto)
        tem_input = '"input"' in (r.text or "")
        print(texto, rota, r.status_code, f"input={tem_input}", sep="\t")
finally:
    try:
        from db.connection import close_pool
        close_pool()          # solta as conexoes, senao o DROP e recusado
    except Exception:
        pass
    with psycopg.connect(BASE, autocommit=True) as c:
        c.execute(f'drop database if exists "{NOME}" with (force)')
```

### 8.4 `sinks.py` — os dois sinks do §1, sem HTTP

```python
"""Os dois sinks, chamados direto (sem HTTP).
Uso:  DATABASE_URL=... PII_* ... PYTHONPATH=. python sinks.py"""
from core.crypto import encrypt_pii, hash_pii
from db import consume_password_reset_token

CASOS = [("consume_password_reset_token", lambda t: consume_password_reset_token(t, "x"), "tok"),
         ("hash_pii", hash_pii, "a"), ("encrypt_pii", encrypt_pii, "a")]
for nome, f, arg in CASOS:
    for veneno, rotulo in (("\x00", "NUL"), ("\ud800", "surrogate")):
        try:
            print(f"OK    {nome}({rotulo})\t-> {str(f(arg + veneno))[:26]}")
        except Exception as e:
            print(f"RAISE {nome}({rotulo})\t-> {type(e).__name__}: {str(e)[:72]}")
```

### 8.5 `vaza.py` — os 21 sites do §5

```python
"""Os `except` que capturam ValueError (direto ou via Exception) e ecoam a
excecao no `detail` de uma HTTPException. O criterio e o TIPO CAPTURADO, nao o
texto: um grep por `detail=str(exc)` acha linhas que nao sao desta classe e
perde os `detail=f"...{exc}"`.   Uso:  python vaza.py .   (da raiz do repo)"""
import ast, pathlib, sys

PULA = {".venv", "tests", "mobile", "node_modules", ".git", "scripts"}
for arq in sorted(pathlib.Path(sys.argv[1]).rglob("*.py")):
    if PULA & set(arq.relative_to(sys.argv[1]).parts):
        continue
    arvore = ast.parse(arq.read_text(encoding="utf-8", errors="replace"))
    pai = {f: p for p in ast.walk(arvore) for f in ast.iter_child_nodes(p)}
    for h in ast.walk(arvore):
        if not isinstance(h, ast.ExceptHandler) or not h.name:
            continue
        tipos = {t.id for t in ast.walk(h.type or ast.Pass()) if isinstance(t, ast.Name)}
        if not tipos & {"ValueError", "Exception"}:
            continue
        for c in ast.walk(h):
            if not (isinstance(c, ast.Call) and getattr(c.func, "id", "") == "HTTPException"):
                continue
            det = next((k.value for k in c.keywords if k.arg == "detail"), None)
            if det is None or not any(isinstance(n, ast.Name) and n.id == h.name
                                      for n in ast.walk(det)):
                continue
            fn = h
            while fn in pai and not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = pai[fn]
            st = next((k.value.value for k in c.keywords
                       if k.arg == "status_code" and isinstance(k.value, ast.Constant)),
                      c.args[0].value if c.args and isinstance(c.args[0], ast.Constant) else None)
            print(f"{arq}:{c.lineno}", "|".join(sorted(tipos)), st,
                  getattr(fn, "name", "?"), sep="\t")
```
