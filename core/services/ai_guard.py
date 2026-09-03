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

import json
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

# Na evidência, qualquer literal numérico serve como DINHEIRO — as tools
# devolvem JSON e o formato varia. O `-?` é obrigatório: sem ele a evidência
# ficava sem sinal, e comparar em módulo tornava `R$ 50,00` e `R$ -50,00`
# indistinguíveis contra `{"balance": -50.0}`. Um modelo que come o menos diz
# que você TEM cinquenta reais quando você DEVE cinquenta — e era justamente
# esse o evento que a guarda deixava de emitir.
_ANY_NUM_RE = re.compile(r"-?\d[\d.,]*")

# ID precisa de evidência PRÓPRIA, e ela sai da CHAVE do campo — não de um
# padrão numérico. Duas tentativas anteriores falharam pelo mesmo motivo,
# olhar só a forma do número:
#
#   1. derivar ID dos centavos (`5000 // 100 == 50`) fazia `"valor": 50.0`
#      sustentar um `#50` inventado;
#   2. "inteiro que não faz parte de um decimal" ainda engolia data —
#      `"start_date": "2026-09-01"` injetava 2026, 9 e 1, e um `#9` inventado
#      passava.
#
# Nos dois casos o efeito era SUPRIMIR evento: a guarda ficava silenciosa e
# parecia estar funcionando, que é o pior estado possível pra ela.
#
# E não basta a chave ser "de identificação": tem que ser o ID que o USUÁRIO
# VÊ. `list_recent_launches` devolve `id` (interno) E `user_seq` lado a lado
# (`tools/launches.py:59-60`), e o `#N` que o usuário digita é o user_seq —
# `db.resolve_user_seq_to_id` existe exatamente pra traduzir um no outro.
# Aceitar `id` fazia uma resposta com `#4242` passar por sustentada contra a
# linha `{"id": 4242, "user_seq": 1}`, sendo que #4242 não existe pro usuário.
#
# Medido em `core/services/ai_chat/tools/`: `user_seq` é a ÚNICA chave de ID
# visível ao usuário. `bill_id`, `card_id`, `group_id` e `id` são internos e
# nunca aparecem como `#N` — cartão e parcelamento têm sintaxe própria (CC/PC),
# tratada pelo `_CLAIM_CODE_RE`.
#
# O `(^|_)` continua exigido: sem ele, `"paid"` e `"valid"` entrariam.
_ID_KEY_RE = re.compile(r"(^|_)seq$")

# Antes de ler DINHEIRO, apaga os trechos que já são ID ou código. Sem isto,
# `apague #50` punha 5000 centavos na evidência e uma resposta afirmando
# `R$ 50,00` passava por sustentada só porque o usuário citou o ID #50 — a
# mesma mistura ID×dinheiro do outro sentido, que eu já tinha corrigido só
# metade.
_MASCARA_NAO_MONETARIA_RE = re.compile(r"#\d{1,9}|\b(?:CC\d{1,9}|PC[0-9A-Fa-f]{8})\b")

# Na mensagem do USUÁRIO, número também vem como data, contagem de parcela e
# ano — e virava dinheiro: "quais contas vencem dia 20?" sustentava um
# `R$ 20,00` inventado, "parcelei em 12 vezes" um `R$ 12,00`, "em 2026" um
# `R$ 2.026,00`. Pergunta de finanças com data é o caso comum, não a exceção.
#
# É HEURÍSTICA, e a lista é declaradamente incompleta: o que sobrar continua
# entrando como dinheiro, ou seja, o erro remanescente é de SUPRESSÃO.
#
# Por que não uma regra estrita ("só com R$ ou centavos"): ela quebraria o
# valor que o usuário diz solto — `guarda 100 na viagem` —, que é justamente
# o que impede a confirmação pendente de virar falso alarme. Testado nos dois
# sentidos logo abaixo.
#
# E por que não `utils_text.parse_money`, que já existe: medido em 2026-09-02,
# ela devolve 20.0 pra "dia 20", 12.0 pra "12 vezes" e 2026.0 pra "em 2026" —
# não faz esta distinção, e ainda por cima só devolve UM valor por mensagem.
_NAO_MONETARIO_DO_USUARIO_RE = re.compile(
    r"\bdias?\s+\d{1,2}\b"
    r"|\b\d{1,3}\s*(?:x|vezes|parcelas?|meses|m[eê]s)\b"
    r"|\b(?:em|de|desde|at[ée])\s+(?:19|20)\d{2}\b",
    re.IGNORECASE,
)


