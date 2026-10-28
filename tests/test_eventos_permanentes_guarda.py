"""Guarda (AST, sem banco) da retenção de `system_event_logs`: quem lê linha mais
velha que `RETENCAO_MINIMA_DIAS` lê evento de `EVENTOS_PERMANENTES`
(core/services/log_retention.py), senão a poda apaga o marcador e o dedup volta.

  G1  todo `recent_event_exists`: janela <= limiar, evento permanente, ou janela à mão
      em `JANELA_RESOLVIDA_A_MAO`. Reprova referência não reconhecida (alias, `partial`,
      atribuição) e janela que não resolve (parâmetro, `*args`/`**kw`, string, nome
      reescrito ou reimportado no módulo, local de função envolvente).
  G2/G3  controles negativo e positivo, pelo MESMO `_achados` do G1.
  G4  lista à mão sem órfã; todo `_fire_email(...)` com `dedup_days` <= limiar.
  G5  leitor SQL novo de `system_event_logs`+`event_type` reprova até entrar em
      `LEITORES_SQL`; os "sem janela" leem só permanentes.
  G6  `EVENTOS_FALHA_DELETE` <= `EVENTOS_PERMANENTES`.
  G7  permanente digitado à mão existe como string no código (§0.7). Os de
      `EVENTOS_FALHA_DELETE` vêm da tupla em `db/open_finance_state.py` e passam sempre.

CLASSES CEGAS:
  1. Referência dinâmica (`getattr`, nome em string, `importlib`).
  2. Wrapper com janela por parâmetro reprova, e a saída é a lista à mão: revisão
     humana (hoje só `_fire_email`, com os chamadores conferidos no G4).
  3. G5: SQL sem `system_event_logs` e `event_type` na MESMA constante ou f-string
     (`+`, `.format`, `%`, `sql.Identifier`, tabela em variável) e literal `bytes`.
  4. G5 só força CLASSIFICAR o leitor; não prova que a classificação está certa.
  5. Consulta em `docs/*.md`, script fora do git, consulta manual no banco.
  6. Janela de env em runtime reprova, mas a lista à mão não sabe o valor de produção.
  7. Constante reescrita por `global X`, `setattr` ou outro módulo; parâmetro de
     `lambda`, captura de `match/case` e nome do corpo de `class` não contam como local.
  8. Universo = `git ls-files`: arquivo novo só entra depois do `git add` (no CI, tudo).
"""
from __future__ import annotations

import ast
import re
from functools import cache

from core.services.log_retention import EVENTOS_PERMANENTES, RETENCAO_MINIMA_DIAS
from db.open_finance_diagnostico import EVENTOS_FALHA_DELETE
from test_max_lines_python import RAIZ, _rastreados

LIMIAR = RETENCAO_MINIMA_DIAS
REF = "recent_event_exists"

JANELA_RESOLVIDA_A_MAO = {  # janela = parâmetro `dedup_days`; chamadores conferidos no G4
    ("frontend/finance_bot_websocket_custom.py", "_fire_email"): "parâmetro dedup_days"}

# (arquivo, função) -> eventos lidos SEM janela (têm de ser permanentes), ou None
# quando a janela é curta ou perder a linha velha não muda nada para o usuário.
LEITORES_SQL: dict[tuple[str, str], tuple[str, ...] | None] = {
    ("db/open_finance_diagnostico.py", "itens_com_remocao_remota_falha"): EVENTOS_FALHA_DELETE,
    # A limpeza do texto cru: reescreve, não apaga, e só os permanentes.
    ("core/services/log_retention.py", "limpar_texto_das_falhas_de_remocao"): EVENTOS_FALHA_DELETE,
    # Sem janela, mas a linha só deduplica o LOG: o aviso ao número sem conta sai
    # em toda mensagem (`_send_no_account_notice`, wa_runtime.py). Sem a linha, o
    # efeito é uma linha de log a mais, nunca uma mensagem a mais (decisão T1).
    ("adapters/whatsapp/wa_runtime.py", "_autolink_warning_already_sent"): None,
    ("core/system_event_log.py", REF): None,  # coberto pelo G1
    ("core/services/security_alerts.py", "_detect_and_maybe_alert"): None,  # cooldown em minutos
    ("db/open_finance_state.py", "of_health_counters"): None,  # 24 h
    # Painel: até 30 d; `last_*_at` fica vazio se o evento não ocorre há > 90 d.
    ("core/admin_dashboard.py", "_fetch_admin_overview_inner"): None,
    ("core/admin_dashboard.py", "fetch_admin_user_detail"): None,  # últimos 15
    ("db/privacy.py", "build_user_export_zip"): None,  # exportação: 90 d + permanentes
}


