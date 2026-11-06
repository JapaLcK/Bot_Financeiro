"""Lacunas da campanha (Black Friday) que sobreviveram à 1ª passada: texto exato, relógio real, migração
cartão para Pix, caderno cheio, crédito sobre o valor pago e o mock do front igual ao servidor.

Controles (2026-11-06, mutação em produção e revertida): tirar "depois" de `frase_mensal`/`linha_mensal`
(textos), `datetime.now()` sem fuso em `_agora` (relógio), `pct_da_oferta` fora do `criar_checkout` (migração e
caderno), crédito sobre `PRECOS_ANUAIS_CENTS` em `_credito_proporcional` (crédito) e mudar uma frase só no
Python (mock). Positivos: o 409 `retomada_sem_migracao` fica só no link; o caminho legítimo sai com 40%.
"""
import importlib.util
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _pix_checkout_helpers import _comprar, _linhas, _marcar_paga, asaas_falso, vendavel  # noqa: F401 - fixtures
from core.services import oferta_campanha as camp
from core.services import pix_checkout
from core.services.pix_pricing import PRECOS_ANUAIS_CENTS
from frontend.routes.retomada import campanha_config
import frontend.finance_bot_websocket_custom as dashboard
from test_oferta_campanha import DENTRO, _cartao, _relogio
from test_pix_extras_checkout import _CAT, loja  # noqa: F401 - fixture
from test_retomada_backend import _checkout, _cliente, _conta, _pix

RAIZ = Path(__file__).resolve().parent


def test_textos_exatos_do_plano(monkeypatch):
    _relogio(monkeypatch, DENTRO)
    c = campanha_config()
    assert c["frase"] == ("Black Friday: 40% OFF nos planos Plus e Pro, anual a partir de R$ 119,40, "
                          "só até 30 de novembro.")
    assert c["frase_assinante"] == ("Black Friday: o anual no Pix sai com 40% OFF nos planos Plus e Pro, "
                                    "só até 30 de novembro.")
    for nome, anual, mensal, cheio_m in (("Plus", "R$ 119,40", "R$ 11,94", "R$ 19,90"),
                                         ("Pro", "R$ 299,40", "R$ 29,94", "R$ 49,90")):
        t = c["textos"][nome.lower()]
        assert t["frase_anual"] == f"12 meses de {nome} pelo preço de 6, {anual} no plano anual, só até 30 de novembro."
        assert t["frase_mensal"] == f"1º mês do {nome} por {mensal}, depois {cheio_m} por mês. Só até 30 de novembro."
        assert t["linha_mensal"] == f"1º mês por {mensal}, depois {cheio_m} por mês"


# ── relógio: o corpo real de `_agora` (a fixture do conftest o congela em todo teste) ──────────────────────

