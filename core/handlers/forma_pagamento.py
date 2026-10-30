"""core/handlers/forma_pagamento.py — Q40: com banco conectado, lançamento
manual é só dinheiro vivo.

Fonte ÚNICA da regra (CLAUDE.md §0.7). Quem tem Open Finance conectado recebe
Pix, cartão, débito e transferência pelo extrato; lançar de novo à mão dobra o
gasto. Então, com OF:

- a forma declarada "dinheiro" grava na Carteira, como sempre;
- "banco" não grava — responde que chega pelo OF e mostra o que já chegou;
- sem forma declarada, o Piggy PERGUNTA antes de gravar (`payment_method_choice`).

Sem OF, tudo fica como antes (decisão A2 do dono): a Carteira continua sendo
"o dinheiro fora dos bancos conectados", e `decidir` devolve sempre CARTEIRA.

Ninguém mais consulta o OF para esta regra: o funil (`add_from_entities`), a
conta a pagar (`quitar`, o único chamador de `db.bills.mark_bill_paid`) e a
rota do painel passam por `decidir`.
"""
from __future__ import annotations

import logging
import re

import db
from core.handlers import declaracao_banco_textos
from core.response_formatter import wrap_wa_markup
from utils_text import fmt_brl, normalize_text

# forma DECLARADA (o que o usuário disse)
DINHEIRO, BANCO, MISTO, DESCONHECIDA = "dinheiro", "banco", "misto", "desconhecida"
FORMAS = frozenset({DINHEIRO, BANCO, MISTO, DESCONHECIDA})
# DECISÃO (o que o sistema faz). BANCO e MISTO também são decisões.
CARTEIRA, PERGUNTA = "carteira", "pergunta"

# Sobre o texto NORMALIZADO (minúsculo, sem acento, sem pontuação). Só marcador
# de COMO pagou: "boleto" e "doc" dizem O QUE foi pago (boleto se paga em
# dinheiro na lotérica; "doc do carro"; o DOC bancário acabou em 2024).
# A lista de formas é UMA só (`_FORTE`/`_FRACA`): a detecção e a limpeza do alvo (`_EXPRESSAO_BANCO_RE`) saem dela.
_FORTE = r"pix|d[eé]bito|transfer[eê]ncias?|ted"          # "no pix" é forma em qualquer posição
_FRACA = r"cart[aã]o|cr[eé]dito"                          # só no fim da oração ou antes de outra preposição
_BANCO_RE = re.compile(rf"\b(?:{_FORTE}|{_FRACA}|pixei|debitei|transferi|no banco|pelo banco)\b")
# Sozinhos, "banco", "dinheiro", "espécie" e "vivo" só valem como RESPOSTA à
# pergunta: numa frase eles são ambíguos ("banco da praça", "o dinheiro do
# freela", "uma espécie de taxa", "show ao vivo"). Na frase, o dinheiro é a
# expressão abaixo.
_BANCO_RESPOSTA_RE = re.compile(r"\bbanco\b")
_DINHEIRO_RESPOSTA_RE = re.compile(r"\b(dinheiro|especie|cash|vivo)\b")
# A expressão de dinheiro: o que a frase declara e o que sai antes do parse,
# senão o alvo vira "mercado em dinheiro" e a categoria muda. "dinheiro" colado
# no valor ("50 dinheiro", "50 reais dinheiro") também é forma.
_EXPRESSAO_DINHEIRO_RE = re.compile(
    r"\s*\b(?:(?:em|no|de|com)\s+(?:dinheiro(?:\s+vivo)?|esp[eé]cie|cash)|dinheiro\s+vivo)\b"
    r"|(?:(?<=\d)|(?<=\breal)|(?<=\breais))\s+dinheiro\b",
    re.IGNORECASE)
