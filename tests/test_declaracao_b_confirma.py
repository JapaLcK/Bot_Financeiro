"""#1315: a declaração pelo banco do ROTEADOR confirma o valor antes de gravar (ADR 0006, decisões do dono de 2026-11-09).

Tudo entra pelo caminho de produção: o texto e o áudio pelo `handle_incoming` (`manda_ts`/`fala`, com o `timestamp` da
Meta que o `wa_runtime` passa ao core), o toque de botão pelo `wa_runtime.process_message` com o payload
`interactive.button_reply` que o `wa_parse` lê. Só o Whisper (a transcrição) e o modelo da IA são trocados. As
portas (grupo 1 do plano) estão em `test_declaracao_b_conversa.py::test_porta_ligada_declara`.

Controles negativos (desligar o conserto, ver vermelho, repor) estão no docstring de cada grupo; o relato do Coder diz
qual mutação foi rodada. O que estes testes nunca pegam: o `timestamp` real da Meta (o disparo do webhook, não
necessariamente o envio) e o relógio de produção, o toque real no aparelho, mensagem composta offline, o modelo real.
"""
from __future__ import annotations

import time
import uuid
from datetime import timedelta

import pytest

import db
from core.handlers import declaracao_banco_textos as tx
from core.handlers import declaracao_confirma as dc
from core.intent_router import NOT_UNDERSTOOD_MSG
from tests._declaracao_b import (ana, carteira, declaracoes, fala, liga, manda_ts, manda_wamid,  # noqa: F401
                                 n_lancamentos, pendencia, q)
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from utils_date import today_tz

GASTO = "gastei 50 no mercado no pix"
PADARIA = "gastei 30 na padaria no pix"
PERGUNTA = "🐷 Só confirmando: registrar *despesa de R$ 50,00 em mercado, hoje, pelo banco*? Responde *sim* ou *não*."


def valores(uid) -> list[float]:
    return [float(d["valor"]) for d in declaracoes(uid)]


def viva(uid) -> dict:
    p = db.get_pending_action(uid)
    assert p and p["action_type"] == dc.TIPO, p
    return p


def seg(quando) -> int:
    return int(quando.timestamp())


@pytest.fixture
def rt(monkeypatch):
    """O `wa_runtime.process_message` de verdade; `envia(uid, texto | botão)` devolve [(corpo, ids dos botões)]."""
    import adapters.whatsapp.wa_runtime as wr
    from adapters.whatsapp.wa_parse import InboundMessage
    saida, quem = [], {}
    for nome, f in {
        "get_or_create_canonical_user": lambda p, e: quem["uid"],
        "attempt_whatsapp_phone_link": lambda wa_id, current_user_id=None: {"status": "already_linked",
                                                                            "user_id": quem["uid"]},
        "log_system_event_sync": lambda *a, **k: None,
        "send_typing_indicator": lambda *a, **k: None,
        "_seen_recent": lambda m: False,
        "_bloqueado_pelo_corte": lambda *a, **k: False,
        "_send_reply": lambda to, body, *a, **k: saida.append((body, [])),
        "send_interactive_buttons": lambda to, body, buttons, **k: saida.append((body, [b["id"] for b in buttons])),
    }.items():
        monkeypatch.setattr(wr, nome, f)

    def envia(uid, texto="", botao=None, ts=None):
        quem["uid"] = uid
        del saida[:]
        raw = {"id": "wamid.rt-" + uuid.uuid4().hex, "type": "text"}
        if botao:
            raw.update(type="interactive", interactive={"type": "button_reply",
                                                         "button_reply": {"id": botao, "title": texto}})
        wr.process_message(InboundMessage(wa_id="5511999998888", text=texto, attachments=[], raw=raw,
                                          timestamp=str(int(time.time()) + 1) if ts is None else ts))
        return list(saida)
    return envia


# ── R2 grupo 1: botão com identidade (DP-11) ─────────────────────────────────────────────────────────────────────────
def test_botao_velho_nao_confirma_nem_cancela_a_nova(ana, liga, rt):
    """Negativo (rodado): `responde` tratando o toque como texto (sem o ramo `_toque` e sem o `not botao`) faz o toque
    velho `decl_sim:<A>` gravar a padaria."""
    ((corpo, botoes_a),) = rt(ana, GASTO)
    a = viva(ana)["payload"]["livro_id"]
    assert corpo == PERGUNTA and botoes_a == [f"decl_sim:{a}", f"decl_nao:{a}"]
    ((corpo, botoes_b),) = rt(ana, PADARIA)
    b = viva(ana)["payload"]["livro_id"]
    assert corpo.startswith("🔕 Cancelei a pergunta anterior.") and botoes_b == [f"decl_sim:{b}", f"decl_nao:{b}"]
    pergunta_b = viva(ana)["payload"]["question"]
    ((corpo, botoes),) = rt(ana, "Sim", f"decl_sim:{a}")
    assert corpo == f"{tx.BOTAO_VELHO}\n\n{pergunta_b}" and botoes == botoes_b  # a viva repetida, com os botões dela
    ((corpo, _),) = rt(ana, "Não", f"decl_nao:{a}")
    assert corpo.startswith(tx.BOTAO_VELHO) and viva(ana)["payload"]["livro_id"] == b and declaracoes(ana) == []
    ((corpo, _),) = rt(ana, "Sim", "confirm_yes")  # o botão fixo de outra confirmação (apagar, recibo)
    assert corpo.startswith(tx.BOTAO_VELHO) and declaracoes(ana) == []
    ((corpo, _),) = rt(ana, "Sim", f"decl_sim:{b}")  # positivo: o botão da viva grava, sem depender do timestamp
    assert "registrada pelo banco" in corpo and valores(ana) == [30.0]
    ((corpo, _),) = rt(ana, "Sim", f"decl_sim:{b}")  # o mesmo botão de novo (outro toque): já gravou
    assert corpo == tx.REPETIDA.format(seq=1) and valores(ana) == [30.0]


def test_botao_nao_da_viva_descarta(ana, liga, rt):
    rt(ana, GASTO)
    a = viva(ana)["payload"]["livro_id"]
    assert rt(ana, "Não", f"decl_nao:{a}") == [(tx.NAO_FIZ_NADA, [])]
    assert pendencia(ana) is None and declaracoes(ana) == []
    assert rt(ana, GASTO.replace("50", "51"))[0][0].startswith("🐷 Só confirmando")


def test_isolamento_botao_com_id_de_outro_usuario(ana, liga, rt):
    """R2 grupo 7: B toca `decl_sim:<id da ação de A>`: texto de botão velho, nada lido nem escrito de A."""
    from tests._declaracao_b import _bank
    outro = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(outro)
    from conftest import promote_to_pro
    promote_to_pro(outro)
    _bank(outro)
    rt(ana, GASTO)
    a = viva(ana)["payload"]["livro_id"]
    assert rt(outro, "Sim", f"decl_sim:{a}") == [(tx.BOTAO_VELHO, [])]
    assert viva(ana)["payload"]["livro_id"] == a and declaracoes(ana) == [] and declaracoes(outro) == []
    assert "registrada pelo banco" in rt(ana, "Sim", f"decl_sim:{a}")[0][0]  # positivo: o dono do botão grava


# ── R2 grupo 3: ordem por envio (R2-2 B, sem margem) ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("quando", ["antes", "empate", "ausente", "invalido"])
def test_sim_sem_prova_de_ser_depois_nao_grava_e_repete(ana, liga, quando):
    """Negativo (rodado): `_depois` sempre True faz o 1º "sim" gravar a padaria."""
    manda_ts(ana, GASTO)
    manda_ts(ana, PADARIA)  # C' (a do mercado foi abandonada com aviso)
    p = viva(ana)
    t = seg(p["created_at"])
    ts = {"antes": str(t - 1), "empate": str(t), "ausente": "", "invalido": "12:00"}[quando]
    assert manda_ts(ana, "sim", ts=ts) == [f"{tx.ENVIADA_ANTES}\n\n{p['payload']['question']}"]
    assert declaracoes(ana) == [] and viva(ana)["created_at"] == p["created_at"]
    assert "registrada pelo banco" in manda_ts(ana, "sim", ts=str(t + 1))[0]  # positivo
    assert valores(ana) == [30.0]


def test_nao_sem_prova_de_ser_depois_nao_descarta(ana, liga):
    manda_ts(ana, GASTO)
    antes = viva(ana)
    t = seg(antes["created_at"])
    assert manda_ts(ana, "não", ts=str(t))[0].startswith(tx.ENVIADA_ANTES)
    assert viva(ana)["created_at"] == antes["created_at"]
    assert manda_ts(ana, "não", ts=str(t + 1)) == [tx.NAO_FIZ_NADA] and pendencia(ana) is None


# ── R2 grupo 4: expirada (DP-5, R2-1) e o "sim" antigo que não consome outra pendência ─────────────────────────────
@pytest.fixture
def vence(monkeypatch):
    """A confirmação nasce vencida: o mesmo prazo (`dc.PRAZO`) arma a pendência e diz "expirou" pelo livro."""
    monkeypatch.setattr(dc, "PRAZO", timedelta(0))


def test_sim_depois_de_expirar(ana, liga, vence):
    manda_ts(ana, GASTO)
    assert pendencia(ana) is None
    assert manda_ts(ana, "sim") == [tx.EXPIROU] and declaracoes(ana) == []
    assert manda_ts(ana, "não") == [tx.NAO_FIZ_NADA]  # R2-5