@cache
def _producao() -> dict[str, ast.Module]:
    return {c: ast.parse((RAIZ / c).read_text(encoding="utf-8"), c)
            for c in _rastreados() if not c.startswith(("tests/", "harness_tests/"))}


_ESCOPO_NOVO = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
_SEM_VALOR = ast.Constant(value=None)  # escrita cujo valor a leitura não sabe
_IMPORTADO = ast.Constant(value=None)  # `from m import NOME`: o valor é o do repositório


def _constantes_de_modulo(tree: ast.Module) -> dict[str, list[ast.expr]]:
    """Toda escrita de nome no escopo do módulo, inclusive dentro de `if`/`try`/
    `for`. Só `NOME = valor` traz o valor; `+=`, desempacotamento, `for`, `import
    X as NOME` e afins entram como escrita sem valor, e o nome deixa de resolver
    (2 escritas). `from m import NOME` sem alias vale como o NOME do repositório,
    salvo se o módulo também escrever NOME."""
    achadas: dict[str, list[ast.expr]] = {}
    pilha = list(ast.iter_child_nodes(tree))
    while pilha:
        no = pilha.pop()
        if isinstance(no, _ESCOPO_NOVO):
            continue
        simples = (no.targets if isinstance(no, ast.Assign) else
                   [no.target] if isinstance(no, ast.AnnAssign) and no.value else [])
        for alvo in simples:
            if isinstance(alvo, ast.Name):
                achadas.setdefault(alvo.id, []).append(no.value)
        ja = {id(a) for a in simples}
        if isinstance(no, ast.Name) and isinstance(no.ctx, ast.Store) and id(no) not in ja:
            achadas.setdefault(no.id, []).append(_SEM_VALOR)
        if isinstance(no, (ast.Import, ast.ImportFrom)):
            for a in no.names:
                nome = a.asname or a.name.split(".")[0]
                direto = isinstance(no, ast.ImportFrom) and a.asname in (None, a.name)
                achadas.setdefault(nome, []).append(_IMPORTADO if direto else _SEM_VALOR)
        pilha.extend(f for f in ast.iter_child_nodes(no) if id(f) not in ja)
    return achadas


@cache
def _constantes_do_repo() -> dict[str, list[ast.expr]]:
    todas: dict[str, list[ast.expr]] = {}
    for tree in _producao().values():
        for nome, valores in _constantes_de_modulo(tree).items():
            todas.setdefault(nome, []).extend(v for v in valores if v is not _IMPORTADO)
    return todas


