"""#657 (PR-9a, P8): `scripts/limpa_wamid_assunto_657.sql`, a troca única do wamid cru e do assunto
com texto do usuário nas linhas antigas de `system_event_logs`.

O teste lê os comandos do PRÓPRIO script e os executa em ordem numa sessão real, com a tabela
temporária `system_event_logs` à frente da pública (`pg_temp` vem antes no `search_path`), com
`standard_conforming_strings` ligado e desligado.

POSITIVO: `test_coerencia_com_o_codigo_novo`. A linha nova sai do código real (envio, typing,
`process_message`, contato, e-mail de agente e de verificação); a antiga é ela com o campo cru. Depois
do script as duas ficam iguais. O digest esperado vem de `mask_wamid`, e o rótulo de
`_AGENT_EMAIL_LABEL`, nunca escritos à mão.

CONTROLES (CLAUDE.md §3): as mutações de `MUTACOES` são aplicadas ao texto lido do arquivo e cada uma
tem de produzir os defeitos esperados num caso que estava verde. Duas delas rodam também na coerência.

CLASSE CEGA:
- o formato real do wamid de produção não foi medido. O digest não depende dele, mas o `a_like`, o
  `b_like` e o R1 dependem do prefixo `wamid.`;
- a ordem do array depende do `ORDER BY o`. `jsonb_agg` sem ele preserva a ordem na prática, então
  tirá-lo não reprova nada e fica sem controle;
- tempo, locks e a tabela real com FK e WAL.
"""
from __future__ import annotations

import logging
import os
import pathlib
import re
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import adapters.whatsapp.wa_client as wa_client
import adapters.whatsapp.wa_runtime as wa_runtime
import core.services.email_service as email_service
import frontend.finance_bot_websocket_custom as dashboard
from _log_dono_helpers import _linhas, _marcador, _uid, banco_real  # noqa: F401 (fixture autouse)
from core.services.piggy_agents import _AGENT_EMAIL_LABEL, _agent_email_subject
from test_account_deletion_pii_logs import marca, tabelas_admin  # noqa: F401
from test_category_normalization import _wa
from test_limpa_pilha_message_1331 import _comandos
from test_log_d4_email import EMAIL, _contato, _resend
from test_log_d4_wamid import NUMERO, WAMID, _graph, _mensagem
from utils_phone import mask_wamid as D

ARQ = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "limpa_wamid_assunto_657.sql"
CMDS = _comandos(ARQ.read_text(encoding="utf-8"))
(VERSAO, CANARIO, ANTES, TAMANHO, BEGIN, LOCK, TETO, UPD_A, UPD_B, UPD_C, DEPOIS, RESIDUO, PASSO6) = range(13)
COLUNAS = ("a_wamid_details", "b_wamid_message", "c_assunto")
INTACTA = object()
W, W2 = WAMID, WAMID.replace("wamid.", "wamid.HBgM", 1)
FALHA = "WA typing indicator failed message_id={} error={}"
VERIFICACAO = "🔐 {} — seu código de verificação PigBank"   # o formato da base: o código novo não o produz
EVENTO = {"payload": {"titulo": "Limite de açaí estourado"}}


def caso(nome, ev, msg, det, msg_esp=INTACTA, det_esp=INTACTA, src="wa_client", uid=None, r="", like=True):
    """`like`: o passo 4, rodado ANTES do UPDATE, conta esta linha (só as reescritas com `wamid.`)."""
    like = like and (msg_esp is not INTACTA or det_esp is not INTACTA)
    return SimpleNamespace(nome=nome, ev=ev, src=src, msg=msg, det=det, msg_esp=msg_esp, det_esp=det_esp,
                           uid=uid, r=r, like=like and f"{_grupo(ev)}_like")


def _grupo(ev: str) -> str:
    return "c" if ev.startswith("email_") else "b" if ev.startswith("logger.") else "a"


def _email(nome, assunto, novo=INTACTA, ev="email_sent", r=""):
    det = {"to": "z•••@example.com", "subject": assunto}
    return caso(nome, ev, "E-mail enviado para z•••@example.com", det,
                det_esp=INTACTA if novo is INTACTA else {**det, "subject": novo}, src="email_service", r=r)


