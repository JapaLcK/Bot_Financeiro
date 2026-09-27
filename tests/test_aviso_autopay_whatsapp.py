"""#616 — aviso de vencimento do gasto fixo autopay no WhatsApp.

Banco real, `send_template` REAL; só o `requests.post` é trocado (a borda HTTP).
O relógio entra por `now=` (ou por `rc.now_tz` no loop inteiro).

Controles NEGATIVOS declarados (docs/controles_declarados.md) — em
`db/recurring.py` / `core/services/recurring_charger.py`:

  a. apagar `and rc.charged_at >= %s` (e o parâmetro `since`) da listagem →
     vermelho `test_aviso_de_ontem_nao_sai`;
  b. apagar `rc.launch_id is null and` da listagem →
     vermelho `test_linha_do_cobrador_com_lancamento_nao_sai`;
  c. apagar `and wa_notified_at is null` do UPDATE do claim E da listagem →
     vermelhos `test_dois_ticks_seguidos_mandam_uma_vez` e
     `test_dois_ticks_concorrentes_mandam_uma_vez`;
  d. trocar o claim por "enviar e depois marcar" (claim depois do laço de
     `send_template`) → vermelhos `test_dois_ticks_concorrentes_mandam_uma_vez`
     e `test_sem_telefone_nao_envia_e_consome_o_aviso` (o seguido fica verde:
     em série, marcar depois basta);
  f. trocar `not get_whatsapp_updates_opt_out(uid)` por `True` →
     vermelho `test_opt_out_nao_recebe`;
  g. trocar `bool(filtrar_por_acesso([uid]))` por `True` →
     vermelho `test_sem_acesso_nao_recebe` (e o portão
     `tests/test_portao_lacos_proativos.py`);
  h. calcular `targets` com `list_identities_by_user(<primeiro user_id da
     listagem>)` → vermelho `test_cada_telefone_recebe_so_o_proprio_aviso`;
  fonte única: renomear um `{{x}}` de `AUTOPAY_BODY` (ou uma chave de `params`)
     → vermelho `test_parametros_batem_com_o_template_do_script`.
Positivo: `test_loop_real_manda_um_aviso_por_gasto_conta_e_cartao` e o segundo
passo de `test_antes_da_hora_nao_reserva_e_depois_envia` seguem verdes nas injeções.
"""
from __future__ import annotations

import importlib.util
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pytest

import core.services.recurring_charger as rc
import db
from _billing_grants_helpers import garantir_system_event_logs
from conftest import promote_to_pro
from core.observability import recent_event_exists
from db.recurring import create_recurring_expense
from db_support import invalidate_auth_user_cache
from test_recorrente_so_preve import _um_tick
from utils_date import now_tz

_FONE_A = "5511999961601"
_FONE_B = "5511999961602"
_EVENTO = "whatsapp_autopay_notice_sent"


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def _armar(monkeypatch, status: int = 200, atraso: float = 0.0) -> list[dict]:
    posts: list[dict] = []

    class _Resp:
        status_code = status
        text = ""

        @staticmethod
        def json():
            return {"messages": [{"id": "wamid.x"}]}

    def _post(url, **kw):
        time.sleep(atraso)
        posts.append(kw["json"])
        return _Resp()

    monkeypatch.setenv("WA_TOKEN", "tok")
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "123")
    monkeypatch.setenv("WA_AUTOPAY_NOTICE_TEMPLATE_NAME", "aviso_gasto_fixo")
    monkeypatch.delenv("WA_PROACTIVE_TEMPLATE_LANGUAGE", raising=False)
    monkeypatch.delenv("WA_BILL_REMINDER_HOUR", raising=False)
    monkeypatch.setattr("requests.post", _post)
    return posts


def _as(hora: int):
    return now_tz().replace(hour=hora, minute=0, second=0, microsecond=0)


def _dono(uid: int, fone: str | None) -> int:
    promote_to_pro(uid)
    if fone:
        db.bind_identity("whatsapp", fone, uid)
    return uid


def _gasto(uid: int, nome: str, valor: float = 55.9, tipo: str = "account") -> dict:
    card_id = db.create_card(uid, "Nubank", closing_day=10, due_day=17) if tipo == "credit_card" else None
    hoje = date.today()
    return create_recurring_expense(uid, nome, valor, "assinaturas", hoje.day, tipo,
                                    card_id=card_id, start_date=hoje)


