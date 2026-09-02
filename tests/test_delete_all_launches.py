"""
Regressão pra tool `delete_all_launches` da IA conversacional.

Contexto do bug (screenshot WhatsApp): user mandou "Apague todos os lançamentos";
a IA NÃO tinha tool pra isso e improvisou um "Confirma com sim ou não?" como
texto livre — sem registrar pending. Aí o "Sim" caía no determinístico e morria
em "Não entendi". Conserto: tool real `delete_all_launches` que pede confirmação
de verdade (seta ai_pending_action), e o "sim" seguinte executa.

Os testes do fluxo de confirmação NÃO tocam OpenAI: o caminho pending+"sim" em
`_chat_inner` executa a tool e retorna ANTES de chamar o LLM, então roda mesmo
com o kill switch de rede do conftest ativo.
"""

import db
from db import add_launch_and_update_balance, get_balance
from core.services.ai_chat import chat
from core.services.ai_chat.tools import get_tool


def _bal(uid: int) -> float:
    return round(float(get_balance(uid)), 2)


def test_delete_all_launches_tool_registrada():
    tool = get_tool("delete_all_launches")
    assert tool is not None, "tool delete_all_launches não registrada"
    assert tool.is_write is True
    assert tool.requires_confirmation is True, "precisa pedir confirmação (destrutivo em massa)"
    assert tool.summary is not None and tool.validate is not None


def test_delete_all_launches_db_reverte_saldo(user_id: int):
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    add_launch_and_update_balance(user_id, "despesa", 200, "luz", "paguei 200 luz")
    add_launch_and_update_balance(user_id, "despesa", 50, "mercado", "gastei 50 mercado")
    assert _bal(user_id) == 750.0
    assert db.count_launches(user_id) == 3

    result = db.delete_all_launches_and_rollback(user_id)
    assert result == {"deleted": 3, "kept_no_effects": [], "kept_unsafe": [],
                      "errors": [], "remaining": 0}
    assert db.count_launches(user_id) == 0
    assert _bal(user_id) == 0.0, "saldo deve voltar ao estado pré-lançamentos"


def test_delete_all_launches_validate_bloqueia_quando_vazio(user_id: int):
    tool = get_tool("delete_all_launches")
    # Sem lançamentos → valida com mensagem amigável (evita 'confirma apagar tudo?' inútil)
    err = tool.validate(user_id, {})
    assert err is not None and "nenhum lançamento" in err.lower()

    # Com lançamento → valida None (segue pro fluxo de confirmação)
    add_launch_and_update_balance(user_id, "despesa", 10, "cafe", "gastei 10 cafe")
    assert tool.validate(user_id, {}) is None


def test_delete_all_launches_confirmacao_sim_executa(user_id: int):
    """End-to-end do caminho de confirmação, SEM OpenAI: seta o pending (como
    a tool faria) e manda 'sim' — o chat() executa a tool e limpa o pending."""
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    add_launch_and_update_balance(user_id, "despesa", 300, "aluguel", "paguei 300 aluguel")
    assert _bal(user_id) == 700.0

    # Simula o que `_dispatch_tool` faz quando o LLM chama a tool de write.
    db.ai_set_pending_action(
        user_id,
        "delete_all_launches",
        {},
        "apagar TODOS os seus lançamentos e reverter o saldo",
    )
    assert db.ai_get_pending_action(user_id) is not None

    resp = chat(user_id, "sim", monthly_limit=1000, platform="whatsapp")

    assert "apaguei" in resp.lower(), f"esperava confirmação de exclusão, veio: {resp!r}"
    assert db.count_launches(user_id) == 0
    assert _bal(user_id) == 0.0
    assert db.ai_get_pending_action(user_id) is None, "pending deve ser limpo após executar"


def test_delete_all_launches_confirmacao_nao_cancela(user_id: int):
    """'não' após o pending NÃO apaga nada e limpa o pending."""
    add_launch_and_update_balance(user_id, "despesa", 80, "uber", "gastei 80 uber")
    assert db.count_launches(user_id) == 1

    db.ai_set_pending_action(
        user_id, "delete_all_launches", {}, "apagar TODOS os seus lançamentos"
    )
    resp = chat(user_id, "não", monthly_limit=1000, platform="whatsapp")

    assert db.count_launches(user_id) == 1, "cancelar não pode apagar nada"
    assert db.ai_get_pending_action(user_id) is None
    # Controle positivo do cancelamento: quem VENCE o CAS continua ouvindo
    # "não fiz nada" — é o único que sabe que nada aconteceu. O perdedor não
    # afirma desfecho nenhum (`_CAS_PERDIDO`, em
    # test_ai_chat_concurrent_confirm.py).
    assert "não fiz nada" in resp.lower(), resp


def _pocket_balance(user_id: int, name: str):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select balance from pockets where user_id=%s and lower(name)=lower(%s)",
                (user_id, name),
            )
            row = cur.fetchone()
            return float(row["balance"]) if row else None


def _investment_balance(user_id: int, name: str):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select balance from investments where user_id=%s and lower(name)=lower(%s)",
                (user_id, name),
            )
            row = cur.fetchone()
            return float(row["balance"]) if row else None


def test_delete_all_launches_preserva_caixinha_e_investimento(user_id: int):
    """Regressão do bug 'apaga tudo zerava o usuário do começo': caixinhas e
    investimentos NÃO podem ser tocados — só despesas/receitas (e pagamento de
    fatura) somem.

    Armadilha que isto trava: criar caixinha/investimento gera um launch com
    `is_internal_movement=false`; se o filtro fosse por essa flag (em vez de
    `tipo in ('despesa','receita')`), apagá-lo deletaria a caixinha/o
    investimento junto (efeitos.create_pocket → delete from pockets)."""
    from db.pockets import create_pocket, pocket_deposit_from_account
    from db.investments import create_investment_db, investment_deposit_from_account

    add_launch_and_update_balance(user_id, "receita", 1000, None, "salario")
    add_launch_and_update_balance(user_id, "despesa", 200, "luz", "paguei 200 luz")
    create_pocket(user_id, "viagem")
    pocket_deposit_from_account(user_id, "viagem", 300, "guardando")
    create_investment_db(user_id, "CDB Teste", 1.0, "monthly")
    investment_deposit_from_account(user_id, "CDB Teste", 250)

    # só a despesa e a receita entram no conjunto "apagável"; a criação e os
    # aportes de caixinha/investimento ficam de fora.
    assert db.count_launches(user_id) == 2

    pocket_before = _pocket_balance(user_id, "viagem")
    inv_before = _investment_balance(user_id, "CDB Teste")
    assert pocket_before == 300.0
    assert inv_before is not None and inv_before >= 250.0

    result = db.delete_all_launches_and_rollback(user_id)
    assert result == {"deleted": 2, "kept_no_effects": [], "kept_unsafe": [],
                      "errors": [], "remaining": 0}

    # o ponto do teste: caixinha e investimento INTACTOS (registro + saldo).
    assert _pocket_balance(user_id, "viagem") == pocket_before, "caixinha não pode ser tocada"
    assert _investment_balance(user_id, "CDB Teste") == inv_before, "investimento não pode ser tocado"
    # as despesas/receitas sumiram.
    assert db.count_launches(user_id) == 0


def _set_efeitos_null(user_id: int, launch_id: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update launches set efeitos = null where id=%s and user_id=%s",
                (launch_id, user_id),
            )
        conn.commit()


def test_falha_prevista_e_erro_tecnico_sao_distinguiveis(user_id, monkeypatch, caplog):
    """Antes, `failed += 1` sem log: banco caído e lançamento legado davam o
    MESMO número e a MESMA frase. Agora são duas listas e o erro técnico tem
    log — com user_id e launch_id, e sem dado sensível."""
    import logging
    from db import accounts as accounts_mod

    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    legado, seq_legado, _ = add_launch_and_update_balance(
        user_id, "despesa", 300, "aluguel", "paguei 300 aluguel"
    )
    quebrado, seq_quebrado, _ = add_launch_and_update_balance(
        user_id, "despesa", 77, "mercadinho segredo", "gastei 77 no mercadinho segredo"
    )
    _set_efeitos_null(user_id, legado)

    real = accounts_mod.delete_launch_and_rollback

    def falha_num_id(uid, lid, **kw):
        if lid == quebrado:
            raise RuntimeError("boom")
        return real(uid, lid, **kw)

    monkeypatch.setattr(accounts_mod, "delete_launch_and_rollback", falha_num_id)

    with caplog.at_level(logging.ERROR, logger="db.accounts"):
        result = db.delete_all_launches_and_rollback(user_id)

    assert result["deleted"] == 1, result           # só a receita seed passou
    assert result["kept_no_effects"] == [seq_legado], result
    assert result["errors"] == [seq_quebrado], result
    assert result["remaining"] == 2, "recontagem tem de ver os dois que ficaram"

    linhas = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("delete_all_launches:")]
    assert len(linhas) == 1, linhas
    # Igualdade EXATA é a assertiva de não-vazamento: qualquer valor,
    # descrição, categoria ou alvo que entrasse no log quebraria aqui.
    # (Comparar por substring seria flaky: o user_id é um número aleatório de
    # 10 dígitos e pode conter "77" ou "300" por acaso.)
    assert linhas[0] == (
        f"delete_all_launches: falha inesperada user_id={user_id} "
        f"launch_id={quebrado} user_seq={seq_quebrado} causa=RuntimeError sqlstate=None"
    ), linhas[0]
    assert "boom" not in linhas[0], "str(e) não pode entrar no log"


