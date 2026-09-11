"""
tests/test_paywall_gate_copy.py — a COPY da mensagem de bloqueio do bot.

Arquivo próprio por assunto (e porque `test_paywall_gate_bot.py` bateu no teto
de 350 linhas): lá mora o VEREDITO — quem é barrado e o que barrar escreve no
banco —, aqui mora **o que a pessoa lê**. São duas formas, e o que as separa é
existir ou não linha em `auth_accounts`:

  • **só-WhatsApp** (sem linha) — nunca viu o dashboard e o dono decidiu cortá-la
    SEM aviso prévio, então esta mensagem é a ÚNICA comunicação que ela recebe.
    Nada de "acesse seu painel", e NADA sobre o período grátis;
  • **ex-assinante** (com linha) — recebe a verdade dos três estados do trial,
    de `core.services.trial_offer.texto_da_oferta`.

**Por que este arquivo existe**: a copy do ex-assinante era a única do PR sem
NENHUMA asserção de conteúdo, e é a que os 58 avisados vão ler amanhã.

Os vermelhos destes casos sob a injeção da perna do DIREITO estão declarados no
cabeçalho de `tests/test_paywall_gate_bot.py` — a mensagem some quando ninguém
é barrado.
"""
from __future__ import annotations

import pathlib
import re

import db
from _paywall_gate_helpers import (  # noqa: F401  (v2_ligado é fixture autouse)
    barrado as _barrado,
    cadastro_novo as _cadastro_novo,
    diga as _diga,
    so_whatsapp as _so_whatsapp,
    v2_ligado,
)


def _copy_da_precos_promete_trial() -> str:
    """A frase do trial que a /precos mostra ao VISITANTE, lida do HTML.

    Serve à direção OPOSTA da de antes. Este arquivo comparava a copy do bot com
    ela para exigir PARIDADE, e a paridade era o defeito: a mensagem do gate
    prometia "15 dias grátis (um teste por número)" a quem o corte manda para
    lá, e o trial é um por telefone NA VIDA — o ex-assinante que já o queimou
    lia uma promessa que o checkout não cumpre. Hoje ela existe para provar que
    o bot NÃO repete essa frase."""
    html = (pathlib.Path(__file__).resolve().parent.parent
            / "frontend" / "precos.html").read_text(encoding="utf-8")
    m = re.search(r"15 dias grátis pra testar</strong>\s*\(([^)]+)\)", html)
    assert m, "não achei a frase do trial na precos.html"
    return m.group(1)


def test_so_whatsapp_e_barrado_e_recebe_a_copy_sem_painel():
    """A população sem cadastro web é cortada (decisão do dono), e a mensagem do
    gate é a ÚNICA comunicação que ela recebe.

    As três asserções de conteúdo são requisito, não estilo: quem nunca viu o
    dashboard não pode ser mandado para "seu painel", e nada pode ser afirmado
    sobre o período grátis. Para TODA esta população não há `phone_hash` (não há
    linha), então `motivo_trial_indisponivel` devolve `"sem_telefone"` — o "não
    sei". Medido 2026-09-11: `texto_da_oferta` aí responde "O checkout confirma
    seu período grátis ou o valor da primeira cobrança antes da confirmação:".
    A justificativa anterior ("diria 'esse telefone já usou o período grátis'")
    valia enquanto ela lia o booleano e MORREU no mesmo PR que a fez ler o
    motivo. O que continua proibido é reusar a frase do `assinar` do mesmo
    jeito: ela abre com "Aqui ó, link pra assinar" e afirma que existe período
    grátis a confirmar — oferta implícita de trial, numa mensagem de BLOQUEIO
    que ninguém pediu."""
    uid = _so_whatsapp()

    resposta = _diga(uid, "gastei 50 no mercado")

    assert _barrado(resposta), f"a população só-WhatsApp não foi cortada: {resposta!r}"
    assert db.list_launches(uid) == [], "o gate respondeu mas registrou o gasto"
    baixa = resposta.lower()
    assert "painel" not in baixa, resposta
    assert "dashboard" not in baixa, resposta
    assert "grátis" not in baixa and "gratis" not in baixa, (
        "a copy da população sem cadastro afirmou algo sobre o período grátis: "
        f"{resposta!r}")


