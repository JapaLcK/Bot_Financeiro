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