def test_mensagem_nao_finge_sucesso_quando_sobrou(user_id, monkeypatch):
    """A tool não pode dizer 'não havia nada' nem omitir o que ficou."""
    from core.services.ai_chat.tools import launches as tool_mod

    monkeypatch.setattr(tool_mod.db, "delete_all_launches_and_rollback", lambda uid: {
        "deleted": 0, "kept_no_effects": [3], "errors": [7, 8], "remaining": 3,
    })
    msg = get_tool("delete_all_launches").execute(user_id, {})
    assert "não havia nenhum" not in msg.lower(), msg
    assert "#3" in msg and "#7" in msg and "#8" in msg, msg
    assert "erro técnico" in msg.lower(), "erro técnico tem frase própria"
    assert "antigo" in msg.lower(), "lançamento antigo tem frase própria"
    # remaining CONCORDA com 1 antigo + 2 erros: repetir "sobrou 3" seria dizer
    # duas vezes a mesma coisa.
    assert "conferência" not in msg.lower(), f"cauda redundante voltou: {msg!r}"

    # amostra de 5 + "e mais N"
    monkeypatch.setattr(tool_mod.db, "delete_all_launches_and_rollback", lambda uid: {
        "deleted": 2, "kept_no_effects": [1, 2, 3, 4, 5, 6, 7], "errors": [], "remaining": 7,
    })
    msg = get_tool("delete_all_launches").execute(user_id, {})
    assert "#1, #2, #3, #4, #5 e mais 2" in msg, msg
    assert "#6" not in msg and "#7 " not in msg, msg


def test_amostra_ids_produz_os_exemplos_da_propria_docstring():
    """A docstring de `_amostra_ids` já mentiu uma vez ('#3, #7, #9 e mais 4' —
    não produzível: com 3 ids `resto` dá -2 e a cauda nem aparece). Os dois
    exemplos que ela cita agora ficam presos aqui."""
    from core.services.ai_chat.tools.launches import _amostra_ids

    assert _amostra_ids([3, 7, 9]) == "#3, #7, #9"
    assert _amostra_ids([3, 7, 9, 11, 12, 15, 20]) == "#3, #7, #9, #11, #12 e mais 2"


def test_mensagem_vazio_continua_dizendo_que_nao_havia_nada(user_id, monkeypatch):
    """Controle positivo: o caso legítimo 'histórico já vazio' não regrediu."""
    from core.services.ai_chat.tools import launches as tool_mod

    monkeypatch.setattr(tool_mod.db, "delete_all_launches_and_rollback", lambda uid: {
        "deleted": 0, "kept_no_effects": [], "errors": [], "remaining": 0,
    })
    msg = get_tool("delete_all_launches").execute(user_id, {})
    assert "não havia nenhum lançamento" in msg.lower(), msg


def test_recontagem_que_falha_nao_apaga_o_trabalho_feito(user_id, monkeypatch, caplog):
    """O recount roda DEPOIS do loop destrutivo. Se ele estourar (blip de
    conexão, timeout de pool) e a exceção subir, o usuário lê "não consegui
    apagar" com tudo já apagado — a mentira oposta à que a mensagem existe pra
    matar. `remaining` é conferência: falhou, vem None ("não conferi"), e os
    fatos medidos continuam sendo relatados."""
    import logging
    from db import accounts as accounts_mod

    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    add_launch_and_update_balance(user_id, "despesa", 300, "aluguel", "paguei 300 aluguel")
    assert _bal(user_id) == 700.0

    def sem_banco(uid):
        raise ConnectionError("connection to server was lost")

    monkeypatch.setattr(accounts_mod, "count_launches", sem_banco)

    with caplog.at_level(logging.ERROR, logger="db.accounts"):
        msg = get_tool("delete_all_launches").execute(user_id, {})

    assert "apaguei 2" in msg.lower(), f"trabalho feito sumiu do relato: {msg!r}"
    assert "não consegui apagar" not in msg.lower(), msg
    assert "conferência" not in msg.lower(), "sem recontagem não há discordância a relatar"
    assert _bal(user_id) == 0.0
    assert any("recontagem falhou" in r.getMessage() for r in caplog.records), \
        "a falha da conferência tem de ir pro log"
    assert not any("connection to server was lost" in r.getMessage()
                   for r in caplog.records), "str(e) não pode entrar no log"


def test_conferencia_fala_so_quando_discorda(user_id, monkeypatch):
    """O caso que a recontagem existe pra pegar: o delete casou zero linhas e
    ninguém levantou — `remaining` maior que as listas explicam."""
    from core.services.ai_chat.tools import launches as tool_mod

    monkeypatch.setattr(tool_mod.db, "delete_all_launches_and_rollback", lambda uid: {
        "deleted": 4, "kept_no_effects": [], "errors": [], "remaining": 4,
    })
    msg = get_tool("delete_all_launches").execute(user_id, {})
    assert "conferência não bateu" in msg.lower(), f"discordância silenciada: {msg!r}"
    assert "ainda tem 4" in msg.lower(), msg

    # E não fala quando bate (mesmo resultado, remaining coerente).
    monkeypatch.setattr(tool_mod.db, "delete_all_launches_and_rollback", lambda uid: {
        "deleted": 4, "kept_no_effects": [], "errors": [], "remaining": 0,
    })
    assert "conferência" not in get_tool("delete_all_launches").execute(user_id, {}).lower()


def test_conferencia_conta_o_balde_kept_unsafe(user_id, monkeypatch):
    """`kept_unsafe` é o balde NOVO, e o `esperado` da conferência tem de somá-lo.

    Todos os outros mocks deste arquivo omitem `kept_unsafe`, então o
    `+ len(inseguros)` podia sumir de `tools/launches.py` com a suíte inteira
    verde — e o usuário lia as duas frases se contradizendo: "mantive 1 que não
    consigo reverter" seguido de "esperava 0". Controle negativo (medido):
    removendo `+ len(inseguros)` do `esperado`, ESTE teste fica vermelho."""
    from core.services.ai_chat.tools import launches as tool_mod

    monkeypatch.setattr(tool_mod.db, "delete_all_launches_and_rollback", lambda uid: {
        "deleted": 2, "kept_no_effects": [], "kept_unsafe": [9], "errors": [],
        "remaining": 1,
    })
    msg = get_tool("delete_all_launches").execute(user_id, {})
    assert "não consigo reverter com segurança: #9" in msg, msg
    assert "conferência" not in msg.lower(), (
        "o único mantido JÁ foi explicado pela frase do kept_unsafe; a "
        f"conferência dizendo 'esperava 0' contradiz a linha de cima: {msg!r}"
    )

    # Controle positivo do outro lado: com `remaining` MAIOR que os baldes
    # somados, a discordância continua sendo relatada.
    monkeypatch.setattr(tool_mod.db, "delete_all_launches_and_rollback", lambda uid: {
        "deleted": 2, "kept_no_effects": [], "kept_unsafe": [9], "errors": [],
        "remaining": 3,
    })
    msg = get_tool("delete_all_launches").execute(user_id, {})
    assert "conferência não bateu" in msg.lower(), msg
    assert "esperava 1" in msg.lower(), msg


# ─── Guarda 1 (segurança) e guarda 2 (escopo) do "apagar tudo" ──────────────
#
# Duas guardas DIFERENTES, em lugares diferentes:
#   1. `delete_launch_and_rollback` recusa `efeitos` que não sabe reverter por
#      inteiro — vale pra TODO chamador, porque todos passam por essa função. O
#      inventário dos oito (e os três do Open Finance, onde a recusa vira
#      silêncio) está na docstring dela, em `db/accounts.py`;
#   2. `escopo_conta_corrente=True` (só o "apagar tudo") recusa o que mexe em
#      caixinha/investimento, porque a mensagem promete que eles não são
#      afetados.

def _set_tipo(user_id: int, launch_id: int, tipo: str):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update launches set tipo=%s where id=%s and user_id=%s",
                        (tipo, launch_id, user_id))
        conn.commit()


def _user_seq(user_id: int, launch_id: int) -> int:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select user_seq, id from launches where id=%s and user_id=%s",
                        (launch_id, user_id))
            row = cur.fetchone()
            return row["user_seq"] or row["id"]


def _bill_status(bill_id: int) -> str:
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select status from credit_bills where id=%s", (bill_id,))
            return cur.fetchone()["status"]


def _set_efeitos(user_id: int, launch_id: int, jsonb: str):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update launches set efeitos = '{jsonb}'::jsonb where id=%s and user_id=%s",
                (launch_id, user_id),
            )
        conn.commit()


def test_efeitos_vazio_nao_e_apagado_e_o_dinheiro_fica(user_id: int, caplog):
    """O buraco medido: `efeitos = '{}'::jsonb` NÃO é `null`, então não caía em
    `kept_no_effects`; lá dentro `.get("delta_conta", 0)` dava 0, a linha era
    apagada e os R$ 300 NÃO voltavam pro saldo. O discriminador é PRESENÇA de
    chave, não valor — escritores legítimos gravam `delta_conta: 0`
    (open_finance, criar_caixinha).

    Controle negativo MEDIDO: tirando a checagem `"delta_conta" not in efeitos`
    de `db/accounts.py`, sai
    `extrato=[] saldo=-300.0` — a linha some E o dinheiro não volta, que é o
    bug inteiro numa assertiva só."""
    import logging

    lid, seq, _ = add_launch_and_update_balance(user_id, "despesa", 300, "aluguel", "paguei 300")
    _set_efeitos(user_id, lid, "{}")
    assert _bal(user_id) == -300.0

    with caplog.at_level(logging.WARNING, logger="db.accounts"):
        result = db.delete_all_launches_and_rollback(user_id)

    # As duas metades do bug, juntas: se a linha sumiu, os R$ 300 tinham de ter
    # voltado. Com `delta_conta` ausente não voltam — some a linha E some o
    # dinheiro, sem nada no extrato explicando o saldo.
    restantes = [int(r["id"]) for r in db.list_launches(user_id, limit=10)]
    assert (restantes, _bal(user_id)) == ([lid], -300.0), \
        f"extrato={restantes} saldo={_bal(user_id)}"
    assert result["kept_unsafe"] == [seq], result
    assert result["kept_no_effects"] == [], "sem `efeitos` e `efeitos` degenerado são frases diferentes"
    assert result["deleted"] == 0, result

    # Igualdade EXATA, mesmo motivo do `falha inesperada` acima: o WARNING do
    # `kept_unsafe` não pode carregar `str(e)`. Hoje as mensagens das exceções
    # são texto nosso, mas nada prende essa invariante — um `raise
    # LaunchUnsafeRollback(f"... {row[...]}")` amanhã persistiria dado do
    # cliente em `system_event_logs`, e a guarda por `ast` só olha
    # `com_traceback`. O `motivo=` entra na MESMA igualdade exata: ele é
    # código enumerado (vem do `raise`), então prendê-lo aqui não afrouxa a
    # regra — e prende a discriminação, que era o ponto de tê-lo.
    linhas = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("delete_all_launches:")]
    assert linhas == [
        f"delete_all_launches: mantido sem reverter user_id={user_id} "
        f"launch_id={lid} user_seq={seq} causa=LaunchUnsafeRollback "
        f"motivo=sem_delta_conta"
    ], linhas


