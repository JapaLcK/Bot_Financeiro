"""
tests/test_payment_reminder_lote.py — UMA LINHA RUIM NÃO DERRUBA O LOTE.

`engagement_scheduler.run_engagement_loop` embrulha `check_payment_reminder`
INTEIRA num `except` (`:98-102`). Consequência: toda exceção que escapa do laço
por linha abandona os candidatos **seguintes**, e eles podem sair da janela de
`PAYMENT_REMINDER_WINDOW_DAYS` antes do próximo tick. O dano não é a linha ruim
— é o resto do lote, e é isso que os testes daqui medem (asserção sobre quem
veio DEPOIS, nunca só "não levantou").

A CLASSE é "operação por linha que pode levantar fora do `try` da linha", e
enumerá-la é o ponto deste arquivo. O laço tem estas operações, nesta ordem:

| operação                            | levanta? | protegida por |
|-------------------------------------|----------|---------------|
| `int(row["user_id"])`               | não alcançável — o `select` do funil traz `user_id`, coluna `not null` int | — |
| `user_id in _ACCESS_ALLOWLIST`      | `set.__contains__(int)` não levanta | — |
| `row.get("email_enc")`              | `dict.get` não levanta | — |
| **`decrypt_pii_optional`** (em `_resolver_email`, no ponto do envio) | **SIM** — `RuntimeError` de `core/crypto.py:238` (`InvalidToken`) e de `:115` (env de chave ausente) | `except` próprio |
| `email = row["email"]`              | `KeyError` só se a coluna sair do `select` | — |
| `recent_event_exists`               | **NÃO** — `except Exception` → `False` no CALLEE (`core/observability.py:311`) | o callee, medido em `test_dedupe_indisponivel_*` |
| `_pago_por_outro_caminho`           | sim | `except` próprio (já existia) |
| `lembrete_ainda_vale`            | sim | `except` próprio (já existia) |
| envio + `_wa_lembrete` + log        | sim | `except` próprio (já existia) |

Ou seja: **um buraco só**, e o segundo candidato óbvio já estava fechado do
lado de dentro. O teste do `recent_event_exists` aqui não protege o laço — ele
amarra a DEPENDÊNCIA da decisão de não pôr um `try` lá. Se aquele `except` sair
de `core/observability.py`, este arquivo fica vermelho antes de o lote começar a
morrer em produção.

**Proveniência**: o padrão desprotegido não nasceu neste PR. O
`_check_trial_ending` (`core/services/engagement_scheduler.py:260`) tem o mesmo
`decrypt_pii_optional` fora de `try`, é anterior a esta branch (confirmado com
`git diff 50017dd -- core/services/engagement_scheduler.py`, que só mostra a
chamada nova do lembrete) e **não** foi consertado aqui — §0.3, não widening.
Mesma bala na agulha ao lado, oferecida como trabalho separado.

CONTROLE NEGATIVO DECLARADO — em `core/services/payment_reminder.py`, apague o
`try`/`except` em volta do `decrypt_pii_optional` (voltando ao `email =
decrypt_pii_optional(...)` cru):
    VERMELHO: test_linha_com_email_enc_corrompido_nao_derruba_o_resto_do_lote
    VERDE:    test_lote_sem_defeito_entrega_a_todos, os dois de dedupe, e todo
              o `test_payment_reminder.py` / `_janela.py` / `_revalida.py` — a
              fixture deles não tem `email_enc`, que é o que prova que a
              injeção mede a DECRIPTAÇÃO e não o funil.

CONTROLE POSITIVO: `test_lote_sem_defeito_entrega_a_todos`. Sem ele o arquivo
passaria num código que engolisse tudo e não mandasse nada — o `continue` de um
`except Exception` largo em volta do corpo do laço passaria nos negativos.
"""
from __future__ import annotations

import pytest

import db
from db.connection import get_conn
from test_payment_reminder import _inadimplente, _limpar_eventos, _tick


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    from _billing_grants_helpers import garantir_system_event_logs
    monkeypatch.setenv("PAYMENT_REMINDER_ENABLED", "1")
    monkeypatch.delenv("WA_TEMPLATE_PAYMENT_REMINDER", raising=False)
    garantir_system_event_logs()


