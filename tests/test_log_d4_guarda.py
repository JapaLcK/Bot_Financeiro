"""`print` e log não levam telefone, wamid, e-mail, texto do usuário, token nem exceção crua (D4 da #657).

Par da guarda `tests/test_log_dono_na_coluna.py`, que cuida do WARNING+ e da coluna. `print` e INFO/DEBUG
não vão ao banco, mas vão ao log de plataforma, e o destino final é o mesmo para stdout e stderr. Só
`LOG_WAMID` vale em qualquer nível e em `details`.

Classes, por (arquivo, função, classe):
  PRINT_CRU  `print`, `warnings.warn`, `sys.stdout.write`, `sys.stderr.write`, com `sys.__stdout__`,
             `sys.__stderr__` e `.buffer`, com a exceção crua; e `print_exc`, `print_exception`,
             `print_stack` soltos. A tag de `falha_de_tarefa` conta como `print` nas três PRINT_*
  PRINT_PII  telefone ou e-mail nos argumentos, por nome, chave ou marcador
  PRINT_TXT  texto do usuário ou segredo nos argumentos (`_TXT`), por nome, chave ou marcador
  BAIXO_CRU  logger INFO/DEBUG com a exceção crua ou `exc_info` ligado
  BAIXO_PII  logger INFO/DEBUG, como PRINT_PII, com ou sem `extra=`
  BAIXO_TXT  logger INFO/DEBUG, como PRINT_TXT, com ou sem `extra=`
  LOG_WAMID  wamid ou id de mensagem em `print`, tag de `falha_de_tarefa`, logger de qualquer nível
             ou `log_system_event*`, sem `mask_wamid`: o base64 do wamid traz o telefone inteiro
  TAG_VIVA   tag de `falha_de_tarefa` que não é string literal: cada tag distinta é uma entrada no
             estado da P11, que nunca é podado, e a tag sai no log como veio
Nome de categoria conta como texto do usuário: `create_user_category` aceita nome livre.

Use `causa_segura(exc)` (ou `causa_do_provedor(exc)`, com o `code`; core/pg_text.py) para a exceção,
`mask_phone` para o telefone, `mask_wamid` para o id de mensagem e `len(...)` para o texto. A dívida mora em `tests/_log_d4_baseline.py`, num RATCHET como o da guarda do dono:
acima reprova, abaixo também, até alguém diminuir a entrada. Consertou um ponto? Da raiz:

    PYTHONPATH=. .venv/bin/python tests/test_log_d4_guarda.py --regenera

O QUE ESTA GUARDA NÃO VÊ (não é prova de completude):
  * objeto opaco (`{res}`, `{summary}`, `result.get("error")`);
  * `str(exc)` guardado em dict ou variável antes do print;
  * `exc` como parâmetro de função, fora do `except`;
  * mensagem montada fora da chamada;
  * `print` por apelido, `pprint`, e `print` ou logger passados como valor;
  * `logger.log(nivel, …)` com nível em variável;
  * wamid com nome genérico: `m.get("id")`, `raw.get("id")`, `raw["id"]`, `status.get("id")`, `mid`, e o
    `message.raw` inteiro;
  * nome em camelCase (`resetUrl`), `warn()` solto, e o que passa por `_log_falha`;
  * nomes de `_NUMERICO` valem pelo sufixo: `resposta_text_len` passaria com texto dentro;
  * `_INERTES` e máscaras valem pelo nome: `r.len(nota)` e `mask_phone(msg_id)` passam;
  * os valores de `extra=` em INFO/DEBUG, fora o wamid, e `causa_segura(str(exc))`;
  * `token[-6:]` é inerte só com esse nome, sufixo até 6 e sem `step`: é a máscara do `push_service`.
    Código de 6 dígitos não entra, porque o sufixo seria o código inteiro;
  * logger sem "log" no nome do receptor;
  * texto do usuário com outro nome (`alvo`, `row`, `d`): `_TXT` é lista negra por nome;
  * os `_SANEADORES` valem pelo nome, e `_desfecho` é um deles;
  * trocar uma dívida por outra na mesma função passa, e homônimas dividem a chave;
  * exceção relançada, que o uvicorn imprime sozinho;
  * `scripts/`, `app/e2e/`, `marketing/`, `harness_tests/`, `harness_support/`, `admin.py` e
    `dashboard_dev.py`, que saem no terminal do operador, salvo os 2 jobs de `_DENTRO`;
  * WARNING+: é a guarda do dono e a #1327.

ASSUNTO DE E-MAIL. O ratchet vê o `subject` no `logger.info` de `send_email`, nunca os chamadores: o
formulário de contato levou texto do visitante ao log passando `send_email` como valor para o
`to_thread`, e nada acusou. `_assuntos_abertos` confere cada chamada, direta ou com a função passada
como valor: o assunto é literal, ou vem com `log_subject=`, ou está em `ASSUNTOS_SEGUROS`. Não vê:
  * `send_email` guardado em dict ou atributo, `partial` com argumento nomeado,
    `getattr(mod, "send_email")` e `func=send_email`;
  * o conteúdo de `log_subject=`: ele vale pela presença, e o chamador pode passar texto do usuário;
  * wrapper novo: a lista confere a chamada a `send_email`, e quem chama o wrapper fica de fora;
  * o que preenche um assunto de `ASSUNTOS_SEGUROS`: o motivo é lido, não medido.
"""
from __future__ import annotations