def test_efeitos_jsonb_escalar_e_tratado_como_sem_efeitos(user_id: int):
    """`jsonb` aceita escalar e lista, não só objeto. Por isso a checagem é
    `not isinstance(efeitos, dict)` e não `efeitos is None`: com `efeitos is
    None`, um `42` passava da guarda e explodia no `"delta_conta" not in 42`
    (`TypeError`), caindo em `errors` — ou seja, o usuário lia "deu erro
    técnico, tenta de novo em alguns minutos" (conselho falso: a condição é
    PERMANENTE, sempre vai falhar) e cada tentativa gravava um ERROR, inflando
    o `backend_errors_24h` do /admin. Classificação, não dinheiro: a linha fica
    de pé nos dois casos.

    Controle negativo MEDIDO: voltando a guarda para `efeitos is None`, este
    teste fica vermelho com `kept_no_effects=[] errors=[<seq>]`."""
    lid, seq, _ = add_launch_and_update_balance(user_id, "despesa", 300, "aluguel", "paguei 300")
    _set_efeitos(user_id, lid, "42")

    result = db.delete_all_launches_and_rollback(user_id)

    assert result["kept_no_effects"] == [seq], \
        f"jsonb escalar não é erro técnico: kept_no_effects={result['kept_no_effects']} errors={result['errors']}"
    assert result["errors"] == [], result
    assert result["deleted"] == 0, result
    assert [int(r["id"]) for r in db.list_launches(user_id, limit=10)] == [lid]
    assert _bal(user_id) == -300.0, "recusar não pode mexer no saldo"


def test_apagar_tudo_nao_toca_caixinha_com_tipo_reescrito(user_id: int):
    """A proteção de hoje é INDIRETA (`tipo in ('despesa','receita')`), e um
    `tipo` reescrito a fura: o launch de `criar_caixinha` entra no conjunto
    apagável e `efeitos.create_pocket` DELETA a caixinha.

    Controle negativo medido: sem a guarda de escopo
    (`_EFEITOS_FORA_DO_APAGAR_TUDO`), a caixinha some — `_pocket_balance` volta
    None."""
    from db.pockets import create_pocket

    lid, _pid, _nome = create_pocket(user_id, "viagem")
    # `despesa` (e não `saida`): é o valor que faz a linha ENTRAR no conjunto
    # apagável — com um tipo de fora do filtro o teste passaria sem medir nada.
    _set_tipo(user_id, lid, "despesa")
    assert _pocket_balance(user_id, "viagem") == 0.0

    result = db.delete_all_launches_and_rollback(user_id)

    assert _pocket_balance(user_id, "viagem") is not None, "a caixinha foi apagada pelo 'apagar tudo'"
    seq = _user_seq(user_id, lid)
    assert result["kept_unsafe"] == [seq], result
    assert result["deleted"] == 0, result


def test_apagar_tudo_apaga_o_normal_e_reabre_a_fatura(user_id: int):
    """Controle POSITIVO do grupo: as guardas não podem recusar tudo — isso
    seria pior que o bug. Três lançamentos comuns + um pagamento de fatura
    (que carrega `bill_id`/`paid_amount_added`) somem, o saldo zera e a fatura
    volta a 'open'."""
    from datetime import date as _date

    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    _tx, _due, bill_id = db.add_credit_purchase(
        user_id, card_id, 100, "outros", "compra", _date.today(),
    )
    add_launch_and_update_balance(user_id, "receita", 1000, None, "salario")
    add_launch_and_update_balance(user_id, "despesa", 50, "mercado", "gastei 50")
    add_launch_and_update_balance(user_id, "despesa", 20, "cafe", "gastei 20")
    db.pay_bill_amount(user_id, card_id, "Nubank", 100.0)
    assert _bal(user_id) == 830.0
    assert _bill_status(bill_id) == "paid"

    result = db.delete_all_launches_and_rollback(user_id)

    assert result == {"deleted": 4, "kept_no_effects": [], "kept_unsafe": [],
                      "errors": [], "remaining": 0}, result
    assert _bal(user_id) == 0.0
    assert _bill_status(bill_id) == "open", "apagar o pagamento tem de reabrir a fatura"


def test_deposito_de_caixinha_recusa_no_delete_singular(user_id: int):
    """`pocket_lot_create` fica FORA da allowlist de propósito: o depósito
    reverte `pockets.balance`, mas a linha em `pocket_lots` ficava — e o
    `_sync_pocket_from_lots` recalcula `balance = sum(pocket_lots.balance)` no
    próximo depósito/saque, RESSUSCITANDO o dinheiro desfeito. Enquanto isso,
    a porta recusa (mudança de comportamento visível e desejada).

    Controle negativo medido: com `pocket_lot_create` na allowlist, não levanta
    e a linha é apagada."""
    from db.pockets import create_pocket, pocket_deposit_from_account

    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_pocket(user_id, "viagem")
    pocket_deposit_from_account(user_id, "viagem", 300, "guardando")
    dep_id = int(db.list_launches(user_id, limit=1)[0]["id"])
    assert _pocket_balance(user_id, "viagem") == 300.0

    import pytest
    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, dep_id)
    # o par do `motivo=sem_delta_conta` prendido no log: sem uma SEGUNDA recusa
    # com código diferente, um `motivo` constante passaria lá.
    assert exc.value.motivo == "chave_desconhecida"

    assert _pocket_balance(user_id, "viagem") == 300.0, "recusa não pode mexer no saldo"
    assert _bal(user_id) == 700.0
    assert [int(r["id"]) for r in db.list_launches(user_id, limit=10) if int(r["id"]) == dep_id] == [dep_id]
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from pocket_lots where user_id=%s", (user_id,))
            assert int(cur.fetchone()["n"]) == 1, "o lote tem de continuar intacto"


# ─── Shape LEGADO de lote: tem lote no banco, não tem a chave que o nomeia ───
#
# A guarda de allowlist chaveia no que a linha REGISTRA. Linha gravada antes de
# `79bd52f` (16/05/2026) não tem `pocket_lot_create`/`investment_lot_create`,
# mas TEM lote — criado pelo backfill preguiçoso (`_ensure_pocket_lots`,
# `db/pockets.py`; `_ensure_investment_lots`, `db/investments.py`) no primeiro
# movimento posterior. Sonda na `main` e no HEAD anterior a este conserto:
#
#   antes    : pocket= 300.0  lotes= (1, 300.0)
#   delete   : APAGOU              ← nenhuma guarda pegava o shape legado
#   depois   : pocket=   0.0  lotes= (1, 300.0)
#   apos +10 : pocket= 310.0  lotes= (2, 310.0)     ← 10 seria o certo
#
# A matriz de tipos do Tester montou tudo pelos escritores ATUAIS, que sempre
# gravam a chave do lote: era estruturalmente cega a esta forma.

