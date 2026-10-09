"""#722: `vincular CODIGO` de número novo parava no paywall.

Pela conversa real (`process_message`), Postgres real; só o envio é falso.

O número novo (sem conta) é barrado pelo `_paywall_gate`, e a isenção de lá só
cobria ajuda e billing: o código nunca chegava ao handler. A isenção nova
devolve a resposta do vínculo (não deixa o turno seguir), então nenhuma
pendência velha de quem está barrado engole o código. Para o pagante, a
pendência do `wa_runtime` ainda consome o texto antes do gate (achado A3, issue
separada).

Controles negativos (medidos no PR, ver o relato):
- tirar a isenção do `account.link`/`account.vincular` no `_paywall_gate` deixa
  vermelho `test_numero_novo_vincula_e_grava_na_conta` (volta o "/precos");
- tirar o teto (`_tentativas_estouradas`) deixa vermelho
  `test_teto_de_tentativas` (a 6ª tentativa liga); tirar só a trava do `link()`
  deixa vermelho o caso `link` dele;
- responder sempre o texto do #607 no `except db.MergeRefused` (sem o mapa da D6)
  deixa vermelho `test_recusa_diz_o_motivo[stripe]`; o Open Finance caindo no
  motivo `origem_presa` deixa vermelho o `[open_finance]`; sem a entrada
  `autoindicacao` no mapa, os dois `[autoindicacao_*]` ficam vermelhos; Stripe
  antes de Open Finance deixa vermelho o `[open_finance_e_stripe]`;
- tirar o `already_linked` do ramo sem telefone de
  `attempt_whatsapp_phone_link_impl` deixa vermelhos os dois
  `test_numero_ligado_por_codigo_*` e a 2ª metade do primeiro teste (o número
  ligado recebe o convite de cadastro em toda mensagem).
Positivos (o caminho que a correção não pode abrir nem fechar):
`test_numero_novo_sem_codigo_continua_no_convite`, e no `test_teto_de_tentativas`
o outro número que liga no mesmo momento e a janela vencida que volta a ligar.
"""
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

import core.handle_incoming as hi
import db
from _paywall_gate_helpers import cadastro_novo, com_plano
from core.crypto import hash_pii
from core.types import IncomingMessage
from test_merge_users_recusa import _com_conexao_of
from test_senha_obrigatoria import _com_senha, conta_paga_sem_credencial
from test_senha_obrigatoria_whatsapp import _dono_do_numero, _gastos, _manda, _q, enviadas  # noqa: F401

VINCULADO = "✅ WhatsApp vinculado"
MUITAS = "Muitas tentativas de código. Espere 15 minutos e gere um código novo no site."


def _numero_novo() -> str:
    return f"5511{secrets.randbelow(10**9):09d}"


def test_numero_novo_vincula_e_grava_na_conta(enviadas):
    b = com_plano()
    code = db.create_link_code(b)
    n = _numero_novo()

    _manda(n, f"vincular {code}")
    assert _dono_do_numero(n) == b, enviadas
    assert len(enviadas) == 1 and enviadas[0].startswith(VINCULADO), enviadas

    enviadas.clear()
    _manda(n, "gastei 50 mercado")
    assert _gastos(b) == 1, enviadas


@pytest.mark.parametrize("molde", ["Vincular: {}", "link {}.", "  vincular   {}  "])
def test_variantes_de_digitacao(enviadas, molde):
    b = com_plano()
    n = _numero_novo()

    _manda(n, molde.format(db.create_link_code(b)))
    assert _dono_do_numero(n) == b, enviadas
    assert "/precos" not in enviadas[0], enviadas


def test_numero_novo_sem_codigo_continua_no_convite(enviadas):
    n = _numero_novo()

    _manda(n, "gastei 50 mercado")
    assert "Ainda não tenho uma conta ligada" in enviadas[0], enviadas


def test_codigo_invalido_nao_liga(enviadas):
    n = _numero_novo()
    _manda(n, "oi")
    dono = _dono_do_numero(n)  # o usuário só-WhatsApp que o número acabou de criar

    enviadas.clear()
    _manda(n, "vincular 000000")
    assert enviadas[0].startswith("❌ Código inválido"), enviadas
    assert _dono_do_numero(n) == dono


def test_pendencia_velha_nao_engole_o_codigo(enviadas):
    b = com_plano()
    n = _numero_novo()
    a = db.get_or_create_canonical_user("whatsapp", n)
    db.add_launch_and_update_balance(a, "despesa", 50, None, "mercado")
    launch_id = db.latest_launch_id(a)
    db.set_pending_action(a, "recategorize_launch_text", {"launch_id": launch_id}, minutes=5)

    _manda(n, f"vincular {db.create_link_code(b)}")
    assert _dono_do_numero(n) == b, enviadas
    assert enviadas[0].startswith(VINCULADO), enviadas
    cat = _q("select categoria from launches where id=%s", (launch_id,))["categoria"]
    assert "vincular" not in (cat or "").lower(), cat


