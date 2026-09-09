"""
tests/test_payment_reminder_wa_revalida.py — o WhatsApp × o estado que mudou
DURANTE o envio do e-mail.

A revalidação do lote (`db.dunning.lembrete_ainda_vale`) roda antes do e-mail e
fecha a janela "pagou durante o LOTE" (célula nº 28). Sobra uma janela menor e
real: o envio do e-mail é uma requisição HTTP a serviço externo, e o WhatsApp
sai DEPOIS dela com o veredito daquela leitura. Quem paga nesse intervalo recebe um
e-mail correto (era verdade quando saiu) e, em seguida, um WhatsApp dizendo que
a cobrança está pendente — com `whatsapp: true` no registro.

É a célula nº 28 no canal IRMÃO (célula nº 31 de
`docs/dunning_estados_eventos.md`), a instância que a rodada anterior não
fechou. O conserto é `db.dunning.ciclo_de_atraso_aberto`, lido DENTRO de
`core.services.payment_reminder_wa._wa_lembrete` — o mesmo lugar onde o
consentimento do canal já é lido fresco, e não no call site.

**A dedupe continua sendo gravada, de propósito.** O lembrete FOI entregue, por
e-mail; não gravar faria o e-mail sair de novo no tick seguinte, que é uma
regressão pior que o WhatsApp errado. A razão da recusa mora no `logger.info`,
como já mora na ramificação do opt-out: `whatsapp: false` é o veredito de VÁRIAS
saídas diferentes (`grep -n "return False" core/services/payment_reminder_wa.py`
lista as do canal), e um campo novo nos `details` não pagaria por si. O `except`
do laço do chamador NÃO acrescenta um sentido a `whatsapp: false`, ao contrário
do que este parágrafo já disse: em `core/services/payment_reminder.py` o
`log_system_event_sync` está DENTRO do `try`, então quando aquele `except`
dispara não se grava `payment_reminder_sent` nenhum — o desfecho é "sem
registro", não `whatsapp: false`. **A contagem não vem escrita aqui** — este
parágrafo dizia "cinco" no mesmo commit que acrescentou duas saídas (§2).

Arquivo próprio porque este grupo não cabe em `test_payment_reminder_revalida.py`:
somados, os dois passariam do teto de 350 de `tests/test_max_lines_python.py`
(meça com `wc -l`; não é que o irmão esteja perto do teto sozinho — quem está é
`test_payment_reminder_whatsapp.py`, e uma versão anterior deste parágrafo
publicou a razão errada). Helpers por
IMPORT (§0.7), inclusive a fixture `_ambiente` de
`test_payment_reminder_whatsapp.py`, que é quem define
`WA_TEMPLATE_PAYMENT_REMINDER` — sem ela este caminho é dormente.

CONTROLE NEGATIVO — em `core/services/payment_reminder_wa.py::_wa_lembrete`,
chame `db.dunning.ciclo_de_atraso_aberto(user_id)` descartando o retorno:
    VERMELHO: test_pagou_durante_o_envio_do_email_nao_recebe_whatsapp
              test_status_saiu_da_lista_durante_o_envio_do_email_nao_recebe_whatsapp
**Descartar o retorno, e não "apagar o bloco"** — as duas NÃO são a mesma
injeção, e a diferença é o CONTROLE 4 abaixo: apagar o bloco leva o `except`
junto, e aí cai também
`test_revalidacao_que_levanta_nao_derruba_o_tick_e_nao_manda` (medido: 3
vermelhos, não 2). Uma versão anterior deste bloco oferecia as duas como
equivalentes, o que era verdade antes de o controle 4 existir e virou falso com
ele. O corolário está em `docs/controles_declarados.md` ("Se duas variantes
produzem o MESMO vermelho, fique com uma; se produzem vermelhos DIFERENTES, são
injeções diferentes — nomeie qual"), e ele foi ESCRITO nesta rodada, a partir
deste caso: antes só existia a metade do "mesmo vermelho".

CONTROLE NEGATIVO 2, que discrimina o SEGUNDO TERMO do predicado — em
`db.dunning.ciclo_de_atraso_aberto`, troque `list(PAST_DUE_PAYMENT_STATUSES)`
por `list(PAST_DUE_PAYMENT_STATUSES) + ["active"]`:
    VERMELHO: test_status_saiu_da_lista_durante_o_envio_do_email_nao_recebe_whatsapp,
              e SÓ ele — no outro caso o relógio é zerado, então
              `past_due_since is not null` sozinho ainda barra. É essa diferença
              que prova que o predicado tem DOIS termos e não um.

**Esta injeção ALARGA o segundo termo em vez de apagá-lo, e a forma é o
conserto de um controle que mentia.** Duas redações anteriores mandavam APAGAR o
predicado do `where`, e cada uma invertia a leitura pelo SEU mecanismo — o mapa
é este, e não "as duas quebram de qualquer jeito":

  • 1ª redação (apague o predicado): apagando só o texto sobra
    `1 placeholders but 2 parameters were passed`;
  • 2ª redação (apague o predicado E o valor da tupla): apagando a LINHA
    inteira, a concatenação encosta na tupla e vira `'str' object is not
    callable`. Nesta redação, apagar só o TEXTO dava o vermelho correto e
    único — o defeito era depender de o leitor adivinhar o que preservar.

Nos dois mecanismos a exceção cai no `except` fail-closed do gate, o canal
recusa TUDO, **o vermelho declarado PASSA** e quem seguiu a instrução conclui o
oposto (medido: até 11 vermelhos no grupo largo, incluindo arquivos irmãos). A
patologia entrou no `docs/controles_declarados.md` nesta rodada. Alargar não apaga
nada, então não há expressão para quebrar e a leitura literal é única — as duas
grafias plausíveis (`+ ["active"]` e `[*PAST_DUE_PAYMENT_STATUSES, "active"]`)
foram medidas e dão o MESMO vermelho.

CONTROLE NEGATIVO 3, que amarra o predicado à CONSTANTE — em
`db.dunning.ciclo_de_atraso_aberto`, troque `list(PAST_DUE_PAYMENT_STATUSES)`
por `["past_due"]`:
    VERMELHO: test_conta_em_unpaid_recebe_whatsapp, e só ele. Sem este caso a
              divergência entre o gate e o funil é invisível: `unpaid` e
              `incomplete` estão na constante e o funil os ACEITA, então a conta
              receberia e-mail e nunca WhatsApp, em silêncio. É a duplicação
              deliberada do predicado sendo amarrada por teste, que é o que o
              §0.7 pede de quem aceita duplicar.

CONTROLE NEGATIVO 4, o fail-closed — em
`core/services/payment_reminder_wa.py::_wa_lembrete`, apague o `except` que
embrulha a chamada a `ciclo_de_atraso_aberto` (desidentando o corpo do `try`):
    VERMELHO: test_revalidacao_que_levanta_nao_derruba_o_tick_e_nao_manda, e só
              ele. Sem o `except`, a exceção sobe até o `try` do laço do chamador
              e nenhum `payment_reminder_sent` é gravado.

Regra dos controles: predicado citado, só os VERMELHOS nomeados, sem `N
passed` — ver `docs/controles_declarados.md`.

CONTROLE POSITIVO: test_sem_mudanca_durante_o_envio_o_whatsapp_continua_saindo.
O conserto RESTRINGE, e este caso é o que separa "o gate discrimina" de "o gate
recusa tudo". **Sem o contrafactual inflado**: uma versão anterior dizia que sem
ele "o grupo passaria" num gate que recusa tudo, e isso é falso — com o gate
recusando tudo caem 11 casos, três neste arquivo (medido). O positivo não é a
única barreira contra o gate cego; ele é a barreira EXPLÍCITA, e é por isso que
tem nome.

POSITIVO DO DESENHO: test_engagement_opt_out_durante_o_envio_nao_bloqueia_o
_whatsapp **e** test_revalidacao_que_levanta_nao_derruba_o_tick_e_nao_manda —
os DOIS reprovam a implementação errada (reusar `lembrete_ainda_vale` dentro do
`_wa_lembrete`, arrastando o consentimento do canal de E-MAIL): o primeiro
porque o opt-out de e-mail passaria a calar o WhatsApp, o segundo porque a
implementação errada não chama mais `ciclo_de_atraso_aberto` e o `monkeypatch`
do controle 4 deixa de levantar. Medido: 2 vermelhos, não 1 — o controle 4
mudou este conjunto e a declaração ficou para trás, a mesma classe que o bloco
do CONTROLE NEGATIVO acima registra.
"""
from __future__ import annotations