CASOS = [
    caso("A send 2 wamids", "whatsapp_send_success", "s", {"kind": "text", "message_ids": [W, W2]},
         det_esp={"kind": "text", "message_ids": [D(W), D(W2)]}),
    caso("A typing ok", "whatsapp_typing_indicator_success", "ok", {"message_id": W}, det_esp={"message_id": D(W)}),
    caso("A typing failed", "whatsapp_typing_indicator_failed", "f",
         {"message_id": W, "status_code": 400, "response": f"erro {W}"}, r="r1",
         det_esp={"message_id": D(W), "status_code": 400, "response": f"erro {W}"}),
    caso("A typing exception", "whatsapp_typing_indicator_exception", "e", {"message_id": W},
         det_esp={"message_id": D(W)}),
    caso("A token invalid", "whatsapp_token_invalid", "t", {"message_id": W, "kind": "typing_indicator"},
         det_esp={"message_id": D(W), "kind": "typing_indicator"}),
    caso("A array vazio", "whatsapp_send_success", "s", {"kind": "text", "message_ids": []}),
    caso("A sem message_ids", "whatsapp_send_success", "s", {"kind": "interactive_list"}),
    caso("A string solta", "whatsapp_send_success", "s", {"kind": "x", "message_ids": "1730000000"}),
    caso("A digest e cru", "whatsapp_send_success", "s", {"kind": "text", "message_ids": [D(W), W2, 1, None]},
         det_esp={"kind": "text", "message_ids": [D(W), D(W2), 1, None]}),
    caso("A ja digest", "whatsapp_typing_indicator_success", "ok", {"message_id": D(W)}),
    caso("A nulo", "whatsapp_typing_indicator_success", "ok", {"message_id": None}),
    caso("A inteiro", "whatsapp_typing_indicator_success", "ok", {"message_id": 12}),
    caso("A timestamp", "whatsapp_typing_indicator_success", "ok", {"message_id": "1730000000"},
         det_esp={"message_id": D("1730000000")}, like=False),
    caso("A 64 hex", "whatsapp_typing_indicator_success", "ok", {"message_id": "a" * 64},
         det_esp={"message_id": D("a" * 64)}, like=False),
    caso("A outro evento", "whatsapp_send_failed", "x", {"message_id": W}, r="r1"),
    caso("A outro source", "whatsapp_typing_indicator_success", "ok", {"message_id": W}, src="outro", r="r1"),
    caso("A details array", "whatsapp_typing_indicator_success", "ok", [{"message_id": W}], r="r1"),
    caso("A message_id array", "whatsapp_typing_indicator_success", "ok", {"message_id": [W]}, r="r1"),
    caso("A message_id misto", "whatsapp_typing_indicator_success", "ok", {"message_id": [1, W]}, r="r1"),
    caso("A message_ids objeto", "whatsapp_send_success", "s", {"message_ids": {"id": W}}, r="r1"),
    caso("A message_ids aninhado", "whatsapp_send_success", "s", {"message_ids": [[W]]}, r="r1"),
    caso("A com dono", "whatsapp_typing_indicator_success", "ok", {"message_id": W}, uid=111,
         det_esp={"message_id": D(W)}),
    caso("B runtime", "logger.warning", FALHA.format(W, f"boom {W}"), {"logger": "x"}, r="r1",
         msg_esp=FALHA.format(D(W), f"boom {W}"), src="adapters.whatsapp.wa_runtime"),
    caso("B demo", "logger.warning", "WA demo " + FALHA.format(W, "fora")[3:], {"logger": "x"},
         msg_esp="WA demo " + FALHA.format(D(W), "fora")[3:], src="adapters.whatsapp.wa_demo"),
    caso("B ja digest", "logger.warning", FALHA.format(D(W), "e"), {"logger": "x"}, src="x"),
    caso("B sem error", "logger.warning", FALHA.format(W, "")[:-7], {"logger": "x"}, src="x", r="r1"),
    caso("B logger.error", "logger.error", FALHA.format(W, "e"), {"logger": "x"}, src="x", r="r1"),
    _email("C verificacao", VERIFICACAO.format(735194), "Seu código de verificação PigBank"),
    _email("C contato", "[Contato site] Dúvida do açaí 💸 — Zélia Conceição", "Contato site", ev="email_failed"),
    *[_email(f"C {k}", _agent_email_subject(k, [EVENTO]), rotulo) for k, rotulo in _AGENT_EMAIL_LABEL.items()],
    _email("C novidades", _agent_email_subject("xerife", [EVENTO] * 3), _AGENT_EMAIL_LABEL["xerife"]),
    *[_email(f"C ja {k}", rotulo) for k, rotulo in _AGENT_EMAIL_LABEL.items()],  # prende o NOT IN do R2
    _email("C ja contato", "Contato site"),
    _email("C ja fixo", "Seu código de verificação PigBank"),
    _email("C 🔐 sem digitos", VERIFICACAO.format(""), r="r2"),
    _email("C catalogo", "✓ Pagamento confirmado — Plus (R$ 10)"),
    # Um caso por prefixo do `LIKE ANY` do R2 (`🔐` é o "sem digitos"); o do 🎤 é o formato de 2026-08-05.
    *[_email(f"C r2 {k}", f"{rotulo.split()[0]} Seu mês — PigBank", r="r2") for k, rotulo in _AGENT_EMAIL_LABEL.items()],
    _email("C r2 contato", "[Contato] Dúvida", r="r2"),
    _email("C catalogo 📈", "📈 Investimentos, configurações e site mais organizado", r="r2"),  # send_update_email
    _email("C outro evento", VERIFICACAO.format(1), ev="outro"),
]


