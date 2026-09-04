"""
core/services/funding.py — de onde sai o dinheiro de uma movimentação.

O problema que isto resolve: `accounts.balance` (a **Carteira**) fazia dois papéis —
era o saldo que o Pig mantém E o teste de "você tem dinheiro para isso?". Quando o
usuário conecta um banco por Open Finance, o produto pede para zerar a Carteira, senão
o mesmo dinheiro conta duas vezes (ver `dashboard.js`, "Ajustar carteira"). O saldo real
passa a vir do sync — mas o aporte, o resgate e as caixinhas continuavam exigindo
cobertura na Carteira, que agora é zero. Daí o "Saldo insuficiente na conta" com
R$ 1.387,76 na tela.

Aqui a origem vira explícita:

    carteira  → debita `accounts.balance`, exige cobertura nela
    bank      → não toca em `accounts.balance`; quem reflete a saída é o sync do banco

A regra de escolha vive só neste módulo. As cinco superfícies que movimentam dinheiro
(bot, chat da IA, Discord e as duas rotas do dashboard) chamam daqui — se cada uma
reimplementasse, elas divergiriam e o bot recusaria um lançamento que a tela aceita.
"""
from __future__ import annotations

from decimal import Decimal

import db

CARTEIRA = "carteira"
BANK = "bank"


def _dec(v) -> Decimal:
    return Decimal(str(v or 0))


def list_sources(user_id: int) -> list[dict]:
    """Fontes possíveis, Carteira primeiro, cada uma com saldo e rótulo.

    A Carteira sempre aparece (mesmo zerada) — é ela que sustenta o caminho de quem
    não tem Open Finance, e mostrá-la com R$ 0,00 é justamente o que explica o erro
    para quem tem banco conectado.
    """
    cb = db.get_consolidated_balance(user_id)
    fontes = [{
        "kind": CARTEIRA,
        "of_account_id": None,
        "label": "Carteira",
        "balance": _dec(cb.get("manual")),
        "espelho": _dec(cb.get("manual")),
        "comprometido": Decimal("0"),
    }]
    # O saldo do banco é espelho: o Pig não escreve nele, então um lançamento com
    # origem `bank` não o reduz. Sem descontar o que já foi comprometido desde o
    # último sync, o MESMO saldo autorizaria lançamentos infinitos.
    pendentes = db.pending_bank_outflows(user_id)
    for conta in db.list_bank_accounts(user_id):
        conta_id = int(conta["id"])
        espelho = _dec(conta.get("balance"))
        comprometido = _dec(pendentes.get(conta_id))
        fontes.append({
            "kind": BANK,
            "of_account_id": conta_id,
            "label": conta["label"],
            # `balance` é o DISPONÍVEL — é ele que decide. `espelho` é o que o banco
            # mandou no último sync, e é o número que aparece na tela.
            "balance": max(espelho - comprometido, Decimal("0")),
            "espelho": espelho,
            "comprometido": comprometido,
        })
    return fontes


def resolve(user_id: int, amount) -> dict:
    """Decide a origem de uma saída de `amount`.

    Devolve exatamente uma destas formas:

        {"source": {...}}        → resolvido, siga
        {"ask": [fonte, ...]}    → mais de uma fonte cobre; o canal decide como perguntar
        {"insufficient": {...}}  → nenhuma cobre; use `msg_insuficiente`

    Uma fonte só que cobre nunca vira pergunta: para quem tem a Carteira zerada — o
    caso do relato — o fluxo segue sem round-trip nenhum.
    """
    v = _dec(amount)
    fontes = list_sources(user_id)
    cobrem = [f for f in fontes if f["balance"] >= v]

    if len(cobrem) == 1:
        return {"source": cobrem[0]}
    if len(cobrem) > 1:
        return {"ask": cobrem}
    return {"insufficient": {"amount": v, "sources": fontes}}


