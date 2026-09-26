"""Exclusão definitiva de conta apaga os items na Pluggy (Onda 4, PR-C).

Sem isto, `delete_user_data` não falava com a Pluggy em ponto nenhum: o item
continuava vivo (e pago) lá com os dados bancários do titular DEPOIS de uma
exclusão LGPD, e — como o `user_id` é determinístico a partir do e-mail
(`get_or_create_canonical_user`) e o `avoidDuplicates` da Pluggy é escopado por
`clientUserId` — o recadastro com o mesmo e-mail reconectava o MESMO itemId e
trazia de volta o histórico bancário anterior à exclusão.

CONTROLES DO GRUPO (CLAUDE.md §3), todos injetados em caso que estava VERDE,
em árvore isolada, e restaurados:

- negativo — remover a chamada `remote_cleanup()` de `delete_user_data`
  (a do delete, NÃO a do `reset_user_data`, que fica no mesmo arquivo — mire a
  linha): T1, T4 e T10 vermelhos. T11 fica VERDE e é o certo: sem o 1º passe, o
  2º passe deleta o mesmo item depois do commit, e T11 não afere a ordem;
- negativo — trocar `user_id=None` por `user_id=user_id` nos dois logs novos:
  T4 e T5 vermelhos (T3 NÃO — ver o docstring dele);
- negativo — tirar o `log_user_id=False` das DUAS chamadas de
  `delete_pluggy_items_best_effort` em `db/privacy.py` (a exclusão passa a logar
  o dono na coluna, como o disconnect e o reset): T12 vermelho. O irmão deste
  controle mora em `tests/test_open_finance_disconnect_route.py`
  (`…_com_falha_de_auth_loga_o_dono_na_coluna`), que fica vermelho se o
  `log_user_id=False` vazar para o disconnect — o parâmetro é travado dos dois
  lados;
- negativo — trocar o `if not locked:` de "loga e segue" por `raise`:
  T5 vermelho;
- positivo — T6 (conta sem Open Finance não chama a Pluggy) e T9 (item de
  outro dono nunca é enviado à Pluggy): a PR passa a chamar um serviço externo
  no caminho de exclusão, e sem estes dois o grupo passaria numa versão que
  deleta tudo ou nada.

CLASSE CEGA declarada: nenhum caso aqui fala com a Pluggy de verdade. Erro de
contrato do `DELETE /items/{id}` real (403 por escopo, rate limit, corpo
diferente) não é pego por este arquivo — é a metade B do fatiamento.

Arquivo IRMÃO, outro assunto: `tests/test_account_deletion_pii_logs.py` mede o
resíduo de PII (e-mail e `user_id`) em `system_event_logs` depois da exclusão —
lá nenhum caso toca na Pluggy.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import core.services.pluggy as pluggy_api
import db
import frontend.routes.open_finance as of_routes
from db.connection import get_conn

SENHA = "senha-certa-123"


def _item_de(uid: int) -> str:
    """Id de item na forma REAL da Pluggy: hex opaco, sem nenhuma relação com o
    `user_id` (o `uuid5` é só para ser determinístico dentro do teste).

    A forma anterior (`item-delete-<uid>`) embutia o uid no NOME do item e
    obrigava T12 a mascarar o item antes de varrer `system_event_logs` — e
    máscara é ponto cego: ela apagava da varredura o item E qualquer uid que
    aparecesse colado nele. Sem uid no item, a varredura é literal.
    """
    return f"item-of-{uuid.uuid5(uuid.NAMESPACE_OID, str(uid)).hex}"


_AUTO = object()  # sentinela: `item=None` é "conta SEM Open Finance" (T6)


def _semeia(uid: int, *, item=_AUTO, agendada: bool = True) -> None:
    """O mínimo que ESTA PR discrimina: a conta agendada e vencida + 1 conexão
    Pluggy. Cópia reduzida de `tests/test_account_reset.py::_semeia` (lá a
    semeadura enche 30 tabelas que aqui não separam nenhum desfecho) — não é
    import de outro módulo de teste de propósito.
    """
    from db.users import _hash_password

    db.ensure_account_deletion_columns()
    item = _item_de(uid) if item is _AUTO else item
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts
                  (user_id, email, password_hash, deletion_status,
                   deletion_requested_at, deletion_scheduled_for)
                values (%s, %s, %s, %s,
                        now() - interval '8 days', now() - interval '1 minute')
                """,
                (uid, f"delete-{uid}@t.local", _hash_password(SENHA),
                 "scheduled" if agendada else None),
            )
            if item:
                cur.execute(
                    "insert into open_finance_connections "
                    "(user_id, provider, provider_item_id, status, "
                    " institution_id, institution_name) "
                    "values (%s, 'pluggy', %s, 'UPDATED', '612', 'Nubank')",
                    (uid, item),
                )
        conn.commit()


