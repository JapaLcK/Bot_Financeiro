"""#711/#630: o auto-vínculo mesclava pelo telefone que OUTRA conta digitou no site.

B (com login) digita no site o número n de A. Antes, a mensagem seguinte de A
rodava `merge_users(A, B)`: os dados de A iam para B (V1, #711) e, se A era conta
do site, o login de A sumia (V2, #630). D1(a) do dono: o auto-vínculo não mescla
remetente que já é conta (login ou outro canal) nem remetente com dados; a junção
legítima é pelo código. Número novo e vazio continua ligando sozinho (V3, PR-5).

Pela conversa real (`process_message`), Postgres real; só o envio é falso.

Controles negativos (medidos no PR, ver o relato):
- sem a regra 1 (`ja_em_conta` no ramo `current != target`), o V2 fica vermelho
  (o `auth_accounts` de A some);
- sem a regra 2 (`_tem_dados_financeiros` antes do `conta_sem_credencial`), o V1
  fica vermelho (o merge acontece);
- com a regra 1 olhando só `auth_accounts`, o V2-Discord fica vermelho (a
  identidade Discord de A passa para B);
- sem o `target_user_id` no opt-out, B segue ligado;
- sem o mapa de motivos no `merge_conflict`, a autoindicação recebe o texto de
  "dados cada um".
Positivo: número novo e vazio liga em B e grava nele.
"""
import secrets

import pytest

import db
import db.users as users
import db_support
from core.crypto import encrypt_pii_optional, hash_pii_optional
from adapters.whatsapp import wa_runtime as wr
from _paywall_gate_helpers import cadastro_novo, com_plano
from test_senha_obrigatoria import _com_senha, conta_paga_sem_credencial
from test_senha_obrigatoria_whatsapp import (  # noqa: F401 (enviadas é fixture)
    _clique, _dono_do_numero, _fone, _gastos, _manda, _q, enviadas,
)
from test_vincular_codigo_numero_novo import TEXTO, VINCULADO
from tests._billing_grants_helpers import garantir_system_event_logs


@pytest.fixture(autouse=True)
def _logs():
    garantir_system_event_logs()  # o "uma vez" do aviso da saudação mora nela


def _b_com_senha() -> tuple[int, str]:
    """B: conta do site com senha e plano, com o número n digitado no cadastro."""
    b, _, _ = conta_paga_sem_credencial()
    _com_senha(b)
    return b, _fone(b)


def _existe(tabela: str, coluna: str, uid: int) -> bool:
    return _q(f"select count(*) as n from {tabela} where {coluna}=%s", (uid,))["n"] > 0


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


def test_v1_so_whatsapp_com_dados_nao_vai_para_quem_digitou(enviadas):
    a, b, n = _v1()

    for texto in ("gastei 50 mercado", "Gastei R$ 50 no açougue"):
        enviadas.clear()
        _manda(n, texto)
        assert enviadas == [wr.NAO_JUNTEI_WA], (texto, enviadas)
    assert _dono_do_numero(n) == a
    assert _gastos(b) == 0 and _gastos(a) == 1
    assert _existe("users", "id", a), "a conta do WhatsApp foi apagada pelo merge"


@pytest.mark.parametrize("a_com_plano", [False, True], ids=["sem_plano", "com_plano"])
def test_v2_conta_do_site_nao_perde_o_login(enviadas, a_com_plano):
    a, b, n = _v2(a_com_plano)

    _manda(n, "oi")
    assert _existe("auth_accounts", "user_id", a), "o login de A foi apagado pelo merge (#630)"
    assert _dono_do_numero(n) == a
    assert enviadas == [wr.NAO_JUNTEI_WA], enviadas

    enviadas.clear()
    _manda(n, "gastei 50 mercado")
    assert wr.NAO_JUNTEI_WA not in enviadas, "o aviso é só na saudação, uma vez"
    assert _dono_do_numero(n) == a and _gastos(b) == 0
    assert _gastos(a) == (1 if a_com_plano else 0), enviadas
    assert a_com_plano or "/precos" in enviadas[0], enviadas