def resolve_deterministic(user_id: int, amount) -> dict:
    """Igual ao `resolve`, mas sem pergunta — para canais que não conversam.

    Dashboard, Discord e chat da IA não têm como armar uma pendência e esperar resposta.
    Preferir a Carteira quando ela cobre preserva o comportamento histórico desses canais;
    só quando ela não cobre é que o banco entra.

    SEMPRE devolve `{"source": ...}`, nunca `insufficient`. Quando nada cobre, cai na
    Carteira de propósito: aí o `db` levanta INSUFFICIENT_ACCOUNT e cada canal responde
    no formato dele (400 no dashboard, mensagem no chat). Devolver `insufficient` aqui
    fazia os chamadores estourarem `KeyError: 'source'` — vira 500 em vez de 400.
    """
    r = resolve(user_id, amount)
    if "source" in r:
        return r
    candidatas = r.get("ask") or r.get("insufficient", {}).get("sources") or []
    carteira = next((f for f in candidatas if f["kind"] == CARTEIRA), None)
    return {"source": carteira or (candidatas[0] if candidatas else
                                   {"kind": CARTEIRA, "of_account_id": None,
                                    "label": "Carteira", "balance": Decimal("0")})}


def resolve_destination(user_id: int, *, origem_de: tuple[str, str] | None = None) -> dict:
    """Para onde volta o dinheiro de um resgate/saque. Sempre `{"source": {...}}`.

    `origem_de` é `(tipo_do_lancamento_de_deposito, alvo)` — ex.:
    `("deposito_caixinha", "viagem")`. Quando vem, a pergunta é de ESTADO: **todos os
    lotes ABERTOS deste alvo vieram da mesma origem?** Se sim, o dinheiro volta para
    ELA. É o conserto da #282: sem isso o destino era decidido do zero e qualquer
    banco conectado vencia a Carteira, então quem depositou da Carteira nunca recebia
    de volta — medido em produção, R$ 300 saíram e R$ 0,00 voltaram.

    Lote aberto, e não histórico de depósito, porque a versão por histórico não
    consertava o caso comum: depositou do banco e sacou tudo, depois depositou da
    Carteira — o alvo é 100% dinheiro da Carteira, mas a HISTÓRIA tem duas origens e
    o saque voltava pro banco. Por lote a resposta é exata, e a mistura se desfaz
    sozinha quando o lote fecha (ver `db.open_lot_origins`).

    Em TODO o resto cai na regra de sempre (banco primeiro, senão Carteira): sem
    `origem_de`, sem lote aberto, **lotes abertos de origens diferentes**, lote órfão
    (sem lançamento criador, então sem origem conhecida), ou banco de origem
    desconectado desde então. A degradação é sempre para o comportamento de hoje.

    **Lotes abertos de origens diferentes mantêm o banco de propósito** (decisão do
    dono): dividir o resgate proporcionalmente ou por LIFO é escolha de produto ainda
    não tomada, e adivinhar aqui seria pior que o comportamento conhecido.

    Não pergunta, de propósito: no destino a escolha não muda o razão. Com qualquer
    banco conectado o `delta_conta` é 0 igual (o dinheiro volta pro banco e o sync
    reflete), então entre dois bancos só mudaria o rótulo da mensagem — não vale um
    round-trip. Sem banco conectado, volta para a Carteira, como sempre foi.
    """
    fontes = list_sources(user_id)
    if origem_de:
        origens = db.open_lot_origins(user_id, *origem_de)
        if len(origens) == 1 and not origens[0]["orfao"]:
            o = origens[0]
            # `of_account_id` vem em TEXTO do jsonb; a fonte tem int. Comparar sem
            # converter faz TODO banco parecer diferente e o conserto nunca valer.
            igual = next((f for f in fontes if f["kind"] == o["kind"]
                          and (None if f["of_account_id"] is None
                               else str(f["of_account_id"])) == o["of_account_id"]), None)
            if igual:
                return {"source": igual}
    bancos = [f for f in fontes if f["kind"] == BANK]
    return {"source": bancos[0] if bancos else fontes[0]}