def _drop_efeito(user_id: int, launch_id: int, key: str):
    """Deixa a linha no shape LEGADO: apaga só a chave do lote do jsonb, sem
    tocar no lote em si (que é exatamente o que o backfill teria deixado)."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update launches set efeitos = efeitos - %s where id=%s and user_id=%s",
                (key, launch_id, user_id),
            )
        conn.commit()


def _lotes(user_id: int, tabela: str):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"select count(*) as n, coalesce(sum(balance),0) as s "
                f"from {tabela} where user_id=%s",
                (user_id,),
            )
            r = cur.fetchone()
            return int(r["n"]), float(r["s"])


def test_deposito_caixinha_legado_sem_chave_de_lote_recusa(user_id: int):
    """Controle negativo medido: sem `_DELTA_EXIGE_LOTE` em `db/accounts.py`,
    `DID NOT RAISE <class 'db.errors.LaunchUnsafeRollback'>` e a sonda acima se
    repete (pocket 300 → 0 com o lote de 300 de pé)."""
    import pytest
    from db.pockets import create_pocket, pocket_deposit_from_account

    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_pocket(user_id, "viagem")
    pocket_deposit_from_account(user_id, "viagem", 300, "guardando")
    dep_id = int(db.list_launches(user_id, limit=1)[0]["id"])
    _drop_efeito(user_id, dep_id, "pocket_lot_create")
    assert _pocket_balance(user_id, "viagem") == 300.0
    assert _lotes(user_id, "pocket_lots") == (1, 300.0)

    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, dep_id)
    # a recusa COMUM (lote legado): é dela que o `motivo` tem de separar a rara.
    assert exc.value.motivo == "lote_ausente"

    assert _pocket_balance(user_id, "viagem") == 300.0, "recusa não pode mexer no saldo"
    assert _lotes(user_id, "pocket_lots") == (1, 300.0), "o lote tem de continuar intacto"
    assert _bal(user_id) == 700.0
    assert any(int(r["id"]) == dep_id for r in db.list_launches(user_id, limit=10))


def test_aporte_investimento_legado_sem_chave_de_lote_recusa(user_id: int):
    """O irmão que ninguém tinha olhado: `investment_lot_create` ESTÁ na
    allowlist e É revertido, mas o aporte legado sem a chave tem o buraco
    idêntico. Controle negativo medido: sem a guarda, `DID NOT RAISE` e a linha
    fica `invest= 0.0` com o lote de 300 de pé."""
    import pytest
    from db.investments import create_investment, investment_deposit_from_account

    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_investment(user_id, "cdb", 0.01, "monthly")
    investment_deposit_from_account(user_id, "cdb", 300, "aportando")
    ap_id = int(db.list_launches(user_id, limit=1)[0]["id"])
    _drop_efeito(user_id, ap_id, "investment_lot_create")
    assert _investment_balance(user_id, "cdb") == 300.0
    assert _lotes(user_id, "investment_lots") == (1, 300.0)

    with pytest.raises(db.LaunchUnsafeRollback):
        db.delete_launch_and_rollback(user_id, ap_id)

    assert _investment_balance(user_id, "cdb") == 300.0, "recusa não pode mexer no saldo"
    assert _lotes(user_id, "investment_lots") == (1, 300.0), "o lote tem de continuar intacto"
    assert _bal(user_id) == 700.0


def test_shape_legado_recusa_com_frase_de_produto_nas_duas_portas(user_id: int):
    """A recusa tem de chegar ao usuário como frase de produto, não como "erro
    do meu lado" (que promete retry pra condição PERMANENTE) nem como 500.

    Controle negativo medido: sem `_DELTA_EXIGE_LOTE`, as duas portas devolvem
    "apagado"/"Saldo revertido" (a linha some e o lote fica), e o assert de
    "reverter ... com segurança" falha nas duas."""
    from core.handlers.pending import resolve_delete
    from db.pockets import create_pocket, pocket_deposit_from_account

    add_launch_and_update_balance(user_id, "receita", 2000, None, "seed")
    create_pocket(user_id, "viagem")

    # porta 1 — WhatsApp
    pocket_deposit_from_account(user_id, "viagem", 300, "guardando")
    dep1 = int(db.list_launches(user_id, limit=1)[0]["id"])
    _drop_efeito(user_id, dep1, "pocket_lot_create")
    db.set_pending_action(user_id, "delete_launch",
                          {"launch_id": dep1, "display_id": _user_seq(user_id, dep1)})
    resp = resolve_delete(user_id, confirmed=True)
    assert "reverter" in resp.lower() and "segurança" in resp.lower(), resp
    assert "erro do meu lado" not in resp.lower(), resp
    assert "apagado" not in resp.lower(), resp

    # porta 2 — /ai/chat
    pocket_deposit_from_account(user_id, "viagem", 400, "guardando de novo")
    dep2 = int(db.list_launches(user_id, limit=1)[0]["id"])
    _drop_efeito(user_id, dep2, "pocket_lot_create")
    msg = get_tool("delete_launch").execute(
        user_id, {"launch_id": str(_user_seq(user_id, dep2))})
    assert "reverter" in msg.lower() and "segurança" in msg.lower(), msg
    assert "tenta de novo" not in msg.lower(), msg

    assert _pocket_balance(user_id, "viagem") == 700.0, "nenhuma porta pode ter mexido"
    assert _lotes(user_id, "pocket_lots") == (2, 700.0)


def test_ciclo_de_vida_de_caixinha_e_investimento_continua_apagavel(user_id: int):
    """Controle POSITIVO da recusa nova: ela é só pra linha que mexe em saldo de
    LOTE. `criar_caixinha`, `create_investment` (que grava `delta_invest` com
    delta 0.0, `db/investments.py`) e `delete_investment` não têm lote nenhum e
    continuam apagáveis pelo delete singular. Sem este caso, uma guarda que
    recusasse `delta_invest` por PRESENÇA passaria na suíte inteira."""
    from db.pockets import create_pocket
    from db.investments import create_investment, delete_investment

    create_pocket(user_id, "viagem")
    cx_id = int(db.list_launches(user_id, limit=1)[0]["id"])
    db.delete_launch_and_rollback(user_id, cx_id)
    assert _pocket_balance(user_id, "viagem") is None, "criar_caixinha tem de desfazer"

    create_investment(user_id, "cdb", 0.01, "monthly")
    inv_id = int(db.list_launches(user_id, limit=1)[0]["id"])
    db.delete_launch_and_rollback(user_id, inv_id)
    assert _investment_balance(user_id, "cdb") is None, "create_investment tem de desfazer"

    create_investment(user_id, "tesouro", 0.01, "monthly")
    delete_investment(user_id, "tesouro")
    del_id = int(db.list_launches(user_id, limit=1)[0]["id"])
    db.delete_launch_and_rollback(user_id, del_id)
    assert _investment_balance(user_id, "tesouro") == 0.0, "delete_investment tem de recriar"


def test_delta_de_lote_zero_com_delta_conta_nao_zero_recusa(user_id: int):
    """Escape do `delta == 0`: ele existe pro `create_investment`, que grava
    `delta_invest` com delta 0.0 num investimento SEM lote. Olhando só o
    `delta`, uma linha que tira dinheiro da CONTA e põe num lote que a reversão
    não desfaz passava — o `delta_conta` voltava pro saldo e o lote ficava.

    Forjado (nenhum escritor de hoje produz este shape; o único de delta 0
    grava `delta_conta: 0.0`):

        efeitos {"delta_conta": -300.0, "delta_pocket": {"nome":…,"delta":0.0}}
        ANTES : conta 99700.0 pocket 300.0  TOTAL 100000.0
        delete: APAGOU
        DEPOIS: conta 100000.0 pocket 300.0 TOTAL 100300.0   ← criou 300

    Controle negativo: tirando `and delta_conta == 0` de `db/accounts.py`,
    `DID NOT RAISE LaunchUnsafeRollback` e o total sobe 300.
    O controle POSITIVO do escape já existe acima
    (`test_ciclo_de_vida_de_caixinha_e_investimento_continua_apagavel`).
    """
    import pytest
    from db.pockets import create_pocket, pocket_deposit_from_account

    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_pocket(user_id, "viagem")
    pocket_deposit_from_account(user_id, "viagem", 300, "guardando")
    dep_id = int(db.list_launches(user_id, limit=1)[0]["id"])
    _set_efeitos(
        user_id, dep_id,
        '{"delta_conta": -300.0, "delta_pocket": {"nome": "viagem", "delta": 0.0}}',
    )
    total_antes = _bal(user_id) + _pocket_balance(user_id, "viagem")

    with pytest.raises(db.LaunchUnsafeRollback):
        db.delete_launch_and_rollback(user_id, dep_id)

    assert _bal(user_id) == 700.0, "recusa não pode mexer na conta"
    assert _pocket_balance(user_id, "viagem") == 300.0
    assert _bal(user_id) + _pocket_balance(user_id, "viagem") == total_antes


# ── chave PRESENTE e OCA: o irmão do `lote_ausente` ──────────────────────────
#
# `_DELTA_EXIGE_LOTE` pega a chave AUSENTE (linha legada). Este grupo pega a
# chave PRESENTE sem o campo que a torna reversível — cada `if <campo>:` a
# jusante vira no-op e o `delete` acontece assim mesmo.
#
# Sonda REMEDIDA aqui, no `pigbank_ci_test`, com o `_EFEITOS_FORMA`
# esvaziado e o `efeitos` EXATO que cada caso abaixo forja (aporte de 300 real,
# `efeitos` reescrito depois). Estado = conta / inv / lotes:
#
#   A) investment_lot_create={"investment_id":1}
#        antes  700.00 / 300.00 / (1, 300.00)
#        APAGOU 1000.00 /  0.00 / (1, 300.00)   <- agregado revertido, lote DE PÉ;
#        o `_sync_*_from_lots` traz os 300 de volta no movimento seguinte.
#   C) investment_lot_withdrawals=[{}]
#        idem A: o `continue` de :1394 não restaura, e o lote fica de pé.
#   E) investment_lot_withdrawals=[{"lot_id": <lote real>}]
#        antes  700.00 / 300.00 / (1, 300.00)
#        APAGOU 1000.00 /  0.00 / (1,   0.00)   <- lote ZERADO pelo default do
#        `.get("balance", 0)` (:1404).
#
#   E na variante com resgate parcial (aporte 300 + resgate 100, `before`
#   ausente no resgate), que é onde o zero DESTRÓI dinheiro:
#        antes  800.00 / 200.00 / (1, 200.00)
#        APAGOU 700.00 /   0.00 / (1,   0.00)
#        certo seria 700.00 / 300.00 / (1, 300.00) — os 200 que estavam no lote
#        somem e os 100 do resgate não voltam. Não há sincronização que traga:
#        sumiu do lote E do agregado.
#
# E) não estava no apontamento do Codex e é o pior dos oito.
# `InvestmentLotHasWithdrawal` não pega — mora dentro do `if lot_id:` (:1363).
#
# Inalcançável pelos escritores de hoje (os cinco gravam os campos no mesmo
# insert atômico; nada reescreve `efeitos` depois). Fechado porque é a tese do
# PR e um dos oito destrói dinheiro.


def _aporte_forjado(user_id: int, efeitos_jsonb: str) -> int:
    """Aporte REAL (lote de verdade em `investment_lots`), com `efeitos`
    reescrito depois para a forma oca. Devolve o id do lançamento."""
    from db.investments import create_investment, investment_deposit_from_account

    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_investment(user_id, "cdb", 0.01, "monthly")
    investment_deposit_from_account(user_id, "cdb", 300, "aportando")
    lid = int(db.list_launches(user_id, limit=1)[0]["id"])
    _set_efeitos(user_id, lid, efeitos_jsonb)
    return lid


def _recusa_nao_mexe_em_nada(user_id: int, lid: int, conta: float, inv: float, lotes):
    assert _bal(user_id) == conta, "a recusa é ANTES de qualquer update"
    assert _investment_balance(user_id, "cdb") == inv, "saldo do investimento mexeu"
    assert _lotes(user_id, "investment_lots") == lotes, "o lote tem de ficar intacto"
    assert any(int(r["id"]) == lid for r in db.list_launches(user_id, limit=10)), \
        "o lançamento tem de continuar listado"


def test_A_lot_create_sem_lot_id_recusa(user_id: int):
    """Sem a guarda: APAGOU, conta 700→1000 e o lote de 300 de pé."""
    import pytest
    lid = _aporte_forjado(
        user_id, '{"delta_conta": -300.0, "delta_invest": {"nome": "cdb", "delta": 300.0}, '
                 '"investment_lot_create": {"investment_id": 1}}')
    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert exc.value.motivo == "efeito_incompleto"
    _recusa_nao_mexe_em_nada(user_id, lid, 700.0, 300.0, (1, 300.0))


def test_C_lot_withdrawals_sem_lot_id_recusa(user_id: int):
    """Sem a guarda: APAGOU e o lote não era restaurado."""
    import pytest
    lid = _aporte_forjado(
        user_id, '{"delta_conta": -300.0, "delta_invest": {"nome": "cdb", "delta": 300.0}, '
                 '"investment_lot_withdrawals": [{}]}')
    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert exc.value.motivo == "efeito_incompleto"
    _recusa_nao_mexe_em_nada(user_id, lid, 700.0, 300.0, (1, 300.0))


def test_E_lot_withdrawals_com_lot_id_e_sem_before_recusa(user_id: int):
    """O PIOR dos oito, e o que o Codex não apontou: `before` ausente faz o
    `.get("balance", 0)` da restauração do lote escrever ZERO nele. Sem a
    guarda: APAGOU e o lote foi ZERADO — dinheiro destruído, sem volta."""
    import pytest
    lid = _aporte_forjado(
        user_id, '{"delta_conta": -300.0, "delta_invest": {"nome": "cdb", "delta": 300.0}, '
                 '"investment_lot_withdrawals": [{"lot_id": 1}]}')
    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert exc.value.motivo == "efeito_incompleto"
    _recusa_nao_mexe_em_nada(user_id, lid, 700.0, 300.0, (1, 300.0))


def test_E2_before_presente_mas_oco_tambem_recusa(user_id: int):
    """`before` PRESENTE e sem `balance`/`principal_remaining` cai no mesmo
    default 0. Exigir só a chave `before` deixaria este caso aberto — é por
    isso que `_BEFORE_FORMA` existe."""
    import pytest
    lid = _aporte_forjado(
        user_id, '{"delta_conta": -300.0, "delta_invest": {"nome": "cdb", "delta": 300.0}, '
                 '"investment_lot_withdrawals": [{"lot_id": 1, "before": {"status": "open"}}]}')
    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert exc.value.motivo == "efeito_incompleto"
    _recusa_nao_mexe_em_nada(user_id, lid, 700.0, 300.0, (1, 300.0))


def test_F1_withdrawals_como_dict_recusa_em_vez_de_estourar(user_id: int):
    """FORMA do CONTAINER, não só do item. Sem a guarda o swap passava a
    validação (o dict vira `[valor]`, item bem formado) e estourava LÁ EMBAIXO,
    cru: `for effect in dict` itera as CHAVES → `AttributeError: 'str' object
    has no attribute 'get'`. Cai no `except Exception` (balde `errors`, não
    `kept_unsafe`) e em SILÊNCIO nos três `except Exception: pass` de
    `db/open_finance.py`."""
    import pytest
    lid = _aporte_forjado(
        user_id, '{"delta_conta": -300.0, "delta_invest": {"nome": "cdb", "delta": 300.0}, '
                 '"investment_lot_withdrawals": {"lot_id": 1, "before": '
                 '{"balance": 300.0, "principal_remaining": 300.0}}}')
    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert exc.value.motivo == "efeito_incompleto"
    _recusa_nao_mexe_em_nada(user_id, lid, 700.0, 300.0, (1, 300.0))


def test_F2_lot_create_como_lista_recusa_em_vez_de_estourar(user_id: int):
    """O swap contrário: `investment_lot_create` é lido com `.get` direto, então
    a lista estoura `AttributeError: 'list' object has no attribute 'get'` —
    mesmo balde `errors`, mesmo silêncio no OF."""
    import pytest
    lid = _aporte_forjado(
        user_id, '{"delta_conta": -300.0, "delta_invest": {"nome": "cdb", "delta": 300.0}, '
                 '"investment_lot_create": [{"lot_id": 1, "investment_id": 1}]}')
    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert exc.value.motivo == "efeito_incompleto"
    _recusa_nao_mexe_em_nada(user_id, lid, 700.0, 300.0, (1, 300.0))


def test_G_before_com_valor_nulo_recusa(user_id: int):
    """`{"balance": null}` é chave PRESENTE com valor nulo: passava pelo
    `c not in antes` e virava `Decimal(str(None))` → `InvalidOperation:
    [ConversionSyntax]` cru, mesmo balde `errors` do swap acima."""
    import pytest
    lid = _aporte_forjado(
        user_id, '{"delta_conta": -300.0, "delta_invest": {"nome": "cdb", "delta": 300.0}, '
                 '"investment_lot_withdrawals": [{"lot_id": 1, "before": '
                 '{"balance": null, "principal_remaining": 300.0}}]}')
    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert exc.value.motivo == "efeito_incompleto"
    _recusa_nao_mexe_em_nada(user_id, lid, 700.0, 300.0, (1, 300.0))


def test_site4_bill_id_sem_paid_amount_added_recusa(user_id: int):
    """Site 4 do inventário: `:1248` exige o PAR. Com um só, a reversão do
    pagamento é pulada e a fatura fica `paid` com o lançamento apagado."""
    import pytest
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    lid, _seq, _b = add_launch_and_update_balance(
        user_id, "despesa", 200, "fatura", "paguei fatura")
    _set_efeitos(user_id, lid, '{"delta_conta": -200.0, "bill_id": 1}')
    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert exc.value.motivo == "efeito_incompleto"
    assert _bal(user_id) == 800.0
    assert any(int(r["id"]) == lid for r in db.list_launches(user_id, limit=10))


def test_positivo_resgate_multi_lote_e_total_continua_apagavel(user_id: int):
    """CONTROLE POSITIVO das guardas novas, na forma que elas mais poderiam
    recusar por engano: `investment_lot_withdrawals` com VÁRIOS itens (resgate
    PEPS que atravessa dois lotes) e resgate TOTAL (os dois lotes fechados).
    Nenhum outro teste apagava um resgate multi-lote — a lista de um item só
    não discrimina container errado de container certo."""
    from db.investments import (create_investment, investment_deposit_from_account,
                                investment_withdraw_to_account)

    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_investment(user_id, "cdb", 0.01, "monthly")
    investment_deposit_from_account(user_id, "cdb", 300, "aporte 1")
    investment_deposit_from_account(user_id, "cdb", 400, "aporte 2")
    assert _lotes(user_id, "investment_lots") == (2, 700.0)

    investment_withdraw_to_account(user_id, "cdb", 700, "resgatando tudo")
    resgate = int(db.list_launches(user_id, limit=1)[0]["id"])
    assert _lotes(user_id, "investment_lots") == (2, 0.0), "resgate total fecha os dois"

    db.delete_launch_and_rollback(user_id, resgate)

    assert _lotes(user_id, "investment_lots") == (2, 700.0), \
        "os dois `before` da lista têm de restaurar os dois lotes"
    assert _investment_balance(user_id, "cdb") == 700.0


def test_positivo_aporte_e_resgate_integros_continuam_apagaveis(user_id: int):
    """CONTROLE POSITIVO, obrigatório: a guarda RESTRINGE, e todo falso
    positivo vira `kept_unsafe` — recusa visível em 5 portas e SILÊNCIO em 3
    (`db/open_finance.py`, dentro de `except Exception: pass`). Sem este caso o
    grupo passaria numa versão que recusa tudo, que é pior que o bug."""
    from db.investments import (create_investment, investment_deposit_from_account,
                                investment_withdraw_to_account)

    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_investment(user_id, "cdb", 0.01, "monthly")
    investment_deposit_from_account(user_id, "cdb", 300, "aportando")
    aporte = int(db.list_launches(user_id, limit=1)[0]["id"])

    # resgate íntegro: escreve `investment_lot_withdrawals` com lot_id + before
    investment_withdraw_to_account(user_id, "cdb", 100, "resgatando")
    resgate = int(db.list_launches(user_id, limit=1)[0]["id"])

    # as duas portas continuam apagando o que É reversível
    db.delete_launch_and_rollback(user_id, resgate)
    db.delete_launch_and_rollback(user_id, aporte)

    assert _lotes(user_id, "investment_lots") == (0, 0.0), \
        "aporte íntegro apagado tem de levar o lote junto"
    assert _investment_balance(user_id, "cdb") == 0.0
    assert _bal(user_id) == 1000.0, "o dinheiro tem de voltar inteiro pra conta"


# ═══════════════════════════════════════════════════════════════════════════
# A MATRIZ DE FORMA — `_EFEITOS_FORMA` (`db/accounts.py`)
#
# Os grupos acima nasceram um remendo por vez (chave oca, depois container,
# depois `before` nulo) e o revisor achou irmão nas duas rodadas. Aqui a matriz
# fecha por CLASSE, não célula a célula: um caso representativo por invariante
# × forma. Os quatro invariantes são `_dinheiro` (Decimal FINITO), `_id`
# (inteiro positivo), `_nome` (str não vazia) e `_data` (`date.fromisoformat`).
#
# NENHUMA destas formas é alcançável pelos escritores de hoje: `Json(efeitos)`
# serializa `NaN`/`Infinity` NUS e o jsonb os recusa no INSERT (medido), e os
# cinco escritores gravam os campos no mesmo insert atômico. O veneno entra como
# a STRING `"NaN"`, como número JSON fora do double (`1e400`) ou como linha
# mexida à mão — é blindagem fail-closed contra escritor futuro e importador de
# Open Finance, não conserto de incêndio.
import uuid

import pytest


_APORTE = '"delta_conta": -300.0, "delta_invest": {"nome": "cdb", "delta": 300.0}'


@pytest.mark.parametrize("caso,efeitos", [
    # ── dinheiro: NÃO-FINITO. É o pior da matriz — sem a guarda o `numeric` do
    # Postgres ACEITA `NaN`, o saldo vira `NaN` e não volta a ser número por
    # soma nenhuma: apaga E corrompe.
    ("dinheiro/delta_conta NaN",
     '{"delta_conta": "NaN", "delta_invest": {"nome": "cdb", "delta": 300.0}, '
     '"investment_lot_create": {"lot_id": LOTE, "investment_id": INVEST}}'),
    ("dinheiro/delta_conta 1e400 (inf fora do double)",
     '{"delta_conta": -1e400, "delta_invest": {"nome": "cdb", "delta": 300.0}, '
     '"investment_lot_create": {"lot_id": LOTE, "investment_id": INVEST}}'),
    ("dinheiro/paid_amount_added Infinity",
     '{"delta_conta": -200.0, "bill_id": 1, "paid_amount_added": "Infinity"}'),
    ("dinheiro/before.balance NaN",
     '{' + _APORTE + ', "investment_lot_withdrawals": [{"lot_id": LOTE, "before": '
     '{"balance": "NaN", "principal_remaining": 300.0}}]}'),
    # ── dinheiro: NÃO-NUMÉRICO. Hoje estoura CRU (`InvalidOperation`/
    # `TypeError`) — balde `errors` em vez de recusa, e SILÊNCIO nos três
    # `except Exception: pass` de `db/open_finance.py`.
    ("dinheiro/before.principal_remaining texto",
     '{' + _APORTE + ', "investment_lot_withdrawals": [{"lot_id": LOTE, "before": '
     '{"balance": 300.0, "principal_remaining": "abc"}}]}'),
    ("dinheiro/delta_invest.delta lista",
     '{"delta_conta": -300.0, "delta_invest": {"nome": "cdb", "delta": [300.0]}, '
     '"investment_lot_create": {"lot_id": LOTE, "investment_id": INVEST}}'),
    # `.get("balance", 0)` devolve o `null` GRAVADO, não o default 0 — por isso
    # `null` num campo opcional de dinheiro é recusado, e AUSENTE não é.
    ("dinheiro/delete_investment.balance null",
     '{"delta_conta": 0.0, "delete_investment": {"nome": "cdb", "balance": null}}'),
    ("dinheiro/delete_pocket.balance texto",
     '{"delta_conta": 0.0, "delete_pocket": {"nome": "viagem", "balance": "muito"}}'),
    # ── id: `lot_id` que não casa linha nenhuma. O delete do lote não remove
    # nada, o agregado é revertido assim mesmo e o lote fica DE PÉ.
    ("id/lot_id texto",
     '{' + _APORTE + ', "investment_lot_create": {"lot_id": "abc", "investment_id": INVEST}}'),
    # única célula da matriz que continua verde com `_id` desligado (medido):
    # o `rowcount == 0` é a SEGUNDA linha e pega `1.9` sozinho, porque o
    # Postgres compara `id = 1.9` sem casar nada. Quem discrimina `_id` no
    # não-integral é o `bill_id` logo abaixo, que não tem `rowcount` atrás.
    ("id/lot_id 1.9",
     '{' + _APORTE + ', "investment_lot_create": {"lot_id": 1.9, "investment_id": INVEST}}'),
    # `int(1.9)` == 1: sem `_id`, o update vai pra fatura ERRADA (ou nenhuma) e
    # o pagamento é apagado assim mesmo. Não há segunda linha aqui.
    ("id/bill_id 1.9", '{"delta_conta": -200.0, "bill_id": 1.9, "paid_amount_added": 200.0}'),
    ("id/lot_id lista",
     '{' + _APORTE + ', "investment_lot_withdrawals": [{"lot_id": [1], "before": '
     '{"balance": 300.0, "principal_remaining": 300.0}}]}'),
    ("id/investment_id false",
     '{' + _APORTE + ', "investment_lot_create": {"lot_id": LOTE, "investment_id": false}}'),
    # ── id OCO: `bill_id` falsy passava pelo par (os dois não-nulos) e o
    # `if paid_bill_id` pulava a reversão — fatura `paid` com o pagamento apagado.
    ("id/bill_id 0", '{"delta_conta": -200.0, "bill_id": 0, "paid_amount_added": 200.0}'),
    ("id/bill_id false", '{"delta_conta": -200.0, "bill_id": false, "paid_amount_added": 200.0}'),
    # `"7"` NÃO entra aqui: o Postgres coage a string de dígitos e o
    # `int(paid_bill_id)` também — é reversível, e virou controle POSITIVO
    # (`test_positivo_bill_id_como_string_e_float_continua_revertendo`). O que
    # não reverte é o texto que não é número.
    ("id/bill_id texto", '{"delta_conta": -200.0, "bill_id": "sete", "paid_amount_added": 200.0}'),
    ("id/bill_id dígito não-ASCII",
     '{"delta_conta": -200.0, "bill_id": "١٢٣", "paid_amount_added": 200.0}'),
    # ── nome: o `lower(%s)` não casa e a caixinha/o investimento não é
    # deletado nem recriado, com o lançamento apagado.
    ("nome/delta_invest.nome lista",
     '{"delta_conta": -300.0, "delta_invest": {"nome": ["cdb"], "delta": 300.0}, '
     '"investment_lot_create": {"lot_id": LOTE, "investment_id": INVEST}}'),
    ("nome/create_pocket.nome número",
     '{"delta_conta": 0.0, "create_pocket": {"nome": 42}}'),
    ("nome/delete_pocket.nome vazio",
     '{"delta_conta": 0.0, "delete_pocket": {"nome": "   "}}'),
    # ── data: `date.fromisoformat` cru no meio da reversão, ou tipo que a
    # coluna `date` recusa.
    ("data/last_date número",
     '{"delta_conta": 0.0, "delete_investment": {"nome": "cdb", "last_date": 42}}'),
    ("data/maturity_date não-ISO",
     '{"delta_conta": 0.0, "delete_investment": {"nome": "cdb", "maturity_date": "ontem"}}'),
    ("data/before.closed_at número",
     '{' + _APORTE + ', "investment_lot_withdrawals": [{"lot_id": LOTE, "before": '
     '{"balance": 300.0, "principal_remaining": 300.0, "closed_at": 123}}]}'),
    # ── texto: só o TRUTHY não-string quebra (o falsy vira "open" no `or`).
    ("texto/before.status número",
     '{' + _APORTE + ', "investment_lot_withdrawals": [{"lot_id": LOTE, "before": '
     '{"balance": 300.0, "principal_remaining": 300.0, "status": 42}}]}'),
    ("texto/before.status lista",
     '{' + _APORTE + ', "investment_lot_withdrawals": [{"lot_id": LOTE, "before": '
     '{"balance": 300.0, "principal_remaining": 300.0, "status": ["open"]}}]}'),
    # ── container trocado: `AttributeError` cru lá embaixo, balde `errors`.
    ("container/delta_invest como lista",
     '{"delta_conta": -300.0, "delta_invest": [{"nome": "cdb", "delta": 300.0}], '
     '"investment_lot_create": {"lot_id": LOTE, "investment_id": INVEST}}'),
    ("container/create_pocket como lista",
     '{"delta_conta": 0.0, "create_pocket": [{"nome": "viagem"}]}'),
    # o único caso que a checagem de CONTAINER pega sozinha: escalar não
    # iterável na chave que é lida como lista -> `TypeError: 'int' object is not
    # iterable`, cru. Com dict/lista/string o `isinstance(item, dict)` de dentro
    # do laço já pegaria.
    ("container/withdrawals como número",
     '{' + _APORTE + ', "investment_lot_withdrawals": 42}'),
    ("container/before como lista",
     '{' + _APORTE + ', "investment_lot_withdrawals": [{"lot_id": LOTE, '
     '"before": [{"balance": 300.0, "principal_remaining": 300.0}]}]}'),
])
def test_forma_do_efeito_recusa_em_vez_de_apagar(user_id: int, caso: str, efeitos: str):
    """Um caso por classe da matriz. O estado é sempre o mesmo aporte real
    (conta 700 / investimento 300 / 1 lote de 300) com o `efeitos` reescrito
    depois, então a recusa tem de deixar TUDO como estava.

    `LOTE`/`INVEST` viram os ids REAIS do aporte de propósito. Com `"lot_id": 1`
    fixo, cinco células passavam com o predicado que elas NOMEIAM desligado: o
    id 1 não casa lote do usuário, e o `rowcount == 0` recusava com o MESMO
    `motivo="efeito_incompleto"` que o teste espera — verde por construção
    (`CLAUDE.md` §3). Com o id real, o vermelho só pode vir do predicado."""
    lid = _aporte_forjado(user_id, '{"delta_conta": 0.0}')
    lot_id, inv_id = _ids_do_aporte(user_id)
    _set_efeitos(user_id, lid,
                 efeitos.replace("LOTE", str(lot_id)).replace("INVEST", str(inv_id)))
    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert exc.value.motivo == "efeito_incompleto", caso
    _recusa_nao_mexe_em_nada(user_id, lid, 700.0, 300.0, (1, 300.0))


def test_delta_conta_nulo_e_efeitos_degenerado(user_id: int):
    """`{"delta_conta": null}` não é `{}`: a chave está lá. A checagem era de
    PRESENÇA, então isto passava e `Decimal(str(None))` estourava
    `InvalidOperation` cru — balde `errors`, não recusa. Motivo `sem_delta_conta`
    (e não `efeito_incompleto`): o que falta é quanto reverter na conta."""
    lid = _aporte_forjado(user_id, '{"delta_conta": null}')
    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert exc.value.motivo == "sem_delta_conta"
    _recusa_nao_mexe_em_nada(user_id, lid, 700.0, 300.0, (1, 300.0))


# ── CONTROLES POSITIVOS da matriz ────────────────────────────────────────────
# A guarda RESTRINGE, e todo falso positivo vira `kept_unsafe`: recusa visível
# em 5 portas e SILÊNCIO em 3. Estes são os valores que um predicado escrito
# com `if not valor:` (em vez de `is None`) mataria.

def _ids_do_aporte(user_id: int) -> tuple[int, int]:
    """(lot_id, investment_id) REAIS do aporte — o id do lote vem da sequence
    global, então forjar `"lot_id": 1` não serve pro caminho que TEM de passar."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select min(id) as i from investment_lots where user_id=%s", (user_id,))
            lot = cur.fetchone()["i"]
            cur.execute("select min(id) as i from investments where user_id=%s", (user_id,))
            return int(lot), int(cur.fetchone()["i"])