import ast
import copy
import re
import subprocess
import sys
from collections import Counter

import pytest

import test_log_falha_traceback as _tlft
from _log_d4_baseline import DIVIDA
from test_log_dono_na_coluna import (_MASCARAS, _NIVEIS, _achados, _atual, _cita, _compara, _cru,
                                     _e_logger, _pii, _regrava_baseline)
from test_log_falha_traceback import _apelidos, _nome
from test_max_lines_python import _repo_de_brinquedo

_PALAVRAS = (r"notas?|descri\w*|desc|textos?|texts?|transcri\w*|prompts?|perguntas?|filenames?|titles?"
             r"|t[ií]tulos?|subjects?|assuntos?|tokens?|senhas?|passwords?|passwd|secrets?|otp|totp|jwt|bearer|codes?|c[oó]digos?"
             r"|cats?|categorias?|category|categories|reset_url|reset_link|api_key|authorization|cookies?")
# Número ou chave técnica, não o texto: `cat_id`, `text_len`, `prompt_tokens`, `status_code`.
_NUMERICO = (r"(?!\w*_(?:ids?|len|count|size|key_sql)$)"
             r"(?!\w*(?:prompt|completion|total|max|input|output)_tokens$)"
             r"(?!\w*(?:status|http|error|exit|country|series|cur)_code$)")
_TXT = re.compile(rf"^{_NUMERICO}(\w*_)?({_PALAVRAS})($|_)", re.I)
_TXT_MARCA = re.compile(rf"\b({_PALAVRAS})\s*[=:]\s*(%|\{{)", re.I)
_WAMID = re.compile(r"(^|_)(wamid|message_ids?|msg_ids?)($|_)", re.I)
_WAMID_MARCA = re.compile(r"\b(wamid|message_ids?|msg_ids?)\s*[=:]\s*(%|\{)", re.I)
_INERTES = _MASCARAS | {"len", "type", "isinstance", "bool"}
_PILHA_SOLTA = {"print_exc", "print_exception", "print_stack"}
_SAIDAS = {f"sys.{s}{b}" for s in ("stdout", "stderr", "__stdout__", "__stderr__") for b in ("", ".buffer")}
_NIVEL_NUM = {10: "debug", 20: "info"}
_FORA = ("scripts/", "app/e2e/", "marketing/", "harness_tests/", "harness_support/")
_FORA_ARQ = {"admin.py", "dashboard_dev.py"}
_DENTRO = {"scripts/account_deletion_job.py", "scripts/cleanup_job.py"}