def _nomes_locais(cadeia: tuple) -> set[str]:
    """Parâmetros, escritas e imports com alias de TODAS as funções envolventes
    (closure enxerga o local da de fora)."""
    nomes: set[str] = set()
    for funcao in cadeia:
        a = funcao.args
        nomes |= {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg] if x}
        for n in ast.walk(funcao):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                nomes.add(n.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                nomes |= {x.asname for x in n.names if x.asname and x.asname != x.name}
    return nomes


def _valores(expr: ast.expr, modulo: dict, cadeia: tuple = ()) -> set | None:
    """Os valores possíveis de `expr`, ou None se não resolve por leitura."""
    if isinstance(expr, ast.Constant) and not isinstance(expr.value, bool):
        return {expr.value}
    if isinstance(expr, ast.Name):
        if expr.id in _nomes_locais(cadeia):
            return None  # parâmetro ou local: só o chamador sabe
        candidatas = [c for c in modulo.get(expr.id, []) if c is not _IMPORTADO]
        if len(candidatas) < len(modulo.get(expr.id, [])) and candidatas:
            return None  # importado E escrito no módulo: depende da ordem
        candidatas = candidatas or _constantes_do_repo().get(expr.id) or []
        return _valores(candidatas[0], modulo) if len(candidatas) == 1 else None
    if isinstance(expr, ast.IfExp):
        a, b = _valores(expr.body, modulo, cadeia), _valores(expr.orelse, modulo, cadeia)
        return a | b if a is not None and b is not None else None
    if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
            and expr.func.id in ("float", "int") and len(expr.args) == 1 and not expr.keywords):
        v = _valores(expr.args[0], modulo, cadeia)
        return {float(x) for x in v} if v is not None else None
    return None


def _default(tree: ast.Module, funcao: str, parametro: str) -> ast.expr:
    for no in ast.walk(tree):
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)) and no.name == funcao:
            a = no.args
            pos = a.posonlyargs + a.args
            pares = list(zip(pos[len(pos) - len(a.defaults):], a.defaults))
            pares += [(x, d) for x, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None]
            return next(d for x, d in pares if x.arg == parametro)
    raise AssertionError(f"def {funcao} não achado")


def _e_ref(no: ast.AST) -> bool:
    return (isinstance(no, ast.Name) and no.id == REF) or (
        isinstance(no, ast.Attribute) and no.attr == REF)


def _com_funcao(tree: ast.Module):
    """(nó, funções que o contêm, da de fora para a de dentro) para todo nó."""
    pilha: list[tuple[ast.AST, tuple]] = [(tree, ())]
    while pilha:
        no, cadeia = pilha.pop()
        yield no, cadeia
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
            cadeia = (*cadeia, no)
        pilha.extend((filho, cadeia) for filho in ast.iter_child_nodes(no))


def _nome(cadeia: tuple) -> str:
    return cadeia[-1].name if cadeia else "<módulo>"


def _achados(fonte: str, caminho: str) -> tuple[list[str], set[tuple[str, str]]]:
    """(violações, chamadas resolvidas à mão) de um arquivo."""
    tree = ast.parse(fonte, caminho)
    modulo = _constantes_de_modulo(tree)
    default = _default(_producao()["core/system_event_log.py"], REF, "within_days")
    violacoes: list[str] = []
    a_mao: set[tuple[str, str]] = set()
    reconhecidas: set[int] = set()
    for no, cadeia in _com_funcao(tree):
        if isinstance(no, ast.ImportFrom):
            violacoes += [f"{caminho}:{no.lineno} import com alias de {REF}"
                          for a in no.names if a.name == REF and a.asname]
        if not isinstance(no, ast.Call):
            continue
        attr = getattr(no.func, "attr", None)
        if _e_ref(no.func):
            args = no.args
            reconhecidas.add(id(no.func))
        elif attr == "to_thread" and no.args and _e_ref(no.args[0]):
            args = no.args[1:]
            reconhecidas.add(id(no.args[0]))
        elif attr == "run_in_executor" and len(no.args) > 1 and _e_ref(no.args[1]):
            args = no.args[2:]
            reconhecidas.add(id(no.args[1]))
        else:
            continue
        kw = {k.arg: k.value for k in no.keywords}
        evento = args[0] if args else kw.get("event_type")
        janela = args[2] if len(args) > 2 else kw.get("within_days", default)
        nome = _nome(cadeia)
        dias = _valores(janela, modulo, cadeia)
        espalhado = None in kw or any(isinstance(a, ast.Starred) for a in no.args)
        if espalhado or (dias and not all(isinstance(d, (int, float)) for d in dias)):
            dias = None  # `*args`/`**kw` ou janela string: não resolve por leitura
        if dias is None:
            if (caminho, nome) in JANELA_RESOLVIDA_A_MAO:
                a_mao.add((caminho, nome))
            else:
                violacoes.append(f"{caminho}:{no.lineno} ({nome}) janela não resolve")
        elif max(dias) > LIMIAR:
            eventos = _valores(evento, modulo, cadeia) if evento is not None else None
            if eventos is None or not eventos <= EVENTOS_PERMANENTES:
                violacoes.append(f"{caminho}:{no.lineno} ({nome}) janela {max(dias)} > "
                                 f"{LIMIAR} e evento fora de EVENTOS_PERMANENTES")
    violacoes += [f"{caminho}:{no.lineno} referência a {REF} que não é chamada reconhecida"
                  for no in ast.walk(tree) if _e_ref(no) and id(no) not in reconhecidas]
    return violacoes, a_mao


