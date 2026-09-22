"""`POST /simulator/{user_id}` pelo HTTP de verdade e a tool `simulate_purchase`.

O gate de plano roda inteiro (`plan_gate_ok` → `require_min_tier` →
`get_plan_tier`): só a BUSCA da conta (`plan_service.get_auth_user`) é mockada,
porque ela vai ao banco. A autorização roda a checagem de dono real; só a
leitura da sessão (`resolve_dashboard_user_id`) e as pernas de banco do gate de
assinatura são mockadas.
"""
import json
import re

import pytest

from _cashflow_helpers import _mock_sources

VALIDO = {"reserva_minima": 1000.0, "cenarios": [
    {"nome": "Entrada 36k", "preco": 180000.0, "entrada": 36000.0, "parcelas": 48, "juros_mensal_pct": 1.49,
     "custos_unicos": 2500.0, "despesa_mensal_nova": 450.0}]}


@pytest.fixture
def cliente(monkeypatch):
    from fastapi.testclient import TestClient
    import core.services.plan_service as plan_service
    import frontend.finance_bot_websocket_custom as app_mod
    from frontend.routes import shared

    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    sessao = {"user_id": 1}
    monkeypatch.setattr(shared, "resolve_dashboard_user_id", lambda req: sessao["user_id"])
    monkeypatch.setattr(shared, "raise_if_account_scheduled_for_deletion", lambda uid: None)
    monkeypatch.setattr(shared, "_enforce_subscription_gate", lambda req, uid, exige_direito=True: None)
    conta = {"plan": "pro_max", "plan_expires_at": None}  # tier pro, vitalício
    monkeypatch.setattr(plan_service, "get_auth_user", lambda uid: dict(conta))
    _mock_sources(monkeypatch, saldo=50000.0)
    client = TestClient(app_mod.app, raise_server_exceptions=False)
    return client, conta, sessao


def _post(client, corpo, user_id=1):
    raw = corpo if isinstance(corpo, str) else json.dumps(corpo)
    return client.post(f"/simulator/{user_id}", content=raw, headers={"content-type": "application/json"})


@pytest.mark.parametrize("plan,status", [("essencial", 403), ("pro", 403), ("pro_max", 200)])
def test_gate_so_libera_o_tier_pro(cliente, plan, status):
    client, conta, _ = cliente
    conta["plan"] = plan  # 'pro' gravado = tier Plus; 'pro_max' = tier Pro
    r = _post(client, VALIDO)
    assert r.status_code == status, r.text
    if status == 403:
        assert r.json()["detail"] == {"error": "pro_required", "feature": "simulator"}
    else:
        sim = r.json()["simulacao"]
        assert set(sim) == {"today", "balance_source", "banks_excluded", "reserva_minima", "atual", "cenarios", "premissas"}
        assert "trajectory" not in json.dumps(sim)
        contrato = sim["cenarios"][0]["contrato"]
        # entrada 36.000 + 48 parcelas (202.606,84) + custos únicos 2.500
        assert (contrato["parcela"], contrato["total_pago"]) == (4220.98, 241106.84)
        assert contrato["a_vista"] is False
        assert (contrato["entrada"], contrato["pago_na_compra"]) == (36000.0, 38500.0)  # + custos 2.500
        assert sim["cenarios"][0]["resumo"]["compra_fora_da_janela"] is False


@pytest.mark.parametrize("corpo", ['[1, 2]', '{"cenarios": [', '{"x": NaN}'])
def test_sem_acesso_a_resposta_nao_depende_do_corpo(cliente, corpo):
    client, conta, sessao = cliente
    conta["plan"] = "essencial"
    assert _post(client, corpo).status_code == 403
    conta["plan"] = "pro_max"
    sessao["user_id"] = 2
    assert _post(client, corpo, user_id=1).status_code == 403  # sessão de outro usuário