def to_db_arg(source: dict | None) -> dict | None:
    """Converte a fonte no argumento `funding_source` das funções de `db/`.

    `None` (ou Carteira) mantém o comportamento antigo: debita a Carteira. Só a origem
    `bank` muda o que é gravado.
    """
    if not source or source.get("kind") != BANK:
        return None
    return {
        "kind": BANK,
        "of_account_id": source.get("of_account_id"),
        "label": source.get("label"),
    }


def origem_txt(source: dict | None) -> str:
    """Sufixo para a mensagem de sucesso: ", saindo do Nubank · Conta".

    Vazio quando a origem é a Carteira e não há banco conectado — sem nada a
    desambiguar, dizer a origem só polui a resposta.
    """
    if not source or source.get("kind") != BANK:
        return ""
    return f", saindo do {source.get('label') or 'banco conectado'}"


def nota_sync(saida: bool = True) -> str:
    """Aviso obrigatório quando a origem é o banco.

    O Pig anota só metade da movimentação: o lado do investimento/caixinha entra na
    hora, o lado do dinheiro só quando o banco sincronizar. Nesse intervalo o patrimônio
    fica alto (saída) ou baixo (entrada) pelo valor da operação — e sem aviso o usuário
    vê o número errado sem entender por quê.

    A segunda frase não é enfeite: a transação do banco entra como lançamento próprio e
    a reconciliação ainda não a funde com o aporte (`_find_manual_candidates` filtra por
    `tipo` e `is_internal_movement = false`, e o aporte falha nos dois). Enquanto isso
    não mudar, a segunda linha aparece — é mais honesto avisar do que deixar descobrir.
    """
    movimento = "saída" if saida else "entrada"
    return (
        f"🔄 Anotei só o lado do investimento. A {movimento} do dinheiro aparece "
        "quando o Open Finance sincronizar — até lá o saldo do banco fica como está, "
        "e a transação pode surgir como uma segunda linha no extrato."
    )


def msg_insuficiente(user_id: int, amount, acao: str = "aporte", sources: list | None = None) -> str:
    """"Saldo insuficiente na conta" era vago, e foi o que enganou: o usuário via
    R$ 1.387,76 na tela e o bot dizia que não tinha saldo. Agora a resposta nomeia
    cada saldo e mostra o número."""
    from utils_text import fmt_brl

    v = _dec(amount)
    fontes = sources if sources is not None else list_sources(user_id)
    bancos = [f for f in fontes if f["kind"] == BANK]
    carteira = next((f for f in fontes if f["kind"] == CARTEIRA), None)
    saldo_carteira = carteira["balance"] if carteira else Decimal("0")

    if not bancos:
        return (
            f"Saldo insuficiente: você tem {fmt_brl(float(saldo_carteira))} na conta "
            f"e o {acao} é de {fmt_brl(float(v))}."
        )

    linhas = [f"• **Carteira**: {fmt_brl(float(saldo_carteira))}"]
    tem_comprometido = False
    for b in bancos:
        comprometido = b.get("comprometido") or Decimal("0")
        if comprometido > 0:
            tem_comprometido = True
            linhas.append(
                f"• **{b['label']}**: {fmt_brl(float(b['balance']))} disponíveis "
                f"({fmt_brl(float(b.get('espelho') or 0))} no banco, "
                f"{fmt_brl(float(comprometido))} já lançados aqui e ainda não sincronizados)"
            )
        else:
            linhas.append(f"• **{b['label']}**: {fmt_brl(float(b['balance']))}")

    rodape = (
        "\n\nA **Carteira** é o dinheiro fora dos bancos conectados (espécie e contas "
        "que você não ligou) — por isso ela costuma ficar zerada depois que você conecta "
        "um banco."
    )
    if tem_comprometido:
        rodape += (
            "\n\nO valor já lançado sai do disponível até o banco sincronizar, para você "
            "não comprometer o mesmo dinheiro duas vezes."
        )
    return (
        f"Nenhum dos seus saldos cobre {fmt_brl(float(v))} de {acao}:\n\n"
        + "\n".join(linhas)
        + rodape
    )