def test_sim_antigo_nao_apaga_o_lancamento_da_pergunta_seguinte(ana, liga, vence):
    """Negativo (rodado): `_da_ultima_acao` devolvendo None faz o "sim" antigo apagar o #1 (o fluxo de hoje)."""
    manda_ts(ana, "gastei 10 no mercado em dinheiro")
    manda_ts(ana, GASTO)  # vence na hora
    acao = q("select criado_em from ia_declaracao_livro where user_id=%s and papel='acao'", (ana,))
    assert "apagar o lançamento *#1*" in manda_ts(ana, "apagar #1")[0] and pendencia(ana) == "delete_launch"
    apagar = db.get_pending_action(ana)["created_at"]
    assert manda_ts(ana, "sim", ts=str(seg(acao["criado_em"]))) == [tx.EXPIROU]  # mandado antes do "apagar"
    assert n_lancamentos(ana) == 1 and pendencia(ana) == "delete_launch"
    r = manda_ts(ana, "sim", ts=str(seg(apagar) + 1))  # positivo: o "sim" do apagar apaga (o fluxo de hoje)
    assert n_lancamentos(ana) == 0, r


def test_sim_fora_da_janela_e_sem_confirmacao_nenhuma(ana, liga, vence, monkeypatch):
    assert manda_ts(ana, "sim") == [NOT_UNDERSTOOD_MSG]  # nunca houve confirmação
    manda_ts(ana, GASTO)
    monkeypatch.setattr(dc, "JANELA", timedelta(0))
    assert manda_ts(ana, "sim") == [NOT_UNDERSTOOD_MSG]
    assert manda_ts(ana, GASTO.replace("50", "52"), ts="")[0].startswith("🐷 Só confirmando")  # a janela não toca o livro


def test_cancelar_da_assinatura_volta_quando_a_confirmacao_fechou(ana, liga):
    """DP-12: "cancelar" é "não" só com a confirmação aberta; fechada, volta a ser o comando da assinatura."""
    from core.services.billing_commands import handle_billing_command
    manda_ts(ana, GASTO)
    assert manda_ts(ana, "cancelar") == [tx.NAO_FIZ_NADA] and pendencia(ana) is None and declaracoes(ana) == []
    (r,) = manda_ts(ana, "cancelar")
    assert r.startswith("🐷 Quer cancelar?") and handle_billing_command(ana, "cancelar").startswith("🐷 Quer cancelar?")


# ── R2 grupo 5: o "sim" falado pelo caminho real do áudio ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("como", ["texto", "audio"])
def test_sim_falado_grava(ana, liga, monkeypatch, como):
    """Negativo (rodado): sem a chamada do gancho depois da transcrição (`_handle_audio`), o "sim" falado não grava."""
    if como == "texto":
        manda_ts(ana, GASTO)
    else:
        assert PERGUNTA in fala(monkeypatch, ana, GASTO)[0]
    r = fala(monkeypatch, ana, "Sim.")
    assert r[0].startswith('🎙️ Entendi: "Sim."') and "registrada pelo banco" in r[0], r
    assert valores(ana) == [50.0]


def test_sim_falado_depois_de_expirar(ana, liga, monkeypatch, vence):
    manda_ts(ana, GASTO)
    assert fala(monkeypatch, ana, "sim")[0].endswith(tx.EXPIROU) and declaracoes(ana) == []


# ── r1 grupo 2 e R2 grupo 6: idempotência pela identidade da mensagem (DP-9) ────────────────────────────────────────
def test_reentrega_do_original_com_a_confirmacao_viva_repete_a_mesma(ana, liga):
    """Negativo (rodado): `_viva` sem a pendência do roteador faz a reentrega responder T19."""
    w = "wamid.orig-" + uuid.uuid4().hex
    assert manda_ts(ana, GASTO, w) == [PERGUNTA]
    criada = viva(ana)["created_at"]
    assert manda_ts(ana, GASTO, w) == [PERGUNTA]
    assert viva(ana)["created_at"] == criada and declaracoes(ana) == []
    s = "wamid.sim-" + uuid.uuid4().hex
    assert "registrada pelo banco" in manda_ts(ana, "sim", s)[0]
    assert manda_ts(ana, "sim", s) == [tx.REPETIDA.format(seq=1)]  # o "sim" reentregue
    assert manda_ts(ana, GASTO, w) == [tx.REPETIDA.format(seq=1)]  # o original depois do "sim"
    assert valores(ana) == [50.0]


def test_reentrega_depois_do_nao_e_da_expiracao_e_t19(ana, liga, monkeypatch):
    w1, w2 = "wamid.n-" + uuid.uuid4().hex, "wamid.x-" + uuid.uuid4().hex
    manda_ts(ana, GASTO, w1)
    manda_ts(ana, "não")
    assert manda_ts(ana, GASTO, w1) == [tx.ACAO_ENCERRADA]
    monkeypatch.setattr(dc, "PRAZO", timedelta(0))
    manda_ts(ana, GASTO, w2)
    assert manda_ts(ana, GASTO, w2) == [tx.ACAO_ENCERRADA] and declaracoes(ana) == []


def test_dp9_apagar_e_reentregar_o_original_nao_recria(ana, liga):
    w = "wamid.dp9-" + uuid.uuid4().hex
    manda_ts(ana, GASTO, w)
    manda_ts(ana, "sim")
    (d,) = declaracoes(ana)
    manda_ts(ana, "apagar #1")
    manda_ts(ana, "sim")
    assert declaracoes(ana) == []
    assert manda_ts(ana, GASTO, w) == [tx.REPETIDA.format(seq=1)]
    assert declaracoes(ana) == [] and pendencia(ana) != dc.TIPO


def test_dp9_audio_reentregue_com_outra_transcricao(ana, liga, monkeypatch):
    a = "wamid.aud-" + uuid.uuid4().hex
    fala(monkeypatch, ana, GASTO, a)
    manda_ts(ana, "sim")
    r = fala(monkeypatch, ana, "sim, gastei 50 no mercado no pix e 30 na padaria", a)
    assert r == [tx.REPETIDA.format(seq=1)] and valores(ana) == [50.0]


# ── r1 grupo 3: conversa de dois assuntos ───────────────────────────────────────────────────────────────────────────
def test_saldo_abandona_com_aviso_e_o_sim_nao_grava(ana, liga):
    """Negativo (rodado): `desfecho` devolvendo a confirmação mesmo com o comando atendido (o `nao_ajudou` sempre
    verdadeiro no `handle_incoming`), o "sim" grava R$ 50."""
    manda_ts(ana, GASTO)
    r = manda_ts(ana, "saldo")[0]
    assert r.startswith("🔕 Cancelei a pergunta anterior.\n\n") and "aldo" in r, r
    assert manda_ts(ana, "sim") == [tx.ACAO_ENCERRADA] and declaracoes(ana) == []


def test_outro_gasto_pelo_banco_troca_a_confirmacao(ana, liga):
    manda_ts(ana, GASTO)
    r = manda_ts(ana, PADARIA)[0]
    assert r.startswith("🔕 Cancelei a pergunta anterior.\n\n🐷 Só confirmando") and "R$ 30,00 em padaria" in r, r
    manda_ts(ana, "sim")
    assert valores(ana) == [30.0]


def test_em_dinheiro_grava_na_carteira_sem_confirmacao(ana, liga):
    """Positivo do grupo: o mesmo roteiro em dinheiro grava na Carteira na 1ª mensagem, como antes."""
    assert "registrada" in manda_ts(ana, "gastei 50 no mercado em dinheiro")[0]
    assert carteira(ana) == 150.0 and declaracoes(ana) == [] and pendencia(ana) != dc.TIPO


# ── r1 grupo 4: o que o usuário digita ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("sim", ["Sim", "SIM!", "sim 👍", "s", "pode", "isso"])
def test_formas_de_sim(ana, liga, sim):
    manda_ts(ana, GASTO)
    assert "registrada pelo banco" in manda_ts(ana, sim)[0] and valores(ana) == [50.0]


@pytest.mark.parametrize("nao", ["não", "nao.", "Não!", "cancelar"])
def test_formas_de_nao(ana, liga, nao):
    manda_ts(ana, GASTO)
    assert manda_ts(ana, nao) == [tx.NAO_FIZ_NADA] and pendencia(ana) is None and declaracoes(ana) == []


@pytest.mark.parametrize("sim", ["Sim, pode registrar", "pode registrar", "sim sim", "simm", "👍"])
@pytest.mark.parametrize("como", ["texto", "audio"])
def test_formas_completas_de_sim(ana, liga, monkeypatch, sim, como):
    """T-6 A. Negativo (rodado): `e_sim` igual ao `is_confirm` da IA deixa as cinco sem gravar."""
    manda_ts(ana, GASTO)
    r = manda_ts(ana, sim) if como == "texto" else fala(monkeypatch, ana, sim)
    assert valores(ana) == [50.0], r


def test_forma_completa_mantem_o_horario_e_a_reentrega(ana, liga):
    manda_ts(ana, GASTO)
    t, s = seg(viva(ana)["created_at"]), "wamid.pr-" + uuid.uuid4().hex
    assert manda_ts(ana, "pode registrar", ts=str(t))[0].startswith(tx.ENVIADA_ANTES) and declaracoes(ana) == []
    assert seg(viva(ana)["created_at"]) == t
    assert "registrada pelo banco" in manda_ts(ana, "pode registrar", s)[0]
    assert manda_ts(ana, "pode registrar", s) == [tx.REPETIDA.format(seq=1)] and valores(ana) == [50.0]


