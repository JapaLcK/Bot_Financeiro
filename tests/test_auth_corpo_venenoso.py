"""Issue #369 — NUL e surrogate solitário no corpo das rotas de auth ANÔNIMA.

MEDIDO nesta árvore antes do conserto (status por caso, corpo JSON válido,
CSRF correto): `email` com NUL ou surrogate → **500** em `register`, `login`,
`verify-email` e `forgot-password`; `register.name` e `verify-email.code` com
NUL → 500; `register.name`/`register.password` com surrogate → **409 com a
mensagem interna da exceção no `detail`**; `register.password` com NUL →
**200, o cadastro SEGUE**. Na 3ª rodada a varredura da CATEGORIA (§2) achou
mais três anônimas no mesmo 500: `reset-password.token`,
`mfa/verify-login.challenge` e `google/complete-signup.token` — esta última
devolvendo `'utf-8' codec can't encode…` no `detail` do 400.

São DOIS mecanismos, e um conserto só fecha metade:

1. **serialização do 422** (`validation_exception_page_handler`): o handler
   padrão do FastAPI ecoa o `input`, que é o CORPO INTEIRO. Surrogate em
   QUALQUER campo estoura `UnicodeEncodeError` ao renderizar, e o 422 vira 500
   — em qualquer rota do app com modelo Pydantic. São TRÊS venenos pelo mesmo
   `input`: `NaN`/`Infinity`/`-Infinity`/`1e400` (`ValueError`, a família do
   #310) e aninhamento >~1500 níveis (`RecursionError`) davam o MESMO 500 na
   MESMA linha (36 de 36 e 1500/5000). O handler tira o `input` de TODO 422
   antes de delegar — os três venenos viajavam nele —, e o `except` ficou como
   cinto para o que sobra no erro (`tests/test_422_nao_ecoa_corpo.py`).
2. **validação na borda** (`_CorpoSemVeneno`): sem ela o veneno chega ao
   `INSERT` do `_check_persistent_rate_limit` (coluna `text`), ao `hash_pii` e
   ao `consume_password_reset_token`/`mfa_consume_login_challenge`.

CONTROLES NEGATIVOS — os TRÊS, medidos em 2026-09-10 com
`pytest tests/test_auth_corpo_venenoso.py tests/test_422_nao_ecoa_corpo.py`
(136 casos verdes). Remedir antes de reusar o número; cada um pega grupo
DIFERENTE:
  (a) handler de volta ao de antes do #369 (sem a supressão do `input` e sem o
      `except`): **108 vermelhos** — surrogate, não finito e aninhamento viram
      500; os de **NUL** seguem VERDES (NUL serializa).
  (b) `_CorpoSemVeneno` sem o `model_validator` (os bodies voltam a
      `BaseModel`): **55 vermelhos** — os de **NUL** viram 500 (e
      `register.password` volta a 200) e
      `test_veneno_no_login_nao_deixa_rastro_nem_cota` fica vermelho.
  (c) só o `except` removido, com a supressão do `input` de pé: **1 vermelho**,
      e ele está no outro arquivo — com o `input` fora, nenhum veneno que entra
      PELA ROTA chega mais ao ramo.
  Nenhuma deixa o arquivo verde: as peças medem coisas diferentes.

CONTROLE POSITIVO: `test_controle_positivo_login_limpo_401_e_conta_no_bucket` —
um validador que recusasse TUDO passaria em todo o resto e seria pior que o
bug. Exige 401 (credencial errada, caminho vivo), a linha em
`auth_login_events` e os DOIS buckets em 1.

Contrato que MUDA de propósito (aceito pelo dono): a tentativa envenenada morre
na validação e não conta mais no bucket `ip:` — igual ao que já vale para
`{"email": 1}`, que sempre foi 422 sem contar.

O eco do `input` (o corpo inteiro, senha em claro incluída) era o teto
conhecido deste arquivo e FOI FECHADO na categoria, não na instância: o
handler suprime o `input` de TODO 422, de toda rota. Assunto e testes em
`tests/test_422_nao_ecoa_corpo.py`.
"""
import json

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _corpo_json_helpers import (  # noqa: F401  (_admin_tables: fixture autouse)
    _admin_tables,
    _client,
    _post_texto,
)

NUL = "\x00"
SURR_ALTO = "\ud800"
SURR_BAIXO = "\udc00"
VENENOS = [NUL, SURR_ALTO, SURR_BAIXO]
VENENO_IDS = ["nul", "surrogate_alto", "surrogate_baixo"]
# Tokens que o `json` do Python aceita e a RFC 8259 não tem — mesma lista do
# #310 (`tests/test_corpo_json_nao_finito.py`), com `1e400` separado de
# `Infinity` porque são tokens diferentes e um conserto pode cobrir só um.
NAO_FINITOS = ["NaN", "Infinity", "-Infinity", "1e400"]
NF_IDS = ["nan", "infinity", "menos_infinity", "1e400"]