# Fora do ratchet, com o motivo. Cada entrada cobre UMA ocorrência; órfã reprova.
JUSTIFICADAS: dict[tuple[str, str, str], str] = {
    ("config/env.py", "load_app_env", "PRINT_CRU"):
        "nome de fuso do ambiente, sem dado do titular: "
        "tests/test_log_d4_justificadas.py::test_fuso_invalido_imprime_so_a_linha_do_boot",
    ("core/services/meta_capi.py", "send_event", "BAIXO_TXT"):
        "corpo de resposta 2xx (`events_received`, `fbtrace_id`), cortado em 200. O corpo de erro é "
        "WARNING, #1327: tests/test_log_d4_justificadas.py::test_corpo_2xx_do_meta_sai_cortado",
    ("core/services/media_service.py", "transcribe_audio", "PRINT_TXT"):
        "`api_key=` imprime só OK ou AUSENTE, nunca a chave: "
        "tests/test_log_d4_conversa.py::test_t4_audio_nao_imprime_o_nome_do_arquivo",
    ("core/services/email_service.py", "send_email", "BAIXO_TXT"):
        "`log_subject`: rótulo fixo ou assunto sem texto do usuário, o que "
        "`test_assunto_dinamico_de_send_email` confere chamador a chamador",
}

_PLANO = "nome de plano do catálogo (`plan_display_name`), valor da assinatura e data"
_PLANO_NOVO = ("`plan_names.get(plan, plan.title())`, depois da lista branca `TIER_TO_STORED_PLAN` "
               "(frontend/finance_bot_websocket_custom.py), e data")
# Assunto dinâmico sem `log_subject`: (arquivo, função) -> por que não leva texto do usuário.
ASSUNTOS_SEGUROS: dict[tuple[str, str], str] = {
    ("core/services/email_service.py", "send_plan_change_scheduled_email"): _PLANO_NOVO,
    ("core/services/email_service.py", "send_welcome_email"): "número de dias do trial",
    ("core/services/email_service.py", "send_tip_email"): "título da lista fixa `_TIPS`",
    ("core/services/email_service.py", "send_password_reset_email"): "dois literais",
    ("core/services/email_service.py", "send_pro_welcome_email"): _PLANO,
    ("core/services/email_service.py", "send_ebook_email"):
        "nome do produto: `description` da linha do Stripe ou foto da cobrança Pix",
    ("core/services/email_service.py", "send_trial_ending_email"): _PLANO,
    ("core/services/email_service.py", "send_pro_charged_email"): _PLANO,
    ("core/services/email_service.py", "send_pix_paid_email"): _PLANO,
    ("core/services/email_service.py", "send_founder_email"): "literal",
    ("core/services/email_service.py", "send_payment_failed_email"): _PLANO,
    ("core/services/email_service.py", "send_subscription_canceled_email"): _PLANO,
    ("core/services/email_service.py", "send_free_plan_sunset_email"): "data do corte",
    ("core/services/remarketing.py", "send_remarketing_email"): "texto fixo por etapa (`_TEXTOS`)",
    ("frontend/finance_bot_websocket_custom.py", "export_email"): "datas do período",
    ("scripts/send_update_email.py", "main"): "constante `SUBJECT`",
    ("api/v2/feedback.py", "enviar"):
        "rótulo do dict fixo `ASSUNTO`, com a chave validada por `Literal` em `FeedbackIn.tipo`",
}


