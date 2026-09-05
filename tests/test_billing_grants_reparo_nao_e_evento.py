"""
tests/test_billing_grants_reparo_nao_e_evento.py — a INVARIANTE 3 do §4.1.1 D
do docs/plano_pix_anual_asaas.md: `event_version` é marca d'água de EVENTO, e o
reparo da varredura não é evento.

Assunto próprio, e não uma seção de `..._evento_obsoleto.py`: ali a pergunta é
"este evento é velho?", decidida entre eventos; aqui é "o REPARO pode calar um
evento?", que é a corrida entre a varredura de 60 s e o webhook. Os três casos
compartilham o mesmo estado inicial (`_reparar_pela_varredura`) e o mesmo
negativo: fazer a célula 7 voltar a reparar por `upsert_grant` com
`epoch(now())`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _billing_grants_helpers import (
    FakeStripeSubs, conta as _conta, evt_deleted as _evt_deleted,
    evt_paid as _evt_paid, ler as _ler, set_customer, sub_stripe as _sub,
)
from core.services.billing_access import recompute_entitlement
from db.plan_grants import (
    esticar_grant, list_grants, revoke_grant, upsert_grant,
)
from test_billing_webhook_lifecycle import _post, _setup


def _stripe_vivo(monkeypatch, sub_id: str, fim: datetime) -> None:
    ts = int(fim.timestamp())
    monkeypatch.setitem(__import__("sys").modules, "stripe", FakeStripeSubs(
        ativa={"id": sub_id, "status": "active", "current_period_end": ts,
               "items": {"data": [{"current_period_end": ts}]}}))


def _reparar_pela_varredura(uid: int, monkeypatch, sub_id: str, agora):
    """Deixa a conta no estado pós-reparo: grant truncado + varredura passou."""
    _conta(uid, "pro", agora + timedelta(days=1), "past_due")
    set_customer(uid, f"cus_{uid}")
    upsert_grant(uid, "stripe", sub_id, "pro",
                 agora - timedelta(days=30), agora - timedelta(minutes=1), 1_000)
    _stripe_vivo(monkeypatch, sub_id, agora + timedelta(days=30))
    recompute_entitlement(uid, origem="varredura")
    return next(g for g in list_grants(uid) if g["source"] == "stripe")

def test_9v_o_reparo_nao_escreve_event_version_e_o_paid_em_voo_aplica(user_id, monkeypatch):
    """Invariante 3 (§4.1.1 D): `event_version` é marca d'água de EVENTO, e o
    reparo da varredura **não é evento**.

    A corrida é alcançável: a varredura roda a cada 60 s e o reparo dispara
    justamente quando o `invoice.paid` de recuperação está chegando. Enquanto o
    reparo carimbava `epoch(now())`, esse `paid` legítimo era reprovado pela
    guarda do upsert — e a conta ficava `past_due` com a fatura paga.

    Com o `esticar_grant`, a `event_version` do grant continua sendo a do
    ÚLTIMO EVENTO, então o `paid` aplica de verdade: data e tier saem dele, não
    só o status.

    NEGATIVO: faça a célula 7 voltar a reparar por `upsert_grant` com
    `int(datetime.now(timezone.utc).timestamp())` → vermelho.
    """
    uid, client, fake = _setup(monkeypatch, f"g9v-{user_id}")
    agora = datetime.now(timezone.utc)
    g = _reparar_pela_varredura(uid, monkeypatch, "sub_9v", agora)

    assert int(g["event_version"]) == 1_000, (
        "o reparo carimbou event_version; ela e marca d'agua de EVENTO")
    assert g["last_event_id"] == "reparo:varredura", "faltou a marca de auditoria"
    assert g["ends_at"] > agora, "o reparo nao esticou o grant"

    monkeypatch.setitem(__import__("sys").modules, "stripe", fake)
    r = _post(client, fake, _evt_paid(uid, "sub_9v", 1_800_000_000),
              subs={"sub_9v": _sub("active", "price_x", 365)})
    assert r.status_code == 200, r.text

    row = _ler(uid)
    assert row["last_payment_status"] == "active", (
        "o paid legitimo virou EVENTO VELHO; conta past_due com fatura paga")
    assert row["plan_expires_at"] > agora + timedelta(days=300), (
        "o paid nao aplicou a DATA dele — o reparo continua mandando no grant")

def test_R2_1_evento_ANTIGO_de_verdade_continua_obsoleto_depois_do_reparo(user_id, monkeypatch):
    """A LARGURA do buraco — a asserção que faltava no 9v.

    O remendo anterior (`or last_event_id = REPARO_EVENT_ID` na reclassificação)
    não comparava versão nenhuma: depois de um reparo, QUALQUER evento deixava
    de ser obsoleto, e o marcador só saía quando um evento aplicasse o upsert —
    numa conta parada, ficava indefinidamente. Este é o mesmo cenário do 9v com
    `created` de 2023: aqui o evento **tem** de ser recusado.

    Sem este teste, 5 segundos e 3 anos passam iguais — foi assim que a cláusula
    entrou verde na rodada anterior.

    NEGATIVO: reponha `or last_event_id = %s` no `_NAO_E_EVENTO_VELHO` →
    vermelho.
    """
    uid, client, fake = _setup(monkeypatch, f"gr21-{user_id}")
    agora = datetime.now(timezone.utc)
    # Grant com versão ALTA (evento recente de verdade), depois reparado.
    _conta(uid, "pro", agora + timedelta(days=1), "active")
    set_customer(uid, f"cus_{uid}")
    upsert_grant(uid, "stripe", "sub_r21", "pro",
                 agora - timedelta(days=30), agora - timedelta(minutes=1),
                 1_900_000_000, "evt_recente")
    _stripe_vivo(monkeypatch, "sub_r21", agora + timedelta(days=30))
    recompute_entitlement(uid, origem="varredura")
    assert next(g for g in list_grants(uid))["last_event_id"] == "reparo:varredura"

    _conta(uid, "pro", agora + timedelta(days=30), "active")
    monkeypatch.setitem(__import__("sys").modules, "stripe", fake)
    r = _post(client, fake, _evt_paid(uid, "sub_r21", 1_700_000_000),   # 2023
              subs={"sub_r21": _sub("trialing", "price_x", 3)})
    assert r.status_code == 200, r.text
    assert _ler(uid)["last_payment_status"] != "trialing", (
        "evento de 2023 foi aceito por causa do marcador do reparo")

def test_R2_2_o_reparo_nao_bloqueia_a_revogacao_de_um_deleted_atrasado(user_id, monkeypatch):
    """Some junto com a invariante 3, e é dinheiro.

    `revoke_grant` exige `event_version <= %s` (a do grant tem de ser menor ou
    igual à do evento). Com o reparo carimbando `epoch(now())`, todo
    `subscription.deleted` com `created` anterior à passada da varredura era
    DESCARTADO: o grant continuava ativo e a conta cancelada voltava a `pro` no
    `recompute` do próprio ramo do cancelamento.

    NEGATIVO: o mesmo da invariante 3 — faça a célula 7 reparar por
    `upsert_grant` com `epoch(now())` → vermelho.
    """
    uid, client, fake = _setup(monkeypatch, f"gr22-{user_id}")
    agora = datetime.now(timezone.utc)
    _reparar_pela_varredura(uid, monkeypatch, "sub_r22", agora)

    # `created` ANTERIOR à passada da varredura — é o que a Stripe manda quando
    # o cancelamento sai segundos antes de o reparo rodar. Um `created` no
    # futuro (1_800_000_000 é 2027) passaria pela guarda de qualquer jeito e
    # deixaria este teste cego ao defeito que ele existe para medir.
    monkeypatch.setitem(__import__("sys").modules, "stripe", fake)
    r = _post(client, fake, _evt_deleted(uid, "sub_r22", int(agora.timestamp()) - 5))
    assert r.status_code == 200, r.text

    g = next(x for x in list_grants(uid) if x["source"] == "stripe")
    assert g["status"] == "revoked", (
        "o carimbo do reparo descartou a revogacao do cancelamento")
    assert _ler(uid)["plan"] == "free", "conta cancelada continuou paga"


def test_esticar_grant_so_estica_grant_ATIVO_do_DONO_e_nunca_encurta(user_id):
    """As três cláusulas do `UPDATE`, uma a uma — chamando `esticar_grant` DIRETO.

    Elas são as invariantes 1 e 2 do §4.1.1 D viradas em condição de banco, e
    pelo caminho de cima **nenhuma delas era medida**: o `_reparar_grant_pelo_stripe`
    tem um filtro `_ativo()` em Python que faz o mesmo trabalho, e as duas
    guardas são redundantes entre si. Medido nesta árvore: desligar só a
    cláusula SQL → 31 verdes; desligar só o `_ativo()` → 31 verdes; desligar as
    DUAS → o `test_9l` fica vermelho. Ou seja, o negativo do 9l não discrimina
    nenhuma das duas sozinha, e é por isso que este teste existe.

    Ele não passa por `recompute_entitlement` de propósito: guarda de banco se
    mede no banco, sem uma segunda guarda em Python no caminho.
    """
    agora = datetime.now(timezone.utc)
    ref = f"est_{user_id}"
    upsert_grant(user_id, "stripe", ref, "pro", agora,
                 agora + timedelta(days=30), 1_000)

    # `and ends_at < %s` — só ESTICA. Encurtar seria tirar acesso pago numa
    # varredura, sem evento nenhum.
    assert esticar_grant(user_id, "stripe", ref, agora + timedelta(days=10)) is False
    g = next(x for x in list_grants(user_id) if x["external_ref"] == ref)
    assert g["ends_at"] > agora + timedelta(days=29), "encurtou o grant"

    # sem `insert`: não recria linha que sumiu.
    assert esticar_grant(user_id, "stripe", "nao_existe",
                         agora + timedelta(days=99)) is False
    assert [x for x in list_grants(user_id) if x["external_ref"] == "nao_existe"] == []

    # `and user_id = %s` (CLAUDE.md §0) — a unique é `(source, external_ref)`,
    # GLOBAL. Não precisa existir conta para `outro`: o `UPDATE` filtra por
    # `user_id` e é justamente o não-casamento que se mede aqui.
    outro = user_id + 1
    assert esticar_grant(outro, "stripe", ref, agora + timedelta(days=99)) is False, (
        "esticou grant de outro dono")

    # `and status = 'active'` — invariante 1. Só um EVENTO desfaz revogação.
    revoke_grant(user_id, "stripe", ref, "estorno", 2_000_000)
    assert esticar_grant(user_id, "stripe", ref, agora + timedelta(days=99)) is False, (
        "esticou grant REVOGADO")
    g = next(x for x in list_grants(user_id) if x["external_ref"] == ref)
    assert g["status"] == "revoked"
    assert g["ends_at"] < agora + timedelta(days=31), "moveu o ends_at do revogado"
