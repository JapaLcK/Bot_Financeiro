"""
tests/test_open_finance_proactive_dedupe.py — os avisos proativos do Open Finance
(reconectar / salário) saem UMA vez por janela e deixam rastro quando falham.

`core/services/open_finance_proactive.py` roda a cada 6 h e, sem marcador, o
mesmo aviso saía 4x por dia enquanto a condição durasse; e o `except: pass` do
envio apagava a falha. Agora cada laço lê `recent_event_exists(<EVENT>, uid,
<DIAS>)` DEPOIS do `filtrar_por_acesso`, grava o marcador SÓ se algum alvo
recebeu, e loga WARNING com `user_id` e o TIPO da exceção (nunca o número nem
`str(exc)`). Continua dormente sem a env do template.

Banco real, `garantir_system_event_logs()`, e NUNCA mock de `recent_event_exists`:
o par leitura/escrita é o que está sendo medido.

CONTROLES NEGATIVOS DECLARADOS — em `core/services/open_finance_proactive.py`:
    remover o `if recent_event_exists(...)` de um laço
        VERMELHO: test_manda_uma_vez_por_janela[<reconectar|salario>]
                  (e test_marcador_de_um_usuario_nao_cala_outro[<o mesmo>]: sem
                  a leitura, o 1º usuário também recebe)
    remover o `if enviou:` (gravar o marcador incondicionalmente)
        VERMELHO: test_falha_do_envio_nao_grava_marcador_e_deixa_rastro[<os dois>]
    tratar `None` de `send_template` como sucesso de volta (descartar o retorno)
        VERMELHO: test_recusa_401_nao_grava_marcador_e_deixa_rastro[<o laço mutado>]
    voltar `named_body_params` de um laço a LISTA (`[banks]` / `[fmt_brl(...)]`)
        VERMELHO: test_send_template_real_recebe_parametro_nomeado[<o laço mutado>]
                  (`send_template` faz `.items()` → AttributeError antes do POST,
                  o `except` do laço engole, `sent == 0` e o marcador não sai).
                  É o ÚNICO caso deste arquivo que exercita o `send_template`
                  REAL — os outros o mockam inteiro e eram cegos a isso.

O espião devolve o dict do 2xx porque `None` é o 401 (token inválido) que o
`wa_client` loga em `whatsapp_token_invalid` e NÃO levanta — sem isso, o laço
contava o 401 como enviado e gravava o marcador de um usuário que ficou calado.

CONTROLE POSITIVO: a 2ª chamada de `test_falha_do_envio_nao_grava_marcador_e_deixa_rastro`
e de `test_recusa_401_nao_grava_marcador_e_deixa_rastro`
(envio volta a funcionar → o template sai) e `test_marcador_de_um_usuario_nao_cala_outro`
(isolamento do §0 do CLAUDE.md na LEITURA: o marcador de um usuário não cala
outro). Esse teste NÃO prova que a escrita usa o `user_id` certo — um marcador
gravado com `user_id=None` ou com outro uid passa nele, porque a leitura do
2º usuário continua não achando nada. Quem prova a escrita com o `user_id`
certo é `test_manda_uma_vez_por_janela`: marcador em outro uid → a 2ª chamada
manda de novo e ele cai.
"""
from __future__ import annotations

import logging

import pytest

from _billing_grants_helpers import garantir_system_event_logs
from core.observability import log_system_event_sync, recent_event_exists
from core.services import open_finance_proactive as ofp
from db import ensure_user

_FONE = "5511999990000"

# (função, env do template, event_type, dias da janela, detector patchado, retorno dele)
_CASOS = {
    "reconectar": (ofp.run_reconnect_notifications, "OF_RECONNECT_TEMPLATE_NAME",
                   ofp.OF_RECONNECT_EVENT, ofp.OF_RECONNECT_DEDUPE_DAYS,
                   "list_connections_needing_reconnect",
                   lambda uid: [{"institution_name": "Nubank"}]),
    "salario": (ofp.run_salary_notifications, "OF_SALARY_TEMPLATE_NAME",
                ofp.OF_SALARY_EVENT, ofp.OF_SALARY_DEDUPE_DAYS,
                "detect_open_finance_salary",
                lambda uid: {"launch_id": 1, "valor": 3500.0, "date": None}),
}


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def _armar(monkeypatch, caso: str, uids: list[int], enviar, alvo=lambda uid: [_FONE]):
    """Liga o template, restringe o laço a `uids` e troca detector, acesso, alvos
    e `send_template` (o laço faz `from adapters.whatsapp.wa_client import
    send_template` a cada rodada — o atributo do módulo é o que vale).
    `enviar=None` deixa o `send_template` REAL no lugar."""
    _, env, _, _, detector, resultado = _CASOS[caso]
    monkeypatch.setenv(env, "tpl")
    monkeypatch.setattr(ofp, "list_open_finance_user_ids", lambda: list(uids))
    monkeypatch.setattr(ofp, detector, resultado)
    monkeypatch.setattr(ofp, "filtrar_por_acesso", lambda ids: list(ids))
    monkeypatch.setattr(ofp, "_targets", alvo)
    if enviar is not None:
        monkeypatch.setattr("adapters.whatsapp.wa_client.send_template", enviar)


def _espiao(chamadas: list, erro: Exception | None = None, recusar: bool = False):
    """Devolve o formato real do 2xx (`{"messages": [{"id": ...}]}`) porque `None`
    é o 401 da Meta, que `send_template` NÃO levanta — um espião sem `return`
    deixaria a suíte cega a esse desfecho. `recusar=True` simula o 401."""
    def _send(to, name, **kw):
        if erro:
            raise erro
        chamadas.append(to)
        if recusar:
            return None
        return {"messages": [{"id": f"wamid.{len(chamadas)}"}]}
    return _send