class _SemInertes(ast.NodeTransformer):
    """`len(nota)`, `token[-6:]` e `[mask_phone(c) for c in xs]` viram constante; `d.get("nota")` vira
    o nome, e o receptor continua visível para o `.get` encadeado."""

    def visit_Call(self, n: ast.Call) -> ast.AST:
        if _nome(n.func) in _INERTES:
            return ast.Constant(0)
        if _nome(n.func) == "get" and n.args and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str):
            receptor = self.visit(n.func.value) if isinstance(n.func, ast.Attribute) else ast.Constant(0)
            return ast.Tuple(elts=[ast.Name(id=n.args[0].value, ctx=ast.Load()), receptor], ctx=ast.Load())
        if _nome(n.func) == "getattr" and len(n.args) > 1 and isinstance(n.args[1], ast.Constant) \
                and isinstance(n.args[1].value, str):
            return ast.Name(id=n.args[1].value, ctx=ast.Load())
        return self.generic_visit(n)

    def visit_Subscript(self, n: ast.Subscript) -> ast.AST:
        s = n.slice            # `token[-6:]`: o sufixo do token do aparelho, a máscara do `push_service`
        sufixo = isinstance(n.value, ast.Name) and n.value.id == "token" and isinstance(s, ast.Slice) \
            and s.upper is None and s.step is None and isinstance(s.lower, ast.UnaryOp) \
            and isinstance(s.lower.op, ast.USub) and isinstance(s.lower.operand, ast.Constant) \
            and type(s.lower.operand.value) is int and 1 <= s.lower.operand.value <= 6
        return ast.Constant(0) if sufixo else self.generic_visit(n)

    def visit_ListComp(self, n: ast.ListComp) -> ast.AST:
        inerte = isinstance(n.elt, ast.Call) and _nome(n.elt.func) in _INERTES
        return ast.Constant(0) if inerte else self.generic_visit(n)


def _sem_inertes(nos: list) -> list:
    return [_SemInertes().visit(copy.deepcopy(n)) for n in nos]


def _txt(nos: list) -> bool:
    """Texto do usuário ou segredo por nome, atributo, chave ou marcador. O marcador só não conta
    quando todo valor da chamada é constante depois dos inertes: `"nota=%s", len(nota)` é um número,
    e `"code=%s n=%s", c, len(c)` reprova."""
    limpos = _sem_inertes(nos)
    valores = [v.value if isinstance(v, ast.FormattedValue) else v for n in limpos
               for v in (n.values if isinstance(n, ast.JoinedStr) else [n])]
    numero = all(isinstance(v, ast.Constant) for v in valores)
    return _cita(limpos, _TXT, None if numero else _TXT_MARCA)


def _imprime(f: ast.AST) -> bool:
    return isinstance(f, ast.Name) and f.id == "print" or ast.unparse(f) == "warnings.warn" or (
        isinstance(f, ast.Attribute) and f.attr == "write" and ast.unparse(f.value) in _SAIDAS)


def _loga(f: ast.AST) -> bool:
    return isinstance(f, ast.Attribute) and f.attr in _NIVEIS and _e_logger(f.value)


def _classes_d4(c: ast.Call, excs: set[str], evt=frozenset()) -> list[str]:
    """O wamid vale em qualquer nível e em `details`: nele o telefone vai inteiro (D4)."""
    saida = _imprime(c.func) or _loga(c.func) or _nome(c.func) in evt or _nome(c.func) == "falha_de_tarefa"
    nos = [*c.args, *(k.value for k in c.keywords)]
    mascara = any(isinstance(x, ast.Call) and _nome(x.func) == "mask_wamid"
                  for n in nos for x in ast.walk(n))
    wamid = saida and _cita(_sem_inertes(nos), _WAMID, None if mascara else _WAMID_MARCA)
    return _classes_print_e_baixo(c, excs) + (["LOG_WAMID"] if wamid else [])


