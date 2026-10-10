"""/auth/forgot-password: nada que difere entre "e-mail existe" e "não existe" roda antes da resposta.

O ramo cadastrado faz INSERT do token, consulta e envio; o inexistente não faz nada
disso. Dentro da resposta, esse trabalho a mais é um oráculo de tempo de enumeração
de contas: o envio sozinho soma a latência do provedor de e-mail só para quem tem
conta. Por isso tudo depois do rate limit roda na tarefa de fundo.

Duas provas, nenhuma compara medianas entre ramos:
1. Ordem (sem relógio). O `send` do ASGI marca um Event quando sai o último pedaço
   do corpo; cada passo nomeado (token, consulta, envio) ESPERA esse Event e registra
   se ele chegou. Esperar, em vez de só olhar o Event, é de propósito: os
   `BaseHTTPMiddleware` do app retransmitem a resposta por fora e a tarefa de fundo
   começa antes de o último pedaço chegar ao `send` externo, então "olhar e
   registrar" seria uma corrida.
2. Teto contra o envio lento. Só guarda os passos que o teste conhece; qualquer
   espera nova no caminho (um `sleep`, uma consulta a mais) escapa da prova 1. Com o
   envio dormindo LATENCIA_ENVIO, a resposta dos DOIS ramos tem de sair antes de TETO_RESPOSTA.

Controles: handler antigo (token, consulta e envio dentro da resposta) →
test_nada_do_ramo_cadastrado_atrasa_a_resposta, test_resposta_identica_com_e_sem_conta
e test_resposta_nao_espera_a_latencia_do_envio vermelhos; um `asyncio.sleep(0.3)`
inline de LATENCIA_ENVIO só para conta cadastrada, antes do `add_task` → só o do teto vermelho.
Positivos: test_o_email_cadastrado_recebe_o_link_e_o_token_funciona (1 e-mail com a
URL, e o token troca a senha).
"""
import asyncio
import threading
import time
import uuid

import db
from core.services import email_service
from _apoio_auth_app import limpa_rate_limits
from test_auth_email_fora_do_loop import SENHA, _post

_CRIA = db.create_password_reset_token
_TEM_SENHA = db.email_has_password
ESPERA = 5  # teto da espera pela resposta: estourar vira False/vermelho, nunca trava
LATENCIA_ENVIO = 1.0  # envio simulado lento (provedor de e-mail)
# 70% da latência. Em máquina muito carregada (16 processos queimando CPU, load ~35, medido em
# 2026-10-09 com 15 processos novos; remedir antes de reusar) a 1ª chamada fria do app chegou a
# 0,44 s e, já aquecida, a 0,37 s; por isso o teste aquece o app antes de cronometrar e o teto
# deixa folga acima disso. Pega qualquer espera >= LATENCIA_ENVIO; uma espera menor que o teto
# escapa daqui e só a prova da ordem a vê.
TETO_RESPOSTA = 0.7
_MENSAGEM = {"message": "Se este e-mail estiver cadastrado, você receberá as instruções em breve."}


def _email():
    return f"forgot-enum-{uuid.uuid4().hex[:10]}@example.com"


def _pede_reset(monkeypatch, email, latencia_envio=0.0):
    """Um POST /auth/forgot-password.

    Devolve (status, corpo, cabeçalhos, registros, enviados, segundos até a resposta sair).
    """
    resposta_enviada = threading.Event()
    registros, enviados, cabecalhos, saiu_em = {}, [], [], []

    def _marca(nome, original):
        def _passo(*args):
            registros[nome] = resposta_enviada.wait(ESPERA)
            return original(*args)
        return _passo

    def _envia(*args):
        registros["envio"] = resposta_enviada.wait(ESPERA)
        enviados.append(args)
        time.sleep(latencia_envio)
        return True

    def _ao_enviar(msg):
        if msg["type"] == "http.response.start":
            cabecalhos.extend(msg["headers"])
        elif msg["type"] == "http.response.body" and not msg.get("more_body"):
            saiu_em.append(time.monotonic())
            resposta_enviada.set()

    monkeypatch.setattr(db, "create_password_reset_token", _marca("cria", _CRIA))
    monkeypatch.setattr(db, "email_has_password", _marca("tem_senha", _TEM_SENHA))
    monkeypatch.setattr(email_service, "send_password_reset_email", _envia)
    limpa_rate_limits("forgot-password", email)
    inicio = time.monotonic()
    status, corpo = asyncio.run(_post("/auth/forgot-password", {"email": email}, _ao_enviar))
    return status, corpo, sorted(cabecalhos), registros, enviados, saiu_em[0] - inicio


