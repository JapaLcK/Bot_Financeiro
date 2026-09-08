"""
core/services/pix_pricing.py — preço, crédito e vigência da cobrança Pix (§7).

Módulo PRÓPRIO, e não uma função a mais em `billing_access.py`: aquele arquivo
é a PROJEÇÃO (grants → `auth_accounts`), fala com o banco e com o Stripe. Este
é aritmética de venda e não fala com nada. Misturar os dois faria a próxima
pessoa achar que pode ler o banco aqui.

Plano: docs/plano_pix_anual_asaas.md §7 e §9.

**Fatia INERTE (PR 1b-A): nenhum módulo de produção importa este arquivo.**
O chamador é o checkout, no PR 1b-B.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.services.billing_access import _tier_do_stored

# A vigência do plano anual, em dias. Um número, um lugar: `pix_charges` grava
# `duration_days` a partir daqui e `access_expires_at = access_starts_at + 365d`
# (§7). Crédito NUNCA vira tempo — quem compra upgrade recebe desconto em
# dinheiro, não meses a mais.
DURACAO_DIAS = 365


class CoberturaJaPaga(RuntimeError):
    """A compra não acrescentaria acesso nenhum — o cliente JÁ pagou por ela.

    **Não cobra e não altera grant algum** (decisão do dono, 2026-09-07):
    levantada ANTES de qualquer escrita, e esta função é pura. Quem prova isso
    não é esta frase — é `test_recusa_nao_toca_no_banco_nem_muda_os_grants`, que
    roda a recusa com o pool do banco trocado por um espião.

    **O 409 é do 1b-B**, não daqui: o checkout mora lá e este PR não tem rota.
    Os três atributos existem para a tradução ser UMA linha, e para ninguém no
    1b-B reconsultar o banco pelo dado que já veio junto (o resultado poderia
    divergir do que motivou a recusa):

        except CoberturaJaPaga as exc:
            raise HTTPException(409, detail={"error": exc.ERRO,
                                             "plan": exc.plano,
                                             "covered_until": exc.cobertura_ate})

    Exceção e não campo `recusada` num dict: campo pode ser ignorado em
    silêncio, exceção não — e o modo de falha que ela impede foi MEDIDO
    (365 dias sobrepostos, 0 novos, R$ 499,00 cobrados).

    O porquê de recusar em vez de creditar, e a evolução separada que a
    substituição exigiria, estão no **§7 do plano** — aqui não, para não haver
    duas versões da mesma decisão (CLAUDE.md §0.7).
    """

    #: Código estável para o 409 do 1b-B. Não mude sem mudar o front junto.
    ERRO = "pix_future_purchase_conflict"

    def __init__(self, plano: str, cobertura_ate):
        super().__init__(
            f"cobertura de {plano} já paga até {cobertura_ate.isoformat()}"
        )
        self.plano = plano
        self.cobertura_ate = cobertura_ate


def _exigir_amount_cents(grants: list[dict]) -> None:
    """Grant `source='pix'` SEM `amount_cents` utilizável é erro, não zero.

    `amount_cents` não existe em `plan_grants` — vem de `pix_charges` por um
    `join` do chamador (ver a docstring pública). Esquecer o `join` fazia a
    coluna chegar `None`, e o `_credito_proporcional` devolvia **0**: crédito
    legítimo virava zero e o cliente pagava o valor CHEIO, calado. A própria
    docstring do helper dizia "ou o chamador esqueceu o `join`" — ou seja, o
    modo de falha estava documentado e tratado como normal (P1-1 do Codex).

    É a mesma lição do P1-2 da rodada anterior: **"não sei" não pode virar um
    valor que decide dinheiro.** Lá era `[]` autorizando apagar cobrança; aqui é
    `0` autorizando cobrar cheio.

    A guarda vale para TODO grant `pix` da lista, não só para o que acaba
    gerando crédito: o `join` esquecido é um bug do chamador em qualquer caso, e
    qual grant vira `atual` depende de dados. Verificar só o escolhido deixaria
    a falha aparecer para um cliente e não para outro.

    `stripe`, `legacy` e `admin` seguem aceitando `None`: aquele dinheiro está
    noutro gateway (ou não existe), e o §7 é explícito que crédito monetário só
    existe no caminho Pix → Pix.
    """
    for g in grants:
        if g.get("source") != "pix":
            continue
        valor = g.get("amount_cents")
        if not isinstance(valor, int) or isinstance(valor, bool) or valor < 0:
            raise ValueError(
                "grant pix sem `amount_cents` inteiro e não-negativo "
                f"({valor!r}) — o chamador provavelmente esqueceu o join com "
                "pix_charges, e sem ele o crédito vira 0 e a cobrança sai cheia"
            )


def _agora(agora: datetime | None) -> datetime:
    return agora or datetime.now(timezone.utc)


def _vigentes(grants: list[dict], agora: datetime) -> list[dict]:
    return [g for g in grants
            if g["starts_at"] <= agora < g["ends_at"]]


def plano_da_cobranca(
    grants_ativos: list[dict],
    plano_novo: str,
    preco_novo_cents: int,
    min_cents: int,
    agora: datetime | None = None,
) -> dict:
    """FUNÇÃO PURA: grants ativos + o que se quer comprar → o snapshot financeiro
    da cobrança. **Não abre conexão, não lê env, não chama o Stripe.**

    ## O contrato de entrada, e ele NÃO é `list_grants()` cru

    Cada item de `grants_ativos` precisa de:

        {"source", "plan_stored", "starts_at", "ends_at", "amount_cents"}

    `amount_cents` **não existe em `plan_grants`** — ele mora em `pix_charges`,
    porque é o que o cliente PAGOU naquela compra, e o crédito proporcional é
    calculado sobre ele. Quem monta a lista é o chamador, com o `join`
    `plan_grants.external_ref = pix_charges.id` para os grants de `source='pix'`;
    para os demais, `amount_cents` pode ser `None` (não gera crédito).

    Isto está escrito aqui de propósito: sem a frase, a leitura natural de
    "função pura que recebe grants" é chamar `list_grants()` e passar direto —
    e o `amount_cents` viria ausente, silenciosamente zerando todo crédito de
    upgrade. Não ponha um `get_conn()` aqui para resolver isso; o `join` é do
    chamador (§0.2: uma responsabilidade por lugar).

    `plano_novo` é o valor **LEGADO** da coluna `auth_accounts.plan` (`'pro'` =
    Plus, `'pro_max'` = Pro), o mesmo que `plan_grants.plan_stored` — é o que
    `_tier_do_stored` sabe ler, e é o que volta em `plan_stored`.

    `min_cents` é **parâmetro**, não `os.getenv("ASAAS_MIN_CHARGE_CENTS")`. O
    leitor da env e o 503 quando ela falta são do 1b-B — a env não tem default
    (§7), e um default inventado aqui viraria cobrança recusada pelo Asaas.

    ## O que devolve

        {"plan_stored", "access_starts_at", "access_expires_at",
         "price_cents", "credit_cents", "amount_cents", "agendada"}

    `amount_cents = price_cents - credit_cents` é o que se COBRA. `agendada`
    diz que a compra não é imediata: o acesso começa no fim do período atual.

    ## As quatro relações (§7)

    | relação                          | vigência              | preço | crédito |
    |----------------------------------|-----------------------|-------|---------|
    | mesmo plano (renovação)          | max(now, fim dos grants) | cheio | — |
    | upgrade, acesso atual **PIX**    | now                   | −crédito | proporcional |
    | upgrade, acesso atual **STRIPE** | fim do período do cartão | cheio | **nenhum** |
    | downgrade                        | fim do período atual  | cheio | — |

    **Crédito monetário existe só no caminho Pix → Pix.** Vindo do Stripe não há
    o que creditar: aquele dinheiro está no outro gateway, e o período já pago
    continua valendo — por isso a vigência começa no fim dele, e não hoje.
    """
    agora = _agora(agora)
    _exigir_amount_cents(grants_ativos)
    preco_novo_cents = int(preco_novo_cents)
    # Convertido AQUI e não no ramo que usa: `int(min_cents)` só era avaliado no
    # caminho de upgrade Pix→Pix, então `min_cents=None` passava silencioso em
    # três das quatro relações e estourava `TypeError` na quarta. Falha que
    # depende do ramo é pior que falha sempre — some no teste e aparece na venda.
    min_cents = int(min_cents)
    tier_novo = _tier_do_stored(plano_novo)

    vigentes = _vigentes(grants_ativos, agora)
    # `fim da cobertura` usa TODOS os grants ativos, não só os vigentes: uma
    # renovação comprada em cima de um downgrade já agendado tem de começar
    # depois dele, senão as duas janelas se sobrepõem e o cliente perde dias.
    fim_cobertura = max((g["ends_at"] for g in grants_ativos), default=agora)

    if not vigentes:
        # Nada VIGENTE para comparar. Duas situações bem diferentes caem aqui, e
        # `agendada` tem de separá-las: primeira compra (ou acesso já vencido),
        # que começa AGORA; e quem só tem grant FUTURO — um downgrade agendado —,
        # cuja compra emenda no fim dele e portanto **é** agendada.
        #
        # A versão anterior devolvia `agendada=False` nos dois, e o segundo é
        # uma compra que só começa daqui a mais de um ano com a tela dizendo que
        # o acesso é imediato.
        inicio = max(agora, fim_cobertura) if grants_ativos else agora
        return _resultado(plano_novo, inicio, preco_novo_cents, 0,
                          agendada=inicio > agora)

    atual = _acesso_atual(vigentes)
    tier_atual = _tier_do_stored(atual["plan_stored"])

    # RECOMPRA QUE NÃO ACRESCENTARIA ACESSO — a invariante: uma compra tem de
    # acrescentar acesso. Os dois ramos de UPGRADE abaixo decidem
    # `access_starts_at` olhando só o grant VIGENTE (`atual`) e ignoram
    # `fim_cobertura`, que é `max(ends_at)` sobre TODOS os grants ativos,
    # justamente para incluir os FUTUROS. Medido com um grant Pix futuro já pago:
    #
    #   upgrade a partir do STRIPE   365d sobrepostos, 0 novos, R$ 499,00
    #   upgrade PIX -> PIX           365d sobrepostos, 0 novos, R$ 305,45
    #
    # Cobre QUALQUER grant `pix` futuro ainda vigente, de qualquer tier: limitar
    # a tier igual/maior deixava passar a mesma cobrança dupla um tier abaixo.
    # `source == "pix"` porque só esse dinheiro está na NOSSA conta — grant
    # `stripe`/`legacy` futuro não é crédito nosso a devolver (§9).
    #
    # Recusar (e não agendar, e não creditar) é decisão do dono, com o porquê e
    # a evolução separada no §7 do plano. Os casos, com os dois achados que os
    # produziram, em `tests/test_pix_recompra.py`.
    if tier_novo > tier_atual:
        pix_futuro_pago = [g for g in grants_ativos
                           if g.get("source") == "pix" and g["ends_at"] > agora
                           and g["starts_at"] > agora]
        cobre_o_tier = [g for g in grants_ativos
                        if _tier_do_stored(g["plan_stored"]) >= tier_novo
                        and g["ends_at"] > agora]
        bloqueiam = pix_futuro_pago or cobre_o_tier
        if bloqueiam:
            raise CoberturaJaPaga(plano_novo,
                                  max(g["ends_at"] for g in bloqueiam))

    if tier_novo > tier_atual and atual["source"] == "pix":
        credito = _credito_proporcional(atual, agora)
        # O mínimo do Asaas incide sobre o LÍQUIDO — a recusa medida no Sandbox
        # é literal: "O valor da cobrança menos o valor do desconto não pode ser
        # menor que R$ 5,00" (§7). Comparar o BRUTO seria comparar grandeza
        # errada, e nada na suíte teria acusado.
        if preco_novo_cents - credito < min_cents:
            # NÃO se cobra o mínimo: a compra vira AGENDADA, preço cheio,
            # crédito 0. Cobrar R$ 5,00 para dar um ano seria vender abaixo do
            # custo; e o crédito não some — ele continua sendo o acesso que a
            # pessoa já tem até o fim.
            return _resultado(plano_novo, fim_cobertura, preco_novo_cents, 0,
                              agendada=True)
        return _resultado(plano_novo, agora, preco_novo_cents, credito,
                          agendada=False)

    if tier_novo > tier_atual:
        # Upgrade a partir do STRIPE: preço cheio, crédito nenhum, e a vigência
        # começa no fim do período do cartão — que é o `ends_at` do grant que
        # sustenta o acesso agora.
        return _resultado(plano_novo, atual["ends_at"], preco_novo_cents, 0,
                          agendada=True)

    # Renovação (mesmo tier) e downgrade caem no mesmo lugar: preço cheio, sem
    # crédito, começando quando a cobertura atual acaba. O `max` com `agora` é o
    # §7 literal e cobre a cobertura que termina no passado por corrida de
    # relógio.
    return _resultado(plano_novo, max(agora, fim_cobertura), preco_novo_cents, 0,
                      agendada=fim_cobertura > agora)


def _acesso_atual(vigentes: list[dict]) -> dict:
    """O grant que DEFINE o acesso de agora, com desempate DETERMINÍSTICO.

    `max()` devolve o primeiro máximo, então sem desempate o resultado dependia
    da ORDEM DA LISTA. Medido: dois grants vigentes de mesmo tier, um `pix` e um
    `stripe`, davam `credit_cents` de 0 ou 19355 conforme a ordem em que o
    chamador os passasse — **R$ 193,55 de diferença no mesmo conjunto de
    entrada**. O chamador monta a lista com um `join`, e `join` sem `order by`
    não promete ordem nenhuma.

    A escada, em ordem:

      1. **maior tier** — é o que o cliente de fato tem hoje;
      2. **`source == 'pix'` ganha** no empate de tier. Não é arbitrário: o
         crédito monetário existe SÓ no caminho Pix → Pix (§7, §9), e o dinheiro
         do grant `pix` é o único que está na NOSSA conta. Escolher o `stripe`
         num empate negaria ao cliente um crédito que ele tem direito de receber;
      3. **maior `ends_at`** — mais tempo restante, mais crédito, e é o grant que
         realmente sustenta a cobertura;
      4. **maior `amount_cents`** e **maior `starts_at`** — o desempate final.

    **Todos os quatro critérios usam campos DO CONTRATO** (`source`,
    `plan_stored`, `starts_at`, `ends_at`, `amount_cents`), e isso é o conserto:
    a versão anterior desempatava por `external_ref`, que **não está no
    contrato** publicado na docstring pública. Dois grants `pix` de mesmo tier e
    mesmo `ends_at` passados na forma mínima não tinham desempate nenhum, e a
    ordem da lista decidia **R$ 123,29** — o mesmo defeito que o desempate
    existia para matar, um nível abaixo. O teste também não pegava: ele passava
    `external_ref` nos dois grants, então nunca exercitava a entrada mínima.

    Esgotados os quatro, os grants são indistinguíveis PELO CONTRATO — e aí o
    resultado é o mesmo qualquer que seja o escolhido, porque tudo que a função
    calcula sai desses campos. É o que torna a saída determinística de fato, e
    não só a escolha.
    """
    return max(vigentes, key=lambda g: (
        _tier_do_stored(g["plan_stored"]),
        g.get("source") == "pix",
        g["ends_at"],
        g.get("amount_cents") or 0,
        g["starts_at"],
    ))


def _credito_proporcional(grant: dict, agora: datetime) -> int:
    """`round(amount_cents × dias_restantes / DURACAO_DIAS)`, em centavos.

    Só é chamado para grant `pix`, e `_exigir_amount_cents` já garantiu que a
    coluna é inteiro não-negativo — o ramo "esqueceu o join → 0" saiu daqui de
    propósito (P1-1). `0` legítimo (cortesia, cobrança de valor zero) segue
    dando crédito zero, que é aritmética e não ausência de dado.

    O teto é o próprio `amount_cents`: um grant com janela maior que 365 dias —
    reparo manual, concessão do admin — não pode gerar crédito maior que o
    dinheiro que de fato entrou.
    """
    valor = grant["amount_cents"]
    if not valor:
        return 0
    restantes = (grant["ends_at"] - agora) / timedelta(days=1)
    if restantes <= 0:
        return 0
    credito = round(int(valor) * restantes / DURACAO_DIAS)
    return max(0, min(int(valor), credito))


def _resultado(plano: str, starts_at: datetime, price_cents: int,
               credit_cents: int, *, agendada: bool) -> dict:
    return {
        "plan_stored": plano,
        "access_starts_at": starts_at,
        "access_expires_at": starts_at + timedelta(days=DURACAO_DIAS),
        "price_cents": price_cents,
        "credit_cents": credit_cents,
        "amount_cents": price_cents - credit_cents,
        "agendada": agendada,
    }
