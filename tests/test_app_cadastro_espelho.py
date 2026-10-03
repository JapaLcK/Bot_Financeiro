"""O cadastro do app (`app/src/features/auth/criarConta.ts`) valida no cliente
com espelhos das regras do `/auth/register`; aqui cada espelho bate no servidor.

Telefone: a tabela é UMA, `tests/fixtures/telefones_cadastro.json`, lida por
este arquivo contra `normalize_phone_e164` e pelo Jest do app
(`app/__tests__/features/criar_conta.test.ts`) contra `normalizarTelefone`.
Ela prende a regra de `utils_phone.py`, que também serve Configurações e o
Google: quem mudar a regra verá este teste vermelho, atualizará a fixture, e o
Jest fica vermelho até o espelho do app mudar. Esse acoplamento é o desejado
(CLAUDE.md §0.7). A fixture é só ASCII de propósito: o `\\D` do Python é
Unicode, o do JS não, e o app manda só dígitos ASCII.

Senha e nome: `SENHA_MIN`, `NOME_MIN` e `NOME_MAX` são lidos do `.ts` por regex
e batidos nas fronteiras do endpoint real. Todo register manda `phone` (sem ele
a resposta é 422, não 400). No máximo 3 registers por teste: o teto é 3 por hora.

Controles medidos em 2026-09-25: teto de 15 → 16 em `utils_phone.py` deixa o
caso de 16 dígitos vermelho; `SENHA_MIN` 8 → 9 e `NOME_MAX` 50 → 60 no `.ts`
deixam vermelhos os testes de fronteira. Os 200 com `SENHA_MIN` e `NOME_MAX`
são o controle positivo.
"""
import json
import pathlib
import re
import uuid

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from core.services import email_service
from utils_phone import normalize_phone_e164
from _apoio_auth_app import limpa_rate_limits

RAIZ = pathlib.Path(__file__).resolve().parent.parent
CRIAR_CONTA_TS = RAIZ / "app" / "src" / "features" / "auth" / "criarConta.ts"
FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "telefones_cadastro.json").read_text(encoding="utf-8")
)
TELEFONE_VALIDO = "11999998888"


def _constante(nome: str) -> int:
    achado = re.search(rf"^export const {nome} = (\d+);$", CRIAR_CONTA_TS.read_text(), re.M)
    assert achado, f"`export const {nome} = <n>;` não encontrado em {CRIAR_CONTA_TS}"
    return int(achado.group(1))


def _register(monkeypatch, **campos):
    monkeypatch.setattr(email_service, "send_verification_email", lambda to, code: True)
    email = f"espelho-app-{uuid.uuid4().hex[:10]}@example.com"
    limpa_rate_limits("register", email)
    corpo = {"email": email, "password": "senha-forte-123", "name": "Fulana", "phone": TELEFONE_VALIDO, **campos}
    return TestClient(dashboard.app).post(
        "/auth/register", headers={dashboard.APP_CLIENT_HEADER: "app"}, json=corpo
    )


@pytest.mark.parametrize("caso", FIXTURE["casos"], ids=[repr(c["entrada"]) for c in FIXTURE["casos"]])
def test_telefone_do_app_bate_com_normalize_phone_e164(caso):
    if caso["e164"] is None:
        with pytest.raises(ValueError) as erro:
            normalize_phone_e164(caso["entrada"])
        assert str(erro.value) == FIXTURE["mensagem"]
    else:
        assert normalize_phone_e164(caso["entrada"]) == caso["e164"]


def test_register_recusa_telefone_invalido_com_a_mensagem_da_fixture(monkeypatch):
    r = _register(monkeypatch, phone="99999-8888")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == FIXTURE["mensagem"]


def test_fronteira_da_senha_bate_com_senha_min(monkeypatch):
    senha_min = _constante("SENHA_MIN")
    curta = _register(monkeypatch, password="x" * (senha_min - 1))
    assert curta.status_code == 400, curta.text
    assert _register(monkeypatch, password="x" * senha_min).status_code == 200


def test_fronteiras_do_nome_batem_com_nome_min_e_nome_max(monkeypatch):
    """`NOME_MAX` precisa dos dois lados: só o `+1 → 400` ficaria verde com o
    app aceitando nome que o servidor recusa (teto do `.ts` subindo)."""
    nome_max = _constante("NOME_MAX")
    curto = _register(monkeypatch, name="x" * (_constante("NOME_MIN") - 1))
    assert curto.status_code == 400, curto.text
    longo = _register(monkeypatch, name="x" * (nome_max + 1))
    assert longo.status_code == 400, longo.text
    assert _register(monkeypatch, name="x" * nome_max).status_code == 200


def test_fronteiras_do_nome_no_complete_signup_do_google(monkeypatch):
    """O `complete-signup` do Google tem os próprios literais 2..50
    (`db/google_auth.py`), e o app valida o nome do cadastro Google com os
    mesmos `NOME_MIN`/`NOME_MAX`. O pré-cadastro sobrevive às recusas: o nome é
    conferido antes de qualquer escrita, então o mesmo token serve às três."""
    import db

    email = f"espelho-google-{uuid.uuid4().hex[:10]}@example.com"
    token = db.create_pending_google_signup(f"sub-{email}", email, None)
    telefone = f"55119{uuid.uuid4().int % 100_000_000:08d}"

    def _completa(nome: str):
        return TestClient(dashboard.app).post(
            "/auth/google/complete-signup",
            headers={dashboard.APP_CLIENT_HEADER: "app"},
            json={"token": token, "name": nome, "phone": telefone, "accepted_terms": True},
        )

    nome_max = _constante("NOME_MAX")
    curto = _completa("x" * (_constante("NOME_MIN") - 1))
    assert curto.status_code == 400, curto.text
    longo = _completa("x" * (nome_max + 1))
    assert longo.status_code == 400, longo.text
    assert _completa("x" * nome_max).status_code == 200