def _roda(cmds: list[str], scs: str, casos: list) -> SimpleNamespace:
    """Os passos 1 a 6 na ordem, numa sessão só. O `COMMIT` é a escolha do dono no passo 5."""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row) as conn:
        conn.execute(f"set standard_conforming_strings = {scs}")
        conn.execute("create temp table system_event_logs (like public.system_event_logs including defaults)")
        with conn.cursor() as cur:
            cur.executemany(
                "insert into system_event_logs (id, level, event_type, source, message, user_id, details)"
                " values (%s, 'warning', %s, %s, %s, %s, %s)",
                [(i, c.ev, c.src, c.msg, c.uid, Jsonb(c.det)) for i, c in enumerate(casos, 1)])

        def foto():
            return conn.execute("select * from system_event_logs order by id").fetchall()

        def um(cmd):
            return conn.execute(cmd).fetchone()

        r = SimpleNamespace(antes=foto())
        conn.execute(cmds[VERSAO])
        r.canario, r.contado = um(cmds[CANARIO])["literais_ok"], um(cmds[ANTES])
        r.like_antes = um(cmds[DEPOIS])  # sem o UPDATE: as `*_like` não podem dar 0 à toa
        for c in cmds[TAMANHO:UPD_A]:
            conn.execute(c)
        r.tocadas = [conn.execute(cmds[n]).rowcount for n in (UPD_A, UPD_B, UPD_C)]
        r.depois = foto()
        r.segunda = [conn.execute(cmds[n]).rowcount for n in (UPD_A, UPD_B, UPD_C)]
        r.depois2, r.mede4 = foto(), um(cmds[DEPOIS])
        r.residuo = um(cmds[RESIDUO].replace("count(*)", "array_agg(s.id)"))
        r.transacao = conn.info.transaction_status
        conn.execute("commit")
        r.mede6 = um(cmds[PASSO6])
    return r