@pytest.mark.parametrize("caso,before", [
    ("lote drenado a zero", '{"balance": 0, "principal_remaining": 0}'),
    ("dinheiro como string numérica", '{"balance": "300.00", "principal_remaining": "300.00"}'),
    ("status vazio", '{"balance": 300.0, "principal_remaining": 300.0, "status": ""}'),
    ("status null", '{"balance": 300.0, "principal_remaining": 300.0, "status": null}'),
    ("status false", '{"balance": 300.0, "principal_remaining": 300.0, "status": false}'),
    ("status 0", '{"balance": 300.0, "principal_remaining": 300.0, "status": 0}'),
    ("closed_at null", '{"balance": 300.0, "principal_remaining": 300.0, "closed_at": null}'),
])
def test_positivo_before_valido_continua_apagando(user_id: int, caso: str, before: str):
    """`Decimal(0)` é FALSY: um `if not valor:` no lugar de `_dinheiro` recusaria
    o lote drenado a zero. E `status` falsy já vira "open" no `or` da restauração
    — fechar o falsy seria recusar o que hoje funciona."""
    lid = _aporte_forjado(user_id, '{"delta_conta": 0.0}')
    lot_id, _inv = _ids_do_aporte(user_id)
    _set_efeitos(user_id, lid,
                 '{"delta_conta": 0.0, "investment_lot_withdrawals": '
                 '[{"lot_id": %d, "before": %s}]}' % (lot_id, before))

    db.delete_launch_and_rollback(user_id, lid)

    assert not any(int(r["id"]) == lid for r in db.list_launches(user_id, limit=10)), caso
    assert _bal(user_id) == 700.0, caso


