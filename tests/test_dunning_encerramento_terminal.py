"""
tests/test_dunning_encerramento_terminal.py — o ramo TERMINAL do
`customer.subscription.deleted` e o que ele deixa de verdade no banco.

Quando a Stripe encerra a assinatura DE VEZ por inadimplência
(`cancellation_details.reason == 'payment_failed'`, a constante
`billing_dunning.STRIPE_CANCEL_REASON_INADIMPLENCIA`), o ramo grava `unpaid` (o
MOTIVO, que `canceled` apagaria) e limpa o relógio de carência
INCONDICIONALMENTE, por `db.dunning.encerrar_ciclo_de_atraso`. Nos outros
motivos nada muda: `canceled` + `clear_past_due_since` com predicado de versão.

**A configuração que faz este arquivo MEDIR alguma coisa**: com um `deleted`
EM ORDEM (evento mais novo que o relógio) as duas escritas produzem o MESMO
resultado — o predicado `past_due_since <= to_timestamp(created)` é verdadeiro e
a versão antiga limpa igual. Todo caso daqui carimba o relógio DEPOIS do
`created` do evento; é a única configuração em que as duas divergem.

O par (`plan='free'`, `unpaid`) que este ramo grava é lido pelo PAR em dois
lugares, e os dois têm teste em `tests/test_admin_users_panel.py`: o rótulo do
painel (`_derive_account_status` + o espelho SQL, atados por plano) e a guarda
do `/trial-reset`, que LIBERA este estado e continua recusando `unpaid` com
plano pago.

CONTROLES DECLARADOS (`docs/controles_declarados.md`)
────────────────────────────────────────────────────
**Negativo do grupo terminal** — em `finance_bot_websocket_custom.py`, na perna
`if encerramento_por_inadimplencia:`, troque
`encerrar_ciclo_de_atraso(uid)` por
`clear_past_due_since(int(user_id), nao_mais_novo_que=_event_version(event))`.
O predicado volta, nada é apagado. VERMELHOS:
  `test_terminal_apaga_o_relogio[dentro-da-janela-do-lembrete]`  (as três
     asserções caem: relógio, acesso e candidatos)
  `test_terminal_apaga_o_relogio[relogio-de-21-dias]`  (só o relógio; num
     relógio velho a carência já está fechada e o acesso cai de qualquer jeito)
  `test_terminal_nao_deixa_orfao_nem_e_lido_como_gratis`  (o órfão nasce: o
     relógio sobrevive com o status `unpaid` DENTRO da lista)
  `test_payment_failed_depois_do_terminal_nao_devolve_nada`  (a pré-condição
     dele é o relógio limpo pelo terminal)
  `test_encerrar_ciclo_de_atraso_tem_um_unico_call_site`  (a injeção APAGA o
     único call site; o portão exige exatamente um)
Direção: falso POSITIVO de acesso — o relógio sobrevivente reabre a carência e
devolve o app a quem a Stripe acabou de encerrar.

**Negativo do MOTIVO** — em `core/services/billing_dunning.py`, troque
`STRIPE_CANCEL_REASON_INADIMPLENCIA = "payment_failed"` por
`= "nunca_isso"` (troca de VALOR; o critério continua lá e deixa de
discriminar). VERMELHOS (remedido 2026-09-11):
  `test_terminal_apaga_o_relogio[dentro-da-janela-do-lembrete]`
  `test_terminal_apaga_o_relogio[relogio-de-21-dias]`
Direção: falso NEGATIVO do terminal — a Stripe encerra por inadimplência, o
ramo cai no `else`, grava `canceled` e o MOTIVO se perde; o relógio sobrevive
com o predicado de versão em vez de ser apagado.

**Esta instrução estava ERRADA das duas formas, e as duas são o mesmo defeito.**
Ela nomeava `test_motivo_nao_terminal_mantem_o_comportamento_de_hoje` e
`test_deleted_sem_cancellation_details_nao_e_terminal` — que ficam VERDES: não
ser terminal continua não sendo terminal com qualquer valor. E ela era
INAPLICÁVEL, porque até 2026-09-11 os casos daqui e o código liam a MESMA
string (`payment_failure`, que não existe no enum da Stripe): a injeção movia os
dois juntos e não produzia vermelho nenhum. Por isso `_MOTIVO_TERMINAL` é
escrito à mão neste arquivo — ver o comentário dele.

**Negativo da GRAFIA, e é o que teria pego o bug original** — troque o valor da
constante por `"payment_failure"` (a grafia que o webhook usou desde sempre).
VERMELHOS:
  `tests/test_stripe_cancel_reason.py::test_o_motivo_terminal_existe_no_enum_da_stripe`
  `test_terminal_apaga_o_relogio[dentro-da-janela-do-lembrete]`
  `test_terminal_apaga_o_relogio[relogio-de-21-dias]`
Antes deste PR, esse mesmo estado era VERDE no repositório inteiro.

**Negativo do grupo do `payment_failed` atrasado** — ALARGUE, não apague: no
ramo `invoice.payment_failed`, troque a lista contra a qual o status LIVE é
comparado por `list(PAST_DUE_PAYMENT_STATUSES) + ["canceled"]`. VERMELHO:
`test_payment_failed_depois_do_terminal_nao_devolve_nada`.
Este negativo NÃO reprova `test_payment_failed_em_assinatura_viva_continua_valendo`,
e é isso que separa "a guarda discrimina" de "a guarda recusa tudo".
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
from _billing_grants_helpers import garantir_system_event_logs
from core.services.plan_service import has_app_access
from db.connection import get_conn
from db.dunning import list_payment_reminder_candidates
from test_billing_webhook_lifecycle import _T_LIFE, _fake_sub, _post, _setup

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


@pytest.fixture(autouse=True)
def _gate_ligado(monkeypatch):
    """O corte é o default de produção; fixar as duas envs deixa o arquivo
    imune ao `PLANS_V2_ENABLED=0` que o `conftest.py` põe por `setdefault`."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _por_a_conta_em_atraso(uid: int, *, idade_do_relogio: timedelta, status="unpaid"):
    """Estado S3: relógio carimbado há `idade_do_relogio` e status na lista."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set past_due_since=%s, last_payment_status=%s,"
                "       plan='pro', plan_expires_at=%s, plan_selected_at=now()"
                " where user_id=%s",
                (_agora() - idade_do_relogio, status,
                 _agora() - timedelta(days=1), uid),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)


def _relogio(uid: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select past_due_since from auth_accounts where user_id=%s", (uid,))
            return cur.fetchone()["past_due_since"]


# O motivo terminal ESCRITO À MÃO, e nunca importado de
# `billing_dunning.STRIPE_CANCEL_REASON_INADIMPLENCIA`. Importar faria o caso
# DERIVAR da constante e se mover junto com ela: trocar a constante moveria os
# casos daqui e o controle do MOTIVO (declarado acima) ficaria verde — que é
# exatamente como `payment_failure` sobreviveu neste arquivo. A duplicação é
# deliberada e tem o par que o §0.7 exige: `tests/test_stripe_cancel_reason.py`
# ancora a constante no enum do pacote `stripe` instalado.
_MOTIVO_TERMINAL = "payment_failed"


def _evento_deleted(uid: int, *, created: int, reason: str | None):
    detalhes = {"reason": reason} if reason is not None else None
    return {
        "type": "customer.subscription.deleted",
        "id": f"evt_term_{uid}_{created}",
        "created": created,
        "data": {"object": {"id": f"sub_term_{uid}",
                            "metadata": {"finbot_user_id": str(uid)},
                            "cancellation_details": detalhes}},
    }


def _epoch(delta: timedelta) -> int:
    return int((_agora() - delta).timestamp())


# ── 1. reentrega do terminal ────────────────────────────────────────────────

@pytest.mark.parametrize(
    "rotulo,idade_relogio,idade_evento",
    [
        # Deltas ABSOLUTOS, nunca `DUNNING_GRACE_DAYS ± n`: escrito em função da
        # constante, alargar a carência moveria o caso junto com o guard e a
        # injeção ficaria invisível (`docs/controles_declarados.md`).
        #
        # 6,5 dias põe o relógio DENTRO da janela do lembrete ([6, 9) dias), que
        # é o que faz as três asserções discriminarem de uma vez.
        ("dentro-da-janela-do-lembrete", timedelta(days=6, hours=12), timedelta(days=8)),
        # 21 dias é o caso que NÃO se move quando alguém alarga a carência: aqui
        # a carência já está fechada e quem discrimina é só o relógio.
        ("relogio-de-21-dias", timedelta(days=21), timedelta(days=30)),
    ],
    ids=["dentro-da-janela-do-lembrete", "relogio-de-21-dias"],
)
def test_terminal_apaga_o_relogio(user_id, monkeypatch, rotulo, idade_relogio, idade_evento):
    """Reentrega do terminal não devolve acesso nem lembrete.

    O `created` do evento é ANTERIOR ao carimbo do relógio — é a reentrega de um
    `deleted` velho, e a única configuração em que `clear_past_due_since` (que
    compara as duas idades) e `encerrar_ciclo_de_atraso` (que não compara nada)
    dão respostas diferentes.
    """
    uid, client, fake = _setup(monkeypatch, f"term-{user_id}-{rotulo}")
    _por_a_conta_em_atraso(uid, idade_do_relogio=idade_relogio)
    assert _relogio(uid) is not None, "pré-condição: o ciclo tem de estar aberto"

    r = _post(client, fake,
              _evento_deleted(uid, created=_epoch(idade_evento), reason=_MOTIVO_TERMINAL))
    assert r.status_code == 200, r.text

    assert _relogio(uid) is None
    assert has_app_access(uid) is False
    assert uid not in [c["user_id"] for c in list_payment_reminder_candidates(7)]
    # O MOTIVO fica gravado, e o par é o que o painel e o /trial-reset leem.
    conta = db.get_auth_user(uid)
    assert conta["last_payment_status"] == "unpaid"
    assert (conta["plan"] or "free").lower() == "free"


def test_terminal_nao_deixa_orfao_nem_e_lido_como_gratis(user_id, monkeypatch):
    """As duas leituras do PAR, no estado que o ramo acabou de gravar.

    A INVARIANTE (relógio não nulo só com status na lista) fica de pé porque o
    relógio foi apagado; e o painel diz "Cancelado", não "Grátis" — que é o
    motivo inteiro de gravar `unpaid` em vez de `canceled`.
    """
    from core.admin_dashboard import _derive_account_status
    uid, client, fake = _setup(monkeypatch, f"orfao-{user_id}")
    _por_a_conta_em_atraso(uid, idade_do_relogio=timedelta(days=6, hours=12))
    assert _post(client, fake,
                 _evento_deleted(uid, created=_epoch(timedelta(days=8)),
                                 reason=_MOTIVO_TERMINAL)).status_code == 200

    conta = db.get_auth_user(uid)
    assert conta["past_due_since"] is None, "órfão: relógio sobreviveu ao status na lista"
    assert _derive_account_status(dict(conta), _agora()) == "canceled"


def test_motivo_nao_terminal_mantem_o_comportamento_de_hoje(user_id, monkeypatch):
    """Cancelamento a pedido do cliente NÃO é terminal para uma cobrança: grava
    `canceled` e passa pelo clear com predicado, como sempre."""
    uid, client, fake = _setup(monkeypatch, f"nterm-{user_id}")
    _por_a_conta_em_atraso(uid, idade_do_relogio=timedelta(days=6, hours=12))

    r = _post(client, fake,
              _evento_deleted(uid, created=_epoch(timedelta(days=8)),
                              reason="cancellation_requested"))
    assert r.status_code == 200, r.text
    assert db.get_auth_user(uid)["last_payment_status"] == "canceled"
    # `canceled` está FORA de PAST_DUE_PAYMENT_STATUSES, então o `CASE` de
    # set_payment_status_impl já zerou o relógio no mesmo UPDATE.
    assert _relogio(uid) is None
    assert has_app_access(uid) is False


def test_deleted_sem_cancellation_details_nao_e_terminal(user_id, monkeypatch):
    """Ausente e desconhecido caem na perna que NÃO apaga dado — default seguro."""
    uid, client, fake = _setup(monkeypatch, f"ndet-{user_id}")
    _por_a_conta_em_atraso(uid, idade_do_relogio=timedelta(days=6, hours=12))
    r = _post(client, fake,
              _evento_deleted(uid, created=_epoch(timedelta(days=8)), reason=None))
    assert r.status_code == 200, r.text
    assert db.get_auth_user(uid)["last_payment_status"] == "canceled"


# ── 2. `payment_failed` atrasado, depois do terminal ────────────────────────

def _evento_failed(uid: int, sub_id: str, *, created: int):
    return {"type": "invoice.payment_failed", "id": f"evt_late_{uid}_{created}",
            "created": created,
            "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                "subscription": sub_id, "attempt_count": 3}}}


def test_payment_failed_depois_do_terminal_nao_devolve_nada(user_id, monkeypatch):
    """Um `payment_failed` que a Stripe reentrega DEPOIS de já ter encerrado a
    assinatura não devolve acesso, nem carimbo, nem lembrete.

    Quem fecha isso é a guarda (a) do ramo: ela consulta o status LIVE da
    assinatura, que agora é `canceled` — fora de PAST_DUE_PAYMENT_STATUSES.
    """
    uid, client, fake = _setup(monkeypatch, f"late-{user_id}")
    _por_a_conta_em_atraso(uid, idade_do_relogio=timedelta(days=6, hours=12))
    # 1) o terminal chega e encerra tudo
    assert _post(client, fake,
                 _evento_deleted(uid, created=_epoch(timedelta(days=8)),
                                 reason=_MOTIVO_TERMINAL)).status_code == 200
    assert _relogio(uid) is None and has_app_access(uid) is False

    # 2) e SÓ ENTÃO o falho atrasado
    r = _post(client, fake, _evento_failed(uid, f"sub_term_{uid}", created=_T_LIFE),
              subs={f"sub_term_{uid}": _fake_sub("canceled")})
    assert r.status_code == 200, r.text

    assert _relogio(uid) is None
    assert has_app_access(uid) is False
    assert uid not in [c["user_id"] for c in list_payment_reminder_candidates(7)]


def test_payment_failed_em_assinatura_viva_continua_valendo(user_id, monkeypatch):
    """POSITIVO DO GRUPO: o MESMO evento numa conta com assinatura de fato viva
    em `unpaid` continua carimbando e continua elegível ao lembrete.

    Sem ele, o grupo passaria num código que recusa TODO `payment_failed` — que
    é pior que o bug.
    """
    uid, client, fake = _setup(monkeypatch, f"viva-{user_id}")
    _por_a_conta_em_atraso(uid, idade_do_relogio=timedelta(days=6, hours=12))
    relogio_antes = _relogio(uid)

    r = _post(client, fake, _evento_failed(uid, f"sub_viva_{uid}", created=_T_LIFE),
              subs={f"sub_viva_{uid}": _fake_sub("unpaid")})
    assert r.status_code == 200, r.text

    # O ciclo já estava aberto: o `claim` é idempotente e NÃO reinicia a
    # contagem — o relógio é o mesmo, e é por isso que a conta segue na janela.
    assert _relogio(uid) == relogio_antes
    assert db.get_auth_user(uid)["last_payment_status"] == "past_due"
    assert uid in [c["user_id"] for c in list_payment_reminder_candidates(7)]
    # E a carência aberta CONCEDE acesso — o relógio nunca tira, só dá.
    assert has_app_access(uid) is True


# ── o portão de call sites ──────────────────────────────────────────────────

def test_encerrar_ciclo_de_atraso_tem_um_unico_call_site():
    """`encerrar_ciclo_de_atraso` apaga o relógio SEM predicado nenhum, e é essa
    ausência que a torna correta só no evento terminal. Um segundo call site
    herdaria a incondicionalidade sem herdar a razão — que é exatamente como o
    `clear_past_due_since` incondicional de antes do PR #312 chegou onde chegou.

    Precedente da forma: `tests/test_phosphor_subset.py` (§0.7). Quem precisar de
    um segundo call site muda ESTE teste — e é essa a conversa que ele força.
    """
    permitido = "frontend/finance_bot_websocket_custom.py"
    # USO, não MENÇÃO. A 1ª versão casava a substring e ficou vermelha quando a
    # docstring de `core/services/billing_dunning.py` passou a CITAR a função —
    # falso positivo do próprio portão. A 2ª exigia o nome colado a `(`/`,`/`)`
    # ou precedido de `import`, e tinha DOIS furos medidos: `getattr(_d,
    # "encerrar_ciclo_de_atraso")(uid)` (o `"` depois do nome) e
    # `fn = _d.encerrar_ciclo_de_atraso` seguido de `fn(uid)` (o nome no fim da
    # linha). Os dois passavam verdes.
    #
    # Esta versão casa o nome **exceto quando ele está entre crases** — a única
    # forma que a prosa deste repositório usa para citar. Ou seja: volta a ser
    # por substring (que não tem furo) e abre UMA exceção nomeada, em vez de
    # enumerar as formas de chamar, que é a lista que nunca fecha.
    #
    # Erra para o lado VERMELHO: prosa que cite o nome SEM crases conta como
    # call site. Num portão, o lado seguro é esse.
    uso = re.compile(r"(?<!`)\bencerrar_ciclo_de_atraso\b(?!`)")
    achados = []
    for arq in RAIZ.rglob("*.py"):
        rel = arq.relative_to(RAIZ).as_posix()
        if rel.startswith((".venv/", "tests/", ".claude/", "db/dunning.py")):
            continue
        if uso.search(arq.read_text(encoding="utf-8")):
            achados.append(rel)
    assert achados == [permitido], (
        f"call sites de encerrar_ciclo_de_atraso: {achados}; só {permitido} é permitido"
    )