def _defeitos(r: SimpleNamespace, casos: list) -> set[str]:
    d = set()
    for c, a, b in zip(casos, r.antes, r.depois):
        msg = c.msg if c.msg_esp is INTACTA else c.msg_esp
        det = c.det if c.det_esp is INTACTA else c.det_esp
        if (b["message"], b["details"]) != (msg, det):
            d.add(f"caso {c.nome}")
        if {k: v for k, v in a.items() if k not in ("message", "details")} != \
                {k: v for k, v in b.items() if k not in ("message", "details")}:
            d.add(f"coluna {c.nome}")
    for k in ("a_like", "b_like", "c_like"):
        if r.like_antes[k] != sum(c.like == k for c in casos):
            d.add(f"like_antes {k} {r.like_antes[k]}")
    if r.tocadas != [r.contado[k] for k in COLUNAS]:
        d.add("rowcount")
    if r.segunda != [0, 0, 0] or r.depois2 != r.depois:
        d.add("2a rodada")
    d |= {f"passo4 {k} {v}" for k, v in r.mede4.items() if v}
    d |= {f"passo6 {k} {v}" for k, v in r.mede6.items() if v}
    for chave, marca_ in (("r1_wamid_em_texto", "r1"), ("r2_assunto_desconhecido", "r2")):
        if {casos[i - 1].nome for i in r.residuo[chave] or []} != {c.nome for c in casos if c.r == marca_}:
            d.add(f"residuo {marca_}")
    if r.canario is not True:
        d.add("canario")
    if r.transacao != psycopg.pq.TransactionStatus.INTRANS:
        d.add(f"transacao {r.transacao.name}")
    return d


def _plano(texto: str) -> str:
    return " ".join(texto.split())


def test_o_arquivo_tem_os_passos_na_ordem_e_nao_faz_commit():
    assert [c.split()[0].upper() for c in CMDS] == [
        "SELECT", "SELECT", "SELECT", "SELECT", "BEGIN", "SET", "SET", "UPDATE", "UPDATE", "UPDATE",
        "SELECT", "SELECT", "SELECT"]
    assert "version()" in CMDS[VERSAO] and "pg_total_relation_size" in CMDS[TAMANHO]
    assert CMDS[BEGIN] == "BEGIN"
    assert CMDS[LOCK] == "SET LOCAL lock_timeout = '5s'"
    assert CMDS[TETO] == "SET LOCAL statement_timeout = '5min'"
    antes, depois = _plano(CMDS[ANTES]), _plano(CMDS[DEPOIS])
    assert CMDS[DEPOIS] == CMDS[PASSO6]
    assert depois.startswith(antes.removesuffix(" FROM system_event_logs s")), "as 3 de `antes` abrem `depois`"
    for upd, col in zip((UPD_A, UPD_B, UPD_C), COLUNAS):
        filtro = f"FILTER (WHERE {_plano(CMDS[upd]).rsplit(' WHERE ', 1)[1]}) AS {col}"
        assert filtro in antes and filtro in depois, f"o WHERE do UPDATE de {col} é o predicado da contagem"
    likes = depois.split("count(*) FILTER")[4:]
    assert len(likes) == 3 and all("~" not in x and "@?" not in x for x in likes), "`*_like` sem regex/jsonpath"
    texto = ARQ.read_text(encoding="utf-8")
    fim = texto.index("FROM system_event_logs s;", texto.index("AS r2_assunto_desconhecido"))
    decisao = texto[fim:texto.index("-- 6. ")].splitlines()
    assert "-- COMMIT;" in decisao and "-- ROLLBACK;" in decisao, "a decisão fica entre o resíduo e o passo 6"
    for cmd in CMDS:
        for lit in re.finditer(r"(?<![A-Za-z])(E?)'([^']*)'", cmd):
            assert "\\" not in lit[2] or lit[1] == "E", f"literal com barra sem E: {lit[0]}"
    canario = re.search(r"convert_to\('([^']*)'", CMDS[CANARIO])[1]
    usados = {ch for n, c in enumerate(CMDS) if n != CANARIO for ch in c if ord(ch) > 127}
    assert usados == set(canario), "o canário cobre exatamente os não-ASCII dos literais"
    assert canario.encode().hex() in CMDS[CANARIO]


@pytest.mark.parametrize("scs", ["on", "off"])
def test_reescreve_e_e_idempotente(scs):
    r = _roda(CMDS, scs, CASOS)
    assert _defeitos(r, CASOS) == set()
    assert r.tocadas == [9, 2, 10], r.tocadas