@pytest.fixture(autouse=True)
def tabelas_admin():
    """`system_event_logs` não nasce de `db/schema.py` — é criada
    preguiçosamente por `core/admin_dashboard.py`. Sem ela o INSERT do log
    falha em silêncio e T2/T3/T4/T5 não mediriam nada (mesmo idioma de
    `tests/_system_event_log_helpers.py:40-46`)."""
    from core.admin_dashboard import ensure_admin_tables

    asyncio.run(ensure_admin_tables())


@pytest.fixture(autouse=True)
def limpa_eventos(user_id):
    """Os logs desta PR vão com `user_id NULL` de propósito — nenhuma cascata os
    leva embora. Limpa pelo item id (único por usuário), nunca por event_type:
    `pluggy_item_delete_failed` é compartilhado com os testes de Open Finance."""
    yield
    with get_conn() as conn:
        conn.execute(
            "delete from system_event_logs where details::text like %s",
            (f"%{_item_de(user_id)}%",),
        )
        conn.commit()


def _mocka_pluggy(monkeypatch, *, erro: Exception | None = None) -> list[str]:
    deletados: list[str] = []

    def _delete(item_id, api_key=None):
        deletados.append(item_id)
        if erro is not None:
            raise erro
        return True

    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(of_routes, "delete_pluggy_item", _delete)
    return deletados


def _marca_de_evento() -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select coalesce(max(id), 0) as m from system_event_logs")
            m = int(cur.fetchone()["m"])
        conn.commit()
    return m


def _eventos(marca: int) -> list[dict]:
    """Eventos novos dos tipos que ESTE caminho escreve, na ordem."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select event_type, user_id, details
                from system_event_logs
                where id > %s
                  and event_type in ('account_deletion_of_lock_busy',
                                     'account_deletion_pluggy_cleanup_failed',
                                     'pluggy_item_delete_failed',
                                     'pluggy_disconnect_auth_failed')
                order by id
                """,
                (marca,),
            )
            linhas = cur.fetchall()
        conn.commit()
    return linhas


def _existe_usuario(uid: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1 from users where id = %s", (uid,))
            achou = cur.fetchone() is not None
        conn.commit()
    return achou


# ── T1 ──────────────────────────────────────────────────────────────────────

def test_t1_exclusao_deleta_o_item_do_usuario_na_pluggy(user_id, monkeypatch):
    """Sem a correção esta lista dá `[]` (a pergunta 1 do §3): hoje a exclusão
    não falava com a Pluggy em ponto nenhum.

    `conexao_viva` pinça a ORDEM do §4 do plano (remoto ANTES do delete local),
    e não só o fato de o item ter sido deletado: sem ela, remover o 1º passe
    deixa este caso VERDE — o 2º passe de `process_due_account_deletions`
    deletaria o mesmo item, só que depois do commit, com o lock já solto.
    """
    _semeia(user_id)
    deletados: list[str] = []
    conexao_viva: list[bool] = []

    def _delete(item_id, api_key=None):
        deletados.append(item_id)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select 1 from open_finance_connections where user_id = %s",
                    (user_id,),
                )
                conexao_viva.append(cur.fetchone() is not None)
            conn.commit()
        return True

    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(of_routes, "delete_pluggy_item", _delete)

    db.process_due_account_deletions(limit=10)

    assert deletados == [_item_de(user_id)], \
        "a exclusão não mandou o item do usuário para a Pluggy"
    assert conexao_viva == [True], \
        "o DELETE remoto tinha que acontecer ANTES do delete local (1º passe)"
    assert not _existe_usuario(user_id), "a conta tinha que ter sido excluída"