def test_conta_do_site_sem_plano_vincula_com_codigo_de_outra(enviadas):
    """D2(b): todo barrado pode mandar o código, não só o número novo."""
    a = cadastro_novo()
    n = _numero_novo()
    db.bind_identity("whatsapp", n, a)
    c = com_plano()

    _manda(n, f"vincular {db.create_link_code(c)}")
    assert _dono_do_numero(n) == c, enviadas
    assert enviadas[0].startswith(VINCULADO), enviadas
    # O que o `merge_users` faz com A hoje (registro, não decisão: é o PR-2). C já
    # tem login, então o `auth_accounts` de A não migra e some com a linha `users`.
    assert _q("select count(*) as n from users where id=%s", (a,))["n"] == 0
    assert _q("select count(*) as n from auth_accounts where user_id=%s", (a,))["n"] == 0


# D6 (dono): a recusa do `merge_users` responde o motivo verdadeiro. A origem é a
# conta do site barrada (sem plano) ligada ao número; o destino, C, tem plano.
# `stripe` é o A1 do Tester: ex-assinante pelo cartão recebia "já têm dados cada
# uma" sem ter lançamento nenhum. Compara com o TEXTO, não com a chave do mapa: o
# texto é o que o usuário lê, e chave ausente daria KeyError em vez de asserção.
_SUPORTE = "Fale com a gente em suporte@pigbankai.com."
_JUNTADA = "e ela não pode ser juntada a outra automaticamente. " + _SUPORTE
TEXTO = {
    "origem_presa": "⚠️ A conta que você usa aqui tem ou teve assinatura no site, " + _JUNTADA,
    "open_finance": "⚠️ A conta que você usa aqui tem banco conectado pelo Open Finance, " + _JUNTADA,
    "autoindicacao": ("⚠️ Não deu pra vincular: uma destas contas foi indicada pela outra no "
                      "programa de afiliados, e as duas não podem virar uma só. " + _SUPORTE),
    "dados_dos_dois_lados": ("⚠️ Não deu pra vincular: sua conta do site e esta conta já têm dados "
                             "cada uma, então não dá pra juntar as duas automaticamente.\n"
                             "Pra ter tudo num lugar só, use este número numa conta só."),
}


def _stripe(a, c):
    _q("update auth_accounts set stripe_customer_id = 'cus_teste' where user_id=%s returning 1", (a,))


def _afiliado(dono, indicado):
    af = _q("insert into affiliates(user_id, code) values (%s, %s) returning id",
            (dono, secrets.token_hex(5)))["id"]
    _q("insert into affiliate_referrals(affiliate_id, referred_user_id) values (%s, %s) returning 1",
       (af, indicado))


def _com_dados(a, c):
    db.add_launch_and_update_balance(a, "despesa", 10, None, "mercado")
    db.add_launch_and_update_balance(c, "despesa", 20, None, "mercado")


# Open Finance e Stripe juntos: vale o Open Finance (dono, D6), o que a pessoa
# consegue desfazer sozinha.
@pytest.mark.parametrize("prende,motivo", [
    (_stripe, "origem_presa"),
    (lambda a, c: _com_conexao_of(a, "UPDATED"), "open_finance"),  # vivo, e sem Stripe
    (lambda a, c: (_stripe(a, c), _com_conexao_of(a, "UPDATED")), "open_finance"),
    (_afiliado, "autoindicacao"),                    # a origem indicou o destino
    (lambda a, c: _afiliado(c, a), "autoindicacao"),  # o destino indicou a origem
    (_com_dados, "dados_dos_dois_lados"),
], ids=["stripe", "open_finance", "open_finance_e_stripe", "autoindicacao_origem_dona",
        "autoindicacao_destino_dono", "dados_dos_dois_lados"])
def test_recusa_diz_o_motivo(enviadas, prende, motivo):
    a = cadastro_novo()
    n = _numero_novo()
    db.bind_identity("whatsapp", n, a)
    c = com_plano()
    prende(a, c)

    _manda(n, f"vincular {db.create_link_code(c)}")
    assert enviadas == [TEXTO[motivo]], enviadas
    assert _dono_do_numero(n) == a and _gastos(c) == (1 if motivo == "dados_dos_dois_lados" else 0)