def _velho(nome, nova, msg=None, **det):
    """A linha antiga: a nova com o campo cru. A esperada é a nova inteira."""
    velho = caso(nome, nova["event_type"], msg or nova["message"], {**nova["details"], **det},
                 msg_esp=nova["message"], det_esp=nova["details"], src=nova["source"], uid=nova["user_id"])
    assert (velho.msg, velho.det) != (nova["message"], nova["details"]), f"{nome}: a antiga tem de diferir"
    return velho


def _coerencia(monkeypatch, marca_) -> list:
    def ultima(ev, like="%"):
        return _linhas(marca_, "event_type = %s and message like %s", (ev, like))[-1]

    pares = []
    _graph(monkeypatch, corpo={"messages": [{"id": W}]})
    wa_client.send_text(NUMERO, "oi")
    pares.append(_velho("A send_text", ultima("whatsapp_send_success"), message_ids=[W]))
    _graph(monkeypatch, corpo={"success": True})
    wa_client.send_typing_indicator(W)
    pares.append(_velho("A typing", ultima("whatsapp_typing_indicator_success"), message_id=W))

    def falha(message_id):
        raise RuntimeError("fora")

    _wa(monkeypatch, _uid())
    monkeypatch.setattr(wa_runtime, "send_typing_indicator", falha)
    wa_runtime.process_message(_mensagem())
    nova = ultima("logger.warning", "WA typing indicator failed%")
    pares.append(_velho("B process_message", nova, msg=nova["message"].replace(D(W), W)))

    enviados = _resend(monkeypatch)
    corpo, _, _ = _contato(_marcador())
    assert TestClient(dashboard.app).post("/contact", json=corpo).status_code == 200
    pares.append(_velho("C2 contato", ultima("email_sent"), subject=enviados[-1]["subject"]))
    for kind, rotulo in _AGENT_EMAIL_LABEL.items():
        for n in (1, 2):
            assert email_service.send_agent_report_email(
                EMAIL, 1, _agent_email_subject(kind, [EVENTO] * n), "<p>oi</p>", kind=kind, log_subject=rotulo)
            pares.append(_velho(f"C3 {kind} {n}", ultima("email_sent"), subject=enviados[-1]["subject"]))
    assert email_service.send_verification_email(EMAIL, "735194")
    pares.append(_velho("C1 verificacao", ultima("email_sent"), subject=VERIFICACAO.format("735194")))
    return pares


def _muda(*trocas: tuple[int, str, str]) -> list[str]:
    cmds = list(CMDS)
    for n, de, para in trocas:
        assert de in cmds[n], f"mutação sem efeito: {de!r} não está no comando {n}"
        cmds[n] = cmds[n].replace(de, para)
    return cmds


def _sem_item_do_r2(item: str) -> tuple[int, str, str]:
    cmd = CMDS[RESIDUO]
    return (RESIDUO, f"{item}, ", "") if f"{item}, " in cmd else (RESIDUO, f", {item}", "")


_R2 = re.findall(r"'[^']*'", re.search(r"ARRAY\[([^]]*)\]", CMDS[RESIDUO])[1]
                 + re.search(r"NOT IN \(([^)]*)\)", CMDS[RESIDUO])[1])  # os prefixos e os rótulos do R2
