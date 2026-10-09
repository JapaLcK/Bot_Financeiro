import random
import sys

import pytest

import db
import scripts.send_update_whatsapp as su
from db.connection import get_conn
from scripts.send_update_whatsapp import (
    WA_UPDATES_DISABLE_ID,
    _dedupe_targets,
    build_quick_reply_buttons,
    get_all_update_targets,
    get_test_targets,
)
from utils_phone import phone_lookup_candidates


def test_dedupe_targets_remove_alias_do_mesmo_numero():
    rows = [
        {
            "user_id": 1,
            "email": "um@example.com",
            "identity_phone": "5565999929199",
        },
        {
            "user_id": 2,
            "email": "dois@example.com",
            "identity_phone": "556599929199",  # o mesmo número sem o nono dígito
        },
    ]

    targets = _dedupe_targets(rows)

    assert len(targets) == 1
    assert targets[0].user_id == 1
    assert targets[0].to == "5565999929199"
    assert targets[0].source == "whatsapp"


def test_get_test_targets_normaliza_numero_informado():
    targets = get_test_targets("(65) 99992-9199")

    assert len(targets) == 1
    assert targets[0].user_id == 0
    assert targets[0].to == "5565999929199"
    assert targets[0].source == "test"


def test_build_quick_reply_buttons_usa_payload_de_opt_out_das_atualizacoes():
    assert build_quick_reply_buttons(False) is None
    assert build_quick_reply_buttons(True) == [{"index": 0, "payload": WA_UPDATES_DISABLE_ID}]


# #721 (D4b): a atualização vai só a número ligado em `user_identities`, nunca ao
# `phone_e164` digitado no site. Postgres real. Controle negativo: na SQL de
# `get_all_update_targets`, trocar `join` por `left join`, `i.external_id as
# identity_phone` por `coalesce(nullif(i.external_id, ''), a.phone_e164) as
# identity_phone` e o `where` por `nullif(a.phone_e164, '') is not null or
# nullif(i.external_id, '') is not null` deixa vermelhos o 1º e o da issue.
def _numero() -> str:
    return f"55119{random.randint(10_000_000, 99_999_999)}"


def _conta(fone: str | None = None, wa: str | None = None, opt_out: bool = False,
           exclusao: bool = False, uid: int | None = None) -> int:
    uid = uid or random.randint(1_000_000, 9_000_000_000)
    db.ensure_user(uid)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into auth_accounts (user_id, email, phone_e164, whatsapp_updates_opt_out,"
            " deletion_status, deletion_scheduled_for)"
            " values (%s, %s, %s, %s, %s, case when %s then now() + interval '7 days' end)",
            (uid, f"upd-{uid}@t.local", f"+{fone}" if fone else None, opt_out,
             "scheduled" if exclusao else None, exclusao),
        )
        conn.commit()
    if wa:
        db.bind_identity("whatsapp", wa, uid)
    return uid


def _alvos():
    return {t.user_id: t.to for t in get_all_update_targets()}


def _ninguem_manda_para(numero: str) -> bool:
    return not set(_alvos().values()) & set(phone_lookup_candidates(numero))


def test_telefone_digitado_sem_identidade_fica_fora():
    n = _numero()
    uid = _conta(fone=n)
    assert uid not in _alvos()
    assert _ninguem_manda_para(n)


def test_numero_ligado_entra():
    n = _numero()
    uid = _conta(fone=_numero(), wa=n)
    assert _alvos().get(uid) == n


def test_numero_ligado_com_opt_out_fica_fora():
    n = _numero()
    uid = _conta(fone=n, wa=n, opt_out=True)
    assert uid not in _alvos()
    assert _ninguem_manda_para(n)


def test_issue_721_numero_de_a_digitado_por_b_nao_recebe():
    # A é só-WhatsApp: sem `auth_accounts`, o clique de opt-out dele não grava nada e
    # ele nunca é alvo. O que a #721 exige é que B, que digitou o número de A, não receba.
    n = _numero()
    db.get_or_create_canonical_user("whatsapp", n)
    b = _conta(fone=n)
    assert b not in _alvos()
    assert _ninguem_manda_para(n)