@pytest.mark.parametrize("comando", ["saldo", "gastei 20 no mercado", "apagar #1"])
def test_comando_reconhecido_cancela_mesmo_com_a_repeticao(ana, liga, comando):
    """Positivo do T-6: repetir a pergunta não impede outro comando de cancelar a anterior (DP-6)."""
    manda_ts(ana, "gastei 10 na feira em dinheiro")
    manda_ts(ana, GASTO)
    r = manda_ts(ana, comando)[0]
    assert r.startswith("🔕 Cancelei a pergunta anterior.\n\n") and pendencia(ana) != dc.TIPO, r
    assert declaracoes(ana) == []


def test_flag_desligada_antes_do_sim_nao_grava(ana, liga, monkeypatch):
    """O "sim" com a flag desligada não passa pelo gancho e chega ao `route` (`confirm.yes` -> `na_rota`): a
    confirmação sai sem gravar, com o texto do banco."""
    from core.handlers import forma_pagamento as fp
    manda_ts(ana, GASTO)
    monkeypatch.delenv("DECLARACAO_BANCO")
    assert manda_ts(ana, "sim") == [fp.msg_banco(ana, "despesa", 50.0)]
    assert pendencia(ana) is None and declaracoes(ana) == [] and n_lancamentos(ana) == 0


def test_ontem_e_virada_da_meia_noite_gravam_o_dia_mostrado(ana, liga, monkeypatch):
    import core.handlers.launches as L
    ontem = today_tz() - timedelta(days=1)
    assert "ontem, pelo banco" in manda_ts(ana, "gastei 50 no mercado ontem no pix")[0]
    manda_ts(ana, "sim")
    monkeypatch.setattr(L, "today_tz", lambda: ontem)  # armada "às 23:59 de ontem"...
    manda_ts(ana, PADARIA)
    monkeypatch.undo()  # ... e o "sim" depois da meia-noite
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    manda_ts(ana, "sim")
    assert [d["criado_em"].date() for d in declaracoes(ana)] == [ontem, ontem]


def test_tres_casas_recusa_antes_de_confirmar(ana, liga):
    assert manda_ts(ana, "gastei 50,123 no mercado no pix") == [tx.valor_casas(50.123)]
    assert pendencia(ana) is None


def test_sim_de_outro_usuario_nao_grava(ana, liga):
    from tests._declaracao_b import _bank
    from conftest import promote_to_pro
    outro = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(outro)
    promote_to_pro(outro)
    _bank(outro)
    manda_ts(ana, GASTO)
    assert manda_ts(outro, "sim") == [NOT_UNDERSTOOD_MSG]
    assert pendencia(ana) == dc.TIPO and declaracoes(ana) == []


# ── r1 grupo 5: T16 e a fila (DP-7 A) ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("frase", ["gastei 50 no mercado e paguei o aluguel pelo banco",
                                   "paguei o aluguel e paguei a luz pelo banco"])
@pytest.mark.parametrize("como", ["texto", "audio"])
def test_dois_gastos_com_um_sem_valor_sao_t16(ana, liga, monkeypatch, frase, como):
    """Negativo (rodado): sem a regra no `add`/`rotear_partes`, nasce a fila e a confirmação disputa a linha com ela."""
    r = manda_ts(ana, frase) if como == "texto" else fala(monkeypatch, ana, frase)
    assert any(tx.UM_POR_MENSAGEM in x for x in r), r
    assert pendencia(ana) is None and declaracoes(ana) == []


def test_dois_gastos_em_dinheiro_seguem_com_a_fila(ana, liga):
    """Positivo: em dinheiro o mercado grava e o aluguel vai para a fila, como antes."""
    r = manda_ts(ana, "gastei 50 no mercado e paguei o aluguel em dinheiro")[0]
    assert "aluguel" in r and carteira(ana) == 150.0 and pendencia(ana) == "multi_launch_values", r


# ── R2 grupo 2 e grupo 5 com a IA (modelo falso; o runner de verdade arma e lê o histórico) ──────────────────────────
@pytest.fixture
def ana_ia_rot(monkeypatch):
    """Ana do chat IA (`tests/_chat_ia_apoio.py`) com o `WA_IA_PRIMEIRO` desligado: o gasto vai ao roteador."""
    from tests._chat_ia_apoio import _bank
    from tests._ia_falsa_helpers import desliga_flag
    from conftest import promote_to_pro
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    promote_to_pro(uid)
    _bank(uid)
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    desliga_flag(monkeypatch)
    return uid


def test_ia_arma_depois_e_o_botao_da_declaracao_nao_vale(ana_ia_rot, monkeypatch, rt):
    """R2-6 e DP-13: a pergunta da IA, mais recente, invalida a do roteador; o toque nela não grava e a da IA fica
    intacta. Negativo (rodado): sem `_vence_a_do_roteador` no runner, o toque grava o mercado."""
    from tests._ia_falsa_helpers import chamada, com_tools, openai_falso, texto
    uid = ana_ia_rot
    db.upsert_budget(uid, "mercado", 300)  # atualizar o orçamento pede "sim" (`tools/budgets.py`)
    reg = openai_falso(monkeypatch, com_tools(chamada("set_budget", {"categoria": "mercado", "budget": 500})),
                       texto("Confirma?"))
    rt(uid, GASTO)
    a = viva(uid)["payload"]["livro_id"]
    rt(uid, "piggy meu orçamento de mercado é 500")
    meta = db.ai_get_pending_action(uid)
    assert meta and meta["tool_name"] == "set_budget" and pendencia(uid) is None, reg.creates
    assert rt(uid, "Sim", f"decl_sim:{a}") == [(tx.BOTAO_VELHO, [])]
    assert declaracoes(uid) == [] and db.ai_get_pending_action(uid)["created_at"] == meta["created_at"]


@pytest.mark.parametrize("como", ["texto", "audio"])
def test_comando_a_ia_cancela_a_confirmacao_e_o_sim_vai_a_ia(ana_ia_rot, monkeypatch, como):
    """DP-6/DP-13: "piggy …" é outro comando e cancela a confirmação com o aviso; a pergunta da IA (de um turno real,
    não injetada) é a mais nova e o "sim" (digitado ou falado) vai a ela. Negativo (rodado): sem a chamada de
    `suspende` no `handle_incoming`, a confirmação fica viva e o "sim" grava a declaração."""
    from tests._ia_falsa_helpers import openai_falso, texto
    uid = ana_ia_rot
    reg = openai_falso(monkeypatch, texto("Você gastou R$ 0,00 este mês. Quer ver por categoria?"),
                       texto("Mercado: R$ 0,00."))
    manda_ts(uid, GASTO)
    r = manda_ts(uid, "piggy quanto gastei este mês?")[0]
    assert r.startswith("🔕 Cancelei a pergunta anterior.\n\nVocê gastou") and pendencia(uid) is None, r
    r = manda_ts(uid, "sim") if como == "texto" else fala(monkeypatch, uid, "sim")
    assert declaracoes(uid) == [] and len(reg.creates) == 2 and "Mercado" in r[0], r


@pytest.mark.parametrize("resposta", ["sim", "não"])
def test_oferta_da_ia_depois_de_uma_declaracao_vai_a_ia(ana_ia_rot, monkeypatch, rt, resposta):
    """Até 24 h depois de uma declaração, o "sim"/"não" à pergunta aberta da IA (sem pendência), mais nova que a ação,
    vai à IA. Negativo (rodado): sem a pergunta aberta da IA entre as candidatas de `_da_ultima_acao`, ele recebe T3."""
    from tests._ia_falsa_helpers import openai_falso, texto
    uid = ana_ia_rot
    reg = openai_falso(monkeypatch, texto("Você gastou R$ 50,00 este mês. Quer ver por categoria?"), texto("ok"))
    rt(uid, GASTO)
    rt(uid, "sim")  # pelo `wa_runtime`: a oferta "categoria errada?" sai com o botão, como no WhatsApp
    rt(uid, "piggy quanto gastei este mês?")
    time.sleep(1.1)  # a hora da pergunta da IA é lida pela idade da mensagem (ADR): sem empate de segundo
    assert rt(uid, resposta) == [("ok", [])] and len(reg.creates) == 2 and valores(uid) == [50.0]


@pytest.mark.parametrize("quando", ["antes", "empate", "ausente", "invalido", "depois"])
def test_confirmacao_da_ia_no_whatsapp_segue_a_regra_do_horario(ana_ia_rot, monkeypatch, quando):
    """T-4 A: a confirmação de declaração da IA no WhatsApp só grava com o `timestamp` com certeza posterior; o
    Discord e a web seguem como antes (`test_declaracao_b_chat_ia_discord.py`, `_web.py`). Negativo (rodado): sem o
    ramo da IA em `responde`, "antes", "empate", "ausente" e "invalido" gravam."""
    from tests._chat_ia_apoio import CONFIRMA, banco
    from tests._ia_falsa_helpers import openai_falso
    uid = ana_ia_rot
    openai_falso(monkeypatch, banco())
    assert manda_ts(uid, "piggy " + GASTO) == [CONFIRMA]
    t = seg(db.ai_get_pending_action(uid)["created_at"])
    ts = {"antes": str(t - 5), "empate": str(t), "ausente": "", "invalido": "12:00", "depois": str(t + 1)}[quando]
    r = manda_ts(uid, "sim", ts=ts)
    if quando == "depois":
        assert "registrada pelo banco" in r[0] and valores(uid) == [50.0], r
    else:
        assert r == [f"{tx.ENVIADA_ANTES}\n\n{CONFIRMA}"] and declaracoes(uid) == [], r
        assert db.ai_get_pending_action(uid) is not None


# ── R3 (decisões do dono de 2026-11-10): outro comando, alteração de valor, recusas, as duas portas ─────────────────
def _ia_responde(monkeypatch, texto="🐷 resposta da IA"):
    """A IA do fallback (a fronteira do modelo) atende com `texto`."""
    import core.services.ai_chat as ai_chat_mod
    monkeypatch.setattr(ai_chat_mod, "chat", lambda user_id, text, **kw: texto)


