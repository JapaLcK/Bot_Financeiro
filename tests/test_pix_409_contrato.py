"""O CONTRATO do 409 de cobertura já paga: a CHAVE e o FORMATO que a tela lê.

`grep -rn covered_until tests/` dava ZERO até este arquivo — o nome da chave e o
ISO eram consumidos pelo `pix-checkout.js` e não eram prendidos por teste nenhum
dos dois lados. Trocar o formato, ou renomear a chave, devolvia o cliente
PAGANTE ao genérico "Não consegui gerar o código Pix agora." (a frase da
indisponibilidade real) com as duas suítes verdes.

Arquivo próprio, e não uma função a mais no `test_pix_rotas_billing.py`, por
TETO: aquele está em 329 linhas e com este teste dentro ia a 371 — o
`test_max_lines_python.py` (teto 350) fica vermelho. Medido, não estimado.

Rodar:  .venv/bin/python -m pytest tests/test_pix_409_contrato.py -q
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as rotas
from _billing_grants_helpers import conta
from core.services.pix_pricing import CoberturaJaPaga

client = TestClient(dashboard.app)


def _csrf() -> dict[str, str]:
    """Mesmas duas linhas do `test_pix_rotas_billing.py`: os NOMES vêm do
    `dashboard`, então não há constante duplicada aqui — só a mecânica."""
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def test_409_de_cobertura_paga_leva_covered_until_em_iso(user_id, monkeypatch):
    """A tela lê `detail.covered_until` e só nomeia a data quando ela casa com
    `/^\\d{4}-\\d{2}-\\d{2}/` (o `pixEnviar` do pix-checkout.js). Qualquer outro
    formato — e qualquer outro NOME de chave — cai no genérico.

    NEGATIVO MEDIDO, no `except CoberturaJaPaga` de billing_pix.py:
      * `.isoformat()` -> `.strftime("%d/%m/%Y")` -> vermelho aqui (o `re.match`);
      * `covered_until` -> `coberto_ate` -> vermelho aqui (o `in`).
    As duas passam verdes no resto das duas suítes.

    `criar_checkout` vira a recusa por monkeypatch de propósito: o que se mede é
    a TRADUÇÃO da exceção em HTTP, não a regra de recompra (test_pix_recompra.py).
    """
    conta(user_id, "free", None)
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: user_id)
    ate = datetime(2028, 7, 6, 3, 0, tzinfo=timezone.utc)

    def _recusa(*_args, **_kwargs):
        raise CoberturaJaPaga("pro_max", ate)

    monkeypatch.setattr(rotas, "criar_checkout", _recusa)
    # O #355 pôs mod-11 no servidor: `12345678901` agora para no 400 do documento
    # e nunca chega ao `criar_checkout`. Este é o mesmo CPF estruturalmente válido
    # do test_pix_documento_invalido.py — uma fonte só para o número (§0.7).
    r = client.post("/billing/pix/checkout", headers=_csrf(),
                    json={"plan": "pro", "cpf_cnpj": "52998224725"})
    assert r.status_code == 409, r.text
    detalhe = r.json()["detail"]
    assert detalhe["error"] == CoberturaJaPaga.ERRO
    assert "covered_until" in detalhe, f"a chave do contrato sumiu: {sorted(detalhe)}"
    # `06/07/2028` e um epoch passariam pelo `in` de cima e morreriam na tela.
    assert re.match(r"^\d{4}-\d{2}-\d{2}", detalhe["covered_until"]), detalhe["covered_until"]
    assert date.fromisoformat(detalhe["covered_until"][:10]) == ate.date()