# Opt-out é do número: duas contas ligadas ao mesmo número (com e sem o nono dígito);
# a que recebe pede para parar e a outra não passa a receber. Controle negativo:
# tirar o laço de opt-out do `_dedupe_targets` deixa este vermelho.
def test_opt_out_de_uma_conta_barra_o_numero_nas_outras():
    n = _numero()
    _conta(wa=n)
    _conta(wa=n[:4] + n[5:])
    antes = [u for u, to in _alvos().items() if to in phone_lookup_candidates(n)]
    assert len(antes) == 1
    db.set_whatsapp_updates_opt_out(antes[0], True)
    assert _ninguem_manda_para(n), _alvos()


def test_opt_out_de_um_numero_nao_barra_outro_numero():
    n, m = _numero(), _numero()
    _conta(wa=n, opt_out=True)
    uid = _conta(wa=m)
    assert _alvos().get(uid) == m


# `send_template` devolve `None` no 401 da Meta sem levantar: conta como falha; a
# resposta da API conta como enviado. Controle negativo: tirar o `is None` deixa o
# caso `None` vermelho; um laço que sempre levanta deixa o caso da resposta vermelho.
@pytest.mark.parametrize("resposta,resumo", [
    (None, "Enviados: 0 | Falhas: 1"),
    ({"messages": [{"id": "x"}]}, "Enviados: 1 | Falhas: 0"),
])
def test_main_conta_envio_recusado_como_falha(monkeypatch, capsys, resposta, resumo):
    monkeypatch.setattr(su, "send_template", lambda *a, **k: resposta)
    monkeypatch.setattr(sys, "argv", ["send_update_whatsapp.py", "--test", "5511987654321"])
    su.main()
    assert resumo in capsys.readouterr().out


# `--test email` segue a mesma regra: só número ligado. Controle negativo: na SQL de
# `get_test_targets`, trocar `join` por `left join`, `i.external_id as identity_phone`
# por `coalesce(nullif(i.external_id, ''), a.phone_e164) as identity_phone` e tirar o
# `and nullif(i.external_id, '') is not null` deixa o 1º vermelho.
def test_get_test_targets_email_sem_identidade_nao_devolve_destino():
    uid = _conta(fone=_numero())
    assert get_test_targets(f"upd-{uid}@t.local") == []


def test_get_test_targets_email_com_identidade_devolve_o_numero_ligado():
    n = _numero()
    uid = _conta(fone=_numero(), wa=n)
    assert [(t.user_id, t.to) for t in get_test_targets(f"upd-{uid}@t.local")] == [(uid, n)]


# Exclusão de conta pedida (`db.is_account_scheduled_for_deletion`): a conta não
# recebe, nem pelo `--test email`. A exclusão é da conta, não do número: a variante
# em outra conta ativa recebe, a menos que a conta em exclusão tenha opt-out.
# Controle negativo: `_sem_exclusao_pedida` devolvendo `rows` deixa o 1º vermelho;
# filtrar também as com opt-out deixa o 3º vermelho.
def test_conta_em_exclusao_fica_fora():
    n = _numero()
    uid = _conta(wa=n, exclusao=True)
    assert uid not in _alvos()
    assert _ninguem_manda_para(n)
    assert get_test_targets(f"upd-{uid}@t.local") == []


def test_exclusao_nao_barra_a_variante_em_conta_ativa():
    n = _numero()
    _conta(wa=n, exclusao=True, uid=random.randint(1, 999_999))  # vem antes no `order by`
    ativa = _conta(wa=n[:4] + n[5:])
    assert _alvos().get(ativa) in phone_lookup_candidates(n)


def test_exclusao_com_opt_out_ainda_barra_a_variante():
    n = _numero()
    _conta(wa=n, opt_out=True, exclusao=True)
    _conta(wa=n[:4] + n[5:])
    assert _ninguem_manda_para(n), _alvos()


