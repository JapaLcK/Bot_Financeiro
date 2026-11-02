"""#711/#630 e V3: o telefone digitado no site NÃO liga o WhatsApp a conta nenhuma.

B (com login) digita no site o número n de outra pessoa. Antes, a mensagem seguinte de
quem era dono de n rodava `merge_users` pelo telefone: os dados iam para B (V1, #711), o
login de A sumia (V2, #630) e um número novo e vazio virava B (V3: C mandava "oi" e B
passava a ler tudo). Grupo 7, PR-5 (D1(c), dono): o auto-vínculo por telefone foi
desligado por completo; só o código (`vincular <código>`) liga. O telefone continua LIDO só
para a conta sem credencial (`precisa_senha`, opt-out do lead), nos testes de
`test_senha_obrigatoria_whatsapp.py` e `test_wa_parar.py`.

Pela conversa real (`process_message`), Postgres real; só o envio é falso.

Controles negativos (medidos no PR, ver o relato):
- reinserir o auto-vínculo (`merge_users` + `user_identities` no impl) deixa vermelhos
  o V3, o V1 e o V2 (o dono de n vira B, ou o `auth_accounts` de A some);
- tirar a gravação do marcador em `link_platform_identity` deixa vermelho o positivo do
  código; marcar sem comparar o telefone deixa vermelho o "só com prova".
Positivo: o código de B liga o número de C em B, grava nele e confirma o telefone.
"""
import secrets

import pytest

import db
import db_support
from core.crypto import encrypt_pii_optional, hash_pii_optional
from adapters.whatsapp import wa_runtime as wr
from _paywall_gate_helpers import cadastro_novo, com_plano
from test_senha_obrigatoria import _com_senha, conta_paga_sem_credencial
from test_senha_obrigatoria_whatsapp import (  # noqa: F401 (enviadas é fixture)
    _clique, _dono_do_numero, _fone, _gastos, _manda, _q, enviadas,
)
from test_settings_contact_pii_sync import _client_for
from test_vincular_codigo_numero_novo import TEXTO, VINCULADO
from tests._billing_grants_helpers import garantir_system_event_logs


@pytest.fixture(autouse=True)
def _logs():
    garantir_system_event_logs()  # o evento do vínculo e o log do convite moram nela


def _b_com_senha() -> tuple[int, str]:
    """B: conta do site com senha e plano, com o número n digitado no cadastro."""
    b, _, _ = conta_paga_sem_credencial()
    _com_senha(b)
    return b, _fone(b)


def _existe(tabela: str, coluna: str, uid: int) -> bool:
    return _q(f"select count(*) as n from {tabela} where {coluna}=%s", (uid,))["n"] > 0


def _telefone_de(uid: int):
    """O que só o código escreve: telefone confirmado e WhatsApp verificado."""
    r = _q("select phone_status, whatsapp_verified_at from auth_accounts where user_id=%s", (uid,))
    return r["phone_status"], r["whatsapp_verified_at"]


def _convite(texto: str) -> bool:
    """O convite do `no_match`: ensina o código e não promete ligar pelo telefone."""
    return ("Ainda não tenho uma conta ligada" in texto and "vincular <código>" in texto
            and "me manda um *oi*" not in texto)


def _so_o_convite(enviadas, contexto=""):
    assert len(enviadas) == 1 and _convite(enviadas[0]), (contexto, enviadas)


def _v1():
    """A só-WhatsApp com um lançamento; B com senha e o número de A."""
    b, n = _b_com_senha()
    a = db.get_or_create_canonical_user("whatsapp", n)
    db.add_launch_and_update_balance(a, "despesa", 50, None, "mercado")
    return a, b, n


def _v2(a_com_plano: bool):
    """A conta do site, sem dados, ligada a n por código, telefone diferente; B com n."""
    b, n = _b_com_senha()
    a = com_plano() if a_com_plano else cadastro_novo()
    db.link_platform_identity("whatsapp", n, a)  # o que o `vincular CODIGO` faz
    return a, b, n


def _numero_novo() -> str:
    return f"5511{secrets.randbelow(10**9):09d}"


