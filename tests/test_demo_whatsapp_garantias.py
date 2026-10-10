""""Testar o Piggy": as GARANTIAS do demo — nada é salvo, retenção de 30 dias,
reserva/envio com falha, prompt e histórico. A conversa em si está em
tests/test_demo_whatsapp.py; a infra, em tests/_demo_whatsapp_helpers.py.

Cada grupo tem controle negativo medido numa cópia da árvore (ver o relato do PR):
- nada salvo: get_or_create_canonical_user dentro de so_texto / reenviar,
  `message.text` no log de "modelo falhou", insert+update+delete em `executar`;
- retenção: tirar ("demo_sessions", ...) de `_cleanups()`; tirar o filtro de data
  de `sessao_recente`; tirar `and opened_at is null` do passo 1 de `abrir_sessao`;
- envio/reserva: tirar `devolver_mensagem` do except; `marcar_resposta` antes do
  envio; tratar None de `reservar_mensagem` sempre como fim;
- prompt/histórico: apagar uma das duas regras, o JSON da persona, ou fazer `_ler`
  devolver o histórico de outro número;
- `numero_tem_conta`: trocar `o.provider <> 'whatsapp'` ou o `exists(auth_accounts`
  por false (um caso isolado para cada).
"""
import asyncio
import json
import logging

import pytest

import db
from _demo_whatsapp_helpers import (  # noqa: F401  (mundo e _demo_limpo são fixtures)
    GATILHO, _abre, _conta_com_telefone, _demo_limpo, _escritas, _impressao, _params_do_log, _liga_por_codigo,
    _manda, _msg, _numero, _q, _sessao, _um, mundo, sql,
)
from _paywall_gate_helpers import cadastro_novo
from adapters.whatsapp import wa_client, wa_demo
from adapters.whatsapp.wa_parse import InboundAttachmentRef
from core.admin_dashboard import ensure_admin_tables
from core.crypto import hash_pii
from db.connection import get_conn
from core.services.ai_chat.runner import ERROR_MSG
from core.services.demo import conversa, dados
from core.services.table_cleanup import run_table_cleanup
from db import demo_funnel as funil

_REAL_SEND_TEXT = wa_client.send_text
_REAL_SEND_LIST = wa_client.send_interactive_list
SEGREDO = "segredoxyz"  # sem hífen/acento/maiúscula: sobrevive intacto ao _normalize


# ── 1. nada do usuário é salvo ───────────────────────────────────────────────

def test_nada_do_usuario_e_salvo(mundo, caplog, monkeypatch, sql):
    """Roda o `send_text`/`send_interactive_list` REAIS (só o HTTP é falso) e espia
    o gravador de eventos do wa_client: nenhum evento leva o telefone cru nem o
    texto do usuário. (system_event_logs nem existe no banco isolado do pytest, por
    isso a medição é no gravador, não na tabela.)"""
    caplog.set_level(logging.DEBUG)
    eventos, posts = [], []

    class Resp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"messages": [{"id": "wamid.fake"}]}

    monkeypatch.setenv("WA_TOKEN", "t")
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "123")
    monkeypatch.setattr(wa_client.requests, "post", lambda url, **k: posts.append(k["json"]) or Resp())
    monkeypatch.setattr(wa_client, "log_system_event_sync", lambda *a, **k: eventos.append((a, k)))
    monkeypatch.setattr(wa_client, "send_text", _REAL_SEND_TEXT)
    monkeypatch.setattr(wa_client, "send_interactive_list", _REAL_SEND_LIST)

    n = _numero()
    antes = _impressao()
    sql.clear()
    _manda(n, GATILHO)
    for i in range(8):
        _manda(n, f"{SEGREDO} {i}")
    _manda(n, f"{SEGREDO} nona")      # já no limite
    _manda(n, "gastei 50 no mercado")  # comando de gravação, no limite
    assert len(mundo.chamadas) == 8, "o demo devia ter respondido de verdade"
    assert len(posts) == 11 and len(eventos) == 11, "o envio real devia ter rodado e logado"

    assert any("demo_sessions" in q for q, _ in sql), "o gravador de SQL devia estar vivo"
    assert _escritas(sql) == [], "o demo executou escrita fora da demo_sessions"
    assert _impressao() == antes, "o demo escreveu em tabela que não é a dele"
    assert _um("select count(*) as n from user_identities where external_id_hash = %s",
               (hash_pii(n, kind="external_id"),))["n"] == 0
    assert n not in caplog.text and SEGREDO not in caplog.text, "telefone/texto no log"
    assert n not in repr(eventos) and SEGREDO not in repr(eventos), "telefone/texto nos eventos do wa_client"
    assert n not in _params_do_log(sql) and SEGREDO not in _params_do_log(sql), "telefone/texto em system_event_logs"


