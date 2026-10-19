"""B0a: trocar o e-mail em PATCH /settings/{uid}/security/contact pede a senha atual
e avisa o e-mail ANTIGO. Rota real, banco real, CSRF; o envio é espionado.

Controles por mutação (rodados pelo Coder, ver o relato do PR):
  · sem a checagem de senha            → senha_errada, sem_campo, outra_conta vermelhos;
  · 409 antes da senha                 → outra_conta vermelho;
  · sem a exceção da conta sem credencial → sem_credencial vermelho;
  · sem o aviso                        → aviso vermelho;
  · `_esc` tirado do aviso             → email_hostil vermelho;
  · senha depois do 409 do telefone    → telefone_de_outra_conta vermelho;
  · `if email_actually_changed:` → `if email:` na auditoria → auditoria vermelho;
  · sem o `for update` de `travar_e_ler_email` → corrida vermelho (2 avisos);
  · gravar o e-mail mesmo sem `email_actually_changed` → reversao vermelho;
  · `email_da_linha` fora do try de `avisar_email_antigo` → decifragem_falha vermelho.
Positivos: senha_certa, nome/telefone sem senha, mesmo e-mail — sem eles o grupo
passaria num código que recusa tudo.
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest

import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import promote_to_pro
from core.services.retomada import mascarar_email
from db import ensure_user
from db.connection import get_conn
from db.privacy import PASSWORD_NOT_SET_MSG
from frontend.routes.settings import CONTATO_EM_USO
from tests._helpers_pii import HASH_SENHA, SENHA, insert_auth_account_pii
from tests.test_settings_contact_pii_sync import _auth_row, _client_for
from tests.test_settings_contato_teto import _assert_429


def _email() -> str:
    return f"b0a-{uuid.uuid4().hex[:10]}@test.local"


def _conta(uid: int, *, senha: str | None = HASH_SENHA, google: bool = False, customer: str | None = None):
    """Conta com login e plano. Devolve (patch, email antigo). `senha=None` = sem senha;
    com `google=True` é a conta só-Google, sem `google` é a conta paga sem credencial."""
    email, fone = _email(), "5511" + str(900000000 + uuid.uuid4().int % 99999999)
    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, uid, email, phone=fone, name="Antigo", password_hash=senha)
        if customer:
            cur.execute("update auth_accounts set stripe_customer_id = %s where user_id = %s", (customer, uid))
        conn.commit()
    if google:
        db.link_google_identity(uid, f"g-{uid}", email)
    promote_to_pro(uid)
    client, headers = _client_for(uid, email)

    def patch(*, no_caminho_de: int | None = None, **body):
        return client.patch(f"/settings/{no_caminho_de or uid}/security/contact", json=body, headers=headers)

    patch.client = client
    return patch, email


@pytest.fixture
def envios(monkeypatch):
    """Espia `send_email`: lista de (to, subject, html, text). `.devolve`/`.levanta` mudam o desfecho."""
    import core.services.email_service as es

    class Espiao(list):
        devolve, levanta = True, None

    esp = Espiao()

    def fake(to, subject, html_body, text_body=None, **kw):
        esp.append((to, subject, html_body, text_body))
        if esp.levanta:
            raise esp.levanta
        return esp.devolve

    monkeypatch.setattr(es, "send_email", fake)
    return esp


def _pendencias(uid: int) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from stripe_email_pendente where user_id = %s", (uid,))
        return cur.fetchone()["n"]


def _nome(uid: int):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select display_name from auth_accounts where user_id = %s", (uid,))
        return cur.fetchone()["display_name"]


def test_senha_certa_troca_o_email(user_id, envios):  # controle positivo
    patch, _ = _conta(user_id, customer=f"cus_{user_id}")
    novo = _email()
    r = patch(email=novo, current_password=SENHA)
    assert r.status_code == 200, r.text
    assert _auth_row(user_id)["email"] == novo
    assert _pendencias(user_id) == 1


def test_senha_errada_401_sem_www_authenticate_e_nada_gravado(user_id, envios):
    patch, antigo = _conta(user_id, customer=f"cus_{user_id}")
    r = patch(email=_email(), phone="+55 65 99908-8732", display_name="Novo", current_password="errada")
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "Senha incorreta."
    assert "www-authenticate" not in {k.lower() for k in r.headers}
    row = _auth_row(user_id)
    assert row["email"] == antigo and row["phone_e164"] != "5565999088732"  # nem o telefone do mesmo corpo
    assert _nome(user_id) == "Antigo"
    assert _pendencias(user_id) == 0
    assert envios == []


@pytest.mark.parametrize("corpo", [{}, {"current_password": ""}, {"current_password": None}])
def test_sem_o_campo_400(user_id, envios, corpo):
    patch, antigo = _conta(user_id)
    r = patch(email=_email(), **corpo)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Confirme sua senha atual para trocar o e-mail."
    assert _auth_row(user_id)["email"] == antigo
    assert envios == []


def test_so_nome_ou_so_telefone_nao_pede_senha(user_id, envios):
    patch, _ = _conta(user_id)
    assert patch(display_name="Fulana").status_code == 200
    assert patch(phone="+55 65 99908-8732").status_code == 200
    assert _nome(user_id) == "Fulana" and _auth_row(user_id)["phone_e164"] == "5565999088732"
    assert envios == []


def test_mesmo_email_nao_pede_senha_nem_avisa(user_id, envios):
    patch, antigo = _conta(user_id)
    assert patch(email=antigo).status_code == 200
    assert patch(email=antigo.upper()).status_code == 200
    assert envios == []


def test_conta_so_google_409_definir_senha(user_id, envios):
    patch, antigo = _conta(user_id, senha=None, google=True)
    for corpo in ({}, {"current_password": "qualquer"}):
        r = patch(email=_email(), **corpo)
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == PASSWORD_NOT_SET_MSG
    assert _auth_row(user_id)["email"] == antigo
    assert envios == []


def test_conta_sem_credencial_troca_sem_senha_e_sem_aviso(user_id, envios):
    patch, _ = _conta(user_id, senha=None)
    assert db.conta_sem_credencial(user_id)
    novo = _email()
    r = patch(email=novo)
    assert r.status_code == 200, r.text
    assert _auth_row(user_id)["email"] == novo
    assert envios == []


def test_email_de_outra_conta_401_com_senha_errada_e_409_com_a_certa(user_id, envios):
    patch, antigo = _conta(user_id)
    outro = int(uuid.uuid4().int % 900_000_000) + 1
    ensure_user(outro)
    _, email_de_b = _conta(outro)

    r = patch(email=email_de_b, current_password="errada")
    assert r.status_code == 401, r.text  # NUNCA 409: senha errada não enumera contas
    r = patch(email=email_de_b, current_password=SENHA)
    assert r.status_code == 409 and r.json()["detail"] == CONTATO_EM_USO, r.text
    assert _auth_row(user_id)["email"] == antigo
    assert envios == []


def test_cinco_senhas_erradas_e_a_sexta_mesmo_certa_leva_429(user_id, envios):
    patch, antigo = _conta(user_id)
    for _ in range(5):
        assert patch(email=_email(), current_password="errada").status_code == 401
    _assert_429(patch(email=_email(), current_password=SENHA))
    assert _auth_row(user_id)["email"] == antigo


def test_sessao_de_a_no_caminho_de_b_403_e_nada_gravado_em_b(user_id, envios):
    patch_a, _ = _conta(user_id)
    b = int(uuid.uuid4().int % 900_000_000) + 1
    ensure_user(b)
    _, email_b = _conta(b)
    r = patch_a(no_caminho_de=b, email=_email(), current_password=SENHA)
    assert r.status_code == 403, r.text
    assert _auth_row(b)["email"] == email_b
    assert envios == []


def test_aviso_vai_uma_vez_ao_antigo_com_o_novo_mascarado(user_id, envios):
    patch, antigo = _conta(user_id)
    novo = _email()
    assert patch(email=novo, current_password=SENHA).status_code == 200
    assert len(envios) == 1
    to, subject, html, text = envios[0]
    assert to == antigo
    assert subject == "🔔 O e-mail da sua conta PigBank foi alterado"
    for corpo in (html, text):
        assert mascarar_email(novo) in corpo
        assert novo not in corpo and antigo not in corpo


@pytest.mark.parametrize("falha", ["devolve_false", "levanta"])
def test_falha_do_aviso_nao_desfaz_a_troca(user_id, envios, falha):
    patch, _ = _conta(user_id)
    if falha == "devolve_false":
        envios.devolve = False
    else:
        envios.levanta = RuntimeError("resend fora")
    novo = _email()
    r = patch(email=novo, current_password=SENHA)
    assert r.status_code == 200, r.text
    assert _auth_row(user_id)["email"] == novo
    assert len(envios) == 1


def test_email_hostil_vai_escapado_no_html_do_aviso(user_id, envios):
    patch, _ = _conta(user_id)
    novo = '<b>x@"><img src=x onerror=alert(1)>.com'
    assert patch(email=novo, current_password=SENHA).status_code == 200
    _, _, html, text = envios[0]
    assert "<img src=x" not in html and "onerror=alert(1)>" not in html and "&lt;img src=x" in html
    assert mascarar_email(novo.lower()) in text  # o texto puro leva a máscara, sem o local inteiro
    assert "b>x@" not in text


def test_telefone_de_outra_conta_401_com_senha_errada_e_409_com_a_certa(user_id, envios):
    patch, antigo = _conta(user_id)
    outro = int(uuid.uuid4().int % 900_000_000) + 1
    ensure_user(outro)
    _conta(outro)
    fone_de_b = _auth_row(outro)["phone_e164"]
    r = patch(email=_email(), phone=fone_de_b, current_password="errada")
    assert r.status_code == 401, r.text  # senha antes do 409: o telefone alheio não é enumerável
    r = patch(email=_email(), phone=fone_de_b, current_password=SENHA)
    assert r.status_code == 409 and r.json()["detail"] == CONTATO_EM_USO, r.text
    assert _auth_row(user_id)["email"] == antigo
    assert envios == []


def _eventos_email(uid: int) -> int:
    from core.audit import AuditEvent, list_audit_events
    return sum(1 for e in list_audit_events(uid, limit=50) if e["event"] == AuditEvent.EMAIL_CHANGED)


def test_so_troca_de_verdade_audita(user_id, envios):
    patch, antigo = _conta(user_id)
    assert patch(email=antigo, display_name="Outra").status_code == 200  # mesmo e-mail + nome
    assert patch(phone="+55 65 99908-8732").status_code == 200
    assert _eventos_email(user_id) == 0
    assert patch(email=_email(), current_password=SENHA).status_code == 200
    assert _eventos_email(user_id) == 1


def test_dois_patch_simultaneos_trocam_uma_vez_e_avisam_so_o_antigo(user_id, envios, monkeypatch):
    """Duplo clique/duas abas: o 2º PATCH enxerga, sob o lock da linha, o e-mail que o 1º
    já trocou — 200 sem mudança, sem 2º aviso (que iria ao e-mail NOVO), sem 2ª pendência."""
    patch, antigo = _conta(user_id, customer=f"cus_{user_id}")
    original = dashboard.verify_user_password

    def lento(uid, senha):  # os dois passam pela senha juntos, antes de qualquer commit
        time.sleep(0.3)
        return original(uid, senha)

    monkeypatch.setattr(dashboard, "verify_user_password", lento)
    # Alarga a janela DENTRO da transação: depois da leitura sob o lock, antes do commit.
    import asyncio
    import frontend.routes.settings as rota
    leitura = rota.travar_e_ler_email

    async def leitura_lenta(cur, uid):
        linha = await leitura(cur, uid)
        await asyncio.sleep(0.3)
        return linha

    monkeypatch.setattr(rota, "travar_e_ler_email", leitura_lenta)
    novo, codigos = _email(), []
    ts = [threading.Thread(target=lambda: codigos.append(patch(email=novo, current_password=SENHA).status_code))
          for _ in range(2)]
    from frontend.routes import shared
    shared.reset_db_pool()  # pool próprio deste loop (o dos testes anteriores é de loop morto)
    try:
        with patch.client:  # um portal só = um event loop: o pool async da rota é preso a um loop
            [t.start() for t in ts]
            [t.join() for t in ts]
    finally:
        shared.reset_db_pool()
    assert codigos == [200, 200]
    assert _auth_row(user_id)["email"] == novo
    assert [e[0] for e in envios] == [antigo]
    assert _eventos_email(user_id) == 1
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select versao from stripe_email_pendente where user_id = %s", (user_id,))
        assert [r["versao"] for r in cur.fetchall()] == [1]


def test_patch_com_email_antigo_nao_reverte_troca_concorrente(user_id, envios, monkeypatch):
    """PATCH 2 manda o e-mail "atual" (sem senha, julgado sem mudança) enquanto o PATCH 1 troca
    com senha: o 2º não pode regravar o antigo por cima; o nome do 2º corpo segue gravado."""
    patch, antigo = _conta(user_id, customer=f"cus_{user_id}")
    import asyncio
    import frontend.routes.settings as rota
    leitura = rota.travar_e_ler_email

    async def leitura_lenta(cur, uid):  # janela entre o lock e o commit do PATCH 1
        linha = await leitura(cur, uid)
        await asyncio.sleep(0.6)
        return linha

    monkeypatch.setattr(rota, "travar_e_ler_email", leitura_lenta)
    novo, codigos = _email(), []

    def segundo():
        time.sleep(0.2)  # chega depois de PATCH 1 ter o lock, antes do commit dele
        codigos.append(patch(email=antigo, display_name="Qq Nome").status_code)

    ts = [threading.Thread(target=lambda: codigos.append(patch(email=novo, current_password=SENHA).status_code)),
          threading.Thread(target=segundo)]
    from frontend.routes import shared
    shared.reset_db_pool()
    try:
        with patch.client:
            [t.start() for t in ts]
            [t.join() for t in ts]
    finally:
        shared.reset_db_pool()
    assert sorted(codigos) == [200, 200]
    assert _auth_row(user_id)["email"] == novo and _nome(user_id) == "Qq Nome"
    assert [e[0] for e in envios] == [antigo]
    assert _eventos_email(user_id) == 1
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select versao from stripe_email_pendente where user_id = %s", (user_id,))
        assert [r["versao"] for r in cur.fetchall()] == [1]


def test_decifragem_do_antigo_falha_a_troca_segue_sem_aviso(user_id, envios, monkeypatch):
    import db.privacy as privacy

    def quebra(*a, **kw):
        raise RuntimeError("decifragem")

    monkeypatch.setattr(privacy, "email_da_linha", quebra)
    patch, _ = _conta(user_id)
    novo = _email()
    r = patch(email=novo, current_password=SENHA)
    assert r.status_code == 200, r.text
    assert _auth_row(user_id)["email"] == novo
    assert envios == []


def test_conta_com_duas_linhas_segue_em_409_e_nada_muda(user_id, envios):
    patch, antigo = _conta(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, user_id, _email(), phone=None, name="Dup", password_hash=HASH_SENHA)
        conn.commit()
    r = patch(email=_email(), current_password=SENHA)
    assert r.status_code == 409, r.text
    assert envios == []