# ── T2 ──────────────────────────────────────────────────────────────────────

def test_t2_falha_na_pluggy_nao_impede_a_exclusao_e_deixa_rastro(user_id, monkeypatch):
    _semeia(user_id)
    marca = _marca_de_evento()
    deletados = _mocka_pluggy(monkeypatch, erro=RuntimeError("pluggy fora do ar"))

    db.process_due_account_deletions(limit=10)

    assert deletados == [_item_de(user_id)], "a tentativa remota tinha que ter acontecido"
    assert not _existe_usuario(user_id), \
        "falha remota (best-effort) não podia bloquear uma exclusão LGPD"
    falhas = [e for e in _eventos(marca) if e["event_type"] == "pluggy_item_delete_failed"]
    assert len(falhas) == 1, f"esperava 1 rastro da falha por item, veio {falhas}"
    assert falhas[0]["details"]["item_id"] == _item_de(user_id)


# ── T3 ──────────────────────────────────────────────────────────────────────

def test_t3_o_log_da_falha_sobrevive_a_exclusao(user_id, monkeypatch):
    """Mede o MECANISMO DO BANCO por trás da escolha, não a escolha desta PR.

    O CONTROLE está no próprio caso: a linha `teste_exclusao_com_dono`, escrita
    com `user_id=<uid>` ANTES do delete, SOME (`delete from system_event_logs
    where user_id = %s` é das primeiras instruções da transação, e a FK é
    `on delete cascade`); a de `user_id NULL` fica. É isso que este caso prova.

    NÃO discrimina nenhuma decisão desta PR: o `pluggy_item_delete_failed` que
    ele inspeciona é escrito por `delete_pluggy_items_best_effort`, função que a
    PR não toca e que já gravava sem a coluna `user_id`. Quem trava a escolha dos
    logs NOVOS é T4 e T5; quem trava o `details` do ramo de auth falhada é T12.
    """
    _semeia(user_id)
    item = _item_de(user_id)
    with get_conn() as conn:
        conn.execute(
            "insert into system_event_logs (level, event_type, message, user_id, details) "
            "values ('warning', 'teste_exclusao_com_dono', %s, %s, %s::jsonb)",
            (f"controle de {item}", user_id, '{"item_id": "%s"}' % item),
        )
        conn.commit()

    marca = _marca_de_evento()
    _mocka_pluggy(monkeypatch, erro=RuntimeError("pluggy fora do ar"))

    db.process_due_account_deletions(limit=10)

    assert not _existe_usuario(user_id)
    falhas = [e for e in _eventos(marca) if e["event_type"] == "pluggy_item_delete_failed"]
    assert len(falhas) == 1 and falhas[0]["user_id"] is None, \
        f"o log da falha remota tem que ir com user_id NULL, veio {falhas}"
    assert falhas[0]["details"].get("user_id") is None, \
        "o user_id não pode ir nem em `details` (identificador de conta apagada)"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from system_event_logs "
                "where event_type = 'teste_exclusao_com_dono'"
            )
            com_dono = int(cur.fetchone()["n"])
        conn.commit()
    assert com_dono == 0, \
        "controle: um log COM user_id é apagado pela própria exclusão — " \
        "é por isso que os logs desta PR vão com a coluna NULL"


# ── T4 ──────────────────────────────────────────────────────────────────────

