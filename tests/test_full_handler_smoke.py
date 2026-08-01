"""
Smoke test ABRANGENTE do handler principal (core.handle_incoming.handle_incoming).

Exercita TODAS as grandes funcionalidades do PigBank end-to-end pelo mesmo
caminho que uma mensagem real do WhatsApp/Discord percorre: paywall → mídia →
classify → route → handlers → format. Objetivo: pegar qualquer regressão/crash
em qualquer área do produto num único arquivo.

Roda como user Pro (uid < 1bi pra is_pro enxergar dentro do handle_incoming) e
mocka a IA/mídia pra não bater em rede. A ideia é usar comandos determinísticos
de alta confiança, então a IA quase nunca deve ser chamada.
"""
from __future__ import annotations

import re
import pytest

from core.types import IncomingMessage
import core.handle_incoming as hi


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def spy_ai(monkeypatch):
    """Espiona a IA — se chamada, devolve marker e registra o texto."""
    calls: list[str] = []
    import core.services.ai_chat as ai_chat_mod

    def fake_chat(user_id, text, *, monthly_limit, platform):
        calls.append(text)
        return f"[IA] {text}"

    monkeypatch.setattr(ai_chat_mod, "chat", fake_chat)
    return calls


@pytest.fixture
def pro_uid():
    """User Pro com uid pequeno (nunca normalizado por handle_incoming)."""
    import uuid as _uuid
    import db as _db
    from db.connection import get_conn

    uid = int(_uuid.uuid4().int % 1_000_000_000)
    _db.ensure_user(uid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into auth_accounts(user_id, email, password_hash, plan) "
                "values (%s, %s, 'x', 'pro')",
                (uid, f"pro-{uid}@test.local"),
            )
        conn.commit()
    return uid


def _msg(uid: int, text: str, platform: str = "whatsapp") -> IncomingMessage:
    return IncomingMessage(
        platform=platform, user_id=uid, text=text,
        message_id="1", attachments=[], external_id=str(uid), raw={},
    )


def _send(uid: int, text: str, platform: str = "whatsapp") -> str:
    out = hi.handle_incoming(_msg(uid, text, platform))
    assert out, f"handler retornou vazio para: {text!r}"
    return out[0].text


# ---------------------------------------------------------------------------
# O smoke test — um cenário por área, sequencial (estado acumula de propósito)
# ---------------------------------------------------------------------------

def test_full_product_smoke(pro_uid, spy_ai, capsys):
    uid = pro_uid
    results: list[tuple[str, str, str]] = []  # (area, input, output)

    def step(area: str, text: str, must_contain: list[str] | None = None,
             must_not_contain: list[str] | None = None, platform: str = "whatsapp"):
        resp = _send(uid, text, platform)
        results.append((area, text, resp))
        # nunca deve cair no erro interno genérico do handler
        assert "erro interno" not in resp.lower(), (
            f"[{area}] '{text}' → ERRO INTERNO:\n{resp}"
        )
        if must_contain:
            for frag in must_contain:
                assert frag.lower() in resp.lower(), (
                    f"[{area}] '{text}' → esperava conter {frag!r}, veio:\n{resp}"
                )
        if must_not_contain:
            for frag in must_not_contain:
                assert frag.lower() not in resp.lower(), (
                    f"[{area}] '{text}' → NÃO devia conter {frag!r}, veio:\n{resp}"
                )
        return resp

    # 1. Saudação
    step("greeting", "oi")

    # 2. Ajuda
    step("help", "ajuda")

    # 3. Saldo inicial
    step("balance", "saldo", must_contain=["conta corrente"])

    # 4. Lançamentos — despesa e receita
    step("launch.add", "gastei 50 no mercado", must_contain=["mercado"])
    step("launch.add", "recebi 3000 de salario")

    # 5. Listar lançamentos
    step("launch.list", "meus lançamentos", must_contain=["mercado"])

    # 6. Quanto gastei
    step("launch.spend", "quanto gastei esse mês")

    # 7. Saldo depois dos lançamentos
    step("balance", "qual meu saldo")

    # 8. Caixinhas (pockets) — usa as formas ANUNCIADAS no catálogo
    #    (cria/deposita/saca), que devem rodar deterministicamente (sem IA).
    step("pocket.create", "cria caixinha viagem", must_contain=["viagem"],
         must_not_contain=["[IA]", "qual o nome"])
    step("pocket.list", "minhas caixinhas", must_contain=["viagem"])
    step("pocket.deposit", "deposita 100 na caixinha viagem",
         must_contain=["depósito", "viagem"], must_not_contain=["[IA]"])
    step("pocket.withdraw", "saca 30 da caixinha viagem",
         must_contain=["viagem"], must_not_contain=["[IA]"])

    # 9. Investimentos
    step("invest.list", "meus investimentos")

    # 10. Categorias
    step("cat.list", "categorias")

    # 11. Relatórios
    step("report.daily", "relatório")

    # 12. Recorrentes
    step("recurring", "gasto fixo aluguel 1200 todo dia 10")

    # 13. Contas a pagar (bills)
    step("bills", "contas a pagar")

    # 14. Cartão de crédito
    step("credit", "meus cartões")

    # 15. Dashboard
    step("dashboard", "dashboard")

    # 16. CDI — forma interrogativa natural deve ser determinística (sem IA)
    step("cdi", "quanto está o cdi", must_not_contain=["[IA]"])

    # 17. Fluxo destrutivo: apagar lançamento por #seq
    #     Descobre um #seq real da listagem.
    listagem = _send(uid, "meus lançamentos")
    m = re.search(r"#(\d+)", listagem)
    if m:
        seq = m.group(1)
        step("launch.delete", f"apagar #{seq}", must_contain=["sim", "não"])
        step("confirm.yes", "sim")

    # Dump legível de tudo pro relatório
    print("\n\n===== SMOKE RESULTS =====")
    for area, text, resp in results:
        one_line = resp.replace("\n", " ⏎ ")[:220]
        print(f"[{area:14}] {text!r:45} -> {one_line}")
    print(f"\nIA chamada {len(spy_ai)}x: {spy_ai}")

    # A IA não deveria ser necessária pra nenhum comando determinístico acima.
    # Se for chamada, é sinal de que a classificação regrediu (não é crash, mas
    # anotar). Não falha o teste por isso — só reporta.