def test_positivo_withdrawals_lista_vazia_continua_apagando(user_id: int):
    """`[]` é container CERTO com zero itens: a checagem de forma não pode
    confundir "lista vazia" com "container errado"."""
    lid = _aporte_forjado(user_id, '{"delta_conta": -50.0, "investment_lot_withdrawals": []}')
    db.delete_launch_and_rollback(user_id, lid)
    assert _bal(user_id) == 750.0
    assert not any(int(r["id"]) == lid for r in db.list_launches(user_id, limit=10))


# ── `investment_lots_handled` só quando o DELETE removeu a linha ─────────────
#
# `_EFEITOS_FORMA` não alcança esta classe: forma não sabe se a linha EXISTE.
# Marcando por formato (o que a `main` faz), medido no `pigbank_ci_test` com o
# `efeitos` de um aporte real (conta/investimento/lotes):
#
#   lot_id inexistente     700/300/(1,300) -> APAGOU 1000/300/(1,300)  TOTAL 1300
#   lot_id de OUTRO user   700/300/(1,300) -> APAGOU 1000/300/(1,300)  TOTAL 1300
#
# R$300 criados do nada nos dois, com o lote de pé. O `and user_id=%s` já
# impedia MEXER no lote alheio; o que faltava era não CREDITAR por causa dele.
#
# Por que RECUSAR em vez de "seguir sem marcar": seguir faz o agregado ser
# recalculado dos lotes E o `delta_invest` subtrair por cima. Medido, com o lote
# já removido por fora: investimento ia a -300.00 e o TOTAL de 1000 pra 700 —
# dinheiro destruído num caso que a `main` acerta. Recusando, nenhum update roda.