def _rodar(hora: int = 10) -> int:
    rc.sync_autopay_notices_once()
    return rc.notify_autopay_notices_whatsapp_once(now=_as(hora))


def _para(posts: list[dict], fone: str) -> list[dict]:
    return [p for p in posts if p["to"] == fone]


def _params(post: dict) -> dict[str, str]:
    return {p["parameter_name"]: p["text"] for p in post["template"]["components"][0]["parameters"]}


def _sql(sql: str, args: tuple) -> None:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        conn.commit()


def _reservados(uid: int) -> int:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from recurring_charges "
                    "where user_id=%s and wa_notified_at is not null", (uid,))
        return int(cur.fetchone()["n"])


def test_loop_real_manda_um_aviso_por_gasto_conta_e_cartao(user_id, monkeypatch):
    posts = _armar(monkeypatch)
    _dono(user_id, _FONE_A)
    _gasto(user_id, "Netflix Q616", 55.9, "account")
    _gasto(user_id, "Spotify Q616", 21.9, "credit_card")
    monkeypatch.setattr(rc, "now_tz", lambda: _as(10))

    _um_tick(monkeypatch)

    meus = _para(posts, _FONE_A)
    assert sorted((_params(p)["gasto"], _params(p)["valor"], _params(p)["meio"]) for p in meus) == [
        ("Netflix Q616", "R$ 55,90", "débito na conta"),
        ("Spotify Q616", "R$ 21,90", "cobrança no cartão"),
    ]
    for p in meus:
        assert p["template"]["name"] == "aviso_gasto_fixo"
        assert p["template"]["language"]["code"] == "pt_BR"
        assert [c["type"] for c in p["template"]["components"]] == ["body"]  # sem botão
    assert _reservados(user_id) == 2
    assert recent_event_exists(_EVENTO, user_id, 1) is True


def test_aviso_de_ontem_nao_sai(user_id, monkeypatch):
    posts = _armar(monkeypatch)
    _dono(user_id, _FONE_A)
    _gasto(user_id, "Ontem Q616")
    rc.sync_autopay_notices_once()
    _sql("update recurring_charges set charged_at = now() - interval '1 day' where user_id=%s", (user_id,))

    rc.notify_autopay_notices_whatsapp_once(now=_as(10))

    assert _para(posts, _FONE_A) == []
    assert _reservados(user_id) == 0


def test_linha_do_cobrador_com_lancamento_nao_sai(user_id, monkeypatch):
    posts = _armar(monkeypatch)
    _dono(user_id, _FONE_A)
    _gasto(user_id, "Lancado Q616")
    rc.sync_autopay_notices_once()
    launch_id, _, _ = db.add_launch_and_update_balance(user_id, "despesa", 55.9, None, "antigo")
    _sql("update recurring_charges set launch_id=%s where user_id=%s", (launch_id, user_id))

    rc.notify_autopay_notices_whatsapp_once(now=_as(10))

    assert _para(posts, _FONE_A) == []


def test_dois_ticks_seguidos_mandam_uma_vez(user_id, monkeypatch):
    posts = _armar(monkeypatch)
    _dono(user_id, _FONE_A)
    _gasto(user_id, "Seguido Q616")

    _rodar()
    _rodar()

    assert len(_para(posts, _FONE_A)) == 1


def test_dois_ticks_concorrentes_mandam_uma_vez(user_id, monkeypatch):
    posts = _armar(monkeypatch, atraso=0.2)
    _dono(user_id, _FONE_A)
    _gasto(user_id, "Concorrente Q616")
    rc.sync_autopay_notices_once()

    with ThreadPoolExecutor(2) as pool:
        for f in [pool.submit(rc.notify_autopay_notices_whatsapp_once, now=_as(10)) for _ in range(2)]:
            f.result()

    assert len(_para(posts, _FONE_A)) == 1


def test_sem_telefone_nao_envia_e_consome_o_aviso(user_id, monkeypatch):
    posts = _armar(monkeypatch)
    _dono(user_id, None)
    _gasto(user_id, "SemFone Q616")

    _rodar()

    assert [p for p in posts if _params(p)["gasto"] == "SemFone Q616"] == []
    assert _reservados(user_id) == 1


