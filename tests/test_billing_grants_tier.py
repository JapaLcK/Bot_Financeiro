"""
tests/test_billing_grants_tier.py — QUEDA DE TIER na varredura (§4.1.1 B/D do
docs/plano_pix_anual_asaas.md).

Arquivo próprio porque `test_billing_grants_reducao.py` está em 340/350 linhas,
e porque é assunto próprio: lá a redução é de DATA (`_e_reducao`), aqui é de
TIER — o eixo que `_e_reducao` não enxerga.

**A regra dos dois eixos, e a assimetria que a explica.** Os dois mandam
CONSULTAR o Stripe; só o da data pode CONGELAR:

  • DATA — o erro é `free`, o usuário perde o produto inteiro. Só `reduz`
    autoriza encurtar; qualquer outro veredito não escreve (9c/9d, no arquivo
    da redução);
  • TIER — a matriz é INCAPAZ de confirmar tier (todo reparo move só a data),
    então "não confirmou" é o caso NORMAL desta entrada. Só `indisponivel` não
    escreve; nos demais vereditos ESCREVE o que os grants dizem e registra
    `projecao_queda_de_tier`. Os dois erros são simétricos e se curam no mesmo
    prazo — o próximo evento reprojeta com `origem="evento"`.

A versão anterior deste arquivo testava uma regra de CONTIGUIDADE que tentava
separar "downgrade agendado" de "defeito" caminhando a cadeia de grants. Ela
saiu inteira: engolia o caso COMUM (quem assina o barato antes de o caro vencer
produz grants SOBREPOSTOS) e congelava a conta no tier alto.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _billing_grants_helpers import (
    FakeStripeSubs, conta as _conta, ler as _ler, set_customer,
)
from core.services.billing_access import recompute_entitlement
from db.plan_grants import list_grants, upsert_grant


def _sub_viva(sub_id: str, fim: datetime) -> dict:
    ts = int(fim.timestamp())
    return {"id": sub_id, "status": "active", "current_period_end": ts,
            "items": {"data": [{"current_period_end": ts}]}}


def _usar_stripe(monkeypatch, fake) -> None:
    monkeypatch.setitem(__import__("sys").modules, "stripe", fake)


def _capturar_registros(monkeypatch) -> list[tuple[str, str]]:
    """Pares (evento, mensagem) do `_observar`. A MENSAGEM importa: é onde o
    veredito aparece, e é a única coisa que separa células com observável
    igual."""
    registros: list[tuple[str, str]] = []
    import core.observability as observability
    monkeypatch.setattr(observability, "log_system_event_sync",
                        lambda *a, **k: registros.append((a[1], a[2])),
                        raising=False)
    return registros


@pytest.mark.parametrize("tier_menor", ["essencial", "pro"])
def test_queda_de_tier_consulta_o_stripe_e_o_reparo_restaura(user_id, monkeypatch, tier_menor):
    """Tier cai → a varredura CONSULTA, e é a consulta que salva o tier.

    Este é o caso que justifica a entrada na matriz: o grant `pro_max` está
    TRUNCADO por falha de materialização (venceu há 10 dias com a assinatura
    viva), e a projeção passa a dizer o tier menor mantendo a MESMA data —
    `_e_reducao` não vê redução nenhuma nisso, porque só compara `free` e data.

    O Stripe devolve a assinatura do `pro_max` viva e mais longa → célula 7
    estica o grant, ele volta a ser vigente, e o tier alto volta SOZINHO, sem
    ninguém escrever tier.

    NEGATIVO: tire o `or _queda_de_tier(...)` de `recompute_entitlement` → a
    varredura escreve o tier menor sem nem consultar o Stripe, e este teste
    fica vermelho nos dois parâmetros.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro_max", agora + timedelta(days=30), "active")
    set_customer(user_id, f"cus_{user_id}")
    # Tier ALTO, venceu por relógio há 10 dias (nunca revogado).
    upsert_grant(user_id, "stripe", f"sub_max_{user_id}", "pro_max",
                 agora - timedelta(days=395), agora - timedelta(days=10), 1_000)
    # Tier MENOR, outra assinatura, vigente há 60 dias — 50 dias de buraco até
    # o fim do grant alto, muito além da GAP_TOLERANCIA de 120 s.
    upsert_grant(user_id, "stripe", f"sub_men_{user_id}", tier_menor,
                 agora - timedelta(days=60), agora + timedelta(days=30), 1_000)
    assert _ler(user_id)["plan"] == "pro_max"

    fim = agora + timedelta(days=60)
    _usar_stripe(monkeypatch, FakeStripeSubs(ativa=_sub_viva(f"sub_max_{user_id}", fim)))

    resultado = recompute_entitlement(user_id, origem="varredura")

    assert _ler(user_id)["plan"] == "pro_max", (
        f"varredura rebaixou o tier para {tier_menor} sem o Stripe confirmar")
    assert resultado and resultado["plan"] == "pro_max"
    alto = next(g for g in list_grants(user_id)
                if g["external_ref"] == f"sub_max_{user_id}")
    assert alto["ends_at"] > agora, "o grant do tier alto não foi reparado"
    assert alto["plan_stored"] == "pro_max", "o reparo não pode inventar tier"


