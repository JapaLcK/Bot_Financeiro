"""Bot: "PARAR" digitado desliga as mensagens de atualização; lead sem compra tem texto próprio
(PR 2 do remarketing, docs/plano-remarketing.md).

Pela conversa real (`process_message`), estado no Postgres; só o envio ao WhatsApp é falso.

Controles medidos no PR (ver o relato): sem o casamento do texto (`_eh_parar` -> False) ficam
vermelhos os testes de PARAR (a flag não liga e a resposta é a de antes); forçar `ja_comprou`
a False deixa vermelho o "comprou e não criou senha"; sem o `try` em `_lead_sem_compra` a
exceção cai no `except` geral e a resposta vira "Não consegui processar".
"""
import pytest

import db
from adapters.whatsapp import wa_runtime as wr
from db.remarketing import ja_comprou
from test_senha_obrigatoria import _com_senha, conta_paga_sem_credencial, _conta_quiz, _email, _navegador
from test_senha_obrigatoria_whatsapp import (  # noqa: F401 (enviadas e env são fixtures)
    _clique, _dono_do_numero, _fone, _gastos, _manda, _q, enviadas, env,
)

ATUALIZACOES = "parei as atualizações"  # o texto do botão "parar atualizações", sem mudança
SINONIMOS = ["parar", "stop", "sair", "parar mensagens", "parar de receber", "nao quero mais receber",
             "STOP", "Sair!", "Parar mensagens.", "Não quero mais receber!", "Parar de receber"]


def lead_sem_compra() -> int:
    """Conta nascida na /assinar e nunca paga: sem senha, sem plano, sem grant."""
    r = _conta_quiz(_navegador(), _email())
    assert r.status_code == 200, r.text
    uid = int(r.json()["user_id"])
    assert db.conta_sem_credencial(uid) and not ja_comprou(uid)
    return uid


def _opt_out(uid: int) -> bool:
    return db.get_whatsapp_updates_opt_out(uid)


# ── 1. lead não ligado (`precisa_senha`) ─────────────────────────────────────

def test_lead_digita_parar_desliga_responde_o_texto_do_lead_e_nao_religa(enviadas):
    uid = lead_sem_compra()
    wa_id = _fone(uid)
    assert not _opt_out(uid)

    _manda(wa_id, "PARAR")
    assert _opt_out(uid), enviadas
    assert enviadas == [wr.PARAR_LEAD_WA], enviadas
    assert _dono_do_numero(wa_id) != uid and _gastos(uid) == 0

    # "Se mudar de ideia, é só me chamar": a mensagem seguinte recebe a resposta normal
    # do bot e NÃO religa o opt-out (só a tela de Configurações grava False).
    enviadas.clear()
    _manda(wa_id, "oi")
    assert enviadas == [wr.CONVITE_LEAD_WA], enviadas
    assert _opt_out(uid)


@pytest.mark.parametrize("texto", ["Parar.", " PARAR!", "parar", "Párar", "parar...", *SINONIMOS])
def test_variacoes_de_parar_desligam(enviadas, texto):
    uid = lead_sem_compra()
    _manda(_fone(uid), texto)
    assert _opt_out(uid) and enviadas == [wr.PARAR_LEAD_WA], (texto, enviadas)


@pytest.mark.parametrize("texto", ["parar de gastar com ifood", "pare", "para", "parar report diario",
                                   "quero parar", "não parar", "sair do tutorial", "stop loss"])
def test_outras_frases_nao_desligam(enviadas, texto):
    uid = lead_sem_compra()
    _manda(_fone(uid), texto)
    assert not _opt_out(uid) and enviadas == [wr.CONVITE_LEAD_WA], (texto, enviadas)


# ── 2. o convite do lead × o de quem comprou ─────────────────────────────────

def test_lead_sem_compra_recebe_o_convite_e_quem_comprou_o_de_sempre(enviadas):
    lead = lead_sem_compra()
    _manda(_fone(lead), "quero assinar")
    assert enviadas == [wr.CONVITE_LEAD_WA], enviadas
    assert "pigbankai.com/precos" in enviadas[0] and "responda PARAR" in enviadas[0]

    comprou, _, _ = conta_paga_sem_credencial()  # plano pago, ainda sem senha
    assert ja_comprou(comprou)
    enviadas.clear()
    _manda(_fone(comprou), "quero assinar")
    assert enviadas == [wr.PRECISA_SENHA_WA], enviadas