def _modulo_real():
    """Cópia nova do módulo: o `_agora` de dentro dela é o original, sem a fixture."""
    spec = importlib.util.spec_from_file_location("oferta_campanha_real", camp.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_agora_real_devolve_horario_com_fuso_e_decide_a_janela_em_brasilia(monkeypatch):
    real = _modulo_real()
    assert real._agora().utcoffset() is not None
    assert abs(real._agora() - datetime.now(timezone.utc)) < timedelta(seconds=5)

    class Relogio(datetime):   # o `datetime.now(tz)` do módulo, num instante escolhido (em UTC)
        instante = datetime(2026, 11, 23, 2, 59, 59, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.instante.astimezone(tz) if tz else cls.instante.replace(tzinfo=None)

    monkeypatch.setattr(real, "datetime", Relogio)
    assert real._agora().utcoffset() == timedelta(hours=-3) and real.campanha_pct() is None
    Relogio.instante = datetime(2026, 11, 23, 3, 0, 0, tzinfo=timezone.utc)   # 00:00 em Brasília
    assert real.campanha_pct() == 40
    Relogio.instante = datetime(2026, 12, 1, 2, 59, 59, tzinfo=timezone.utc)   # 30/11 23:59:59
    assert real.campanha_pct() == 40
    Relogio.instante = datetime(2026, 12, 1, 3, 0, 0, tzinfo=timezone.utc)
    assert real.campanha_pct() is None


# ── Pix: migração do cartão, caderno e crédito ──────────────────────────────────────────────────────────────

def test_pix_migracao_do_cartao_na_campanha_sai_com_40_e_o_link_nao_migra(monkeypatch, vendavel, asaas_falso):
    a, _ = _conta("l-migra")
    _relogio(monkeypatch, DENTRO)
    monkeypatch.setattr(pix_checkout, "_stripe_vivo", lambda uid: ("sub_9", datetime.now(timezone.utc) + timedelta(days=20)))
    assert _pix(_cliente(painel=a), plan="plus").status_code == 409          # sem confirmar não migra
    assert _linhas(a) == []
    r = _pix(_cliente(painel=a), plan="plus", confirm_cancel_stripe=True)
    assert r.status_code == 200 and r.json()["amount_cents"] == 11940        # 40%, não os 19900
    assert [l["stripe_subscription_id"] for l in _linhas(a)] == ["sub_9"]
    b, _ = _conta("l-migra-link")
    r = _pix(_cliente(b, 3), plan="plus", confirm_cancel_stripe=True)        # o link nunca migra
    assert r.status_code == 409 and r.json()["detail"] == {"error": "retomada_sem_migracao"}
    assert _linhas(b) == []


def test_pix_caderno_fica_a_preco_cheio_na_campanha(monkeypatch, vendavel, asaas_falso, loja):
    a, _ = _conta("l-caderno")
    _relogio(monkeypatch, DENTRO)
    r = _comprar(a, plano="pro", extras=["price_ca"])
    assert r["amount_cents"] == PRECOS_ANUAIS_CENTS["pro"] * 6 // 10 == 11940
    assert r["total_cents"] == 11940 + _CAT["price_ca"]                        # o caderno NÃO leva os 40%


def test_pix_upgrade_credita_sobre_o_valor_pago_e_nao_sobre_o_cheio(monkeypatch, vendavel, asaas_falso):
    from _billing_grants_helpers import conta  # noqa: PLC0415
    a, _ = _conta("l-up")
    conta(a, "free", None)
    _relogio(monkeypatch, DENTRO)
    plus = _comprar(a, "pro")
    _marcar_paga(a, plus["public_token"], dias=-1)                             # pagou 11940; resta 364 de 365 dias
    _comprar(a, "pro_max")
    linha = _linhas(a)[-1]
    esperado = round(11940 * 364 / 365)                                        # 11907; sobre o cheio seria 19845
    assert linha["credit_cents"] == esperado
    assert linha["amount_cents"] == 29940 - esperado


# ── o mock do front é o servidor (CLAUDE.md §0.7) ───────────────────────────────────────────────────────────

def test_mock_da_campanha_do_front_e_igual_ao_campanha_config(monkeypatch):
    _relogio(monkeypatch, DENTRO)
    out = subprocess.run(
        ["node", "-e", "import('./tests/frontend/_campanha.mjs').then(m => console.log(JSON.stringify(m.CAMP)))"],
        cwd=RAIZ.parent, capture_output=True, text=True, check=True, timeout=60).stdout
    mock, real = json.loads(out), campanha_config()
    for k in ("desconto_pct", "trial", "frase", "frase_assinante", "textos"):
        assert mock[k] == real[k], k
    assert mock["precos"] == real["precos"]   # o dict inteiro: a tela lê `p.pct` e `p.pix` também


# ── ex-cliente: a campanha vale para qualquer pessoa; o 409 `ja_comprou` é só do link ────────────────────

def test_cartao_ex_cliente_logado_leva_os_40_e_pelo_link_leva_409(monkeypatch):
    a, _ = _conta("l-ex")
    fake, _x = _cartao(monkeypatch), _relogio(monkeypatch, DENTRO)
    monkeypatch.setattr(dashboard, "ja_comprou", lambda uid: True)
    assert _checkout(_cliente(auth=a), plan="plus").status_code == 200
    k = fake.last_session_kwargs
    assert k["discounts"] == [{"coupon": "rm_e0_40_prod_x"}] and k["metadata"]["rm_desc"] == "40"
    r = _checkout(_cliente(a, 3), plan="plus")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "ja_comprou"