# ── R2-5: a queda de tier ESCREVE; só `indisponivel` segura ──────────────────
#
# O par abaixo é o mesmo cenário com o Stripe respondendo e com o Stripe fora do
# ar. É o caso COMUM de downgrade: o usuário assina o plano barato 5 dias antes
# de o caro vencer, então os grants ficam SOBREPOSTOS. A regra de contiguidade
# que existia aqui lia isso como defeito e congelava a conta em `pro_max`.

def _cenario_downgrade_sobreposto(uid: int):
    """`pro_max [-60d, -30d]` e `essencial [-35d, +30d]` — 5 dias de sobreposição."""
    agora = datetime.now(timezone.utc)
    _conta(uid, "pro_max", agora + timedelta(days=30), "active")
    set_customer(uid, f"cus_{uid}")
    upsert_grant(uid, "stripe", f"max_{uid}", "pro_max",
                 agora - timedelta(days=60), agora - timedelta(days=30), 1_000)
    upsert_grant(uid, "stripe", f"ess_{uid}", "essencial",
                 agora - timedelta(days=35), agora + timedelta(days=30), 1_000)
    return agora


def test_R2_5_queda_de_tier_nao_confirmada_ESCREVE_e_registra(user_id, monkeypatch):
    """O Stripe responde e **não confirma o tier** (não tem como: a matriz só
    move data). Veredito `nao_reduz` — e mesmo assim a projeção ESCREVE.

    Congelar aqui dava tier de graça por tempo indefinido a quem já não paga por
    ele. Escrever pode tirar tier de quem paga, mas os dois erros se curam no
    MESMO prazo — o próximo evento daquela assinatura reprojeta com
    `origem="evento"`, que não passa por nada disto.

    NEGATIVO: volte os vereditos `nao_reduz`/`reparo_insuficiente` a devolver
    `None` (o congelamento da v6) → vermelho.
    """
    agora = _cenario_downgrade_sobreposto(user_id)
    # Assinatura viva do ESSENCIAL, já coberta pelo grant → célula 6.
    _usar_stripe(monkeypatch, FakeStripeSubs(
        ativa=_sub_viva(f"ess_{user_id}", agora + timedelta(days=10))))

    registros = _capturar_registros(monkeypatch)

    resultado = recompute_entitlement(user_id, origem="varredura")

    assert resultado and resultado["plan"] == "essencial", (
        f"downgrade com sobreposicao de 5 dias travou no tier alto: {resultado}")
    assert _ler(user_id)["plan"] == "essencial"
    # O tipo do evento não basta: células com o MESMO observável só se separam
    # pelo VEREDITO no texto. A 6 (aqui) escreve; a 5 tem de segurar, e as duas
    # dividiam a string `nao_reduz` até o R3-1.
    # Forma FECHADA (`veredito nao_reduz)`) e nao substring nua: `"reduz" in m`
    # casaria com `"nao_reduz"` e vice-versa, e o teste ficaria verde na celula
    # errada. Vale para toda assercao de veredito deste arquivo.
    assert any(e == "projecao_queda_de_tier" and "veredito nao_reduz)" in m
               for e, m in registros), (
        f"passou por outra celula da matriz, ou nao registrou: {registros}")