def _segunda_conta(uid_base: int) -> int:
    """Um segundo inadimplente, do jeito que o repo já cria conta secundária
    (`db.ensure_user`, como `tests/test_account_reset.py`).

    O `ensure_user` é o que importa para a limpeza: `_conta` insere só em
    `auth_accounts`, e é a linha de `users` que a fixture autouse
    `_auto_cleanup_orphan_users` enxerga — o `delete from users` cascateia o
    `auth_accounts` junto. Sem ele a conta ficaria de resíduo no banco e o
    funil (que varre a base inteira) a devolveria em testes seguintes.
    """
    uid = uid_base + 1
    db.ensure_user(uid)
    _inadimplente(uid, dias=6.5)
    _limpar_eventos(uid)
    return uid


def _espiar_todos(monkeypatch) -> list:
    """Todos os destinos do lembrete neste tick. Ao contrário do `_espia` do
    arquivo irmão (que filtra UMA conta), aqui o teste precisa de duas — e o
    filtro por prefixo `dun-` continua descartando resíduo de outro teste."""
    from core.services import email_service
    destinos: list = []

    def _fake(to, dash=""):
        if str(to).startswith("dun-"):
            destinos.append(to)
        return True

    monkeypatch.setattr(email_service, "send_payment_reminder_email", _fake)
    return destinos