def test_registrar_gasto_no_demo_nao_grava(mundo, sql):
    n = _numero()
    _abre(n)
    antes = _impressao()
    sql.clear()
    _manda(n, "gastei 50 no mercado")
    assert _escritas(sql) == [] and _impressao() == antes
    assert mundo.chamadas[-1][-1] == {"role": "user", "content": "gastei 50 no mercado"}


def _audio(n, mundo, mp):
    anexo = InboundAttachmentRef(media_id="m1", filename="a.ogg", content_type="audio/ogg")
    _manda(n, "", attachments=[anexo], tipo="audio")


def _gatilho_de_novo(n, mundo, mp):
    _manda(n, GATILHO)


def _modelo_cai(n, mundo, mp):
    mundo.falha = True
    _manda(n, f"{SEGREDO} com modelo fora")


def _reacao(n, mundo, mp):  # ação "ignorar": calada, mas passa por executar
    _manda(n, "", tipo="reaction")


def _lotou(n, mundo, mp):
    """Teto cheio (a sessão de `n` já conta): OUTRO número, sem sessão, cai no LOTOU."""
    mp.setenv("DEMO_DAILY_MAX", "1")
    outro = _numero()
    _manda(outro, GATILHO)
    assert "lotou" in mundo.textos()[-1], mundo.saida
    return outro


def _lista_cai(n, mundo, mp):
    def quebra(*a, **k):
        raise RuntimeError("meta 400")
    mp.setattr(wa_client, "send_interactive_list", quebra)
    _manda(n, GATILHO)


@pytest.mark.parametrize("acao", [_audio, _gatilho_de_novo, _modelo_cai, _reacao, _lotou, _lista_cai],
                         ids=["so_texto", "reenviar", "modelo_falhou", "ignorar", "lotou", "lista_cai"])
def test_nada_e_salvo_nas_outras_acoes(mundo, caplog, sql, monkeypatch, acao):
    """Toda ação do `executar` além da conversa: mesma garantia (nada salvo, nada no log)."""
    caplog.set_level(logging.DEBUG)
    n = _numero()
    _abre(n)
    antes = _impressao()
    visto = len(mundo.saida)
    sql.clear()
    outro = acao(n, mundo, monkeypatch)
    nums = [n] + ([outro] if outro else [])
    if acao is _reacao:
        assert len(mundo.saida) == visto, "ignorar devia ficar calada"
    else:
        assert mundo.demo()[-1][1] in ("text", "lista"), "a ação devia ter respondido"
    assert _escritas(sql) == [], f"{acao.__name__} executou escrita fora da demo_sessions"
    assert _impressao() == antes, f"{acao.__name__} escreveu em tabela que não é do demo"
    assert all(x not in caplog.text for x in nums) and SEGREDO not in caplog.text
    assert all(x not in _params_do_log(sql) for x in nums) and SEGREDO not in _params_do_log(sql), "telefone/texto em system_event_logs"
    if acao is _modelo_cai:  # o warning vira INSERT em system_event_logs: o gravador não está cego
        assert _params_do_log(sql) != "[]"