def test_POSITIVO_queda_de_tier_com_stripe_INDISPONIVEL_nao_escreve(user_id, monkeypatch):
    """O positivo do par: sem INFORMAÇÃO nunca se escreve.

    Mesmo cenário do R2-5, só trocando a resposta do Stripe por uma falha. Sem
    este teste, o grupo passaria num código que escreve queda de tier sempre —
    inclusive quando o gateway está fora do ar e ninguém sabe de nada.
    """
    _cenario_downgrade_sobreposto(user_id)
    _usar_stripe(monkeypatch, FakeStripeSubs(erro=True))

    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda m: alertas.append(m) or True)

    assert recompute_entitlement(user_id, origem="varredura") is None, (
        "escreveu queda de tier com o Stripe fora do ar")
    assert _ler(user_id)["plan"] == "pro_max"
    assert len(alertas) == 1
    assert "indisponivel" in alertas[0], (
        f"passou pela celula errada da matriz: {alertas[0]!r}")


def test_reparo_do_tier_menor_nao_congela_a_conta(user_id, monkeypatch):
    """O caminho `reparou` com a queda de tier PERSISTINDO.

    O alvo do reparo é o grant do tier MENOR (é ele que tem a assinatura viva).
    A célula 7 estica a data, o grant volta vigente — e o tier continua caído.
    `_e_reducao` diz `False` porque a data até AUMENTOU, então quem decide é a
    regra do tier: escreve.

    Foi aqui que a v6 congelava por `reparo_insuficiente`.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro_max", agora + timedelta(days=30), "active")
    set_customer(user_id, f"cus_{user_id}")
    upsert_grant(user_id, "stripe", f"sub_max_{user_id}", "pro_max",
                 agora - timedelta(days=395), agora - timedelta(days=10), 1_000)
    upsert_grant(user_id, "stripe", f"sub_ess_{user_id}", "essencial",
                 agora - timedelta(days=60), agora + timedelta(days=30), 1_000)

    _usar_stripe(monkeypatch, FakeStripeSubs(
        ativa=_sub_viva(f"sub_ess_{user_id}", agora + timedelta(days=40))))

    resultado = recompute_entitlement(user_id, origem="varredura")

    ess = next(g for g in list_grants(user_id)
               if g["external_ref"] == f"sub_ess_{user_id}")
    assert ess["ends_at"] > agora + timedelta(days=35), "o reparo nem rodou"
    assert resultado and resultado["plan"] == "essencial", (
        f"congelou depois do reparo em vez de escrever: {resultado}")


def test_R3_1_celula5_assinatura_VIVA_sem_grant_nosso_NAO_rebaixa(user_id, monkeypatch):
    """CÉLULA 5 no eixo do TIER — a que faltava na suíte inteira.

    O Stripe tem a assinatura `pro_max` VIVA e nós não temos grant ATIVO dela
    (nunca materializou, ou foi revogada por engano); o único grant vigente é o
    `essencial`. A projeção quer escrever `essencial`, e a promessa da matriz é
    literal: *"quem tem assinatura viva não perde acesso por falta de linha
    nossa"*.

    Enquanto as células 5 e 6 dividiram a string `nao_reduz`, a regra do tier
    não conseguia segurar uma sem travar a outra — e como a lista era de quem
    SEGURA, a 5 caiu fora dela e rebaixou.

    NEGATIVO: ponha `"sem_grant"` na lista de quem escreve (ou faça a célula 5
    devolver `nao_reduz` de novo) → a conta é rebaixada e este teste fica
    vermelho.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro_max", agora + timedelta(days=30), "active")
    set_customer(user_id, f"cus_{user_id}")
    upsert_grant(user_id, "stripe", f"ess_{user_id}", "essencial",
                 agora - timedelta(days=60), agora + timedelta(days=30), 1_000)
    _usar_stripe(monkeypatch, FakeStripeSubs(
        ativa=_sub_viva(f"max_{user_id}", agora + timedelta(days=60))))

    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda m: alertas.append(m) or True)

    assert recompute_entitlement(user_id, origem="varredura") is None, (
        "celula 5: rebaixou conta com assinatura VIVA no Stripe")
    assert _ler(user_id)["plan"] == "pro_max"
    assert len(alertas) == 1
    assert "(sem_grant)" in alertas[0], (
        f"celula 5 tem de ter veredito PROPRIO, senao ninguem a separa da 6: "
        f"{alertas[0]!r}")