# ── V3: o bug que o PR-5 fecha ───────────────────────────────────────────────

def test_v3_numero_digitado_por_b_nao_liga_quem_manda_oi(enviadas):
    """B digitou o número de C. C (número novo e vazio) manda "oi" e depois um gasto, dois
    assuntos. Antes: o dono de n virava B e o gasto de C gravava na conta de B."""
    b, n = _b_com_senha()
    c = db.get_or_create_canonical_user("whatsapp", n)  # o usuário que o número de C cria

    _manda(n, "oi")
    _so_o_convite(enviadas, "oi")
    enviadas.clear()
    _manda(n, "gastei 50 mercado")
    _so_o_convite(enviadas, "gasto")

    assert _dono_do_numero(n) not in (None, b), "o número de C foi ligado à conta de B (V3)"
    assert _gastos(b) == 0
    assert _telefone_de(b) == ("pending", None), "o número de C virou confirmado na conta de B"
    assert _existe("users", "id", c)


# ── o caminho legítimo: o código de B ────────────────────────────────────────

@pytest.mark.parametrize("molde", ["vincular {}", "Vincular: {}", "link {}.", "  VINCULAR   {}  "])
def test_codigo_de_b_liga_o_numero_grava_e_confirma_o_telefone(enviadas, molde):
    b, n = _b_com_senha()
    _manda(n, "oi")  # antes do código, o número é só um convite
    _so_o_convite(enviadas)
    enviadas.clear()

    _manda(n, molde.format(db.create_link_code(b)))
    assert _dono_do_numero(n) == b, enviadas
    assert len(enviadas) == 1 and "vinculad" in enviadas[0], enviadas

    enviadas.clear()
    _manda(n, "gastei 50 mercado")
    assert _gastos(b) == 1, enviadas
    # O número ligado é o digitado: quem tem o código mandou mensagem DESSE número.
    estado, verificado = _telefone_de(b)
    assert estado == "confirmed" and verificado is not None


def test_marcador_so_com_prova_numero_diferente_liga_sem_confirmar(enviadas):
    b, n = _b_com_senha()
    c = _numero_novo()
    assert c != n

    _manda(c, f"vincular {db.create_link_code(b)}")
    assert _dono_do_numero(c) == b, enviadas
    assert _telefone_de(b) == ("pending", None), "ligar outro número não confirma o telefone digitado"

    # O checklist da Início lê a identidade, não o marcador (DP-10).
    client, _ = _client_for(b, db.get_auth_user(b)["email"])
    r = client.get("/auth/dashboard-profile")
    assert r.status_code == 200, r.text
    assert r.json()["whatsapp_linked"] is True


def test_dashboard_profile_nao_decifra_a_identidade(enviadas):
    """A1: a rota lê só a EXISTÊNCIA da identidade. `external_id_enc` ruim não derruba a rota
    (decifrar via `list_identities_by_user` dava KeyError no except e 500)."""
    b, n = _b_com_senha()
    db.bind_identity("whatsapp", n, b)
    _q("update user_identities set external_id_enc = 'lixo-nao-cifrado' where provider='whatsapp'"
       " and user_id=%s returning 1", (b,))
    client, _ = _client_for(b, db.get_auth_user(b)["email"])
    r = client.get("/auth/dashboard-profile")
    assert r.status_code == 200, r.text
    assert r.json()["whatsapp_linked"] is True


def test_dashboard_profile_whatsapp_linked_isola_por_usuario():
    """B tem identidade WhatsApp, C não: C não herda o True de B (o filtro de user_id)."""
    b, _ = _b_com_senha()
    c, _ = _b_com_senha()
    db.bind_identity("whatsapp", _numero_novo(), b)
    perfil = lambda uid: _client_for(uid, db.get_auth_user(uid)["email"])[0].get(  # noqa: E731
        "/auth/dashboard-profile").json()["whatsapp_linked"]
    assert perfil(b) is True
    assert perfil(c) is False


def test_dashboard_profile_whatsapp_linked_falso_sem_identidade():
    b, _ = _b_com_senha()
    client, _ = _client_for(b, db.get_auth_user(b)["email"])
    assert client.get("/auth/dashboard-profile").json()["whatsapp_linked"] is False