def _classes_print_e_baixo(c: ast.Call, excs: set[str]) -> list[str]:
    f = c.func
    if _nome(f) in _PILHA_SOLTA:
        return ["PRINT_CRU"]
    if _imprime(f) or _nome(f) == "falha_de_tarefa":
        # `falha_de_tarefa` imprime a tag como veio; o `exc` do 2º argumento ela mesma redige.
        args = c.args if _imprime(f) else [*c.args[:1], *(k.value for k in c.keywords if k.arg == "tag")]
        viva = not _imprime(f) and not (len(args) == 1 and isinstance(args[0], ast.Constant)
                                        and isinstance(args[0].value, str))
        return [k for k, sim in (("PRINT_CRU", _cru(args, excs)), ("PRINT_PII", _pii(args)),
                                 ("PRINT_TXT", _txt(args)), ("TAG_VIVA", viva)) if sim]
    if not _loga(f):
        return []
    nivel = f.attr
    if nivel == "log" and c.args:                 # logger.log(logging.INFO, …), logger.log(20, …)
        a = c.args[0]
        nivel = a.attr.lower() if isinstance(a, ast.Attribute) else _NIVEL_NUM.get(
            getattr(a, "value", None), "log")
    if nivel not in {"info", "debug"}:
        return []
    kws = {k.arg: k.value for k in c.keywords if k.arg}
    args = [*c.args, *(v for k, v in kws.items() if k not in {"extra", "exc_info"})]
    pilha = kws.get("exc_info")
    cru = _cru(args, excs) or (pilha is not None and not (
        isinstance(pilha, ast.Constant) and pilha.value in (None, False)))
    return [k for k, sim in (("BAIXO_CRU", cru), ("BAIXO_PII", _pii(args)),
                             ("BAIXO_TXT", _txt(args))) if sim]


def _no_universo(rel: str) -> bool:
    return rel in _DENTRO or not (rel in _FORA_ARQ or rel.startswith(_FORA))


def _achados_d4(fonte: str | bytes, rel: str) -> list[tuple[str, str, str]]:
    return _achados(fonte, rel, _classes_d4) if _no_universo(rel) else []


def _assuntos_abertos(fonte: str | bytes, rel: str) -> list[tuple[str, str]]:
    """(arquivo, função) de cada `send_email` com assunto dinâmico e sem `log_subject=`."""
    arvore = ast.parse(fonte)
    nomes = _apelidos(arvore, frozenset({"send_email"}))
    func = {filho: no.name for no in ast.walk(arvore)
            if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)) for filho in ast.walk(no)}
    abertos = []
    for c in ast.walk(arvore):
        if not isinstance(c, ast.Call):
            continue
        valor = next((i for i, a in enumerate(c.args) if _nome(a) in nomes), None)
        if _nome(c.func) in nomes:
            args = c.args
        elif valor is not None:                   # `to_thread(send_email, …)`
            args = c.args[valor + 1:]
        else:
            continue
        kws = {k.arg: k.value for k in c.keywords}
        assunto = kws.get("subject") or (args[1] if len(args) > 1 else None)
        literal = isinstance(assunto, ast.Constant) or (isinstance(assunto, ast.JoinedStr) and all(
            isinstance(v, ast.Constant) for v in assunto.values))
        if not literal and "log_subject" not in kws:
            abertos.append((rel, func.get(c, "<módulo>")))
    return abertos


def test_assunto_dinamico_de_send_email():
    abertos = _tlft._varre_repo(_assuntos_abertos)
    orfas = sorted(set(ASSUNTOS_SEGUROS) - set(abertos))
    assert not orfas, f"ASSUNTOS_SEGUROS com entrada que nenhum código produz, remova: {orfas}"
    novos = sorted((Counter(abertos) - Counter(list(ASSUNTOS_SEGUROS))).elements())
    assert not novos, (
        "`send_email` com assunto dinâmico: o assunto vai ao log e a `details.subject`, com a coluna "
        "nula. Se ele pode levar texto do usuário, passe `log_subject=` com um rótulo fixo; se não "
        f"pode, registre o motivo em ASSUNTOS_SEGUROS: {novos}")