def test_juros_minusculos_nao_dao_500(cliente):
    client, _, _ = cliente
    r = _post(client, {"cenarios": [{"nome": "x", "preco": 1_000_000.0, "parcelas": 420, "juros_mensal_pct": 1e-15}]})
    assert r.status_code == 200, r.text
    assert r.json()["simulacao"]["cenarios"][0]["contrato"]["juros_totais"] >= 0


@pytest.mark.parametrize("corpo", [
    '{"cenarios": [{"nome": "x", "preco": NaN}]}',
    '{"cenarios": [{"nome": "x", "preco": Infinity}]}',
    '{"reserva_minima": NaN, "cenarios": [{"nome": "x", "preco": 10}]}',
    '{"cenarios": [{"nome": "x", "preco": 10, "juros_mensal_pct": -Infinity}]}',
    {"cenarios": [{"nome": "x", "preco": 100.0, "entrada": 100.01}]},
    {"cenarios": []},
    {"cenarios": [{**VALIDO["cenarios"][0], "nome": f"n{i}"} for i in range(4)]},
    {"cenarios": [VALIDO["cenarios"][0]] * 2},  # nomes repetidos
    {"cenarios": [{"nome": "x", "preco": 1000.0, "entrada": 400.0}]},  # entrada sem parcelas
    {"cenarios": [{"nome": "x", "preco": 1000.0, "entrada": 1000.0, "parcelas": 12}]},  # nada a financiar
    {"cenarios": [{"nome": "x", "preco": 10.0, "juros_mensal": None}]},  # chave errada em null
    {"cenarios": [{"nome": "x", "preco": 50000.0, "parcelas": 1, "juros_mensal_pct": 5.0}]},  # 1x com juros
    {"cenarios": [{"nome": "A", "preco": 10.0}, {"nome": "a ", "preco": 20.0}]},  # nome repetido normalizado
    {"cenarios": [{"nome": "x", "preco": 10.0, "juros_mensal": 1.5}]},  # nome de campo errado
    {"reserva": 10.0, "cenarios": [{"nome": "x", "preco": 10.0}]},
    '{"cenarios": [{"nome": "x", "preco": true}]}',
    '{"cenarios": [{"nome": "x", "preco": "180000"}]}',
    '[1, 2]',
    '{"cenarios": [',
])
def test_corpo_invalido_da_400_e_o_valido_200(cliente, corpo):
    client, _, _ = cliente
    assert _post(client, VALIDO).status_code == 200  # controle positivo no mesmo grupo
    assert _post(client, '{"cenarios": [{"nome": "x", "preco": 180000, "entrada": 36000, "parcelas": 48}]}').status_code == 200
    r = _post(client, corpo)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error"] == "invalid_simulation"
    assert all(set(e) == {"loc", "msg"} for e in r.json()["detail"]["errors"])  # sem `input` ecoando o corpo


@pytest.mark.parametrize("corpo", ['{"cenarios": [{"nome": "x", "preco": 1' + "0" * 5000 + '}]}', "[" * 100000])
def test_json_que_estoura_o_parser_da_400_e_nao_500(cliente, corpo):
    client, _, _ = cliente
    assert _post(client, VALIDO).status_code == 200  # controle positivo no mesmo grupo
    invalido = _post(client, {"cenarios": []})
    assert invalido.status_code == 400 and invalido.json()["detail"]["errors"]  # erro de validação mantém o detalhe
    r = _post(client, corpo)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error"] == "invalid_simulation"


def test_usuario_nao_simula_com_o_id_de_outro(cliente):
    client, _, sessao = cliente
    sessao["user_id"] = 1
    assert _post(client, VALIDO, user_id=1).status_code == 200
    r = _post(client, VALIDO, user_id=2)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == "Acesso negado para este usuário."