def test_t4_hook_que_levanta_inteiro_nao_bloqueia_a_exclusao(user_id, monkeypatch):
    """O `try/except` em volta do `remote_cleanup()` é ESTRUTURAL: cobre também
    o `ImportError` do import `db/` → `frontend/` feito dentro do hook."""
    _semeia(user_id)
    marca = _marca_de_evento()

    def _hook_quebrado(uid, item_ids=None, *, log_user_id=True):
        # A assinatura ACOMPANHA o helper real: sem o `log_user_id` o stub
        # levantaria `TypeError` antes do corpo e o caso passaria por outro
        # motivo que não o testado.
        raise RuntimeError("helper inteiro fora do ar")

    monkeypatch.setattr(of_routes, "delete_pluggy_items_best_effort", _hook_quebrado)

    db.process_due_account_deletions(limit=10)

    assert not _existe_usuario(user_id)
    agregados = [e for e in _eventos(marca)
                 if e["event_type"] == "account_deletion_pluggy_cleanup_failed"]
    # DOIS, e é o certo: o hook morreu ANTES de enumerar, então `enumerados`
    # fica vazio e TUDO que o DELETE local varreu vai para o 2º passe — a
    # retentativa correta (mesmo raciocínio de frontend/routes/settings.py).
    # O 2º passe usa o mesmo helper quebrado e loga de novo.
    assert len(agregados) == 2, f"esperava 1º passe + 2º passe, veio {agregados}"
    assert all(e["user_id"] is None for e in agregados), \
        f"log de exclusão com dono não sobrevive à exclusão: {agregados}"
    assert all(e["details"]["items"] == [_item_de(user_id)] for e in agregados)


# ── T5 ──────────────────────────────────────────────────────────────────────

def test_t5_lock_ocupado_loga_e_segue(user_id, monkeypatch):
    """É o INVERSO do contrato do reset (que levanta `ResetLockUnavailableError`
    e não toca na Pluggy). Decisão do dono D4: o prazo da LGPD ganha do lock.
    Este caso existe para travar a diferença."""
    from db.open_finance_state import pluggy_item_lock

    _semeia(user_id)
    marca = _marca_de_evento()
    monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", "100")  # lido a cada chamada
    deletados = _mocka_pluggy(monkeypatch)

    with pluggy_item_lock(_item_de(user_id)) as segurei:
        assert segurei, "pré-condição: o teste precisa estar segurando o lock"
        db.process_due_account_deletions(limit=10)

    assert not _existe_usuario(user_id), "lock ocupado não podia adiar a exclusão"
    assert deletados == [_item_de(user_id)], \
        "com o lock ocupado a exclusão SEGUE, inclusive a limpeza remota (D4)"
    ocupados = [e for e in _eventos(marca)
                if e["event_type"] == "account_deletion_of_lock_busy"]
    assert len(ocupados) == 1 and ocupados[0]["user_id"] is None, \
        f"esperava 1 registro de lock ocupado com user_id NULL, veio {ocupados}"
    assert ocupados[0]["details"]["items"] == [_item_de(user_id)]


# ── T6 (positivo) ───────────────────────────────────────────────────────────

def test_t6_conta_sem_open_finance_nao_chama_a_pluggy(user_id, monkeypatch):
    """Sem este caso o grupo passaria numa versão que chama a Pluggy sempre —
    um POST /auth por conta excluída, em toda rodada do cron."""
    _semeia(user_id, item=None)
    marca = _marca_de_evento()

    def _nunca():
        raise AssertionError("conta sem Open Finance não podia pedir apiKey à Pluggy")

    monkeypatch.setattr(of_routes, "create_pluggy_api_key", _nunca)
    monkeypatch.setattr(
        of_routes, "delete_pluggy_item",
        lambda item_id, api_key=None: pytest.fail("nenhum DELETE podia ser tentado"),
    )

    db.process_due_account_deletions(limit=10)

    assert not _existe_usuario(user_id)
    assert _eventos(marca) == [], "conta sem Open Finance não podia gerar evento nenhum"


# ── T7 ──────────────────────────────────────────────────────────────────────

def test_t7_item_ja_apagado_na_pluggy_404_nao_e_falha(user_id, monkeypatch):
    """Com o `delete_pluggy_item` REAL contra um transport falso: 404 conta como
    sucesso (`core/services/pluggy.py`), então a retentativa de uma exclusão
    é idempotente e não polui o painel com falha que não é falha."""
    _semeia(user_id)
    marca = _marca_de_evento()
    chamadas: list[str] = []

    class _Resp:
        status_code = 404

    def _delete(self, url, **kwargs):
        chamadas.append(url)
        return _Resp()

    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(pluggy_api.httpx.Client, "delete", _delete)

    db.process_due_account_deletions(limit=10)

    assert len(chamadas) == 1 and chamadas[0].endswith(f"/items/{_item_de(user_id)}"), \
        f"esperava um DELETE /items/{_item_de(user_id)}, veio {chamadas}"
    assert not _existe_usuario(user_id)
    assert _eventos(marca) == [], "404 é sucesso: não podia virar evento de falha"