CSRF = "test-csrf-369"
# Domínio próprio: isola as contagens de `auth_login_events` e dos buckets de
# rate limit do resto da suíte sem precisar de fixture de dados.
DOMINIO = "369.example.com"

# TODAS as rotas anônimas com modelo Pydantic, enumeradas varrendo `app.routes`
# e medindo cada uma sem cookie. Fora, medidas: `/contact` (nunca dá 500 — o
# `send_email` não levanta) e `/api/prospect/status` (exige `X-Prospect-Key`,
# recusada antes do banco). Todo o resto responde 401.
CORPOS = {
    "/auth/register": {"email": f"reg@{DOMINIO}", "password": "senhaforte123",
                       "phone": "+5511999990000", "name": "Fulano"},
    "/auth/verify-email": {"email": f"ver@{DOMINIO}", "code": "123456"},
    "/auth/login": {"email": f"log@{DOMINIO}", "password": "senhaforte123"},
    "/auth/forgot-password": {"email": f"esq@{DOMINIO}"},
    "/auth/reset-password": {"token": "tok-inexistente", "new_password": "senhaforte123"},
    "/auth/mfa/verify-login": {"challenge": "ch-inexistente", "code": "123456",
                               "use_backup": False},
    "/auth/google/complete-signup": {"token": "tok-inexistente", "name": "Fulano",
                                     "phone": "+5511999990000", "accepted_terms": True},
}

# Todo campo `str` de todas elas, derivado do corpo e não escrito à mão: rota
# nova entra na matriz sozinha. Não é só o `email`: `password` com NUL
# COMPLETAVA um cadastro (200), e `name`/`code`/`token`/`challenge` davam 500.
CASOS = [(rota, campo) for rota, corpo in CORPOS.items()
         for campo, valor in corpo.items() if isinstance(valor, str)]
CASO_IDS = [f"{rota.rsplit('/', 1)[1]}-{campo}" for rota, campo in CASOS]

# Status EXATO do caminho legítimo com acento/emoji, MEDIDO. Status exato e não
# "!= 422": um 429 ou um 500 também não são 422 e passariam de graça.
ACENTO_ESPERADO = {
    "/auth/register": 200,          # verification_sent
    "/auth/verify-email": 400,      # "Código inválido" — o corpo chegou ao handler
    "/auth/login": 401,             # "E-mail ou senha incorretos"
    "/auth/forgot-password": 200,   # resposta genérica de sempre
    "/auth/reset-password": 400,    # "Link inválido ou expirado"
    "/auth/mfa/verify-login": 400,  # "Sessão MFA expirada"
    "/auth/google/complete-signup": 400,  # token pendente inexistente
}
# Campo de TEXTO LIVRE onde o acento/emoji entra (`token` e `challenge` são
# nossos, nunca têm acento).
ACENTO_CAMPO = {"/auth/register": "name", "/auth/google/complete-signup": "name",
                "/auth/reset-password": "new_password"}


def _post(url: str, corpo: dict):
    """POST com o corpo escrito byte a byte: `json.dumps` escapa o surrogate
    como `\\ud800` e é isso que um cliente hostil manda de verdade."""
    client = TestClient(dashboard.app, raise_server_exceptions=False)
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
    return client.post(
        url,
        content=json.dumps(corpo),
        headers={"Content-Type": "application/json",
                 dashboard.CSRF_HEADER_NAME: CSRF},
    )


def _envenena(rota: str, campo: str, veneno: str) -> dict:
    """Veneno cercado de texto legítimo: um campo que virasse vazio passaria em
    vários asserts de graça."""
    corpo = dict(CORPOS[rota])
    corpo[campo] = f"AAA{veneno}BBB" if campo != "email" else f"a{veneno}b@{DOMINIO}"
    return corpo


def _eventos_de_login() -> int:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from auth_login_events")
            return dict(cur.fetchone())["n"]


def _tentativas(bucket: str, identifier: str) -> int:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select attempts from auth_rate_limits where bucket=%s and identifier=%s",
                (bucket, identifier),
            )
            row = cur.fetchone()
    return int(dict(row)["attempts"]) if row else 0


@pytest.fixture(autouse=True)
def _limites_limpos():
    """Os tetos por IP (`@limiter.limit` em memória) e por e-mail (tabela) são
    compartilhados entre testes: sem reset, o 429 passaria por "não é 500" —
    por isso todo assert daqui é por status EXATO. Mesmo precedente de
    `tests/_corpo_json_helpers.py`."""
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass
    _zera = [f"email:{c['email']}" for c in CORPOS.values() if "email" in c]
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from auth_rate_limits where identifier = any(%s)",
                        (["ip:testclient", *_zera],))
        conn.commit()
    yield


