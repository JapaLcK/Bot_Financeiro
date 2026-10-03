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

A regra de escolha vive só neste módulo. As superfícies que movimentam dinheiro (bot,
chat da IA, Discord e as duas rotas do dashboard) chamam daqui — se cada uma
reimplementasse, elas divergiriam e o bot recusaria um lançamento que a tela aceita.
A regra do DESTINO (para onde volta um saque/resgate) não mora aqui: ela é
`db.destination_of_lots`, dentro da transação do saque, sobre os lotes que o FIFO
consumiu de fato. Uma previsão de fora existiu em quatro versões e errou nas quatro —
ela lê os lotes antes do accrual e fora do lock. A quinta apagou a previsão: as duas
funções de saque devolvem o destino gravado, e a mensagem lê o fato.
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
        # Mesma conta da guarda (`wallet_guard_delta`): receita pendente não autoriza.
        "balance": _dec(cb.get("manual")) + _dec(cb["reconciliation"]["receita_back"]),
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
    from db.bank_movements import has_existing_bank_outflow
    cobrem = [f for f in fontes if f["balance"] >= v or
              (f["kind"] == BANK and has_existing_bank_outflow(user_id, f["of_account_id"], v))]

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
    """Declaração de fato passado; confirmação só existe com prova no extrato."""
    return (
        "🔎 Registrei sua declaração de movimentação no banco. "
        "Confira a confirmação pelo extrato na área de movimentações bancárias do dashboard. "
        "Enquanto houver movimentações não confirmadas, o patrimônio fica a conferir."
    )


def carteira_txt(exibida, disponivel) -> str:
    """A Carteira como a tela mostra, para mensagem de RECUSA. Quando a guarda
    autoriza menos que o exibido (receita pendente de reconciliação), mostra o
    disponível e o motivo À PARTE — senão a mesma conversa diz R$ 100 no /saldo e
    R$ 0 na recusa. A entrada a conferir não é "parte" do exibido: com gasto
    depois dela, ela é maior que ele (tela R$ 30, entrada R$ 100)."""
    from utils_text import fmt_brl

    txt = fmt_brl(float(_dec(exibida)))
    a_conferir = _dec(exibida) - _dec(disponivel)
    if a_conferir > 0:
        txt += (f" (disponível para pagar: {fmt_brl(float(_dec(disponivel)))}, porque"
                f" {fmt_brl(float(a_conferir))} de entrada ainda está a conferir com o banco)")
    return txt


def aviso_conferir(exibido, rec: dict | None, so_contagem: bool = False) -> str:
    """Aviso de pendência de reconciliação (Open Finance × lançamento manual) —
    fonte única do texto que aparece em /saldo, na resposta de lançamento, na
    IA e nos relatórios. `reconciliations.js` espelha isto em JS (CLAUDE.md
    §0.7 — a fixture `tests/fixtures/aviso_conferir.json` é lida pelos dois).

    "pode ser" é o exibido + `delta_se_confirmar`, que já vem com o sinal
    certo (db/open_finance.py): confirmar uma despesa pendente sobe o exibido
    (o dinheiro que "saiu" no banco ainda não saiu da Carteira), confirmar
    uma receita desce.

    `so_contagem`: só o número + " no PigBank.", sem o "pode ser" — para
    "paguei a conta", que não mostra saldo e onde o valor soaria como o saldo
    (core/handlers/bills.py).
    """
    n = int((rec or {}).get("pending_count") or 0)
    if n <= 0:
        return ""
    from utils_text import fmt_brl

    txt = f"⚠ {n} lançamento(s) a conferir"
    if so_contagem:
        return txt + " no PigBank."
    delta = _dec(rec.get("delta_se_confirmar"))
    if delta != 0:
        txt += f" · pode ser {fmt_brl(float(_dec(exibido) + delta))}"
    return txt


def msg_insuficiente(user_id: int, amount, acao: str = "aporte", sources: list | None = None,
                      plain: bool = False) -> str:
    """"Saldo insuficiente na conta" era vago, e foi o que enganou: o usuário via
    R$ 1.387,76 na tela e o bot dizia que não tinha saldo. Agora a resposta nomeia
    cada saldo e mostra o número.

    `plain=True` tira os `**` (negrito Markdown) — o dashboard mostra o texto
    cru, sem renderizar Markdown; o WhatsApp e a IA continuam com negrito."""
    from utils_text import fmt_brl

    v = _dec(amount)
    fontes = sources if sources is not None else list_sources(user_id)
    bancos = [f for f in fontes if f["kind"] == BANK]
    carteira = next((f for f in fontes if f["kind"] == CARTEIRA), None)
    saldo_carteira = (carteira_txt(carteira["espelho"], carteira["balance"])
                      if carteira else fmt_brl(0.0))

    if not bancos:
        msg = (
            f"Saldo insuficiente: você tem {saldo_carteira} na conta "
            f"e o {acao} é de {fmt_brl(float(v))}."
        )
        return msg.replace("**", "") if plain else msg

    linhas = [f"• **Carteira**: {saldo_carteira}"]
    tem_comprometido = False
    for b in bancos:
        comprometido = b.get("comprometido") or Decimal("0")
        if comprometido > 0:
            tem_comprometido = True
            linhas.append(
                f"• **{b['label']}**: {fmt_brl(float(b['balance']))} disponíveis "
                f"({fmt_brl(float(b.get('espelho') or 0))} no banco, "
                f"{fmt_brl(float(comprometido))} declarados aqui e ainda não confirmados)"
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
            "\n\nO valor declarado sai do disponível até ser confirmado no extrato, para você "
            "não comprometer o mesmo dinheiro duas vezes."
        )
    msg = (
        f"Nenhum dos seus saldos cobre {fmt_brl(float(v))} de {acao}:\n\n"
        + "\n".join(linhas)
        + rodape
    )
    return msg.replace("**", "") if plain else msg
