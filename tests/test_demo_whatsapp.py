""""Testar o Piggy" (demo no WhatsApp): a conversa, pela porta real.

`process_message` com Postgres real; só o envio (`wa_client.*`) e o modelo
(`conversa._chamar_modelo`) são falsos (infra em tests/_demo_whatsapp_helpers.py).
LLM mockado NÃO prova a qualidade das respostas nem a lista no aparelho: isso só
se vê no WhatsApp de verdade. As garantias (nada salvo, retenção, prompt) estão
em tests/test_demo_whatsapp_garantias.py.

Controles negativos (medidos no PR; marcados [N]):
- [N] apagar o desvio `wa_demo.decidir` em wa_runtime.process_message deixa
  vermelho `test_nada_do_usuario_e_salvo` (garantias);
- [N] tirar `and msgs_used < %s` de `reservar_mensagem` deixa vermelho
  `test_reserva_e_atomica_sob_concorrencia`;
- [N] `numero_tem_conta` sempre False: `test_conta_real_nao_entra_no_demo[*]` fica
  vermelho (o `test_negativo_*` faz isso por monkeypatch e exige o demo atendendo
  a conta, para provar que o teste mede);
- [N] tirar o `_seen_recent` do desvio deixa vermelho `test_reentrega_nao_conta_duas_vezes`;
- [N] DEMO_DAILY_MAX=0: `test_interruptor_desligado_cai_no_aviso_de_cadastro` é o
  negativo permanente (o mesmo número volta ao fluxo normal e cria user_identities).
Positivos: número só-WhatsApp (sem conta) usa o demo; a conta real segue o fluxo
normal; `vincular 123456` em sessão segue o fluxo normal.
"""
import importlib
import json
import math
import re
import threading
import uuid
from decimal import Decimal

import pytest

import db
from _demo_whatsapp_helpers import (  # noqa: F401  (mundo e _demo_limpo são fixtures)
    GATILHO, _abre, _conta_com_telefone, _demo_limpo, _liga_por_codigo, _manda, _msg,
    _numero, _q, _sessao, _um, mundo,
)
from adapters.whatsapp import wa_client, wa_demo
from adapters.whatsapp import wa_runtime as wr
from adapters.whatsapp.wa_parse import InboundAttachmentRef, InboundMessage
from core import dashboard_links
from core.crypto import hash_pii
from core.services.ai_chat import system_prompt as sp
from core.services.ai_chat.runner import ERROR_MSG
from core.services.demo import conversa, dados
from db import demo_funnel as funil
from utils_text import fmt_brl


def test_interruptor_desligado_cai_no_aviso_de_cadastro(mundo, monkeypatch):
    """Negativo permanente: com DEMO_DAILY_MAX=0 o MESMO número vai ao fluxo normal."""
    monkeypatch.setenv("DEMO_DAILY_MAX", "0")
    n = _numero()
    _manda(n, GATILHO)
    assert mundo.demo() == [] and mundo.chamadas == []
    assert any("Ainda não tenho uma conta" in c for _, _, c in mundo.normal()), mundo.saida
    assert _sessao(n) is None
    assert _um("select count(*) as n from user_identities where external_id_hash = %s",
               (hash_pii(n, kind="external_id"),))["n"] == 1


def test_teto_ilegivel_tambem_desliga(mundo, monkeypatch):
    monkeypatch.setenv("DEMO_DAILY_MAX", "muitos")
    _manda(_numero(), GATILHO)
    assert mundo.demo() == []


# ── 2. limite de 8 ───────────────────────────────────────────────────────────