# Grupo 1 — status EXATO 422 em toda a matriz rota × campo × veneno

@pytest.mark.parametrize("veneno", VENENOS, ids=VENENO_IDS)
@pytest.mark.parametrize("rota,campo", CASOS, ids=CASO_IDS)
def test_corpo_venenoso_responde_422(rota, campo, veneno):
    """O assert de vazamento mora AQUI, junto do status, e não num teste
    próprio: sozinho (`"codec" not in texto`) ficava verde com o bug inteiro de
    volta — 500 responde `{"error":"Erro interno..."}`, que não tem a palavra
    (eram 27 testes inertes)."""
    r = _post(rota, _envenena(rota, campo, veneno))
    # 422 exato, nunca "não é 500": 400/409/429 também não são 500 e eram todos
    # respostas ERRADAS aqui (o 409 do register e o 400 do google vazavam a
    # exceção).
    assert r.status_code == 422, r.text
    assert "codec" not in r.text and "surrogates not allowed" not in r.text, r.text


# Grupo 2 — o veneno não deixa rastro nem consome cota

@pytest.mark.parametrize("veneno", VENENOS, ids=VENENO_IDS)
@pytest.mark.parametrize("campo", ["email", "password"])
def test_veneno_no_login_nao_deixa_rastro_nem_cota(campo, veneno):
    """Nenhuma linha nova em `auth_login_events` e nenhum tick no bucket `ip:`:
    o corpo é recusado antes do handler. O par positivo (a rota limpa GERA a
    linha e conta nos DOIS buckets) é o controle positivo abaixo — sem ele, um
    app que nunca logasse nada passaria."""
    antes = _eventos_de_login()
    r = _post("/auth/login", _envenena("/auth/login", campo, veneno))
    assert r.status_code == 422, r.text
    assert _eventos_de_login() == antes
    assert _tentativas("login", "ip:testclient") == 0


# Grupo 3 — controle POSITIVO: o caminho legítimo continua inteiro

def test_controle_positivo_login_limpo_401_e_conta_no_bucket():
    """Um validador que recusasse TUDO deixaria todo o resto do arquivo verde e
    seria PIOR que o bug. Aqui o corpo é limpo e as três coisas têm de valer:
    401 (credencial errada, e não 422), a linha em `auth_login_events`, e os
    DOIS buckets de rate limit em 1."""
    email = CORPOS["/auth/login"]["email"]
    antes = _eventos_de_login()
    r = _post("/auth/login", CORPOS["/auth/login"])
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "E-mail ou senha incorretos."
    assert _eventos_de_login() == antes + 1
    assert _tentativas("login", "ip:testclient") == 1
    assert _tentativas("login", f"email:{email}") == 1


@pytest.mark.parametrize("rota", list(CORPOS), ids=lambda r: r.rsplit("/", 1)[1])
def test_controle_positivo_acento_e_emoji_passam(rota):
    """O saneamento não pode virar filtro de não-ASCII: `João 😀` é nome legítimo
    e e-mail com acento existe (o emoji chega como PAR de surrogates escapados e
    tem de passar). As 7 rotas continuam no status que já respondiam."""
    corpo = dict(CORPOS[rota])
    if "email" in corpo:
        corpo["email"] = f"joão@{DOMINIO}"
    if rota in ACENTO_CAMPO:
        # Comprido de propósito: com 6 caracteres o 400 do `new_password` vinha
        # do teto de 8, sem chegar ao consumo do token.
        corpo[ACENTO_CAMPO[rota]] = "João 😀 senhaforte"
    r = _post(rota, corpo)
    assert r.status_code == ACENTO_ESPERADO[rota], r.text


# Grupo 4 — o achado transversal: a peça 1 não é específica de auth

def test_peca1_segura_o_422_em_rota_sem_o_validador():
    """`InvestmentCreatePayload` NÃO herda de `_CorpoSemVeneno` (é a #321) e o
    surrogate está num campo que a validação ACEITA (`name` é `str`); quem
    falha é o VIZINHO que falta (`rate`). No erro `missing` o `input` é o
    objeto PAI — o corpo inteiro — então o 422 quebrava na serialização com o
    campo culpado limpo. Rota autenticada, e o 422 sai ANTES do 401: validação
    de corpo roda antes do handler.

    O assert de subclasse impede a tautologia do dia em que alguém puser o
    validador neste modelo; o `type == "missing"` impede o `input` de deixar de
    ser o corpo inteiro. As versões anteriores mandavam LISTA onde se espera
    `str` (erro de tipo, o caso que NÃO precisa do handler) e faziam o arquivo
    afirmar uma cobertura que não existia."""
    assert not issubclass(dashboard.InvestmentCreatePayload, dashboard._CorpoSemVeneno)
    r = _post("/investments/1", {"name": f"AAA{SURR_ALTO}BBB", "initial_amount": 10.0})
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["type"] == "missing", r.text