def _tier3(monkeypatch, intent, confianca):
    """O tier 3 do classificador (LLM) devolve `intent` com `confianca` (só a fronteira do LLM é trocada)."""
    import core.intent_classifier as ic
    monkeypatch.setattr(ic, "_classify_llm_call", lambda *a, **k: ic.IntentResult(intent=intent, confidence=confianca))


PRESOS_TIER2 = ["quero ver meus investimentos", "apaga o lançamento 5", "quanto tenho na caixinha viagem",
                "gastos por categoria", "transfere 100 pra caixinha", "qual o saldo do nubank", "contas a pagar",
                "AUDIO:qual o saldo do nubank"]
PRESOS_TIER3 = ["qto sobrou", "quanto tenho investido", "apaga o ultimo", "cadê meu dinheiro",
                "quanto falta pra minha meta", "previsão do mês", "me ajuda"]


def _diz(uid, monkeypatch, cmd):
    return fala(monkeypatch, uid, cmd[6:]) if cmd.startswith("AUDIO:") else manda_ts(uid, cmd)


@pytest.mark.parametrize("cmd", PRESOS_TIER2)
def test_comando_de_dominio_atendido_cancela_a_confirmacao(ana, liga, monkeypatch, cmd):
    """R3-1 A: o texto que o classificador local não reconhece segue até a IA; atendido, a confirmação sai com o aviso.
    Negativo (rodado): sem `suspende`, estes ficam com a confirmação viva (o "sim" seguinte grava)."""
    _ia_responde(monkeypatch)
    manda_ts(ana, GASTO)
    r = "\n".join(_diz(ana, monkeypatch, cmd))
    if cmd.startswith("AUDIO:"):  # o áudio não vai à IA: sem confirmação ele dá a dica de ajuda, que não atende
        assert "Não entendi exatamente" in r and r.endswith(PERGUNTA) and pendencia(ana) == dc.TIPO, r
        return
    assert r.startswith("🔕 Cancelei a pergunta anterior.") and "resposta da IA" in r, r
    assert pendencia(ana) != dc.TIPO
    assert manda_ts(ana, "sim") == [tx.ACAO_ENCERRADA] and declaracoes(ana) == []


@pytest.mark.parametrize("confianca", [0.9, 0.3])
@pytest.mark.parametrize("cmd", PRESOS_TIER3)
def test_comando_que_depende_do_llm_cancela_a_confirmacao(ana, liga, monkeypatch, cmd, confianca):
    """Os que o classificador local dá 0,00: com o LLM (simulado) confiante, o roteador atende; sem confiança, a IA."""
    _ia_responde(monkeypatch)
    _tier3(monkeypatch, "balance.check", confianca)
    manda_ts(ana, GASTO)
    r = manda_ts(ana, cmd)[0]
    assert r.startswith("🔕 Cancelei a pergunta anterior.") and pendencia(ana) != dc.TIPO, r
    assert declaracoes(ana) == []


@pytest.mark.parametrize("frase", ["hmm", "pera", "asdfgh"])
def test_comando_que_nao_ajuda_devolve_a_mesma_confirmacao(ana, liga, monkeypatch, frase):
    """R3-1 A, caso a: sem IA no plano, o roteador não entende ("Não entendi…"/"Só consigo ajudar…"): a mesma pergunta
    (a mesma ação no livro) volta, e o "sim" depois grava uma vez. Negativo (rodado): sem `desfecho` retomar, a
    confirmação some e o "sim" dá T19."""
    import core.services.plan_service as ps
    monkeypatch.setattr(ps, "ai_chat_allowed", lambda uid: False)
    manda_ts(ana, GASTO)
    a = viva(ana)["payload"]["livro_id"]
    (r,) = manda_ts(ana, frase)
    assert r.endswith(f"\n\n{PERGUNTA}") and not r.startswith("🔕"), r  # a resposta do roteador e a pergunta de novo
    assert viva(ana)["payload"]["livro_id"] == a and declaracoes(ana) == []
    assert "registrada pelo banco" in manda_ts(ana, "sim")[0] and valores(ana) == [50.0]


def test_ia_que_nao_tem_ferramenta_devolve_a_confirmacao(ana, liga, monkeypatch):
    from core.services.ai_chat.tools.meta import _report_out_of_scope_execute
    _ia_responde(monkeypatch, _report_out_of_scope_execute(ana, {"reason": "teste"}))
    _tier3(monkeypatch, "out_of_scope", 0.3)
    manda_ts(ana, GASTO)
    assert manda_ts(ana, "me explica o pib")[0].endswith(f"\n\n{PERGUNTA}") and pendencia(ana) == dc.TIPO


@pytest.mark.parametrize("frase", ["sim, obrigado", "sim por favor", "👍 valeu"])
def test_sim_com_algo_a_mais_que_nao_e_valor_repete(ana, liga, frase):
    """R3-1 A, caso b."""
    manda_ts(ana, GASTO)
    t = seg(viva(ana)["created_at"])
    assert manda_ts(ana, frase, ts=str(t)) == [f"{tx.ENVIADA_ANTES}\n\n{PERGUNTA}"]
    assert manda_ts(ana, frase) == [PERGUNTA] and pendencia(ana) == dc.TIPO and declaracoes(ana) == []


def test_sim_concorrente_durante_outro_comando_nao_grava(ana, liga, monkeypatch):
    """Ajuste do dono: a confirmação sai ANTES do comando rodar. O comando ("saldo", atendido) fica parado depois de a
    tirar; um "sim" chega nesse meio: não grava. Negativo (rodado): sem `suspende` (a confirmação sai só no fim),
    esse "sim" grava R$ 50."""
    import threading
    import core.handle_incoming as hi
    parou, solta = threading.Event(), threading.Event()
    real = hi.classify

    def classify(texto, *a, **k):
        if texto == "saldo":
            parou.set()
            assert solta.wait(20)
        return real(texto, *a, **k)
    monkeypatch.setattr(hi, "classify", classify)
    manda_ts(ana, GASTO)
    resultado = []
    t = threading.Thread(target=lambda: resultado.append(manda_ts(ana, "saldo")))
    t.start()
    assert parou.wait(20)
    r = manda_ts(ana, "sim")
    solta.set()
    t.join(30)
    assert declaracoes(ana) == [] and r == [tx.AINDA_TERMINANDO.format(resposta="sim")], r  # R4-2
    assert resultado[0][0].startswith("🔕 Cancelei a pergunta anterior.") and pendencia(ana) != dc.TIPO


def test_sim_concorrente_durante_comando_que_nao_ajuda_e_o_sim_depois_grava(ana, liga, monkeypatch):
    """A janela de quem volta: o "sim" no meio não grava; a pergunta volta; o "sim" depois grava uma vez."""
    import threading
    import core.handle_incoming as hi
    import core.services.plan_service as ps
    monkeypatch.setattr(ps, "ai_chat_allowed", lambda uid: False)
    parou, solta = threading.Event(), threading.Event()
    real = hi.classify

    def classify(texto, *a, **k):
        if texto == "hmm":
            parou.set()
            assert solta.wait(20)
        return real(texto, *a, **k)
    monkeypatch.setattr(hi, "classify", classify)
    manda_ts(ana, GASTO)
    resultado = []
    t = threading.Thread(target=lambda: resultado.append(manda_ts(ana, "hmm")))
    t.start()
    assert parou.wait(20)
    assert manda_ts(ana, "sim") == [tx.AINDA_TERMINANDO.format(resposta="sim")]  # R4-2
    solta.set()
    t.join(30)
    assert resultado[0][0].endswith(f"\n\n{PERGUNTA}") and declaracoes(ana) == []
    time.sleep(1.1)  # o "sim" depois da pergunta de novo (o timestamp é de segundos)
    assert "registrada pelo banco" in manda_ts(ana, "sim")[0]
    assert manda_ts(ana, "sim") != [] and valores(ana) == [50.0]


PERGUNTA_60 = PERGUNTA.replace("50,00", "60,00")


@pytest.mark.parametrize("frase", ["sim, mas foi 60", "não, foi 60", "👍 mas foi 60", "sim sim, 60", "sim mas foi sessenta",
                                   "foi 60", "na verdade 60", "60", "o valor foi 60", "sim, foram 60",
                                   "sim mas foi R$ 60,00"])
@pytest.mark.parametrize("como", ["texto", "audio"])
def test_alteracao_de_valor_vira_nova_confirmacao(ana, liga, monkeypatch, frase, como):
    """Ajuste do dono: nova confirmação com R$ 60 (mesmo alvo, dia e tipo); a antiga fica inválida. Negativo (rodado):
    sem `_corrige` (a frase repete a pergunta), nada de R$ 60."""
    w = "wamid.orig-" + uuid.uuid4().hex
    manda_ts(ana, GASTO, w)
    a = viva(ana)["payload"]["livro_id"]
    r = manda_ts(ana, frase) if como == "texto" else fala(monkeypatch, ana, frase)
    assert r[-1].endswith(PERGUNTA_60), r
    nova = viva(ana)["payload"]
    assert nova["livro_id"] != a and nova["_declaracao"]["valor"] == 60.0
    assert manda_ts(ana, GASTO, w) == [tx.ACAO_ENCERRADA]  # a reentrega do original: a ação dela foi encerrada
    assert "registrada pelo banco" in manda_ts(ana, "sim")[0] and valores(ana) == [60.0]