# ── T8 ──────────────────────────────────────────────────────────────────────

def test_t8_segunda_rodada_nao_reclama_a_conta_nem_toca_na_pluggy(user_id, monkeypatch):
    _semeia(user_id)
    deletados = _mocka_pluggy(monkeypatch)

    db.process_due_account_deletions(limit=10)
    assert deletados == [_item_de(user_id)]

    resultados = db.process_due_account_deletions(limit=10)

    assert [r for r in resultados if r["user_id"] == user_id] == [], \
        "a conta já excluída não podia ser reclamada de novo"
    assert deletados == [_item_de(user_id)], \
        "a 2ª rodada não podia fazer chamada nenhuma à Pluggy"


# ── T9 (positivo, isolamento) ───────────────────────────────────────────────

def test_t9_item_de_outro_dono_fica_intacto(user_id, monkeypatch):
    """Isolamento por usuário é regra dura (CLAUDE.md §0): a enumeração vem só
    de `list_pluggy_item_ids(user_id)`. Enumerar pelo
    `open_finance_item_registry` (que tem linhas com `user_id NULL` e de outros
    donos do mesmo item) deletaria o banco do vizinho na Pluggy."""
    vizinho = user_id + 1
    db.ensure_user(vizinho)
    item_a, item_b = _item_de(user_id), _item_de(vizinho)
    _semeia(user_id, item=item_a)
    _semeia(vizinho, item=item_b, agendada=False)

    deletados = _mocka_pluggy(monkeypatch)

    db.process_due_account_deletions(limit=10)

    assert deletados == [item_a], \
        f"só o item do dono podia ir para a Pluggy, foram {deletados}"
    assert not _existe_usuario(user_id)
    assert _existe_usuario(vizinho), "o vizinho não estava agendado para exclusão"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from open_finance_connections "
                "where user_id = %s and provider_item_id = %s",
                (vizinho, item_b),
            )
            n = int(cur.fetchone()["n"])
        conn.commit()
    assert n == 1, "a conexão do vizinho tinha que continuar no banco"


# ── T10 ─────────────────────────────────────────────────────────────────────

def test_t10_item_salvo_na_janela_e_deletado_no_segundo_passe(user_id, monkeypatch):
    """Item Pluggy salvo DEPOIS de a limpeza remota enumerar e ANTES do DELETE
    local era varrido do banco sem nunca ser deletado na Pluggy — órfão pago,
    com os dados bancários de uma conta já excluída. O RETURNING do delete diz
    o que foi varrido; o 2º passe deleta o que a enumeração não viu."""
    _semeia(user_id)
    item_velho = _item_de(user_id)
    item_novo = f"{item_velho}-tardio"
    deletados = _mocka_pluggy(monkeypatch)

    # Interleaving determinístico: o save acontece LOGO APÓS a enumeração da
    # limpeza remota (que usa `of_routes.list_pluggy_item_ids`, binding
    # diferente do que `db/privacy.py` usa para pegar os locks).
    real_list = of_routes.list_pluggy_item_ids
    injetado = {"feito": False}

    def _lista_e_injeta(uid):
        itens = real_list(uid)
        if not injetado["feito"]:
            injetado["feito"] = True
            db.save_pluggy_open_finance_item(
                uid, {"id": item_novo, "status": "UPDATED",
                      "connector": {"id": 613, "name": "Inter"}})
        return itens

    monkeypatch.setattr(of_routes, "list_pluggy_item_ids", _lista_e_injeta)

    db.process_due_account_deletions(limit=10)

    assert not _existe_usuario(user_id)
    assert item_velho in deletados, "o item enumerado tinha que sair no 1º passe"
    assert item_novo in deletados, \
        "item salvo na janela ficou órfão na Pluggy depois de uma exclusão LGPD"


# ── T11 (conversa, não função) ──────────────────────────────────────────────