@pytest.mark.parametrize("corpo", [VALIDO, '[1, 2]', '{"cenarios": ['])
def test_sem_sessao_da_401_qualquer_que_seja_o_corpo(corpo):
    from fastapi.testclient import TestClient
    import frontend.finance_bot_websocket_custom as app_mod
    r = _post(TestClient(app_mod.app, raise_server_exceptions=False), corpo)
    assert r.status_code == 401, r.text


# ─── tool de IA ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("plan", ["essencial", "pro"])
def test_tool_abaixo_de_pro_devolve_convite_sem_numeros(cliente, plan):
    from core.services.ai_chat.tools import get_tool
    _, conta, _ = cliente
    conta["plan"] = plan
    out = get_tool("simulate_purchase").execute(1, dict(VALIDO))
    assert set(out) == {"error", "message"} and out["error"] == "pro_required"


def test_tool_no_pro_simula_e_valida_como_a_rota(cliente):
    from core.services.ai_chat.tools import WRITE_TOOL_NAMES, get_tool
    tool = get_tool("simulate_purchase")
    assert "simulate_purchase" not in WRITE_TOOL_NAMES
    out = tool.execute(1, dict(VALIDO))
    assert out["cenarios"][0]["contrato"]["parcela"] == 4220.98 and "note" in out
    for args in ({"cenarios": [{"nome": "x", "preco": float("nan")}]},
                 {"cenarios": [{"nome": "x", "preco": 10.0, "juros_mensal": 1.5}]},
                 {"cenarios": [{"nome": "x", "preco": True}]}):
        assert tool.execute(1, args)["error"] == "invalid_args", args


@pytest.mark.parametrize("campo", [
    "preco", "entrada", "custos_unicos", "despesa_mensal_nova", "reserva_minima",
])
def test_subcentavos_recusados_na_rota_e_no_dispatch_antes_de_ler_fontes(cliente, monkeypatch, campo):
    from core.services import decision_simulator
    from core.services.ai_chat import runner

    client, _, _ = cliente
    # Mesmo caminho com dinheiro válido continua disponível no Pro.
    assert _post(client, VALIDO).status_code == 200
    assert "cenarios" in json.loads(runner._dispatch_tool(1, "simulate_purchase", dict(VALIDO))[0])
    corpo = {"cenarios": [{"nome": "Compra", "preco": 200, "parcelas": 2}]}
    (corpo if campo == "reserva_minima" else corpo["cenarios"][0])[campo] = 100.025

    def nao_calcular(*args):
        pytest.fail("corpo inválido chegou à leitura das fontes financeiras")
    monkeypatch.setattr(decision_simulator, "simulate", nao_calcular)
    r = _post(client, corpo)
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error"] == "invalid_simulation"
    erro = json.loads(runner._dispatch_tool(1, "simulate_purchase", corpo)[0])
    assert erro["error"] == "invalid_args"
    for erros in (r.json()["detail"]["errors"], erro["errors"]):
        assert any(e["loc"][-1] == campo and "duas casas decimais" in e["msg"] for e in erros)


