"""
tests/test_portao_escritores_plano.py — o PORTÃO dos escritores do direito ao plano (PL-06 B6, §13 do plano).

O gatilho `trg_insight_aviso_direito` trata toda perda de direito como expiração natural: guarda a adesão ao
aviso de insight por até 1 hora, sem envio. Só o escritor que é um ATO (cancelamento, contestação, estorno,
operador) retira na hora, chamando `retirar_adesao_sem_direito`. Escritor novo esquecido cai no default
natural — seguro (nada é enviado), mas errado se ele for um ato. Este portão força a classificação: toda
função que escreve `plan`, `plan_expires_at`, `last_payment_status` ou `past_due_since` tem de estar em
`_ESCRITORES`, e as marcadas como explícitas têm de chamar a retirada.

Molde: `tests/test_portao_lacos_proativos.py`, que já falhou quatro vezes pelo ESCOPO da varredura. Por isso
varre o repositório inteiro (fora `tests/`) e lê nomes por `Name`, `Attribute` e passados POR REFERÊNCIA
(`asyncio.to_thread(update_user_plan, ...)`).

O QUE ELE NÃO ALCANÇA (cegueiras declaradas):
  1. SQL montado dinamicamente (f-string com o nome da coluna em variável): a regex lê constantes.
  2. A granularidade é a FUNÇÃO. `billing_webhook` é um escritor só, com dezenas de ramos; o motivo do
     `deleted` (`cancellation_details.reason`) está dentro dela. Quem prova o ramo é teste de comportamento
     (`tests/test_insights_adesao_perda.py`, M5 a M8).
  3. Escritor por um nome fora de `_CHAMADAS` e sem SQL literal passa.
  4. As razões de `_ESCRITORES` não são verificadas: classificar errado como "natural" silencia o portão.
  5. Tabela qualificada ou entre aspas: `update public.auth_accounts ...` e `update "auth_accounts" ...` não casam
     as regexes, que procuram `auth_accounts` solto. Nenhum escritor de produção escreve assim hoje.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

_CHAMADAS = {"update_user_plan", "update_user_plan_impl", "recompute_entitlement", "recompute_users",
             "revoke_grant", "set_payment_status", "set_payment_status_impl"}
_COLUNAS = r"\b(plan|plan_expires_at|last_payment_status|past_due_since)\s*="
_SQL = re.compile(r"update\s+auth_accounts\b.*?\bset\b.*?" + _COLUNAS, re.I | re.S)
# `insert ... on conflict ... do update set <colunas>` também escreve o direito (G09 do Tester r4).
_UPSERT = re.compile(r"insert\s+into\s+auth_accounts\b.*?\bon\s+conflict\b.*?\bdo\s+update\s+set\b.*?" + _COLUNAS,
                     re.I | re.S)
_RETIRA = "retirar_adesao_sem_direito"
# Quem retira por um helper local: o helper tem de chamar a retirada (`test_os_helpers_de_retirada_retiram`).
_HELPERS_DE_RETIRADA = {"core/admin_dashboard.py::_retirar_aviso_do_admin"}

_ESCRITORES = {
    "core/admin_dashboard.py::set_account_plan": "explicito: o operador decide; retira na mesma transação (_retirar_aviso_do_admin)",
    "frontend/finance_bot_websocket_custom.py::billing_webhook":
        "explicito: o `deleted` com `cancellation_requested`/`payment_disputed` retira (os outros ramos são naturais)",
    "core/services/pix_drain_effects.py::_revoke": "explicito: estorno total do Pix (P10 (b)); a baixa desfeita é natural",
    "core/services/billing_access.py::recompute_entitlement": "natural: projeção dos grants; quem decide é o chamador",
    "core/services/billing_access.py::reprojetar_grants_recentes": "natural: a varredura é o relógio",
    "core/services/family_access.py::grant_member": "natural: concede",
    "core/services/family_access.py::_revogar": "natural: perda do membro é ato do titular, não do membro",
    "core/services/pix_baixa_desfeita.py::reconciliar_grant": "natural: reconcede",
    "core/services/pix_drain_effects.py::_grant": "natural: concede",
    "db/dunning.py::claim_past_due_since": "natural: abre a carência",
    "db/dunning.py::clear_past_due_since": "natural: fecha o relógio",
    "db/dunning.py::encerrar_ciclo_de_atraso": "natural: fecha o relógio",
    "db/family.py::remove_member": "natural para o membro",
    "db/family.py::leave_family": "explicito: sair é ato do membro; retira sem o recurso, preserva com plano próprio",
    "db/family.py::dissolve_group": "natural para os membros",
    "db/family_grants.py::recompute_users": "natural: reprojeção",
    "db/privacy.py::delete_user_data": "natural: a linha some na exclusão",
    "db/reports.py::update_user_plan": "natural: primitivo; o chamador decide",
    "db/reports.py::set_payment_status": "natural: primitivo; o chamador decide",
    "db_support.py::update_user_plan_impl": "natural: primitivo",
    "db_support.py::set_payment_status_impl": "natural: primitivo",
    "frontend/finance_bot_websocket_custom.py::_materializar_assinatura":
        "explicito: troca de preço da MESMA assinatura para tier sem `insights` retira; renovação e alta são naturais",
}


def _escritores() -> dict[str, set[str]]:
    achados: dict[str, set[str]] = {}
    for caminho in sorted(RAIZ.rglob("*.py")):
        partes = set(caminho.relative_to(RAIZ).parts)
        if {".venv", "__pycache__", "node_modules", ".claude", "tests", "harness_tests"} & partes:
            continue
        try:
            arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for fn in ast.walk(arvore):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nomes = _escreve_o_direito(fn)
                if nomes is not None:
                    achados[f"{caminho.relative_to(RAIZ).as_posix()}::{fn.name}"] = nomes
    return achados


def _escreve_o_direito(fn) -> set[str] | None:
    """Os nomes chamados pela função, se ela escreve o direito; None se não escreve. O critério do portão, num lugar só."""
    nomes, sql = set(), False
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            for alvo in (n.func, *n.args):   # a função passada por referência também conta
                if isinstance(alvo, ast.Name):
                    nomes.add(alvo.id)
                elif isinstance(alvo, ast.Attribute):
                    nomes.add(alvo.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and (
                _SQL.search(n.value) or _UPSERT.search(n.value)):
            sql = True
    return nomes if nomes & _CHAMADAS or sql else None


def test_todo_escritor_do_direito_esta_classificado():
    achados = _escritores()
    assert set(achados) == set(_ESCRITORES), (
        f"novos: {sorted(set(achados) - set(_ESCRITORES))}; sumiram: {sorted(set(_ESCRITORES) - set(achados))}. "
        "Escritor novo do direito: é ato (chame `retirar_adesao_sem_direito` no fim, pelo estado final) ou "
        "expiração natural? Classifique em _ESCRITORES.")


def _chamadas_de(chave: str) -> set[str]:
    arquivo, nome = chave.split("::")
    arvore = ast.parse((RAIZ / arquivo).read_text(encoding="utf-8"))
    fn = next(f for f in ast.walk(arvore) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == nome)
    return {n.func.id if isinstance(n.func, ast.Name) else n.func.attr for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))}


def test_os_explicitos_retiram():
    achados = _escritores()
    helpers = {h.split("::")[1] for h in _HELPERS_DE_RETIRADA}
    faltam = [k for k, v in _ESCRITORES.items()
              if v.startswith("explicito") and not ({_RETIRA} | helpers) & achados.get(k, set())]
    assert faltam == []


def test_os_helpers_de_retirada_retiram():
    assert [h for h in _HELPERS_DE_RETIRADA if _RETIRA not in _chamadas_de(h)] == []


def test_positivo_a_varredura_enxerga_por_referencia_e_por_sql():
    """Sem isto o portão passaria vazio: um caminho por referência e um por SQL têm de aparecer."""
    achados = _escritores()
    assert "db/dunning.py::clear_past_due_since" in achados                      # só SQL literal
    assert "update_user_plan" in achados["frontend/finance_bot_websocket_custom.py::billing_webhook"]  # to_thread(f, ...)
    upsert = ast.parse("def f(cur, uid):\n    cur.execute('insert into auth_accounts (user_id) values (%s) "
                       "on conflict (user_id) do update set plan = excluded.plan', (uid,))\n").body[0]
    assert _escreve_o_direito(upsert) is not None, "o upsert (G09 do Tester r4) não é visto como escritor"