# Variantes (#909): o celular "55119"+L e o fixo "5511"+L (L em 2-5) são donos diferentes.
# A prova do marcador usa os mesmos candidatos simétricos, então o celular de C ligado por
# código a B (que digitou o fixo) liga, mas não confirma o fixo de B.
def test_marcador_nao_confirma_fixo_com_o_celular_do_outro(enviadas):
    local = f"{2 + secrets.randbelow(4)}{secrets.randbelow(10**7):07d}"
    fixo, celular = f"5511{local}", f"55119{local}"
    b = com_plano()
    _poe_telefone(b, fixo)

    db.link_platform_identity("whatsapp", celular, b)
    assert _telefone_de(b) == ("pending", None)
    db.link_platform_identity("whatsapp", fixo, b)  # o fixo dele, sim
    assert _telefone_de(b)[0] == "confirmed"


# ── V1 e V2: a conta de A não é tocada ───────────────────────────────────────

def test_v1_so_whatsapp_com_dados_nao_vai_para_quem_digitou(enviadas):
    a, b, n = _v1()

    for texto in ("gastei 50 mercado", "Gastei R$ 50 no açougue", "saldo"):
        enviadas.clear()
        _manda(n, texto)
        _so_o_convite(enviadas, texto)
        assert "Encontrei uma conta do site" not in enviadas[0]
    assert _dono_do_numero(n) == a
    assert _gastos(b) == 0 and _gastos(a) == 1
    assert _existe("users", "id", a), "a conta do WhatsApp foi apagada pelo merge"
    assert _telefone_de(b) == ("pending", None), "o número de A virou confirmado na conta de B"


@pytest.mark.parametrize("a_com_plano", [False, True], ids=["sem_plano", "com_plano"])
def test_v2_conta_do_site_nao_perde_o_login(enviadas, a_com_plano):
    a, b, n = _v2(a_com_plano)

    _manda(n, "oi")
    assert _existe("auth_accounts", "user_id", a), "o login de A foi apagado pelo merge (#630)"
    assert _dono_do_numero(n) == a and _existe("auth_accounts", "user_id", b)
    # A usa a própria conta, sem aviso de "não juntei" nem convite (DP-9).
    assert not any(_convite(e) or "cadastrado em outra conta" in e for e in enviadas), enviadas

    enviadas.clear()
    _manda(n, "gastei 50 mercado")
    assert _dono_do_numero(n) == a and _gastos(b) == 0
    assert _gastos(a) == (1 if a_com_plano else 0), enviadas
    assert a_com_plano or "/precos" in enviadas[0], enviadas
    assert _telefone_de(b) == ("pending", None), "o número de A virou confirmado na conta de B"


def test_v2_discord_identidade_do_discord_fica_com_a(enviadas):
    """Apontamento 1: só-WhatsApp ligado por código a uma conta do Discord, sem
    dados. Sem `auth_accounts`, mas é conta: o merge levaria o Discord para B."""
    b, n = _b_com_senha()
    discord = f"d{secrets.randbelow(10**12)}"
    a = db.get_or_create_canonical_user("discord", discord)
    db.link_platform_identity("whatsapp", n, a)

    _manda(n, "oi")
    assert not any(_convite(e) or "cadastrado em outra conta" in e for e in enviadas), enviadas
    assert _dono_do_numero(n) == a
    assert db.get_or_create_canonical_user("discord", discord) == a, "o Discord de A foi para B"
    assert _existe("users", "id", a)


def test_conta_com_login_nao_recebe_o_crie_sua_senha_de_outra(enviadas):
    """A com login e sem dados; B sem credencial com o número de A. Antes, A caía
    no `precisa_senha` de uma conta que não é dele e ficava sem usar o bot."""
    b, _, _ = conta_paga_sem_credencial()
    n = _fone(b)
    a = com_plano()
    db.link_platform_identity("whatsapp", n, a)

    _manda(n, "gastei 50 mercado")
    assert wr.PRECISA_SENHA_WA not in enviadas, enviadas
    assert _dono_do_numero(n) == a and _gastos(a) == 1 and _gastos(b) == 0