def test_limite_de_oito_mensagens(mundo):
    n = _numero()
    _abre(n)
    code = _sessao(n)["code"]
    assert mundo.saida[0][1] == "lista" and mundo.saida[0][2] == wa_demo.BOAS_VINDAS

    for i in range(8):
        _manda(n, f"pergunta {i}")
    textos = mundo.textos()
    assert len(mundo.chamadas) == 8 and len(textos) == 8
    assert wa_demo.ULTIMA in textos[6], "a 7ª devia avisar que a próxima é a última"
    assert wa_demo.ULTIMA not in textos[5] and wa_demo.ULTIMA not in textos[7]
    assert f"/t/{code}" in textos[7], "a 8ª devia trazer o link final"
    assert f"/t/{code}" not in textos[6]
    # só a 8ª chamada leva a linha "última resposta" (sem gancho); as 7 primeiras, nunca
    tem_ultima = [any(m["content"] == conversa.ULTIMA_RESPOSTA for m in c) for c in mundo.chamadas]
    assert tem_ultima == [False] * 7 + [True]
    s = _sessao(n)
    assert s["msgs_used"] == 8 and s["first_answer_at"] is not None and s["limit_at"] is not None

    _manda(n, "nona pergunta")
    assert len(mundo.chamadas) == 8, "a 9ª não pode chamar o modelo"
    assert f"/t/{code}" in mundo.textos()[-1]
    assert _sessao(n)["msgs_used"] == 8


def test_gatilho_de_novo_reenvia_a_lista_sem_contar(mundo):
    n = _numero()
    _abre(n)
    _manda(n, GATILHO)  # 2º clique do mesmo número: sessão viva vence
    assert [s[1] for s in mundo.demo()] == ["lista", "lista"]
    assert _um("select count(*) as n from demo_sessions where wa_hash = %s", (funil.wa_hash(n),))["n"] == 1
    assert _sessao(n)["msgs_used"] == 0 and mundo.chamadas == []


# ── 3. concorrência ──────────────────────────────────────────────────────────

