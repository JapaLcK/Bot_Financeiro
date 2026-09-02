"""
Guarda de afirmações da saída da IA.

A PERGUNTA QUE ELA RESPONDE
  A resposta afirma algum NÚMERO que ninguém deu pro modelo?

  Todo valor e todo ID que a IA escreve tinha que vir de algum lugar: de uma
  tool que ela chamou, ou da própria mensagem do usuário. Número que não sai
  de nenhum dos dois o modelo inventou — é a classe de alucinação que mais
  dói num app de dinheiro, porque sai formatada com cara de extrato.

POR QUE CONTRA AS TOOLS, E NÃO CONTRA O BANCO
  Conferir contra o banco exigiria a guarda conhecer o schema, montar os
  agregados legítimos (total do mês, por categoria, saldo...) e ainda assim
  errar nos que não previu — e compra no crédito nem grava linha em
  `launches` (db/accounts.py:116), então metade dos cenários de cartão
  choraria lobo. O resultado das tools é a evidência que o modelo REALMENTE
  teve na mão: bate com ela e a guarda não precisa saber nada de schema,
  nem sair desatualizada quando uma tool nova entrar.

ASSIMETRIA DE PROPÓSITO
  A evidência é lida de forma PERMISSIVA (`1.234` conta como 1234 e como
  1,234 — a tool pode ter escrito em qualquer formato) e a afirmação de
  forma ESTRITA (o bot escreve em BRL). Errar pra "sustentado" é barato;
  errar pra "inventado" enche o relatório de lobo e a guarda deixa de ser
  lida.

O QUE ELA NÃO PEGA (teto conhecido)
  Conta feita pelo modelo: se as tools devolveram 100 e 50 e a resposta diz
  "R$ 150,00", 150 não está na evidência e sai como NÃO SUSTENTADO. Isso é
  de propósito — o system prompt manda a IA não fazer aritmética — mas
  significa que "não sustentado" quer dizer "confira", não "está errado".

  Também não julga TEXTO: "seu maior gasto foi no mercado" não tem número
  e passa batido. A guarda é sobre número.

NÃO ESTÁ LIGADA NO CAMINHO DE PRODUÇÃO. Hoje só o harness de QA a chama.
Ligar depois é uma linha em `_run_tool_loop`, passando os `history_content`
que o `_dispatch_tool` já devolve.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Só afirmação com R$ na frente conta como afirmação de dinheiro. Número solto
# na resposta é dia, quantidade, porcentagem — checar tudo daria lobo.
# O `-?` não é detalhe: "🏦 Saldo: R$ -50,00" é afirmação como qualquer
# outra, e sem ele saldo negativo — justo o que o usuário confere — passava
# batido. A comparação é em módulo: a evidência vem das tools em JSON, onde o
# sinal fica fora do literal que o `_ANY_NUM_RE` captura.
# O `[*_]?` também não é detalhe: o bot escreve "R$ *1.736,65*" (negrito do
# WhatsApp) e sem ele a guarda dava "nenhuma afirmação numérica" EXATAMENTE
# nas respostas que o modelo escreveu — medido contra saída real em
# 2026-09-01, 2 de 2 respostas de IA passavam batido.
_CLAIM_MONEY_RE = re.compile(r"R\$\s*[*_]?\s*(-?\d[\d.]*(?:,\d{2})?)")
_CLAIM_ID_RE = re.compile(r"#(\d{1,9})\b")
_CLAIM_CODE_RE = re.compile(r"\b(CC\d{1,9}|PC[0-9A-Fa-f]{8})\b")

# Na evidência, qualquer literal numérico serve — as tools devolvem JSON.
_ANY_NUM_RE = re.compile(r"\d[\d.,]*")


def _brl_to_cents(raw: str) -> int | None:
    """Como o BOT escreve: `1.234,56` → 123456. Ponto é milhar."""
    s = raw.strip().lstrip("-").rstrip(".,")
    if not s:
        return None
    try:
        if "," in s:
            return round(float(s.replace(".", "").replace(",", ".")) * 100)
        return round(float(s.replace(".", "")) * 100)
    except ValueError:
        return None


def _evidence_cents(raw: str) -> set[int]:
    """Como a TOOL pode ter escrito: aceita as duas leituras de `1.234`
    (1234 reais e 1,234) porque não dá pra saber, e errar pra permissivo
    aqui é o lado barato."""
    s = raw.strip().rstrip(".,")
    out: set[int] = set()
    if not s:
        return out
    for candidate in {s.replace(".", "").replace(",", "."),  # BR: 1.234,56
                      s.replace(",", "")}:                   # JSON: 1234.56
        try:
            out.add(round(float(candidate) * 100))
        except ValueError:
            pass
    return out


def tool_results(messages: list[dict]) -> list[str]:
    """Os resultados de tool de uma lista de mensagens — a evidência.

    Mora aqui pra ter UMA definição: o runner e o harness de QA precisam
    concordar sobre o que conta como evidência, e quando divergiram foi
    defeito (o wiring de produção lia o histórico inteiro enquanto o harness
    fatiava por turno). Quem chama é responsável por passar só as mensagens
    do turno."""
    return [m.get("content") or "" for m in (messages or []) if m.get("role") == "tool"]


def money_cents(text: str) -> set[int]:
    """Todo valor em R$ afirmado num texto, em centavos (módulo).

    Existe separado do `check()` porque serve a OUTRA pergunta: comparar duas
    respostas entre si — a do comando (determinística, é o gabarito) contra a
    da IA. Mesmo regex, mesma leitura estrita: se as duas divergirem, é porque
    uma delas está afirmando um valor que a outra não afirma."""
    out: set[int] = set()
    for m in _CLAIM_MONEY_RE.finditer(text or ""):
        v = _brl_to_cents(m.group(1))
        if v is not None:
            out.add(abs(v))
    return out


@dataclass
class Claim:
    kind: str          # "dinheiro" | "id" | "codigo"
    token: str         # como apareceu na resposta
    supported: bool
    detail: str = ""


def collect_evidence(tool_results: list[str], user_text: str = "") -> tuple[set[int], set[str]]:
    """(centavos vistos, tokens crus vistos) — do que as tools devolveram e
    do que o próprio usuário escreveu."""
    cents: set[int] = set()
    raw: set[str] = set()
    for chunk in list(tool_results) + [user_text or ""]:
        chunk = chunk or ""
        raw.update(m.group(0).upper() for m in _CLAIM_CODE_RE.finditer(chunk))
        for m in _ANY_NUM_RE.finditer(chunk):
            cents |= _evidence_cents(m.group(0))
    return cents, raw


def check(reply: str, tool_results: list[str], user_text: str = "") -> list[Claim]:
    """Toda afirmação numérica da resposta, marcada como sustentada ou não."""
    reply = reply or ""
    cents, raw_codes = collect_evidence(tool_results, user_text)
    # IDs da evidência: o inteiro cru, não centavos (tool escreve `"id": 3`).
    ev_ints = {c // 100 for c in cents if c % 100 == 0}

    # ponytail: match exato contra a evidência. Soma feita pelo modelo
    # ("100 e 50" → "R$ 150,00") sai como não sustentada. Se o relatório
    # começar a encher de derivados legítimos, o passo é subset-sum em
    # centavos sobre os valores da evidência — não afrouxar o match.
    claims: list[Claim] = []
    for m in _CLAIM_MONEY_RE.finditer(reply):
        val = _brl_to_cents(m.group(1))
        if val is None:
            continue
        val = abs(val)
        ok = val in cents
        claims.append(Claim("dinheiro", m.group(0), ok,
                            "" if ok else "valor não veio de nenhuma tool nem da mensagem"))
    for m in _CLAIM_ID_RE.finditer(reply):
        n = int(m.group(1))
        ok = n in ev_ints
        claims.append(Claim("id", m.group(0), ok,
                            "" if ok else "ID não aparece em nenhum resultado de tool"))
    for m in _CLAIM_CODE_RE.finditer(reply):
        tok = m.group(0).upper()
        ok = tok in raw_codes
        claims.append(Claim("codigo", m.group(0), ok,
                            "" if ok else "código não aparece em nenhum resultado de tool"))
    return claims


def verdict(claims: list[Claim]) -> str:
    """Uma linha pro relatório."""
    if not claims:
        return "🈳 nenhuma afirmação numérica"
    bad = [c for c in claims if not c.supported]
    if not bad:
        return f"✅ {len(claims)} afirmação(ões), todas sustentadas"
    return (f"🚨 {len(bad)} de {len(claims)} NÃO sustentada(s): "
            + ", ".join(f"{c.token} ({c.kind})" for c in bad))


if __name__ == "__main__":
    TOOLS = ['{"saldo": -50.0, "launches": [{"user_seq": 1, "valor": 50.0, '
             '"categoria": "mercado"}, {"user_seq": 2, "valor": 1234.56}]}']

    # positivo — o caminho legítimo continua passando
    ok = check("💸 Despesa de R$ 50,00 registrada. ID: #1", TOOLS)
    assert ok and all(c.supported for c in ok), ok
    assert verdict(ok).startswith("✅"), verdict(ok)

    # negativo — valor que não saiu de tool nenhuma
    bad = check("Você gastou R$ 230,00 na Amazon.", TOOLS)
    assert len(bad) == 1 and not bad[0].supported, bad
    assert verdict(bad).startswith("🚨"), verdict(bad)
    # e o controle que prova que a checagem discrimina: MESMA resposta, mesma
    # guarda, evidência que contém o valor → tem que virar sustentada.
    assert check("Você gastou R$ 230,00 na Amazon.", ['{"valor": 230.0}'])[0].supported

    # ID inventado
    inv = check("Apaguei o lançamento #47.", TOOLS)
    assert len(inv) == 1 and not inv[0].supported, inv

    # formato: o bot escreve 1.234,56; a tool escreveu 1234.56
    assert check("Total: R$ 1.234,56", TOOLS)[0].supported
    assert not check("Total: R$ 1.234,57", TOOLS)[0].supported, "1 centavo tem que reprovar"

    # valor da própria mensagem do usuário, sem tool nenhuma
    assert check("Anotei R$ 89,90.", [], user_text="paguei 89,90 na farmácia")[0].supported
    assert not check("Anotei R$ 89,90.", [])[0].supported

    # negrito do WhatsApp entre o R$ e o número
    neg = check("🐷 Sobrou R$ *1.234,56* pra você!", TOOLS)
    assert len(neg) == 1 and neg[0].supported, neg
    assert check("Sobrou R$ *99,99*", TOOLS)[0].supported is False

    # saldo negativo é afirmação também
    assert check("🏦 Saldo: R$ -50,00", TOOLS)[0].supported, "negativo tem que ser checado"
    assert not check("🏦 Saldo: R$ -77,00", TOOLS)[0].supported

    # tool_results: só as mensagens de tool, na ordem
    assert tool_results([{"role": "system", "content": "s"},
                         {"role": "tool", "content": "a"},
                         {"role": "user", "content": "u"},
                         {"role": "tool", "content": "b"}]) == ["a", "b"]
    assert tool_results([]) == [] and tool_results(None) == []

    # money_cents: mesma leitura, servindo à comparação entre duas respostas
    assert money_cents("Saldo: R$ 1.826,55 · Receita R$ 2.000,00") == {182655, 200000}
    assert money_cents("Sobrou R$ *1.826,55* pra você") == {182655}
    assert money_cents("nenhum número aqui") == set()
    assert money_cents("Saldo: R$ -50,00") == {5000}, "negativo entra em módulo"

    # resposta sem número não vira lobo
    assert verdict(check("Seu maior gasto foi no mercado.", TOOLS)).startswith("🈳")

    # código de compra no crédito
    assert check("Compra CC12 apagada.", ['{"codigo": "CC12"}'])[0].supported
    assert not check("Compra CC12 apagada.", ['{"codigo": "CC99"}'])[0].supported

    print("ai_guard: autoteste OK")
