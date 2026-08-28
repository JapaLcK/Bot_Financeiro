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


# ── ONDA 2: item saudável na Pluggy NÃO é sync concluído ────────────────────
# O job de saúde só faz `GET /items` e grava `health` com `ok=None` — nunca toca
# em `last_sync_at`. O ramo do health devolvia "updated" sem olhar o sucesso,
# então um banco recém-conectado/reconectado ficava "Tudo em dia!" com
# "Última sync: pendente" escrito na linha logo abaixo, na mesma tela.
# CONTROLE NEGATIVO: tirar a branch `last_sync_at is None` do `out()` deixa os
# dois primeiros testes deste bloco vermelhos.
# CONTROLE POSITIVO: `test_tudo_atualizado_vira_updated` e
# `test_motivo_ok_ou_vazio_continua_verde` (mesmo health, COM last_sync_at)
# continuam verdes — a guarda não recusa quem sincronizou de verdade.

_SAUDAVEL = {"status": "UPDATED", "executionStatus": "SUCCESS",
             "statusDetail": {"accounts": {"isUpdated": True}}}


def test_item_saudavel_sem_sync_nunca_e_atualizado():
    health = derive_item_health(_SAUDAVEL, now=AGORA)
    ui = connection_ui_state({"status": "ACTIVE", "health": health, "last_sync_at": None})
    assert ui["state"] == "updating", "item vivo não é espelho fresco"
    assert ui["detail"] == "Ainda não sincronizou"


def test_nunca_sincronizou_vale_para_todo_status_local_verde():
    """Reconexão: o upsert escreve o `status` que a Pluggy mandou e zera health.
    Nenhum desses status pode virar verde enquanto não houver sync.

    Só o ramo COM health entra aqui: sem health, as duas linhas que a Onda 1 já
    tinha no fim da função respondem igual — incluir `h=None` seria um caso que
    passa na base e não mede a guarda."""
    health = derive_item_health(_SAUDAVEL, now=AGORA)
    for status in ("ACTIVE", "UPDATED", "UPDATING", "LOGIN_ERROR"):
        ui = connection_ui_state({"status": status, "health": health, "last_sync_at": None})
        assert ui["state"] != "updated", status
        assert ui["detail"] == "Ainda não sincronizou"


def test_sem_sync_nao_rebaixa_estado_que_ja_era_pior():
    """A guarda mira SÓ o verde: quem já tinha veredito próprio o mantém, senão
    ela apagaria "Refaça a conexão" e "Reautorize o banco" de toda conexão nova."""
    doente = derive_item_health({"status": "LOGIN_ERROR"}, now=AGORA)
    casos = [
        ({"status": "ERROR", "status_reason": "item_missing"}, "item_missing"),
        ({"status": "PAUSED"}, "paused"),
        ({"status": "DELETED"}, "removed"),
        ({"status": "ACTIVE", "health": doente}, "needs_user_action"),
        ({"status": "ERROR"}, "error_recoverable"),
    ]
    for linha, esperado in casos:
        assert connection_ui_state({**linha, "last_sync_at": None})["state"] == esperado, linha


def test_sem_sync_nao_engole_o_motivo_do_default_seguro():
    """O caso que os 5 acima NÃO alcançam: todos eles saem da função antes de
    chegar em `out("updated")`, então nenhum passa pelo ramo onde a guarda pode
    causar dano. Aqui o item está SAUDÁVEL — o veredito seria verde — e é o
    `status_reason` que tem de falar mais alto que "Ainda não sincronizou".

    Alcançável: conexão nova cujo 1º sync não espelhou nada grava
    `mark_sync_result(ok=False, status="ACTIVE", status_reason="no_accounts")`,
    que NÃO carimba last_sync_at. Medido: com a guarda ANTES do default seguro,
    os três casos abaixo viram "Atualizando…/Ainda não sincronizou" — "Sem dados"
    some da tela e a pílula do `read_failed` cai de vermelho (error) para âmbar
    (pending), ver OF_PILL_CLASS em frontend/settings.html."""
    saudavel = derive_item_health(_SAUDAVEL, now=AGORA)
    for motivo, esperado in (("no_accounts", "no_accounts"),
                             ("read_failed", "error_recoverable"),
                             ("motivo_que_ninguem_escreveu_ainda", "error_recoverable")):
        ui = connection_ui_state({"status": "ACTIVE", "status_reason": motivo,
                                  "health": saudavel, "last_sync_at": None})
        assert ui["state"] == esperado, f"{motivo} perdeu o veredito por falta de sync"
        assert ui["detail"] != "Ainda não sincronizou", motivo


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