# A expressão de banco (preposição + forma), que sai do alvo da DECLARAÇÃO pelo banco (ADR 0006): o comércio
# casa com o extrato por "mercado", não por "mercado pelo banco". Forma fraca ("cartão", "banco") só sai no fim da
# oração ou antes de outra preposição: "Pixar", "no Banco Inter", "banco de dados" e "cartão de visita" ficam.
# ponytail: "pelo Nubank", "pela conta" e "app do banco" não são forma (decisão A1, ADR 0006).
_PREP = r"(?:em|no|na|pelo|pela|via|por|com|usando|utilizando)\s+(?:(?:o|a)\s+)?"
# Palavras de enchimento (lista FECHADA; "hoje"/"ontem" são data, `extract_date_from_text` já as tira). Saem do FIM do
# alvo da declaração, para "no pix mesmo" / "no mercado tbm" casarem com o extrato.
_ENCHIMENTO = r"mesmo|tbm|tb|tamb[eé]m|aqui"
_RESTO = rf"(?:\s+(?:de\s+(?:cr[eé]dito|d[eé]bito)|banc[aá]ria|autom[aá]tico|{_ENCHIMENTO}))*"
_ENCHIMENTO_NO_FIM_RE = re.compile(rf"(?:\s+(?:{_ENCHIMENTO}))+\b(?=\s*[.,;!?]*$)", re.IGNORECASE)
_FIM = r"(?=\s*(?:[.,;!?]|$)|\s+(?:no|na|em|num|numa|pra|para|e)\b)"
_EXPRESSAO_BANCO_RE = re.compile(
    rf"\s*\b(?:(?:{_PREP}|de\s+)(?:{_FORTE})\b{_RESTO}"
    rf"|(?:{_PREP}(?:{_FRACA})|(?:no|pelo|via)\s+banco|(?:{_FORTE}|{_FRACA}))\b{_RESTO}{_FIM})",
    re.IGNORECASE)
# Negação em qualquer lugar ("não foi em dinheiro", "pix não", "nem pix", "sem
# ser no pix"): o texto não decide a forma. Com banco o Piggy pergunta (ou
# repete a pergunta); sem banco `decidir` é Carteira de qualquer jeito. "não"
# sozinho cancela: `resolver` confere `_CANCELA` antes de `e_resposta`.
# ponytail: "sem desconto no pix" também pergunta; separar a negação que fala
# da forma da que não fala é o parser que já errou três vezes.
NEGACAO_RE = re.compile(r"\b(nao|nem|nunca|sem)\b")
_CANCELA = {"cancelar", "cancela", "nao"}
# O que pode cercar o marcador numa RESPOSTA ("foi no pix", "paguei no pix
# mesmo", "cartão de crédito", "app do banco" — a pergunta da conta cita o app).
# Lista fechada: qualquer outra palavra faz a frase ser assunto novo.
_LIGACAO = frozenset(
    "foi no na em pelo pela de do da com o a mesmo via sim e eu tudo app "
    "paguei pagou pago recebi recebeu caiu".split())
logger = logging.getLogger(__name__)


def regra_ativa(user_id: int) -> bool:
    """Consulta falhou → True: como banco conectado, nada vai para a Carteira
    sem o usuário dizer "dinheiro" — no pior caso, uma pergunta a mais ou um
    "não registrei" visível. A conta paga pelo banco confere de novo (`quitar`)."""
    try:
        return db.has_open_finance_connections(user_id)
    except Exception:
        logger.warning("regra da forma falhou pro user %s — tratando como banco "
                       "conectado", user_id, exc_info=True)
        return True


def decidir(user_id: int, declarada: str) -> str:
    """CARTEIRA | BANCO | MISTO | PERGUNTA. Dinheiro não consulta o banco."""
    if declarada not in FORMAS:
        raise ValueError("FORMA_PAGAMENTO_INVALIDA")
    if declarada == DINHEIRO or not regra_ativa(user_id):
        return CARTEIRA
    return {BANCO: BANCO, MISTO: MISTO}.get(declarada, PERGUNTA)


def _formas(norm: str, resposta: bool) -> tuple[bool, bool]:
    if NEGACAO_RE.search(norm):
        return False, False
    banco = bool(_BANCO_RE.search(norm) or (resposta and _BANCO_RESPOSTA_RE.search(norm)))
    dinheiro = bool((_DINHEIRO_RESPOSTA_RE if resposta else _EXPRESSAO_DINHEIRO_RE).search(norm))
    return banco, dinheiro