# Opt-out feito DURANTE a execução (Configurações ou botão de uma atualização
# anterior) vale para quem ainda não recebeu: `main` reconfere antes de cada envio.
# Controle negativo: tirar a reconferência faz o número de B receber.
def test_opt_out_durante_o_disparo_vale_para_quem_ainda_nao_recebeu(monkeypatch, capsys):
    na, nb, nc = _numero(), _numero(), _numero()
    _conta(wa=na, uid=random.randint(1, 999_999))
    b = _conta(wa=nb, uid=random.randint(1_000_000, 1_999_999))
    _conta(wa=nc, uid=random.randint(5_000_000_000, 9_000_000_000))
    enviados = []

    def _envia(to, *a, **k):
        if not enviados:
            db.set_whatsapp_updates_opt_out(b, True)  # B desliga enquanto o 1º sai
        enviados.append(to)
        return {"messages": [{"id": "x"}]}

    monkeypatch.setattr(su, "send_template", _envia)
    monkeypatch.setattr(sys, "argv", ["send_update_whatsapp.py"])
    su.main()
    assert na in enviados and nc in enviados
    assert nb not in enviados, enviados
    assert "PULADO (opt-out ou exclusão pedida)" in capsys.readouterr().out


def _disparo(monkeypatch, ao_enviar=None):
    enviados = []

    def _envia(to, *a, **k):
        if ao_enviar and not enviados:
            ao_enviar()
        enviados.append(to)
        return {"messages": [{"id": "x"}]}

    monkeypatch.setattr(su, "send_template", _envia)
    monkeypatch.setattr(sys, "argv", ["send_update_whatsapp.py"])
    su.main()
    return enviados


# `external_id` fora do formato canônico: a reconferência acha a própria linha pelo
# valor gravado, então o formato não decide nada; só o opt-out decide. Controle
# negativo: tirar o `| {target.raw}` da reconferência deixa os 2 "recebe" vermelhos.
@pytest.mark.parametrize("formato", ["mais", "espacos"])
@pytest.mark.parametrize("opt_out_no_meio", [False, True])
def test_reconferencia_independe_do_formato_gravado(monkeypatch, capsys, formato, opt_out_no_meio):
    n = _numero()
    raw = {"mais": f"+{n}", "espacos": f"{n[:2]} {n[2:4]} {n[4:9]}-{n[9:]}"}[formato]
    _conta(wa=_numero(), uid=random.randint(1, 999_999))  # recebe primeiro
    uid = _conta(wa=raw)
    to = su._normalize_whatsapp_target(raw)[0]
    desliga = (lambda: db.set_whatsapp_updates_opt_out(uid, True)) if opt_out_no_meio else None
    enviados = _disparo(monkeypatch, desliga)
    assert (to in enviados) is not opt_out_no_meio, enviados
    assert ("PULADO" in capsys.readouterr().out) is opt_out_no_meio


# Falha na reconferência conta como falha daquele destinatário e o disparo segue.
# Controle negativo: reconferência fora do `try` derruba o `main` no 2º.
def test_falha_na_reconferencia_nao_derruba_o_disparo(monkeypatch, capsys):
    numeros = [_numero() for _ in range(3)]
    for i, n in enumerate(numeros):
        _conta(wa=n, uid=random.randint(1, 999_999) + i * 1_000_000)
    original, chamadas = su.get_all_update_targets, []

    def _reconfere(numeros=None):
        if numeros is not None:
            chamadas.append(numeros)
            if len(chamadas) == 2:
                raise RuntimeError("banco caiu")
        return original(numeros)

    monkeypatch.setattr(su, "get_all_update_targets", _reconfere)
    enviados = _disparo(monkeypatch)
    assert enviados == [numeros[0], numeros[2]]
    assert "Enviados: 2 | Falhas: 1 | Pulados: 0" in capsys.readouterr().out


# #901: 10/11 dígitos é ambíguo (brasileiro sem 55 ou estrangeiro com o código do
# país: `51987654321` do Peru viraria `5551987654321`, um celular de Porto Alegre).
# Não recebe, nem no `--test email`, e a saída não mostra o número. Controle
# negativo: tirar o pulo do `_dedupe_targets` deixa os dois vermelhos.
@pytest.mark.parametrize("formato", ["sem_55", "estrangeiro"])
def test_numero_ambiguo_de_10_ou_11_digitos_nao_recebe(monkeypatch, capsys, formato):
    raw = {"sem_55": _numero()[2:], "estrangeiro": f"51987{random.randint(100_000, 999_999)}"}[formato]
    uid = _conta(wa=raw)
    to = su._normalize_whatsapp_target(raw)[0]
    enviados = _disparo(monkeypatch)
    out = capsys.readouterr().out
    assert to not in enviados, enviados
    assert "1 destinatário(s) com número ambíguo" in out
    assert raw not in out and to not in out
    assert get_test_targets(f"upd-{uid}@t.local") == []