def _efeitos_de_aporte(lot_id: int, investment_id: int) -> str:
    return ('{"delta_conta": -300.0, "delta_invest": {"nome": "cdb", "delta": 300.0}, '
            '"investment_lot_create": {"lot_id": %d, "investment_id": %d}}'
            % (lot_id, investment_id))


def test_rowcount_1_apaga_o_lote_e_devolve_o_dinheiro(user_id: int):
    """CONTROLE POSITIVO do `rowcount`: lote válido e DO usuário — uma linha
    removida e o saldo correto. Sem ele o grupo passaria numa versão que recusa
    todo aporte, que é pior que o bug."""
    lid = _aporte_forjado(user_id, '{"delta_conta": 0.0}')
    lot_id, inv_id = _ids_do_aporte(user_id)
    _set_efeitos(user_id, lid, _efeitos_de_aporte(lot_id, inv_id))

    db.delete_launch_and_rollback(user_id, lid)

    assert _lotes(user_id, "investment_lots") == (0, 0.0), "a linha do lote tem de sair"
    assert _investment_balance(user_id, "cdb") == 0.0
    assert _bal(user_id) == 1000.0, "o dinheiro volta inteiro pra conta"


@pytest.mark.parametrize("caso", ["inexistente", "ja_removido", "de_outro_usuario"])
def test_rowcount_0_recusa_e_nao_cria_nem_credita_nada(user_id: int, caso: str):
    """`lot_id` formalmente válido — inteiro positivo — sem correspondência
    AUTORIZADA. O de outro usuário é a regra dura do `CLAUDE.md` §0: o
    `and user_id=%s` tem de segurar, e o lote alheio não pode ser tocado."""
    lid = _aporte_forjado(user_id, '{"delta_conta": 0.0}')
    lot_id, inv_id = _ids_do_aporte(user_id)

    if caso == "inexistente":
        alvo = lot_id + 1_000_000
    elif caso == "ja_removido":
        alvo = lot_id
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from investment_lots where id=%s", (lot_id,))
            conn.commit()
    else:
        outro = int(uuid.uuid4().int % 10_000_000_000)
        db.ensure_user(outro)
        _aporte_forjado(outro, '{"delta_conta": 0.0}')
        alvo, _ = _ids_do_aporte(outro)

    _set_efeitos(user_id, lid, _efeitos_de_aporte(alvo, inv_id))
    lotes_antes = _lotes(user_id, "investment_lots")

    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert exc.value.motivo == "efeito_incompleto"

    # nenhuma quantia criada, creditada ou somada — nem no dono, nem no outro
    assert _bal(user_id) == 700.0, caso
    assert _investment_balance(user_id, "cdb") == 300.0, caso
    assert _lotes(user_id, "investment_lots") == lotes_antes, caso
    assert any(int(r["id"]) == lid for r in db.list_launches(user_id, limit=10))
    if caso == "de_outro_usuario":
        assert _lotes(outro, "investment_lots") == (1, 300.0), "lote alheio intacto"
        assert _bal(outro) == 700.0


class _CursorMentiroso:
    """Proxy que responde `rowcount=2` no delete do lote. É a ÚNICA forma de
    alcançar a violação de invariante: `id` é PK, então o banco casa 0 ou 1 e
    nenhum `efeitos` forjado chega lá."""

    def __init__(self, cur):
        self._cur = cur
        self._mentir = False

    def __getattr__(self, nome):
        return getattr(self._cur, nome)

    def __enter__(self):
        self._cur.__enter__()
        return self

    def __exit__(self, *args):
        return self._cur.__exit__(*args)

    def execute(self, query, params=None, **kw):
        r = self._cur.execute(query, params, **kw)
        self._mentir = "delete from investment_lots" in " ".join(str(query).split())
        return r

    @property
    def rowcount(self):
        return 2 if self._mentir else self._cur.rowcount


class _ConnMentirosa:
    """Embrulha o CONTEXT MANAGER do pool, não a conexão: devolver a conexão pro
    pool é o `__exit__` dele. Fechando a conexão na mão, o pool ficava sem ela e
    o logger de observabilidade do teste seguinte quebrava."""

    def __init__(self, cm):
        self._cm = cm
        self._conn = None

    def __getattr__(self, nome):
        return getattr(self._conn, nome)

    def __enter__(self):
        self._conn = self._cm.__enter__()
        return self

    def __exit__(self, *args):
        return self._cm.__exit__(*args)

    def cursor(self, *a, **kw):
        return _CursorMentiroso(self._conn.cursor(*a, **kw))


def test_rowcount_maior_que_1_interrompe_e_reverte_a_transacao(user_id: int, monkeypatch):
    """`rowcount > 1` é invariante do banco quebrada, não caso de uso: sai CRU
    (balde `errors`, com log de ERROR), nunca como `LaunchUnsafeRollback`, que é
    recusa PREVISTA e vira frase de produto.

    E a transação DESTRUTIVA reverte: quando o erro sobe, o
    `update accounts set balance = balance - delta_conta` JÁ RODOU (a reversão
    da conta vem antes do bloco do lote). Os asserts abaixo provam pelo estado,
    não pela dedução — conta de volta em 700, lote de pé, lançamento listado."""
    import db.accounts as accounts

    lid = _aporte_forjado(user_id, '{"delta_conta": 0.0}')
    lot_id, inv_id = _ids_do_aporte(user_id)
    _set_efeitos(user_id, lid, _efeitos_de_aporte(lot_id, inv_id))

    real = accounts.get_conn
    monkeypatch.setattr(accounts, "get_conn", lambda: _ConnMentirosa(real()))

    with pytest.raises(RuntimeError) as exc:
        db.delete_launch_and_rollback(user_id, lid)
    assert "invariante" in str(exc.value)
    assert not isinstance(exc.value, db.LaunchUnsafeRollback)

    monkeypatch.undo()
    assert _bal(user_id) == 700.0, "a reversão da conta tem de ter sido desfeita"
    assert _lotes(user_id, "investment_lots") == (1, 300.0), "o lote não pode sair"
    assert _investment_balance(user_id, "cdb") == 300.0
    assert any(int(r["id"]) == lid for r in db.list_launches(user_id, limit=10))