def test_opt_out_nao_recebe(user_id, monkeypatch):
    posts = _armar(monkeypatch)
    _dono(user_id, _FONE_A)
    db.set_whatsapp_updates_opt_out(user_id, True)
    _gasto(user_id, "OptOut Q616")

    _rodar()

    assert _para(posts, _FONE_A) == []


def test_sem_acesso_nao_recebe(user_id, monkeypatch):
    posts = _armar(monkeypatch)
    _dono(user_id, _FONE_A)
    _sql("update auth_accounts set plan='free', plan_expires_at=null where user_id=%s", (user_id,))
    invalidate_auth_user_cache(user_id)
    _gasto(user_id, "Cortado Q616")

    _rodar()

    assert _para(posts, _FONE_A) == []


def test_cada_telefone_recebe_so_o_proprio_aviso(user_id, monkeypatch):
    from conftest import _cleanup_user
    posts = _armar(monkeypatch)
    outro = user_id + 1
    try:
        db.ensure_user(outro)
        _dono(user_id, _FONE_A)
        _dono(outro, _FONE_B)
        _gasto(user_id, "Do A Q616")
        _gasto(outro, "Do B Q616")

        _rodar()

        assert [_params(p)["gasto"] for p in _para(posts, _FONE_A)] == ["Do A Q616"]
        assert [_params(p)["gasto"] for p in _para(posts, _FONE_B)] == ["Do B Q616"]
    finally:
        _cleanup_user(outro)


def test_sem_env_nao_le_nem_reserva(user_id, monkeypatch):
    posts = _armar(monkeypatch)
    monkeypatch.delenv("WA_AUTOPAY_NOTICE_TEMPLATE_NAME")
    _dono(user_id, _FONE_A)
    _gasto(user_id, "SemEnv Q616")

    assert _rodar() == 0

    assert posts == []
    assert _reservados(user_id) == 0


def test_antes_da_hora_nao_reserva_e_depois_envia(user_id, monkeypatch):
    posts = _armar(monkeypatch)
    _dono(user_id, _FONE_A)
    _gasto(user_id, "Hora Q616")

    _rodar(8)
    assert _para(posts, _FONE_A) == [] and _reservados(user_id) == 0

    _rodar(9)
    assert len(_para(posts, _FONE_A)) == 1


def test_nome_com_quebra_e_tab_vira_uma_linha(user_id, monkeypatch):
    posts = _armar(monkeypatch)
    _dono(user_id, _FONE_A)
    rec = _gasto(user_id, "Aluguel Q616")
    _sql("update recurring_expenses set name=%s where id=%s", ("Aluguel\n\t  apto", rec["id"]))

    _rodar()

    assert [_params(p)["gasto"] for p in _para(posts, _FONE_A)] == ["Aluguel apto"]


def test_401_nao_levanta_nem_registra_envio(user_id, monkeypatch):
    posts = _armar(monkeypatch, status=401)
    _dono(user_id, _FONE_A)
    _gasto(user_id, "Token Q616")

    assert _rodar() == 0

    assert len(_para(posts, _FONE_A)) == 1  # tentou uma vez
    assert recent_event_exists(_EVENTO, user_id, 1) is False


def test_parametros_batem_com_o_template_do_script(user_id, monkeypatch):
    """§0.7: o nome dos parâmetros vive no script (Meta) e no envio; os dois têm de bater."""
    caminho = Path(__file__).resolve().parent.parent / "scripts" / "create_whatsapp_report_templates.py"
    spec = importlib.util.spec_from_file_location("_templates_616", caminho)
    script = importlib.util.module_from_spec(spec)
    # O script faz `load_dotenv(.env)` no import: num checkout com `.env` real isso
    # poria o WA_TOKEN de verdade no `os.environ` do resto da suíte.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    spec.loader.exec_module(script)
    posts = _armar(monkeypatch)
    _dono(user_id, _FONE_A)
    _gasto(user_id, "Fonte Q616")

    _rodar()

    [post] = _para(posts, _FONE_A)
    no_corpo = set(re.findall(r"\{\{(\w+)\}\}", script.AUTOPAY_BODY))
    assert no_corpo == set(script.AUTOPAY_EXAMPLES) == set(_params(post))