def test_numero_ja_ligado_a_conta_sem_senha_tambem(enviadas):
    """Ramo `conta_sem_credencial` (número ligado antes do PR 4): mesmas respostas."""
    lead = lead_sem_compra()
    db.bind_identity("whatsapp", _fone(lead), lead)
    _manda(_fone(lead), "quero assinar")
    assert enviadas == [wr.CONVITE_LEAD_WA], enviadas
    enviadas.clear()
    _manda(_fone(lead), "PARAR")
    assert _opt_out(lead) and enviadas == [wr.PARAR_LEAD_WA], enviadas

    comprou, _, _ = conta_paga_sem_credencial()
    db.bind_identity("whatsapp", _fone(comprou), comprou)
    enviadas.clear()
    _manda(_fone(comprou), "quero assinar")
    assert enviadas == [wr.PRECISA_SENHA_WA], enviadas
    enviadas.clear()
    _manda(_fone(comprou), "PARAR")  # comprou: o texto do lead diria que não recebe mais nada
    assert _opt_out(comprou) and len(enviadas) == 1 and ATUALIZACOES in enviadas[0], enviadas


def test_ja_comprou_que_levanta_vale_como_comprou(enviadas, monkeypatch):
    """Política: erro = comprou (o comportamento de antes) e a mensagem nunca cai."""
    def quebra(_uid):
        raise RuntimeError("banco fora")
    monkeypatch.setattr("db.remarketing.ja_comprou", quebra)  # import tardio: patch na origem
    lead = lead_sem_compra()
    _manda(_fone(lead), "quero assinar")
    assert enviadas == [wr.PRECISA_SENHA_WA], enviadas
    enviadas.clear()
    _manda(_fone(lead), "PARAR")
    assert _opt_out(lead) and len(enviadas) == 1 and ATUALIZACOES in enviadas[0], enviadas


# ── 3. número ligado a conta COM senha ───────────────────────────────────────

def _com_senha_ligada(com_plano: bool) -> tuple[int, str]:
    if com_plano:
        uid, _, _ = conta_paga_sem_credencial()
    else:
        uid = lead_sem_compra()
    _com_senha(uid)
    db.bind_identity("whatsapp", _fone(uid), uid)
    return uid, _fone(uid)


@pytest.mark.parametrize("com_plano", [True, False])
def test_usuario_com_senha_digita_parar_recebe_a_resposta_de_atualizacoes(enviadas, com_plano):
    # sem plano (nunca comprou) também: o texto do lead só existe nos ramos sem senha
    uid, wa_id = _com_senha_ligada(com_plano)
    _manda(wa_id, "PARAR")
    assert _opt_out(uid) and len(enviadas) == 1 and ATUALIZACOES in enviadas[0], enviadas
    assert enviadas[0] != wr.PARAR_LEAD_WA

    if com_plano:  # a mensagem seguinte funciona e o opt-out continua
        enviadas.clear()
        _manda(wa_id, "gastei 50 mercado")
        assert _gastos(uid) == 1 and _opt_out(uid), enviadas


def test_parar_report_diario_continua_indo_para_o_report(enviadas):
    uid, wa_id = _com_senha_ligada(True)
    _manda(wa_id, "parar report diario")
    assert not _opt_out(uid), "só 'parar' sozinho desliga as atualizações"
    assert not db.get_daily_report_prefs(uid)["enabled"], enviadas


@pytest.mark.parametrize("texto", ["parar de gastar com ifood", "pare", "para", "sair do tutorial", "stop loss"])
def test_frases_parecidas_seguem_o_fluxo_normal(enviadas, texto):
    uid, wa_id = _com_senha_ligada(True)
    _manda(wa_id, texto)
    assert not _opt_out(uid), (texto, enviadas)


def test_com_pendencia_aberta_parar_vence_e_encerra_a_pendencia(enviadas):
    """Regra: PARAR encerra as pendências de confirmação (as do bot e as da IA)."""
    uid, wa_id = _com_senha_ligada(True)
    db.set_pending_action(uid, "bill_pay_amount", {"bill_id": None, "name": "luz"}, minutes=30)
    _manda(wa_id, "PARAR")
    assert _opt_out(uid) and len(enviadas) == 1 and ATUALIZACOES in enviadas[0], enviadas
    assert db.get_pending_action(uid) is None


