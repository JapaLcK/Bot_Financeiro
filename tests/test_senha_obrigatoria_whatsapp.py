"""Bot: o número da conta paga sem senha não se liga sozinho (PR 4 do funil v3).

A conta da /assinar nasce com o telefone digitado por quem pagou. Sem este
bloqueio, a 1ª mensagem desse número mesclava e vinculava a conta pelo
auto-vínculo por telefone (`attempt_whatsapp_phone_link_impl`), antes de o
e-mail ser provado. Pela conversa real (`process_message`), estado no Postgres;
só o envio ao WhatsApp é falso.

Controle negativo: tirar o `if conta_sem_credencial(target)` do auto-vínculo
deixa vermelho o primeiro teste (a mensagem grava o gasto e liga o número).
Positivo: depois da senha, a mesma mensagem liga e grava.
"""
import uuid

import pytest

import db
from adapters.whatsapp import wa_runtime as wr
from adapters.whatsapp.wa_parse import InboundMessage
from db.connection import get_conn
from test_senha_obrigatoria import _com_senha, conta_paga_sem_credencial, env  # noqa: F401 (env é autouse)


@pytest.fixture
def enviadas(monkeypatch):
    saida = []
    for nome in ("send_text", "send_interactive_buttons", "send_interactive_list"):
        monkeypatch.setattr(wr, nome, lambda *a, **k: saida.append(k.get("body") or str(a)))
    monkeypatch.setattr(wr, "send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr(wr, "send_welcome", lambda *a, **k: saida.append("WELCOME"))
    return saida


def _manda(wa_id: str, texto: str) -> None:
    wr.process_message(InboundMessage(wa_id=wa_id, text=texto, timestamp="1", attachments=[],
                                      raw={"id": f"wamid.{uuid.uuid4().hex}", "type": "text"}))


def _fone(uid: int) -> str:
    return db.get_auth_user(uid)["phone_e164"].lstrip("+")


def _q(sql, args):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        linha = cur.fetchone()
        conn.commit()
    return linha


def _dono_do_numero(wa_id: str):
    linha = _q("select user_id from user_identities where provider='whatsapp' and external_id=%s", (wa_id,))
    return linha and int(linha["user_id"])


def _gastos(uid: int) -> int:
    return _q("select count(*) as n from launches where user_id=%s", (uid,))["n"]


def _vinculo(uid: int):
    """O que o auto-vínculo grava na conta: o telefone confirmado e o WhatsApp dela."""
    conta = _q("select phone_status, phone_confirmed_at, whatsapp_verified_at"
               " from auth_accounts where user_id=%s", (uid,))
    wa = _q("select count(*) as n from user_identities where provider='whatsapp' and user_id=%s", (uid,))
    return dict(conta or {}), wa["n"]


def test_sem_senha_nao_liga_nem_grava_e_com_senha_liga(enviadas):
    uid, _, _ = conta_paga_sem_credencial()
    wa_id = _fone(uid)

    _manda(wa_id, "gastei 50 mercado")
    assert enviadas == [wr.PRECISA_SENHA_WA], enviadas
    assert _dono_do_numero(wa_id) != uid, "o número foi ligado à conta sem senha"
    assert _gastos(uid) == 0

    _manda(wa_id, "Gastei 50 no mercado")  # de novo, com maiúscula e preposição: mesma resposta
    assert enviadas[-1] == wr.PRECISA_SENHA_WA and _gastos(uid) == 0

    _com_senha(uid)
    enviadas.clear()
    _manda(wa_id, "gastei 50 mercado")
    assert _dono_do_numero(wa_id) == uid, enviadas
    assert _gastos(uid) == 1, enviadas
    assert wr.PRECISA_SENHA_WA not in enviadas


# Número JÁ ligado à conta sem senha (vínculo anterior ao PR 4): current == target,
# o auto-vínculo nem chega na checagem dele. Controle negativo: tirar o
# `if conta_sem_credencial(uid)` do `process_message` deixa este vermelho (grava o gasto).
def test_numero_ja_ligado_a_conta_sem_senha_nao_grava(enviadas):
    uid, _, _ = conta_paga_sem_credencial()
    wa_id = _fone(uid)
    db.bind_identity("whatsapp", wa_id, uid)
    antes = _vinculo(uid)

    for texto in ("gastei 50 mercado", "Gastei R$ 50 no açougue"):
        enviadas.clear()
        _manda(wa_id, texto)
        assert enviadas == [wr.PRECISA_SENHA_WA], (texto, enviadas)
    assert _gastos(uid) == 0
    # Guarda depois do auto-vínculo seguiria verde acima e confirmaria o telefone aqui.
    assert _vinculo(uid) == antes

    _com_senha(uid)
    enviadas.clear()
    _manda(wa_id, "Gastei R$ 50 no açougue")
    assert _gastos(uid) == 1, enviadas
    assert wr.PRECISA_SENHA_WA not in enviadas


# Bug do Tester: A usa o bot pelo WhatsApp e tem dados; B paga na /assinar com o
# número de A. Sem a checagem do remetente, A recebia "crie sua senha" sem ter
# senha para criar. `com_plano`: A é conta do site paga, ligada a este número por
# identidade (o telefone do cadastro é outro), e o gasto grava nela. Só-WhatsApp
# não tem plano: cai no convite da /precos, como qualquer turno sem plano.
# Controle negativo: tirar o `_tem_dados_financeiros` do ramo
# `conta_sem_credencial(target)` do auto-vínculo deixa os dois vermelhos.
@pytest.mark.parametrize("com_plano", [False, True])
def test_remetente_com_dados_segue_na_conta_dele(enviadas, com_plano):
    b, _, _ = conta_paga_sem_credencial()
    wa_id = _fone(b)
    if com_plano:
        a, _, _ = conta_paga_sem_credencial()
        _com_senha(a)
        db.bind_identity("whatsapp", wa_id, a)
    else:
        a = db.get_or_create_canonical_user("whatsapp", wa_id)
    db.add_launch_and_update_balance(a, "despesa", 50, None, "mercado")
    antes = _vinculo(b)

    _manda(wa_id, "gastei 50 mercado")
    assert wr.PRECISA_SENHA_WA not in enviadas, enviadas
    assert com_plano or "/precos" in enviadas[0], enviadas
    assert _dono_do_numero(wa_id) == a, "o número mudou de conta (vínculo ou merge)"
    assert _gastos(a) == (2 if com_plano else 1), enviadas
    assert _gastos(b) == 0 and _vinculo(b) == antes


def test_numero_ligado_a_conta_com_senha_grava(enviadas):
    uid, _, _ = conta_paga_sem_credencial()
    _com_senha(uid)
    wa_id = _fone(uid)
    db.bind_identity("whatsapp", wa_id, uid)

    _manda(wa_id, "gastei 50 mercado")
    assert _gastos(uid) == 1, enviadas
    assert wr.PRECISA_SENHA_WA not in enviadas


# Só-WhatsApp (sem auth_accounts): `conta_sem_credencial` é False e o turno segue o
# fluxo de antes, o convite de cadastro. Uma checagem de "sem senha" que não exigisse
# a linha em auth_accounts responderia PRECISA_SENHA_WA aqui. (Só-WhatsApp não grava
# gasto nem antes deste PR: sem auth_accounts não há plano, e o no_match para o turno.)
def test_numero_so_whatsapp_segue_o_fluxo_de_antes(enviadas):
    wa_id = f"5511{uuid.uuid4().int % 10**9:09d}"
    for _ in range(2):  # a 2ª já com a identidade do WhatsApp gravada pela 1ª
        enviadas.clear()
        _manda(wa_id, "gastei 50 mercado")
        assert len(enviadas) == 1 and "/cadastro" in enviadas[0], enviadas
    assert not db.conta_sem_credencial(_dono_do_numero(wa_id))


def _clique(wa_id: str, botao: str) -> None:
    wr.process_message(InboundMessage(
        wa_id=wa_id, text="", timestamp="1", attachments=[],
        raw={"id": f"wamid.{uuid.uuid4().hex}", "type": "interactive",
             "interactive": {"type": "button_reply", "button_reply": {"id": botao, "title": "x"}}}))


# Apontamento do Codex no #716: a guarda de conta sem credencial rodava antes dos
# botões de opt-out, e quem não tem senha não conseguia PARAR as notificações
# (`_WA_INTERACTIVE_ISENTOS`). Controle negativo: tirar o `_tratar_opt_out` de
# dentro da guarda deixa os dois vermelhos (a resposta vira PRECISA_SENHA_WA e a
# preferência não muda). Positivo: texto continua recebendo só o PRECISA_SENHA_WA.
@pytest.mark.parametrize("botao,desligou", [
    ("daily_report_disable", lambda uid: not db.get_daily_report_prefs(uid)["enabled"]),
    ("whatsapp_updates_disable", lambda uid: db.get_whatsapp_updates_opt_out(uid)),
])
def test_conta_sem_senha_consegue_desligar_notificacoes(enviadas, botao, desligou):
    uid, _, _ = conta_paga_sem_credencial()
    wa_id = _fone(uid)
    db.bind_identity("whatsapp", wa_id, uid)
    antes = _vinculo(uid)
    assert not desligou(uid)

    _clique(wa_id, botao)
    assert desligou(uid), enviadas
    assert len(enviadas) == 1 and enviadas[0] != wr.PRECISA_SENHA_WA, enviadas
    if botao == "daily_report_disable":
        assert enviadas == [wr.h_report.disable(uid)], enviadas
    assert _vinculo(uid) == antes and _gastos(uid) == 0

    enviadas.clear()
    _manda(wa_id, "gastei 50 mercado")
    assert enviadas == [wr.PRECISA_SENHA_WA] and _gastos(uid) == 0


# Apontamento do Codex no #716: o envio de atualizações ia ao `phone_e164` da conta
# paga mesmo sem o número ligado. Desde a #721 o `send_update_whatsapp` só envia a
# número em `user_identities`, mas o botão de mensagens já enviadas continua clicável. O
# clique chega com o uid do usuário só-WhatsApp, cai no `precisa_senha` do
# auto-vínculo e tem de desligar a conta ALVO. Controle negativo: trocar o
# `_tratar_opt_out` do ramo `precisa_senha` pelo PRECISA_SENHA_WA de antes deixa os
# dois vermelhos. Positivo: texto em seguida recebe o PRECISA_SENHA_WA e não liga.
@pytest.mark.parametrize("botao,desligou", [
    ("daily_report_disable", lambda uid: not db.get_daily_report_prefs(uid)["enabled"]),
    ("whatsapp_updates_disable", lambda uid: db.get_whatsapp_updates_opt_out(uid)),
])
def test_numero_nao_ligado_da_conta_sem_senha_desliga_a_conta_paga(enviadas, botao, desligou):
    b, _, _ = conta_paga_sem_credencial()
    wa_id = _fone(b)
    antes = _vinculo(b)
    assert not desligou(b)

    _clique(wa_id, botao)
    assert desligou(b), enviadas
    assert len(enviadas) == 1 and enviadas[0] != wr.PRECISA_SENHA_WA, enviadas
    assert _dono_do_numero(wa_id) != b and _vinculo(b) == antes and _gastos(b) == 0

    enviadas.clear()
    _manda(wa_id, "gastei 50 mercado")
    assert enviadas == [wr.PRECISA_SENHA_WA], enviadas
    assert _dono_do_numero(wa_id) != b and _vinculo(b) == antes and _gastos(b) == 0


# `remetente_com_dados`: o número é de A, e B (sem credencial) o digitou e recebe
# envios nele. O clique é "parem de mandar para este número": desliga os dois, com
# uma resposta. Controle negativo: tirar o `target_user_id` da chamada do
# `_tratar_opt_out` na cadeia interativa deixa este vermelho (B segue ligado).
@pytest.mark.parametrize("com_plano", [False, True])
def test_remetente_com_dados_opt_out_desliga_os_dois(enviadas, com_plano):
    b, _, _ = conta_paga_sem_credencial()
    wa_id = _fone(b)
    if com_plano:
        a, _, _ = conta_paga_sem_credencial()
        _com_senha(a)
        db.bind_identity("whatsapp", wa_id, a)
    else:
        a = db.get_or_create_canonical_user("whatsapp", wa_id)
    db.add_launch_and_update_balance(a, "despesa", 50, None, "mercado")
    antes = _vinculo(b)

    _clique(wa_id, "whatsapp_updates_disable")
    assert db.get_whatsapp_updates_opt_out(a) and db.get_whatsapp_updates_opt_out(b), enviadas
    assert len(enviadas) == 1 and enviadas[0] != wr.PRECISA_SENHA_WA, enviadas
    assert _dono_do_numero(wa_id) == a, "o número mudou de conta (vínculo ou merge)"
    assert _gastos(a) == 1 and _gastos(b) == 0 and _vinculo(b) == antes