# ── ONDA 3, item 3: a taxonomia da Pluggy deixou de ser um chute ─────────────
# A Onda 1 registrou "um `item_status` que não esteja em `_NEEDS_USER` nem seja
# ERROR cai adiante como saudável" como LIMITAÇÃO CONHECIDA, porque a taxonomia
# do provedor não era observável daqui e adivinhá-la seria pior que a lacuna.
#
# A evidência apareceu, e é documentação oficial — não log nosso, não payload
# observado: https://docs.pluggy.ai/docs/item-lifecycle enumera o campo `status`
# do Item em CINCO valores, e só esses cinco:
#
#   UPDATED             sucesso
#   UPDATING            transitório
#   WAITING_USER_INPUT  transitório, mas depende do usuário (MFA)
#   LOGIN_ERROR         erro, exige credencial nova
#   OUTDATED            erro, retentável
#
# O que a mesma página mostra e é a armadilha aqui: `ERROR`, `MERGE_ERROR`,
# `INVALID_CREDENTIALS`, `SITE_NOT_AVAILABLE` e companhia são valores de
# `executionStatus`, um campo DIFERENTE — não são status de Item. Por isso a
# Onda 3 não acrescenta MERGE_ERROR a lista nenhuma: ele nunca chega em
# `health["item_status"]`, que sai de `item.get("status")`
# (`pluggy_health.py:196`). Colocá-lo lá seria exatamente o "inventar status
# externo" que esta onda proíbe.
#
# Este teste não muda comportamento: ele PRENDE o que já é verdade, agora que dá
# para dizer que a lista está fechada. Se a Pluggy publicar um sexto status, ele
# não falha sozinho — quem falha é a revisão. O que ele pega é a regressão:
# alguém tirar um dos cinco de `_NEEDS_USER`/`_UPDATING` por engano.
#
# CONTROLE NEGATIVO (medido): tirar "OUTDATED" de `_NEEDS_USER` deixa 1 vermelho
# (o caso OUTDATED); tirar "UPDATING" de `_UPDATING` deixa 2 (o caso UPDATING e o
# `test_updating_e_updating`, que já existia).
# CONTROLE POSITIVO: o caso UPDATED prova que a guarda não recusa tudo.

_STATUS_DOCUMENTADOS = [
    # (item_status, (status, reason) esperados do resolve, estado da tela)
    ("UPDATED",            ("ACTIVE", ""), "updated"),
    ("UPDATING",           ("ACTIVE", ""), "updating"),
    ("WAITING_USER_INPUT", ("ERROR",  ""), "needs_user_action"),
    ("LOGIN_ERROR",        ("ERROR",  ""), "needs_user_action"),
    ("OUTDATED",           ("ERROR",  ""), "needs_user_action"),
    # O SEXTO, que a tabela do `item-lifecycle` não lista mas os payloads de
    # `docs/connect-an-account` (Safra, Banco Inter PF) e `docs/sandbox` (QR
    # Login) trazem: `"status": "WAITING_USER_ACTION"`. Autorizar dispositivo /
    # ler QR no app do banco. Estava em `_UPDATING` e a tela dizia
    # "Atualizando…" numa conexão que só anda se a pessoa AGIR.
    # CONTROLE NEGATIVO: devolvê-lo a `_UPDATING` deixa este caso vermelho.
    ("WAITING_USER_ACTION", ("ERROR", ""), "needs_user_action"),
]


@pytest.mark.parametrize("item_status, resolve_esperado, estado_esperado",
                         _STATUS_DOCUMENTADOS)
def test_os_status_de_item_da_pluggy_estao_cobertos(
        item_status, resolve_esperado, estado_esperado):
    """Os valores conhecidos do campo `status` do Item, ponta a ponta: o que o
    backend grava (`resolve_connection_state`) e o que a tela mostra
    (`connection_ui_state`).

    O par `(status, status_reason)` inteiro, não só o `status`: o módulo declara
    que os dois são UM estado só, e afirmar metade deixaria passar um caminho
    que devolvesse `ERROR, "item_missing"` para um `LOGIN_ERROR`."""
    health = {"item_status": item_status, "products": {}, "stale_products": []}

    assert resolve_connection_state(
        health=health, has_data=True,
        leitura_completa=True) == resolve_esperado, f"{item_status} → estado local"
    status = resolve_esperado[0]

    ui = connection_ui_state({
        "status": status, "status_reason": "", "health": health,
        # sync real posterior à autorização atual: sem isto o `sem_sync` da
        # Onda 2 devolve "updating" para todo mundo e o teste não mede nada.
        "last_sync_at": AGORA, "reconnected_at": None,
    })
    assert ui["state"] == estado_esperado, f"{item_status} → tela"