# DP-2: quem já estava ligado (pelo auto-vínculo de antes, ou por código) continua. O vínculo
# existente não é desfeito, e a mensagem não reescreve `whatsapp_verified_at` (o autorreparo
# morreu com o auto-vínculo; só o código regrava).
def test_quem_ja_estava_ligado_continua_e_a_mensagem_nao_reescreve_o_marcador(enviadas):
    b, n = _b_com_senha()
    db.bind_identity("whatsapp", n, b)  # o vínculo que o auto-vínculo antigo deixou
    _q("update auth_accounts set phone_status='confirmed', whatsapp_verified_at = now() - interval '3 days'"
       " where user_id=%s returning 1", (b,))
    antes = _telefone_de(b)

    for texto in ("gastei 50 mercado", "saldo"):
        _manda(n, texto)
    assert _dono_do_numero(n) == b and _gastos(b) == 1, enviadas
    assert _telefone_de(b) == antes, "a mensagem reescreveu o marcador"


# O clique é "parem de mandar para este número". B (com senha) digitou n, mas não recebe
# envios nele (só chega a número em `user_identities`, #721): o clique de A desliga a conta
# de A e NUNCA a de B. A conta SEM credencial que digitou o número é desligada junto, e isso
# mora em `test_senha_obrigatoria_whatsapp.py` e `test_wa_parar.py`.
@pytest.mark.parametrize("caso", ["v1", "v2"])
@pytest.mark.parametrize("botao", ["whatsapp_updates_disable", "daily_report_disable"])
def test_opt_out_desliga_a_conta_de_quem_clica_e_nao_a_de_b(enviadas, caso, botao):
    a, b, n = _v1() if caso == "v1" else _v2(a_com_plano=False)
    if botao == "daily_report_disable":
        desligou = lambda uid: not db.get_daily_report_prefs(uid)["enabled"]  # noqa: E731
    else:
        desligou = db.get_whatsapp_updates_opt_out
    # Só-WhatsApp (V1) não tem `auth_accounts`: a preferência dele não existe.
    quem_clica = () if caso == "v1" else (a,)
    assert not any(desligou(u) for u in (b, *quem_clica))

    _clique(n, botao)
    assert all(desligou(u) for u in quem_clica), enviadas
    assert not desligou(b), "o clique de quem recebeu o número desligou a conta de B"
    # V1: A só-WhatsApp não tem conta nem preferência a desligar, é número sem conta (convite).
    assert len(enviadas) == 1 and _convite(enviadas[0]) == (caso == "v1"), enviadas
    assert _dono_do_numero(n) == a and _gastos(b) == 0


def test_v1_parar_digitado_nao_desliga_b(enviadas):
    a, b, n = _v1()

    _manda(n, "Parar")
    assert not db.get_whatsapp_updates_opt_out(b), enviadas
    _so_o_convite(enviadas)  # A não tem conta: nada a parar
    assert _dono_do_numero(n) == a and _gastos(b) == 0


# V1 antes recebia ajuda e billing "como qualquer barrado" (T5, dono M8) e nada junta. Agora o
# número de A é um número sem conta como outro qualquer: toda mensagem que não é o código
# recebe o convite que ensina o código, e nada junta.
@pytest.mark.parametrize("texto", ["ajuda", "Ajuda", "assinar", "plano", "cancelar", "quero assinar"])
def test_v1_qualquer_mensagem_sem_codigo_recebe_o_convite(enviadas, texto):
    a, b, n = _v1()

    _manda(n, texto)
    _so_o_convite(enviadas, texto)
    assert _dono_do_numero(n) == a and _gastos(b) == 0 and _gastos(a) == 1


