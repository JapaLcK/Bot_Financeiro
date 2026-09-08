"""
tests/test_payment_reminder_whatsapp.py — o VEREDITO do envio por WhatsApp, e o
que fica gravado por causa dele.

`core.services.payment_reminder_wa._wa_lembrete` descartava o retorno de
`adapters.whatsapp.wa_client.send_template` e punha `enviado = True`
incondicional. O retorno carrega veredito:

  • **401** (token inválido/expirado) → `return None`, sem levantar
    (`adapters/whatsapp/wa_client.py:220`);
  • qualquer outro `>= 400` → `raise RuntimeError` (`:237`);
  • 2xx → o JSON da resposta (`:252`).

Ou seja, o 401 é o **único** caminho silencioso — e é o pior de todos, porque
token expirado faz TODO envio falhar de uma vez. Com o retorno descartado, o
evento `payment_reminder_sent` gravava `whatsapp: true` com a Meta tendo
recusado a mensagem, e o painel mentia exatamente no incidente em que alguém
iria olhar.

**A CLASSE é "descartar um retorno que carrega veredito", e esta é a TERCEIRA
instância no PR** — as duas primeiras foram os `clear_past_due_since` do
`invoice.paid` e do `checkout.session.completed`, que ignoravam o retorno de
`_materializar_assinatura` (`tests/test_billing_dunning_eventos.py`, R2 e R3).
Por isso o código NOVO do PR inteiro foi varrido por retorno descartado antes
deste conserto; o resultado da varredura está no relato da rodada.

O OBSERVÁVEL é o que foi **gravado**, não o retorno da função: o dano descrito
é o registro errado, e um teste que só olhasse o `bool` de `_wa_lembrete`
passaria mesmo que o `details` fosse montado de outro jeito.

O canal WhatsApp foi extraído para `core/services/payment_reminder_wa.py` na
rodada 6: o assunto é outro (sistema externo, template na Meta, envio por
destino) e o `payment_reminder.py` estourou o teto de 350 linhas com o conserto
de falha por destino. §0.5 — dividir por assunto em vez de cortar a razão das
decisões dos comentários.

CONTROLE NEGATIVO DECLARADO — em
`core/services/payment_reminder_wa.py::_wa_lembrete`,
volte o corpo do laço a descartar o retorno (`send_template(...)` numa linha e
`enviado = True` na seguinte):
    VERMELHO: test_token_invalido_grava_whatsapp_false
    VERDE:    test_envio_aceito_grava_whatsapp_true (é o caso que a injeção NÃO
              pode reprovar), test_sem_template_nao_tenta_whatsapp, e todo
              `test_payment_reminder.py` / `_janela.py` / `_revalida.py` /
              `_lote.py`, cuja fixture não define
              `WA_TEMPLATE_PAYMENT_REMINDER`.

CONTROLE POSITIVO: `test_envio_aceito_grava_whatsapp_true`. Sem ele o arquivo
passaria num `_wa_lembrete` que devolvesse sempre False — WhatsApp desligado na
prática, e o registro sempre `false`.
"""
from __future__ import annotations

import json

import pytest

from db.connection import get_conn
from test_payment_reminder import _espia, _inadimplente, _limpar_eventos, _tick


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    """Como os irmãos, MAIS o template de WhatsApp definido — sem a env o
    `_wa_lembrete` é dormente e nem importa o `wa_client`."""
    from _billing_grants_helpers import garantir_system_event_logs
    monkeypatch.setenv("PAYMENT_REMINDER_ENABLED", "1")
    monkeypatch.setenv("WA_TEMPLATE_PAYMENT_REMINDER", "pigbank_cobranca_pendente")
    garantir_system_event_logs()


