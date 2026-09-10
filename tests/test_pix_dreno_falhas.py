"""O dreno quando algo dá errado — os cinco achados do Codex no PR #330.

Arquivo próprio porque os irmãos (`test_pix_dreno_janela.py`,
`test_pix_migracao_stripe.py`) medem o CAMINHO FELIZ de cada assunto, e aqui
mora o contrário: exceção fora do laço de efeitos, Stripe fora do ar por seis
passadas, valor que não bate, e-mail recusado, período do cartão que renovou no
meio. Cinco assuntos, uma pergunta só: **o dinheiro entrou; o acesso sai?**

CONTROLES NEGATIVOS MEDIDOS (um a um, com o resto do grupo verde) — as saídas
estão no relato do PR:

  * tire o `try/except` de `drenar_evento` →
    `test_falha_antes_dos_efeitos_vira_attempts` vermelho por EXCEÇÃO, e a fila
    inteira para no evento mais velho;
  * tire o ramo do `attempts >= 5` no laço de efeitos →
    `test_stripe_fora_do_ar_seis_vezes_concede_assim_mesmo` vermelho sem grant;
  * ignore o retorno de `send_pix_paid_email` →
    `test_email_recusado_nao_registra_o_efeito` vermelho (efeito registrado e
    evento fechado, e-mail nunca reenviado);
  * não chame `alertar_valor` → `test_valor_menor_que_o_combinado_alerta` vermelho;
  * devolva `{}` sempre em `_janela_adiada` →
    `test_periodo_do_stripe_que_renovou_adia_o_acesso` vermelho, com o ano Pix
    começando dentro de um mês que o cartão já cobrou.

POSITIVOS do grupo: `test_venda_normal_continua_fechando_o_evento` (nenhuma das
guardas novas dispara na venda comum) e, no arquivo da migração,
`test_falha_no_stripe_vira_attempts_e_nao_fecha` — ele prova que o fallback NÃO
antecipa o grant na primeira falha, que é o jeito errado de passar no teste do
fallback.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import (
    efeitos,
    entregar,
    evento,
    ler,
    mundo_externo,
    nova_cobranca,
)
from db.plan_grants import list_grants


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


@pytest.fixture()
def stripe_falso(monkeypatch):
    """Stripe por CONTADOR. `erro` liga a indisponibilidade; `fim` é o
    `current_period_end` que o `retrieve` devolve."""
    import sys
    import types

    estado = {"feito": [], "erro": None,
              "fim": datetime.now(timezone.utc) + timedelta(days=40)}

    class _Sub:
        @staticmethod
        def retrieve(sub_id):
            estado["feito"].append(f"retrieve:{sub_id}")
            if estado["erro"]:
                raise estado["erro"]
            return {"id": sub_id,
                    "current_period_end": int(estado["fim"].timestamp())}

        @staticmethod
        def modify(sub_id, **kw):
            estado["feito"].append(f"modify:{sub_id}")
            return {"id": sub_id}

    falso = types.ModuleType("stripe")
    falso.Subscription = _Sub
    monkeypatch.setitem(sys.modules, "stripe", falso)
    return estado


def _pix(uid: int) -> list[dict]:
    return [g for g in list_grants(uid) if g["source"] == "pix"]


# ── 1. exceção FORA do laço de efeitos (P1, `pix_drain.py:127`) ──────────────

def test_falha_antes_dos_efeitos_vira_attempts(user_id, externo, monkeypatch):
    """DISCRIMINA. `drenar_evento` promete nunca levantar — e só o laço de
    efeitos convertia exceção em `(tipo, code)`.

    Uma falha ao BUSCAR a cobrança subia por cima de `drenar_pendentes`: o
    evento mais velho da fila abortava a passada inteira, com `attempts` parado
    em zero (logo, sem nunca alcançar o alerta de travado) e os eventos
    seguintes nunca visitados. Aqui o veneno é uma consulta que falha, que é a
    forma mais barata de reproduzir "o banco piscou".
    """
    import core.services.pix_drain as dreno

    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    monkeypatch.setattr(dreno, "buscar_por_asaas_payment_id",
                        lambda pid: (_ for _ in ()).throw(RuntimeError("db piscou")))

    event_id = entregar("PAYMENT_RECEIVED", cobranca)  # não pode levantar

    linha_evt = evento(event_id)
    assert linha_evt["processed_at"] is None
    assert linha_evt["attempts"] == 1
    assert linha_evt["last_error"] and "RuntimeError" in linha_evt["last_error"]
    assert _pix(user_id) == []


def test_evento_envenenado_nao_segura_a_fila(user_id, externo, monkeypatch):
    """O efeito prático do teste acima, medido no LAÇO e não numa chamada só.

    `drenar_pendentes` visita a fila em ordem; sem a conversão, o primeiro
    evento levava a passada junto e o segundo — a venda de outra pessoa — nunca
    era drenado.
    """
    import core.services.pix_drain as dreno
    from core.services.pix_sweeps import drenar_pendentes
    from db.webhook_outbox import registrar_evento

    conta(user_id, "free", None)
    venenoso = "evt_veneno_fila"
    registrar_evento(venenoso, "PAYMENT_RECEIVED",
                     {"id": venenoso, "payment": {"id": "pay_veneno"}}, 1)
    monkeypatch.setattr(dreno, "buscar_por_asaas_payment_id",
                        lambda pid: (_ for _ in ()).throw(RuntimeError("db piscou")))

    assert drenar_pendentes() >= 1
    assert evento(venenoso)["attempts"] == 1


# ── 2. o fallback do `stripe_cancel` (P1, `pix_drain.py:137`) ────────────────

def test_stripe_fora_do_ar_seis_vezes_concede_assim_mesmo(
        user_id, externo, stripe_falso):
    """DISCRIMINA, e é o pior dos cinco: dinheiro dentro, acesso fora.

    `stripe_cancel` é o PRIMEIRO efeito, então o Stripe fora do ar barrava o
    `grant` em TODA retentativa — para sempre, porque nada no dreno desiste.
    Na 6ª falha o §8.2 manda alertar **e** conceder: o começo do acesso já é
    `max(now, stripe_period_end_at)` desde a transição, e sobreposição de
    período é estornável (acesso negado a quem pagou não é).
    """
    conta(user_id, "free", None)
    stripe_falso["erro"] = RuntimeError("stripe fora do ar")
    estimativa = datetime.now(timezone.utc) + timedelta(days=12)
    cobranca = nova_cobranca(user_id, stripe_subscription_id="sub_out",
                             stripe_period_end_at=estimativa)

    event_id = entregar("PAYMENT_RECEIVED", cobranca, tentativas=5)

    marcados = efeitos(cobranca["asaas_payment_id"])
    assert "grant" in marcados, "o acesso continuou preso ao Stripe"
    assert "stripe_cancel" not in marcados, (
        "registrou um efeito que NÃO rodou — a assinatura ficaria sem "
        "cancelamento e sem ninguém sabendo"
    )
    assert evento(event_id)["processed_at"] is not None
    grant = _pix(user_id)[0]
    assert grant["starts_at"] >= estimativa - timedelta(minutes=1), (
        "o grant do fallback começou ANTES do fim do período pago no cartão"
    )
    assert any("cancelamento no Stripe" in m for m in externo["alerta"]), \
        externo["alerta"]


def test_fallback_sem_periodo_gravado_comeca_agora(user_id, externo, stripe_falso):
    """O `coalesce` do §8.2: `stripe_period_end_at` NULO é alcançável
    exatamente quando o fallback age (o Stripe já estava fora no checkout).

    Sem ele, `max(now, None)` levantaria `TypeError` e o `starts_at not null` de
    `plan_grants` derrubaria a concessão — o fallback estouraria na hora exata
    em que existe para agir.
    """
    conta(user_id, "free", None)
    stripe_falso["erro"] = RuntimeError("stripe fora do ar")
    cobranca = nova_cobranca(user_id, stripe_subscription_id="sub_sem_periodo")
    antes = datetime.now(timezone.utc)

    entregar("PAYMENT_RECEIVED", cobranca, tentativas=5)

    grant = _pix(user_id)[0]
    assert grant["starts_at"] >= antes - timedelta(minutes=1)
    assert ler(cobranca["id"])["status"] == "paid"


# ── 3. a janela contra o período RECONFIRMADO (P1, efeitos `:135`) ───────────

def test_periodo_do_stripe_que_renovou_adia_o_acesso(user_id, externo, stripe_falso):
    """DISCRIMINA. O QR vive três dias; a assinatura pode renovar nesse meio.

    A transição para `paid` decide a janela pela ESTIMATIVA do checkout, e o
    `stripe_cancel` só depois lê o `current_period_end` de verdade. Sem adiar a
    janela, o ano Pix começava na data velha e o cliente queimava dias de um mês
    que o cartão já tinha cobrado.
    """
    conta(user_id, "free", None)
    estimativa = datetime.now(timezone.utc) + timedelta(days=2)
    stripe_falso["fim"] = datetime.now(timezone.utc) + timedelta(days=40)
    cobranca = nova_cobranca(user_id, stripe_subscription_id="sub_renovou",
                             stripe_period_end_at=estimativa)

    entregar("PAYMENT_RECEIVED", cobranca)

    linha = ler(cobranca["id"])
    assert int(linha["access_starts_at"].timestamp()) == int(
        stripe_falso["fim"].timestamp()), "a janela ficou na estimativa velha"
    assert (linha["access_expires_at"] - linha["access_starts_at"]).days == 365
    grant = _pix(user_id)[0]
    assert grant["starts_at"] >= stripe_falso["fim"] - timedelta(minutes=1), (
        "o grant nasceu com a janela velha — o efeito seguinte leu a linha antiga"
    )


def test_periodo_reconfirmado_mais_CEDO_nao_antecipa_a_janela(
        user_id, externo, stripe_falso):
    """POSITIVO do par: a janela só ADIA.

    Antecipar sobreporia o grant Pix ao período de cartão ainda pago — o erro
    que a migração inteira existe para evitar. `_janela_adiada` devolve `{}` e
    nada é reescrito.
    """
    conta(user_id, "free", None)
    estimativa = datetime.now(timezone.utc) + timedelta(days=30)
    stripe_falso["fim"] = datetime.now(timezone.utc) + timedelta(days=5)
    cobranca = nova_cobranca(user_id, stripe_subscription_id="sub_cedo",
                             stripe_period_end_at=estimativa)

    entregar("PAYMENT_RECEIVED", cobranca)

    linha = ler(cobranca["id"])
    assert int(linha["access_starts_at"].timestamp()) == int(estimativa.timestamp())


# ── 4. o valor liquidado (P2, `pix_drain.py:147`) ────────────────────────────

def test_valor_menor_que_o_combinado_alerta(user_id, externo):
    """O `value` que a minimização guarda existe para ser conferido, e ninguém
    o lia. Divergência só nasce de edição no painel do provedor — o QR dinâmico
    carrega o valor, então o pagador não escolhe quanto paga.

    **Alerta e concede**: recusar acesso a quem pagou o desconto que NÓS demos
    é o erro irreversível (§7). A decisão está em `alertar_valor`.
    """
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)

    entregar("PAYMENT_RECEIVED", cobranca, valor=1.0)

    assert any("valor diferente do combinado" in m for m in externo["alerta"]), \
        externo["alerta"]
    assert _pix(user_id), "o alerta virou recusa — não é o que o §7 manda"


# ── 5. o e-mail recusado (P2, efeitos `:205`) ────────────────────────────────

def test_email_recusado_nao_registra_o_efeito(user_id, externo):
    """DISCRIMINA. `send_email` nunca levanta: Resend fora do ar e Resend sem
    chave saem os dois por `return False`.

    Ignorar o retorno registrava `(payment_id, 'email')` com zero e-mail
    enviado — e esse par **nunca é purgado**, então o pagante ficava sem
    confirmação para sempre. O acesso, esse, já saiu: `grant` vem antes.
    """
    conta(user_id, "free", None)
    externo["email_ok"] = False
    cobranca = nova_cobranca(user_id)

    event_id = entregar("PAYMENT_RECEIVED", cobranca)

    marcados = efeitos(cobranca["asaas_payment_id"])
    assert "grant" in marcados
    assert "email" not in marcados, "marcou como enviado um e-mail que não saiu"
    assert evento(event_id)["processed_at"] is None, "fechou o evento sem o e-mail"
    assert evento(event_id)["attempts"] == 1


def test_venda_normal_continua_fechando_o_evento(user_id, externo):
    """POSITIVO do arquivo inteiro: com o mundo lá fora respondendo, nenhuma das
    guardas novas dispara.

    Sem ele, uma versão que recusasse tudo (valor sempre divergente, e-mail
    sempre falho) passaria em todos os negativos acima.
    """
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)

    event_id = entregar("PAYMENT_RECEIVED", cobranca)

    assert efeitos(cobranca["asaas_payment_id"]) == {
        "stripe_cancel", "grant", "ga4", "capi", "email"}
    assert evento(event_id)["processed_at"] is not None
    assert externo["alerta"] == []
    assert _pix(user_id)
