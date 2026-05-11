"""
Cobre a tool `add_launch` da IA conversacional. A tool valida args e delega
pra `core.handlers.launches.add_from_entities` — então os testes garantem:
  - validação de args (tipo enum, valor > 0) não escreve no DB
  - delegação ao handler produz a mesma mensagem padrão do bot tradicional
  - categoria inferida vs explícita
  - data ISO 8601 vira criado_em correto
"""
from decimal import Decimal

import db
from core.services.ai_chat._context import CURRENT_PLATFORM
from core.services.ai_chat.tools.launches import _add_launch_execute


def test_execute_despesa_basica(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")

    msg = _add_launch_execute(user_id, {
        "tipo": "despesa",
        "valor": 50,
        "alvo": "mercado",
    })

    # Formato do handler (mesmo do bot tradicional)
    assert "💸 **Despesa registrada**: R$ 50,00" in msg
    assert "🏷️ Categoria: alimentação" in msg  # "mercado" cai em alimentação via LOCAL_RULES
    assert "🏦 Saldo: R$ 950,00" in msg
    assert "ID: #" in msg
    assert db.get_balance(user_id) == Decimal("950")


def test_execute_receita_atualiza_saldo(user_id):
    msg = _add_launch_execute(user_id, {
        "tipo": "receita",
        "valor": 200,
        "alvo": "freela",
    })

    assert "💰 **Receita registrada**: R$ 200,00" in msg
    assert "🏦 Saldo: R$ 200,00" in msg
    assert db.get_balance(user_id) == Decimal("200")


def test_execute_categoria_explicita_respeitada(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")

    msg = _add_launch_execute(user_id, {
        "tipo": "despesa",
        "valor": 30,
        "alvo": "presente da mãe",
        "categoria": "lazer",
    })

    assert "🏷️ Categoria: lazer" in msg
    rows = db.list_launches(user_id, limit=2)
    assert rows[0]["categoria"] == "lazer"


def test_execute_tipo_invalido_nao_escreve(user_id):
    msg = _add_launch_execute(user_id, {"tipo": "transferencia", "valor": 50})

    assert "Tipo inválido" in msg
    assert db.get_balance(user_id) == Decimal("0")
    assert db.list_launches(user_id, limit=1) == []


def test_execute_valor_zero_nao_escreve(user_id):
    msg = _add_launch_execute(user_id, {"tipo": "despesa", "valor": 0})

    assert "maior que zero" in msg
    assert db.get_balance(user_id) == Decimal("0")


def test_execute_data_iso_define_criado_em(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")

    _add_launch_execute(user_id, {
        "tipo": "despesa",
        "valor": 10,
        "alvo": "padaria",
        "data": "2026-04-15",
    })

    # list_launches ordena por criado_em desc, e a data passada é mais antiga
    # que o seed (now), então filtra por alvo em vez de presumir ordem.
    rows = db.list_launches(user_id, limit=10)
    padaria = next(r for r in rows if r.get("alvo") == "padaria")
    assert padaria["criado_em"].date().isoformat() == "2026-04-15"


def test_execute_no_whatsapp_seta_pending_action_de_botao(user_id):
    """Quando platform=whatsapp, o handler seta `recategorize_launch_offer`
    pra o wa_runtime renderizar botões 'Trocar categoria' / 'Desfazer'."""
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")

    token = CURRENT_PLATFORM.set("whatsapp")
    try:
        _add_launch_execute(user_id, {
            "tipo": "despesa",
            "valor": 50,
            "alvo": "mercado",
        })
    finally:
        CURRENT_PLATFORM.reset(token)

    pending = db.get_pending_action(user_id)
    assert pending is not None
    assert pending["action_type"] == "recategorize_launch_offer"
    assert "launch_id" in pending["payload"]
    assert "user_seq" in pending["payload"]


def test_execute_no_dashboard_nao_seta_pending_action_de_botao(user_id):
    """Dashboard não renderiza botões — não polui o pending_action slot."""
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")

    # default do CURRENT_PLATFORM = 'dashboard'
    _add_launch_execute(user_id, {
        "tipo": "despesa",
        "valor": 50,
        "alvo": "mercado",
    })

    pending = db.get_pending_action(user_id)
    assert pending is None


def test_execute_consistente_com_handler_tradicional(user_id):
    """Tool da IA usa a mesma fn `add_from_entities` que o bot — então a
    resposta deve ser IDÊNTICA pra args equivalentes (modulo balance/IDs
    que dependem do estado, por isso comparamos campos estruturais)."""
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    msg_ia = _add_launch_execute(user_id, {
        "tipo": "despesa",
        "valor": 25,
        "alvo": "uber",
    })

    # Mesma chamada via handler direto, num user separado, deve produzir
    # estrutura idêntica de linhas (despesa, categoria, saldo, ID).
    from core.handlers.launches import add_from_entities
    db.ensure_user(user_id + 1)
    try:
        db.add_launch_and_update_balance(user_id + 1, "receita", 1000, None, "seed")
        msg_handler = add_from_entities(
            user_id + 1,
            tipo="despesa",
            valor=25,
            alvo="uber",
            platform="ia",
        )

        # Mesma quebra de linhas; só o ID interno pode diferir
        ia_lines = msg_ia.split("\n")
        handler_lines = msg_handler.split("\n")
        assert len(ia_lines) == len(handler_lines)
        for i, (a, b) in enumerate(zip(ia_lines, handler_lines)):
            if i == len(ia_lines) - 1:  # última linha = ID, depende do estado
                assert a.startswith("ID: #") and b.startswith("ID: #")
            else:
                assert a == b
    finally:
        # Cleanup do user secundário
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from launches where user_id = %s", (user_id + 1,))
                cur.execute("delete from accounts where user_id = %s", (user_id + 1,))
                cur.execute("delete from users where id = %s", (user_id + 1,))
            conn.commit()