# ── `investment_lot_withdrawals`: o irmão que ficou aberto ───────────────────
#
# O `91493d8` fechou `rowcount == 0` só no `investment_lot_create`. O bloco do
# `investment_lot_withdrawals` continuava marcando `investment_lots_handled` por
# SUCESSO DO FETCH (`restored = cur.fetchone(); if restored:`) — `None` deixava
# a flag em `False` e o `delta_invest` subtraía por cima de um agregado sem lote
# atrás. Diferente do irmão, este é alcançável por CAMINHO DE PRODUTO: o
# `ON DELETE CASCADE` de `investment_lots_investment_id_fkey` (`confdeltype='c'`,
# medido em `pg_constraint`) leva os lotes junto quando o investimento é apagado.

def test_produto_lote_sumiu_por_cascade_recusa_em_vez_de_destruir(user_id: int):
    """PONTA A PONTA pelos escritores REAIS, sem `efeitos` forjado: aporte →
    resgate total → apagar o investimento → apagar o lançamento do resgate.

    Sem a guarda (medido, com o `rowcount == 0` do bloco de `withdrawals`
    desligado): APAGOU, conta 1000 → 700 e TOTAL 1000 → 700 — R$300 destruídos
    para sempre, por 5 toques de produto, pelas 4 portas do delete singular."""
    from db.investments import (create_investment, delete_investment,
                                investment_deposit_from_account,
                                investment_withdraw_to_account)

    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_investment(user_id, "cdb", 0.01, "monthly")
    investment_deposit_from_account(user_id, "cdb", 300, "aportando")
    investment_withdraw_to_account(user_id, "cdb", 300, "resgatando tudo")
    resgate = int(db.list_launches(user_id, limit=1)[0]["id"])
    assert _bal(user_id) == 1000.0, "resgate total devolve o dinheiro pra conta"

    delete_investment(user_id, "cdb")
    assert _lotes(user_id, "investment_lots") == (0, 0.0), \
        "o CASCADE leva os lotes junto — é o gatilho, não um `efeitos` forjado"

    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, resgate)
    assert exc.value.motivo == "efeito_incompleto"
    assert _bal(user_id) == 1000.0, "a recusa é ANTES de qualquer update"
    assert any(int(r["id"]) == resgate for r in db.list_launches(user_id, limit=20))


def test_multi_lote_com_um_podre_recusa_a_lista_inteira(user_id: int):
    """A DECISÃO explícita: um item podre entre bons recusa o lançamento inteiro.

    Sem ela (medido, com o `rowcount == 0` desligado), o primeiro item bom já
    liga `investment_lots_handled=True`, o `delta_invest` é pulado inteiro e o
    agregado é recalculado só do que restaurou:
      antes    conta=1000 inv=0   lotes=[(A,0,closed),(B,0,closed)]  TOTAL=1000
      APAGOU
      depois   conta= 300 inv=300 lotes=[(A,300,open),(B,0,closed)]  TOTAL= 600
    R$400 destruídos. Reverter METADE de um resgate não é reverter — o único
    fail-closed coerente é não reverter nada.

    Forjado de propósito: o CASCADE tira os lotes de um investimento TODOS de
    uma vez, então "um bom + um podre" não é alcançável pelos escritores de
    hoje. É a mesma blindagem fail-closed do resto da matriz."""
    from db.investments import (create_investment, investment_deposit_from_account,
                                investment_withdraw_to_account)

    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_investment(user_id, "cdb", 0.01, "monthly")
    investment_deposit_from_account(user_id, "cdb", 300, "aporte 1")
    investment_deposit_from_account(user_id, "cdb", 400, "aporte 2")
    investment_withdraw_to_account(user_id, "cdb", 700, "resgatando tudo")
    resgate = int(db.list_launches(user_id, limit=1)[0]["id"])
    lot_a, _inv = _ids_do_aporte(user_id)
    assert _bal(user_id) == 1000.0 and _lotes(user_id, "investment_lots") == (2, 0.0)

    # item 1 casa linha de verdade; item 2 aponta pra lote que não existe
    _set_efeitos(user_id, resgate,
                 '{"delta_conta": 700.0, "delta_invest": {"nome": "cdb", "delta": -700.0}, '
                 '"investment_lot_withdrawals": ['
                 '{"lot_id": %d, "before": {"balance": 300.0, "principal_remaining": 300.0, '
                 '"status": "open"}}, '
                 '{"lot_id": %d, "before": {"balance": 400.0, "principal_remaining": 400.0, '
                 '"status": "open"}}]}' % (lot_a, lot_a + 1_000_000))

    with pytest.raises(db.LaunchUnsafeRollback) as exc:
        db.delete_launch_and_rollback(user_id, resgate)
    assert exc.value.motivo == "efeito_incompleto"
    assert _bal(user_id) == 1000.0, "nada creditado nem debitado"
    assert _investment_balance(user_id, "cdb") == 0.0
    assert _lotes(user_id, "investment_lots") == (2, 0.0), \
        "o item BOM também não pode ser restaurado — meia reversão destrói R$400"


# ── CONTROLES POSITIVOS do afrouxamento de `_id` e `_data` ───────────────────
#
# Regressão medida do `91493d8` contra o `b4d0085` (varredura em duas colunas,
# 435 formas): 4 formas que o `b4d0085` revertia CERTO viraram `kept_unsafe`.
# `kept_unsafe` é PERMANENTE e é SILÊNCIO em 3 das 8 portas — falso positivo
# aqui custa mais que a forma que ele recusa.

@pytest.mark.parametrize("caso,bill", [
    ("bill_id string de dígitos", '"%d"'),
    ("bill_id float integral", '%d.0'),
])
def test_positivo_bill_id_como_string_e_float_continua_revertendo(user_id: int,
                                                                  caso: str, bill: str):
    """O Postgres coage `"93"` e `93.0` no `where id=%s` e o `int(paid_bill_id)`
    aceita os dois (medido). No `91493d8` isso virava RECUSA com a fatura presa
    em `paid` e o pagamento de pé; no `b4d0085` revertia certo."""
    from datetime import date as _date

    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    _tx, _due, bill_id = db.add_credit_purchase(
        user_id, card_id, 100, "outros", "compra", _date.today())
    add_launch_and_update_balance(user_id, "receita", 1000, None, "salario")
    db.pay_bill_amount(user_id, card_id, "Nubank", 100.0)
    pgto = int(db.list_launches(user_id, limit=1)[0]["id"])
    assert _bal(user_id) == 900.0 and _bill_status(bill_id) == "paid"

    _set_efeitos(user_id, pgto,
                 '{"delta_conta": -100.0, "bill_id": %s, "paid_amount_added": 100.0}'
                 % (bill % bill_id))

    db.delete_launch_and_rollback(user_id, pgto)

    assert _bal(user_id) == 1000.0, caso
    assert _bill_status(bill_id) == "open", caso


@pytest.mark.parametrize("caso,lot", [
    ("lot_id string de dígitos", '"%d"'),
    ("lot_id float integral", '%d.0'),
])
def test_positivo_lot_id_como_string_e_float_apaga_o_lote(user_id: int,
                                                          caso: str, lot: str):
    """Mesmo afrouxamento no `investment_lot_create`: o `delete from
    investment_lots where id=%s` casa a linha com os dois, o `rowcount` vem 1 e
    o lote SAI. No `91493d8` era recusa; na base o lote saía certo."""
    lid = _aporte_forjado(user_id, '{"delta_conta": 0.0}')
    lot_id, inv_id = _ids_do_aporte(user_id)
    _set_efeitos(user_id, lid,
                 '{"delta_conta": -300.0, "delta_invest": {"nome": "cdb", "delta": 300.0}, '
                 '"investment_lot_create": {"lot_id": %s, "investment_id": %d}}'
                 % (lot % lot_id, inv_id))

    db.delete_launch_and_rollback(user_id, lid)

    assert _lotes(user_id, "investment_lots") == (0, 0.0), caso
    assert _bal(user_id) == 1000.0, caso


_ISO_COM_HORA = "2026-09-02T15:04:05.123456-03:00"


@pytest.mark.parametrize("campo", ["last_date", "purchase_date", "maturity_date"])
def test_positivo_delete_investment_com_data_iso_completa_recria(user_id: int, campo: str):
    """`date.fromisoformat` recusa ISO com hora no 3.13 (medido) e a coluna
    `date` do Postgres aceita, cortando a hora (medido). O `91493d8` recusava os
    três campos; `last_date` era o único que a reversão de fato não sabia usar,
    e o `[:10]` (padrão já usado em `core/services/cashflow.py:30`) fecha."""
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    lid, _seq, _b = add_launch_and_update_balance(user_id, "despesa", 0, None, "apaguei cdb")
    _set_efeitos(user_id, lid,
                 '{"delta_conta": 0.0, "delete_investment": {"nome": "cdb", '
                 '"balance": 300.0, "%s": "%s"}}' % (campo, _ISO_COM_HORA))

    db.delete_launch_and_rollback(user_id, lid)

    assert _investment_balance(user_id, "cdb") == 300.0, "o investimento tem de voltar"
    assert not any(int(r["id"]) == lid for r in db.list_launches(user_id, limit=10))


def test_positivo_before_closed_at_iso_completo_restaura_o_lote(user_id: int):
    """Mesmo afrouxamento no `before.closed_at`, que vai CRU pra coluna `date`."""
    lid = _aporte_forjado(user_id, '{"delta_conta": 0.0}')
    lot_id, _inv = _ids_do_aporte(user_id)
    _set_efeitos(user_id, lid,
                 '{"delta_conta": 0.0, "investment_lot_withdrawals": [{"lot_id": %d, '
                 '"before": {"balance": 300.0, "principal_remaining": 300.0, '
                 '"status": "closed", "closed_at": "%s"}}]}' % (lot_id, _ISO_COM_HORA))

    db.delete_launch_and_rollback(user_id, lid)

    assert not any(int(r["id"]) == lid for r in db.list_launches(user_id, limit=10))
    assert _bal(user_id) == 700.0