def test_escritas_enxerga_executemany_e_alvo_disfarcado(sql):
    """O gravador não é cego: executemany e escrita cujo SQL só CITA demo_sessions contam."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.executemany("update users set id = id where false and id = %s", [(1,), (2,)])
        cur.execute("update users set id = id where false /* demo_sessions */")
        cur.execute("delete from users where false and id in (select 1 from demo_sessions)")
        cur.execute("select 1 from demo_sessions")
        conn.rollback()
    assert len(_escritas(sql)) == 3, _escritas(sql)


def test_impressao_ignora_a_sequencia_do_log_mas_acusa_a_de_outra_tabela():
    """Independe da ordem: a tabela do log existe (outro teste do worker a cria) e o demo
    a escreve, sem acusar; nextval em sequência de qualquer outra tabela, acusa."""
    asyncio.run(ensure_admin_tables())  # DDL oficial de system_event_logs
    antes = _impressao()
    assert not any(r["sequencename"].startswith(("system_event_logs_", "demo_sessions_"))
                   for r in antes["__sequencias__"])
    _q("select nextval('system_event_logs_id_seq')")
    assert _impressao() == antes
    outra = antes["__sequencias__"][0]["sequencename"]  # a 1ª que sobrou é de outra tabela
    _q("select nextval(%s)", (outra,))
    assert _impressao() != antes, outra


# ── 2. retenção de 30 dias ───────────────────────────────────────────────────

def test_esquecer_numeros_antigos_libera_o_numero(mundo):
    n, recente = _numero(), _numero()
    _abre(n)
    _abre(recente)
    _q("update demo_sessions set opened_at = now() - interval '31 days' where wa_hash = %s",
       (funil.wa_hash(n),))
    h_recente = funil.wa_hash(recente)

    assert funil.esquecer_numeros_antigos() >= 1
    assert _um("select count(*) as n from demo_sessions where wa_hash = %s", (funil.wa_hash(n),))["n"] == 0
    assert _sessao(recente)["wa_hash"] == h_recente, "a sessão recente não pode ser apagada"

    mundo.saida.clear()
    _manda(n, GATILHO)  # 30 dias depois pode testar de novo
    assert mundo.demo()[-1][1] == "lista" and _sessao(n) is not None


def test_poda_de_demo_sessions_esta_ligada_ao_run_table_cleanup(mundo):
    """Fiação: o job real (sem trocar `_cleanups`) esquece o número de 31 dias."""
    n = _numero()
    _abre(n)
    h = funil.wa_hash(n)
    _q("update demo_sessions set opened_at = now() - interval '31 days' where wa_hash = %s", (h,))
    resultado = run_table_cleanup()
    assert resultado["removed"].get("demo_sessions", 0) >= 1, resultado
    assert _um("select count(*) as n from demo_sessions where wa_hash = %s", (h,))["n"] == 0


def test_sessao_recente_so_conta_os_ultimos_30_dias():
    h = funil.wa_hash(_numero())
    code = funil.abrir_sessao(None, h, 10**6)
    assert funil.sessao_recente(h)["code"] == code
    _q("update demo_sessions set opened_at = now() - interval '29 days' where code = %s", (code,))
    assert funil.sessao_recente(h) is not None
    _q("update demo_sessions set opened_at = now() - interval '31 days' where code = %s", (code,))
    assert funil.sessao_recente(h) is None, "sessão de 31 dias (ainda sem poda) não pode contar"


def test_codigo_esquecido_pela_poda_nao_e_reaproveitado_por_outro_numero(mundo):
    code = funil.criar_clique("ig", None)
    a, b = _numero(), _numero()
    _manda(a, f"{GATILHO} (teste {code})")
    _q("update demo_sessions set msgs_used = 8, opened_at = now() - interval '31 days' where code = %s", (code,))
    funil.esquecer_numeros_antigos()
    assert _um("select wa_hash from demo_sessions where code = %s", (code,))["wa_hash"] is None

    _manda(b, f"{GATILHO} (teste {code})")
    sb = _sessao(b)
    assert sb["code"] != code and sb["msgs_used"] == 0 and sb["clicked_at"] is None
    _manda(b, "primeira pergunta de B")
    assert len(mundo.chamadas) == 1, "a 1ª pergunta de B não pode herdar o 8/8 de A"
    assert "Acabaram" not in mundo.textos()[-1]


# ── 3. reserva e envio com falha ─────────────────────────────────────────────

def test_erro_de_banco_na_reserva_nao_vira_fim_do_teste(mundo, monkeypatch):
    n = _numero()
    _abre(n)
    _manda(n, "primeira")
    assert _sessao(n)["msgs_used"] == 1
    monkeypatch.setattr(funil, "reservar_mensagem", lambda code: None)  # erro transitório
    antes = len(mundo.chamadas)
    _manda(n, "segunda")
    assert mundo.textos()[-1] == ERROR_MSG and "/t/" not in mundo.textos()[-1]
    assert _sessao(n)["msgs_used"] == 1 and len(mundo.chamadas) == antes


def test_limite_batido_por_outra_thread_manda_o_fim_com_link(mundo, monkeypatch):
    n = _numero()
    _abre(n)
    code = _sessao(n)["code"]
    _q("update demo_sessions set msgs_used = 7 where code = %s", (code,))

    def outra_thread_ganha(c):
        _q("update demo_sessions set msgs_used = 8 where code = %s", (c,))
        return None

    monkeypatch.setattr(funil, "reservar_mensagem", outra_thread_ganha)
    _manda(n, "oitava")
    assert f"/t/{code}" in mundo.textos()[-1] and "Acabaram" in mundo.textos()[-1]
    assert mundo.chamadas == []


def test_falha_de_envio_devolve_a_mensagem_e_nao_carimba(mundo, monkeypatch):
    n = _numero()
    _abre(n)

    def quebra(*a, **k):
        raise RuntimeError("meta 500")

    monkeypatch.setattr(wa_client, "send_text", quebra)
    try:
        _manda(n, "oi, tudo bem?")
    except RuntimeError:
        pass  # propagar ou não é do process_message; o que importa é o estado
    s = _sessao(n)
    assert len(mundo.chamadas) == 1, "o modelo devia ter sido chamado"
    assert s["msgs_used"] == 0, "a mensagem não chegou: tem de voltar"
    assert s["first_answer_at"] is None, "sem envio não há 'primeira resposta'"


def _historico_visto(mundo):
    """O que a ÚLTIMA chamada ao modelo recebeu entre o system e a pergunta atual."""
    return [m["content"] for m in mundo.chamadas[-1][1:-1]]


def test_resposta_nao_entregue_nao_entra_no_historico(mundo, monkeypatch):
    """Envio falhou (Meta 5xx): a pessoa não leu a resposta, então a retentativa
    não pode chegar ao modelo com ela no histórico (nem o par pergunta/resposta)."""
    n = _numero()
    _abre(n)

    def quebra(*a, **k):
        raise RuntimeError("meta 500")

    real = wa_client.send_text
    monkeypatch.setattr(wa_client, "send_text", quebra)
    _manda(n, "primeira que nao chegou")  # o process_message engole o erro de envio
    assert _sessao(n)["msgs_used"] == 0
    assert conversa._ler(funil.wa_hash(n)) == [], "nada foi entregue: histórico vazio"
    monkeypatch.setattr(wa_client, "send_text", real)
    _manda(n, "de novo")
    assert _historico_visto(mundo) == [], _historico_visto(mundo)
    assert _sessao(n)["msgs_used"] == 1


def test_resposta_entregue_entra_no_historico_sem_o_aviso_anexado(mundo):
    """Caminho legítimo: entregue ⇒ a pergunta seguinte a recebe. E o que fica é a
    resposta do modelo, sem o ULTIMA que o envio cola no fim."""
    n = _numero()
    _abre(n)
    _q("update demo_sessions set msgs_used = 6 where code = %s", (_sessao(n)["code"],))
    _manda(n, "setima")  # n=7 ⇒ o envio anexa ULTIMA
    assert wa_demo.ULTIMA in mundo.textos()[-1]
    _manda(n, "oitava")
    assert _historico_visto(mundo) == ["setima", "resposta 1"], _historico_visto(mundo)


def test_conversa_de_A_nao_toca_a_linha_de_B(mundo):
    """Toda escrita do funil é `where code = <o próprio>`: três vizinhas (uma com
    carimbos nulos e msgs_used baixo, uma com carimbos cheios e msgs_used=3, uma só
    clicada) ficam byte a byte iguais depois de uma conversa completa em A: falha do
    modelo (devolver), 8/8 (limit_at), resposta, checkout. Cada vizinha existe para um
    `where ... or true` diferente: nulos pegam marcar_*, msgs_used baixo pega reservar/devolver."""
    a, b, c = _numero(), _numero(), _numero()
    for n in (a, b, c):
        _abre(n)
    cb, cc = _sessao(b)["code"], _sessao(c)["code"]
    cd = funil.criar_clique("ig", "camp")
    _q("update demo_sessions set msgs_used = 1 where code = %s", (cb,))
    _q("update demo_sessions set msgs_used = 3, first_answer_at = now() - interval '1 hour',"
       " limit_at = now() - interval '1 hour', checkout_at = now() - interval '1 hour' where code = %s", (cc,))
    vizinhas = "select * from demo_sessions where code = any(%s) order by code"
    antes = _q(vizinhas, ([cb, cc, cd],))
    assert len(antes) == 3

    mundo.falha = True
    _manda(a, "com o modelo fora")            # reservar + devolver
    assert _sessao(a)["msgs_used"] == 0
    mundo.falha = False
    for i in range(9):                        # 8 respostas (marcar_resposta, limit_at) + o link
        _manda(a, f"pergunta {i}")
    funil.marcar_checkout(_sessao(a)["code"])
    sa = _sessao(a)
    assert sa["msgs_used"] == 8 and sa["limit_at"] and sa["first_answer_at"] and sa["checkout_at"]

    assert _q(vizinhas, ([cb, cc, cd],)) == antes, "a conversa de A mexeu na linha de outra sessão"


# ── 4. numero_tem_conta, caso a caso (§0.7: espelha o auto-vínculo) ──────────

def _liga_whatsapp(n, uid):
    _q("insert into user_identities (provider, external_id, user_id, external_id_hash)"
       " values ('whatsapp', %s, %s, %s)", (n, uid, hash_pii(n, kind="external_id")))


def test_numero_tem_conta_espelha_o_auto_vinculo(mundo):
    novo, com_fone, por_codigo, so_wa = _numero(), _numero(), _numero(), _numero()
    _conta_com_telefone(com_fone)
    _liga_por_codigo(por_codigo)
    db.get_or_create_canonical_user("whatsapp", so_wa)

    for n, esperado in ((novo, False), (com_fone, True), (por_codigo, True), (so_wa, False)):
        tem = funil.numero_tem_conta(n)  # ANTES: o attempt cria usuário
        status = db.attempt_whatsapp_phone_link(n)["status"]
        assert tem == (status != "no_match"), (n, tem, status)
        assert tem is esperado, (n, tem, status)


def test_whatsapp_ligado_a_usuario_com_conta_e_sem_outro_canal(mundo):
    n, uid = _numero(), cadastro_novo()
    _q("delete from user_identities where user_id = %s", (uid,))  # o cadastro web cria um canal
    _liga_whatsapp(n, uid)
    assert _um("select count(*) as c from user_identities where user_id = %s and provider <> 'whatsapp'",
               (uid,))["c"] == 0, "pré-condição: sem outro canal"
    assert funil.numero_tem_conta(n) is True
    assert db.attempt_whatsapp_phone_link(n)["status"] != "no_match"


def test_whatsapp_ligado_a_usuario_sem_conta_mas_com_outro_canal(mundo):
    n = _numero()
    uid = db.get_or_create_canonical_user("discord", f"d-{_numero()}")
    _liga_whatsapp(n, uid)
    assert _um("select count(*) as c from auth_accounts where user_id = %s", (uid,))["c"] == 0
    assert funil.numero_tem_conta(n) is True
    assert db.attempt_whatsapp_phone_link(n)["status"] != "no_match"


# ── 5. prompt e histórico ────────────────────────────────────────────────────

def test_prompt_do_demo_tem_as_regras_e_a_persona():
    assert "NUNCA diga que cabe ou que não cabe" in conversa.PROMPT
    assert "NUNCA afirme que registrou, salvou ou lançou" in conversa.PROMPT
    marca = "DADOS DE EXEMPLO (a Ana, fictícia):\n"
    assert json.loads(conversa.PROMPT.split(marca, 1)[1]) == dados.PERSONA


def test_historico_e_por_numero_e_nao_vaza_entre_dois(mundo):
    ha, hb = funil.wa_hash(_numero()), funil.wa_hash(_numero())
    for h, q in ((ha, "pergunta-A1"), (hb, "pergunta-B1"), (ha, "pergunta-A2"), (hb, "pergunta-B2")):
        conversa.registrar(h, q, conversa.responder(h, q))
    textos = [" | ".join(m["content"] for m in msgs[1:]) for msgs in mundo.chamadas]  # sem o system
    assert "pergunta-A1" in textos[2] and "pergunta-B1" not in textos[2], textos[2]
    assert "pergunta-B1" in textos[3] and "pergunta-A1" not in textos[3], textos[3]


# ── 6. decidir: falha de import alarma ───────────────────────────────────────

@pytest.mark.parametrize("erro,nivel", [(ImportError("db sumiu"), logging.ERROR),
                                        (RuntimeError("banco caiu"), logging.WARNING)])
def test_decidir_que_falha_segue_o_fluxo_normal_e_so_import_alarma(monkeypatch, caplog, erro, nivel):
    def boom():
        raise erro
    monkeypatch.setattr(wa_demo, "_funil", boom)
    assert wa_demo.decidir(_msg(_numero(), GATILHO)) is None
    regs = [r for r in caplog.records if r.name == wa_demo.logger.name]
    assert [r.levelno for r in regs] == [nivel] and not regs[0].exc_info