# O texto é montado no import: `SUPPORT_EMAIL=""` no ambiente não pode virar
# "Fale com a gente em .". Subprocesso porque o valor é lido uma vez, no boot.
def test_suporte_vazio_nao_deixa_texto_sem_email():
    codigo = ("from core.handlers import account as a\n"
              "print('\\n'.join([*a._RECUSA_POR_MOTIVO.values(), a._RECUSA_COLISAO]))")
    r = subprocess.run([sys.executable, "-c", codigo], env={**os.environ, "SUPPORT_EMAIL": ""},
                       cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "em ." not in r.stdout and r.stdout.count("suporte@pigbankai.com") == 4, r.stdout


# Bug da main desde dba20eb7: o auto-vínculo só procura o telefone do site, e um
# número ligado por código a conta de telefone diferente caía no `no_match` e
# recebia o convite de cadastro em TODA mensagem.
def test_numero_ligado_por_codigo_com_telefone_diferente_grava(enviadas):
    b, _, _ = conta_paga_sem_credencial()
    _com_senha(b)
    n = _numero_novo()
    assert db.get_auth_user(b)["phone_e164"].lstrip("+") != n
    db.link_platform_identity("whatsapp", n, b)  # o que o `vincular CODIGO` faz

    _manda(n, "gastei 50 mercado")
    assert _dono_do_numero(n) == b, enviadas
    assert _gastos(b) == 1, enviadas
    _manda(n, "Gastei R$ 30 no açougue")
    assert _gastos(b) == 2, enviadas


def test_numero_ligado_por_codigo_a_conta_so_discord(enviadas):
    """Conta sem cadastro web (só Discord): não é "sem conta". Sem plano, o
    turno chega ao gate e recebe a cópia do plano, não o convite de cadastro."""
    b = db.get_or_create_canonical_user("discord", f"d{secrets.randbelow(10**12)}")
    n = _numero_novo()
    db.link_platform_identity("whatsapp", n, b)

    _manda(n, "gastei 50 mercado")
    assert _dono_do_numero(n) == b, enviadas
    assert "Ainda não tenho uma conta ligada" not in enviadas[0], enviadas
    assert "/precos" in enviadas[0], enviadas
    assert _gastos(b) == 0


# A isenção é da mensagem que é SÓ o código: com outro comando junto, o
# classificador não dá `account.*` com código e o barrado continua barrado.
@pytest.mark.parametrize("texto", [
    "vincular {c} gastei 50", "link {c} e saldo", "gastei 50 vincular {c}",
    "vincular {c}\ngastei 50", "vincular {c} {c}", "link {c} saldo",
])
def test_isencao_nao_carrega_outro_comando(enviadas, texto):
    b = com_plano()
    code = db.create_link_code(b)
    a = cadastro_novo()
    n = _numero_novo()
    db.bind_identity("whatsapp", n, a)

    _manda(n, texto.format(c=code))
    assert _dono_do_numero(n) == a, enviadas
    assert _gastos(a) == 0 and _gastos(b) == 0
    assert any("/precos" in e for e in enviadas), enviadas


# No WhatsApp a legenda do anexo vira `msg.text`: a isenção é do campo texto, e
# um anexo legendado com o código não pode atravessar o gate nem gastar o código.
def test_anexo_com_legenda_codigo_nao_isenta():
    code = db.create_link_code(com_plano())
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=cadastro_novo(), text=f"vincular {code}",
        attachments=[{"type": "image", "url": "x"}], external_id=_numero_novo()))
    assert "/precos" in out[0].text, out
    assert _q("select count(*) as n from link_codes where code=%s", (code,))["n"] == 1


@pytest.mark.parametrize("verbo", ["vincular {}", "link {}"])
def test_teto_de_tentativas(enviadas, verbo):
    b = com_plano()
    code = db.create_link_code(b)
    n = _numero_novo()

    for _ in range(5):
        _manda(n, verbo.format("000000"))
    assert all(e.startswith("❌ Código inválido") for e in enviadas), enviadas
    enviadas.clear()
    _manda(n, verbo.format(code))
    assert enviadas == [MUITAS], enviadas
    assert _dono_do_numero(n) != b

    # Outro número, no mesmo momento, não herda o teto.
    outro = _numero_novo()
    _manda(outro, verbo.format(db.create_link_code(b)))
    assert _dono_do_numero(outro) == b, enviadas

    # Janela vencida: o mesmo código (não consumido pela tentativa barrada) liga.
    _q("update auth_rate_limits set window_started_at = now() - interval '16 minutes'"
       " where bucket = 'wa-link-code' and identifier = %s returning 1",
       ("whatsapp:" + hash_pii(n, kind="external_id"),))
    enviadas.clear()
    _manda(n, verbo.format(code))
    assert _dono_do_numero(n) == b, enviadas