@pytest.mark.parametrize("caso", list(_CASOS))
def test_manda_uma_vez_por_janela(user_id, monkeypatch, caso):
    rodar, _, event, dias, _, _ = _CASOS[caso]
    chamadas: list = []
    _armar(monkeypatch, caso, [user_id], _espiao(chamadas))

    assert rodar()["sent"] == 1
    assert recent_event_exists(event, user_id, dias) is True, "marcador não gravado"
    assert rodar()["sent"] == 0, "2ª rodada dentro da janela mandou de novo"
    assert chamadas == [_FONE]


@pytest.mark.parametrize("caso", list(_CASOS))
def test_falha_do_envio_nao_grava_marcador_e_deixa_rastro(user_id, monkeypatch, caplog, caso):
    rodar, _, event, dias, _, _ = _CASOS[caso]
    chamadas: list = []
    _armar(monkeypatch, caso, [user_id], _espiao(chamadas, RuntimeError("meta fora")))

    with caplog.at_level(logging.WARNING, logger=ofp.logger.name):
        assert rodar()["sent"] == 0
    assert recent_event_exists(event, user_id, dias) is False, (
        "marcador gravado SEM envio — o usuário ficaria sem aviso pela janela inteira")
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1, [r.getMessage() for r in caplog.records]
    texto = avisos[0].getMessage()
    assert f"user_id={user_id}" in texto and "erro=RuntimeError" in texto, texto
    assert _FONE not in texto and "meta fora" not in texto, texto

    # Rodada seguinte, envio OK → o template sai.
    _armar(monkeypatch, caso, [user_id], _espiao(chamadas))
    assert rodar()["sent"] == 1
    assert chamadas == [_FONE]


@pytest.mark.parametrize("caso", list(_CASOS))
def test_recusa_401_nao_grava_marcador_e_deixa_rastro(user_id, monkeypatch, caplog, caso):
    """401 da Meta: `send_template` devolve `None` sem levantar. Não é envio."""
    rodar, _, event, dias, _, _ = _CASOS[caso]
    chamadas: list = []
    _armar(monkeypatch, caso, [user_id], _espiao(chamadas, recusar=True))

    with caplog.at_level(logging.WARNING, logger=ofp.logger.name):
        assert rodar()["sent"] == 0, "401 contado como enviado"
    assert recent_event_exists(event, user_id, dias) is False, (
        "marcador gravado no 401 — o usuário ficaria calado pela janela inteira")
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1, [r.getMessage() for r in caplog.records]
    texto = avisos[0].getMessage()
    assert f"user_id={user_id}" in texto and "token_invalido" in texto, texto
    assert _FONE not in texto, texto

    # Token renovado, envio OK → o template sai.
    chamadas.clear()
    _armar(monkeypatch, caso, [user_id], _espiao(chamadas))
    assert rodar()["sent"] == 1
    assert chamadas == [_FONE]


@pytest.mark.parametrize("caso", list(_CASOS))
def test_marcador_de_um_usuario_nao_cala_outro(user_id, monkeypatch, caso):
    rodar, _, event, _, _, _ = _CASOS[caso]
    outro = user_id + 1
    ensure_user(outro)  # o conftest apaga o órfão no fim do teste
    chamadas: list = []
    _armar(monkeypatch, caso, [user_id, outro], _espiao(chamadas),
           alvo=lambda uid: [f"55{uid}"])
    log_system_event_sync("info", event, "ja avisado", source="open_finance_proactive",
                          user_id=user_id)

    assert rodar()["sent"] == 1
    assert chamadas == [f"55{outro}"], "o marcador do 1º usuário calou o 2º"


# (nome do parâmetro nomeado, texto esperado) — o texto vem dos detectores de `_CASOS`.
_PARAM_ESPERADO = {"reconectar": (ofp.OF_RECONNECT_PARAM, "Nubank"),
                   "salario": (ofp.OF_SALARY_PARAM, "R$ 3.500,00")}


@pytest.mark.parametrize("caso", list(_CASOS))
def test_send_template_real_recebe_parametro_nomeado(user_id, monkeypatch, caplog, caso):
    """`send_template` REAL, só o `requests.post` mockado: o laço tem de passar
    `named_body_params` como dict `{nome: texto}`, que vira `parameters[0]`
    com `parameter_name`. Lista quebra em `.items()` antes do POST."""
    rodar, _, event, dias, _, _ = _CASOS[caso]
    nome, texto = _PARAM_ESPERADO[caso]
    posts: list = []

    class _Resp:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"messages": [{"id": "wamid.x"}]}

    def _post(url, **kw):
        posts.append(kw["json"])
        return _Resp()

    _armar(monkeypatch, caso, [user_id], None)
    monkeypatch.setenv("WA_TOKEN", "tok")
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "123")
    monkeypatch.setattr("requests.post", _post)

    with caplog.at_level(logging.WARNING, logger=ofp.logger.name):
        assert rodar()["sent"] == 1
    assert "erro=AttributeError" not in caplog.text, caplog.text
    assert len(posts) == 1 and posts[0]["to"] == _FONE, posts
    param = posts[0]["template"]["components"][0]["parameters"][0]
    assert param == {"type": "text", "parameter_name": nome, "text": texto}, param
    assert recent_event_exists(event, user_id, dias) is True, "marcador não gravado"