def test_t11_job_ponta_a_ponta_deleta_o_item_e_manda_o_email(user_id, monkeypatch):
    """Ponta a ponta pelo `scripts/account_deletion_job.run`, que é o que o
    Railway Cron executa às 06:00 UTC — o mesmo molde de
    `tests/test_auth_cookie.py::test_account_deletion_job_processes_due_accounts`.
    Cobre a chave nova `pluggy_items_swept` atravessando o retorno do job sem
    atrapalhar o e-mail final."""
    import core.services.email_service as email_service
    from scripts.account_deletion_job import run as run_account_deletion_job

    _semeia(user_id)
    deletados = _mocka_pluggy(monkeypatch)
    enviados: list[str] = []
    monkeypatch.setattr(
        email_service, "send_account_deletion_completed_email",
        lambda to: enviados.append(to) or True,
    )

    assert run_account_deletion_job(limit=5) == 0

    assert enviados == [f"delete-{user_id}@t.local"], \
        f"o e-mail final da exclusão não saiu como esperado: {enviados}"
    assert deletados == [_item_de(user_id)], \
        "a rodada do cron tinha que deletar o item na Pluggy"
    assert not _existe_usuario(user_id)


# ── T12 ─────────────────────────────────────────────────────────────────────

def test_t12_sem_credenciais_pluggy_nao_sobra_user_id_em_log_nenhum(user_id, monkeypatch):
    """O ramo DEFAULT do serviço do cron hoje: sem `PLUGGY_CLIENT_ID`/`SECRET`,
    `create_pluggy_api_key` levanta `PluggyConfigError` e o helper grava
    `pluggy_disconnect_auth_failed` — um log SEM a coluna `user_id`, que por isso
    não é levado pela cascata e sobreviveria para sempre. Antes da correção o
    `user_id` ia em `details` E interpolado no `message`: identificador de conta
    apagada, persistido para sempre, exatamente o que a exclusão existe para
    remover.

    A varredura é sobre `system_event_logs` INTEIRA, não sobre os tipos deste
    caminho: qualquer linha remanescente que cite o `user_id` reprova, sem
    máscara nenhuma — o item id da semeadura tem a forma real da Pluggy
    (`_item_de`) e não carrega o uid."""
    _semeia(user_id)
    marca = _marca_de_evento()
    for var in ("PLUGGY_API_KEY", "PLUGGY_CLIENT_ID", "PLUGGY_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        of_routes, "delete_pluggy_item",
        lambda item_id, api_key=None: pytest.fail("sem apiKey não podia haver DELETE"),
    )

    db.process_due_account_deletions(limit=10)

    assert not _existe_usuario(user_id), \
        "falta de credencial da Pluggy não podia bloquear uma exclusão LGPD"
    auth = [e for e in _eventos(marca) if e["event_type"] == "pluggy_disconnect_auth_failed"]
    assert len(auth) == 1, f"esperava 1 rastro do ramo de auth falhada, veio {auth}"
    assert auth[0]["details"]["items"] == [_item_de(user_id)], \
        "o item é a chave operacional que sobra no lugar do user_id"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, event_type, user_id, message, details::text as det
                from system_event_logs
                where user_id = %s
                   or message like %s
                   or details::text like %s
                """,
                (user_id, f"%{user_id}%", f"%{user_id}%"),
            )
            sobras = cur.fetchall()
        conn.commit()
    assert sobras == [], \
        f"log de conta excluída ficou com o user_id no banco: {sobras}"


# ── T16 (guarda da enumeração) ───────────────────────────────────────────────

def test_t16_enumeracao_sem_dono_e_recusada(user_id):
    """`list_pluggy_item_ids(None)` significava "de TODOS os usuários" e não
    anexava `and user_id=%s`. Esta PR é a primeira a pôr o helper num laço em
    LOTE (`process_due_account_deletions`), e o que o chamador faz com a lista é
    DELETE na Pluggy: um `None` que escapasse apagaria o item de todos os
    clientes. Não era alcançável (os três chamadores passam `int`), mas o
    desfecho não tem conserto — daí a recusa explícita em vez de nota.

    NEGATIVO: apagar o `raise` de `db/open_finance.py` → vermelho (sem ele o
    `None` não levanta nada). POSITIVO: o segundo assert prova que a enumeração
    legítima continua funcionando — uma guarda que recusasse tudo passaria no
    primeiro assert e é pior que o bug.
    """
    _semeia(user_id)

    with pytest.raises(ValueError):
        db.list_pluggy_item_ids(None)

    assert db.list_pluggy_item_ids(user_id) == [_item_de(user_id)], \
        "a enumeração com dono é o caminho legítimo dos três chamadores"


# ── T19 (fronteira do status remoto) ─────────────────────────────────────────

def _status_da_conexao(item_id: str) -> str | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select status from open_finance_connections where provider_item_id = %s",
                (item_id,),
            )
            linha = cur.fetchone()
        conn.commit()
    return linha["status"] if linha else None


@pytest.mark.parametrize("status_remoto, na_coluna", [
    ("PAUSED", "UPDATING"),      # sentinela local vinda do provedor
    ("paused", "UPDATING"),      # a leitura faz `.upper()`, a caixa não protege
    ("DELETED", "UPDATING"),     # a OUTRA sentinela local, e terminal (ver docstring)
    ("MERGE_ERROR", "UPDATING"),  # desconhecido NÃO-sentinela: `executionStatus` fora da lista
    ("UPDATED", "UPDATED"),      # POSITIVO: o status legítimo dos outros testes
    ("LOGIN_ERROR", "LOGIN_ERROR"),  # POSITIVO: status de erro real continua cru
])
def test_t19_status_do_payload_remoto_nunca_vira_a_sentinela_local(
        user_id, monkeypatch, status_remoto, na_coluna):
    """`save_pluggy_open_finance_item` é o ÚNICO ponto que grava status REMOTO, e
    ele gravava o valor cru. `PAUSED` é sentinela LOCAL ("o item já foi deletado
    na Pluggy no fim do trial") e é o valor que TIRA o item do DELETE remoto da
    exclusão de conta — as duas guardas da exclusão leem o mesmo filtro
    (`pluggy_items_a_deletar`), então as duas ficavam cegas juntas e o item seguia
    vivo e pago na Pluggy depois de uma exclusão LGPD (#539 A1).

    NEGATIVO: voltar a fronteira para `item.get("status") or
    item.get("executionStatus") or "UPDATING"` (sem a lista de permissão) →
    os dois primeiros casos vermelhos, nos DOIS asserts.
    POSITIVO: os dois últimos casos são status que os outros testes já usam
    (`tests/test_of_item_ownership.py`) e provam que a fronteira não achata o
    vocabulário legítimo — sem eles o grupo passaria numa versão que gravasse
    `UPDATING` para tudo, que é pior que o bug.

    `DELETED` é a outra sentinela local e é TERMINAL
    (`db/open_finance_state._TERMINAL`): um payload que a gravasse congelaria uma
    conexão viva para o `mark_sync_result` (`where ... not in _TERMINAL`). O preço
    de recusá-la está escrito no bloco de `STATUS_REMOTOS_ACEITOS`
    (`core/services/pluggy_health.py`), junto com a decisão de manter o
    `or item.get("executionStatus")` — e `MERGE_ERROR` é o caso do desconhecido
    NÃO-sentinela: não é vocabulário nosso, não é status de Item, e tem o MESMO
    desfecho de qualquer outro valor fora da lista.

    A sentinela LEGÍTIMA (o único escritor local, `pause_open_finance_connection`)
    continua presa em `tests/test_of_trial_expiry.py`
    (`test_free_expirado_pausa_e_preserva_dados` e
    `test_lister_inclui_ativa_e_exclui_pausada`).
    """
    _semeia(user_id, item=None)   # conta agendada e vencida, zero conexões
    item = f"{_item_de(user_id)}-t19"
    db.save_pluggy_open_finance_item(
        user_id,
        {"id": item, "status": status_remoto, "connector": {"id": 613, "name": "Inter"}},
        criar_usuario=False,   # o caminho da adoção por webhook
    )
    assert _status_da_conexao(item) == na_coluna, \
        f"status remoto {status_remoto!r} chegou errado na coluna"

    deletados = _mocka_pluggy(monkeypatch)
    db.process_due_account_deletions(limit=10)
    assert deletados == [item], \
        (f"item VIVO na Pluggy depois da exclusão LGPD: status remoto "
         f"{status_remoto!r} suprimiu o delete remoto")