def test_R3_5_esticar_que_nao_esticou_nada_NAO_vira_veredito_reparou(user_id, monkeypatch):
    """O veredito é o que autoriza escrita — não pode mentir.

    `esticar_grant` não casa linha quando o grant sumiu, foi revogado ou já foi
    esticado entre o `list_grants` e o `UPDATE`. Ignorar o bool fazia sair um
    registro "veredito reparou" sem nada ter sido reparado, e ainda autorizava
    a escrita da queda de tier.

    NEGATIVO: volte a ignorar o retorno (`esticar_grant(...); return "reparou"`)
    → vermelho nas duas asserções.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro_max", agora + timedelta(days=30), "active")
    set_customer(user_id, f"cus_{user_id}")
    upsert_grant(user_id, "stripe", f"max_{user_id}", "pro_max",
                 agora - timedelta(days=60), agora - timedelta(days=30), 1_000)
    upsert_grant(user_id, "stripe", f"ess_{user_id}", "essencial",
                 agora - timedelta(days=35), agora + timedelta(days=30), 1_000)

    import db.plan_grants as pg
    monkeypatch.setattr(pg, "esticar_grant", lambda *a, **k: False)
    _usar_stripe(monkeypatch, FakeStripeSubs(
        ativa=_sub_viva(f"max_{user_id}", agora + timedelta(days=60))))
    registros = _capturar_registros(monkeypatch)

    assert recompute_entitlement(user_id, origem="varredura") is None, (
        "escreveu com base num reparo que nao reparou nada")
    assert not any("reparou" in m for _, m in registros), (
        f"registrou veredito 'reparou' sem ter esticado nada: {registros}")


def test_B1_reducao_para_free_CONFIRMADA_nao_dispara_o_alerta_de_tier(user_id, monkeypatch):
    """O caminho LEGÍTIMO de redução não pode gastar o canal de alerta.

    `_tier_do_stored("free") == 0`, então **toda** redução para `free` também é
    queda de tier — e o registro disparava nela, com uma mensagem que se
    contradiz: *"tier caiu de pro para free (veredito reduz); a matriz nao
    confirma tier"*. `veredito reduz` é justamente o Stripe CONFIRMANDO que não
    há assinatura.

    Não é caso raro: é a célula 3 (dunning esgotado, assinatura encerrada sem
    `deleted` processado) e a célula 1 (conta **sem `stripe_customer_id`** — que
    será TODA conta Pix do PR 1b). O dado sai certo; o que quebrava era o CANAL:
    `_observar` alerta 1x/dia por conta, e é o mesmo canal que tem de fazer um
    humano olhar `indisponivel`/`sem_grant`.

    **O veredito aqui é fixado pela CONSTRUÇÃO, não por substring:**
    `FakeStripeSubs(ativa=None)` faz `_find_active_subscription` devolver `None`,
    que é a célula 3 e só ela — veredito `reduz`. E o `resultado` `free/None`
    prova que o caminho de escrita rodou (um veredito de segurar devolveria
    `None`).

    NEGATIVO: tire o `and not _e_reducao(...)` da condição do registro → o
    `projecao_queda_de_tier` volta a sair e este teste fica vermelho. A bateria
    de tier cobria `nao_reduz`, `indisponivel`, `sem_grant` e `reparou`, e
    **nunca `reduz`** — foi por isso que ninguém pegou.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro", agora + timedelta(days=330), "active")
    set_customer(user_id, f"cus_{user_id}")
    upsert_grant(user_id, "stripe", f"sub_b1_{user_id}", "pro",
                 agora - timedelta(days=395), agora - timedelta(days=30), 1_000)

    _usar_stripe(monkeypatch, FakeStripeSubs(ativa=None))     # célula 3 → `reduz`
    registros = _capturar_registros(monkeypatch)
    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda m: alertas.append(m) or True)

    resultado = recompute_entitlement(user_id, origem="varredura")

    assert resultado == {"plan": "free", "plan_expires_at": None}, (
        f"a reducao confirmada tem de ser escrita normalmente: {resultado}")
    assert not any(e == "projecao_queda_de_tier" for e, _ in registros), (
        f"alerta de tier disparou numa reducao que o Stripe CONFIRMOU: "
        f"{registros}")
    assert alertas == [], f"cancelamento de rotina gastou o canal: {alertas}"


