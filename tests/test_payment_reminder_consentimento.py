"""
tests/test_payment_reminder_consentimento.py — o lembrete de pagamento respeita
a preferência DO CANAL que a pessoa desligou.

Arquivo próprio porque o assunto é CONSENTIMENTO, e não transporte: o irmão
`test_payment_reminder_whatsapp.py` mede o veredito de `send_template` (401,
2xx, `raise`, falha por destino) e este mede se a mensagem tinha permissão de
sair. Também porque o irmão passou de 350 linhas com este bloco dentro, o teto
de `tests/test_max_lines_python.py`.

Helpers por IMPORT do irmão (§0.7), inclusive a fixture de ambiente — uma fonte
só para "flag ligada, template definido, `system_event_logs` garantida".
"""
from __future__ import annotations

import pytest  # noqa: F401  (usado pelas fixtures importadas)

from test_payment_reminder import (
    _espia,
    _inadimplente,
    _limpar_eventos,
    _tick,
)
from test_payment_reminder_whatsapp import (  # noqa: F401
    _OK,
    _ambiente,
    _detalhes_do_evento,
    _send_template_roteiro,
    _um_destino_whatsapp,
)


# ──────────────────────────────────────────────────────────────────────────────
# CONSENTIMENTO DO CANAL — `auth_accounts.whatsapp_updates_opt_out`.
#
# Quem desligou "atualizações do Piggy" em Configurações > Notificações
# (`frontend/routes/settings.py:507` → `db.reports.set_whatsapp_updates_opt_out`)
# ou pelo botão do próprio WhatsApp (`adapters/whatsapp/wa_runtime.py:878`) pediu
# para NÃO receber neste canal. Mandar mesmo assim não é bug de mecânica, é
# violação de consentimento.
#
# O precedente do repositório está do lado certo: `scripts/send_update_whatsapp.py:105`
# filtra `where coalesce(a.whatsapp_updates_opt_out, false) = false`. E o canal
# de E-MAIL desta mesma feature respeita o `engagement_opt_out`
# (`db/dunning.py`) — então, antes deste conserto, dentro da MESMA feature um
# canal respeitava a preferência e o outro não.
#
# **A checagem NÃO foi para a query do funil, e isso é o ponto do desenho.** O
# funil serve os DOIS canais, e quem desligou só o WhatsApp continua com direito
# ao e-mail de cobrança. Filtrar lá trocaria a violação de consentimento por um
# erro PIOR (perder aviso legítimo de cobrança). A coluna é SELECIONADA — mesma
# linha já lida, zero query a mais — e quem decide é o canal.
#
# CONTROLE NEGATIVO — em `core/services/payment_reminder_wa.py::_wa_lembrete`,
# apague o `if wa_opt_out: return False`:
#     VERMELHO: test_opt_out_de_whatsapp_nao_recebe_template
#     VERDE:    test_sem_opt_out_continua_recebendo (o positivo),
#               test_opt_out_de_whatsapp_nao_tira_o_email, e todo o resto dos
#               arquivos de lembrete — a fixture deles não liga o opt-out, que é
#               o que prova que a injeção mede a PREFERÊNCIA e não o canal.
#     MEDIDO:   1 failed, 34 passed.
# CONTROLE POSITIVO: test_sem_opt_out_continua_recebendo. Sem ele o grupo
# passaria num canal que nunca manda.
# E O TERCEIRO, que separa este conserto do erro pior:
# test_opt_out_de_whatsapp_nao_tira_o_email — uma implementação que filtrasse no
# FUNIL passaria no positivo e reprovaria neste. MEDIDO, pondo
# `and coalesce(whatsapp_updates_opt_out, false) = false` no `where` de
# `db.dunning.list_payment_reminder_candidates`: 2 failed, 33 passed — vermelho
# neste e no test_opt_out_de_whatsapp_nao_recebe_template (a conta desaparece do
# lote inteiro, então o e-mail cai junto), verde no positivo.
# ──────────────────────────────────────────────────────────────────────────────

def _desligar_whatsapp(uid: int) -> None:
    """Usa a função de PRODUÇÃO que a tela de Configurações chama (§0.7), em vez
    de um UPDATE à mão — se a coluna mudar de nome, o teste acompanha."""
    from db.reports import set_whatsapp_updates_opt_out
    set_whatsapp_updates_opt_out(uid, True)
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)


def test_opt_out_de_whatsapp_nao_recebe_template(user_id, monkeypatch):
    """NEGATIVO: conta com o canal desligado não recebe template, e o evento
    grava `whatsapp: false`.

    As duas asserções são necessárias: `send_template` não chamado é o
    consentimento respeitado, e `whatsapp: false` é o registro dizendo a
    verdade sobre isso.
    """
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    _desligar_whatsapp(user_id)
    enviados = _espia(monkeypatch, user_id)
    _um_destino_whatsapp(monkeypatch, user_id)
    chamadas = _send_template_roteiro(monkeypatch, {0: _OK})

    _tick()

    assert chamadas == [], \
        "mandou WhatsApp para quem desligou o canal — violação de consentimento"
    assert len(enviados) == 1, "o e-mail deveria continuar saindo"
    assert _detalhes_do_evento(user_id)["whatsapp"] is False


def test_sem_opt_out_continua_recebendo(user_id, monkeypatch):
    """POSITIVO: conta que NÃO desligou o canal continua recebendo. Sem este
    caso o grupo passaria num `_wa_lembrete` que nunca manda."""
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _espia(monkeypatch, user_id)
    _um_destino_whatsapp(monkeypatch, user_id)
    chamadas = _send_template_roteiro(monkeypatch, {0: _OK})

    _tick()

    assert len(chamadas) == 1, "o canal ligado deixou de enviar"
    assert len(enviados) == 1
    assert _detalhes_do_evento(user_id)["whatsapp"] is True


def test_opt_out_de_whatsapp_nao_tira_o_email(user_id, monkeypatch):
    """O caso que separa este conserto do ERRO PIOR: quem desligou o WhatsApp
    continua recebendo o e-mail de cobrança.

    Uma implementação que filtrasse `whatsapp_updates_opt_out` na query do
    funil (`db.dunning.list_payment_reminder_candidates`) passaria nos dois
    testes acima e reprovaria neste — a conta sairia do lote e perderia o aviso
    de cobrança que ela nunca pediu para perder. É o §1: quando a escolha
    parece ser entre dois males, falta um passo.

    Sem template de WhatsApp de propósito: aqui o assunto é só o e-mail, e o
    canal já tem os testes dele acima.
    """
    monkeypatch.delenv("WA_TEMPLATE_PAYMENT_REMINDER", raising=False)
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    _desligar_whatsapp(user_id)
    enviados = _espia(monkeypatch, user_id)

    # O funil TEM de continuar devolvendo a conta — é a metade do desenho que
    # mora no SQL, e sem esta asserção o teste passaria por acidente se o
    # e-mail viesse de outro caminho.
    from db.dunning import list_payment_reminder_candidates
    from core.services.billing_dunning import DUNNING_GRACE_DAYS
    ids = [int(r["user_id"])
           for r in list_payment_reminder_candidates(DUNNING_GRACE_DAYS)]
    assert user_id in ids, \
        "opt-out de WhatsApp tirou a conta do funil — o e-mail foi junto"

    _tick()

    assert len(enviados) == 1, \
        "quem desligou só o WhatsApp perdeu o e-mail de cobrança"
    assert _detalhes_do_evento(user_id)["whatsapp"] is False