# Grupo 5 — os OUTROS dois venenos que entram pelo mesmo `input`

def _corpo_texto(rota: str, campo: str, token: str) -> str:
    # Corpo à mão: `json.dumps` não produz `1e400` (em Python já é `inf`).
    corpo = dict(CORPOS[rota])
    corpo.pop(campo)
    return json.dumps(corpo)[:-1] + (", " if corpo else "") + f'"{campo}": {token}}}'


@pytest.mark.parametrize("token", NAO_FINITOS, ids=NF_IDS)
@pytest.mark.parametrize("rota,campo", CASOS, ids=CASO_IDS)
def test_nao_finito_no_corpo_responde_422(rota, campo, token):
    """MEDIDO antes do conserto: 36 de 36 eram **500 anônimo**, na MESMA linha
    do surrogate (`ValueError: Out of range float values are not JSON
    compliant` ao renderizar o `input`). Mesma família do #310, que fechou
    estes tokens no `json.loads` do webhook da Pluggy; aqui quem faz o parse é
    o Starlette, então a guarda é na SAÍDA — e o `limpa_para_pg` não serve,
    porque só trata `str`."""
    r = _post_texto(_client(), rota, _corpo_texto(rota, campo, token))
    assert r.status_code == 422, r.text
    assert "JSON compliant" not in r.text, r.text


_FUNDO = "[" * 5000 + "]" * 5000


@pytest.mark.parametrize("texto", [
    '{"email": ' + "[" * 1500 + "]" * 1500 + "}",
    '{"email": ' + _FUNDO + "}",
    '{"email": "a\\ud800b@x.com", "password": "senhaforte123", "fundo": ' + _FUNDO + "}",
], ids=["1500", "5000", "5000_com_surrogate"])
def test_aninhamento_profundo_responde_422(texto):
    """`RecursionError` no `jsonable_encoder` do handler padrão: 100 e 500
    níveis davam 422; 1500 e 5000 davam **500** (medido). O 3º caso junta os
    dois venenos no mesmo corpo — e nenhum dos dois volta, porque os dois
    viajavam no `input`, que o handler suprime antes de delegar."""
    assert _post_texto(_client(), "/auth/login", texto).status_code == 422


def test_controle_positivo_numero_finito_no_corpo_nao_vira_422():
    """Sem ele o grupo acima passaria num app que recusasse TODO número.
    `1e-400` é finito (underflow → 0.0) — mesmo controle do #310."""
    texto = ('{"email": "log@%s", "password": "senhaforte123",'
             ' "n": 123.45, "z": 0.0, "u": 1e-400}' % DOMINIO)
    r = _post_texto(_client(), "/auth/login", texto)
    assert r.status_code == 401, r.text


def test_caminho_limpo_continua_delegado_ao_handler_do_fastapi(monkeypatch):
    """O 422 do caminho comum continua sendo o do FastAPI — MENOS o `input`, que
    o handler tira de todo erro antes de delegar (`tests/test_422_nao_ecoa_corpo.py`).
    Sem este teste, o ramo de conserto poderia virar o caminho normal e inventar
    um formato de erro nosso (mutante medido: um handler que NUNCA chamasse o do
    FastAPI ficava verde).

    O assert é o corpo BYTE A BYTE do que o handler do FastAPI devolveu, e não
    uma lista de campos escrita à mão: assim ele vale igual no pydantic 2.12.5
    (aqui) e no 2.13.5 (o do CI), que acrescenta um campo `url` por erro. O
    espião registra o `exc` que chegou ao handler — é onde se vê que o `input`
    saiu ANTES da delegação, e não que o pydantic deixou de produzi-lo."""
    recebidos, corpos = [], []
    original = dashboard.request_validation_exception_handler

    async def _espiao(request, exc):
        recebidos.append(exc.errors())
        resposta = await original(request, exc)
        corpos.append(resposta.body)
        return resposta

    monkeypatch.setattr(dashboard, "request_validation_exception_handler", _espiao)
    r = _post("/auth/login", {"email": "a@b.com"})  # falta `password`: corpo limpo
    assert r.status_code == 422, r.text
    assert len(recebidos) == 1 and r.content == corpos[0], r.text
    assert [e["type"] for e in recebidos[0]] == ["missing"]
    assert all("input" not in e for e in recebidos[0]), recebidos