import pytest  # noqa: F401  (usado pelas fixtures importadas)

from db.connection import get_conn
from test_payment_reminder import _inadimplente, _limpar_eventos, _tick
from test_payment_reminder_revalida import _eventos_de_dedupe
from test_payment_reminder_whatsapp import (  # noqa: F401
    _OK,
    _ambiente,
    _detalhes_do_evento,
    _send_template_roteiro,
    _um_destino_whatsapp,
)


def _email_que_muda_o_banco(monkeypatch, uid: int, sql: str) -> list:
    """`send_payment_reminder_email` que MUDA O BANCO e devolve True.

    É literalmente "o pagamento completou enquanto a requisição HTTP do e-mail
    estava em voo": o e-mail saiu (True) e, quando o `_wa_lembrete` roda logo
    depois, o estado no banco já é outro. **Nada da revalidação é mockado** — o
    `check_payment_reminder` é o de produção e a leitura do gate é a real, no
    banco real; o que a injeção controla é só o instante da mudança.

    Filtra por endereço como o `_espia` do irmão: o funil varre a base inteira e
    resíduo de outro teste no mesmo banco viraria falha intermitente.
    """
    from core.services import email_service
    meus: list = []
    alvo = f"dun-{uid}@t.local"

    def _fake(to, dash=""):
        if to != alvo:
            return True
        meus.append(to)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uid,))
            conn.commit()
        from db_support import invalidate_auth_user_cache
        invalidate_auth_user_cache(uid)
        return True

    monkeypatch.setattr(email_service, "send_payment_reminder_email", _fake)
    return meus