def test_alteracao_reentregue_repete_a_nova_e_depois_do_sim_e_t3(ana, liga):
    manda_ts(ana, GASTO)
    c = "wamid.corr-" + uuid.uuid4().hex
    assert manda_ts(ana, "sim, mas foi 60", c) == [PERGUNTA_60]
    assert manda_ts(ana, "sim, mas foi 60", c) == [PERGUNTA_60] and pendencia(ana) == dc.TIPO
    manda_ts(ana, "sim")
    assert manda_ts(ana, "sim, mas foi 60", c) == [tx.REPETIDA.format(seq=1)] and valores(ana) == [60.0]


@pytest.mark.parametrize("frase", ["sim, mas foi uns 60", "sim mas foi mais", "sim, 60 ou 70", "não, foi 60,123",
                                   "sim mas foi 1,5kk", "sim, mas foi 60 ontem", "sim mas foi 60 na padaria",
                                   "sim, mas foi 60 em dinheiro", "sim, mas foi 60 no débito", "sim mas foi -60",
                                   "sim mas foi 6o", "sim, mas foi 0", "sim mas foi 600 no mercado e 20 na padaria",
                                   "foi 60 ontem", "na verdade 60 na padaria"])
def test_alteracao_sem_certeza_pede_de_novo_sem_deixar_o_valor_antigo(ana, liga, frase):
    """R4-4: mais que um valor, ou valor sem certeza: a confirmação sai PRIMEIRO e o texto do dono pede o gasto completo.
    Negativo (rodado): `declaracao_correcao.so_valor` aceitando qualquer valor que o parser lê deixa estes com uma
    confirmação nova."""
    manda_ts(ana, GASTO)
    r = manda_ts(ana, frase)
    assert r == [tx.CORRECAO_INCOMPLETA], r
    assert [x["estado"] for x in q("select estado from ia_declaracao_livro where user_id=%s and papel='acao'", (ana,),
                                   fetch=True)] == ["descartada"]
    assert pendencia(ana) != dc.TIPO and manda_ts(ana, "sim") == [tx.ACAO_ENCERRADA] and declaracoes(ana) == []


def test_alteracao_mandada_antes_da_confirmacao_nao_troca(ana, liga):
    manda_ts(ana, GASTO)
    t = seg(viva(ana)["created_at"])
    assert manda_ts(ana, "sim, mas foi 60", ts=str(t)) == [f"{tx.ENVIADA_ANTES}\n\n{PERGUNTA}"]
    assert viva(ana)["payload"]["_declaracao"]["valor"] == 50.0


NAOS_R3 = ["não, obrigado", "não registra", "cancela isso", "não não", "nope"]


@pytest.mark.parametrize("nao", NAOS_R3)
def test_recusas_novas(ana, liga, nao):
    """R3-2 A, com horário e reentrega. Negativo (rodado): `e_nao` igual ao `is_cancel` deixa as cinco sem cancelar."""
    manda_ts(ana, GASTO)
    t, w = seg(viva(ana)["created_at"]), "wamid.nao-" + uuid.uuid4().hex
    assert manda_ts(ana, nao, ts=str(t)) == [f"{tx.ENVIADA_ANTES}\n\n{PERGUNTA}"]  # empate: um "não" antigo não cancela
    assert manda_ts(ana, nao, w) == [tx.NAO_FIZ_NADA] and pendencia(ana) is None
    manda_ts(ana, PADARIA)
    assert manda_ts(ana, nao, w)[-1].endswith("R$ 30,00 em padaria, hoje, pelo banco*? Responde *sim* ou *não*.")
    assert pendencia(ana) == dc.TIPO and declaracoes(ana) == []  # o "não" reentregue não cancela a nova


@pytest.mark.parametrize("frase", ["não quero isso", "nem pensar"])
def test_nao_que_so_o_classificador_le(ana, liga, monkeypatch, frase):
    """R3-2 A: o `confirm.no` confiante do classificador (LLM simulado) vale como "não"; antes da pergunta, não."""
    _tier3(monkeypatch, "confirm.no", 0.95)
    manda_ts(ana, GASTO)
    t = seg(viva(ana)["created_at"])
    assert manda_ts(ana, frase, ts=str(t))[0].startswith(tx.ENVIADA_ANTES) and pendencia(ana) == dc.TIPO
    assert manda_ts(ana, frase) == [tx.NAO_FIZ_NADA] and pendencia(ana) is None and declaracoes(ana) == []


# ── a porta da IA no WhatsApp (R3-3 A e R3-1 nas duas portas) ─────────────────────────────────────────────────────
def _arma_ia(uid, monkeypatch, *depois):
    from tests._chat_ia_apoio import CONFIRMA, banco
    from tests._ia_falsa_helpers import openai_falso
    reg = openai_falso(monkeypatch, banco(), *depois)
    assert manda_ts(uid, "piggy " + GASTO) == [CONFIRMA]
    return reg, CONFIRMA


@pytest.mark.parametrize("forma", ["Sim, pode registrar", "sim sim", "simm", "👍", "pode registrar"])
def test_ia_aceita_as_formas_completas(ana_ia_rot, monkeypatch, forma):
    uid = ana_ia_rot
    _, confirma = _arma_ia(uid, monkeypatch)
    p = db.ai_get_pending_action(uid)
    t = seg(p["created_at"])
    assert manda_ts(uid, forma, ts=str(t)) == [f"{tx.ENVIADA_ANTES}\n\n{confirma}"]
    assert db.ai_get_pending_action(uid)["created_at"] == p["created_at"]
    assert "registrada pelo banco" in manda_ts(uid, forma)[0] and valores(uid) == [50.0]


@pytest.mark.parametrize("nao", NAOS_R3)
def test_ia_aceita_as_recusas_novas(ana_ia_rot, monkeypatch, nao):
    uid = ana_ia_rot
    _, confirma = _arma_ia(uid, monkeypatch)
    p = db.ai_get_pending_action(uid)
    t = seg(p["created_at"])
    assert manda_ts(uid, nao, ts=str(t)) == [f"{tx.ENVIADA_ANTES}\n\n{confirma}"]
    assert db.ai_get_pending_action(uid)["created_at"] == p["created_at"]
    assert manda_ts(uid, nao) == [tx.NAO_FIZ_NADA] and db.ai_get_pending_action(uid) is None and declaracoes(uid) == []


def test_ia_alteracao_de_valor_vira_nova_confirmacao(ana_ia_rot, monkeypatch):
    uid = ana_ia_rot
    _arma_ia(uid, monkeypatch)
    assert manda_ts(uid, "sim, mas foi 60") == [PERGUNTA_60]
    assert db.ai_get_pending_action(uid) is None and pendencia(uid) == dc.TIPO
    assert "registrada pelo banco" in manda_ts(uid, "sim")[0] and valores(uid) == [60.0]


def test_ia_alteracao_sem_certeza(ana_ia_rot, monkeypatch):
    uid = ana_ia_rot
    _arma_ia(uid, monkeypatch)
    assert manda_ts(uid, "sim, mas foi 60 na padaria") == [tx.CORRECAO_INCOMPLETA]
    assert db.ai_get_pending_action(uid) is None and pendencia(uid) != dc.TIPO and declaracoes(uid) == []


@pytest.mark.parametrize("cmd", PRESOS_TIER2[:3])
def test_ia_comando_atendido_cancela_a_confirmacao(ana_ia_rot, monkeypatch, cmd):
    from tests._ia_falsa_helpers import texto
    uid = ana_ia_rot
    _tier3(monkeypatch, "out_of_scope", 0.3)
    _arma_ia(uid, monkeypatch, texto("🐷 resposta da IA"))
    r = manda_ts(uid, cmd)[0]
    assert r.startswith("🔕 Cancelei a pergunta anterior.\n\n🐷 resposta da IA"), r
    assert db.ai_get_pending_action(uid) is None and manda_ts(uid, "sim")[0] != "" and declaracoes(uid) == []


def test_ia_comando_que_nao_ajuda_devolve_a_confirmacao_da_ia(ana_ia_rot, monkeypatch):
    from core.services.ai_chat.tools.meta import _report_out_of_scope_execute
    from tests._ia_falsa_helpers import texto
    uid = ana_ia_rot
    _tier3(monkeypatch, "out_of_scope", 0.3)
    _, confirma = _arma_ia(uid, monkeypatch, texto(_report_out_of_scope_execute(uid, {"reason": "t"})))
    assert manda_ts(uid, "me explica o pib")[0].endswith(f"\n\n{confirma}")
    assert db.ai_get_pending_action(uid) is not None
    assert "registrada pelo banco" in manda_ts(uid, "sim")[0] and valores(uid) == [50.0]


# ── R4 (decisões do dono de 2026-11-10, 4ª rodada) ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("tipo", ["vazio", "reacao", "reacao_joinha", "localizacao", "figurinha"])
def test_mensagem_sem_texto_nao_mexe_na_confirmacao(ana, liga, rt, monkeypatch, tipo):
    """R4-1: a reação (inclusive 👍), a localização, a figurinha e o texto vazio chegam ao core sem texto: a confirmação
    fica como estava. Negativo (rodado): sem o `_sem_texto` no `handle_incoming`, ela é suspensa e some ou renasce."""
    import adapters.whatsapp.wa_runtime as wr
    from adapters.whatsapp.wa_parse import InboundAttachmentRef, InboundMessage
    rt(ana, GASTO)
    antes = viva(ana)
    raw = {"id": "wamid.x-" + uuid.uuid4().hex,
           "type": {"vazio": "text", "reacao": "reaction", "reacao_joinha": "reaction", "localizacao": "location",
                    "figurinha": "sticker"}[tipo]}
    if tipo.startswith("reacao"):
        raw["reaction"] = {"message_id": "wamid.qualquer", "emoji": "👍" if tipo == "reacao_joinha" else "❤️"}
    atts = ([InboundAttachmentRef(media_id="m1", filename="sticker_m1", content_type="image/webp")]
            if tipo == "figurinha" else [])
    monkeypatch.setattr(wr, "_download_attachments_sync", lambda refs: [])
    wr.process_message(InboundMessage(wa_id="5511999998888", text="", attachments=atts, raw=raw,
                                      timestamp=str(int(time.time()) + 1)))
    depois = viva(ana)
    assert depois["created_at"] == antes["created_at"] and declaracoes(ana) == []
    assert [x["estado"] for x in q("select estado from ia_declaracao_livro where user_id=%s and papel='acao'", (ana,),
                                   fetch=True)] == ["armada"]
    assert "registrada pelo banco" in rt(ana, "👍")[0][0]  # a MENSAGEM de texto 👍 confirma


