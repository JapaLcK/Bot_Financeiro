"""G2 — saúde do item e os estados de UI (funções puras, sem banco e sem rede).

`derive_item_health` traduz o `GET /items/{id}`; `connection_ui_state` é a única
função que decide em que estado uma conexão está. Antes, quem decidia era um
`connStatusPill` no settings.html que só olhava o `status` — e por isso não sabia
diferenciar "atualizado" de "atualizei o que deu" nem de "o item sumiu".

CONTROLE NEGATIVO do grupo: fazer `derive_item_health` devolver `{}` fixo deixa
9–13 vermelhos.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from core.services.pluggy_health import (
    connection_ui_state,
    derive_item_health,
    resolve_connection_state,
)
from utils_date import _tz

AGORA = datetime(2026, 8, 20, 12, 0, 0, tzinfo=_tz())

ITEM_PARCIAL = {
    "id": "item-x",
    "status": "UPDATED",
    "executionStatus": "PARTIAL_SUCCESS",
    "statusDetail": {
        "accounts": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00.000Z", "warnings": []},
        "creditCards": {"isUpdated": False, "lastUpdatedAt": "2026-08-12T03:10:00.000Z",
                        "warnings": [{"code": "004", "message": "Conta 1234-5 de JOÃO DA SILVA"}]},
    },
}


def test_partial_com_cartao_atrasado_desde_12_08():
    health = derive_item_health(ITEM_PARCIAL, now=AGORA)
    assert health["execution_status"] == "PARTIAL_SUCCESS"
    assert health["stale_products"] == ["CREDIT"]

    ui = connection_ui_state({"status": "ACTIVE", "health": health, "last_sync_at": AGORA})
    assert ui["state"] == "partial"
    assert ui["label"] == "Parcial"
    assert "Cartão" in ui["detail"]
    assert "12/08" in ui["detail"], ui["detail"]
    assert ui["stale_products"] == ["CREDIT"]


def test_tudo_atualizado_vira_updated():
    item = {
        "status": "UPDATED", "executionStatus": "SUCCESS",
        "statusDetail": {
            "accounts": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00.000Z"},
            "creditCards": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00.000Z"},
            "investments": {"isUpdated": True, "lastUpdatedAt": "2026-08-20T11:00:00.000Z"},
        },
    }
    health = derive_item_health(item, now=AGORA)
    assert health["stale_products"] == []

    ui = connection_ui_state({"status": "ACTIVE", "health": health, "last_sync_at": AGORA})
    assert (ui["state"], ui["label"], ui["detail"]) == ("updated", "Atualizado", None)


def test_updating_e_updating():
    health = derive_item_health({"status": "UPDATING"}, now=AGORA)
    ui = connection_ui_state({"status": "ACTIVE", "health": health, "last_sync_at": AGORA})
    assert ui["state"] == "updating"
    assert ui["label"] == "Atualizando…"


def test_login_error_e_waiting_user_input_pedem_acao_do_usuario():
    for status in ("LOGIN_ERROR", "WAITING_USER_INPUT"):
        health = derive_item_health({"status": status}, now=AGORA)
        ui = connection_ui_state({"status": "ACTIVE", "health": health, "last_sync_at": AGORA})
        assert ui["state"] == "needs_user_action", status
        assert ui["detail"] == "Reautorize o banco"


def test_warning_chega_ao_health_sem_pii():
    health = derive_item_health(ITEM_PARCIAL, now=AGORA)
    warnings = health["products"]["CREDIT"]["warnings"]
    assert warnings == ["004"], "só o código do warning entra no health"

    texto = repr(health)
    for vazamento in ("JOÃO", "SILVA", "1234-5", "Conta"):
        assert vazamento not in texto, f"vazou '{vazamento}' no health: {texto}"


def test_linha_legada_sem_health_nao_vira_nunca_sincronizou():
    ui = connection_ui_state({"status": "ACTIVE", "health": None, "last_sync_at": AGORA})
    assert ui["state"] == "updated"
    assert "nunca" not in (ui["detail"] or "").lower()
    assert "não sincronizou" not in (ui["detail"] or "").lower()


def test_sem_health_e_sem_last_sync_e_que_e_nunca_sincronizou():
    ui = connection_ui_state({"status": "ACTIVE", "health": None, "last_sync_at": None})
    assert ui["state"] == "updating"
    assert ui["detail"] == "Ainda não sincronizou"


def test_item_missing_paused_e_removed_vem_do_status_local():
    perdido = connection_ui_state({"status": "ERROR", "status_reason": "item_missing"})
    assert (perdido["state"], perdido["label"]) == ("item_missing", "Conexão perdida")
    assert perdido["detail"] == "Refaça a conexão com o banco"

    pausado = connection_ui_state({"status": "PAUSED"})
    assert (pausado["state"], pausado["label"]) == ("paused", "Pausado")

    removido = connection_ui_state({"status": "DELETED"})
    assert (removido["state"], removido["label"], removido["detail"]) == ("removed", "Removido", None)


def test_erro_sem_motivo_e_recuperavel():
    ui = connection_ui_state({"status": "ERROR", "health": None, "last_sync_at": AGORA})
    assert ui["state"] == "error_recoverable"
    assert ui["detail"] == "Tentaremos de novo automaticamente"


# ── warning como lista de STRINGS: o corte em 64 chars não protegia nada ─────
# Medido pelo Tester: `warnings: ["Conta 1234-5 de JOAO DA SILVA CPF ..."]` entrava
# no health com 64 caracteres da mensagem — e o health desce no snapshot pro
# navegador e sobe pro painel admin.
# CONTROLE NEGATIVO: voltar `code = w.get("code") if isinstance(w, dict) else w`
# deixa este teste vermelho.

def test_warning_como_string_nao_vaza_texto_livre():
    item = {
        "status": "UPDATED",
        "statusDetail": {"accounts": {
            "isUpdated": False, "lastUpdatedAt": "2026-08-12T03:00:00Z",
            "warnings": ["Conta 1234-5 de JOAO DA SILVA CPF 123.456.789-00 nao pode ser lida"],
        }},
    }
    health = derive_item_health(item, now=AGORA)

    assert health["products"]["BANK"]["warnings"] == ["invalid_warning"]
    texto = repr(health)
    for vazamento in ("JOAO", "SILVA", "123.456.789", "1234-5", "Conta"):
        assert vazamento not in texto, f"vazou '{vazamento}': {texto}"


def test_warning_dict_com_code_estranho_tambem_vira_sentinela():
    """CONTROLE POSITIVO/limite: o formato legítimo passa (test_warning_chega_ao_health
    _sem_pii cobre isso); texto livre dentro do `code` não."""
    item = {"status": "UPDATED", "statusDetail": {"accounts": {
        "isUpdated": True, "warnings": [
            {"code": "MFA_TIMEOUT"},                       # legítimo
            {"code": "titular JOAO DA SILVA sem acesso"},  # texto livre disfarçado
            {"message": "sem code nenhum"},
        ]}}}
    codes = derive_item_health(item, now=AGORA)["products"]["BANK"]["warnings"]

    assert codes == ["MFA_TIMEOUT", "invalid_warning", "invalid_warning"]
    assert "JOAO" not in repr(codes)


# ── ordem: ERROR ganha de "produto atrasado" ────────────────────────────────
# CONTROLE NEGATIVO: pôr o teste de `stale` antes do de ERROR (como era) devolve
# "partial" e este teste fica vermelho.

def test_error_com_produto_atrasado_e_erro_nao_parcial():
    health = derive_item_health({
        "status": "ERROR", "executionStatus": "ERROR",
        "statusDetail": {"creditCards": {"isUpdated": False,
                                         "lastUpdatedAt": "2026-08-12T03:00:00Z"}},
    }, now=AGORA)

    ui = connection_ui_state({"status": "ERROR", "health": health, "last_sync_at": AGORA})
    assert ui["state"] == "error_recoverable", "erro com produto atrasado é ERRO, não 'Parcial'"


def test_partial_sem_erro_continua_partial():
    """CONTROLE POSITIVO da ordem acima: sem ERROR, atraso continua sendo 'Parcial'."""
    ui = connection_ui_state({"status": "ACTIVE", "health": derive_item_health(ITEM_PARCIAL, now=AGORA)})
    assert ui["state"] == "partial"


# ── a CLASSE: estado/motivo desconhecido nunca é verde ──────────────────────
# `no_accounts` chegava à tela como "Atualizado" porque `connection_ui_state` não
# conhecia o motivo e caía no ramo de sucesso. O conserto é a regra, não o caso.
# CONTROLE NEGATIVO: tirar a guarda do `out()` deixa os três casos vermelhos.

def test_motivo_pendente_desconhecido_nunca_e_updated():
    saudavel = derive_item_health({"status": "UPDATED", "executionStatus": "SUCCESS",
                                   "statusDetail": {"accounts": {"isUpdated": True}}}, now=AGORA)
    for motivo, esperado in (("no_accounts", "no_accounts"),
                             ("motivo_que_ninguem_escreveu_ainda", "error_recoverable"),
                             ("quota_exceeded", "error_recoverable")):
        ui = connection_ui_state({"status": "ACTIVE", "status_reason": motivo,
                                  "health": saudavel, "last_sync_at": AGORA})
        assert ui["state"] == esperado, motivo
        assert ui["state"] != "updated"


def test_motivo_ok_ou_vazio_continua_verde():
    """CONTROLE POSITIVO: a guarda não pode recusar o caminho legítimo."""
    saudavel = derive_item_health({"status": "UPDATED", "executionStatus": "SUCCESS",
                                   "statusDetail": {"accounts": {"isUpdated": True}}}, now=AGORA)
    for motivo in ("ok", "", None):
        ui = connection_ui_state({"status": "ACTIVE", "status_reason": motivo,
                                  "health": saudavel, "last_sync_at": AGORA})
        assert ui["state"] == "updated", motivo


# ── RODADA 4: espelho vazio NÃO pode deixar o ERROR grudado ─────────────────
# Defeito medido: `resolve_connection_state` só corrigia o `status` no ramo
# `has_data=True`; com o espelho vazio devolvia `None` ("não mexe"), e `None` não
# tira um ERROR que já está lá. Com `OF_REFRESH_ENABLED` off (o default) não há
# PATCH → webhook → sync completo, então ERROR virava TERMINAL na prática.
# CONTROLE NEGATIVO (medido): trocar os dois `return "ACTIVE", ...` do ramo de
# espelho vazio por `return None, ...` deixa os dois primeiros testes vermelhos.

def test_espelho_vazio_com_item_vivo_sai_de_error():
    """O par (status, reason) JUNTO: o status é a saúde do ITEM (boa), o motivo é
    quem explica o espelho vazio."""
    vivo = {"item_status": "UPDATED"}
    # E: leu tudo e não veio nada.
    assert resolve_connection_state(health=vivo, has_data=False, leitura_completa=True,
                                    reason_atual="item_missing") == ("ACTIVE", "no_accounts")
    # D: 429 no meio da leitura — "não consegui ler" ≠ "li e veio vazio".
    assert resolve_connection_state(health=vivo, has_data=False, leitura_completa=False,
                                    reason_atual="item_missing") == ("ACTIVE", "read_failed")


def test_job_de_saude_com_espelho_vazio_tambem_sai_de_error():
    """H: o job não leu `/accounts`, então não INVENTA motivo — mas o item está
    vivo, e deixar o ERROR de ontem de pé era o bug. Motivo de espelho vazio
    continua sendo carregado; um `item_missing` desmentido cai."""
    vivo = {"item_status": "UPDATED"}
    assert resolve_connection_state(health=vivo, has_data=False, leitura_completa=None,
                                    reason_atual="item_missing") == ("ACTIVE", "")
    assert resolve_connection_state(health=vivo, has_data=False, leitura_completa=None,
                                    reason_atual="no_accounts") == ("ACTIVE", "no_accounts")


@pytest.mark.parametrize("item_status", ["LOGIN_ERROR", "WAITING_USER_INPUT", "ERROR"])
def test_item_doente_com_espelho_vazio_continua_em_erro(item_status):
    """CONTROLE POSITIVO: o conserto não pode virar "nunca marca erro" — que é
    pior que o bug. Item que exige o usuário (ou em erro) segue em ERROR."""
    assert resolve_connection_state(health={"item_status": item_status}, has_data=False,
                                    leitura_completa=True) == ("ERROR", "")
    assert resolve_connection_state(missing=True) == ("ERROR", "item_missing")


# ── RODADA 4: `no_accounts` mascarado de "Erro temporário" na UI ────────────
# `connection_ui_state` testava `status == "ERROR"` ANTES do ramo `updated`, e o
# override de motivo só rodava dentro do `out("updated")`: a linha
# ERROR/no_accounts nunca mostrava "Sem dados", e o /refresh mandava
# state="error_recoverable" (o OF_VERDICT do settings.html casa por STATE).
# CONTROLE NEGATIVO (medido): voltar os dois ramos para `out("error_recoverable")`
# fixo deixa `test_error_com_no_accounts_mostra_sem_dados` vermelho nos 2 casos.

def _saudavel() -> dict:
    return derive_item_health({"status": "UPDATED", "executionStatus": "SUCCESS",
                               "statusDetail": {"accounts": {"isUpdated": True}}}, now=AGORA)


def test_error_com_no_accounts_mostra_sem_dados():
    com_health = connection_ui_state({"status": "ERROR", "status_reason": "no_accounts",
                                      "health": _saudavel(), "last_sync_at": AGORA})
    assert (com_health["state"], com_health["label"]) == ("no_accounts", "Sem dados")
    # linha legada, sem saúde medida: mesma classe, mesmo desfecho.
    sem_health = connection_ui_state({"status": "ERROR", "status_reason": "no_accounts",
                                      "health": None, "last_sync_at": AGORA})
    assert (sem_health["state"], sem_health["label"]) == ("no_accounts", "Sem dados")


def test_erro_de_verdade_nao_e_rebaixado_a_sem_dados():
    """CONTROLE POSITIVO da tabela de gravidade: motivo velho não subestima erro,
    e este ramo NUNCA vira verde."""
    doente = derive_item_health({"status": "ERROR", "statusDetail": {}}, now=AGORA)
    ui = connection_ui_state({"status": "ERROR", "status_reason": "no_accounts",
                              "health": doente, "last_sync_at": AGORA})
    assert ui["state"] == "error_recoverable"
    outro = connection_ui_state({"status": "ERROR", "status_reason": "read_failed",
                                 "health": _saudavel(), "last_sync_at": AGORA})
    assert outro["state"] == "error_recoverable"
    for estado in ("no_accounts", "error_recoverable"):
        assert estado != "updated"


# ── painel admin: falha ≠ contagem ──────────────────────────────────────────
# `of.erro` é a CONTAGEM de conexões em erro e o fail-soft devolvia `{"erro":
# "<mensagem>"}` na MESMA chave — o grid fazia `fInt("relation não existe")` =
# NaN, `NaN > 0` = false, e a caixa inteira ficava VERDE com tudo zerado.
# CONTROLE NEGATIVO: voltar o fail-soft para `{"erro": str(exc)}` deixa o
# segundo teste vermelho nas duas asserções.

def test_painel_of_traz_a_contagem_e_nao_a_chave_de_falha(user_id):
    import asyncio
    import core.admin_dashboard as ad

    # o painel inteiro depende das tabelas de admin, criadas preguiçosamente
    asyncio.run(ad.ensure_admin_tables())
    of = asyncio.run(ad._fetch_admin_overview_inner(days=1))["open_finance"]
    assert isinstance(of["erro"], int), "`erro` é contagem de conexões, não texto"
    assert "error" not in of, "sem falha, a chave de falha não existe"


def test_falha_ao_ler_contadores_aparece_como_falha(user_id, monkeypatch):
    import asyncio
    import core.admin_dashboard as ad
    import db.open_finance_state as ofs

    def _explode():
        raise RuntimeError('relation "open_finance_connections" does not exist')

    asyncio.run(ad.ensure_admin_tables())
    monkeypatch.setattr(ofs, "of_health_counters", _explode)
    of = asyncio.run(ad._fetch_admin_overview_inner(days=1))["open_finance"]

    assert "erro" not in of, "a falha não pode se disfarçar da contagem"
    assert "does not exist" in of["error"]


# ── RODADA 3: o que PARECE código, e o que é PII ────────────────────────────
# `_warning_codes` e a mensagem de erro da API compartilham `safe_code`. A regra
# é o teto de DÍGITOS: CPF e conta têm 11, código de verdade tem 3.
# CONTROLE NEGATIVO: subir `_CODE_MAX_DIGITS` para 11 deixa
# `test_code_com_cara_de_cpf_nao_passa` vermelho.

def test_codigos_reais_da_pluggy_passam():
    """CONTROLE POSITIVO: os três vistos em produção continuam entrando — apertar
    a regra até recusar código legítimo é pior que o vazamento que ela evita.
    (`CC_001` era recusado ANTES: faltava `_` na classe de caracteres.)"""
    from core.services.pluggy_health import safe_code
    for code in ("004", "CC_001", "INV_005", "MFA_TIMEOUT"):
        assert safe_code(code) == code, code


def test_code_com_cara_de_cpf_nao_passa():
    from core.services.pluggy_health import safe_code
    assert safe_code("123.456.789-01") == ""      # CPF
    assert safe_code("0001-12345-6") == ""        # agência-conta-dígito
    assert safe_code("Lucas Kuramoti") == ""      # nome (tem espaço)


def test_warnings_escalar_nao_derruba_o_sync():
    """Medido: `{"warnings": 7}` estourava `TypeError: 'int' object is not
    iterable` dentro de `derive_item_health`, chamado sem `try` pelo sync."""
    health = derive_item_health({"id": "x", "status": "UPDATED", "statusDetail": {
        "accounts": {"isUpdated": True, "warnings": 7}}})
    assert health["products"]["BANK"]["warnings"] == []


def test_warning_em_formato_de_string_vira_sentinela():
    health = derive_item_health({"id": "x", "status": "UPDATED", "statusDetail": {
        "accounts": {"isUpdated": True,
                     "warnings": ["Conta 0001-12345-6 de LUCAS KURAMOTI, CPF 123.456.789-01"]}}})
    assert health["products"]["BANK"]["warnings"] == ["invalid_warning"]


def test_erro_da_pluggy_nao_carrega_o_corpo_da_resposta():
    """RAIZ do vazamento: `str(exc)` vira `details` de `log_system_event`,
    PERSISTIDO em `system_event_logs` e lido pelo painel admin."""
    import httpx
    from core.services.pluggy import PluggyApiError, _raise_for_pluggy_response

    corpo = {"code": 429, "message": "Too many requests",
             "data": {"name": "Lucas Kuramoti", "documentNumber": "123.456.789-01",
                      "accountNumber": "0001-12345-6"}}
    resp = httpx.Response(429, json=corpo, request=httpx.Request("GET", "https://x/y"))

    with pytest.raises(PluggyApiError) as exc:
        _raise_for_pluggy_response(resp, "Falha ao consultar /investments na Pluggy")

    texto = str(exc.value)
    for pii in ("Kuramoti", "123.456.789-01", "0001-12345-6", "documentNumber"):
        assert pii not in texto, f"PII na mensagem: {pii} — {texto}"
    assert exc.value.status_code == 429, "o status continua acessível para decidir retry"
    assert "429" in texto


# ── PARIDADE Python × JS (§0.7) ──────────────────────────────────────────────
# A tabela de estados existe duas vezes porque o settings.html não importa
# Python. O CLAUDE.md manda: quando a duplicação é inevitável, um teste compara
# as duas (precedente: tests/test_phosphor_subset.py). Sem isto, um estado novo
# no backend degrada CALADO na tela — pílula "pending" e veredito genérico.

import pathlib
import re

_HTML = (pathlib.Path(__file__).resolve().parent.parent
         / "frontend" / "settings.html").read_text(encoding="utf-8")


def _bloco(nome: str, abre: str, fecha: str) -> str:
    m = re.search(rf"const {nome} = \{abre}(.*?)\{fecha};", _HTML, re.S)
    assert m, f"{nome} sumiu/foi renomeado no settings.html — a paridade ficou cega"
    return m.group(1)


def _js_pill_states() -> set[str]:
    return set(re.findall(r"(\w+)\s*:", _bloco("OF_PILL_CLASS", "{", "}")))


def _js_verdict_ok() -> set[str]:
    return set(re.findall(r'"(\w+)"', _bloco("OF_VERDICT_OK", "[", "]")))


def _js_verdict_states() -> set[str]:
    return set(re.findall(r'^\s*\["(\w+)"', _bloco("OF_VERDICT", "[", "]"), re.M))


def test_estados_do_backend_e_do_settings_html_sao_os_mesmos():
    from core.services.pluggy_health import _LABELS

    py, js = set(_LABELS), _js_pill_states()
    assert py == js, (
        f"estado só no Python (vira pílula 'pending' calada): {sorted(py - js)}; "
        f"estado só no JS (o backend nunca emite): {sorted(js - py)}")


def test_conjunto_verde_e_o_mesmo_nos_dois_lados():
    from core.services.pluggy_sync import _ESTADOS_OK

    py, js = set(_ESTADOS_OK), _js_verdict_ok()
    assert py == js, (
        f"verde só no Python (o usuário vê erro no sucesso): {sorted(py - js)}; "
        f"verde só no JS (toast verde em cima de falha): {sorted(js - py)}")


def test_todo_estado_nao_verde_tem_mensagem_de_veredito():
    """OF_VERDICT é a tabela de gravidade: estado sem linha lá cai no genérico."""
    from core.services.pluggy_health import _LABELS
    from core.services.pluggy_sync import _ESTADOS_OK

    esperado = set(_LABELS) - set(_ESTADOS_OK)
    js = _js_verdict_states()
    assert esperado == js, (
        f"estado sem mensagem em OF_VERDICT: {sorted(esperado - js)}; "
        f"mensagem para estado que não existe no backend: {sorted(js - esperado)}")