# A saída legítima de V1/V2: o código de B, que prova os dois lados. `link` é o verbo
# antigo; a resposta não pode falar de Discord.
@pytest.mark.parametrize("caso,verbo,esperado", [
    ("v1", "vincular", "junta"),
    ("v1", "link", "junta"),
    ("v2_sem_plano", "vincular", "apagaria_login"),  # #635: os dois têm login (dono, M8)
    ("v2_com_plano", "vincular", "origem_presa"),  # A pagante não some: recusa com o motivo dela
])
def test_vincular_codigo_de_b_e_a_saida(enviadas, caso, verbo, esperado):
    a, b, n = _v1() if caso == "v1" else _v2(a_com_plano=caso == "v2_com_plano")

    _manda(n, f"{verbo} {db.create_link_code(b)}")
    if esperado == "junta":
        ok = VINCULADO if verbo == "vincular" else "✅ Contas vinculadas! Este canal agora usa os dados da sua conta."
        assert len(enviadas) == 1 and enviadas[0].startswith(ok), enviadas
        assert _dono_do_numero(n) == b
        assert _gastos(b) == (1 if caso == "v1" else 0)
        assert not _existe("users", "id", a), "A some na junção (#635), agora com prova"
    else:
        assert enviadas == [TEXTO[esperado]], enviadas
        assert _dono_do_numero(n) == a and _existe("auth_accounts", "user_id", a)
        assert _existe("auth_accounts", "user_id", b)


# T1 do Tester: `phone_lookup_candidates` tira o 9 de todo celular, então o celular
# "55119"+L de A casa com o FIXO "5511"+L de B quando L começa com 2-5. São duas
# linhas de donos diferentes. Hoje nenhum caminho do telefone liga, mas o teste fica:
# quem "consertar" a #909 pondo o 9 também nessa faixa reabriria o caso se o telefone
# voltasse a ligar.
def _poe_telefone(uid: int, fone: str) -> None:
    """O telefone digitado no site (cadastro ou Configurações)."""
    _q("update auth_accounts set phone_e164=%s, phone_hash=%s, phone_enc=%s where user_id=%s returning 1",
       (fone, hash_pii_optional(fone, kind="phone"), encrypt_pii_optional(fone), uid))
    db_support.invalidate_auth_user_cache(uid)


# Os dois sentidos: B com o fixo e A no celular, e B com o celular (ligado por
# código) e o fixo digitado, com A no fixo.
@pytest.mark.parametrize("sentido", ["b_no_fixo", "b_no_celular"])
@pytest.mark.parametrize("caso", ["v1", "v2"])
def test_fixo_de_b_nao_e_alias_do_celular_de_a(enviadas, caso, sentido):
    local = f"{2 + secrets.randbelow(4)}{secrets.randbelow(10**7):07d}"  # 2-5: fixo
    fixo, celular = f"5511{local}", f"55119{local}"
    de_b, de_a = (fixo, celular) if sentido == "b_no_fixo" else (celular, fixo)
    b = com_plano()
    _poe_telefone(b, fixo)
    db.link_platform_identity("whatsapp", de_b, b)  # o número de B, por código
    assert _dono_do_numero(de_b) == b, enviadas
    if caso == "v1":
        a = db.get_or_create_canonical_user("whatsapp", de_a)
        db.add_launch_and_update_balance(a, "despesa", 50, None, "farmacia")
    else:
        a = cadastro_novo()
        db.link_platform_identity("whatsapp", de_a, a)

    for texto in ("gastei 30 na farmacia", "saldo"):
        enviadas.clear()
        _manda(de_a, texto)
        assert _dono_do_numero(de_a) == a, ("o número de A foi ligado à conta de B", texto, enviadas)
        assert _gastos(b) == 0, ("os lançamentos de A foram para B", texto, enviadas)
    assert _existe("users", "id", a)
    if caso == "v2":
        assert _existe("auth_accounts", "user_id", a), "o login de A foi apagado"