def _em_suspensao(ana, monkeypatch, cmd, resposta):
    """O comando `cmd` fica parado logo depois de suspender a confirmação; `resposta` chega nesse meio."""
    import threading
    parou, solta = threading.Event(), threading.Event()
    from core.handlers import declaracao_suspensao as ds
    real = ds.suspende

    def suspende(*a, **k):
        real(*a, **k)
        if threading.current_thread().name == "cmd":
            parou.set()
            assert solta.wait(20)
    monkeypatch.setattr(ds, "suspende", suspende)
    out = {}
    t = threading.Thread(target=lambda: out.__setitem__("cmd", manda_ts(ana, cmd)), name="cmd")
    t.start()
    assert parou.wait(20)
    out["resp"] = resposta()
    solta.set()
    t.join(30)
    return out


@pytest.mark.parametrize("rep", range(10))
@pytest.mark.parametrize("cmd", ["saldo", "blablabla xyz"])
def test_sim_durante_a_suspensao_recebe_ainda_terminando(ana, liga, monkeypatch, cmd, rep):
    """R4-2: nunca T19; o "sim" no meio não grava. Comando atendido: a confirmação acaba com o aviso. Comando que não
    ajudou: a pergunta volta e o "sim" depois grava uma vez. Negativo (rodado): a suspensão apagando a pendência (como
    na r3) dá T19 a esse "sim"."""
    import core.services.plan_service as ps
    monkeypatch.setattr(ps, "ai_chat_allowed", lambda uid: False)
    manda_ts(ana, GASTO)
    out = _em_suspensao(ana, monkeypatch, cmd, lambda: manda_ts(ana, "sim"))
    assert out["resp"] == [tx.AINDA_TERMINANDO.format(resposta="sim")] and declaracoes(ana) == []
    if cmd == "saldo":
        assert out["cmd"][0].startswith("🔕 Cancelei a pergunta anterior.") and pendencia(ana) != dc.TIPO
        return
    assert out["cmd"][0].endswith(PERGUNTA) and pendencia(ana) == dc.TIPO
    time.sleep(1.1)
    assert "registrada pelo banco" in manda_ts(ana, "sim")[0] and valores(ana) == [50.0]


@pytest.mark.parametrize("como", ["não", "botao", "audio"])
def test_resposta_durante_a_suspensao_outras_formas(ana, liga, monkeypatch, como):
    import core.services.plan_service as ps
    monkeypatch.setattr(ps, "ai_chat_allowed", lambda uid: False)
    manda_ts(ana, GASTO)
    a = viva(ana)["payload"]["livro_id"]
    resposta = {"não": lambda: manda_ts(ana, "não"),
                "botao": lambda: manda_ts(ana, "Sim", botao=f"decl_sim:{a}"),
                "audio": lambda: fala(monkeypatch, ana, "sim")}[como]
    out = _em_suspensao(ana, monkeypatch, "blablabla xyz", resposta)
    esperado = tx.AINDA_TERMINANDO.format(resposta="não" if como == "não" else "sim")
    assert out["resp"][-1].endswith(esperado) and declaracoes(ana) == [] and pendencia(ana) == dc.TIPO, out


def test_marcas_de_nao_ajudou_vem_da_fonte():
    """R4-3 (§0.7): as respostas reais de quem não atendeu, tiradas das funções e constantes de origem, são
    reconhecidas; a resposta atendida não. Falha quando a frase da fonte muda sem a constante (rodado: a dica
    `infer_contextual_fallback` com outro começo fica vermelha)."""
    import core.handle_incoming as hi
    from core.handlers import credit, help_handler
    from core.intent_router import NOT_UNDERSTOOD_MSG, OUT_OF_SCOPE_MSG
    from core.services import ofx_service, statement_service
    from core.services.ai_chat.runner import ERROR_MSG
    from core.services.ai_chat.tools.meta import _FALLBACK_BASE, _FALLBACK_ALTERNATIVES
    respostas = [
        help_handler.infer_contextual_fallback("xyzzy", "whatsapp"),
        help_handler._prepend_not_understood("caixinhas", "• `caixinhas`"),
        credit.contextual_help("comprar no cartao"), credit.contextual_help("pagar fatura"),
        NOT_UNDERSTOOD_MSG, OUT_OF_SCOPE_MSG, f"{_FALLBACK_BASE}\n\n{_FALLBACK_ALTERNATIVES}", ERROR_MSG,
        hi.AUDIO_SEM_LANCAMENTO, hi.ERRO_INTERNO, hi.IMAGEM_SO_NOS_PAGOS, hi.IMAGEM_NAO_BAIXOU, hi.IMAGEM_NAO_ANALISOU,
        hi.IMAGEM_SEM_DADO_FINANCEIRO, hi.ANEXO_NAO_BAIXOU.format(ext="PDF", kind="pdf"),
        statement_service.NAO_IMPORTOU.format(tipo="PDF", erro="arquivo inválido"), statement_service._PRO_REQUIRED_STATEMENT_MSG,
        ofx_service.FATURA_NAO_IMPORTOU.format(cartao="Nubank", erro="x"), ofx_service._PRO_REQUIRED_OFX_MSG,
        ofx_service.FATURA_DETECTADA + "Cadastre um cartão primeiro.",
    ]
    for r in respostas:
        assert r and hi._nao_ajudou(r), r
    assert not hi._nao_ajudou("💰 *Saldo*: R$ 10,00") and not hi._nao_ajudou(tx.NAO_FIZ_NADA)


@pytest.mark.parametrize("falha", ["nao_baixou", "nao_analisou", "sem_dado"])
def test_falha_de_imagem_devolve_a_confirmacao(ana, liga, monkeypatch, falha):
    import core.handle_incoming as hi
    from core.types import Attachment, IncomingMessage
    manda_ts(ana, GASTO)
    monkeypatch.setattr(hi, "analyze_image", lambda d, f: None if falha == "nao_analisou" else
                        {"tem_dado_financeiro": False})
    dado = b"" if falha == "nao_baixou" else b"x"
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=ana, text="", message_id="wamid.img-" + uuid.uuid4().hex,
        attachments=[Attachment(filename="foto.jpg", content_type="image/jpeg", data=dado)], external_id=str(ana),
        raw={}, enviado_em=str(int(time.time()) + 1)))
    assert out[0].text.endswith(f"\n\n{PERGUNTA}") and pendencia(ana) == dc.TIPO, out[0].text


@pytest.mark.parametrize("oi", ["oi", "olá", "bom dia", "e aí"])
def test_saudacao_sempre_devolve_a_confirmacao(ana, liga, monkeypatch, oi):
    """R4-3: pelo intent, não pelo texto (as variantes sorteadas da saudação). Negativo (rodado): sem a saudação no
    `desfecho`, a confirmação acaba com o aviso."""
    _ia_responde(monkeypatch)
    manda_ts(ana, GASTO)
    r = manda_ts(ana, oi)[0]
    assert r.endswith(f"\n\n{PERGUNTA}") and not r.startswith("🔕") and pendencia(ana) == dc.TIPO, r


def test_correcao_depois_de_expirar_e_o_texto_de_expirou(ana, liga, monkeypatch):
    """R4-5: dentro das 24 h, o texto do dono; fora delas, o fluxo de antes."""
    monkeypatch.setattr(dc, "PRAZO", timedelta(0))
    manda_ts(ana, GASTO)
    assert manda_ts(ana, "sim, mas foi 60") == [tx.EXPIROU]
    assert manda_ts(ana, "foi 60") == [tx.EXPIROU] and declaracoes(ana) == []
    monkeypatch.setattr(dc, "JANELA", timedelta(0))
    assert manda_ts(ana, "sim, mas foi 60") != [tx.EXPIROU]


# ── R5 (decisões do dono de 2026-11-10, 5ª rodada) ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("frase", ["obrigado", "valeu", "sim, obrigado", "sim por favor", "👍 valeu"])
def test_cortesia_sozinha_repete_a_pergunta(ana, liga, frase):
    """M-1 A. Negativo (rodado): sem o ramo de cortesia, "obrigado" vira outro comando e a confirmação acaba."""
    manda_ts(ana, GASTO)
    antes = viva(ana)
    assert manda_ts(ana, frase) == [PERGUNTA]
    assert viva(ana)["created_at"] == antes["created_at"] and declaracoes(ana) == []


@pytest.mark.parametrize("frase", ["ok, qual meu saldo?", "beleza, quanto tenho na caixinha viagem", "isso, e meu saldo?",
                                   "não, quero ver meu saldo"])