def test_pagou_durante_o_envio_do_email_nao_recebe_whatsapp(user_id, monkeypatch):
    """O `invoice.paid` cai enquanto o e-mail está em voo: o e-mail estava certo
    e o WhatsApp não pode mais sair.

    As quatro asserções são coisas diferentes: o e-mail SAIU (não é o caso de
    abortar tudo), o template NÃO foi tentado (a mensagem seria falsa), o
    registro diz a verdade sobre isso, e a dedupe FOI gravada — o lembrete foi
    entregue por e-mail, e não gravar reenviaria o e-mail no tick seguinte.
    """
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _email_que_muda_o_banco(
        monkeypatch, user_id,
        "update auth_accounts set past_due_since = null where user_id = %s")
    _um_destino_whatsapp(monkeypatch, user_id)
    tentados = _send_template_roteiro(monkeypatch, {0: _OK})

    _tick()

    assert len(enviados) == 1, "o e-mail (caminho garantido) não saiu"
    assert tentados == [], \
        "WhatsApp saiu dizendo 'cobrança pendente' para quem acabou de pagar"
    assert _detalhes_do_evento(user_id) == {"email": True, "whatsapp": False}
    assert _eventos_de_dedupe(user_id) == 1, \
        "dedupe não gravada: o e-mail já entregue sairia de novo no tick seguinte"


def test_status_saiu_da_lista_durante_o_envio_do_email_nao_recebe_whatsapp(
        user_id, monkeypatch):
    """O outro termo do MESMO predicado: o status sai de
    `PAST_DUE_PAYMENT_STATUSES` enquanto o e-mail está em voo.

    O status é trocado SEM tocar no relógio, de propósito (em produção quem faz
    os dois no mesmo UPDATE é o `set_payment_status_impl`). Um gate que checasse
    só `past_due_since is not null` passaria no teste anterior e reprovaria
    neste — é o que separa "o predicado tem dois termos" de "tem um".
    """
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _email_que_muda_o_banco(
        monkeypatch, user_id,
        "update auth_accounts set last_payment_status = 'active' where user_id = %s")
    _um_destino_whatsapp(monkeypatch, user_id)
    tentados = _send_template_roteiro(monkeypatch, {0: _OK})

    _tick()

    assert len(enviados) == 1
    assert tentados == [], "WhatsApp saiu com o status já fora da lista"
    assert _detalhes_do_evento(user_id) == {"email": True, "whatsapp": False}
    assert _eventos_de_dedupe(user_id) == 1


def test_sem_mudanca_durante_o_envio_o_whatsapp_continua_saindo(user_id, monkeypatch):
    """POSITIVO: nada muda durante o envio do e-mail e o WhatsApp sai igual.

    Mesmo caminho, mesma injeção, só sem a corrida (o SQL é um `select`). É o
    que separa "o gate discrimina" de "o gate recusa tudo" — a asserção
    EXPLÍCITA de que o caminho legítimo sobrevive ao conserto. (Não que sem ele
    "o grupo passaria": um gate que recusasse tudo derruba 11 casos, três aqui.
    O contrafactual inflado estava nesta docstring e no cabeçalho.)
    """
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _email_que_muda_o_banco(monkeypatch, user_id, "select %s")
    _um_destino_whatsapp(monkeypatch, user_id)
    tentados = _send_template_roteiro(monkeypatch, {0: _OK})

    _tick()

    assert len(enviados) == 1
    assert len(tentados) == 1, "o WhatsApp legítimo deixou de sair"
    assert _detalhes_do_evento(user_id) == {"email": True, "whatsapp": True}
    assert _eventos_de_dedupe(user_id) == 1