_EVT_A = re.search(r"\n\s+AND s\.event_type IN \([^)]*\)", CMDS[UPD_A])[0]
MUTACOES = {
    "corte_16": (_muda((UPD_A, "'hex'), 12)", "'hex'), 16)"), (UPD_B, "'hex'), 12)", "'hex'), 16)")), "on",
                 {"caso A typing ok", "caso A send 2 wamids", "caso B runtime", "2a rodada"}),
    "sem_guarda_A": (_muda((UPD_A, ' && !(@ like_regex "^[0-9a-f]{12}$")', ""),
                           (UPD_A, " AND e #>> '{}' !~ '^[0-9a-f]{12}$'", "")), "on", {"2a rodada", "caso A ja digest"}),
    "sem_evento_A": (_muda((UPD_A, _EVT_A, "")), "on", {"caso A outro evento"}),
    "lax_no_array": (_muda((UPD_A, "'strict $.message_ids", "'$.message_ids")), "on",
                     {"erro", "InvalidParameterValue"}),
    "sem_demo_B": (_muda((UPD_B, "(?:demo )?", "")), "on", {"caso B demo", "passo4 b_like 1"}),
    "sem_evento_B": (_muda((UPD_B, "s.event_type = 'logger.warning'\n   AND ", "")), "on", {"caso B logger.error"}),
    "sem_xerife_C": (_muda((UPD_C, "🤠 Xerife|", "")), "on", {"caso C xerife", "passo4 c_like 2"}),
    "literal_sem_E": (_muda((UPD_B, "E'", "'"), (UPD_B, "\\\\", "\\"), (UPD_C, "E'", "'"), (UPD_C, "\\\\", "\\")),
                      "off", {"caso B runtime", "caso C contato", "passo4 c_like 1"}),
    "canario_mojibake": (_muda((CANARIO, "'🔐", "'ðŸ”")), "on", {"canario"}),
    # O `strict` sozinho já protege `details` que não é objeto: só a dupla reprova.
    "sem_objeto_A": (_muda((UPD_A, " AND jsonb_typeof(s.details) = 'object'", ""),
                           (UPD_A, "'strict $.message_id ", "'$.message_id ")), "on", {"caso A details array", "2a rodada"}),
    "lax_message_id": (_muda((UPD_A, "'strict $.message_id ", "'$.message_id ")), "on", {"caso A message_id array"}),
    "b_like_sem_demo": (_muda((DEPOIS, "'WA %typing", "'WA typing")), "on", {"like_antes b_like 1"}),
    "a_like_texto": (_muda((DEPOIS, "OR CASE WHEN jsonb_typeof(s.details->'message_ids') = 'array'",
                            "OR (s.details->'message_ids')::text LIKE '%wamid.%' OR CASE WHEN false")), "on",
                     {"passo4 a_like 2"}),
    "b_like_sem_error": (_muda((DEPOIS, "wamid.% error=%'", "wamid.%'")), "on", {"passo4 b_like 1"}),
    "c_like_sem_digito": (_muda((DEPOIS, "'🔐 _% — seu código de verificação%'", "'🔐 %'")), "on", {"passo4 c_like 1"}),
    **{f"r2_sem_{item}": (_muda(_sem_item_do_r2(item)), "on", {"residuo r2"}) for item in _R2},
    "sem_evento_C": (_muda((UPD_C, "s.event_type IN ('email_sent', 'email_failed')\n   AND ", "")), "on",
                     {"caso C outro evento"}),
}


@pytest.mark.parametrize("mutacao", [None, "corte_16", "sem_xerife_C"], ids=["arquivo", "corte_16", "sem_xerife_C"])
def test_coerencia_com_o_codigo_novo(monkeypatch, marca, caplog, mutacao):  # noqa: F811
    with caplog.at_level(logging.INFO):
        pares = _coerencia(monkeypatch, marca)
    cmds = CMDS if mutacao is None else MUTACOES[mutacao][0]
    esperados = {
        "corte_16": {"caso A send_text", "caso A typing", "caso B process_message", "2a rodada"},
        "sem_xerife_C": {"caso C3 xerife 1", "caso C3 xerife 2", "passo4 c_like 2", "rowcount"},
    }.get(mutacao)
    defeitos = _defeitos(_roda(cmds, "on", pares), pares)
    if mutacao is None:
        assert defeitos == set(), defeitos
    else:
        assert esperados <= defeitos, defeitos


@pytest.mark.parametrize("nome", MUTACOES)
def test_controle_negativo_mutacao_reprova(nome):
    cmds, scs, esperados = MUTACOES[nome]
    try:
        defeitos = _defeitos(_roda(cmds, scs, CASOS), CASOS)
    except psycopg.Error as e:  # o `lax` faz o `jsonb_array_elements` levantar: erro também reprova
        defeitos = {"erro", type(e).__name__}
    assert esperados <= defeitos, defeitos