def test_cortesia_mais_comando_e_outro_comando(ana, liga, monkeypatch, frase):
    """M-1 A: cancela com o aviso e o comando roda. Negativo (rodado): a resposta com algo a mais repetindo a pergunta
    (o comportamento da r4) deixa estes presos."""
    _ia_responde(monkeypatch)
    manda_ts(ana, GASTO)
    r = manda_ts(ana, frase)[0]
    assert r.startswith("🔕 Cancelei a pergunta anterior.\n\n") and r.count("🔕") == 1, r
    assert pendencia(ana) != dc.TIPO and manda_ts(ana, "sim") == [tx.ACAO_ENCERRADA] and declaracoes(ana) == []


def test_aviso_nao_duplica_quando_o_route_ja_avisou(ana_ia_rot, monkeypatch):
    """r4#2: a pergunta de forma do roteador e a confirmação da IA vivas; "saldo" cancela as duas, e o aviso sai uma
    vez só. Negativo (rodado): o invólucro sem olhar o aviso do `route` põe dois."""
    uid = ana_ia_rot
    manda_ts(uid, "gastei 50 no mercado")
    assert pendencia(uid) == "payment_method_choice"
    _arma_ia(uid, monkeypatch)
    r = manda_ts(uid, "saldo")[0]
    assert r.count("🔕 Cancelei a pergunta anterior.") == 1, r


@pytest.mark.parametrize("frase", ["o valor é 60", "é 60", "foi 60,00"])
def test_correcao_com_o_vocabulario_unico(ana, liga, frase):
    manda_ts(ana, GASTO)
    assert manda_ts(ana, frase) == [PERGUNTA_60] and viva(ana)["payload"]["_declaracao"]["valor"] == 60.0


@pytest.mark.parametrize("frase", ["60 ontem", "60 na padaria", "R$ 60 em dinheiro", "o valor é 60 ontem"])
def test_valor_com_mais_alguma_coisa_invalida_e_pede_o_gasto_completo(ana, liga, frase):
    """Nada da mensagem é descartado: "60 ontem" não vira R$ 60 hoje. Negativo (rodado): sem o começo por valor em
    `correcao`, "60 ontem" vira outro comando e a confirmação anterior fica."""
    manda_ts(ana, GASTO)
    assert manda_ts(ana, frase) == [tx.CORRECAO_INCOMPLETA]
    assert pendencia(ana) != dc.TIPO and manda_ts(ana, "sim") == [tx.ACAO_ENCERRADA] and declaracoes(ana) == []


def test_pdf_que_nao_importa_devolve_a_confirmacao(ana, liga):
    import core.handle_incoming as hi
    from core.types import Attachment, IncomingMessage
    manda_ts(ana, GASTO)
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=ana, text="", message_id="wamid.pdf-" + uuid.uuid4().hex,
        attachments=[Attachment(filename="extrato.pdf", content_type="application/pdf", data=b"%PDF-1.4 lixo")],
        external_id=str(ana), raw={}, enviado_em=str(int(time.time()) + 1)))
    assert out[0].text.endswith(f"\n\n{PERGUNTA}") and pendencia(ana) == dc.TIPO, out[0].text


def test_botao_com_a_flag_desligada_encerra_so_a_confirmacao_dele(ana, liga, rt, monkeypatch):
    """Sem laço: o toque responde como o "sim" digitado (`msg_banco`), sem pergunta nem botão de novo. Negativo
    (rodado): o ramo de flag desligada do `_toque` repetindo a pergunta faz os botões voltarem."""
    from core.handlers import forma_pagamento as fp
    rt(ana, GASTO)
    a = viva(ana)["payload"]["livro_id"]
    monkeypatch.delenv("DECLARACAO_BANCO")
    assert rt(ana, "Sim", f"decl_sim:{a}") == [(fp.msg_banco(ana, "despesa", 50.0), [])]
    assert pendencia(ana) is None and declaracoes(ana) == []


def test_botao_antigo_com_a_flag_desligada_nao_toca_outra_pendencia(ana, liga, rt, monkeypatch):
    rt(ana, "gastei 10 no mercado em dinheiro")
    rt(ana, GASTO)
    a = viva(ana)["payload"]["livro_id"]
    monkeypatch.delenv("DECLARACAO_BANCO")
    rt(ana, "apagar #1")
    apagar = db.get_pending_action(ana)
    assert apagar["action_type"] == "delete_launch"
    ((corpo, botoes),) = rt(ana, "Sim", f"decl_sim:{a}")
    assert corpo == tx.BOTAO_VELHO and not any(b.startswith("decl_") for b in botoes)  # os da exclusão são dela
    assert db.get_pending_action(ana)["created_at"] == apagar["created_at"] and n_lancamentos(ana) == 1


def test_formas_novas_so_no_whatsapp(monkeypatch):
    """M-8 A, nos dois sentidos: no Discord "pode registrar" não é "sim" (não vai ao livro e não grava); no WhatsApp é
    (`test_ia_aceita_as_formas_completas`). Negativo (rodado): `registrar_mensagem` sem olhar o canal anota no livro."""
    from tests._chat_ia_apoio import CONFIRMA, banco, dc as discord, livro as livro_ia
    from tests._ia_falsa_helpers import openai_falso, texto
    from tests.test_declaracao_b_chat_ia_discord import snowflake
    from conftest import promote_to_pro
    from tests.test_bank_movements import _bank
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    promote_to_pro(uid)
    _bank(uid)
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    openai_falso(monkeypatch, banco(), texto("Não entendi."))
    assert discord(uid, "piggy " + GASTO, snowflake()) == CONFIRMA
    discord(uid, "pode registrar", snowflake())  # como na base: mensagem nova para a IA, que desiste da pendência
    assert declaracoes(uid) == [] and [r for r in livro_ia(uid) if r["papel"] != "acao"] == []


def test_24h_so_para_o_texto(ana, liga, monkeypatch):
    """R2-1: a janela de 24 h só limita o texto de "expirou"; a reentrega do original continua reconhecida."""
    w1, w2 = "wamid.j1-" + uuid.uuid4().hex, "wamid.j2-" + uuid.uuid4().hex
    manda_ts(ana, GASTO, w1)
    manda_ts(ana, "sim")
    monkeypatch.setattr(dc, "PRAZO", timedelta(0))
    manda_ts(ana, PADARIA, w2)
    monkeypatch.setattr(dc, "JANELA", timedelta(0))
    assert manda_ts(ana, GASTO, w1) == [tx.REPETIDA.format(seq=1)]
    assert manda_ts(ana, PADARIA, w2) == [tx.ACAO_ENCERRADA] and valores(ana) == [50.0]


def test_r2_7_sim_depois_de_a_confirmacao_da_ia_expirar(ana_ia_rot, monkeypatch):
    import db.ai_chat as aic
    uid = ana_ia_rot
    _arma_ia(uid, monkeypatch)
    monkeypatch.setattr(aic, "PENDING_TTL_MINUTES", 0)
    monkeypatch.setattr(dc, "PRAZO", timedelta(0))
    assert manda_ts(uid, "sim") == [tx.EXPIROU] and declaracoes(uid) == []


@pytest.mark.parametrize("quando", ["antes", "empate"])
def test_nao_antigo_contra_a_confirmacao_nova_da_ia(ana_ia_rot, monkeypatch, quando):
    uid = ana_ia_rot
    _, confirma = _arma_ia(uid, monkeypatch)
    p = db.ai_get_pending_action(uid)
    t = seg(p["created_at"])
    assert manda_ts(uid, "não", ts=str(t - 1 if quando == "antes" else t)) == [f"{tx.ENVIADA_ANTES}\n\n{confirma}"]
    assert db.ai_get_pending_action(uid)["created_at"] == p["created_at"] and declaracoes(uid) == []


def test_dp13_audio_com_pendencia_da_ia_viva(ana_ia_rot, monkeypatch):
    from tests._ia_falsa_helpers import chamada, com_tools, openai_falso, texto
    uid = ana_ia_rot
    db.upsert_budget(uid, "mercado", 300)
    openai_falso(monkeypatch, com_tools(chamada("set_budget", {"categoria": "mercado", "budget": 500})),
                 texto("Confirma?"))
    manda_ts(uid, "piggy meu orçamento de mercado é 500")
    assert db.ai_get_pending_action(uid)
    assert PERGUNTA in fala(monkeypatch, uid, GASTO)[0]
    assert pendencia(uid) == dc.TIPO and db.ai_get_pending_action(uid) is None
    assert "registrada pelo banco" in manda_ts(uid, "sim")[0] and db.get_budget(uid, "mercado")["budget"] == 300


# ── Codex P1 (PR #1517): a confirmação da IA suspensa não volta por cima de uma mais nova ─────────────────────────
def _ia_suspensa_que_nao_ajuda(uid, monkeypatch):
    """A confirmação da IA armada e o comando seguinte ("blablabla xyz") que a IA não atende."""
    from core.services.ai_chat.tools.meta import _report_out_of_scope_execute
    _, confirma = _arma_ia(uid, monkeypatch)
    _tier3(monkeypatch, "out_of_scope", 0.3)
    _ia_responde(monkeypatch, _report_out_of_scope_execute(uid, {"reason": "t"}))
    return confirma


def _acoes(uid):
    return q("select chave, estado from ia_declaracao_livro where user_id=%s and papel='acao' order by id", (uid,),
             fetch=True)