def detectar(texto: str) -> str:
    banco, dinheiro = _formas(normalize_text(texto or ""), resposta=False)
    if banco and dinheiro:
        return MISTO
    return BANCO if banco else DINHEIRO if dinheiro else DESCONHECIDA


def limpar(texto: str, banco: bool = False) -> str:
    """Tira a expressão de dinheiro (e, com `banco`, a de banco) do texto antes do parse."""
    texto = _EXPRESSAO_DINHEIRO_RE.sub("", texto or "")
    if banco:
        texto = _ENCHIMENTO_NO_FIM_RE.sub("", _EXPRESSAO_BANCO_RE.sub("", texto))
    return re.sub(r"\s{2,}", " ", texto).strip()


def e_resposta(texto: str) -> str | None:
    """A forma, se `texto` é uma RESPOSTA à pergunta; senão None. Resposta é
    frase que, sem os marcadores de forma, só tem palavra de `_LIGACAO`: "me
    fala meu saldo em dinheiro" e "gastei 30 no uber no pix" são assunto novo."""
    norm = normalize_text(texto or "")
    resto = norm
    for rx in (_BANCO_RE, _BANCO_RESPOSTA_RE, _DINHEIRO_RESPOSTA_RE):
        resto = rx.sub(" ", resto)
    if not norm or not set(resto.split()) <= _LIGACAO:
        return None
    banco, dinheiro = _formas(norm, resposta=True)
    if banco == dinheiro:
        return None
    return BANCO if banco else DINHEIRO


def _no_cartao(resposta: str, debito: bool = False) -> bool:
    """"cartão", "crédito", "cartão de crédito" — não "cartão de débito" (com `debito`, também “cartão de débito”: a fila (`resolve_multi_launch_value`), a resposta à pergunta de forma (`resolver`) e o pedaço sem marca no `rotear_partes`, rodadas 10 e 11)."""
    palavras = normalize_text(resposta or "").split()
    return bool({"cartao", "credito"} & set(palavras)) and (debito or "debito" not in palavras)


# ── textos ──────────────────────────────────────────────────────────────────

def pergunta_lancamento(tipo: str | None, valor: float | None) -> str:
    if tipo == "receita":
        quanto = f"Esses {fmt_brl(float(valor))} chegaram" if valor else "Chegou"
        return f"🐷 {quanto} em dinheiro vivo ou pelo banco (Pix, transferência)?"
    quanto = f"Esse {fmt_brl(float(valor))} foi" if valor else "Foi"
    return f"🐷 {quanto} em dinheiro vivo ou passou pelo banco (Pix, cartão, débito)?"


def pergunta_conta(nome: str) -> str:
    return f"🐷 Pagou a {wrap_wa_markup(nome)} pelo banco (Pix, débito, app do banco) ou em dinheiro vivo?"


def msg_banco(user_id: int, tipo: str | None, valor: float | None, cartao: bool = False) -> str:
    texto = ("🐷 Não registrei: com banco conectado, Pix, cartão e débito chegam "
             "sozinhos pelo Open Finance.")
    from core.handlers.declaracao_banco import ligada
    if cartao and ligada(user_id):  # com a flag, Pix e débito deixam de "chegar sozinhos": só o cartão (T8)
        texto = declaracao_banco_textos.CARTAO
    if not valor or float(valor) <= 0:
        return texto
    achados = buscar_no_extrato(user_id, tipo or "despesa", float(valor))
    if not achados:
        return texto + "\n\nAinda não apareceu no extrato; quando o banco sincronizar, aparece."
    linhas = [f"{r['dia']:%d/%m} · {r['alvo'] or 'sem descrição'} · {fmt_brl(float(r['valor']))}"
              for r in achados]
    return texto + "\n\nJá está no extrato:\n" + "\n".join(linhas)


def msg_misto() -> str:
    return ("🐷 Mandou dinheiro e banco na mesma mensagem. Manda o que foi em "
            "dinheiro separado; o que passou pelo banco chega pelo Open Finance.")


def msg_ocupado(user_id: int) -> str:
    """O claim perdeu para outra pergunta viva. Nada foi gravado."""
    pend = db.get_pending_action(user_id) or {}
    viva = (pend.get("payload") or {}).get("question")
    espera = f': "{viva}"' if viva else ""
    return (f"🐷 Antes tem outra pergunta minha esperando{espera}. Responde ela e "
            "me manda de novo — não registrei nada.")


