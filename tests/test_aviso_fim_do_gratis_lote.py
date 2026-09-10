"""
tests/test_aviso_fim_do_gratis_lote.py — o LAÇO de `scripts/aviso_fim_do_gratis.py`:
dry-run, dedupe entre as duas rodadas, isolamento de linha ruim, a guarda do
`--corte` e a copy dos dois coortes.

Assunto separado do irmão `test_aviso_fim_do_gratis.py` (que mede a SQL contra
o predicado Python): ali a pergunta é "a população está certa?", aqui é "o
disparo se comporta?". A divisão é o §0.5 — o teto de 350 linhas chegou junto
com a segunda responsabilidade, e o espaço de estados compartilhado mora em
`_aviso_fim_do_gratis_helpers.py`.

CONTROLES MEDIDOS (`docs/controles_declarados.md`: injeção que ALARGA um valor,
nunca que apaga texto; só os VERMELHOS nomeados; sem contagem). Os controles
A–E, que mexem no `where` da população, estão no arquivo irmão e reprovam
testes daqui — a lista deles está lá.

  • F — desfaça o conserto do lote: no laço de `main`, mova a chamada
    `listar_contas_do_aviso(user_id=uid)` (e o `if not atual`) para ANTES do
    `try`, como estava:
      VERMELHO: test_revalidacao_que_estoura_nao_aborta_o_lote, sozinho.
    **É o único controle daqui que cita POSIÇÃO de bloco**, a categoria que
    `docs/controles_declarados.md` mostrou morrer na primeira refatoração. É
    inerente: o conserto É a posição da chamada, não um valor. Se o laço for
    reescrito, reescreva a instrução — o que ela afirma é "a revalidação está
    coberta pelo `try` da linha".
  • I — inverta a escolha do coorte: em `main`, troque
    `cobranca_pendente=ciclo_de_atraso_aberto(uid)` por
    `cobranca_pendente=not ciclo_de_atraso_aberto(uid)`:
      VERMELHO: test_o_coorte_de_cada_conta_decide_a_copy, sozinho.
    Antes desse teste, esta MESMA injeção passava com tudo verde — o coletor
    descartava o flag e o teste de copy chamava a função de e-mail direto, com
    os dois valores na mão. A copy forkava e ninguém media QUEM escolhe o
    fork.
  • G — alargue a borda de BAIXO da faixa do `--corte`: em `main`, troque
    `hoje <= corte_dia` por `hoje - timedelta(days=3650) <= corte_dia`:
      VERMELHO: test_corte_no_passado_e_recusado.
  • H — alargue o TETO: em `main`, troque
    `corte_dia <= hoje + timedelta(days=MAX_DIAS_ATE_O_CORTE)` por
    `corte_dia <= date.max`:
      VERMELHO: test_corte_absurdamente_longe_e_recusado.
    **NÃO injete pela constante**, e as duas grafias foram medidas:
    `MAX_DIAS_ATE_O_CORTE = 100000` deixa TODOS verdes (o caso de borda do
    teste é derivado da própria constante, então alargá-la move o caso junto,
    e o caso absoluto `9999-12-31` continua fora — ele está a 2,9 milhões de
    dias); `= 100_000_000` reprova CINCO testes, inclusive o POSITIVO, com
    `OverflowError` no lugar do `SystemExit` — e o estouro não é sempre na
    comparação: com data no passado a comparação encadeada curto-circuita em
    `hoje <= corte_dia` e quem morre é a MENSAGEM de erro, que recomputa
    `hoje + timedelta(days=MAX)`; com data legítima morre a comparação. Nos
    dois ramos a leitura se inverte. As duas patologias estão registradas em
    `docs/controles_declarados.md`. `date.max` alarga sem estourar.
    G e H são bordas SEPARADAS: nenhuma das duas reprova o teste da outra.
    **Nenhuma delas reprova `test_corte_de_hoje_e_o_caminho_legitimo_passam`**
    — é o controle POSITIVO, e é ele que separa "a guarda discrimina" de "a
    guarda recusa tudo", que seria pior que não ter guarda nenhuma.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import pytest

from _aviso_fim_do_gratis_helpers import (
    CARENCIA_8D,
    ESPERADO_AVISAR,
    FREE_PURO,
    PAGO_EXPIRADO,
    SEM_PLANO,
    limpar_dedupe,
    montar_base,
)
from _billing_grants_helpers import garantir_system_event_logs

# Data RELATIVA em todo lugar: `main` recusa corte fora da faixa
# [hoje, hoje + MAX_DIAS_ATE_O_CORTE], e uma data fixa apodreceria — o teste
# passaria a morrer no `ap.error` no dia em que ela ficasse no passado.
CORTE_OK = (date.today() + timedelta(days=14)).isoformat()


def _argv(monkeypatch, corte: str, *flags) -> None:
    monkeypatch.setattr(sys, "argv", ["aviso", "--corte", corte, *flags])


def _coletor(monkeypatch, aviso) -> list[tuple[str, bool]]:
    """Registra `(destinatário, cobranca_pendente)` de cada envio.

    O flag entra na lista de propósito: um fake que o descartasse deixaria a
    ESCOLHA do coorte sem medição nenhuma — a copy forkaria e ninguém veria se
    o fork foi escolhido ao contrário (medido: `cobranca_pendente=not ...`
    passava com todos verdes)."""
    enviados: list[tuple[str, bool]] = []

    def _fake_send(to, corte, dashboard_url="", **kw):
        enviados.append((to, kw.get("cobranca_pendente")))
        return True

    monkeypatch.setattr(aviso, "send_free_plan_sunset_email", _fake_send)
    return enviados


def test_dry_run_nao_envia_e_a_segunda_rodada_nao_reenvia(monkeypatch):
    """O script roda DUAS vezes em produção (abertura da janela e véspera do
    merge). A sequência inteira, contra o banco: dry-run, `--apply`, `--apply`
    de novo — e a dedupe real de `system_event_logs`, não um mock dela."""
    import scripts.aviso_fim_do_gratis as aviso

    garantir_system_event_logs()
    montar_base()
    limpar_dedupe()
    enviados = _coletor(monkeypatch, aviso)

    def _rodar(*flags):
        _argv(monkeypatch, CORTE_OK, *flags)
        aviso.main()
        # Só os desta faixa: a varredura é de base e pega conta de outro teste.
        meus = {to for to, _ in enviados if to.startswith("gr-77")}
        enviados.clear()
        return meus

    assert _rodar() == set(), "dry-run mandou e-mail"

    esperado = {f"gr-{uid}@t.local" for uid in ESPERADO_AVISAR}
    assert _rodar("--apply") == esperado
    assert _rodar("--apply") == set(), "segunda rodada reenviou"


def test_revalidacao_que_estoura_nao_aborta_o_lote(monkeypatch):
    """Uma conta ruim não pode levar os candidatos SEGUINTES junto. A
    revalidação é a terceira chamada de rede do laço (as outras duas tratam a
    exceção por dentro), e FORA do `try` ela matava o lote na primeira falha."""
    import scripts.aviso_fim_do_gratis as aviso

    garantir_system_event_logs()
    montar_base()
    limpar_dedupe()

    real = aviso.listar_contas_do_aviso
    vitima = min(ESPERADO_AVISAR)          # a SQL ordena por user_id

    def _estoura_na_vitima(user_id=None):
        if user_id == vitima:
            raise RuntimeError("banco caiu na revalidação")
        return real(user_id)

    enviados = _coletor(monkeypatch, aviso)
    monkeypatch.setattr(aviso, "listar_contas_do_aviso", _estoura_na_vitima)
    _argv(monkeypatch, CORTE_OK, "--apply")
    aviso.main()

    meus = {to for to, _ in enviados if to.startswith("gr-77")}
    assert f"gr-{vitima}@t.local" not in meus, "a conta que estourou foi avisada"
    assert meus == {f"gr-{uid}@t.local" for uid in ESPERADO_AVISAR - {vitima}}, \
        "o lote abandonou os candidatos seguintes"


def test_o_coorte_de_cada_conta_decide_a_copy(monkeypatch):
    """A copy forka em dois coortes; este teste mede QUEM escolhe o fork.

    Sem ele, a decisão (`ciclo_de_atraso_aberto`) e o resultado (a frase que o
    cliente lê) ficam desligados: o Manager injetou
    `cobranca_pendente=not ciclo_de_atraso_aberto(uid)` — a inversão completa,
    que manda "sua cobrança não passou" para quem está no Grátis e chama de
    Grátis o cliente com cartão recusado — e a bateria inteira passou.

    O par: `CARENCIA_8D` é o assinante em dunning (relógio carimbado + status
    na lista, carência VENCIDA, por isso está na população) e tem de receber
    True; `PAGO_EXPIRADO` (cancelado), `FREE_PURO` e `SEM_PLANO` não têm
    relógio nenhum e têm de receber False. As duas direções no mesmo assert:
    uma inversão troca os dois lados de uma vez.
    """
    import scripts.aviso_fim_do_gratis as aviso

    garantir_system_event_logs()
    montar_base()
    limpar_dedupe()
    enviados = _coletor(monkeypatch, aviso)
    _argv(monkeypatch, CORTE_OK, "--apply")
    aviso.main()

    coorte = {to: flag for to, flag in enviados if to.startswith("gr-77")}
    assert coorte == {
        f"gr-{CARENCIA_8D}@t.local": True,
        f"gr-{PAGO_EXPIRADO}@t.local": False,
        f"gr-{FREE_PURO}@t.local": False,
        f"gr-{SEM_PLANO}@t.local": False,
    }


# ── A guarda do `--corte` ────────────────────────────────────────────────────
#
# É a ÚNICA entrada humana do script, ela vai direto para a copy de um envio
# irreversível para a base inteira, e a dedupe de `AVISO_DEDUPE_DAYS` impede
# consertar re-executando. Ano digitado errado não tem desfazer.
#
# Os três casos abaixo rodam em DRY-RUN: o que se mede é a guarda, e nenhum
# e-mail precisa sair para isso. `ap.error` sai por `SystemExit`.

def test_corte_no_passado_e_recusado(monkeypatch):
    import scripts.aviso_fim_do_gratis as aviso
    _argv(monkeypatch, (date.today() - timedelta(days=1)).isoformat())
    with pytest.raises(SystemExit):
        aviso.main()


def test_corte_absurdamente_longe_e_recusado(monkeypatch):
    """DOIS casos, e eles medem coisas diferentes: `9999-12-31` é o ano
    digitado errado (o valor real que passava antes da guarda), ABSOLUTO; o
    outro é UM DIA além de `MAX_DIAS_ATE_O_CORTE`, derivado da constante, e
    prende a posição exata da borda.

    Medido: só o caso ABSOLUTO discrimina o valor do teto — alargar a
    constante move junto o caso derivado dela, e a injeção fica invisível (é a
    armadilha do controle H)."""
    import scripts.aviso_fim_do_gratis as aviso

    borda = date.today() + timedelta(days=aviso.MAX_DIAS_ATE_O_CORTE + 1)
    for alvo in ("9999-12-31", borda.isoformat()):
        _argv(monkeypatch, alvo)
        with pytest.raises(SystemExit):
            aviso.main()


def test_corte_de_hoje_e_o_caminho_legitimo_passam(monkeypatch):
    """CONTROLE POSITIVO da guarda, e ele tem duas metades: HOJE é a borda de
    baixo (inclusiva — o corte pode ser no mesmo dia do merge) e `CORTE_OK` é o
    uso normal. Sem este teste, uma guarda que recusasse tudo passaria nos dois
    de cima — e recusar tudo é pior que não ter guarda, porque o aviso nunca
    sai e o corte pega a base inteira sem avisar ninguém."""
    import scripts.aviso_fim_do_gratis as aviso

    montar_base()
    enviados = _coletor(monkeypatch, aviso)
    for corte in (date.today().isoformat(), CORTE_OK):
        _argv(monkeypatch, corte)
        aviso.main()                       # dry-run: não pode levantar
    assert enviados == [], "dry-run mandou e-mail"


def test_copy_traz_a_data_do_corte_e_nao_promete_trial(monkeypatch):
    """A data tem de atravessar o script inteiro (`--corte` → `_corte_para_email`
    → `_fmt_brl_date`): meia-noite UTC vira o DIA ANTERIOR em BRT. E a copy não
    pode prometer trial — são 15 dias por TELEFONE na vida
    (`db.plans.claim_trial_for_user`), e esta lista tem ex-assinante que já
    usou o dele."""
    import core.services.email_service as es
    from scripts.aviso_fim_do_gratis import _corte_para_email

    capturado: dict = {}

    def _fake(to, subject, html_body, text_body="", **kw):
        capturado.update(to=to, subject=subject, html=html_body,
                         text=text_body, kw=kw)
        return True

    monkeypatch.setattr(es, "send_email", _fake)
    corte = _corte_para_email(date(2026, 9, 24))
    es.send_free_plan_sunset_email("quem@exemplo.com", corte)

    assert "24/09/2026" in capturado["html"]
    assert "24/09/2026" in capturado["text"]
    assert "24/09/2026" in capturado["subject"]
    corpo = (capturado["html"] + capturado["text"]).lower()
    for proibido in ("15 dias", "trial", "teste grátis", "teste gratis"):
        assert proibido not in corpo, f"copy promete {proibido!r}"
    # Transacional: sem link nem cabeçalho de descadastro (o molde do
    # `send_payment_failed_email`, não o dos e-mails de ciclo de vida).
    assert "unsub" not in corpo
    assert "descadastr" not in corpo
    assert capturado["kw"] == {}
    # Coorte 1: Grátis/sem plano — a frase da situação é essa mesma.
    assert "grátis" in corpo

    # Coorte 2: assinatura VIVA com cartão recusado além da carência. Ela entra
    # na população (vai perder acesso), mas chamá-la de "plano Grátis" é falso
    # e contradiz o e-mail de falha e o lembrete que ela já recebeu.
    capturado.clear()
    es.send_free_plan_sunset_email("quem@exemplo.com", corte,
                                   cobranca_pendente=True)
    corpo2 = (capturado["html"] + capturado["text"]).lower()
    assert "grátis" not in corpo2 and "gratis" not in corpo2
    assert "cartão" in corpo2 or "cartao" in corpo2
    assert "24/09/2026" in corpo2