def test_ia_suspensa_nao_volta_se_a_marca_foi_trocada_por_declaracao_nova(ana_ia_rot, monkeypatch):
    """(1) A marca da suspensão é desalojada por uma declaração nova do roteador durante o comando: a pergunta velha da
    IA não volta, a ação dela fica `descartada`, a nova fica intacta e o "sim" grava a nova uma vez. Negativo (rodado):
    o `desfecho` antigo (restaurar sem conferir a marca) devolve a pergunta da IA e deixa duas confirmações vivas."""
    uid = ana_ia_rot
    confirma = _ia_suspensa_que_nao_ajuda(uid, monkeypatch)
    out = _em_suspensao(uid, monkeypatch, "blablabla xyz", lambda: manda_ts(uid, PADARIA))
    assert "R$ 30,00 em padaria" in out["resp"][-1], out
    assert confirma not in out["cmd"][0] and db.ai_get_pending_action(uid) is None, out
    nova = viva(uid)
    assert nova["payload"]["_declaracao"]["valor"] == 30.0
    assert [a["estado"] for a in _acoes(uid)] == ["descartada", "armada"]
    time.sleep(1.1)
    assert "registrada pelo banco" in manda_ts(uid, "sim")[0] and valores(uid) == [30.0]


def test_ia_suspensa_volta_sem_interferencia(ana_ia_rot, monkeypatch):
    """(2) Controle positivo: sem nada no meio, a pergunta da IA volta e o "sim" grava a da IA."""
    uid = ana_ia_rot
    confirma = _ia_suspensa_que_nao_ajuda(uid, monkeypatch)
    assert manda_ts(uid, "blablabla xyz")[0].endswith(f"\n\n{confirma}") and db.ai_get_pending_action(uid)
    time.sleep(1.1)
    # "pode registrar": o gancho decide (o "sim" exato iria ao runner, que aqui está trocado pela IA que não ajuda)
    assert "registrada pelo banco" in manda_ts(uid, "pode registrar")[0] and valores(uid) == [50.0]


def test_ia_suspensa_sem_marca_volta_como_hoje(ana_ia_rot, monkeypatch):
    """(3) A linha do roteador já tinha outra pergunta (a marca nunca existiu) e nenhuma declaração nova nasceu nela:
    a pergunta da IA volta, como antes do conserto."""
    uid = ana_ia_rot
    manda_ts(uid, "gastei 10 no mercado em dinheiro")
    manda_ts(uid, "apagar #1")
    apagar = db.get_pending_action(uid)
    assert apagar["action_type"] == "delete_launch"
    confirma = _ia_suspensa_que_nao_ajuda(uid, monkeypatch)
    r = manda_ts(uid, "blablabla xyz")[0]
    assert r.endswith(f"\n\n{confirma}") and db.ai_get_pending_action(uid) is not None, r
    assert db.get_pending_action(uid)["created_at"] == apagar["created_at"] and declaracoes(uid) == []


@pytest.mark.parametrize("porta", ["roteador", "ia"])
@pytest.mark.parametrize("linha", ["livre", "ocupada"])
def test_suspensa_nao_volta_se_nasceu_declaracao_nova_da_ia(ana_ia_rot, monkeypatch, porta, linha):
    """Irmão do P1 (Tester): com a confirmação suspensa (do roteador ou da IA) e o comando que não ajuda parado, outra
    mensagem arma uma declaração NOVA da IA (R$ 70). A velha não volta (fica `descartada`), a nova fica e o "sim" grava
    R$ 70 uma vez. Negativo (rodado): `abandona` sem a marca suspensa (só a confirmação viva, como no R2-6 original), na
    porta do roteador a de R$ 50 volta e o "sim" grava R$ 50."""
    import threading
    import core.services.ai_chat as m
    from core.handlers import declaracao_suspensao as ds
    from core.services.ai_chat.tools.meta import _FALLBACK_BASE
    from tests._chat_ia_apoio import banco
    from tests._ia_falsa_helpers import openai_falso, texto
    uid = ana_ia_rot
    if linha == "ocupada":
        openai_falso(monkeypatch, texto("x"))
        manda_ts(uid, "gastei 30 na padaria")  # a pergunta de forma na linha do roteador
    openai_falso(monkeypatch, *([banco()] if porta == "ia" else []), banco(70, alvo="farmacia"),
                 *[texto("y") for _ in range(4)])
    manda_ts(uid, ("piggy " if porta == "ia" else "") + GASTO)
    real_chat, real_suspende = m.chat, ds.suspende
    parou, solta = threading.Event(), threading.Event()

    def chat(u, t, **k):  # o comando parado cai no fallback da IA, que não ajuda
        return _FALLBACK_BASE if threading.current_thread().name == "cmd" else real_chat(u, t, **k)

    def suspende(*a, **k):
        real_suspende(*a, **k)
        if threading.current_thread().name == "cmd":
            parou.set()
            assert solta.wait(20)
    monkeypatch.setattr(m, "chat", chat)
    monkeypatch.setattr(ds, "suspende", suspende)
    out = {}
    t = threading.Thread(target=lambda: out.__setitem__("cmd", manda_ts(uid, "blablabla xyz")), name="cmd")
    t.start()
    assert parou.wait(20)
    manda_ts(uid, "piggy gastei 70 na farmacia no pix")
    nova = db.ai_get_pending_action(uid)
    solta.set()
    t.join(30)
    assert nova and nova["tool_args"]["_declaracao"]["valor"] == 70.0
    assert out["cmd"][0].startswith("🔕 Cancelei a pergunta anterior.") and "R$ 50,00" not in out["cmd"][0], out
    assert pendencia(uid) not in (dc.TIPO, dc.SUSPENSA_TIPO)
    assert db.ai_get_pending_action(uid)["created_at"] == nova["created_at"]
    assert [a["estado"] for a in _acoes(uid)][0] == "descartada"
    time.sleep(1.1)
    manda_ts(uid, "sim")
    assert valores(uid) == [70.0]


def _parado_em(monkeypatch, funcao, quando):
    """O worker "cmd" para dentro de `db.<funcao>` (antes de executá-la) quando `quando(*args, **kwargs)`."""
    import threading
    parou, solta = threading.Event(), threading.Event()
    real = getattr(db, funcao)

    def no_meio(*a, **k):
        if threading.current_thread().name == "cmd" and quando(*a, **k):
            parou.set()
            assert solta.wait(20)
        return real(*a, **k)
    monkeypatch.setattr(db, funcao, no_meio)
    return parou, solta


def _comando_parado(uid, monkeypatch, parou, solta, no_meio):
    import threading
    out = {}
    t = threading.Thread(target=lambda: out.__setitem__("cmd", manda_ts(uid, "blablabla xyz")), name="cmd")
    t.start()
    assert parou.wait(20)
    out["meio"] = no_meio()
    solta.set()
    t.join(30)
    return out


def test_janela_entre_ler_a_marca_e_restaurar_porta_do_roteador(ana_ia_rot, monkeypatch):
    """A IA arma uma declaração nova (R$ 70) depois de o `desfecho` ler a marca e antes de restaurá-la: ela consome a
    marca (R2-6 estendido) e o `advance` do `desfecho` perde o CAS; a de R$ 50 não volta e o "sim" grava R$ 70.
    Negativo (rodado): `abandona` sem a marca suspensa deixa as duas vivas e o "sim" grava R$ 50."""
    import core.services.ai_chat as m
    from core.services.ai_chat.tools.meta import _FALLBACK_BASE
    from tests._chat_ia_apoio import banco
    from tests._ia_falsa_helpers import openai_falso, texto
    import threading
    uid = ana_ia_rot
    _tier3(monkeypatch, "out_of_scope", 0.3)
    openai_falso(monkeypatch, banco(70, alvo="farmacia"), *[texto("y") for _ in range(3)])
    manda_ts(uid, GASTO)
    real_chat = m.chat
    monkeypatch.setattr(m, "chat", lambda u, t, **k: _FALLBACK_BASE if threading.current_thread().name == "cmd"
                        else real_chat(u, t, **k))
    parou, solta = _parado_em(monkeypatch, "advance_pending_action",
                              lambda *a, **k: k.get("new_action_type") == "confirm_declaracao_banco")
    out = _comando_parado(uid, monkeypatch, parou, solta, lambda: manda_ts(uid, "piggy gastei 70 na farmacia no pix"))
    assert out["cmd"][0].startswith("🔕 Cancelei a pergunta anterior.") and "R$ 50,00" not in out["cmd"][0], out
    assert pendencia(uid) not in (dc.TIPO, dc.SUSPENSA_TIPO)
    assert db.ai_get_pending_action(uid)["tool_args"]["_declaracao"]["valor"] == 70.0
    assert [a["estado"] for a in _acoes(uid)][0] == "descartada"
    time.sleep(1.1)
    manda_ts(uid, "sim")
    assert valores(uid) == [70.0]


def test_janela_entre_tirar_a_marca_e_restaurar_porta_da_ia(ana_ia_rot, monkeypatch):
    """Simétrico: a da IA suspensa; o roteador arma R$ 30 depois de o `desfecho` tirar a marca e antes de restaurar a
    da IA. A restaurada vê a do roteador e sai; fica só a de R$ 30. Negativo (rodado): sem a conferência depois de
    restaurar, as duas ficam vivas."""
    uid = ana_ia_rot
    _ia_suspensa_que_nao_ajuda(uid, monkeypatch)
    parou, solta = _parado_em(monkeypatch, "ai_set_pending_action", lambda *a, **k: True)
    out = _comando_parado(uid, monkeypatch, parou, solta, lambda: manda_ts(uid, PADARIA))
    assert out["cmd"][0].startswith("🔕 Cancelei a pergunta anterior."), out
    assert db.ai_get_pending_action(uid) is None and viva(uid)["payload"]["_declaracao"]["valor"] == 30.0
    assert [a["estado"] for a in _acoes(uid)][0] == "descartada"
    time.sleep(1.1)
    assert "registrada pelo banco" in manda_ts(uid, "sim")[0] and valores(uid) == [30.0]