def test_note_da_tool_manda_os_dois_efeitos_e_proibe_apontar_vencedor(cliente):
    """O contrato "mostre os dois efeitos e não recomende" só existe no texto da
    `note` — é ele que o LLM lê. `"note" in out` não prendia nada disso, e um
    campo renomeado no payload deixaria a `note` mandando mostrar chave que não
    existe mais (§0.7: as duas versões da regra têm de ser comparadas)."""
    from core.services.ai_chat.tools import get_tool
    out = get_tool("simulate_purchase").execute(1, dict(VALIDO))
    note = out["note"]
    assert "não recomende" in note.lower() and "vencedor" in note.lower()
    resumo, contrato = out["cenarios"][0]["resumo"], out["cenarios"][0]["contrato"]
    # Identificador com `_` na note é nome de campo. Os candidatos saem das chaves
    # REAIS do payload, não de uma lista à mão: campo novo citado entra sozinho.
    citados = set(re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", note))
    # 1) nenhum citado pode estar morto (ex.: `pago_na_compra` renomeado no payload)
    mortos = citados - set(resumo) - set(contrato) - set(out)
    assert not mortos, f"a note cita campos que o payload não tem: {sorted(mortos)}"
    # 2) os DOIS efeitos: ao menos um VALOR de cada lado. Marcador booleano
    #    (`compra_fora_da_janela`, `a_vista`) não é efeito e não conta.
    valores = lambda d: {k for k, v in d.items() if not isinstance(v, bool)}
    assert citados & valores(resumo), f"a note não cita nenhum valor do saldo de 90 dias: {note}"
    assert citados & valores(contrato), f"a note não cita nenhum valor do contrato: {note}"
    # 3) mínimo obrigatório: "um de cada lado" não pega a omissão dos juros, que são
    #    a diferença entre cenários em "compensa dar mais entrada?".
    obrigatorios = {"total_pago", "juros_totais", "parcelas_fora_do_horizonte",
                    "delta_vs_atual", "pior_dia"}
    assert obrigatorios <= citados, f"a note deixou de citar: {sorted(obrigatorios - citados)}"


def test_tool_simula_o_user_da_conversa_e_nunca_um_id_vindo_dos_args(cliente, monkeypatch):
    """O `user_id` da tool vem do runner (`runner.py`), nunca do argumento do LLM.
    O mock das fontes descarta o `uid`, então sem anotar quem foi lido um id
    cravado passaria verde — mesmo motivo do teste irmão em test_decision_simulator.
    """
    from core.services.ai_chat import runner
    from _cashflow_helpers import _mock_sources
    uids: dict[str, list] = {}
    _mock_sources(monkeypatch, saldo=1234.0, uids=uids)
    saida = json.loads(runner._dispatch_tool(42, "simulate_purchase", dict(VALIDO))[0])
    assert {u for lidos in uids.values() for u in lidos} == {42}
    assert saida["atual"]["saldo_final_90"] == 1234.0  # controle positivo: rodou de verdade
    # e o LLM não consegue escolher a conta: `user_id` no corpo é recusado
    ruim = runner._dispatch_tool(42, "simulate_purchase", {"user_id": 1, **VALIDO})
    assert json.loads(ruim[0])["error"] == "invalid_args"


# ─── aviso de saldo de partida incompleto (apontamento P2 do Codex, PR #496) ──

@pytest.mark.parametrize("cb,gate,erro,esperado", [
    # controle positivo: banco conectado e consolidado liberado → sem aviso
    ({"of_bank_count": 1, "manual": 100.0, "consolidated": 50000.0}, True, False, ""),
    # banco conectado com o consolidado desligado → só a Carteira
    ({"of_bank_count": 1, "manual": 100.0, "consolidated": 50000.0}, False, False, "usa só o saldo da sua Carteira"),
    # consulta ao consolidado falhou → não dá pra confirmar
    (None, True, True, "Não foi possível confirmar seu saldo consolidado"),
])
def test_tool_avisa_quando_o_saldo_de_partida_nao_e_o_consolidado(cliente, monkeypatch, cb, gate, erro, esperado):
    import db
    import core.services.plan_service as plan_service
    from core.services.ai_chat import runner

    def consolidado(uid):
        if erro:
            raise RuntimeError("OF fora do ar")
        return dict(cb)
    monkeypatch.setattr(db, "get_consolidated_balance", consolidado, raising=False)
    monkeypatch.setattr(plan_service, "consolidated_balance_enabled", lambda uid, email=None: gate)
    out = json.loads(runner._dispatch_tool(1, "simulate_purchase", dict(VALIDO))[0])
    if esperado:
        assert esperado in out["aviso_saldo"], out["aviso_saldo"]
    else:
        assert out["aviso_saldo"] == "" and out["balance_source"] == "consolidated"
    assert "aviso_saldo" in out["note"]  # a note manda repetir o aviso