def _detalhes_do_evento(uid: int) -> dict:
    """O `details` GRAVADO no `payment_reminder_sent` desta conta. É o
    observável do apontamento — não o retorno de `_wa_lembrete`."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select details from system_event_logs"
                " where event_type = 'payment_reminder_sent' and user_id = %s"
                " order by id desc limit 1",
                (uid,))
            row = cur.fetchone()
    assert row is not None, "o evento payment_reminder_sent não foi gravado"
    det = row["details"]
    return json.loads(det) if isinstance(det, str) else det


_NUMEROS = ["5511999990000", "5521988880000", "5531977770000"]


def _destinos_whatsapp(monkeypatch, uid: int, quantos: int = 1) -> None:
    """`quantos` identidades de WhatsApp para a conta. `_wa_lembrete` importa
    `list_identities_by_user` de `db` DENTRO da função, então o ponto de
    injeção é o módulo `db`.

    Números de DDDs diferentes de propósito: `_dedupe_whatsapp_targets`
    normaliza e deduplica por candidatos de telefone, e variações do mesmo
    número colapsariam em um destino só — o teste de falha parcial precisa de
    destinos realmente distintos."""
    import db
    ids = [{"provider": "whatsapp", "external_id": n}
           for n in _NUMEROS[:quantos]]
    monkeypatch.setattr(db, "list_identities_by_user",
                        lambda u: ids if int(u) == uid else [])


def _um_destino_whatsapp(monkeypatch, uid: int) -> None:
    _destinos_whatsapp(monkeypatch, uid, 1)


def _send_template(monkeypatch, retorno):
    """Troca o `send_template` do `wa_client`. `_wa_lembrete` o importa dentro
    da função, então trocar o atributo do módulo basta. Devolve as chamadas."""
    from adapters.whatsapp import wa_client
    chamadas: list = []

    def _fake(to, nome, language_code=None, **kw):
        chamadas.append((to, nome, language_code))
        return retorno() if callable(retorno) else retorno

    monkeypatch.setattr(wa_client, "send_template", _fake)
    return chamadas


def test_token_invalido_grava_whatsapp_false(user_id, monkeypatch):
    """NEGATIVO: 401 devolve None sem levantar, e o registro tem de dizer
    `whatsapp: false`.

    O e-mail continua saindo — o WhatsApp é melhoria, não pré-requisito —, e é
    justamente por isso que o registro errado passava despercebido: tudo parecia
    ter funcionado.
    """
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _espia(monkeypatch, user_id)
    _um_destino_whatsapp(monkeypatch, user_id)
    chamadas = _send_template(monkeypatch, None)   # é o que o 401 devolve

    _tick()

    assert chamadas, "o send_template não foi chamado — o teste mediria nada"
    assert len(enviados) == 1, "o e-mail (caminho garantido) deixou de sair"
    assert _detalhes_do_evento(user_id)["whatsapp"] is False, \
        "gravou whatsapp: true com a Meta tendo recusado a mensagem (401)"
    assert _detalhes_do_evento(user_id)["email"] is True


def test_envio_aceito_grava_whatsapp_true(user_id, monkeypatch):
    """POSITIVO: 2xx devolve o JSON da resposta e o registro diz `true`.

    Sem este caso, um `_wa_lembrete` que devolvesse sempre False passaria no
    teste acima — e o WhatsApp estaria desligado na prática.
    """
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _espia(monkeypatch, user_id)
    _um_destino_whatsapp(monkeypatch, user_id)
    chamadas = _send_template(
        monkeypatch, {"messages": [{"id": "wamid.abc"}]})   # forma real do 2xx

    _tick()

    assert len(chamadas) == 1
    assert len(enviados) == 1
    assert _detalhes_do_evento(user_id)["whatsapp"] is True, \
        "envio aceito pela Meta ficou registrado como não enviado"


def test_erro_de_servidor_da_meta_grava_whatsapp_false(user_id, monkeypatch):
    """O outro desfecho de falha: `>= 400` que não é 401 LEVANTA
    (`wa_client.py:237`). O `except` de `_wa_lembrete` já existia e devolve
    False — este caso prova que o conserto não duplicou tratamento nem trocou o
    comportamento dele, e que o e-mail sobrevive aos dois modos de falha."""
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _espia(monkeypatch, user_id)
    _um_destino_whatsapp(monkeypatch, user_id)

    def _explode():
        raise RuntimeError("WA send_template failed 500: boom")

    _send_template(monkeypatch, _explode)

    _tick()

    assert len(enviados) == 1, "o e-mail deveria sair mesmo com a Meta em erro"
    assert _detalhes_do_evento(user_id)["whatsapp"] is False


def test_sem_template_nao_tenta_whatsapp(user_id, monkeypatch):
    """Sem `WA_TEMPLATE_PAYMENT_REMINDER` o caminho é dormente: `send_template`
    nem é chamado e o registro diz `false`. É o estado de PRODUÇÃO hoje (a env
    é vazia por padrão), então é o caso que mais roda de verdade."""
    monkeypatch.delenv("WA_TEMPLATE_PAYMENT_REMINDER", raising=False)
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _espia(monkeypatch, user_id)
    _um_destino_whatsapp(monkeypatch, user_id)
    chamadas = _send_template(monkeypatch, {"messages": [{"id": "x"}]})

    _tick()

    assert chamadas == [], "tentou WhatsApp sem template aprovado na Meta"
    assert len(enviados) == 1
    assert _detalhes_do_evento(user_id)["whatsapp"] is False


# ──────────────────────────────────────────────────────────────────────────────
# FALHA POR DESTINO — a categoria que a rodada anterior pulou.
#
# `send_template` tem TRÊS desfechos, e o conserto anterior tratou UM:
#   • `None` no 401 — token inválido/expirado, GLOBAL (não levanta);
#   • `raise` em erro de TRANSPORTE (`wa_client.py:189-195`) e em todo outro
#     `>= 400` (`:237`) — os dois POR DESTINO;
#   • JSON da resposta no 2xx.
#
# O raciocínio da rodada anterior foi "o único desfecho silencioso é o 401, que
# é global, então `any` e `all` coincidem". Isso era verdade PARA O 401 e nos
# fez concluir sobre a categoria a partir do caso examinado (§2 — "achei um
# caso" não é "resolvi a categoria"). Com o `return enviado` DENTRO do `try` de
# fora, um destino levantando abortava o laço antes dos seguintes E fazia o
# `except` devolver False mesmo que um destino anterior tivesse aceitado — o
# comentário que a rodada anterior escreveu ("ALGUM destino aceitou") ficou na
# frente do código, que é o pior tipo de defeito.
#
# CONTROLE NEGATIVO — em `core/services/payment_reminder_wa.py::_wa_lembrete`,
# tire o `try`/`except` de dentro do laço e volte o `return` para dentro do
# `try` de fora:
#     VERMELHO: test_destino_que_estoura_nao_perde_o_sucesso_anterior
#               test_destino_que_estoura_nao_aborta_os_seguintes
#     VERDE:    test_todos_os_destinos_aceitos_conta_todos,
#               test_token_invalido_grava_whatsapp_false (o conserto da rodada
#               anterior, que a injeção NÃO pode reprovar), e os demais.
# CONTROLE POSITIVO: test_todos_os_destinos_aceitos_conta_todos — sem ele o
# grupo passaria num `_wa_lembrete` que devolvesse True sempre.
# ──────────────────────────────────────────────────────────────────────────────

def _send_template_roteiro(monkeypatch, roteiro: dict):
    """`send_template` que responde por ORDEM de chamada: `roteiro[i]` é o que a
    i-ésima chamada faz — um valor devolve, um `Exception` levanta. Devolve a
    lista de destinos tentados."""
    from adapters.whatsapp import wa_client
    tentados: list = []

    def _fake(to, nome, language_code=None, **kw):
        tentados.append(to)
        acao = roteiro[len(tentados) - 1]
        if isinstance(acao, Exception):
            raise acao
        return acao

    monkeypatch.setattr(wa_client, "send_template", _fake)
    return tentados


_OK = {"messages": [{"id": "wamid.ok"}]}


def test_destino_que_estoura_nao_perde_o_sucesso_anterior(user_id, monkeypatch):
    """NEGATIVO 1: destino 1 aceita, destino 2 levanta → `whatsapp: true`.

    A metade mais insidiosa do defeito: a mensagem CHEGOU num número e o
    registro dizia que não saiu nada. É exatamente o que o comentário da rodada
    anterior prometia e o código não fazia.
    """
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _espia(monkeypatch, user_id)
    _destinos_whatsapp(monkeypatch, user_id, 2)
    tentados = _send_template_roteiro(
        monkeypatch, {0: _OK, 1: RuntimeError("WA send_template failed 500")})

    _tick()

    assert len(tentados) == 2, f"os dois destinos não foram tentados: {tentados}"
    assert len(enviados) == 1, "o e-mail (caminho garantido) deixou de sair"
    assert _detalhes_do_evento(user_id)["whatsapp"] is True, \
        "sucesso do 1º destino foi perdido pela falha do 2º"


def test_destino_que_estoura_nao_aborta_os_seguintes(user_id, monkeypatch):
    """NEGATIVO 2: três destinos, o do meio levanta, e o TERCEIRO tem de ser
    tentado. É a outra metade do apontamento — o laço abortava."""
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    _espia(monkeypatch, user_id)
    _destinos_whatsapp(monkeypatch, user_id, 3)
    tentados = _send_template_roteiro(monkeypatch, {
        0: RuntimeError("transporte caiu"),   # 1º já falha: nada a "preservar"
        1: _OK,
        2: _OK,
    })

    _tick()

    assert len(tentados) == 3, \
        f"o laço abortou no destino que levantou — tentou só {len(tentados)}"
    assert _detalhes_do_evento(user_id)["whatsapp"] is True


def test_todos_os_destinos_aceitos_conta_todos(user_id, monkeypatch):
    """POSITIVO: três destinos, três aceitos, `whatsapp: true`. Sem este caso o
    grupo passaria num `_wa_lembrete` que devolvesse True sempre — inclusive
    quando nada saiu."""
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    _espia(monkeypatch, user_id)
    _destinos_whatsapp(monkeypatch, user_id, 3)
    tentados = _send_template_roteiro(monkeypatch, {0: _OK, 1: _OK, 2: _OK})

    _tick()

    assert len(tentados) == 3
    assert _detalhes_do_evento(user_id)["whatsapp"] is True


def test_todos_os_destinos_falhando_grava_false(user_id, monkeypatch):
    """O piso do `any`: nenhum destino aceito → `false`. É o que separa "algum
    aceitou" de "tentou alguém"."""
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _espia(monkeypatch, user_id)
    _destinos_whatsapp(monkeypatch, user_id, 2)
    tentados = _send_template_roteiro(
        monkeypatch, {0: RuntimeError("500"), 1: None})   # 500 e depois 401

    _tick()

    assert len(tentados) == 2
    assert len(enviados) == 1
    assert _detalhes_do_evento(user_id)["whatsapp"] is False
