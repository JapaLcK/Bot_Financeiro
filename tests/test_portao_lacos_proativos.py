"""
tests/test_portao_lacos_proativos.py — o PORTÃO da categoria "laço proativo".

Arquivo próprio porque `tests/test_relatorios_param_no_corte.py` passou de 350
linhas (`tests/test_max_lines_python.py`) e porque o assunto é outro: lá é o
COMPORTAMENTO do `filtrar_por_acesso` (quem sai do lote, quem fica), aqui é o
portão que diz se a categoria continua fechada.

**Ele já falhou QUATRO vezes, sempre pelo mesmo mecanismo — o escopo da
varredura, nunca o predicado:**

  1. CONTAVA ocorrências de `filtrar_por_acesso(` por arquivo, e estava
     invertido: laço novo sem filtro mantinha a contagem e ficava verde;
  2. enumerava, mas só DOIS arquivos — `open_finance_proactive.py` não era
     olhado, e mandava salário e reconexão pra quem foi cortado;
  3. varria a árvore, mas com três prefixos de listador escolhidos por serem os
     que alguém já tinha visto — `list_agents_pending_email` passou, e o laço
     dos agentes mandou relatório financeiro por e-mail;
  4. lia só `ast.Call`, e `run_in_executor(None, fn, ...)` passa a função POR
     REFERÊNCIA — `engagement_scheduler._check_and_send` mandava dica e insight
     pra conta cortada, 1×/dia, sem flag que o segurasse. Era cegueira de
     FORMA, não de vocabulário: com população muito mais larga o conjunto saía
     idêntico, porque não havia chamada nenhuma para casar.

Se você veio acrescentar um quinto remendo, o padrão está aí.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


# Enumerador de POPULAÇÃO. Era `^list_users$|^list_users_with_|^list_\w*_user_ids$`
# — três prefixos escolhidos por serem os que eu já tinha visto — e deixou
# passar `list_agents_pending_email`, o laço que manda relatório financeiro por
# e-mail para quem foi cortado. **É a quinta vez neste PR que o furo está no
# ESCOPO da varredura**, e sempre pelo mesmo mecanismo: enumerar as formas
# conhecidas de dizer uma coisa em vez de enumerar a categoria.
#
# Hoje é `^list_` e nada mais. Medido 2026-09-11: o alargamento custa ZERO
# ruído — passou de 7 para 10 funções, e as TRÊS novas são membros legítimos da
# categoria (uma era o defeito, duas são isenções com razão). Quem discrimina
# não é mais o nome do listador; é o segundo termo, o ENVIO.
#
# `^get_\w*users` entrou depois, e só teve efeito porque o enumerador passou a
# ler `run_in_executor` (ver o coletor): `engagement_scheduler._check_and_send`
# chama `db.get_users_for_engagement` POR REFERÊNCIA, então antes não havia
# chamada nenhuma para casar e nenhum vocabulário o alcançaria. Medido: com as
# duas coisas juntas o conjunto vai de 10 para 14; acrescentar `iter_`/`fetch_`
# não muda nada, então o critério para aqui.
_POPULACAO = re.compile(r"^list_|^get_\w*users")

# Enviar por qualquer canal. `send` cru é o Discord (`await user.send(...)`) e o
# prefixo `_send_` pega os wrappers locais (`_send_periodic_template`).
_ENVIO = {"send", "send_template", "send_text", "send_message"}


def _envia(chamadas: set[str]) -> bool:
    """Manda alguma coisa para fora, por qualquer canal.

    `send_*_email` entra porque foi por aí que o laço dos agentes escapou: ele
    não chama `send_template` nem `send_text` — chama `send_agent_report_email`.
    """
    return (bool(chamadas & _ENVIO)
            or any(c.startswith("_send_")
                   or (c.startswith("send_") and c.endswith("_email"))
                   for c in chamadas))


# Laços proativos que NÃO passam por `filtrar_por_acesso`, cada um com a razão
# escrita — o análogo do `_ISENTAS_COM_RAZAO` de
# `tests/test_portao_pendencias_interceptadas.py`. Sem este escape, alargar o
# critério obrigaria a gatear coisas que não devem ser gateadas, e a saída fácil
# seria apagar o portão em vez de declarar a entrada.
_ISENTOS_COM_RAZAO = {
    "core/services/engagement_scheduler.py::_check_free_upgrade_nudge":
        "WIN-BACK, mesma família do downsell: o funil é `plan = 'free'` ativo, "
        "que DEPOIS DO CORTE é exatamente a população sem acesso — filtrar por "
        "acesso o mataria inteiro. Dormente hoje "
        "(`FREE_UPGRADE_NUDGE_ENABLED` default off), o que não dispensa a "
        "decisão. RESSALVA REGISTRADA, e é de COPY e não de gate: "
        "`send_free_upgrade_nudge_email` promete 'testar 15 dias grátis', e "
        "para o ex-assinante cujo telefone já queimou o trial isso é a mesma "
        "promessa falsa que este PR tirou da /precos e do downsell. Ligar o "
        "flag sem reescrever a copy reabre aquele defeito.",
    "scripts/send_update_email.py::main":
        "Não é laço automático: é script de BROADCAST rodado à mão por um "
        "operador, com `--dry-run` e `--test`. Quem decide o público é a pessoa "
        "que roda, não um scheduler — gatear aqui não impede engano e dá "
        "sensação falsa de cobertura. O irmão "
        "`scripts/send_update_whatsapp.py` é o mesmo caso e nem aparece na "
        "varredura, porque enumera por SQL cru (ver os limites, abaixo).",
    "core/services/trial_downsell.py::send_trial_downsell_emails":
        "É o e-mail de WIN-BACK: existe PARA falar com quem ficou sem plano no "
        "fim do teste, e filtrar por acesso o mataria inteiro. O próprio laço "
        "já filtra o oposto (`get_plan_tier(user_id) != 'free'` → pula), e a "
        "copy dele é presa por "
        "`tests/test_billing_email_downsell_nao_promete_gratis.py`.",
    "core/services/payment_reminder_wa.py::_wa_lembrete":
        "Não é laço de população: roda POR usuário já escolhido pelo funil do "
        "lembrete, e tem gate próprio e mais estrito — "
        "`db.dunning.ciclo_de_atraso_aberto`, lido fresco imediatamente antes "
        "do envio (célula 31 de `docs/dunning_estados_eventos.md`).",
}


def _lacos_proativos() -> dict[str, bool]:
    """{"arquivo::função": ela chama `filtrar_por_acesso`} — REPOSITÓRIO INTEIRO.

    Um laço proativo é uma função que ENUMERA uma população de usuários **e**
    ENVIA alguma coisa. Os dois termos são necessários: só o enumerador pegaria
    `piggy_agents.run_*_once` e `investment_scheduler`, que varrem população e
    não mandam nada (escrevem em tabela); só o envio pegaria
    `payment_reminder_wa._wa_lembrete`, que manda mas é POR usuário já
    escolhido, com gate próprio (`ciclo_de_atraso_aberto`).

    **Varre o repositório, e não uma lista de arquivos**, porque a lista de
    arquivos foi a falha anterior: o portão cobria `wa_app.py` e
    `reports_daily.py`, e `core/services/open_finance_proactive.py` — dois laços
    mandando salário e pedido de reconexão a quem foi cortado — simplesmente não
    era olhado. Antes disso o portão CONTAVA ocorrências e estava INVERTIDO
    (laço novo sem filtro mantinha a contagem e ficava verde). Terceira mordida
    da mesma categoria neste PR; o remédio do §2 é enumerar, e enumerar TUDO.

    O QUE ELE NÃO ALCANÇA (§3: "que classe de bug esta verificação nunca
    pegaria?") — e as três já custaram caro, então ficam escritas:

    1. **Enumeração por SQL CRU.** `scripts/send_update_whatsapp.py` monta o
       público com `cur.execute(...)` e não aparece aqui; nenhum vocabulário
       alcança, porque não há nome de função para casar. É o irmão do
       `send_update_email.py`, que só entra por acidente de outro nome.
    2. **Envio por um nome que não case `_ENVIO`.** Um `notificar()` ou
       `disparar()` novo passa.
    3. **As RAZÕES do `_ISENTOS_COM_RAZAO` nunca são lidas por asserção
       nenhuma** — são valores de dict. Pendurar um laço ali silencia as DUAS
       asserções de uma vez, e nada verifica se a razão é verdadeira. Limite
       herdado do portão irmão (`test_portao_pendencias_interceptadas.py`), e
       nesta rodada ele foi de fato exercido: duas entradas novas.

    AST e não regex porque a forma do laço varia — `_periodic_report_tick` itera
    `weekly_users | monthly_users`, e nenhum `for ... in list_users_with_*(...)`
    casaria com ele. Nomes de chamada por `Name` **e** por `Attribute`, senão
    `user.send(...)` do Discord não conta como envio.
    """
    achados: dict[str, bool] = {}
    for caminho in sorted(RAIZ.rglob("*.py")):
        partes = caminho.relative_to(RAIZ).parts
        if {".venv", "__pycache__", "node_modules", ".claude", "tests"} & set(partes):
            continue
        try:
            arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for fn in ast.walk(arvore):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            chamadas = set()
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                if isinstance(n.func, ast.Name):
                    chamadas.add(n.func.id)
                elif isinstance(n.func, ast.Attribute):
                    chamadas.add(n.func.attr)
                # `await loop.run_in_executor(None, fn, ...)` passa a função POR
                # REFERÊNCIA: `fn` não é um `ast.Call` e some do conjunto. Era
                # cegueira de FORMA, não de vocabulário — medido, alargar a
                # população para `list_|listar_|select_|fetch_|get_users|get_all|
                # buscar_|iter_|all_|find_|query_` não mudava nada, porque não há
                # chamada nenhuma para casar. Foi por aqui que
                # `engagement_scheduler._check_and_send` mandou dica e insight
                # para conta cortada, 1×/dia, sem flag que o segurasse.
                if getattr(n.func, "attr", None) == "run_in_executor" and len(n.args) >= 2:
                    alvo = n.args[1]
                    if isinstance(alvo, ast.Name):
                        chamadas.add(alvo.id)
                    elif isinstance(alvo, ast.Attribute):
                        chamadas.add(alvo.attr)
            if any(_POPULACAO.match(c) for c in chamadas) and _envia(chamadas):
                rel = "/".join(caminho.relative_to(RAIZ).parts)
                achados[f"{rel}::{fn.name}"] = "filtrar_por_acesso" in chamadas
    return achados


_LACOS_ESPERADOS = {
    "adapters/whatsapp/wa_app.py::_daily_report_tick",
    "adapters/whatsapp/wa_app.py::_periodic_report_tick",
    "adapters/whatsapp/wa_app.py::_bill_reminder_tick",
    "core/reports/reports_daily.py::_daily_report_discord",
    "core/reports/reports_daily.py::_periodic_reports_discord",
    "core/services/open_finance_proactive.py::run_salary_notifications",
    "core/services/open_finance_proactive.py::run_reconnect_notifications",
    "core/services/piggy_agents.py::run_agent_emails_once",
    "core/services/engagement_scheduler.py::_check_and_send",
    "core/services/payment_reminder.py::check_payment_reminder",
    *_ISENTOS_COM_RAZAO,
}


def test_todo_laco_proativo_passa_pelo_helper():
    """É a CATEGORIA que tem de estar fechada, não a instância (§2).

    Duas asserções, e as duas são necessárias:

    • **cada laço filtra** — o irmão esquecido;
    • **o conjunto é o esperado** — o laço NOVO, e o ARQUIVO novo. Sem esta,
      um laço que não filtrasse podia simplesmente não ser olhado, que é a
      falha que este teste já teve duas vezes.

    Quem adicionar laço proativo legítimo tem DUAS linhas para mexer: a chamada
    do filtro e esta lista. É de propósito — a lista é a declaração de que
    alguém olhou, e ela é o único lugar do repositório onde a categoria inteira
    está escrita.
    """
    achados = _lacos_proativos()
    sem_filtro = sorted(n for n, filtra in achados.items()
                        if not filtra and n not in _ISENTOS_COM_RAZAO)
    assert not sem_filtro, (
        f"{sem_filtro} enumera(m) população de usuários e envia(m) sem passar por "
        "`filtrar_por_acesso` — manda(m) mensagem para quem foi cortado. Se o "
        "laço tiver razão para não filtrar, ela vai POR ESCRITO em "
        "`_ISENTOS_COM_RAZAO`, não por omissão.")
    assert set(achados) == _LACOS_ESPERADOS, (
        f"laços proativos {sorted(achados)}, esperado {sorted(_LACOS_ESPERADOS)} — "
        "laço novo entra nesta lista junto com o filtro, não depois")