def _corromper_email_enc(uid: int) -> None:
    """Ciphertext que não decifra com a chave configurada — o caso do
    apontamento. Sem prefixo de versão de propósito: cai na versão ATUAL
    (`_parse_versioned`), então a chave usada é a que o `conftest` gerou e o
    `Fernet.decrypt` estoura com `InvalidToken` → `RuntimeError`. Não depende
    de qual é `_CURRENT_VERSION`."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set email_enc = %s where user_id = %s",
                ("naoehumtokenvalido", uid),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)


def _com_defeituoso_primeiro(monkeypatch, uid_ruim: int) -> list:
    """Reordena o lote REAL para a linha ruim vir primeiro.

    O funil não tem `ORDER BY`, então a ordem do `select` não é garantida — sem
    isto o teste seria intermitente e mediria sorte. O envelope chama a função
    REAL e devolve as linhas REAIS: o que muda é só a ordem, que é exatamente a
    condição do apontamento ("valid candidates LATER in the result set").
    Devolve a lista de lotes vistos, para o teste provar que a injeção pegou.
    """
    from db import dunning as db_dunning
    real = db_dunning.list_payment_reminder_candidates
    lotes: list = []

    def _envelope(grace_days):
        rows = sorted(real(grace_days),
                      key=lambda r: 0 if int(r["user_id"]) == uid_ruim else 1)
        lotes.append([int(r["user_id"]) for r in rows])
        return rows

    monkeypatch.setattr(db_dunning, "list_payment_reminder_candidates", _envelope)
    return lotes


def test_linha_com_email_enc_corrompido_nao_derruba_o_resto_do_lote(
        user_id, monkeypatch):
    """NEGATIVO: o `email_enc` que não decifra mata só a própria linha.

    A asserção que importa é sobre a conta BOA, que vem DEPOIS no lote — é o
    dano que o apontamento descreve. "Não levantou" não bastaria: um `try` no
    lugar errado (em volta do laço inteiro) também não levanta, e mesmo assim
    abandona o resto.
    """
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    bom = _segunda_conta(user_id)
    _corromper_email_enc(user_id)
    destinos = _espiar_todos(monkeypatch)
    lotes = _com_defeituoso_primeiro(monkeypatch, user_id)

    _tick()

    assert lotes and lotes[0][0] == user_id, \
        f"a linha ruim não ficou em primeiro — o teste mediria nada: {lotes}"
    assert len(lotes[0]) >= 2, f"o lote não tem a conta boa depois: {lotes}"
    assert f"dun-{bom}@t.local" in destinos, \
        "a conta BOA depois da linha ruim não recebeu o lembrete"
    assert f"dun-{user_id}@t.local" not in destinos, \
        "a linha de decriptação falha virou envio — fallback silencioso"


def test_lote_sem_defeito_entrega_a_todos(user_id, monkeypatch):
    """POSITIVO: as duas contas boas recebem, na mesma ordem e no mesmo tick.

    Sem este caso, um `except` largo que engolisse tudo e não mandasse nada
    passaria no teste acima."""
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    outro = _segunda_conta(user_id)
    destinos = _espiar_todos(monkeypatch)
    lotes = _com_defeituoso_primeiro(monkeypatch, user_id)

    _tick()

    assert lotes and len(lotes[0]) >= 2
    assert f"dun-{user_id}@t.local" in destinos
    assert f"dun-{outro}@t.local" in destinos


# ──────────────────────────────────────────────────────────────────────────────
# O segundo membro da classe, que NÃO precisou de código: `recent_event_exists`
# se defende sozinha. Estes dois casos são a evidência da decisão — não o
# conserto de um bug — e o alarme se o `except` do callee desaparecer.
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("modo", ["conexao_recusada", "url_invalida"])
def test_dedupe_indisponivel_devolve_false_em_vez_de_levantar(monkeypatch, modo):
    """`recent_event_exists` com banco indisponível devolve False e NÃO levanta.

    É por isso que a chamada dela no laço do lembrete não tem `try` — e é a
    única razão. Se este teste ficar vermelho, o laço voltou a poder morrer
    numa linha e o `try` passa a ser necessário lá.
    """
    import core.observability as obs
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://nao:existe@127.0.0.1:1/nada" if modo == "conexao_recusada"
        else "isto-nao-e-uma-url")
    assert obs.recent_event_exists("payment_reminder_sent", 1, 6.0) is False


def test_dedupe_com_erro_inesperado_devolve_false(monkeypatch):
    """O mesmo para uma falha que não é de conexão: o `except Exception` do
    callee é LARGO, e é dele que a ausência de `try` no laço depende. Sem este
    caso, os dois acima provariam só o caminho de rede."""
    import core.observability as obs

    def _explode(*a, **k):
        raise ValueError("erro inesperado no driver")

    monkeypatch.setattr(obs.psycopg, "connect", _explode)
    assert obs.recent_event_exists("payment_reminder_sent", 1, 6.0) is False


# ──────────────────────────────────────────────────────────────────────────────
# O LOTE E O EVENT LOOP: a decriptação de PII não pode escrever no banco de
# forma síncrona uma vez POR LINHA.
#
# Cada `decrypt_pii` chama `core.crypto._record_access`, que sem batch faz
# `get_conn` + `execute` + `commit` imediatos (`core/crypto.py:266-280`). Como o
# funil não tem `LIMIT` e o tick roda no event loop único do Uvicorn, era uma
# ida ao banco BLOQUEANTE por candidato, competindo com request e webhook.
#
# Medido antes de escrever o conserto (200 linhas, `pii_access_log` conferida
# com `count(*)` nos dois modos, 2026-09-08 — remeça antes de reusar):
#   solto: 142,5 ms  (0,712 ms/linha, 200 idas ao banco)
#   lote :   9,3 ms  (0,046 ms/linha, 1 `executemany`)  → 15,4x
# e, com a passada indo para o executor, o loop bloqueia ZERO.
#
# O OBSERVÁVEL é (a) a thread em que a decriptação roda e (b) **para QUEM** a
# trilha de auditoria registra acesso. Contar só linhas não mediria nada, e
# contar só a thread deixaria passar um "conserto" que desligasse a auditoria.
#
# **Este teste mudou de forma na rodada 10, e a nota fica porque a razão
# importa.** Até ela, a decriptação era em LOTE (`_decifrar_lote`, N candidatos
# dentro de um `pii_audit_batch`, uma escrita só) e o teste media "buffer de
# batch ativo". O endereço passou a ser lido no ponto do envio — para deixar de
# vir do snapshot do funil —, então não há mais lote e a asserção de buffer
# perdeu objeto. Ela foi SUBSTITUÍDA, não apagada, pela invariante mais forte
# que o desenho novo dá: a trilha registra acesso **só de quem foi contatado**.
# Medido na troca (2026-09-09): lote = 200 linhas de auditoria para 200
# candidatos; ponto de envio = 10 linhas para 10 enviados.
#
# CONTROLE NEGATIVO — em `core/services/payment_reminder.py`, volte a decifrar
# a partir do snapshot (decifre `_linha_do_funil` antes da dedupe, em vez de
# `atual` depois da revalidação):
#     VERMELHO: test_audit_de_pii_so_registra_quem_foi_contatado
#     VERDE:    todo o resto deste arquivo e dos irmãos.
# CONTROLE POSITIVO: a asserção de que a conta CONTATADA tem exatamente uma
# linha de auditoria. Sem ela, `PII_AUDIT_DISABLED=1` passaria no teste.
# ──────────────────────────────────────────────────────────────────────────────

def _cifrar_email(uid: int) -> None:
    """Põe o e-mail da conta em `email_enc` de verdade, para o laço tomar o
    caminho da decriptação (o `_inadimplente` deixa `email_enc = null`)."""
    from core.crypto import encrypt_pii
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set email_enc = %s where user_id = %s",
                (encrypt_pii(f"dun-{uid}@t.local"), uid),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)


def _linhas_de_audit(uids: list[int]) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from pii_access_log"
                " where purpose = 'send_payment_reminder_email'"
                "   and subject_user_id = any(%s)",
                (uids,))
            return cur.fetchone()["n"]


def test_audit_de_pii_so_registra_quem_foi_contatado(user_id, monkeypatch):
    """A decriptação roda FORA da thread do event loop e a trilha de auditoria
    registra acesso ao e-mail **só de quem recebeu o lembrete**.

    Duas contas com `email_enc`: uma elegível e outra já DEDUPADA (recebeu no
    tick anterior). No desenho antigo as duas eram decifradas e as duas viravam
    linha em `pii_access_log`, porque o lote decifrava todo candidato antes dos
    filtros. Agora só a contatada é.

    A suíte roda com `PII_AUDIT_DISABLED=1` (hook do ambiente), então o teste
    liga a auditoria de propósito: com ela desligada `_record_access` retorna na
    primeira linha e não haveria nada para medir.
    """
    import threading

    from core.observability import log_system_event_sync
    from core.services import payment_reminder as pr

    monkeypatch.setenv("PII_AUDIT_DISABLED", "")
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    dedupado = _segunda_conta(user_id)
    _cifrar_email(user_id)
    _cifrar_email(dedupado)
    # A segunda conta já foi lembrada: a dedupe a descarta ANTES do envio, e é
    # justamente ela que o lote decifrava à toa.
    log_system_event_sync("info", "payment_reminder_sent", "tick anterior",
                          source="engagement_scheduler", user_id=dedupado)
    destinos = _espiar_todos(monkeypatch)

    real = pr.decrypt_pii_optional
    na_thread_do_loop: list[bool] = []

    def _espiao(ct, *, ctx):
        na_thread_do_loop.append(
            threading.current_thread() is threading.main_thread())
        return real(ct, ctx=ctx)

    monkeypatch.setattr(pr, "decrypt_pii_optional", _espiao)

    _tick()

    assert na_thread_do_loop, "o caminho da decriptação não foi exercitado"
    assert not any(na_thread_do_loop), \
        "decriptação rodou na thread do event loop — volta a bloquear request"
    assert len(na_thread_do_loop) == 1, \
        ("decifrou mais de uma conta: a dedupada não devia ser decifrada "
         f"({len(na_thread_do_loop)} decriptações)")
    assert _linhas_de_audit([user_id]) == 1, \
        "a trilha de auditoria da conta contatada não foi gravada"
    assert _linhas_de_audit([dedupado]) == 0, \
        "auditou acesso ao e-mail de quem não recebeu nada"
    # E o lembrete continua saindo para a elegível: a troca é de I/O e de
    # minimização, não de comportamento.
    assert f"dun-{user_id}@t.local" in destinos
    assert f"dun-{dedupado}@t.local" not in destinos