def test_v2_discord_identidade_do_discord_fica_com_a(enviadas):
    """Apontamento 1: só-WhatsApp ligado por código a uma conta do Discord, sem
    dados. Sem `auth_accounts`, mas é conta: o merge levaria o Discord para B."""
    b, n = _b_com_senha()
    discord = f"d{secrets.randbelow(10**12)}"
    a = db.get_or_create_canonical_user("discord", discord)
    db.link_platform_identity("whatsapp", n, a)

    _manda(n, "oi")
    assert enviadas == [wr.NAO_JUNTEI_WA], enviadas
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


def test_positivo_numero_novo_vazio_liga_e_grava_em_b(enviadas):
    b, n = _b_com_senha()

    _manda(n, "oi")
    assert _dono_do_numero(n) == b and "WELCOME" in enviadas, enviadas
    _manda(n, "gastei 50 mercado")
    assert _gastos(b) == 1, enviadas


# O clique é "parem de mandar para este número": B digitou n e recebe envios nele.
@pytest.mark.parametrize("caso", ["v1", "v2"])
@pytest.mark.parametrize("botao", ["whatsapp_updates_disable", "daily_report_disable"])
def test_opt_out_desliga_a_e_b(enviadas, caso, botao):
    a, b, n = _v1() if caso == "v1" else _v2(a_com_plano=False)
    if botao == "daily_report_disable":
        desligou = lambda uid: not db.get_daily_report_prefs(uid)["enabled"]  # noqa: E731
        contas = (a, b)
    else:
        desligou = db.get_whatsapp_updates_opt_out
        # Só-WhatsApp (V1) não tem `auth_accounts`: a preferência dele não existe.
        contas = (b,) if caso == "v1" else (a, b)
    assert not any(desligou(u) for u in contas)

    _clique(n, botao)
    assert all(desligou(u) for u in contas), enviadas
    assert len(enviadas) == 1 and enviadas[0] != wr.NAO_JUNTEI_WA, enviadas
    assert _dono_do_numero(n) == a and _gastos(b) == 0