# R2-1 do Tester, mesma raiz do T1, sem identidade em B: B digitou o PRÓPRIO fixo
# "5511"+L (L em 2-5) e nunca falou com o bot. O celular "55119"+L de A não liga em B.
@pytest.mark.parametrize("caso", ["v3", "v1"])
def test_celular_de_a_nao_casa_com_o_fixo_digitado_por_b(enviadas, caso):
    local = f"{2 + secrets.randbelow(4)}{secrets.randbelow(10**7):07d}"
    fixo, celular = f"5511{local}", f"55119{local}"
    b = com_plano()
    _poe_telefone(b, fixo)
    if caso == "v1":
        a = db.get_or_create_canonical_user("whatsapp", celular)
        db.add_launch_and_update_balance(a, "despesa", 50, None, "farmacia")

    for texto in ("oi", "gastei 30 na farmacia"):
        enviadas.clear()
        _manda(celular, texto)
        assert _dono_do_numero(celular) != b, ("o celular de A foi ligado à conta de B", texto, enviadas)
        assert _gastos(b) == 0, (texto, enviadas)
        assert "Encontrei uma conta do site" not in enviadas[0], (texto, enviadas)
        assert "Ainda não tenho uma conta ligada" in enviadas[0], (texto, enviadas)


# O positivo do R2-1 ("B digitou o próprio celular num formato e o WhatsApp dele chega no outro",
# L em 6-9, celular antigo) ligava sozinho. Agora o telefone, igual ou equivalente, não liga:
# o convite; liga com o código, e o número equivalente confirma o telefone.
@pytest.mark.parametrize("digitado_com_9", [False, True])
def test_celular_legitimo_liga_nos_dois_formatos_so_com_o_codigo(enviadas, digitado_com_9):
    local = f"{6 + secrets.randbelow(4)}{secrets.randbelow(10**7):07d}"
    com_9, sem_9 = f"55119{local}", f"5511{local}"
    digitado, wa = (com_9, sem_9) if digitado_com_9 else (sem_9, com_9)
    b = com_plano()
    _poe_telefone(b, digitado)

    _manda(wa, "oi")
    _so_o_convite(enviadas)
    assert _dono_do_numero(wa) != b
    enviadas.clear()
    _manda(wa, f"vincular {db.create_link_code(b)}")
    assert _dono_do_numero(wa) == b, enviadas
    _manda(wa, "gastei 50 mercado")
    assert _gastos(b) == 1, enviadas
    assert _telefone_de(b)[0] == "confirmed"


# R2-2: a recusa do código diz o motivo verdadeiro (o `merge_conflict` do auto-vínculo morreu
# com ele; o código é o único caminho). Autoindicação: B é dono do afiliado que indicou A.
def _autoindicacao(a, b):
    af = _q("insert into affiliates(user_id, code) values (%s, %s) returning id",
            (b, secrets.token_hex(5)))["id"]
    _q("insert into affiliate_referrals(affiliate_id, referred_user_id) values (%s, %s) returning 1",
       (af, a))


def test_recusa_do_codigo_diz_o_motivo_da_autoindicacao(enviadas):
    b, n = _b_com_senha()
    a = db.get_or_create_canonical_user("whatsapp", n)
    _autoindicacao(a, b)

    _manda(n, f"vincular {db.create_link_code(b)}")
    assert _dono_do_numero(n) == a and _existe("users", "id", a)
    assert enviadas == [TEXTO["autoindicacao"]], enviadas


# Achado 5 do plano: o `precisa_senha` engolia o código. B (conta do quiz, sem senha) digitou o
# número de C; C tem conta com login e plano, gera o código dela e o manda do número dele. O
# código passa e liga; o resto do que sai desse número segue barrado, e nada vai para B.
def test_codigo_passa_no_precisa_senha_e_o_resto_continua_barrado(enviadas):
    b, _, _ = conta_paga_sem_credencial()  # sem senha: a leitura do telefone ainda vale
    n = _fone(b)
    c = com_plano()

    _manda(n, "gastei 50 mercado")
    assert enviadas == [wr.PRECISA_SENHA_WA], enviadas  # positivo: o texto de B segue igual
    enviadas.clear()
    _manda(n, f"Vincular: {db.create_link_code(c)}")
    assert _dono_do_numero(n) == c, enviadas
    assert wr.PRECISA_SENHA_WA not in enviadas and enviadas[0].startswith(VINCULADO), enviadas

    enviadas.clear()
    _manda(n, "gastei 50 mercado")
    assert _gastos(c) == 1 and _gastos(b) == 0, enviadas