def buscar_no_extrato(user_id: int, tipo: str, valor: float) -> list[dict]:
    from db.open_finance import buscar_no_extrato as _busca
    return _busca(user_id, tipo, valor)


# ── a pergunta ──────────────────────────────────────────────────────────────

def perguntar(user_id: int, payload: dict, texto: str) -> str:
    """Arma `payment_method_choice` por claim (nunca `set_pending_action`)."""
    if not db.claim_pending_action(user_id, "payment_method_choice",
                                   {**payload, "question": texto}):
        return msg_ocupado(user_id)
    return texto


def perguntar_conta(user_id: int, bill: dict, amount: float | None) -> str:
    nome = bill.get("name") or "conta"
    return perguntar(user_id, {"fluxo": "conta", "bill_id": int(bill["id"]),
                               "bill_name": nome, "amount": amount},
                     pergunta_conta(nome))


def quitar(user_id: int, bill_id: int, amount: float | None, declarada: str):
    """ÚNICO chamador de produção de `db.bills.mark_bill_paid` (teste AST).
    `("pergunta", None)` quando falta a forma; senão `("paga", conta|None)`."""
    d = decidir(user_id, declarada)
    if d not in (CARTEIRA, BANCO):
        return PERGUNTA, None
    # BANCO marca paga SEM débito: exige a consulta de verdade, não o True de
    # `regra_ativa` em erro (sem banco, a conta sairia paga e a Carteira intacta).
    if d == BANCO and not db.has_open_finance_connections(user_id):
        d = CARTEIRA
    # Import local: os testes trocam `db.bills.mark_bill_paid` por monkeypatch.
    from db.bills import mark_bill_paid
    return "paga", mark_bill_paid(user_id, int(bill_id), amount, metodo=d)