@pytest.mark.parametrize("item, esperado, rotulo", [
    ({"status": "UPDATED", "executionStatus": "MERGE_ERROR"}, "UPDATED",
     "com os dois campos, vence o `status`"),
    # O caso que discrimina de verdade: SEM `status`, um `or` para o
    # `executionStatus` (o padrão que `db/open_finance.py` usa no upsert)
    # deixaria `MERGE_ERROR` entrar como se fosse status de Item. A versão
    # anterior deste teste mandava `status: "UPDATED"` e por isso passava
    # mesmo COM a mutação aplicada — medido, 4957 verdes com o `or` no lugar.
    # Com estes dois casos, a mesma mutação dá 2 vermelhos.
    ({"executionStatus": "MERGE_ERROR"}, None, "sem `status`, NÃO cai no outro campo"),
    ({"status": "", "executionStatus": "SITE_NOT_AVAILABLE"}, None,
     "`status` vazio também não cai"),
])
def test_execution_status_de_erro_nao_e_status_de_item(item, esperado, rotulo):
    """`MERGE_ERROR` e irmãos são `executionStatus`, não `status` do Item.

    Prende a decisão de NÃO os mapear: eles não chegam a `health["item_status"]`
    porque `derive_item_health` lê `item["status"]` e SÓ ele."""
    saude = derive_item_health({"id": "item-x", **item}, now=AGORA)
    assert saude["item_status"] == esperado, rotulo
    assert saude["execution_status"] == ((item.get("executionStatus") or "").upper() or None)


# ── ONDA 3: os membros de `executionStatus` nos conjuntos são LOAD-BEARING ───
# `_NEEDS_USER` tem `INVALID_CREDENTIALS` e `_UPDATING` tem `CREATED` — os dois
# são `executionStatus`, não status de Item, e por isso NUNCA chegam pelo
# `health["item_status"]`. Parecem resíduo. Não são: `connection_ui_state`
# compara os mesmos conjuntos com o `status` LOCAL da conexão, e o upsert de
# `db/open_finance.py` grava `item.get("status") or item.get("executionStatus")`
# ali. Sem eles, uma conexão em `INVALID_CREDENTIALS` deixa de dizer "Ação
# necessária" e uma em `CREATED` deixa de dizer "Atualizando…".
#
# Medido antes deste teste existir: tirar os dois dos conjuntos deixava a suíte
# INTEIRA verde (4957 passed). Era instrução de deleção sem rede.
#
# CONTROLE NEGATIVO: tirar `INVALID_CREDENTIALS` de `_NEEDS_USER` ou `CREATED`
# de `_UPDATING` deixa um caso vermelho cada.

# ── Codex #166: "Ação necessária" não diz QUAL ação ─────────────────────────
# `_NEEDS_USER` é um balde só, e o detalhe fixo do estado é "Reautorize o banco".
# Para `WAITING_USER_ACTION` isso manda a pessoa REFAZER a conexão quando o que
# ela precisa é autorizar o dispositivo / ler o QR antes do `expiresAt` — ou
# seja, a instrução errada faz perder exatamente a janela que importa.
#
# CONTROLE NEGATIVO: tirar a entrada de `_DETALHE_POR_STATUS` deixa os dois
# casos abaixo vermelhos (o detalhe volta a ser "Reautorize o banco").
# CONTROLE POSITIVO: `LOGIN_ERROR` prova que os OUTROS membros do balde seguem
# com o detalhe compartilhado — senão a exceção teria virado regra.

@pytest.mark.parametrize("origem", ["health", "status_local"])
@pytest.mark.parametrize("item_status, detalhe_esperado", [
    ("WAITING_USER_ACTION", "Autorize o acesso no app do banco"),
    ("LOGIN_ERROR", "Reautorize o banco"),
])
def test_detalhe_da_acao_necessaria_e_especifico_quando_precisa(
        origem, item_status, detalhe_esperado):
    """Os dois ramos: com `health` medido e caindo no `status` LOCAL — o upsert
    grava `item.get("status") or item.get("executionStatus")`, então o valor
    chega pelos dois caminhos."""
    linha = {"status_reason": "", "last_sync_at": AGORA, "reconnected_at": None}
    if origem == "health":
        linha |= {"status": "ERROR",
                  "health": {"item_status": item_status, "products": {},
                             "stale_products": []}}
    else:
        linha |= {"status": item_status, "health": None}

    ui = connection_ui_state(linha)
    assert ui["state"] == "needs_user_action"
    assert ui["label"] == "Ação necessária"
    assert ui["detail"] == detalhe_esperado


@pytest.mark.parametrize("status_local, estado_esperado", [
    ("INVALID_CREDENTIALS", "needs_user_action"),
    ("CREATED", "updating"),
])
def test_execution_status_no_status_local_ainda_e_lido(status_local, estado_esperado):
    ui = connection_ui_state({
        "status": status_local, "status_reason": "", "health": None,
        "last_sync_at": AGORA, "reconnected_at": None,
    })
    assert ui["state"] == estado_esperado