# DP-6(b) + evento: o sucesso do `vincular` ensina três comandos, e o funil ganha um contador
# que guarda só o número mascarado.
def test_sucesso_do_vincular_ensina_exemplos_e_registra_o_evento(enviadas):
    b, n = _b_com_senha()

    _manda(n, f"vincular {db.create_link_code(b)}")
    assert len(enviadas) == 1 and enviadas[0].startswith(VINCULADO), enviadas
    for exemplo in ("*gastei 50 mercado*", "*recebi 1000 salario*", "*saldo*", "*ajuda*"):
        assert exemplo in enviadas[0], enviadas
    linha = _q("select details, user_id from system_event_logs where event_type = 'whatsapp_code_link_success'"
               " and user_id = %s", (b,))
    assert linha and linha["details"]["wa_id"] != n and n not in str(linha["details"]), linha
    assert wr.mask_phone(n) == linha["details"]["wa_id"]


# A3 + Codex P2 (#1006): o código pode vir do site OU do `link` da outra plataforma (que não exige
# conta no site). O texto de código inválido orienta os dois caminhos; no Discord não cita WhatsApp.
@pytest.mark.parametrize("plataforma", ["whatsapp", "discord"])
@pytest.mark.parametrize("verbo", ["link", "vincular"])
def test_codigo_invalido_orienta_site_e_link_na_outra_plataforma(verbo, plataforma):
    from core.handlers import account
    ext = _numero_novo() if plataforma == "whatsapp" else f"d{secrets.randbelow(10**12)}"
    texto = getattr(account, verbo)(plataforma, ext, "pbl00000000000000000000000000000000")
    assert texto.startswith("❌ Código inválido"), texto
    assert "envie *link* nela" in texto and "site" in texto, texto  # o caminho de quem não tem conta no site
    if plataforma == "discord":
        assert "WhatsApp" not in texto and "vincular <código>" not in texto, texto
    else:
        assert "Conectar WhatsApp" in texto and "*vincular <código>*" in texto, texto


# A5: o marcador é auxiliar. Se ele explode DEPOIS do merge e do bind, o vínculo segue como
# sucesso (✅), e não vira o paywall de uma pessoa que acabou de ligar.
def test_falha_no_marcador_nao_derruba_o_vinculo(enviadas, monkeypatch):
    import db.users as users

    def explode(*a, **k):
        raise RuntimeError("banco caiu no marcador")

    monkeypatch.setattr(users, "_confirmar_telefone_do_whatsapp", explode)
    b, n = _b_com_senha()
    _manda(n, f"vincular {db.create_link_code(b)}")
    assert _dono_do_numero(n) == b, enviadas
    assert len(enviadas) == 1 and enviadas[0].startswith(VINCULADO), enviadas
    assert _telefone_de(b) == ("pending", None)  # sem marcador, mas ligado


# A5, ramo `current == target`: o número já é de B (identidade criada antes) e B manda o código
# dele. Sem merge, o marcador também roda e também não pode derrubar o ✅.
def test_falha_no_marcador_no_ramo_do_numero_ja_ligado(enviadas, monkeypatch):
    import db.users as users

    def explode(*a, **k):
        raise RuntimeError("banco caiu no marcador")

    monkeypatch.setattr(users, "_confirmar_telefone_do_whatsapp", explode)
    b, n = _b_com_senha()
    db.bind_identity("whatsapp", n, b)
    _manda(n, f"vincular {db.create_link_code(b)}")
    assert _dono_do_numero(n) == b, enviadas
    assert len(enviadas) == 1 and enviadas[0].startswith(VINCULADO), enviadas


# Quem manda o texto inválido do código recebe a MESMA constante, com o verbo do convite.
@pytest.mark.parametrize("verbo", ["link", "vincular"])
def test_codigo_invalido_diz_a_mesma_coisa_nos_dois_verbos(enviadas, verbo):
    _manda(_numero_novo(), "oi")
    enviadas.clear()
    _manda(_numero_novo(), f"{verbo} pbl00000000000000000000000000000000")
    assert len(enviadas) == 1 and enviadas[0].startswith("❌ Código inválido"), enviadas
    assert "vincular <código>" in enviadas[0] and "Envie *link*" not in enviadas[0], enviadas