def resolver(user_id: int, text: str, pending: dict) -> str | None:
    """Resposta a `payment_method_choice`. None = não era resposta; o `route()` decide."""
    payload = pending.get("payload") or {}
    if normalize_text(text) in _CANCELA:
        db.consume_pending_action(user_id, pending)
        return "Ok, não registrei nada."
    forma = e_resposta(text)
    if forma is None or not db.consume_pending_action(user_id, pending):
        return None

    fluxo = payload.get("fluxo")
    # "cartão"/"crédito" (Q2b): cada lançamento refaz a frase direta "… no
    # cartão", e o `add()` decide — cartão manual vai para a fatura; sem ele, o
    # OF traz. Com só a forma BANCO, o cartão manual ficaria sem a compra.
    no_cartao = forma == BANCO and _no_cartao(text)
    # Rodada 10 (#1310): "cartão de débito" o `e_resposta` lê como débito, mas o cartão vence: T8, sem declarar nem ir
    # para a fatura (com a flag; sem ela, como antes). Só a resposta: a frase digitada segue no `_no_cartao` de sempre.
    from core.handlers.declaracao_banco import declara
    so_t8 = forma == BANCO and not no_cartao and _no_cartao(text, debito=True) and declara(user_id, decidir(user_id, BANCO))
    if fluxo == "texto":
        # Sem `restore_pending_on_error`: o multi-lançamento grava item a item,
        # e devolver a pergunta depois de um item gravado gravaria em dobro.
        from core.handlers import launches as h_launches
        texto, plat = payload.get("text") or "", payload.get("platform") or "whatsapp"
        if no_cartao or so_t8:
            # Pedaço a pedaço, fora do multi. O pulo fica: "recebi mais que 5 mil?"
            # não é compra, a guarda do crédito (#568) não o vê; o predicado é o mesmo.
            from core.intent_classifier import is_comparative_question
            partes = h_launches.split_financial_transactions(texto) or [texto]
            # Opção A (rodada 9): o "débito" do pedaço tira o cartão do D8 (`_no_cartao`) e o pedaço declararia; a resposta
            # "cartão" vence e ele recebe T8, sem declarar nem ir para a fatura.
            t8 = declara(user_id, decidir(user_id, BANCO))
            return "\n\n".join(
                [declaracao_banco_textos.CARTAO if t8 and (so_t8 or not _no_cartao(f"{p} no cartão"))
                 else h_launches.add(user_id, f"{p} no cartão", {}, plat, forma_pagamento=BANCO)
                 for p in partes if not is_comparative_question(p)]
                + [h_launches.aviso_pergunta_pulada(p)
                   for p in partes if is_comparative_question(p)])
        return h_launches.add(user_id, texto, payload.get("entities") or {}, plat,
                              forma_pagamento=forma)
    if fluxo == "audio":
        # Os pedaços do áudio, cada um no seu fluxo, agora com a forma. Sem
        # `restore_pending_on_error` pelo mesmo motivo do fluxo "texto".
        from core.handle_incoming import rotear_partes
        from core.types import IncomingMessage
        plat = payload.get("platform") or "whatsapp"
        partes, resultados = payload.get("partes") or [], None
        if no_cartao or so_t8:
            # Só o gasto/receita (com valor ou sem: `r` None) ganha o "no cartão"
            # (a classificação é a da pergunta, `handle_incoming`); o resto segue
            # como na resposta "pix". O `rotear_partes` lê a marca: nada declara.
            from core.intent_classifier import classify
            from parsers import describe_valueless_launch
            resultados = [None if describe_valueless_launch(p) else classify(p, user_id=user_id)
                          for p in partes]
            partes = [f"{p} no cartão" if no_cartao and (r is None or r.intent == "launches.add") else p
                      for p, r in zip(partes, resultados)]  # sem a marca (`so_t8`), o `rotear_partes` dá T8
        corpo, _ = rotear_partes(user_id, partes,
                                 IncomingMessage(platform=plat, user_id=user_id, text=""),
                                 plat, forma, resultados, resposta_cartao=no_cartao or so_t8)
        return corpo
    if fluxo == "entities":
        # Recibo (OCR) e tool da IA: sem o texto do usuário, o leitor estrito (`declaracao_valor`) não roda. Quem confere
        # o valor é a própria pergunta ("Esse R$ X foi…"), e no recibo também o "sim" da prévia (ADR 0006, DP-10).
        ents = dict(payload.get("entities") or {})
        if forma == BANCO and (no_cartao or so_t8 or not declara(user_id, BANCO)):  # cartão nunca declara
            return msg_banco(user_id, ents.get("tipo"), ents.get("valor"), cartao=no_cartao or so_t8)
        from datetime import datetime
        from core.handlers.launches import add_from_entities
        if ents.get("criado_em"):
            ents["criado_em"] = datetime.fromisoformat(ents["criado_em"])
        with db.restore_pending_on_error(user_id, pending):
            resp = add_from_entities(user_id, **ents, forma_pagamento=forma,
                                     platform=payload.get("platform") or "whatsapp")
        return resp if forma == BANCO else f"✅ Lançamento registrado!\n{resp}"  # a declaração traz o próprio cabeçalho
    if fluxo == "conta":
        return _resolver_conta(user_id, payload, forma, pending)
    return None


def _resolver_conta(user_id: int, payload: dict, forma: str, pending: dict) -> str:
    from core.handlers.bills import conta_paga
    from db.bills import get_bill

    bill_id, amount = int(payload["bill_id"]), payload.get("amount")
    bill = get_bill(user_id, bill_id)
    if not bill or bill.get("status") == "paid":
        return "Essa conta não está mais pendente."
    if forma == DINHEIRO and bill.get("variable_amount") and amount is None:
        nome = bill.get("name") or payload.get("bill_name") or "conta"
        if not db.claim_pending_action(user_id, "bill_amount_expected", {
                "bill_id": bill_id, "bill_name": nome, "forma_pagamento": DINHEIRO}):
            return msg_ocupado(user_id)
        return (f"A conta de *{nome}* tem valor variável. Quanto veio este mês?\n"
                "Pode mandar só o valor, por exemplo: *132,50*")
    with db.restore_pending_on_error(user_id, pending):
        _, paid = quitar(user_id, bill_id, amount, forma)
    if paid is None:
        return "Essa conta não está mais pendente."
    return conta_paga(user_id, paid, paid.get("paid_amount") or paid.get("amount") or 0)