def _corre(n_threads, fn):
    saida, barreira = [], threading.Barrier(n_threads)

    def alvo():
        barreira.wait()
        saida.append(fn())

    ts = [threading.Thread(target=alvo) for _ in range(n_threads)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return saida


def test_reserva_e_atomica_sob_concorrencia():
    h = funil.wa_hash(_numero())
    code = funil.abrir_sessao(None, h, 10**6)
    assert code
    _q("update demo_sessions set msgs_used = 7 where code = %s", (code,))
    r = _corre(2, lambda: funil.reservar_mensagem(code))
    assert sorted(x for x in r if x) == [8] and r.count(None) == 1, r

    _q("update demo_sessions set msgs_used = 0 where code = %s", (code,))
    r = _corre(12, lambda: funil.reservar_mensagem(code))
    assert sorted(x for x in r if x) == list(range(1, 9)), r
    assert _um("select msgs_used from demo_sessions where code = %s", (code,))["msgs_used"] == 8


# ── 4. conta real não entra ──────────────────────────────────────────────────

@pytest.mark.parametrize("como", ["telefone", "codigo"])
def test_conta_real_nao_entra_no_demo(mundo, como):
    n = _numero()
    if como == "telefone":
        _conta_com_telefone(n)
    else:
        _liga_por_codigo(n)
        mundo.saida.clear()

    _manda(n, GATILHO)
    _manda(n, "como estão meus gastos?")
    assert mundo.chamadas == [] and mundo.demo() == [], mundo.saida
    assert _sessao(n) is None
    assert _um("select count(*) as n from user_identities where external_id_hash = %s",
               (hash_pii(n, kind="external_id"),))["n"] >= 1, "o fluxo normal devia ter rodado"


def test_negativo_sem_a_guarda_o_demo_atende_quem_tem_conta(mundo, monkeypatch):
    """Mede o teste de cima: sem `numero_tem_conta` o demo abre sessão para a conta."""
    monkeypatch.setattr(funil, "numero_tem_conta", lambda wa_id: False)
    n = _numero()
    _conta_com_telefone(n)
    _manda(n, GATILHO)
    assert _sessao(n) is not None and mundo.demo()


def test_conta_criada_depois_da_sessao_vai_ao_fluxo_normal(mundo):
    n = _numero()
    _abre(n)
    _manda(n, "primeira pergunta")
    assert len(mundo.chamadas) == 1
    _conta_com_telefone(n)  # a pessoa se cadastrou com este número no meio do teste

    _manda(n, "segunda pergunta")
    assert len(mundo.chamadas) == 1, "a conta nova não pode continuar no demo"
    assert _sessao(n)["msgs_used"] == 1


def test_so_whatsapp_sem_conta_usa_o_demo(mundo):
    """Positivo: usuário canônico criado por mensagem anterior, sem conta, PODE testar."""
    n = _numero()
    db.get_or_create_canonical_user("whatsapp", n)
    _manda(n, GATILHO)
    assert _sessao(n) is not None and mundo.demo()[0][1] == "lista"



# ── 6. reentrega ─────────────────────────────────────────────────────────────

def test_reentrega_nao_conta_duas_vezes(mundo):
    n = _numero()
    _abre(n)
    mid = f"wamid.{uuid.uuid4().hex}"
    _manda(n, "quanto gastei?", msg_id=mid)
    _manda(n, "quanto gastei?", msg_id=mid)
    assert len(mundo.chamadas) == 1 and len(mundo.textos()) == 1
    assert _sessao(n)["msgs_used"] == 1


# ── 7. bordas ────────────────────────────────────────────────────────────────

def test_vincular_em_sessao_segue_o_fluxo_normal(mundo):
    n = _numero()
    _abre(n)
    mundo.saida.clear()
    _manda(n, "vincular 123456")
    assert mundo.chamadas == [] and mundo.demo() == []
    assert any("Código inválido" in c for _, _, c in mundo.normal()), mundo.saida
    assert _sessao(n)["msgs_used"] == 0


def test_audio_em_sessao_pede_texto_e_nao_conta(mundo):
    n = _numero()
    _abre(n)
    anexo = InboundAttachmentRef(media_id="m1", filename="a.ogg", content_type="audio/ogg")
    _manda(n, "", attachments=[anexo], tipo="audio")  # download_media do mundo explode se chamado
    assert mundo.textos()[-1] == wa_demo.SO_TEXTO
    assert mundo.chamadas == [] and _sessao(n)["msgs_used"] == 0


def test_reacao_em_sessao_e_ignorada_calada(mundo):
    n = _numero()
    _abre(n)
    antes = len(mundo.saida)
    _manda(n, "", tipo="reaction")
    assert len(mundo.saida) == antes and mundo.chamadas == []


def test_texto_longo_chega_cortado_em_300(mundo):
    n = _numero()
    _abre(n)
    _manda(n, "x" * 500)
    assert len(mundo.chamadas[-1][-1]["content"]) == 300


def test_item_da_lista_manda_a_pergunta_completa(mundo):
    n = _numero()
    _abre(n)
    id_, (titulo, _, completa, _) = f"{wa_demo.PREFIXO}2", wa_demo.PERGUNTAS[f"{wa_demo.PREFIXO}2"]
    raw = {"interactive": {"type": "list_reply", "list_reply": {"id": id_, "title": titulo}}}
    _manda(n, titulo, raw_extra=raw, tipo="interactive")
    assert mundo.chamadas[-1][-1]["content"] == completa != titulo


def test_linha_antiga_da_lista_vira_texto_normal_e_nao_a_pergunta_nova(mundo):
    """Quem abriu a sessão antes da lista de 7 tem linhas "demo_q:N" na conversa. O id
    antigo não casa com PERGUNTAS (prefixo novo): vale o título, como texto comum."""
    n = _numero()
    _abre(n)
    assert "demo_q:3" not in wa_demo.PERGUNTAS
    antigo = "Onde mais gasto?"  # título que demo_q:3 tinha
    raw = {"interactive": {"type": "list_reply", "list_reply": {"id": "demo_q:3", "title": antigo}}}
    _manda(n, antigo, raw_extra=raw, tipo="interactive")
    assert mundo.chamadas[-1][-1]["content"] == antigo
    assert _sessao(n)["msgs_used"] == 1


def test_modelo_que_falha_devolve_a_mensagem(mundo):
    n = _numero()
    _abre(n)
    mundo.falha = True
    _manda(n, "oi, tudo bem?")
    assert mundo.textos()[-1] == ERROR_MSG
    assert _sessao(n)["msgs_used"] == 0 and _sessao(n)["first_answer_at"] is None


def test_gatilho_sem_codigo_vira_sessao_organica(mundo):
    n = _numero()
    _abre(n)
    s = _sessao(n)
    assert s["clicked_at"] is None and s["opened_at"] is not None


@pytest.mark.parametrize("texto,abre", [
    (GATILHO, True), (GATILHO + " (teste ABC123)", True), ("quero testar o pigbank", True),
    ("Oi, quero testar o PigBank", True), ("oi piggy quero testar o pigbank", True),
    ("Olá Piggy! Quero  testar o PIGBANK 🐷", True), ("  QUERO TESTAR O PIGBANK!", True),
    ("não quero testar o PigBank", False), ("nao quero testar o pigbank", False),
    ("eu não quero testar o PigBank", False), ("nunca quero testar o PigBank", False),
    ("ele disse: quero testar o pigbank", False), ("quem quer testar o pigbank?", False),
])
def test_gatilho_so_no_inicio_e_nao_casa_negacao(mundo, texto, abre):
    plano = wa_demo.decidir(_msg(_numero(), texto))
    assert (plano is not None and plano.acao == "abrir") is abre, texto


def test_codigo_clicado_liga_ao_numero_que_abre(mundo):
    code = funil.criar_clique("ig", "campanha")
    n = _numero()
    _manda(n, f"Oi Piggy! Quero testar o PigBank 🐷 (teste {code})")
    s = _sessao(n)
    assert s["code"] == code and s["clicked_at"] is not None and s["utm_source"] == "ig"


def test_codigo_de_outro_numero_vira_sessao_organica_e_a_linha_de_a_fica_intacta(mundo):
    code = funil.criar_clique("ig", None)
    a, b = _numero(), _numero()
    _manda(a, f"{GATILHO} (teste {code})")
    linha_a = _um("select * from demo_sessions where code = %s", (code,))
    _manda(b, f"{GATILHO} (teste {code})")

    assert _um("select * from demo_sessions where code = %s", (code,)) == linha_a
    sb = _sessao(b)
    assert sb["code"] != code and sb["clicked_at"] is None
    assert mundo.demo()[-1][1] == "lista", "B também devia receber o teste"


def test_teto_diario_lota_sem_ligar_o_codigo(mundo, monkeypatch):
    abertas = _um("select count(*) as n from demo_sessions"
                  " where opened_at > now() - interval '24 hours'")["n"]
    monkeypatch.setenv("DEMO_DAILY_MAX", str(abertas + 1))
    primeiro, segundo = _numero(), _numero()
    _manda(primeiro, GATILHO)
    assert mundo.demo()[-1][1] == "lista"

    code = funil.criar_clique("ig", None)
    _manda(segundo, f"{GATILHO} (teste {code})")
    assert "lotou" in mundo.textos()[-1] and f"/t/{code}" in mundo.textos()[-1]
    assert _sessao(segundo) is None
    assert _um("select wa_hash, opened_at from demo_sessions where code = %s", (code,)) == {
        "wa_hash": None, "opened_at": None}
    _manda(segundo, "oi?")  # sem sessão e sem gatilho: nem o demo nem o modelo
    assert mundo.chamadas == []


def _lota(monkeypatch):
    _manda(_numero(), GATILHO)  # garante >= 1 aberta: teto 0 desligaria o demo em vez de lotar
    abertas = _um("select count(*) as n from demo_sessions"
                  " where opened_at > now() - interval '24 hours'")["n"]
    monkeypatch.setenv("DEMO_DAILY_MAX", str(abertas))  # cheio: nenhuma abertura nova


def test_lotou_com_codigo_de_outro_numero_manda_aos_precos_e_a_linha_de_a_fica_intacta(
        mundo, monkeypatch):
    code = funil.criar_clique("ig", None)
    a, b = _numero(), _numero()
    _manda(a, f"{GATILHO} (teste {code})")
    linha_a = _um("select * from demo_sessions where code = %s", (code,))
    _lota(monkeypatch)
    _manda(b, f"{GATILHO} (teste {code})")
    assert "lotou" in mundo.textos()[-1]
    assert "/precos?origem=teste" in mundo.textos()[-1] and "/t/" not in mundo.textos()[-1]
    assert _um("select * from demo_sessions where code = %s", (code,)) == linha_a


def test_lotou_sem_codigo_manda_aos_precos(mundo, monkeypatch):
    _lota(monkeypatch)
    _manda(_numero(), GATILHO)
    assert "lotou" in mundo.textos()[-1]
    assert "/precos?origem=teste" in mundo.textos()[-1] and "/t/" not in mundo.textos()[-1]



def test_lista_falhando_cai_num_texto_com_as_perguntas(mundo, monkeypatch):
    def quebra(*a, **k):
        raise RuntimeError("meta 400")
    monkeypatch.setattr(wa_client, "send_interactive_list", quebra)
    n = _numero()
    _manda(n, GATILHO)
    corpo = mundo.textos()[-1]
    assert corpo.startswith(wa_demo.BOAS_VINDAS.removesuffix(wa_demo._CONVITE))
    fora = [p for _, _, p, c in wa_demo.PERGUNTAS.values() if not c]
    no_corpo = [p for _, _, p, c in wa_demo.PERGUNTAS.values() if c]
    assert len(fora) == len(wa_demo.PERGUNTAS) - 3 and all(f"• {p}" in corpo for p in fora)
    assert not any(f"• {p}" in corpo for p in no_corpo)  # as do corpo não se repetem na lista de texto
    assert corpo.endswith(wa_demo._CONVITE) and len(corpo) <= 4096


# ── 8. lista, prompt e persona ───────────────────────────────────────────────

def test_lista_cabe_na_api_da_meta(mundo):
    assert len(wa_demo.BOTAO) <= 20 and len(wa_demo.SECAO) <= 24
    assert len(wa_demo.BOAS_VINDAS) <= 1024
    assert f"{wa_demo.LIMITE_MSGS} perguntas" in wa_demo.BOAS_VINDAS
    assert 1 <= len(wa_demo.PERGUNTAS) <= 10
    assert len(wa_demo.PERGUNTAS) == 7  # id duplicado sobrescreveria uma pergunta em silêncio
    assert list(wa_demo.PERGUNTAS) == [f"demo_p:{i}" for i in range(1, 8)]
    titulos = [q[0] for q in wa_demo.PERGUNTAS.values()]
    assert len(set(titulos)) == len(titulos)
    for i, (t, d, p, _) in wa_demo.PERGUNTAS.items():
        assert i.startswith(wa_demo.PREFIXO) and len(t) <= 24 and len(d) <= 72 and p, i

    _manda(_numero(), GATILHO)
    enviado = mundo.listas[-1]
    assert enviado["button_label"] == wa_demo.BOTAO
    linhas = [r for s in enviado["sections"] for r in s["rows"]]
    assert len(linhas) <= 10 and len({r["id"] for r in linhas}) == len(linhas)


def _tokens(texto):
    return {t for t in re.findall(r"[\w.]+", texto.lower()) if len(t) >= 3}


def test_corpo_da_abertura_e_as_3_perguntas_da_lista_sao_as_mesmas():
    """O corpo escreve 3 perguntas que a pessoa pode digitar; cada uma tem de ser a de uma
    entrada de PERGUNTAS (mesmo assunto e mesmos valores em R$), na ordem da lista."""
    visiveis = wa_demo.BOAS_VINDAS.split("\n\n")[1].splitlines()
    entradas = [q for q in wa_demo.PERGUNTAS.values() if q[3]]
    assert len(visiveis) == len(entradas) == 3
    assert visiveis == [q[3] for q in entradas] == [q[3] for q in list(wa_demo.PERGUNTAS.values())[:3]]
    for linha, (_, _, completa, _) in zip(visiveis, entradas):
        assert re.findall(r"R\$ [\d.]+", linha) == re.findall(r"R\$ [\d.]+", completa), linha
        assert len(_tokens(linha) & _tokens(completa)) >= 3, f"{linha!r} não é {completa!r}"


def test_constantes_do_prompt_estao_no_system_prompt_e_no_do_demo():
    for c in (sp.PERSONA_PIGGY, sp.FORMATO_WHATSAPP, sp.DICAS_GERAIS):
        assert c and c in sp.SYSTEM_PROMPT and c in conversa.PROMPT


def test_textos_do_demo_nunca_dizem_que_nao_guardamos_nada():
    for t in (wa_demo.BOAS_VINDAS, wa_demo.ULTIMA, wa_demo.FIM, wa_demo.LOTOU, wa_demo.SO_TEXTO):
        assert "não guardamos" not in t.lower()
    assert "não é salvo no PigBank, só vai para a IA que responde" in wa_demo.BOAS_VINDAS
    assert "fica salvo" not in wa_demo.BOAS_VINDAS
    assert "não é salvo no PigBank, só vai para a IA que responde" in conversa.PROMPT
    assert "link 123456" in wa_demo.FIM and "link 123456" in wa_demo.LOTOU
    assert "amanhã" not in wa_demo.LOTOU, "o teto é janela móvel de 24h, não calendário"
    # coladas à resposta do modelo (que já abre com 🐷): sem emoji duplicado
    assert not wa_demo.FIM.startswith("🐷") and not wa_demo.ULTIMA.startswith("🐷")


def test_totais_da_persona_somam():
    for mes in dados.PERSONA["meses"]:
        assert sum(Decimal(v) for v in mes["por_categoria"].values()) == Decimal(mes["total_gastos"]), mes["rotulo"]
    assinaturas = sum(Decimal(a["valor_mensal"]) for a in dados.PERSONA["assinaturas"])
    assert Decimal(dados.PERSONA["meses"][0]["por_categoria"]["assinaturas"]) == assinaturas


def test_projecao_da_persona_bate_com_a_conta():
    atual = dados.PERSONA["meses"][0]
    projetado = Decimal(atual["total_gastos"]) * 30 / 20  # até o dia 20 → mês de 30
    assert Decimal(dados.PERSONA["gasto_projetado_fim_do_mes"]) == projetado
    assert Decimal(dados.PERSONA["sobra_projetada_fim_do_mes"]) == Decimal(dados.PERSONA["renda_mensal"]) - projetado


# ── extratos que não podem mudar comportamento ───────────────────────────────

@pytest.mark.parametrize("dashboard_url,esperado", [
    ("http://localhost:8000", "https://pigbankai.com"),
    ("https://app.exemplo.com/", "https://app.exemplo.com"),
])
def test_base_url_publica_e_signup_url(monkeypatch, dashboard_url, esperado):
    monkeypatch.setenv("DASHBOARD_URL", dashboard_url)
    assert dashboard_links.base_url_publica() == esperado
    assert wr._signup_url() == f"{esperado}/cadastro"


def test_message_id_mantem_a_regra_antiga():
    import hashlib
    assert wr._message_id(_msg("1", "x", msg_id="wamid.A")) == "wamid.A"
    sem_id = InboundMessage(wa_id="1", text="", timestamp="99", attachments=[], raw={})
    assert wr._message_id(sem_id) == "99"
    sem_nada = InboundMessage(wa_id="1", text="", timestamp=None, attachments=[], raw={})
    assert wr._message_id(sem_nada) == hashlib.sha256(repr({}).encode("utf-8")).hexdigest()


def test_persona_pre_calculada_bate_com_as_contas():
    """O modelo erra a conta; por isso a persona traz os números prontos. Aqui eles são refeitos."""
    p = dados.PERSONA
    atual, passado = p["meses"][0], p["meses"][1]
    c = p["contas_a_vencer_este_mes"]
    valores = [Decimal(i["valor"]) for i in c["itens"]]
    assert valores[0] == Decimal(p["cartao"]["fatura_aberta"])
    assert valores[1:] == [Decimal(x["valor"]) for x in p["contas_a_pagar"]]
    # Uma fonte: o vencimento do item é o do cartão / da conta, e todos vencem DEPOIS de `hoje`.
    assert c["itens"][0]["vencimento"] == p["cartao"]["vencimento"]
    assert [i["vencimento"] for i in c["itens"][1:]] == [x["vencimento"] for x in p["contas_a_pagar"]]
    hoje = int(p["hoje"].split()[1])
    assert all(int(i["vencimento"].split()[1]) > hoje for i in c["itens"]), "vencimento no passado"
    assert sum(valores) == Decimal(c["total"])
    assert Decimal(p["saldo_conta_corrente"]) - Decimal(c["total"]) == Decimal(c["saldo_apos_pagar_tudo"])
    assert Decimal(p["assinaturas_por_ano"]) == Decimal(atual["por_categoria"]["assinaturas"]) * 12
    d = p["delivery_este_mes"]
    assert Decimal(d["valor"]) == Decimal(atual["por_categoria"]["delivery"])
    assert (Decimal(d["valor"]) / d["pedidos"]).quantize(Decimal("0.01")) == Decimal(d["ticket_medio"])
    assert (Decimal(d["valor"]) / Decimal(atual["total_gastos"]) * 100).quantize(Decimal("0.1")) == Decimal(d["percentual_dos_gastos"])
    m = p["mes_passado_ate_o_dia_20"]
    assert sum(Decimal(v) for v in m["por_categoria"].values()) == Decimal(m["total_gastos"])
    v = m["variacao_mesmo_periodo"]
    assert Decimal(atual["total_gastos"]) - Decimal(m["total_gastos"]) == Decimal(v["valor"])
    assert (Decimal(v["valor"]) / Decimal(m["total_gastos"]) * 100).quantize(Decimal("0.1")) == Decimal(v["percentual"])
    altas = {k: Decimal(atual["por_categoria"][k]) - Decimal(x) for k, x in m["por_categoria"].items()}
    assert {k: Decimal(x) for k, x in v["maiores_altas"].items()} == {k: altas[k] for k in v["maiores_altas"]}
    assert sorted(altas.values())[-2:] == sorted(Decimal(x) for x in v["maiores_altas"].values())
    assert Decimal(atual["total_gastos"]) * 30 / 20 > Decimal(passado["total_gastos"]), "o mês está mais caro"
    r = p["caixinha"]
    faltam = Decimal(r["meta"]) - Decimal(r["guardado"])
    assert faltam == Decimal(r["faltam"]) and math.ceil(faltam / 500) == r["meses_guardando_500"]


def test_prompt_manda_usar_os_campos_pre_calculados():
    for campo in ("contas_a_vencer_este_mes", "saldo_apos_pagar_tudo", "mes_passado_ate_o_dia_20",
                  "variacao_mesmo_periodo", "assinaturas_por_ano", "delivery_este_mes", "meses_guardando_500",
                  "cobrancas_recorrentes", "total_mensal", "total_anual"):
        assert campo in json.dumps(dados.PERSONA), campo
        assert campo in conversa.REGRAS_DEMO, campo
    assert "pergunta-gancho" in conversa.REGRAS_DEMO
    assert dados.PERSONA["veredito_do_mes"] in conversa.REGRAS_DEMO  # o prompt cita o veredito da persona
    assert conversa.REGRAS_DEMO.count("Termine com UMA pergunta-gancho") == 1
    assert "No máximo 6 linhas" not in conversa.REGRAS_DEMO  # DICAS_GERAIS já fixa 8; uma regra só


def test_veredito_do_mes_segue_o_sinal_da_sobra_projetada():
    p = dados.PERSONA
    assert p["veredito_do_mes"].startswith("fecha no azul") == (Decimal(p["sobra_projetada_fim_do_mes"]) > 0)


def test_prompt_acompanha_a_persona(monkeypatch):
    """Valores das regras saem da PERSONA (§0.7): mudar a persona muda o que o prompt manda citar."""
    antes = conversa.REGRAS_DEMO
    p = dados.PERSONA
    assert f"{fmt_brl(float(p['cartao']['fatura_aberta']))}, vence {p['cartao']['vencimento']}" in antes
    for v in (fmt_brl(float(p["contas_a_vencer_este_mes"]["saldo_apos_pagar_tudo"])),
              fmt_brl(float(p["sobra_projetada_fim_do_mes"])), p["hoje"]):
        assert v in antes
    monkeypatch.setitem(p["cartao"], "fatura_aberta", "1300.00")
    monkeypatch.setitem(p["contas_a_vencer_este_mes"], "proxima_receita", "dia 7 do mês que vem (x)")
    try:
        importlib.reload(conversa)
        assert "R$ 1.300,00, vence" in conversa.REGRAS_DEMO and "R$ 1.260,00" not in conversa.REGRAS_DEMO
        assert "(dia 7 do mês que vem)" in conversa.REGRAS_DEMO and "dia 5 do mês" not in conversa.REGRAS_DEMO
    finally:
        monkeypatch.undo()
        importlib.reload(conversa)
    assert conversa.REGRAS_DEMO == antes


def test_brl_formata_pt_br():
    """O prompt usa o `fmt_brl` do repo (§0.1): os valores que ele escreve saem em pt-BR."""
    assert fmt_brl(float("1260.00")) == "R$ 1.260,00" and fmt_brl(float("300.60")) == "R$ 300,60"
    assert "R$ 1.260,00, vence" in conversa.REGRAS_DEMO and "R$ 300,60" in conversa.REGRAS_DEMO


def test_cobrancas_recorrentes_somam_assinaturas_mais_contas_fixas():
    p = dados.PERSONA
    c = p["cobrancas_recorrentes"]
    mensal = sum(Decimal(i["valor_mensal"]) for i in c["itens"])
    assert mensal == Decimal(c["total_mensal"]) == Decimal("471.50")
    assert Decimal(c["total_mensal"]) * 12 == Decimal(c["total_anual"]) == Decimal("5658.00")
    # as 4 assinaturas (191,60) + as contas_a_pagar (Internet, Energia): mesma fonte, mesmos valores
    esperado = [Decimal(a["valor_mensal"]) for a in p["assinaturas"]] + [Decimal(a["valor"]) for a in p["contas_a_pagar"]]
    assert sorted(Decimal(i["valor_mensal"]) for i in c["itens"]) == sorted(esperado)
    assert "Energia (valor médio)" in [i["nome"] for i in c["itens"]]


def test_textos_fixos_dos_dados_da_ana_nao_falam_com_voce_nem_citam_ifood():
    """Gancho, descrição da lista e corpo falam dos dados da Ana: nunca "você/seu/sua". E
    o PigBank é só Open Finance: nada de oferecer registrar gasto ("gastei 80 no iFood")."""
    ganchos = re.findall(r"→ (Quer [^\n]*\?)", conversa.REGRAS_DEMO)
    assert len(ganchos) == 7 and len(set(ganchos)) == 7  # ciclo de 7, sem repetir
    textos = ganchos + [d for _, d, *_ in wa_demo.PERGUNTAS.values()] + [c for *_, c in wa_demo.PERGUNTAS.values() if c]
    for t in textos:
        assert not re.search(r"\b(você|voce|seus?|suas?)\b", t, re.I), t
    tudo = " ".join(textos + [q for q, *_ in wa_demo.PERGUNTAS.values()] + [q[2] for q in wa_demo.PERGUNTAS.values()])
    assert "ifood" not in tudo.lower() and "Veja como o Piggy anotaria" not in tudo
    assert "ifood" not in " ".join(ganchos).lower() and "anota um gasto" not in conversa.REGRAS_DEMO


def test_ultima_resposta_vai_ao_modelo_depois_do_historico(monkeypatch):
    vistas = []
    monkeypatch.setattr(conversa, "_chamar_modelo", lambda m: vistas.append(m) or "ok")
    conversa.responder("h-ultima", "oi", ultima=True)
    conversa.responder("h-ultima", "oi")
    com, sem = vistas
    assert com[-1] == {"role": "system", "content": conversa.ULTIMA_RESPOSTA} and com[-2]["role"] == "user"
    assert sem == com[:-1], "sem `ultima` o pedido é o mesmo, só sem a linha"