def _tokens_de(user_id):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from password_reset_tokens where user_id = %s", (user_id,))
        return cur.fetchone()["n"]


def test_nada_do_ramo_cadastrado_atrasa_a_resposta(monkeypatch):
    cadastrado = _email()
    db.register_auth_user(cadastrado, SENHA)

    _, _, _, registros, _, _ = _pede_reset(monkeypatch, cadastrado)

    assert registros == {"cria": True, "tem_senha": True, "envio": True}


def test_resposta_identica_com_e_sem_conta(monkeypatch):
    cadastrado, inexistente = _email(), _email()
    db.register_auth_user(cadastrado, SENHA)

    com = _pede_reset(monkeypatch, cadastrado)
    sem = _pede_reset(monkeypatch, inexistente)

    assert com[:3] == sem[:3] and com[0] == 200 and com[1] == _MENSAGEM
    assert sem[3] == {"cria": True} and sem[4] == []  # o inexistente nem consulta nem envia


def test_resposta_nao_espera_a_latencia_do_envio(monkeypatch):
    cadastrado, inexistente = _email(), _email()
    db.register_auth_user(cadastrado, SENHA)
    _pede_reset(monkeypatch, cadastrado)  # aquecimento, NÃO cronometrado: a 1ª chamada fria do app é lenta

    for email in (cadastrado, inexistente):
        *_, segundos = _pede_reset(monkeypatch, email, LATENCIA_ENVIO)
        assert segundos < TETO_RESPOSTA, f"resposta levou {segundos:.3f}s com envio de {LATENCIA_ENVIO}s"


def test_o_email_cadastrado_recebe_o_link_e_o_token_funciona(monkeypatch):
    import frontend.finance_bot_websocket_custom as dashboard

    email = _email()
    db.register_auth_user(email, SENHA)

    _, _, _, _, enviados, _ = _pede_reset(monkeypatch, email)

    assert len(enviados) == 1
    para, url, tem_senha = enviados[0]
    prefixo = f"{dashboard.DASHBOARD_URL}/reset-password#token="
    assert (para, tem_senha, url.startswith(prefixo)) == (email, True, True)
    assert db.consume_password_reset_token(url[len(prefixo):], "senha-nova-789")


def test_pedido_de_a_nao_toca_em_b(monkeypatch):
    a, b = _email(), _email()
    uid_b = int(db.register_auth_user(b, SENHA)["user_id"])
    db.register_auth_user(a, SENHA)

    _pede_reset(monkeypatch, a)

    assert _tokens_de(uid_b) == 0
    assert db.login_auth_user(b, SENHA)


def test_falha_de_infraestrutura_responde_200_e_loga_sem_email(monkeypatch, caplog):
    email = _email()

    def _quebra(*args):
        raise RuntimeError(f"banco caiu para {email}")

    monkeypatch.setattr(db, "create_password_reset_token", _quebra)
    limpa_rate_limits("forgot-password", email)
    with caplog.at_level("ERROR"):
        status, corpo = asyncio.run(_post("/auth/forgot-password", {"email": email}))

    assert (status, corpo) == (200, _MENSAGEM)
    assert "forgot_password_background: RuntimeError" in caplog.text
    assert email not in caplog.text and "banco caiu" not in caplog.text