def _varrer() -> tuple[list[str], set[tuple[str, str]]]:
    violacoes: list[str] = []
    a_mao: set[tuple[str, str]] = set()
    for caminho in _producao():
        fonte = (RAIZ / caminho).read_text(encoding="utf-8")
        if REF in fonte:
            v, m = _achados(fonte, caminho)
            violacoes += v
            a_mao |= m
    return violacoes, a_mao


def test_g1_todo_dedup_com_janela_longa_le_evento_permanente():
    assert _varrer()[0] == []


def test_g2_controle_negativo_cada_forma_reprova():
    casos = [
        'recent_event_exists("x_sent", uid, 365)', 'asyncio.to_thread(recent_event_exists, "x", u, 100)',
        'J = 120\nloop.run_in_executor(None, recent_event_exists, "x", uid, J)',
        'def f(e, uid, d):\n    return recent_event_exists(e, uid, d)',
        'from m import recent_event_exists as r', 'partial(recent_event_exists, "x")',
        'recent_event_exists("x", uid, within_days=91)', 'recent_event_exists("x", uid, "365")',
        'recent_event_exists("x", uid, **{"within_days": 365})',
        'def f(**kw):\n    return recent_event_exists("x", 1, **kw)',
        'def f(*a):\n    return recent_event_exists(*a)',
        'J = 30\nJ += 300\nrecent_event_exists("x", uid, J)',
        'J = 30\nif cond:\n    J = 365\nrecent_event_exists("x", uid, J)',
        'def o():\n    AVISO_DEDUPE_DAYS = 365\n    def i(u):\n        return recent_event_exists("x", u, AVISO_DEDUPE_DAYS)',
        'J = 30\nfrom cfg import J\nrecent_event_exists("x", uid, J)',
        'try:\n    from cfg import J\nexcept ImportError:\n    J = 30\nrecent_event_exists("x", uid, J)',
        'from cfg import ANUAL as AVISO_DEDUPE_DAYS\nrecent_event_exists("x", uid, AVISO_DEDUPE_DAYS)',
        'def f(u):\n    from cfg import ANUAL as AVISO_DEDUPE_DAYS\n    return recent_event_exists("x", u, AVISO_DEDUPE_DAYS)',
    ]
    for fonte in casos:
        assert _achados(fonte, "brinquedo.py")[0], fonte


def test_g3_controle_positivo_janela_curta_ou_evento_permanente_passa():
    casos = [
        'recent_event_exists("x", uid, 90)', 'recent_event_exists("x", uid)',
        'recent_event_exists("founder_email_sent", uid, 3650)', 'recent_event_exists("x", u, 6.0)',
        'loop.run_in_executor(None, recent_event_exists, "x", uid, 6.0)',
        'from core.observability import recent_event_exists', 'J = 30\nrecent_event_exists("x", u, J)',
        'from core.services.billing_dunning import PAYMENT_REMINDER_DEDUPE_DAYS as PAYMENT_REMINDER_DEDUPE_DAYS\n'
        'recent_event_exists("x", uid, PAYMENT_REMINDER_DEDUPE_DAYS)',
        'def f(u):\n    from core.services.billing_dunning import PAYMENT_REMINDER_DEDUPE_DAYS\n'
        '    return recent_event_exists("x", u, PAYMENT_REMINDER_DEDUPE_DAYS)',
    ]
    for fonte in casos:
        assert _achados(fonte, "brinquedo.py")[0] == [], fonte