def test_B1_queda_de_tier_NAO_confirmada_com_veredito_reduz_AINDA_alerta(user_id, monkeypatch):
    """O outro lado do B1 — e o que separa o conserto da versão larga.

    `veredito == "reduz"` não quer dizer "o tier foi confirmado": quer dizer
    "não há assinatura no Stripe". Aqui a conta é `pro_max` **sem
    `stripe_customer_id`** (célula 1, o veredito sai sem nem consultar) e o
    grant vigente é de tier menor terminando na MESMA data — o tier caiu e
    **ninguém confirmou**, então o registro TEM de sair.

    É o caso que o PR 1b torna comum: conta só-Pix não tem `stripe_customer_id`,
    logo cai sempre na célula 1.

    Por isso a condição é `and not _e_reducao(...)` — subtrair o que o OUTRO
    eixo já cobre — e não `veredito != "reduz"`, que é mais curta e silencia
    este caso junto. Sem este teste as duas passam iguais: eu medi.

    Assertion em forma FECHADA (`veredito reduz)`), porque `"reduz" in m`
    casaria com `"nao_reduz"` e o teste ficaria verde na célula 6.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro_max", agora + timedelta(days=30), "active")
    # sem `set_customer`: célula 1 → veredito `reduz` sem consultar o Stripe
    upsert_grant(user_id, "stripe", f"max_b1b_{user_id}", "pro_max",
                 agora - timedelta(days=60), agora - timedelta(days=30), 1_000)
    upsert_grant(user_id, "stripe", f"ess_b1b_{user_id}", "essencial",
                 agora - timedelta(days=35), agora + timedelta(days=30), 1_000)

    registros = _capturar_registros(monkeypatch)
    resultado = recompute_entitlement(user_id, origem="varredura")

    assert resultado and resultado["plan"] == "essencial"
    assert any(e == "projecao_queda_de_tier" and "veredito reduz)" in m
               for e, m in registros), (
        f"queda de tier sem confirmacao de tier ficou sem registro: {registros}")


def test_B1_tier_e_data_caindo_JUNTOS_ainda_registram_a_queda_de_tier(user_id, monkeypatch):
    """Os DOIS eixos se movendo ao mesmo tempo — o buraco simétrico do B1.

    Conta `pro_max`/+60d, grant `pro_max` vencido e `essencial` vigente só até
    +10d: o tier cai **e** a data encurta. A primeira versão do conserto
    subtraía pela opinião do outro eixo (`and not _e_reducao(...)`), e aqui
    `_e_reducao` dá `True` — então o registro SUMIA e a queda de tier era
    escrita em silêncio, que é exatamente o que ele existe para impedir.

    O predicado certo é o **DESTINO** (`plano != "free"`): o que tornava a
    mensagem contraditória era ir para `free`, nunca "o outro eixo também viu".

    NEGATIVO: volte para `and not _e_reducao(plano_atual, expira_atual, plano,
    expira)` → `eventos` fica vazio e este teste fica vermelho. Forma FECHADA na
    asserção do veredito, como no resto do arquivo.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro_max", agora + timedelta(days=60), "active")
    # sem `set_customer`: célula 1 → veredito `reduz` sem consultar o Stripe
    upsert_grant(user_id, "stripe", f"max_2e_{user_id}", "pro_max",
                 agora - timedelta(days=60), agora - timedelta(days=30), 1_000)
    upsert_grant(user_id, "stripe", f"ess_2e_{user_id}", "essencial",
                 agora - timedelta(days=35), agora + timedelta(days=10), 1_000)

    registros = _capturar_registros(monkeypatch)
    resultado = recompute_entitlement(user_id, origem="varredura")

    assert resultado and resultado["plan"] == "essencial"
    assert resultado["plan_expires_at"] < agora + timedelta(days=11), (
        "a data tambem tinha de encurtar neste cenario")
    assert any(e == "projecao_queda_de_tier" and "veredito reduz)" in m
               for e, m in registros), (
        f"tier caiu junto com a data e a queda foi escrita em SILENCIO: {registros}")