def test_engagement_opt_out_durante_o_envio_nao_bloqueia_o_whatsapp(
        user_id, monkeypatch):
    """O POSITIVO DO DESENHO: `engagement_opt_out` é o consentimento do canal de
    E-MAIL, e ele não pode calar o WhatsApp.

    A pessoa desligou os e-mails de engajamento enquanto este e-mail estava em
    voo. O ciclo continua aberto e o canal de WhatsApp não foi desligado — a
    mensagem continua verdadeira e autorizada, e tem de sair. Uma implementação
    que reusasse `lembrete_ainda_vale` dentro do `_wa_lembrete` (para "não
    duplicar o predicado") reprova aqui: ela arrastaria o consentimento do canal
    errado, que é o espelho de
    `test_payment_reminder_consentimento.py::test_opt_out_de_whatsapp_nao_tira_o_email`.
    """
    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _email_que_muda_o_banco(
        monkeypatch, user_id,
        "update auth_accounts set engagement_opt_out = true where user_id = %s")
    _um_destino_whatsapp(monkeypatch, user_id)
    tentados = _send_template_roteiro(monkeypatch, {0: _OK})

    _tick()

    assert len(enviados) == 1
    assert len(tentados) == 1, \
        "o opt-out do canal de E-MAIL calou o canal de WhatsApp"
    assert _detalhes_do_evento(user_id)["whatsapp"] is True


def test_revalidacao_que_levanta_nao_derruba_o_tick_e_nao_manda(user_id, monkeypatch):
    """FAIL-CLOSED: a leitura do gate levanta, e o veredito é "não manda".

    O caminho já estava documentado (o `except` do gate) e **em zero asserção**.
    Ele é alcançável: uma leitura ruim o exercitou por acidente ao reescrever o
    CONTROLE NEGATIVO 2, derrubando testes de arquivos irmãos.

    As asserções separam quatro coisas que não são a mesma: o tick NÃO morre (o
    chamador embrulha `check_payment_reminder` inteira, e uma exceção que
    escapasse abandonaria os candidatos seguintes), o e-mail já saiu e continua
    valendo, o WhatsApp NÃO é tentado, e a dedupe é gravada — o lembrete foi
    entregue por e-mail, exatamente como nos casos de recusa acima.
    """
    from db import dunning as db_dunning

    _inadimplente(user_id, dias=6.5)
    _limpar_eventos(user_id)
    enviados = _email_que_muda_o_banco(monkeypatch, user_id, "select %s")
    _um_destino_whatsapp(monkeypatch, user_id)
    tentados = _send_template_roteiro(monkeypatch, {0: _OK})

    def _explode(uid):
        raise RuntimeError("banco fora do ar na revalidacao")

    monkeypatch.setattr(db_dunning, "ciclo_de_atraso_aberto", _explode)

    _tick()  # não pode levantar: o `raise` acima morre dentro do gate

    assert len(enviados) == 1, "o e-mail (caminho garantido) não saiu"
    assert tentados == [], "fail-closed violado: mandou WhatsApp sem conseguir ler"
    assert _detalhes_do_evento(user_id) == {"email": True, "whatsapp": False}
    assert _eventos_de_dedupe(user_id) == 1


def test_conta_em_unpaid_recebe_whatsapp(user_id, monkeypatch):
    """O gate lê a CONSTANTE, não `'past_due'` — e `unpaid` é o caso que prova.

    O funil aceita todo status de `PAST_DUE_PAYMENT_STATUSES`, mas a família
    inteira de testes do lembrete só exercitava `past_due` (confira:
    `grep -rn "unpaid\\|incomplete" tests/test_payment_reminder*.py`). Um gate
    que checasse `= 'past_due'` em vez da constante passaria em todos os outros
    casos deste arquivo e **divergiria do funil em silêncio**: a conta em
    `unpaid` receberia o e-mail e nunca o WhatsApp.

    Nada muda durante o envio aqui — este caso é sobre o CONJUNTO de estados que
    o gate aceita, não sobre a corrida.
    """
    _inadimplente(user_id, dias=6.5, status="unpaid")
    _limpar_eventos(user_id)
    enviados = _email_que_muda_o_banco(monkeypatch, user_id, "select %s")
    _um_destino_whatsapp(monkeypatch, user_id)
    tentados = _send_template_roteiro(monkeypatch, {0: _OK})

    _tick()

    assert len(enviados) == 1, "o funil aceita `unpaid`: o e-mail tinha de sair"
    assert len(tentados) == 1, \
        "gate divergiu do funil: `unpaid` recebe e-mail e não recebe WhatsApp"
    assert _detalhes_do_evento(user_id)["whatsapp"] is True
