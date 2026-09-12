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
  • J — na copy do e-mail, alargue o "a partir de" para o plano mais caro:
    `min(PRECOS_ANUAIS_CENTS.values())` → `max(...)`:
      VERMELHO: test_copy_traz_a_data_do_corte_e_nao_promete_trial.
  • K — troque a derivação do preço pelos valores de hoje escritos à mão
    (R$ 99,00 e R$ 8,25):
      VERMELHO: test_copy_traz_a_data_do_corte_e_nao_promete_trial.
  • L — faça o pitch (frase de valor + preço) sair também quando
    `cobranca_pendente` é verdadeiro:
      VERMELHO: test_copy_traz_a_data_do_corte_e_nao_promete_trial.
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


def _corpos(capturado: dict):
    """`(nome, corpo, tokens da frase de valor)` dos dois corpos.

    Espaço normalizado — o HTML quebra linha no meio da frase e o token não pode
    depender de onde caiu a quebra; o `text_body` é sem acento por convenção. Os
    tokens saem DAQUI para os dois coortes: a 1 exige, a 2 proíbe, e com listas
    separadas um dos lados pararia de medir. Nenhum colide com o resto da copy da
    coorte 2 (medido) — "cartões" colidiria com o CTA "Atualizar cartão"."""
    return ((nome, " ".join(capturado[nome].lower().split()),
             ("cupom", "boletos", audio, "sem limite"))
            for nome, audio in (("html", "áudio"), ("text", "audio")))


def test_copy_traz_a_data_do_corte_e_nao_promete_trial(monkeypatch):
    """A data tem de atravessar o script inteiro (`--corte` → `_corte_para_email`
    → `_fmt_brl_date`): meia-noite UTC vira o DIA ANTERIOR em BRT. E a copy não
    pode prometer trial — são 15 dias por TELEFONE na vida
    (`db.plans.claim_trial_for_user`), e esta lista tem ex-assinante que já
    usou o dele.

    O preço é medido por DERIVAÇÃO: a fonte (`PRECOS_ANUAIS_CENTS`) é trocada em
    runtime e o e-mail tem de renderizar o valor NOVO — literal cravado na copy
    reprova, que é o modo de falha que importa (o preço muda e o e-mail mente).
    A divisão por 12 continua duplicada aqui; quem a mede contra fonte
    independente é `test_aviso_fim_do_gratis_preco.py`."""
    import core.services.email_service as es
    import core.services.pix_pricing as pp
    from scripts.aviso_fim_do_gratis import _corte_para_email
    from utils_text import fmt_brl

    # A fonte do preço trocada ANTES do envio. 30000 é ACIMA do `pro` de
    # propósito: o mínimo deixa de ser o `essencial`, e trocar o `min(...)` da
    # produção por `["essencial"]` reprova (com 8900 os dois coincidiam). Não é
    # valor de produção nem redondo, então literal cravado não coincide por acaso.
    monkeypatch.setitem(pp.PRECOS_ANUAIS_CENTS, "essencial", 30000)
    proibidos = ("15 dias", "trial", "teste grátis", "teste gratis")

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
    for proibido in proibidos:
        assert proibido not in corpo, f"copy promete {proibido!r}"
    # Transacional: sem link nem cabeçalho de descadastro (o molde do
    # `send_payment_failed_email`, não o dos e-mails de ciclo de vida).
    assert "unsub" not in corpo
    assert "descadastr" not in corpo
    assert capturado["kw"] == {}
    # Coorte 1: Grátis/sem plano — a frase da situação é essa mesma.
    assert "grátis" in corpo
    # ...e o pitch. Esperado calculado DEPOIS do envio, da fonte já trocada.
    anual_cents = min(pp.PRECOS_ANUAIS_CENTS.values())
    preco_anual = fmt_brl(anual_cents / 100).lower()
    preco_mensal = fmt_brl(anual_cents / 12 / 100).lower()
    # HTML e texto conferidos SEPARADO: concatenar deixaria passar o parágrafo
    # perdido em um dos dois corpos, com metade dos leitores vendo cada um. Os
    # tokens são DISCRIMINANTES ("plano ativo" já existe na copy, seria verde por
    # construção) e cobrem quatro promessas: "cupom boletos" sozinho reprova.
    for nome, parte, tokens in _corpos(capturado):
        assert preco_anual in parte, f"coorte 1, {nome}: sem o anual ({preco_anual})"
        assert preco_mensal in parte, f"coorte 1, {nome}: sem o mensal ({preco_mensal})"
        for token in tokens:
            assert token in parte, f"coorte 1, {nome}: sem a frase de valor ({token!r})"

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
    # Nem preço nem frase de valor (decisão do dono, 2026-09-10): essa conta já
    # COMPROU o plano e está inadimplente, não indecisa — a ação dela é o cartão,
    # e o e-mail dela encolhe em vez de crescer. Mesmos tokens da coorte 1, pela
    # mesma fonte, com o sinal trocado.
    for nome, parte, tokens in _corpos(capturado):
        assert preco_anual not in parte, f"coorte 2, {nome}: recebeu o 'a partir de'"
        assert preco_mensal not in parte, f"coorte 2, {nome}: recebeu o mensal"
        for token in tokens:
            assert token not in parte, f"coorte 2, {nome}: recebeu o pitch ({token!r})"
    # Proibição de trial: aqui a direção segura é a oposta — tem de valer nos
    # dois corpos, e a concatenação garante isso.
    for proibido in proibidos:
        assert proibido not in corpo2, f"copy da coorte 2 promete {proibido!r}"