def test_copy_do_ex_assinante_nao_culpa_um_telefone_que_nao_existe():
    """A copy que os 58 avisados vão ler, e ela estava SEM asserção de conteúdo.

    `db.plans.is_trial_eligible_for_user` devolve False também quando a conta
    não tem `phone_hash` — cadastro web que nunca vinculou WhatsApp, que é
    exatamente o que `_cadastro_novo()` cria. A frase "esse telefone já usou o
    período grátis" saía para quem nunca teve telefone. Enquanto isso vivia só
    no `assinar plano` era opt-in; o corte a promoveu a mensagem de BLOQUEIO
    não-solicitada.

    O conserto é na RAIZ (`db.plans.motivo_trial_indisponivel` separa
    "sem_telefone" de "telefone_ja_usou"), então este teste mede a mensagem do
    gate e o `assinar` ganha a verdade junto.

    CONTROLE DECLARADO: em `core/services/trial_offer.texto_da_oferta`, troque
    `if motivo == "telefone_ja_usou":` por `if motivo is not None:` (o termo
    continua lá e deixa de discriminar). VERMELHOS (medido — são dois, o
    segundo fora deste arquivo):
      `test_copy_do_ex_assinante_nao_culpa_um_telefone_que_nao_existe`
      `tests/test_billing_commands.py::test_assinar_v2_com_consulta_indisponivel_delega_confirmacao_ao_checkout`
    Direção: o bot acusa de reincidência quem nunca usou nada — e o mesmo termo
    engole a frase do "não sei", que é a que sobra quando a consulta falha.
    """
    uid = _cadastro_novo()   # conta web SEM telefone vinculado
    from db.plans import motivo_trial_indisponivel
    assert motivo_trial_indisponivel(uid) == "sem_telefone", "pré-condição do caso"
    db.mark_plan_selected(uid)

    resposta = _diga(uid, "gastei 50 no mercado")

    assert _barrado(resposta), "pré-condição: a mensagem medida é a do gate"
    baixa = resposta.lower()
    assert "telefone já usou" not in baixa and "telefone ja usou" not in baixa, resposta
    # E continua deferindo ao checkout, que é quem sabe a verdade (§0.7).
    assert "checkout" in baixa, resposta


def test_copy_do_ex_assinante_com_telefone_queimado_nomeia_o_telefone():
    """POSITIVO do par acima: quando o telefone REALMENTE já usou o trial, a
    frase específica volta. Sem ele, apagar os três estados e devolver sempre a
    frase neutra passaria verde — e o grupo pararia de medir a distinção."""
    from db.connection import get_conn
    from db_support import invalidate_auth_user_cache

    uid = _cadastro_novo()
    db.mark_plan_selected(uid)
    ph = f"ph_queimado_{uid}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update auth_accounts set phone_hash=%s where user_id=%s", (ph, uid))
            cur.execute("insert into plan_trials (phone_hash) values (%s)"
                        " on conflict do nothing", (ph,))
        conn.commit()
    invalidate_auth_user_cache(uid)
    from db.plans import motivo_trial_indisponivel
    assert motivo_trial_indisponivel(uid) == "telefone_ja_usou", "pré-condição do caso"
    try:
        resposta = _diga(uid, "gastei 50 no mercado")
        assert _barrado(resposta), resposta
        baixa = resposta.lower()
        assert "telefone já usou" in baixa, resposta
        assert "15 dias grátis" not in baixa, "prometeu trial a quem já queimou"
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from plan_trials where phone_hash=%s", (ph,))
            conn.commit()


def test_copy_do_gate_nao_promete_isencao_de_cobranca():
    """O trial é 1 por telefone NA VIDA (db/plans.py): quem recria a conta com o
    mesmo número é cobrado na hora (trial_days=0). A copy não pode afirmar 'sem
    cobrança agora' — a moldura é a do send_welcome_email: o checkout confirma
    antes de cobrar."""
    resposta = _diga(_so_whatsapp(), "gastei 50 no mercado")

    assert _barrado(resposta), "pré-condição: a mensagem medida é a do gate"
    assert "sem cobrança agora" not in resposta.lower()
    assert "checkout" in resposta.lower(), "a copy não diz quem confirma a cobrança"
    # A comparação com a /precos mudou de DIREÇÃO: antes exigia paridade, hoje
    # exige o contrário. A frase do visitante ("um teste por número") é falsa
    # para quem o gate manda para lá, e o bot não pode repeti-la. Continua lida
    # do HTML de verdade (§0.7) para não virar uma terceira cópia do literal.
    frase_de_visitante = _copy_da_precos_promete_trial()
    assert frase_de_visitante.lower() not in resposta.lower(), (
        f"o bot repetiu a promessa de visitante da /precos ({frase_de_visitante!r}) "
        "para quem está sendo cortado")