@pytest.mark.parametrize("parar", [False, True])
def test_parar_descarta_a_confirmacao_da_ia_e_o_sim_nao_apaga(enviadas, parar):
    """Sem PARAR o 'sim' apaga (prova que a pendência executa); com PARAR o lançamento fica."""
    uid, wa_id = _com_senha_ligada(True)
    lid, seq, _ = db.add_launch_and_update_balance(uid, "despesa", 50, None, "mercado")
    assert _gastos(uid) == 1
    from core.services.ai_chat.tools.extrato_ia import ao_confirmar  # a pendência como o runner a arma (com `_item`)
    db.ai_set_pending_action(uid, "delete_launch", ao_confirmar(uid, {"launch_id": f"l{lid}"}), f"apagar o gasto #{seq}")
    if parar:
        _manda(wa_id, "PARAR")
        assert _opt_out(uid) and db.ai_get_pending_action(uid) is None
    _manda(wa_id, "sim")
    assert _gastos(uid) == (1 if parar else 0), enviadas


def test_parar_encerra_a_pergunta_aberta_da_ia(enviadas):
    """O 'sim' seguinte não pode ser lido como resposta à oferta que a IA fez antes do PARAR."""
    from core.services.ai_chat_commands import pergunta_aberta_da_ia
    uid, wa_id = _com_senha_ligada(True)
    db.ai_append_message(uid, "user", "quanto gastei?")
    db.ai_append_message(uid, "assistant", "Quer que eu mostre suas maiores despesas?")
    assert pergunta_aberta_da_ia(uid) is not None
    _manda(wa_id, "PARAR")
    assert _opt_out(uid) and pergunta_aberta_da_ia(uid) is None, enviadas


# ── 3b. falha ao gravar o opt-out ────────────────────────────────────────────

def test_falha_ao_gravar_o_opt_out_avisa_e_nao_fica_mudo(enviadas, monkeypatch):
    def quebra(_uid, _v):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(wr, "set_whatsapp_updates_opt_out", quebra)
    uid, wa_id = _com_senha_ligada(True)
    _manda(wa_id, "PARAR")  # digitado
    assert enviadas == [wr.OPT_OUT_FALHOU_WA] and not _opt_out(uid), enviadas
    enviadas.clear()
    _clique(wa_id, "whatsapp_updates_disable")  # botão: mesmo ponto comum
    assert enviadas == [wr.OPT_OUT_FALHOU_WA] and not _opt_out(uid), enviadas


def test_falha_em_uma_das_contas_nao_confirma_e_a_outra_desliga(enviadas, monkeypatch):
    a, b, wa_id = _a_e_b_com_conta()  # remetente com dados: desliga A e B
    real = db.set_whatsapp_updates_opt_out

    def so_a(uid, v):
        if uid == b:
            raise RuntimeError("banco fora")
        real(uid, v)
    monkeypatch.setattr(wr, "set_whatsapp_updates_opt_out", so_a)
    _manda(wa_id, "PARAR")
    assert enviadas == [wr.OPT_OUT_FALHOU_WA], enviadas
    assert _opt_out(a) and not _opt_out(b)


@pytest.mark.parametrize("quebra", ["clear_pending_action", "ai_clear_pending_action"])
def test_limpeza_que_levanta_marca_falha_e_avisa(enviadas, monkeypatch, quebra):
    def boom(_uid):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db, quebra, boom)  # import tardio: patch na origem
    uid, wa_id = _com_senha_ligada(True)
    _manda(wa_id, "PARAR")
    assert enviadas == [wr.OPT_OUT_FALHOU_WA], enviadas  # o opt-out já gravou: um 2º PARAR conserta
    assert _opt_out(uid)


def _a_e_b_com_conta() -> tuple[int, int, str]:
    """A: conta com senha e plano, dona do número; B: sem senha, digitou o número de A.
    (Só-WhatsApp não tem linha em auth_accounts e `get_whatsapp_updates_opt_out` o dá como
    desligado sempre: o opt-out de A só é medível com conta.)"""
    b, _, _ = conta_paga_sem_credencial()
    a, _, _ = conta_paga_sem_credencial()
    _com_senha(a)
    db.bind_identity("whatsapp", _fone(b), a)
    db.add_launch_and_update_balance(a, "despesa", 50, None, "mercado")
    return a, b, _fone(b)


def test_falha_na_primeira_conta_nao_interrompe_a_segunda(enviadas, monkeypatch):
    a, b, wa_id = _a_e_b_com_conta()
    real = db.set_whatsapp_updates_opt_out

    def so_b(uid, v):  # a conversa é de A (a 1ª do loop) e é ela que falha
        if uid == a:
            raise RuntimeError("banco fora")
        real(uid, v)
    monkeypatch.setattr(wr, "set_whatsapp_updates_opt_out", so_b)
    _manda(wa_id, "PARAR")
    assert enviadas == [wr.OPT_OUT_FALHOU_WA], enviadas
    assert _opt_out(b) and not _opt_out(a)