# A saída legítima de V1/V2: o código de B, que prova os dois lados. `link` é o
# verbo que o `NAO_JUNTEI_WA` ensina; a resposta não pode falar de Discord.
@pytest.mark.parametrize("caso,verbo,esperado", [
    ("v1", "vincular", "junta"),
    ("v1", "link", "junta"),
    ("v2_sem_plano", "vincular", "junta"),
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


# T1 do Tester: `phone_lookup_candidates` tira o 9 de todo celular, então o celular
# "55119"+L de A casa com o FIXO "5511"+L de B quando L começa com 2-5. São duas
# linhas de donos diferentes, e o fixo de B já é identidade do WhatsApp de B: a
# exceção do alias (interseção simples) mesclava A em B. Controle negativo: voltar
# à interseção deixa os dois vermelhos.
def _poe_telefone(uid: int, fone: str) -> None:
    """O telefone digitado no site (cadastro ou Configurações)."""
    _q("update auth_accounts set phone_e164=%s, phone_hash=%s, phone_enc=%s where user_id=%s returning 1",
       (fone, hash_pii_optional(fone, kind="phone"), encrypt_pii_optional(fone), uid))
    db_support.invalidate_auth_user_cache(uid)


# Os dois sentidos: B com o fixo e A no celular, e B com o celular (ligado por
# código) e o fixo digitado, com A no fixo. Este segundo é o que a checagem do
# alias nos dois sentidos fecha sozinha: o fixo de A casa de verdade com o
# telefone digitado em B, e o celular de B "contém" o fixo pela interseção.
@pytest.mark.parametrize("sentido", ["b_no_fixo", "b_no_celular"])
@pytest.mark.parametrize("caso", ["v1", "v2"])
def test_fixo_de_b_nao_e_alias_do_celular_de_a(enviadas, caso, sentido):
    local = f"{2 + secrets.randbelow(4)}{secrets.randbelow(10**7):07d}"  # 2-5: fixo
    fixo, celular = f"5511{local}", f"55119{local}"
    de_b, de_a = (fixo, celular) if sentido == "b_no_fixo" else (celular, fixo)
    b = com_plano()
    _poe_telefone(b, fixo)
    if sentido == "b_no_fixo":
        _manda(fixo, "oi")  # o fixo de B liga em B, legítimo
    else:
        db.link_platform_identity("whatsapp", celular, b)  # o celular de B, por código
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
# "5511"+L (L em 2-5) e nunca falou com o bot. A busca pelo telefone usava os
# candidatos do celular "55119"+L de A, que incluem o fixo, e ligava A na conta de B.
# Controle negativo: tirar o filtro de candidatos do impl deixa os dois vermelhos.
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
        assert wr.NAO_JUNTEI_WA not in enviadas, (texto, enviadas)
        assert "Ainda não tenho uma conta ligada" in enviadas[0], (texto, enviadas)


# Positivo do R2-1: B digitou o próprio celular num formato (L em 6-9, celular
# antigo) e o WhatsApp dele chega no outro. Continua ligando, nos dois sentidos.
@pytest.mark.parametrize("digitado_com_9", [False, True])
def test_celular_legitimo_liga_nos_dois_formatos(enviadas, digitado_com_9):
    local = f"{6 + secrets.randbelow(4)}{secrets.randbelow(10**7):07d}"
    com_9, sem_9 = f"55119{local}", f"5511{local}"
    digitado, wa = (com_9, sem_9) if digitado_com_9 else (sem_9, com_9)
    b = com_plano()
    _poe_telefone(b, digitado)

    _manda(wa, "oi")
    assert _dono_do_numero(wa) == b and "WELCOME" in enviadas, enviadas
    _manda(wa, "gastei 50 mercado")
    assert _gastos(b) == 1, enviadas


# R2-2: o `merge_conflict` que sobra (remetente só-WhatsApp vazio) diz o motivo
# verdadeiro. Autoindicação: B é dono do afiliado que indicou A. Corrida: A grava
# um lançamento entre a checagem do auto-vínculo e a do `merge_users`.
def _autoindicacao(a, b):
    af = _q("insert into affiliates(user_id, code) values (%s, %s) returning id",
            (b, secrets.token_hex(5)))["id"]
    _q("insert into affiliate_referrals(affiliate_id, referred_user_id) values (%s, %s) returning 1",
       (af, a))


def _corrida(a, b, monkeypatch):
    db.add_launch_and_update_balance(b, "despesa", 20, None, "mercado")
    real, vez = users._tem_dados_financeiros, []

    def checa_e_grava_na_origem(cur, uid):
        tem = real(cur, uid)
        if uid == a and not vez:  # a 1ª é a do auto-vínculo; a do merge já vê o lançamento
            vez.append(1)
            with db.get_conn() as conn, conn.cursor() as c2:
                c2.execute("insert into launches(user_id, tipo, valor, alvo, nota) "
                           "values (%s, 'despesa', 10, null, 'corrida')", (a,))
                conn.commit()
        return tem

    monkeypatch.setattr(users, "_tem_dados_financeiros", checa_e_grava_na_origem)


@pytest.mark.parametrize("motivo", ["autoindicacao", "dados_dos_dois_lados"])
def test_merge_conflict_do_auto_vinculo_diz_o_motivo(enviadas, monkeypatch, motivo):
    b, n = _b_com_senha()
    a = db.get_or_create_canonical_user("whatsapp", n)
    if motivo == "autoindicacao":
        _autoindicacao(a, b)
    else:
        _corrida(a, b, monkeypatch)

    _manda(n, "oi")
    assert _dono_do_numero(n) == a and _existe("users", "id", a)
    if motivo == "autoindicacao":
        assert enviadas == [TEXTO["autoindicacao"]], enviadas
    else:  # o texto próprio do auto-vínculo (#607), que diz onde o que for mandado fica
        assert len(enviadas) == 1 and "fica na conta do WhatsApp" in enviadas[0], enviadas