def _ids_from_json(obj, out: set[int]) -> None:
    """IDs de um resultado de tool já decodificado, só dos campos que SÃO id."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                _ids_from_json(v, out)
            elif _ID_KEY_RE.search(str(k)):
                try:
                    out.add(int(v))
                except (TypeError, ValueError):
                    pass
    elif isinstance(obj, list):
        for v in obj:
            _ids_from_json(v, out)


def _brl_to_cents(raw: str) -> int | None:
    """Como o BOT escreve: `1.234,56` → 123456. Ponto é milhar."""
    s = raw.strip().rstrip(".,")
    sinal = -1 if s.startswith("-") else 1
    s = s.lstrip("-")
    if not s:
        return None
    try:
        if "," in s:
            return sinal * round(float(s.replace(".", "").replace(",", ".")) * 100)
        return sinal * round(float(s.replace(".", "")) * 100)
    except ValueError:
        return None


def _evidence_cents(raw: str) -> set[int]:
    """Como a TOOL pode ter escrito: aceita as duas leituras de `1.234`
    (1234 reais e 1,234) porque não dá pra saber, e errar pra permissivo
    aqui é o lado barato."""
    s = raw.strip().rstrip(".,")
    out: set[int] = set()
    sinal = -1 if s.startswith("-") else 1
    s = s.lstrip("-")
    if not s:
        return out
    for candidate in {s.replace(".", "").replace(",", "."),  # BR: 1.234,56
                      s.replace(",", "")}:                   # JSON: 1234.56
        try:
            out.add(sinal * round(float(candidate) * 100))
        except ValueError:
            pass
    return out


def tool_results(messages: list[dict]) -> list[str]:
    """Os resultados de tool de uma lista de mensagens — a evidência.

    Mora aqui pra ter UMA definição: o runner e o harness de QA precisam
    concordar sobre o que conta como evidência, e quando divergiram foi
    defeito (o wiring de produção lia o histórico inteiro enquanto o harness
    fatiava por turno). Quem chama é responsável por passar só as mensagens
    do turno.

    DESCARTA o payload de confirmação pendente. Numa write que pede "sim/não",
    o `_dispatch_tool` devolve `{"status": "pending_user_confirmation",
    "summary": ..., "args": ...}` — e `args`/`summary` são o ECO dos argumentos
    que o próprio modelo escolheu. Aceitar isso como evidência tornava a
    invenção auto-validante: se o usuário não disse valor e o modelo chutou
    100, a confirmação dizendo "R$ 100,00" batia com o eco e não gerava evento.
    O payload não traz nenhum dado independente, então sai inteiro — e o valor
    que o usuário de fato disse continua contando, porque `check()` recebe
    `user_text` à parte."""
    out: list[str] = []
    for m in (messages or []):
        if m.get("role") != "tool":
            continue
        chunk = m.get("content") or ""
        try:
            if json.loads(chunk).get("status") == "pending_user_confirmation":
                continue
        except (ValueError, TypeError, AttributeError):
            pass
        out.append(chunk)
    return out


def money_cents(text: str) -> set[int]:
    """Todo valor em R$ afirmado num texto, em centavos (módulo).

    Existe separado do `check()` porque serve a OUTRA pergunta: comparar duas
    respostas entre si — a curta contra a solta. Mesmo regex, mesma leitura
    estrita: se as duas divergirem, uma delas afirma valor que a outra não.

    COM SINAL. Eu tinha corrigido a polaridade no `check()` e deixado este
    chamador em módulo — instância em vez de classe. Com `abs()`, duas
    respostas que diferem SÓ no sinal (`R$ -50,00` × `R$ 50,00`) davam conjuntos
    idênticos, `inventados` e `faltando` vazios, e o par saía ✅ escondendo
    justamente a inversão de fato."""
    out: set[int] = set()
    for m in _CLAIM_MONEY_RE.finditer(text or ""):
        v = _brl_to_cents(m.group(1))
        if v is not None:
            out.add(v)
    return out


@dataclass
class Claim:
    kind: str          # "dinheiro" | "id" | "codigo"
    token: str         # como apareceu na resposta
    supported: bool
    detail: str = ""


def collect_evidence(tool_results: list[str],
                     user_text: str = "") -> tuple[set[int], set[str], set[int]]:
    """(centavos, códigos crus, IDs) — do que as tools devolveram e do que o
    próprio usuário escreveu. Os três são COLHIDOS SEPARADAMENTE: um valor não
    pode sustentar um ID nem vice-versa."""
    cents: set[int] = set()
    raw: set[str] = set()
    ids: set[int] = set()
    for chunk, e_do_usuario in [(c, False) for c in tool_results] + [(user_text or "", True)]:
        chunk = chunk or ""
        raw.update(m.group(0).upper() for m in _CLAIM_CODE_RE.finditer(chunk))
        # Dinheiro sai do texto SEM os IDs e códigos; ID e código saem do texto
        # original, logo abaixo.
        limpo = _MASCARA_NAO_MONETARIA_RE.sub(" ", chunk)
        if e_do_usuario:
            limpo = _NAO_MONETARIO_DO_USUARIO_RE.sub(" ", limpo)
        for m in _ANY_NUM_RE.finditer(limpo):
            if e_do_usuario:
                # A leitura dupla existe porque a TOOL pode escrever em qualquer
                # formato. O usuário não: ele escreve BRL. Mandar `77,90` pela
                # via permissiva injetava 7.790 E 779.000 centavos, e uma
                # resposta afirmando `R$ 7.790,00` passava por sustentada
                # contra um usuário que disse R$ 77,90 — cem vezes menos.
                v = _brl_to_cents(m.group(0))
                if v is not None:
                    cents.add(v)
            else:
                cents |= _evidence_cents(m.group(0))
        # Por CHAVE quando o resultado é JSON (o caso normal); e o `#N`
        # explícito sempre, porque ali o `#` já diz que é ID.
        try:
            _ids_from_json(json.loads(chunk), ids)
        except (ValueError, TypeError):
            pass
        ids.update(int(m.group(1)) for m in _CLAIM_ID_RE.finditer(chunk))
    return cents, raw, ids


def check(reply: str, tool_results: list[str], user_text: str = "") -> list[Claim]:
    """Toda afirmação numérica da resposta, marcada como sustentada ou não."""
    reply = reply or ""
    cents, raw_codes, ev_ints = collect_evidence(tool_results, user_text)

    # ponytail: match exato contra a evidência. Soma feita pelo modelo
    # ("100 e 50" → "R$ 150,00") sai como não sustentada. Se o relatório
    # começar a encher de derivados legítimos, o passo é subset-sum em
    # centavos sobre os valores da evidência — não afrouxar o match.
    claims: list[Claim] = []
    for m in _CLAIM_MONEY_RE.finditer(reply):
        val = _brl_to_cents(m.group(1))
        if val is None:
            continue
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

    # ID e dinheiro não se sustentam: o caso do apontamento do Codex no #238.
    EV = ['{"user_seq": 1, "valor": 50.0}']
    id50 = check("Apaguei o lançamento #50.", EV)[0]
    assert not id50.supported, "R$ 50,00 na evidência NÃO pode sustentar o #50"
    assert check("Apaguei o lançamento #1.", EV)[0].supported, "o #1 real tem que passar"
    assert check("Gastou R$ 50,00.", EV)[0].supported, "o valor real tem que passar"
    # Teto conhecido do outro sentido, medido e aceito: um inteiro solto na
    # evidência ainda sustenta o valor redondo correspondente. Não dá pra
    # separar sem saber o nome do campo, e excluir inteiro do dinheiro
    # reprovaria `"valor": 50` legítimo — cria lobo, que é pior.
    assert check("Anotei R$ 1,00.", EV)[0].supported

    # Data NÃO é ID: `2026-09-01` não pode sustentar `#9`, `#1` nem `#2026`.
    DATA = ['{"start_date": "2026-09-01", "end_date": "2026-09-30", "total": 263.35}']
    assert collect_evidence(DATA)[2] == set(), collect_evidence(DATA)[2]
    for falso in ("#9", "#1", "#2026"):
        assert not check(f"Apaguei o lançamento {falso}.", DATA)[0].supported, falso

    # Chave que TERMINA em "id" sem ser id (`paid`, `valid`) não entra.
    assert collect_evidence(['{"paid": 7, "valid": 9}'])[2] == set()

    # ID INTERNO não sustenta `#N`: a tool devolve os dois lado a lado, e só o
    # `user_seq` é o que o usuário vê.
    LINHA = ['{"id": 4242, "user_seq": 1, "valor": 50.0}']
    assert collect_evidence(LINHA)[2] == {1}, collect_evidence(LINHA)[2]
    assert not check("Apaguei o lançamento #4242.", LINHA)[0].supported
    assert check("Apaguei o lançamento #1.", LINHA)[0].supported
    # ids internos de outras tabelas também não viram `#N`
    assert collect_evidence(['{"bill_id": 7, "card_id": 9, "group_id": 3}'])[2] == set()

    # `#N` cru num resultado de tool conta — ali o `#` já diz que é ID.
    assert check("Apaguei o #4.", ["extrato: [#4] mercado 50,00"])[0].supported

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

    # saldo negativo é afirmação, e o SINAL faz parte dela
    assert check("🏦 Saldo: R$ -50,00", TOOLS)[0].supported, "negativo tem que ser checado"
    assert not check("🏦 Saldo: R$ -77,00", TOOLS)[0].supported
    NEG = ['{"balance": -50.0}']
    assert check("Seu saldo é R$ -50,00.", NEG)[0].supported
    assert not check("Seu saldo é R$ 50,00.", NEG)[0].supported, \
        "comer o menos inverte o fato: DEVE 50 vira TEM 50"

    # eco de confirmação pendente NÃO é evidência
    PEND = [{"role": "tool", "content": '{"status": "pending_user_confirmation", '
             '"summary": "depositar R$ 100,00 na viagem", "args": {"valor": 100.0}}'}]
    assert tool_results(PEND) == [], "o eco do próprio modelo tem que sair"
    assert not check("Confirma depositar R$ 100,00?", tool_results(PEND))[0].supported
    # mas o valor que o USUÁRIO disse continua valendo
    assert check("Confirma depositar R$ 100,00?", tool_results(PEND),
                 user_text="guarda 100 na viagem")[0].supported

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
    assert money_cents("Saldo: R$ -50,00") == {-5000}, "o sinal tem que sobreviver"
    assert money_cents("Saldo: R$ -50,00") != money_cents("Saldo: R$ 50,00"), \
        "diferir só no sinal não pode dar conjuntos iguais"

    # O usuário escreve BRL: `77,90` é 77,90 e nada mais
    assert collect_evidence([], "gastei 77,90")[0] == {7790}, collect_evidence([], "gastei 77,90")[0]
    assert not check("Anotei R$ 7.790,00.", [], user_text="gastei 77,90")[0].supported
    assert check("Anotei R$ 77,90.", [], user_text="gastei 77,90")[0].supported
    # a tool CONTINUA com a leitura dupla, que é onde a ambiguidade é real
    assert 779000 in collect_evidence(['{"valor": "7790"}'])[0]

    # data, contagem de parcela e ano não são dinheiro
    for texto, inventado in [("quais contas vencem dia 20?", "R$ 20,00"),
                             ("parcelei em 12 vezes", "R$ 12,00"),
                             ("em 2026 eu quero economizar", "R$ 2.026,00")]:
        assert collect_evidence([], texto)[0] == set(), (texto, collect_evidence([], texto)[0])
        assert not check(f"Total: {inventado}", [], user_text=texto)[0].supported, texto
    # e o que É dinheiro continua entrando, inclusive inteiro solto
    for texto, valor in [("gastei 50 no mercado", "R$ 50,00"),
                         ("guarda 100 na viagem", "R$ 100,00"),
                         ("recebi 2000 de salario", "R$ 2.000,00"),
                         ("paguei 89,90 na farmácia", "R$ 89,90")]:
        assert check(f"Anotei {valor}.", [], user_text=texto)[0].supported, texto

    # ID e código citados pelo usuário NÃO são dinheiro
    assert collect_evidence([], "apague #50")[0] == set(), collect_evidence([], "apague #50")[0]
    assert not check("Gastou R$ 50,00.", [], user_text="apague #50")[0].supported
    assert collect_evidence([], "apaga a compra CC12")[0] == set()
    # mas o ID citado continua sustentando o ID
    assert check("Apaguei o #50.", [], user_text="apague #50")[0].supported

    # resposta sem número não vira lobo
    assert verdict(check("Seu maior gasto foi no mercado.", TOOLS)).startswith("🈳")

    # código de compra no crédito
    assert check("Compra CC12 apagada.", ['{"codigo": "CC12"}'])[0].supported
    assert not check("Compra CC12 apagada.", ['{"codigo": "CC99"}'])[0].supported

    print("ai_guard: autoteste OK")