def _arma_pendencias(uid: int) -> None:
    db.set_pending_action(uid, "bill_pay_amount", {"bill_id": None, "name": "luz"}, minutes=30)
    db.ai_set_pending_action(uid, "delete_launch", {"launch_id": "1"}, "apagar o gasto #1")


def _tem_pendencias(uid: int) -> tuple[bool, bool]:
    return db.get_pending_action(uid) is not None, db.ai_get_pending_action(uid) is not None


def test_parar_do_remetente_nao_apaga_as_pendencias_da_outra_conta(enviadas):
    """Remetente com dados: o opt-out vale para A e B, mas só as pendências de A (quem conversa) somem."""
    a, b, wa_id = _a_e_b_com_conta()
    assert not _opt_out(a) and not _opt_out(b)
    _arma_pendencias(a)
    _arma_pendencias(b)

    _manda(wa_id, "PARAR")
    assert _opt_out(a) and _opt_out(b), enviadas
    assert _tem_pendencias(a) == (False, False)
    assert _tem_pendencias(b) == (True, True), "a pendência de B foi apagada pelo PARAR de A"


def test_parar_nos_ramos_sem_senha_encerra_so_as_do_remetente(enviadas):
    """`precisa_senha`: o remetente é o usuário do WhatsApp, a conta sem senha é o alvo."""
    lead = lead_sem_compra()
    wa_id = _fone(lead)
    remetente = db.get_or_create_canonical_user("whatsapp", wa_id)
    assert remetente != lead
    _arma_pendencias(remetente)
    _arma_pendencias(lead)
    _manda(wa_id, "PARAR")
    assert _opt_out(lead) and enviadas == [wr.PARAR_LEAD_WA], enviadas
    assert _tem_pendencias(remetente) == (False, False)
    assert _tem_pendencias(lead) == (True, True)

    # `conta_sem_credencial` (número já ligado): o remetente é a própria conta
    outro = lead_sem_compra()
    db.bind_identity("whatsapp", _fone(outro), outro)
    _arma_pendencias(outro)
    _manda(_fone(outro), "PARAR")
    assert _tem_pendencias(outro) == (False, False)


def test_textos_aprovados_pelo_dono():
    """Literais do dono, não as constantes: um teste que as compara consigo mesmas não prende o texto."""
    assert wr.CONVITE_LEAD_WA == (
        "Oi! Aqui é o Piggy 🐷 Vi que você começou a assinatura do PigBank mas não terminou. "
        "Pra eu começar a cuidar do seu dinheiro por aqui, é só concluir em pigbankai.com/precos\n\n"
        "Se não quiser receber mensagens, responda PARAR.")
    assert wr.PARAR_LEAD_WA == "Pronto, não vou mais te mandar mensagens por aqui. Se mudar de ideia, é só me chamar aqui."
    assert wr.OPT_OUT_FALHOU_WA == "Não consegui registrar agora. Tenta mandar PARAR de novo em instantes."


# ── 4. remetente com dados ───────────────────────────────────────────────────

@pytest.mark.parametrize("com_plano", [False, True])
def test_remetente_com_dados_parar_desliga_os_dois_e_o_gasto_segue(enviadas, com_plano):
    b, _, _ = conta_paga_sem_credencial()  # digitou o número de A; sem senha
    wa_id = _fone(b)
    if com_plano:
        a, _, _ = conta_paga_sem_credencial()
        _com_senha(a)
        db.bind_identity("whatsapp", wa_id, a)
    else:
        a = db.get_or_create_canonical_user("whatsapp", wa_id)
    db.add_launch_and_update_balance(a, "despesa", 50, None, "mercado")

    _manda(wa_id, "PARAR")
    # só-WhatsApp (sem auth_accounts) é "desligado" por definição do getter: só A com conta mede
    assert _opt_out(b) and (_opt_out(a) or not com_plano), enviadas
    assert len(enviadas) == 1 and ATUALIZACOES in enviadas[0], enviadas

    enviadas.clear()
    _manda(wa_id, "gastei 50 no mercado")
    assert _dono_do_numero(wa_id) == a and _gastos(b) == 0
    if com_plano:
        assert _gastos(a) == 2, enviadas
    assert wr.PRECISA_SENHA_WA not in enviadas and wr.CONVITE_LEAD_WA not in enviadas
    assert _opt_out(b) and (_opt_out(a) or not com_plano), "mandar mensagem não religa o opt-out"