def test_g4_lista_a_mao_sem_orfa_e_fire_email_com_janela_curta():
    assert set(JANELA_RESOLVIDA_A_MAO) == _varrer()[1]
    caminho = "frontend/finance_bot_websocket_custom.py"
    tree = _producao()[caminho]
    modulo = _constantes_de_modulo(tree)
    default = _default(tree, "_fire_email", "dedup_days")
    chamadas = [(no, f) for no, f in _com_funcao(tree) if isinstance(no, ast.Call)
                and getattr(no.func, "id", None) == "_fire_email"]
    assert chamadas
    for no, cadeia in chamadas:
        janela = next((k.value for k in no.keywords if k.arg == "dedup_days"), default)
        dias = _valores(janela, modulo, cadeia)
        assert dias is not None and max(dias) <= LIMIAR, f"{caminho}:{no.lineno} {dias}"


def _leitores_sql(tree: ast.Module) -> set[str]:
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr)}
    achados, pedacos = set(), set()
    for no, cadeia in _com_funcao(tree):
        if isinstance(no, ast.JoinedStr):  # f-string: lida inteira, não pedaço a pedaço
            pedacos |= {id(v) for v in no.values}
            s = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in no.values)
        elif (isinstance(no, ast.Constant) and isinstance(no.value, str)
              and id(no) not in docstrings | pedacos):
            s = no.value
        else:
            continue
        s = s.lower()
        # INSERT/DDL não lê, salvo se tiver `from system_event_logs` (INSERT ... SELECT).
        so_escreve = (re.search(r"\b(insert\s+into|create\s+(table|index))\b", s)
                      and not re.search(r"\bfrom\s+system_event_logs\b", s))
        if "system_event_logs" in s and "event_type" in s and not so_escreve:
            achados.add(_nome(cadeia))
    return achados


def test_g5_leitor_sql_novo_tem_de_ser_classificado():
    achados = {(c, f) for c, tree in _producao().items() for f in _leitores_sql(tree)}
    assert achados == set(LEITORES_SQL)
    sem_janela = {e for evs in LEITORES_SQL.values() if evs for e in evs}
    assert sem_janela <= EVENTOS_PERMANENTES


def test_g5_controle_negativo_leitor_sql_e_achado():
    fonte = 'def f():\n    q = "select 1 from system_event_logs where event_type = %s"\n'
    assert _leitores_sql(ast.parse(fonte)) == {"f"}
    insert = 'def g():\n    q = "insert into system_event_logs (event_type) values (%s)"\n'
    assert _leitores_sql(ast.parse(insert)) == set()
    insert_select = ('def h():\n    q = "insert into system_event_logs (event_type) select %s'
                     ' where not exists (select 1 from system_event_logs where event_type = %s)"\n')
    assert _leitores_sql(ast.parse(insert_select)) == {"h"}
    fstring = 'def k(t):\n    q = f"select 1 from system_event_logs where {t} and event_type = %s"\n'
    assert _leitores_sql(ast.parse(fstring)) == {"k"}


def test_g6_falha_de_remocao_na_pluggy_e_permanente():
    assert set(EVENTOS_FALHA_DELETE) <= EVENTOS_PERMANENTES


def test_g7_todo_permanente_existe_no_codigo():
    literais = {no.value for c, tree in _producao().items()
                if c != "core/services/log_retention.py"
                for no in ast.walk(tree) if isinstance(no, ast.Constant)}
    assert EVENTOS_PERMANENTES - literais == set()