def test_ratchet_do_print_e_do_log_baixo():
    achados = _tlft._varre_repo(_achados_d4)
    orfas = sorted(set(JUSTIFICADAS) - set(achados))
    assert not orfas, f"JUSTIFICADAS com entrada que nenhum código produz, remova: {orfas}"
    acima, abaixo = _compara(_atual(achados, JUSTIFICADAS), DIVIDA)
    erros = []      # uma falha só: num rename, o "acima" e o "abaixo" vêm juntos
    if acima:
        erros.append(
            "`print` ou log novo com dado que não pode sair (D4 da #657). Troque a exceção por "
            "`causa_segura(exc)` ou `causa_do_provedor(exc)`, o telefone por `mask_phone(...)`, o "
            "wamid por `mask_wamid(...)` e o texto por `len(...)`. Chave nova (base 0) junto de uma entrada que DIMINUIU, com a MESMA "
            "classe, pode ser só RENAME: edite a chave à mão em tests/_log_d4_baseline.py. "
            "(arquivo, função, classe), base -> agora: "
            + "; ".join(f"{k} {b} -> {n}" for k, b, n in acima))
    if abaixo:
        erros.append(
            "A dívida DIMINUIU: diminua a entrada em tests/_log_d4_baseline.py (ou remova se "
            "chegou a 0)."
            + ("" if acima else " Comando: PYTHONPATH=. .venv/bin/python "
               "tests/test_log_d4_guarda.py --regenera.")
            + " (arquivo, função, classe), base -> agora: "
            + "; ".join(f"{k} {b} -> {n}" for k, b, n in abaixo))
    assert not erros, "\n\n".join(erros)


def test_varredura_ponta_a_ponta_pelo_git(tmp_path, monkeypatch):
    """O ratchet de verdade num repo de brinquedo: acima da base reprova, abaixo diz "DIMINUIU",
    rename traz os dois lados, órfã reprova, `scripts/x.py` não entra e os 2 jobs entram."""
    falha = "def tick():\n    try:\n        pass\n    except Exception as exc:\n        print(exc)\n"
    raiz = _repo_de_brinquedo(tmp_path, {
        "core/novo.py": falha.encode(),
        "scripts/x.py": falha.encode(),
        "admin.py": falha.encode(),
        "scripts/account_deletion_job.py": falha.encode(),
    })
    monkeypatch.setattr(_tlft, "_RAIZ", raiz)
    monkeypatch.setitem(globals(), "JUSTIFICADAS", {})
    chave = ("core/novo.py", "tick", "PRINT_CRU")
    job = ("scripts/account_deletion_job.py", "tick", "PRINT_CRU")
    NOVO, MENOS = "com dado que não pode sair", "A dívida DIMINUIU"

    def ratchet(divida: dict) -> str:
        monkeypatch.setitem(globals(), "DIVIDA", divida)
        with pytest.raises(AssertionError) as erro:
            test_ratchet_do_print_e_do_log_baixo()
        return str(erro.value)

    msg = ratchet({})
    assert NOVO in msg and MENOS not in msg and f"{chave} 0 -> 1" in msg and f"{job} 0 -> 1" in msg
    assert "scripts/x.py" not in msg and "admin.py" not in msg
    msg = ratchet({job: 1, chave: 2})
    assert MENOS in msg and NOVO not in msg and f"{chave} 2 -> 1" in msg and "Comando:" in msg
    velha = ("core/novo.py", "tick_velho", "PRINT_CRU")         # rename de função
    msg = ratchet({job: 1, velha: 1})
    assert NOVO in msg and MENOS in msg and f"{velha} 1 -> 0" in msg and "Comando:" not in msg
    monkeypatch.setitem(globals(), "DIVIDA", {job: 1, chave: 1})
    test_ratchet_do_print_e_do_log_baixo()                      # base = código: verde
    monkeypatch.setitem(globals(), "JUSTIFICADAS", {("core/novo.py", "outra", "PRINT_CRU"): "x"})
    assert "JUSTIFICADAS com entrada" in ratchet({job: 1, chave: 1})
    # A justificativa cobre UMA ocorrência: a segunda na mesma função volta ao ratchet.
    monkeypatch.setitem(globals(), "JUSTIFICADAS", {chave: "x"})
    (raiz / "core" / "novo.py").write_text(falha + "        print(exc)\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(raiz), "add", "-A"], check=True)
    assert f"{chave} 0 -> 1" in ratchet({job: 1})


if __name__ == "__main__":
    if sys.argv[1:] != ["--regenera"]:
        sys.exit(f"uso: {sys.argv[0]} --regenera (recebido: {sys.argv[1:]})")
    _regrava_baseline("_log_d4_baseline.py",
                      _atual(_tlft._varre_repo(_achados_d4), JUSTIFICADAS), DIVIDA)
