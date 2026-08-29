# core/handlers/launches.py
from __future__ import annotations
import logging
import os
import re
from datetime import date, timedelta

import db
from utils_text import (
    fmt_brl, is_internal_category, canonicalize_category_label, normalize_text,
    merchant_key, RECURRING_SUGGESTION_BLOCKLIST, CATEGORY_LABELS,
)
from utils_date import (
    launch_day, extract_date_from_text, today_tz, parse_period_from_text,
    month_range_today,
)
from core.services.category_service import infer_category, learn_from_inference
from parsers import (
    parse_receita_despesa_natural,
    split_financial_transactions,
    describe_valueless_launch,
    _extract_valor,
    RECEITA_START_VERBS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers de data
# ---------------------------------------------------------------------------

def _parse_date_entity(entities: dict, original_text: str) -> date | None:
    """
    Tenta obter uma data de:
      1. entities["date_filter"] — pode ser ISO "2026-04-03", "hoje", "ontem" ou "dia 4"
      2. texto original via extract_date_from_text
    Retorna um objeto date ou None.
    """
    raw = entities.get("date_filter")
    if raw:
        raw_s = str(raw).strip().lower()

        # palavras especiais
        today = today_tz()
        if raw_s == "hoje":
            return today
        if raw_s == "ontem":
            return today - timedelta(days=1)

        # ISO direto
        try:
            return date.fromisoformat(raw_s)
        except ValueError:
            pass

        # tenta extrair do valor em si ("dia 4", "03/04", etc.)
        dt, _ = extract_date_from_text(raw_s)
        if dt:
            return dt.date()

    # fallback: extrai do texto original
    dt, _ = extract_date_from_text(original_text)
    if dt:
        return dt.date()

    return None


def _fmt_date_label(d: date) -> str:
    today = today_tz()
    if d == today:
        return "hoje"
    if d == today - timedelta(days=1):
        return "ontem"
    return d.strftime("%d/%m/%Y")


# ---------------------------------------------------------------------------
# list_launches — com suporte a filtro de data
# ---------------------------------------------------------------------------

# Tipos que são ações internas de gerenciamento (não movimentações financeiras)
# Esses registros existem na tabela launches para fins de rollback/auditoria,
# mas não devem aparecer na listagem do usuário.
_INTERNAL_TIPOS = {
    "criar_caixinha", "delete_pocket",
    "create_investment", "delete_investment",
}


# --- eixo TIPO: despesa / receita / os dois ---------------------------------
#
# Casa contra o texto SEM o nome da categoria resolvida
# (`_fora_do_nome_da_categoria`), já normalizado (sem acento, minúsculo) — nunca
# contra o texto inteiro.
#
# NÃO é "o mesmo conjunto de verbos do parser", e a diferença é de propósito: a
# regra de LANÇAR do roteador determinístico (`_ALIAS_PATTERNS`, em
# core/intent_classifier.py) tem `debitei|mandei|enviei|pixei`, que aqui não
# entram — ninguém PERGUNTA "mandei em mercado"; e aqui entram
# `gastos|gastou|despesas?|pagamento|compras`, que lá não valem pra lançar.
_PEDE_GASTO_RE = re.compile(
    r"\b(gastei|gasto|gastos|gastou|gastando|despesas?|paguei|pagamento|comprei|compras|torrei|queimei)\b"
)

# Pergunta explicitamente por dinheiro que ENTROU. Reusa os verbos de receita do
# parser (RECEITA_START_VERBS) em vez de manter uma lista paralela — foi a lista
# paralela que fez "caiu"/"pingou"/"embolsei" caírem no caminho expense-only.
# NÃO é load-bearing: sem palavra nenhuma o tipo fica None e a resposta cobre os
# DOIS tipos da categoria (conteúdo correto, só menos estreito).
_PEDE_RECEITA_RE = re.compile(
    r"\b(receitas?|ganhos?|entradas?|recebimentos?|faturamento|"
    + "|".join(v.strip() for v in RECEITA_START_VERBS)
    + r")\b"
)

# --- eixo FORMATO: total / lista -------------------------------------------
#
# INDEPENDENTE do tipo: "quanto entrou em rendimentos" é TOTAL de RECEITA,
# "liste os gastos em lazer" é LISTA de DESPESA. Os dois eixos eram um só, e o
# resultado era que a palavra que escolhia o formato (`quanto` vs `liste`) também
# escolhia o escopo sem dizer: "quanto gastei em mercado" respondia o mês
# (R$ 50,00) e "quanto foi em mercado" respondia 200 dias (R$ 9.050,00), as duas
# com o mesmo rótulo "💸 Gastos:".
#
# Sem palavra de total → LISTA, que é o formato que não esconde linha nenhuma.
_PEDE_TOTAL_RE = re.compile(
    r"\b(quanto|quantos|quanta|quantas|total|totais|soma|somou|somando)\b"
)


# Palavras de ligacao que o usuario intercala no meio do nome sem mudar o nome:
# o rotulo canonico `pagamento_fatura` e digitado "pagamento DE fatura", e o
# trecho casado tem que ser o mesmo nos dois. Nao entram no comeco do trecho
# (so entre duas palavras do nome ja casadas).
_LIGACAO_NO_NOME = {"de", "da", "do", "dos", "das", "e"}


def _ultimo_trecho_do_nome(toks: list[str], nome: list[str]) -> tuple[int, int] | None:
    """Índices [i, j) do ÚLTIMO trecho de `toks` que soletra `nome`, ou None.

    Critério de desempate quando o nome INTEIRO cabe em dois lugares: fica o
    último, porque o nome é o que vem DEPOIS do conector que
    `_extract_query_category` usou pra achá-lo ("gastos com <nome>",
    "lançamentos em <nome>") e a instrução do usuário vem antes.

    MEDIDO: trocar este `range` pelo crescente (revert "11 ultimo trecho vira o
    primeiro") deixa a suíte VERDE — em "qual o total de gastos com total da
    obra" o trecho é o mesmo nos dois sentidos, porque o "total" solto do começo
    não soletra o nome inteiro. Ou seja: é desempate, não é o conserto. Quem
    conserta os dois P2 é casar TRECHO em vez de subtrair TOKEN
    (`_fora_do_nome_da_categoria`), e ESSE revert fica vermelho.
    """
    for i in range(len(toks) - 1, -1, -1):
        j = i
        k = 0
        while j < len(toks) and k < len(nome):
            if toks[j] == nome[k]:
                k += 1
            elif k == 0 or toks[j] not in _LIGACAO_NO_NOME:
                break
            j += 1
        if k == len(nome):
            return i, j
    return None


def _fora_do_nome_da_categoria(text: str, categoria: str | None) -> str:
    """`text` sem a OCORRÊNCIA do NOME da categoria resolvida (forma preservada).

    Todo leitor de intenção do caminho de categoria lê ESTA saída, nunca o texto
    cru: os dois eixos (`_tipo_pedido`, `_pede_total`) e o período
    (`_resolve_period`, via `_responder_categoria`). Palavra que faz parte do
    nome da categoria é rótulo, não pedido: "gastos com minha namorada" digitado
    cru É o nome da categoria, e ler o "gastos" dele forçava expense-only e
    escondia a RECEITA lançada lá — a categoria da queixa original, na docstring
    de tests/test_custom_category_infer.py.

    O corte anterior era POSICIONAL (tudo antes do primeiro "com|em|no|na") e
    errava a forma que o usuário digita: sem preposição ANTES do nome, a
    primeira preposição é a de DENTRO do nome ("gastos │com│ minha namorada") e
    a palavra de tipo do próprio nome sobrava do lado da pergunta.

    O corte seguinte era por CONJUNTO DE TOKENS, e errava nos dois sentidos —
    apagava toda palavra com aquele valor, inclusive a que o usuário escreveu
    como instrução dele:
      - "qual o total de gastos com total da obra" perdia OS DOIS "total" e
        virava lista quando o pedido era um número;
      - "liste os lançamentos em fim de semana" perdia o "semana" do NOME, mas
        quem lia período (`_resolve_period`) recebia o texto CRU e restringia a
        query à semana corrente — lançamento antigo sumia calado.

    Aqui o corte é do TRECHO casado (`_ultimo_trecho_do_nome`): sai a ocorrência,
    não o valor. Sem trecho contíguo — o usuário digitou só PARTE do nome
    ("...com namorada" pra `gastos com minha namorada`, resolvido pelo fuzzy de
    `custom_category_match`) — sai só a palavra do nome que está no PEDIDO que
    resolveu a categoria (`_extract_query_category`, a mesma entrada que
    `_resolve_query_category` passa pro fuzzy). Palavra do nome que NÃO aparece
    nesse pedido nunca foi rótulo nesta frase: é do usuário e fica. Remover toda
    palavra do rótulo comia o "gastos" de "me liste os gastos com namorada" — o
    eixo TIPO zerava e a listagem de despesa voltava com receita junto.

    A forma do texto é PRESERVADA (só `_` vira espaço) porque `_resolve_period`
    precisa de "03/04" inteiro; `normalize_text` transformaria a barra em espaço
    e a data sumiria. Quem lê por regex normaliza depois.

    `_` vira espaço nos DOIS lados. Os cinco rótulos canônicos com underscore
    (`pagamento_fatura`, `investimento_aporte`, `investimento_resgate`,
    `transferencia_interna`, `ajuste_saldo`) são UM token na forma canônica e
    vários na forma que `canonicalize_category_label` aceita de propósito
    ("Aceita rótulos digitados com espaço", utils_text.py). Sem isso o nome não
    era removido de nenhum dos cinco, e no `pagamento_fatura` o "pagamento" do
    RÓTULO casava `_PEDE_GASTO_RE`: "liste os lançamentos em pagamento de
    fatura" — pergunta neutra — virava expense-only por causa do próprio nome.
    """
    raw = (text or "").replace("_", " ")
    if not categoria:
        return raw
    nome = normalize_text(categoria).replace("_", " ").split()
    if not nome:
        return raw

    # posição de cada palavra no texto ORIGINAL, pra poder recortar preservando
    # o resto (barras, acentos, maiúsculas).
    marcas = list(re.finditer(r"\w+", raw))
    toks = [normalize_text(m.group()) for m in marcas]

    trecho = _ultimo_trecho_do_nome(toks, nome)
    if trecho:
        fora = set(range(*trecho))
    else:
        # Só é rótulo a palavra do nome que o usuário DIGITOU no pedido — as
        # outras ele não escreveu, então nenhuma ocorrência delas no texto veio
        # do nome. `_extract_query_category` devolve exatamente o trecho que
        # `_resolve_query_category` deu pro fuzzy casar a categoria.
        # ponytail: heurística com teto conhecido — nome ABREVIADO e cru
        # ("gastos com namorada" pra `gastos com minha namorada`) tem as mesmas
        # palavras de "me liste os gastos com namorada", então os dois leem
        # despesa. Separá-los exige saber a intenção, não mais texto; se virar
        # queixa, o caminho é o usuário escrever o nome inteiro (aí volta a ser
        # trecho contíguo e o rótulo sai todo).
        pedido = normalize_text(_extract_query_category(text) or "")
        casadas = set(pedido.replace("_", " ").split())
        fora = set()
        for palavra in nome:
            if palavra not in casadas:
                continue
            cand = [i for i, t in enumerate(toks) if t == palavra and i not in fora]
            if cand:
                fora.add(cand[-1])
    if not fora:
        return raw

    pedacos = []
    pos = 0
    for i in sorted(fora):
        pedacos.append(raw[pos:marcas[i].start()])
        pos = marcas[i].end()
    pedacos.append(raw[pos:])
    return " ".join(pedacos)


def _pede_total(text: str, categoria: str | None) -> bool:
    """True = responder com um TOTAL; False = responder com a LISTA.

    Palavra de total VENCE palavra de lista ("me mostra quanto gastei em X" pede
    o número; "mostra" ali é educação, "quanto" é o pedido). O contrário — lista
    vencendo — deixaria "quanto" sem efeito nenhum na maioria das frases reais.
    """
    return bool(_PEDE_TOTAL_RE.search(
        normalize_text(_fora_do_nome_da_categoria(text, categoria))
    ))


def _tipo_pedido(text: str, categoria: str | None) -> str | None:
    """'despesa' | 'receita' | None (os dois) — que TIPO a pergunta pede.

    SÓ o texto decide. `entities["tipo"]` era consultado como fallback e foi
    removido: o prompt do classificador não documenta `tipo` pra `launches.list`
    (catálogo de intents do `_SYSTEM_PROMPT`), então o valor é chute do LLM sem
    contrato — e ele só consegue ESTREITAR. Medido antes da remoção:
    `entities={"tipo":"despesa"}` + "me mostra os lançamentos em rendimentos"
    respondia "você não teve gastos em rendimentos" com a receita no banco.

    Lê SÓ o texto sem o nome da categoria (`_fora_do_nome_da_categoria`): o nome
    da categoria não é vocabulário da pergunta. Sobre o texto inteiro, "liste as
    receitas em compras online" tinha "compras" (categoria de SISTEMA, em
    `CATEGORY_LABELS`/utils_text.py) casando como palavra de gasto e respondia
    "você não teve gastos em compras online". Idem "gastos com minha namorada".

    AS DUAS classes presentes → None (mostra os dois tipos): "liste os gastos e
    receitas em X" pede os dois, e escolher um esconde metade. Isso também deixa
    a NEGAÇÃO ("não quero gastos, quero as receitas") cair em "ambos" — conteúdo
    correto sem interpretar negação, que é análise de linguagem e não é o que
    este gate faz. Nenhuma das duas → None também.
    """
    fora = normalize_text(_fora_do_nome_da_categoria(text, categoria))
    tem_gasto = bool(_PEDE_GASTO_RE.search(fora))
    tem_receita = bool(_PEDE_RECEITA_RE.search(fora))
    if tem_gasto and tem_receita:
        return None
    if tem_gasto:
        return "despesa"
    if tem_receita:
        return "receita"
    return None


def _resolve_period(text: str, entities: dict | None = None) -> tuple[date | None, date | None, str]:
    """Período pedido, ou (None, None, "") quando o texto/entities não trazem um.

    Precedência TEXTO PRIMEIRO: `date_filter` já resolvido (clarificação "gastos
    com saúde dia 4" + "abril" → ISO em entities) é só FALLBACK, senão "julho"
    (mês inteiro) colapsaria no único dia que estiver em entities.

    Texto vazio no 2º arg do `_parse_date_entity` porque o texto JÁ foi consultado
    pelas duas vias: `parse_period_from_text` (utils_date.py) termina chamando
    `extract_date_from_text` no mesmo texto, então dd/mm já viraria janela aqui em
    cima. O que sobra de fora é "dia 4" NU, que nenhuma das duas parseia — e nem
    deve: sem o mês, o mês é chute. Quem resolve isso é a clarificação do
    classificador ("do dia 4 de qual mês?"), cuja resposta volta em `date_filter`
    e é lida logo abaixo.
    """
    period = parse_period_from_text(text)
    if period:
        return period
    dia = _parse_date_entity(entities or {}, "")
    if dia:
        lbl = _fmt_date_label(dia)
        return dia, dia, (lbl if lbl in ("hoje", "ontem") else f"em {lbl}")
    return None, None, ""


def _limit_pedido(entities: dict | None) -> int:
    """Quantas linhas o usuário pediu, ou 20. Blinda contra o LLM.

    `entities` vem do classificador, então `limit` pode ser "tres", None ou 5000.
    O teto de 100 limita a QUERY, não a mensagem: quem garante que a resposta cabe
    é o corte por caracteres em `_listar_categoria` (`_WPP_MAX_CHARS`), porque o
    limite real do WhatsApp é de 4096 caracteres e não de linhas — 100 linhas
    deste formato dão 9392 (medido com linhas reais; ver `_WPP_MAX_CHARS`).
    """
    try:
        return max(1, min(int((entities or {}).get("limit") or 20), 100))
    except (TypeError, ValueError):
        return 20


def _nada_encontrado(label: str, tipo: str | None, sufixo: str) -> str:
    """Resposta de vazio que DIZ qual tipo foi filtrado.

    Uma frase só pros TRÊS caminhos de vazio (lista, total e total de despesa):
    sem isso, "liste os gastos em rendimentos" (só tem RECEITA lá),
    "liste as receitas em mercado" (só tem DESPESA) e "liste os lançamentos em
    beleza" (vazia de verdade) davam a MESMA resposta pra três verdades
    diferentes — "não tem nada aqui" quando tem, só do outro tipo.

    `sufixo` já vem com o espaço da frente (" neste mês", " em 12/04") ou vazio.
    """
    o_que = {"despesa": "gastos", "receita": "receitas"}.get(tipo, "lançamentos")
    return f"🐷 Você não teve {o_que} em **{label}**{sufixo}."


# Teto da resposta de listagem: o limite documentado do WhatsApp, SEM folga —
# quem faz o trabalho é `_wpp_len`, medindo na unidade conservadora.
_WPP_MAX_CHARS = 4096


def _wpp_len(s: str) -> int:
    """Comprimento em unidades UTF-16 — a contagem conservadora.

    A doc do WhatsApp diz "4096 caracteres" e não diz em qual unidade; daqui não
    dá pra conferir qual delas o servidor usa (produção bloqueada). UTF-16 não é
    chute: todo codepoint vale 1 OU 2 unidades UTF-16, então esta contagem é
    sempre >= a contagem por codepoint. Se o lado de lá contar UTF-16 (como
    JS/Java), o corte é exato; se contar codepoints, é conservador. `len()` é o
    único jeito de errar PRA MENOS — medido com 100 linhas de descrição com 50
    emoji astrais: len()=3820 e 5822 unidades UTF-16, 1726 acima de 4096.
    """
    return len(s.encode("utf-16-le")) // 2


def _desc(row: dict) -> str:
    r"""Descrição da linha: UMA linha, truncada. `alvo`/`nota` são `text` sem teto
    nenhum (create table launches, db/schema.py) e vêm do WhatsApp — descrição de
    5000 chars estourava o limite numa linha SÓ, que o corte por linhas não
    alcança (ele para em `n > 1`). Medido antes: 5101 unidades numa única linha.

    O `split()` não é cosmético: descrição com `\n` vira mais linhas na resposta
    (medido: UM lançamento com "linha1\nlinha2\nlinha3" saía com 6 linhas, contra
    4 agora), e uma descrição escrita como "\n#99 • despesa • R$ 9.999,00 • ..."
    se passa por OUTRO lançamento dentro da listagem. Sem argumento ele cobre
    `\r`, `\t` e os separadores Unicode (\u2028/\u2029) de uma vez.

    300 é escolha de legibilidade (o que ainda cabe numa tela de celular) e NÃO é
    folga calculada — o número aqui é em CODEPOINTS, que não é a unidade do teto
    (`_wpp_len`). Cada codepoint pode valer 2 unidades UTF-16, então a resposta
    de um lançamento só cresce ~2 unidades por unidade de teto. Medido (1
    lançamento, valor de 7 dígitos, descrição só de codepoints astrais): teto 300
    → 714 unidades (cabe), teto 2000 → 4114 (18 ACIMA do 4096), teto 3900 → 7914.
    O ponto exato de quebra depende do resto da linha (valor, nome da categoria,
    data), então não existe número seguro de cor: subir este teto exige medir.
    """
    d = " ".join((row.get("descricao") or "").split()) or "—"
    return d if len(d) <= 300 else d[:299] + "…"


def _listar_categoria(
    user_id: int, categoria: str, pergunta: str,
    entities: dict | None = None, tipo: str | None = None,
) -> str:
    """Listagem cronológica dos lançamentos de uma categoria (launches + cartão).

    `pergunta` é o texto JÁ sem o nome da categoria (`_fora_do_nome_da_categoria`,
    aplicado em `_responder_categoria`) — é ele que decide o período, senão o
    nome vira filtro de data.

    Sem período no texto → sem janela: mostra os últimos N da categoria, e o
    sumário diz "(de sempre)". Uma lista que descarta linhas de uma janela não
    anunciada é o bug que este caminho existe pra matar; um TOTAL sem escopo é a
    mesma mentira em uma linha só.

    `entities["limit"]` é respeitado quando o usuário pediu um número ("me mostra
    os últimos 3 lançamentos em lazer"). Sem pedido explícito o default é 20, e
    NÃO o 10 que o roteador usa na listagem geral: o roteador não distingue
    "usuário pediu 10" de "ninguém pediu nada", então ler o `limit` dele
    encolheria toda listagem de categoria por causa de um default.

    Mesmo princípio no corte por `limit`: o sumário sai do `resumo` (TODAS as
    linhas que casam, calculado em SQL antes do LIMIT), e o cabeçalho anuncia
    "mostrando os N mais recentes de M". Somar só as linhas exibidas dava um
    total menor que o real com rótulo de total — medido: 25 despesas de R$ 112,00
    (R$ 2.800,00) viravam "💸 Gastos: R$ 2.240,00" (as 20 exibidas), enquanto
    "quanto gastei em lazer" respondia R$ 2.800,00 pra mesma pergunta.

    O total daqui NÃO bate com `sum_spent_in_category_period` em categoria de
    movimento interno: aquele filtra `is_internal_movement = false`
    (`sum_spent_in_category_period`, db/budgets.py) e este não (ver a docstring
    de `list_launches_by_category`).
    Em pagamento_fatura/aporte a lista mostra e soma o que o outro ignora — é a
    diferença entre "o que aconteceu nesta categoria" e "quanto você gastou".
    """
    start, end, period_label = _resolve_period(pergunta, entities)
    rows, resumo = db.list_launches_by_category(
        user_id, categoria, start, end, tipo=tipo, limit=_limit_pedido(entities),
    )

    label = canonicalize_category_label(categoria) or categoria
    suffix = f" {period_label}" if period_label else ""
    if not rows:
        return _nada_encontrado(label, tipo, suffix)

    lines = []
    for r in rows:
        valor = fmt_brl(float(r["valor"])) if r.get("valor") is not None else "-"
        desc = _desc(r)
        data = r.get("data")
        data_txt = _fmt_date_label(data) if data else "-"
        # Linha de cartão não tem user_seq, e "#N" é o que o usuário digita em
        # "apagar #N" — mostrar um número que não existe seria pior que não mostrar.
        prefixo = f"#{r['user_seq']}" if r.get("user_seq") else "💳"
        lines.append(f"{prefixo} • {r.get('tipo', '')} • {valor} • {desc} • {data_txt}")

    # sumário do PERÍODO INTEIRO, não das linhas exibidas (ver docstring). Sem
    # período, o escopo vai escrito: número de total sem escopo é o que fazia a
    # mesma pergunta valer R$ 50,00 ou R$ 9.050,00 sob o mesmo rótulo.
    escopo = "" if period_label else " (de sempre)"
    summary_parts = []
    if resumo["despesa"] > 0:
        summary_parts.append(f"💸 Gastos: {fmt_brl(resumo['despesa'])}{escopo}")
    if resumo["receita"] > 0:
        summary_parts.append(f"💰 Receitas: {fmt_brl(resumo['receita'])}{escopo}")
    summary = "\n".join(summary_parts)

    def montar(n: int) -> str:
        header = f"🧾 **Lançamentos em {label}**{suffix}"
        # o anúncio conta o TOTAL que existe (`n_total`), não o que sobrou depois
        # do corte — é o número que responde "e o resto?".
        if resumo["n_total"] > n:
            header += f" (mostrando os {n} mais recentes de {resumo['n_total']})"
        corpo = "\n".join(lines[:n])
        return f"{header}:\n{corpo}" + (f"\n\n{summary}" if summary else "")

    # Teto do WhatsApp (4096), medido em unidades UTF-16 (`_wpp_len`).
    # `_limit_pedido` aceita até 100, e 100 linhas deste formato renderizam 9394
    # unidades — medido com 100 lançamentos REAIS no Postgres (descrição de 50
    # chars, valor de 7 dígitos, nome de categoria de 37); com o corte a mesma
    # resposta sai com 4048 unidades em 42 linhas.
    # Sem corte a mensagem seria REJEITADA no envio, não truncada.
    # Não existe chunking em nenhum ponto do caminho de envio
    # (grep por "4096|chunk|split_message|MAX_MSG" em core/ e utils* = 0 hits), e
    # construir um é infra nova fora do escopo: aqui o corte usa o mecanismo que já
    # existe — truncar e ANUNCIAR, o mesmo "(mostrando os N mais recentes de M)".
    #
    # ponytail: corte por tentativa e erro, uma linha por vez (no máximo 100
    # iterações num texto de 6 KB). Uma busca binária ou um orçamento por
    # prefix-sum seria mais rápido e mais código; o custo aqui é irrelevante ao
    # lado da query que acabou de rodar.
    n = len(lines)
    resp = montar(n)
    while n > 1 and _wpp_len(resp) > _WPP_MAX_CHARS:
        n -= 1
        resp = montar(n)
    return resp


def list_launches(user_id: int, limit: int = 10, entities: dict | None = None, original_text: str = "") -> str:
    entities = entities or {}

    # "liste os gastos com <categoria>" chega aqui como launches.list, mas a
    # listagem geral ignora categoria e mostra os últimos N de tudo. Só entra no
    # caminho categoria-aware quando o texto menciona uma categoria que EXISTE de
    # fato (sistema ou custom). "liste os gastos no cartão" não é categoria — cai
    # na listagem geral em vez de responder "R$ 0 em cartao".
    #
    # Dentro dele, `_responder_categoria` decide os dois eixos (tipo e formato).
    # O DEFAULT dos dois é o que não esconde nada: sem palavra de tipo, os dois
    # tipos; sem palavra de total, a lista.
    #
    # `limit` NÃO é repassado de propósito: ele chega aqui com o 10 fixo do
    # roteador (core/intent_router.py é o único chamador), que não distingue
    # "usuário pediu 10" de "ninguém pediu nada". O caminho de categoria lê o
    # pedido explícito direto de `entities["limit"]` (`_limit_pedido`, default
    # 20) — repassar o 10 encolheria toda listagem de categoria por causa de um
    # default de outro caminho.
    categoria_pedida = _resolve_query_category(user_id, original_text)
    if categoria_pedida:
        return _responder_categoria(user_id, categoria_pedida, original_text, entities)

    target_date = _parse_date_entity(entities, original_text)

    if target_date:
        # busca por dia específico
        rows = db.get_launches_by_period(user_id, target_date, target_date)
        label = _fmt_date_label(target_date)

        # filtra tipos internos de gerenciamento
        rows = [r for r in rows if r.get("tipo") not in _INTERNAL_TIPOS]

        if not rows:
            return f"Nenhum lançamento encontrado em **{label}**."

        # calcula totais
        total_despesas = sum(float(r["valor"]) for r in rows if r.get("tipo") == "despesa")
        total_receitas = sum(float(r["valor"]) for r in rows if r.get("tipo") == "receita")

        lines = []
        for r in rows:
            tipo   = r.get("tipo", "")
            valor  = fmt_brl(float(r["valor"])) if r.get("valor") is not None else "-"
            nota   = r.get("nota") or r.get("alvo") or "-"
            cat    = r.get("categoria") or ""
            cat_txt = f" [{cat}]" if cat else ""
            lines.append(f"#{r.get('user_seq') or r['id']} • {tipo} • {valor} • {nota}{cat_txt}")

        header = f"🧾 **Lançamentos de {label}**"
        summary_parts = []
        if total_despesas > 0:
            summary_parts.append(f"💸 Gastos: {fmt_brl(total_despesas)}")
        if total_receitas > 0:
            summary_parts.append(f"💰 Receitas: {fmt_brl(total_receitas)}")
        summary = "\n".join(summary_parts)

        return f"{header}:\n" + "\n".join(lines) + (f"\n\n{summary}" if summary else "")

    # sem filtro de data → últimos N lançamentos (busca mais para compensar os internos filtrados)
    rows = db.list_launches(user_id, limit=limit + 20)
    rows = [r for r in rows if r.get("tipo") not in _INTERNAL_TIPOS][:limit]
    if not rows:
        return "Você ainda não tem lançamentos."

    today = today_tz()

    _TIPO_EMOJI = {
        "despesa":              "💸",
        "receita":              "💰",
        "entrada":              "💰",
        "saida":                "💸",
        "aporte_investimento":  "📈",
        "resgate_investimento": "📉",
        "create_investment":    "📈",
        "transferencia":        "↔️",
    }

    lines = []
    for r in rows:
        tipo   = r.get("tipo", "")
        valor  = r.get("valor")
        alvo   = (r.get("alvo") or "").strip()
        nota   = (r.get("nota") or "").strip()
        criado = r.get("criado_em")

        # limpa nota técnica de investimento
        if tipo in ("create_investment", "aporte_investimento") and nota and "taxa=" in nota:
            try:
                m_taxa = re.search(r"taxa=([0-9.]+)", nota)
                m_per  = re.search(r"periodo=(\w+)", nota)
                taxa   = float(m_taxa.group(1)) * 100 if m_taxa else None
                per    = m_per.group(1) if m_per else ""
                per    = "ao mês" if per.startswith("month") else "ao dia" if per.startswith("day") else per
                nota   = f"{taxa:.4g}% {per}" if taxa is not None else nota
            except Exception:
                pass

        # descrição: prefere nota se informativa, senão usa alvo
        descricao = nota if nota and nota.lower() not in ("-", alvo.lower()) else alvo
        if not descricao:
            descricao = tipo

        # formata data de forma amigável
        if criado is not None:
            try:
                # `launch_day` (utils_date), não `.date()` nem `day_tz` cru: o
                # `.date()` devolve o dia no fuso da SESSÃO do Postgres, que ERA
                # UTC no Railway — 21:30 em São Paulo saía como o dia SEGUINTE
                # (hoje a sessão segue o app, `utils_date.align_process_tz`) — e o
                # `day_tz` sozinho converte um instante que a linha SEM HORA não
                # tem (Open Finance legado, ver `launch_day`). Mesma função de
                # `list_launches_by_category`, senão as duas portas divergem.
                d = (launch_day(criado, r.get("posted_at"), r.get("has_time", True))
                     if hasattr(criado, "date")
                     else __import__("datetime").datetime.fromisoformat(str(criado)).date())
                if d == today:
                    data_str = "hoje"
                elif d == today - timedelta(days=1):
                    data_str = "ontem"
                else:
                    data_str = d.strftime("%d/%m")
            except Exception:
                data_str = str(criado)[:10]
        else:
            data_str = "-"

        emoji     = _TIPO_EMOJI.get(tipo, "•")
        valor_str = fmt_brl(float(valor)) if valor is not None else "-"
        # Mostra user_seq (numeração por usuário, começa em #1) em vez do
        # id global. Fallback pro id interno enquanto o backfill não rodou.
        display_id = r.get("user_seq") or r.get("id")
        id_str    = f" [#{display_id}]" if display_id else ""
        lines.append(f"{emoji} {data_str} • {valor_str} • {descricao}{id_str}")

    # mini resumo de despesas/receitas no período exibido
    total_despesas = sum(
        float(r["valor"])
        for r in rows
        if r.get("tipo") in ("despesa", "saida") and not r.get("is_internal_movement")
    )
    total_receitas = sum(
        float(r["valor"])
        for r in rows
        if r.get("tipo") in ("receita", "entrada") and not r.get("is_internal_movement")
    )

    summary_parts = []
    if total_despesas > 0:
        summary_parts.append(f"💸 Gastos: {fmt_brl(total_despesas)}")
    if total_receitas > 0:
        summary_parts.append(f"💰 Receitas: {fmt_brl(total_receitas)}")
    summary = "  |  ".join(summary_parts)

    header = f"🧾 **Últimos {len(rows)} lançamentos**:"
    body   = "\n".join(lines)
    return f"{header}\n{body}" + (f"\n\n{summary}" if summary else "")


# ---------------------------------------------------------------------------
# spend_query — "quanto gastei [na categoria X] [período]"
# ---------------------------------------------------------------------------

# Palavras de período/conectores que NÃO fazem parte de um nome de categoria.
# Usadas pra limpar o rabo capturado depois de "categoria"/"com"/"no".
_CAT_STOP_WORDS = {
    "hoje", "ontem", "anteontem", "semana", "mes", "ano", "passado", "passada",
    "ultimo", "ultima", "ultimos", "ultimas", "dias", "dia", "essa", "esta",
    "esse", "este", "nessa", "nesta", "nesse", "neste", "dessa", "desta",
    "desse", "deste", "no", "na", "do", "da", "de", "em", "dos", "das", "nos",
    "nas", "recentes", "recente", "total", "gastei", "gastou", "gasto", "gasta",
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
    "agosto", "setembro", "outubro", "novembro", "dezembro",
}


def _extract_query_category(text: str) -> str | None:
    """Extrai o nome da categoria de uma pergunta, ou None.

    Reconhece "na categoria X", "categoria X" e, como fallback, "com/no/na X" —
    tudo depois do conector é candidato a nome de categoria. Do rabo capturado
    saem as palavras de período ("esta semana", "julho", ...) via `_CAT_STOP_WORDS`
    e os números. Retorna o rótulo canônico ou None.

    O corte posicional acaba AQUI: era usado também pra decidir o tipo pedido, e
    lá ele errava (ver `_fora_do_nome_da_categoria`). Pra achar o NOME ele serve —
    quem valida se o nome existe de verdade é `_resolve_query_category`.
    """
    norm = normalize_text(text)  # sem acento, minúsculo
    m = re.search(r"\bcategoria\s+(?:de\s+)?(.+)$", norm)
    if not m:
        # "gastei com/em/no/na X". Palavras de período ("em julho", "no mes")
        # caem no filtro _CAT_STOP_WORDS logo abaixo.
        m = re.search(r"\b(?:com|em|no|na)\s+(.+)$", norm)
    if not m:
        return None
    tail = m.group(1)

    toks = [
        tk for tk in tail.split()
        if tk not in _CAT_STOP_WORDS and not tk.isdigit()
    ]
    cat = " ".join(toks).strip()
    if not cat:
        return None
    return canonicalize_category_label(cat) or cat


def _resolve_query_category(user_id: int, text: str) -> str | None:
    """Categoria REAL mencionada numa pergunta, ou None se não houver.

    Retorna só quando a categoria de fato EXISTE — categoria de sistema
    ("mercado", "saúde") ou custom do usuário ("gastos com namorada"). Texto solto
    depois de "com/no/na" que não é categoria nenhuma ("no cartão", "com a família
    toda") devolve None: senão a listagem geral fica escondida atrás de uma
    pseudo-categoria vazia ("você não teve gastos em cartao").

    Categoria de SISTEMA homônima vence a custom: quem tem custom "saúde da minha
    mãe" e pergunta "gastei com saúde" quer a saúde do sistema, não a custom.
    """
    from core.services.category_service import custom_category_match
    extracted = _extract_query_category(text)
    if not extracted:
        return None
    norm = normalize_text(extracted)
    # 1) categoria de sistema (mercado, saúde, lazer...) vence tudo
    if norm in CATEGORY_LABELS:
        return canonicalize_category_label(extracted)
    # 2) nome de categoria custom EXATO — antes do fuzzy por token. Senão
    #    "cachorro" resolveria pra "cachorro do vizinho": no empate o
    #    custom_category_match mantém o 1º, e list_custom_category_names ordena
    #    por length desc, então o nome mais longo ganharia da categoria exata.
    for name in db.list_custom_category_names(user_id) or []:
        if normalize_text(name) == norm:
            return name
    # 3) categoria custom por token distintivo ("...com namorada"). Casa só no
    #    TRECHO da categoria (extracted), nunca no texto inteiro: senão "quanto
    #    gastei esta semana" casaria uma custom "fim de semana" pelo token de
    #    período "semana", que o _extract_query_category já removeu de propósito.
    return custom_category_match(user_id, norm)


def _total_despesa(
    user_id: int, categoria: str, start: date, end: date, period_label: str,
) -> str:
    """Total GASTO numa categoria no período + os 5 maiores, e o que ficou FORA.

    Duas fontes, de propósito diferentes:
      - `sum_spent_in_category_period` (db/budgets.py) filtra
        `is_internal_movement = false` e `tipo = 'despesa'` → é o número do
        DASHBOARD, e continua sendo o que sai como "você gastou".
      - o `resumo` de `list_launches_by_category` NÃO filtra nada disso → é o
        número que a LISTA da mesma categoria mostra.

    A diferença entre as duas é o que o total escondeu, e ela é medida NOS
    DADOS. Perguntar "esta categoria é interna?" (`is_internal_category`, um
    predicado sobre o NOME) não responde isso: quem grava a coluna
    `is_internal_movement` são 8+ escritores com predicados DIFERENTES. Pra ver a
    lista de hoje:
        grep -rn "is_internal_movement" --include="*.py" core/ db/ frontend/
    `import_statement_bytes` e `import_ofx_bytes` usam só
    `INTERNAL_MOVEMENT_CATEGORIES` (subconjunto estrito, sem os hints de
    investimento); `classify_open_finance_launch` decide por palavra da
    DESCRIÇÃO; e há `True`/`False` cravado em `pay_bill_amount`,
    `rebuild_bill_totals`, `set_initial_balance_route`, `adjust_balance_route`,
    `_charge_one`, `_credit_one` e `mark_bill_paid`. Medido: o
    `adjust_balance_route` grava categoria='ajuste' com a flag True, e
    `is_internal_category("ajuste")` é False — o total negava e a lista mostrava
    R$ 700,00.

    Por isso os DOIS casos saem daqui, e não só o `total <= 0`:
      total == 0 e sobrou → explica em vez de negar (era o B2/B4);
      total  > 0 e sobrou → soma parcial DIZ o que ficou fora (era o B5: R$
      100,00 anunciados com R$ 600,00 na lista, R$ 500,00 engolidos calados —
      número errado com cara de número certo).

    Nenhum número do dashboard muda: "você gastou" continua sendo o filtrado.

    ponytail: a diferença é atribuída a movimentação interna, que é a causa em
    todo escritor acima. Uma linha LEGADA com tipo='saida' (que a lista conta e
    o `sum_spent_...` não, porque ele fixa `tipo = 'despesa'`) cairia no mesmo
    rótulo. Separar as duas causas pede uma 3ª query; enquanto não houver
    escritor de 'saida' (não há), a soma é a mesma e só o rótulo seria impreciso.
    """
    total = db.sum_spent_in_category_period(user_id, categoria, start, end)
    # limit=1: o resumo vem de window aggregates, calculados ANTES do LIMIT.
    _, resumo = db.list_launches_by_category(
        user_id, categoria, start, end, tipo="despesa", limit=1,
    )
    # A lista é superset de LINHAS do que o `sum_spent_...` conta (mesma perna de
    # cartão, mais as linhas internas e as de tipo legado) — o que NÃO implica
    # soma maior: a coluna é `valor numeric not null` SEM CHECK (create table
    # launches, db/schema.py). `fora >= 0` é fato sobre os ESCRITORES, não
    # dedução: nenhum grava negativo — `import_statement_bytes` normaliza o
    # sinal (statement_import.py) e `classify_open_finance_launch` devolve
    # abs(v) (db/open_finance.py). Se um dia entrar um negativo, os
    # `if fora > 0` abaixo só deixam de imprimir a linha de explicação — o total
    # continua sendo o do dashboard, e a linha negativa aparece na LISTA da
    # categoria, que é outro caminho. Guard aqui não consertaria isso; a barreira
    # certa seria um CHECK na coluna.
    fora = round(resumo["despesa"] - total, 2)
    label = canonicalize_category_label(categoria) or categoria

    if total <= 0:
        if fora > 0:
            return (
                f"🔁 {fmt_brl(fora)} movimentados em **{label}** {period_label}.\n"
                "Não conta como gasto — é movimentação interna."
            )
        return _nada_encontrado(label, "despesa", f" {period_label}")

    lines = [f"💸 Você gastou **{fmt_brl(total)}** em **{label}** {period_label}."]
    if fora > 0:
        lines.append(
            f"🔁 Mais {fmt_brl(fora)} em movimentação interna (não conta como gasto)."
        )

    # top 5 maiores lançamentos dessa categoria no período (mesma regra do
    # dashboard: cartão pelo mês da fatura) → lista bate com o total acima
    maiores = db.get_largest_expenses(
        user_id, start, end, limit=5, categoria=categoria, by_bill_month=True
    )
    if maiores:
        lines.append("")
        lines.append("🔝 Maiores gastos:")
        for m in maiores:
            desc = _desc(m)
            lines.append(f"• {fmt_brl(m['valor'])} • {desc}")
    return "\n".join(lines)


def _total_categoria(
    user_id: int, categoria: str, pergunta: str, entities: dict | None, tipo: str | None,
) -> str:
    """Total de uma categoria honrando o eixo TIPO, com o ESCOPO no rótulo.

    `pergunta` é o texto JÁ sem o nome da categoria (`_fora_do_nome_da_categoria`,
    aplicado em `_responder_categoria`) — é ele que decide o período.

    Sem período no texto → mês corrente, e o rótulo DIZ "neste mês". É a mesma
    janela default do resto do `spend_query` e do dashboard; a alternativa ("de
    sempre") daria dois defaults diferentes pra mesma pergunta dependendo da
    palavra usada, que é justamente o bug.

    Receita e "os dois" saem do `resumo` de `list_launches_by_category` (window
    aggregates, sem query nova). Sem isso, "total em rendimentos" voltava pro
    caminho expense-only e respondia "você não teve gastos em rendimentos" com a
    receita no banco.
    """
    start, end, period_label = _resolve_period(pergunta, entities)
    if start is None:
        start, end = month_range_today()
        period_label = "neste mês"

    if tipo == "despesa":
        return _total_despesa(user_id, categoria, start, end, period_label)

    # limit=1: o resumo vem de window aggregates, calculados ANTES do LIMIT — uma
    # linha basta pra trazer os totais de TODAS as que casam.
    _, resumo = db.list_launches_by_category(
        user_id, categoria, start, end, tipo=tipo, limit=1,
    )
    label = canonicalize_category_label(categoria) or categoria
    if resumo["n_total"] == 0:
        return _nada_encontrado(label, tipo, f" {period_label}")

    lines = [f"🧾 **Total em {label}** {period_label}:"]
    if resumo["despesa"] > 0:
        lines.append(f"💸 Gastos: {fmt_brl(resumo['despesa'])}")
    if resumo["receita"] > 0:
        lines.append(f"💰 Receitas: {fmt_brl(resumo['receita'])}")
    return "\n".join(lines)


def _responder_categoria(
    user_id: int, categoria: str, text: str, entities: dict | None = None,
) -> str:
    """Resposta sobre uma categoria JÁ RESOLVIDA, nos dois eixos independentes.

      eixo TIPO    (`_tipo_pedido`) → despesa / receita / os dois
      eixo FORMATO (`_pede_total`)  → total (um número) / lista (cronológica)

    Porta ÚNICA: `list_launches` e `spend_query` entram os dois por aqui. Antes o
    `list_launches` fazia `if tipo == "despesa": return spend_query(...)` e o
    `spend_query` re-resolvia categoria e tipo do MESMO texto pra chegar no mesmo
    lugar — uma 2ª rodada de `list_custom_category_names` + `custom_category_match`
    por request, e duas guardas pra manter em sincronia.
    """
    # Os TRÊS leitores de intenção daqui pra baixo leem o MESMO texto cortado:
    # tipo, formato e PERÍODO (`_tipo_pedido` e `_pede_total` refazem o corte
    # dentro deles — mesma entrada, mesma saída, custo de um `finditer` numa
    # frase). O período era o que faltava: ele lia o texto CRU, então o nome da
    # categoria envenenava a janela — custom "fim de semana" fazia "liste os
    # lançamentos em fim de semana" virar consulta da semana corrente,
    # escondendo os lançamentos antigos sem dizer nada.
    pergunta = _fora_do_nome_da_categoria(text, categoria)
    tipo = _tipo_pedido(text, categoria)
    if _pede_total(text, categoria):
        return _total_categoria(user_id, categoria, pergunta, entities, tipo)
    return _listar_categoria(user_id, categoria, pergunta, entities, tipo)


def spend_query(user_id: int, text: str, entities: dict | None = None) -> str:
    """Responde "quanto gastei [na categoria X] [período]".

    - Interpreta o período em linguagem natural (esta semana, este mês, julho,
      últimos 7 dias, ontem, ...). Sem período reconhecido → mês corrente.
    - Com categoria REAL → `_responder_categoria` (os dois eixos): o roteador
      manda "quanto entrou em rendimentos" e "me mostra os gastos com saúde" pra
      cá também, e nem toda pergunta que chega aqui pede total de despesa.
    - Sem categoria real → total de despesas no período + top categorias, ou o
      "você não teve gastos em <X>" quando o texto cita algo que não é categoria
      nenhuma (preserva a resposta pra quem pergunta por categoria que não usa).
    """
    # spend_query é intent próprio e alcançável direto pelo roteador, então ele
    # entra pela MESMA porta: "quanto entrou em rendimentos" chega aqui sem passar
    # pelo list_launches e respondia "você não teve gastos em rendimentos".
    categoria_pedida = _resolve_query_category(user_id, text)
    if categoria_pedida:
        return _responder_categoria(user_id, categoria_pedida, text, entities)

    start, end, period_label = _resolve_period(text, entities)
    if start is None:
        # sem período reconhecido → mês corrente cheio (igual ao dashboard)
        start, end = month_range_today()
        period_label = "neste mês"

    # Chegou aqui = nenhuma categoria REAL casou (senão já teria retornado acima).
    # Sobra o extrator textual, que preserva o "você não teve gastos em <X>" quando
    # o usuário pergunta por categoria que ele não usa, e o branch geral quando não
    # há categoria nenhuma no texto.
    categoria = _extract_query_category(text)

    if categoria:
        return _total_despesa(user_id, categoria, start, end, period_label)

    # sem categoria → total geral + top categorias do período.
    # O total vem da MESMA fonte das categorias (launches + cartão por mês da
    # fatura), então bate com o "gastos do mês" do dashboard e o total é sempre
    # igual à soma das categorias.
    cats = db.get_top_expense_categories(user_id, start, end, limit=1000, by_bill_month=True)
    total = sum(c["total"] for c in cats)
    if total <= 0:
        return f"🐷 Você não teve gastos {period_label}."

    lines = [f"💸 Você gastou **{fmt_brl(total)}** {period_label}."]
    tops = cats[:3]
    lines.append("")
    lines.append("📊 Top categorias:")
    for t in tops:
        lines.append(f"• {t['categoria']}: {fmt_brl(t['total'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# add / add_from_entities — registra receita/despesa
# ---------------------------------------------------------------------------

def _recurring_suggestion_enabled() -> bool:
    """Flag de kill-switch. Ligada por padrão; desliga só com env explícito."""
    return (os.getenv("AUTO_FIX_SUGGESTION_ENABLED") or "on").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _maybe_recurring_offer(
    user_id: int, descr: str, valor: float, categoria_final: str, criado_em, launch_id,
) -> dict | None:
    """Se esta despesa repete uma anterior (mesma descrição + mesmo valor, em mês
    distinto) numa categoria elegível, devolve o payload da oferta de gasto fixo.
    Senão, None. Nunca levanta — detecção é best-effort e não pode quebrar o
    lançamento."""
    if not _recurring_suggestion_enabled():
        return None
    try:
        cat_canon = canonicalize_category_label(categoria_final) or categoria_final
        if cat_canon in RECURRING_SUGGESTION_BLOCKLIST:
            return None
        key = merchant_key(descr)
        if not key:
            return None
        when = criado_em if isinstance(criado_em, (date,)) else None
        if when is None:
            from datetime import datetime as _dt
            when = criado_em if isinstance(criado_em, _dt) else today_tz()
        from db.recurring import find_recurring_candidate
        prior_months = find_recurring_candidate(
            user_id, key, valor,
            current_year=when.year, current_month=when.month,
            exclude_launch_id=int(launch_id) if launch_id else None,
        )
        if prior_months < 1:
            return None
        name = (descr or "").strip() or (cat_canon.capitalize() if cat_canon else "Gasto fixo")
        return {
            "name": name,
            "amount": float(valor),
            "category": categoria_final,
            "due_day": int(when.day),
            "merchant_key": key,
        }
    except Exception:
        logger.warning(
            "detecção de gasto fixo falhou (user %s, descr=%r) — seguindo sem oferta",
            user_id, descr, exc_info=True,
        )
        return None


def add_from_entities(
    user_id: int,
    *,
    tipo: str,
    valor: float,
    alvo: str | None = None,
    nota: str | None = None,
    categoria: str | None = None,
    category_reason: str | None = None,
    criado_em=None,
    is_internal: bool | None = None,
    platform: str = "whatsapp",
    suppress_pending: bool = False,
    conditional_pending: bool = False,
) -> str:
    """Registra um lançamento a partir de args já estruturados (sem regex).

    Chamado por:
      - `add()` quando o parser regex já extraiu (ou caiu nos entities)
      - tool de IA `add_launch` (LLM extrai os args)

    Toda lógica compartilhada (categorização, learn, DB write, botão WhatsApp,
    alerta de orçamento) vive aqui — fonte única de verdade.

    `suppress_pending=True`: não grava oferta nenhuma em `pending_actions` (nem
    a de recorrente, nem o botão de recategorizar) e não imprime o texto da
    oferta. A tabela tem UMA linha por usuário, então a oferta atropelaria a
    fila de lançamentos múltiplos que o chamador acabou de gravar. Usado só por
    `resolve_multi_launch_value`, quando ainda sobrou item na fila.

    `conditional_pending=True`: cria ofertas de conveniência só se nenhuma
    pendência apareceu desde o commit do lançamento. Usado no último item da
    fila de multi-lançamento: outra recuperação pode ter recriado a fila entre
    a reivindicação e a oferta final.
    """
    if valor <= 0:
        return "Não consegui identificar o valor. Tente: *gastei 50 no mercado*"

    # Teto mensal de lançamentos do tier (Grátis no v2; no-op com v2 off).
    # PlanLimitExceeded sobe pro handle_incoming, que responde com a mensagem
    # amigável de upgrade — mesmo padrão das caixinhas/cartões.
    from core.services.plan_service import check_can_create_launch
    check_can_create_launch(user_id)

    alvo_clean = (alvo or "").strip()
    nota_clean = (nota or "").strip() or alvo_clean

    if categoria:
        reason_final = category_reason or "explicit"
        if reason_final == "ai":
            # Categoria veio do LLM (entities do classificador ou tool add_launch),
            # não de hashtag explícita nem do parser determinístico. O LLM erra
            # categoria de vez em quando (ex.: áudio "gastei 500 no mercado" indo
            # pra "alimentação"). Faz cross-check com as regras determinísticas:
            # um match confiante de regra do usuário / ticker / LOCAL_RULES que
            # CONTRADIZ a IA vence. allow_ai=False pra não gastar 2ª chamada de LLM.
            categoria_ai = infer_category(user_id, "", categoria).category
            local = infer_category(user_id, nota_clean, None, allow_ai=False)
            if local.reason in {"user_rule", "user_category", "ticker_match", "local_rule"} and local.category != categoria_ai:
                logger.info(
                    "categoria da IA (%s) sobreposta por regra local (%s via %s) — nota=%r",
                    categoria_ai, local.category, local.reason, nota_clean,
                )
                categoria_final = local.category
                reason_final = local.reason
            else:
                categoria_final = categoria_ai
        else:
            categoria_final = infer_category(user_id, "", categoria).category
    else:
        res = infer_category(user_id, nota_clean, None)
        categoria_final = res.category or "outros"
        reason_final = res.reason

    is_int = (
        is_internal if is_internal is not None
        else is_internal_category(categoria_final)
    )

    launch_id, user_seq, new_balance = db.add_launch_and_update_balance(
        user_id=user_id,
        tipo=tipo,
        valor=valor,
        alvo=alvo_clean or None,
        nota=nota_clean,
        categoria=categoria_final,
        criado_em=criado_em,
        is_internal_movement=is_int,
    )

    # DEPOIS DO COMMIT nada pode subir exceção. O lançamento e o saldo já
    # existem; quem chamou não tem como distinguir "não gravou" de "gravou e
    # falhou no acessório", e na fila de multi-lançamento essa confusão faz o
    # item ser devolvido e o MESMO gasto ser registrado de novo, dobrando o
    # saldo. Aprender a regra e armar as ofertas são melhor-esforço: se
    # falharem, o usuário fica sem a comodidade, não sem o dinheiro.
    try:
        learn_from_inference(
            user_id,
            nota_clean,
            categoria_final,
            target_hint=alvo_clean,
            reason=reason_final,
        )
    except Exception:
        logger.exception(
            "learn_from_inference falhou depois do commit (user %s, lancamento %s)",
            user_id, launch_id)

    # Reconciliação reversa (Open Finance): se o banco já importou esse gasto, funde
    # com o lançamento que o usuário acabou de fazer — não duplica no "sobrou".
    if not is_int:
        try:
            db.reconcile_manual_launch(user_id, launch_id)
        except Exception:
            pass

    # Detecção "essa despesa se repete → sugere gasto fixo". Só para despesa
    # real (não movimentação interna). A oferta divide a linha de pending_actions
    # com o botão de recategorizar; quando há oferta de recorrente ela vence
    # (é mais valiosa) e o botão de recategorizar é suprimido nesse lançamento.
    recurring_offer = None
    if tipo == "despesa" and not is_int and not suppress_pending:
        # merchant_key precisa da mesma prioridade alvo>nota que a query em
        # find_recurring_candidate usa pro lançamento anterior (coalesce(alvo,
        # nota)) — nota_clean sozinho é a frase toda ("gastei 44,90 na
        # netflix"), que nunca bate com o alvo limpo ("netflix") gravado no mês
        # passado, e a oferta nunca dispara mesmo quando a despesa repete.
        try:
            recurring_offer = _maybe_recurring_offer(
                user_id, alvo_clean or nota_clean, valor, categoria_final,
                criado_em, launch_id,
            )
        except Exception:
            # Pós-commit: melhor perder a oferta que subir exceção (ver o
            # comentário do learn_from_inference acima).
            logger.exception(
                "_maybe_recurring_offer falhou depois do commit (user %s, lancamento %s)",
                user_id, launch_id)

    if recurring_offer:
        try:
            if conditional_pending:
                gravou_pending = db.create_pending_action_if_absent(
                    user_id, "confirm_recurring_offer", recurring_offer)
            else:
                db.set_pending_action(user_id, "confirm_recurring_offer", recurring_offer)
                gravou_pending = True
            if not gravou_pending:
                recurring_offer = None
        except Exception:
            logger.warning(
                "falha ao salvar pending confirm_recurring_offer (user %s) — oferta perdida",
                user_id, exc_info=True,
            )
            recurring_offer = None
    # Botão "categoria errada?" no WhatsApp (one-shot, lido por
    # _send_reply_with_optional_buttons no wa_runtime e limpo em seguida).
    elif platform == "whatsapp" and launch_id and not suppress_pending:
        try:
            recat_payload = {"launch_id": int(launch_id), "user_seq": int(user_seq)}
            if conditional_pending:
                db.create_pending_action_if_absent(
                    user_id, "recategorize_launch_offer", recat_payload)
            else:
                db.set_pending_action(
                    user_id,
                    "recategorize_launch_offer",
                    recat_payload,
                )
        except Exception:
            logger.warning(
                "falha ao salvar pending recategorize_launch_offer (user %s, launch %s) — botão de recategorizar não vai aparecer",
                user_id, launch_id, exc_info=True,
            )

    emoji = "💸" if tipo == "despesa" else "💰"
    resposta = (
        f"{emoji} **{tipo.capitalize()} registrada**: {fmt_brl(valor)}\n"
        f"🏷️ Categoria: {categoria_final}\n"
        f"🏦 Saldo: {fmt_brl(float(new_balance))}\n"
        f"ID: #{user_seq}"
    )

    if tipo == "despesa" and not is_int and categoria_final:
        try:
            from datetime import datetime
            from core.budget_alerts import evaluate_after_expense, format_alert_text
            when = criado_em if isinstance(criado_em, datetime) else datetime.now()
            alert = evaluate_after_expense(user_id, categoria_final, valor, when)
            if alert:
                resposta += format_alert_text(alert)
        except Exception:
            logger.warning(
                "avaliação de alerta de orçamento falhou (user %s, categoria %r) — alerta pode ter sido perdido",
                user_id, categoria_final, exc_info=True,
            )

    if recurring_offer:
        resposta += (
            f"\n\n💡 Você já lançou *{recurring_offer['name']}* de {fmt_brl(valor)} "
            f"em outro mês. Quer marcar como *gasto fixo* (a Piggy lança sozinha "
            f"todo mês)? Responda *sim* ou *não*."
        )

    # Nudge de onboarding: no primeiríssimo lançamento do usuário no WhatsApp,
    # aponta o próximo passo (aprender fazendo). Dispara UMA vez — a trava é o
    # evento em system_event_logs; user_seq == 1 é só o pré-filtro barato pra não
    # consultar o banco a cada lançamento (e o evento ainda cobre o caso de o
    # user_seq voltar a 1 depois de um "apagar tudo"). Só pra lançamento real
    # (não movimentação interna) e quando não há oferta de recorrente competindo
    # pela atenção.
    if platform == "whatsapp" and not is_int and not recurring_offer and user_seq == 1:
        try:
            from core.observability import recent_event_exists, log_system_event_sync
            if not recent_event_exists("onboarding_first_launch_nudge", user_id, within_days=36500):
                resposta += (
                    "\n\n🎉 Esse foi seu primeiro lançamento — viu como é rápido?\n"
                    "Agora tenta **saldo**. Quando quiser, tem caixinhas, cartões e "
                    "dashboard: é só mandar **ajuda**."
                )
                log_system_event_sync(
                    "info",
                    "onboarding_first_launch_nudge",
                    "Nudge de primeiro lançamento enviado no WhatsApp.",
                    source="launches",
                    user_id=user_id,
                )
        except Exception:
            logger.warning(
                "nudge de primeiro lançamento falhou (user %s) — seguindo sem ele",
                user_id, exc_info=True,
            )

    return resposta


def _ask_value_question(item: dict) -> str:
    """Pergunta amigável pelo valor de um lançamento que veio sem número."""
    desc = item.get("desc") or "esse lançamento"
    if item.get("tipo") == "receita":
        return f"🐷 Faltou o valor de *{desc}*. Quanto você recebeu? (só o número)"
    return f"🐷 Faltou o valor de *{desc}*. Quanto foi? (só o número)"


# Quantas vezes o MESMO valor precisa ter aparecido antes (pro mesmo tipo/descrição)
# pra ser considerado recorrente e lançado sozinho, sem perguntar.
_RECURRING_MIN_COUNT = 2


def infer_recurring_value(user_id: int, tipo: str, desc: str) -> float | None:
    """Descobre o valor recorrente de uma descrição ("aluguel", "internet"...).

    Retorna o valor SÓ quando a mesma descrição (mesmo tipo) já foi lançada com
    o MESMO valor em `_RECURRING_MIN_COUNT`+ lançamentos anteriores E esse valor
    é o dominante (sem empate). Caso contrário retorna None — o bot pergunta.

    Conservador de propósito: preencher sozinho um valor errado é pior do que
    perguntar. A comparação de descrição é por texto normalizado (minúsculas,
    sem acento), batendo tanto em `nota` quanto em `alvo` do histórico.
    """
    from collections import Counter
    from utils_text import normalize_text

    target = normalize_text(desc or "").strip()
    if not target:
        return None

    counter: Counter = Counter()
    for r in db.list_launches_by_tipo(user_id, tipo, limit=200):
        nota = normalize_text(r.get("nota") or "").strip()
        alvo = normalize_text(r.get("alvo") or "").strip()
        if target in (nota, alvo):
            try:
                counter[float(r["valor"])] += 1
            except (TypeError, ValueError):
                continue

    if not counter:
        return None
    ranked = counter.most_common(2)
    top_valor, top_freq = ranked[0]
    if top_freq < _RECURRING_MIN_COUNT or top_valor <= 0:
        return None
    # empate no topo (dois valores igualmente frequentes) → ambíguo, pergunta.
    if len(ranked) > 1 and ranked[1][1] == top_freq:
        return None
    return top_valor


_RECURRING_NOTICE = "🔁 _Usei seu valor recorrente. Se mudou, é só corrigir._"


def register_if_recurring(user_id: int, tipo: str, desc: str, platform: str) -> str | None:
    """Se `desc` tem um valor recorrente conhecido, registra o lançamento na hora
    (com aviso) e devolve a resposta. Senão, retorna None (o bot vai perguntar)."""
    valor = infer_recurring_value(user_id, tipo, desc)
    if valor is None:
        return None
    resp = add_from_entities(
        user_id,
        tipo=tipo,
        valor=float(valor),
        alvo=desc,
        nota=desc,
        platform=platform,
    )
    return f"{resp}\n{_RECURRING_NOTICE}"


# Palavras que cancelam a pergunta de valor pendente.
_CANCEL_WORDS = {"nao", "n", "cancelar", "cancela", "deixa", "esquece", "esquecer", "para", "pare"}


def resolve_multi_launch_value(user_id: int, text: str, pending: dict, platform: str = "whatsapp",
                               outro_comando: bool = False) -> str | None:
    """
    Resolve a pergunta de valor pendente de um lançamento múltiplo. O bot havia
    perguntado "quanto foi *aluguel*?"; esta resposta traz o valor.

    - resposta com valor → registra o item da frente da fila; se sobra fila,
      pergunta o próximo; senão encerra.
    - resposta de cancelamento → descarta o que faltava.
    - resposta sem valor (o user mudou de assunto) → abandona a pendência e
      retorna None pra que o roteador processe a mensagem normalmente.
    - `outro_comando` (passo 1, resolvido no `route()` por
      `abandona_pergunta_de_valor`) → mesmo abandono, mas para texto que TEM
      valor e ainda assim é comando ("apagar 42" registrava R$ 42,00 no
      aluguel).

    Duas respostas simultâneas: a fila avança por compare-and-swap
    (`db.advance_pending_action`) ANTES do registro, então a segunda thread
    perde o CAS, relê a fila já encurtada e responde o item seguinte em vez de
    registrar o mesmo de novo. Sem lock — ver o porquê em `db/pending.py`.
    """
    from utils_text import limpa_pontuacao_final, normalize_text, valor_perigoso

    resp_norm = normalize_text(text).strip()
    # Porta 3 da pergunta de valor (a numeração das quatro está em
    # `core/intent_router.py::abandona_pergunta_de_valor`), e até aqui a única
    # sem filtro nenhum: o `_extract_valor` sozinho gravava R$ 10,00 para "-10",
    # R$ 13.250,00 para "132 50" e para "132,50.", R$ 123.456,00 para
    # "1.23.456", `0.001` para "0,001" e `inf` (→ "erro interno") para 400 uns.
    #
    # A ACEITAÇÃO continua sendo o `_extract_valor` — o mesmo desta função antes
    # do PR, então "10 mil", "cinquenta" e "paguei 132 no mercado" seguem
    # entrando. O `valor_perigoso` só olha o que já foi aceito.
    # O texto LIMPO por causa do `_extract_valor`: sem a limpeza
    # "132,50. foi isso" registra R$ 13.250,00 e "1.234,56, foi isso" não
    # registra nada. O `valor_perigoso` limpa por dentro, não depende daqui.
    limpo = limpa_pontuacao_final(text or "")
    valor = _extract_valor(limpo)
    perigo = valor_perigoso(limpo, valor)

    if outro_comando:
        # Passo 1: o intent diz que isto nunca seria a resposta ("apagar 42",
        # "quanto gastei em 132"). Vem ANTES do valor de propósito — os dois
        # têm número, e o `_extract_valor` diria 42.
        #
        # CAS, não `clear_pending_action`: `pending_actions` é uma linha por
        # usuário e outra tarefa pode ter posto uma pergunta nova aqui entre a
        # leitura de `pending` e agora — que já apareceu na tela. Mesmo padrão
        # das portas 1 e 4.
        db.consume_pending_action(user_id, pending)
        return None

    # Duas respostas do mesmo usuário podem ser processadas em paralelo pelo
    # Discord (`discord_bot.py:122`, `on_message` async sem lock, em processo
    # separado do uvicorn). O webhook do WhatsApp sozinho não corre — enfileira
    # e um worker único consome. Sem o
    # compare-and-swap abaixo as duas leem a mesma fila, gravam o MESMO item
    # duas vezes e o segundo valor some sem uma palavra.
    #
    # Sem teto de tentativas, e sem contador. Perder o CAS significa que outra
    # tarefa mexeu na fila; as concorrentes são finitas, então quando elas
    # acabarem o nosso CAS vence. QUALQUER teto por tamanho de fila é errado na
    # premissa: o `_devolve_head` FAZ a fila crescer quando um registro falha,
    # então ela não só encolhe — foi por isso que as duas versões anteriores
    # (teto 4, e depois `len(fila) + 5`) descartavam o valor do usuário em
    # silêncio. A saída é a fila sumir, tratada logo abaixo.
    while True:
        payload = pending.get("payload") or {}
        queue: list[dict] = list(payload.get("queue") or [])
        if not queue:
            # Abandono, condicional: a fila acabou, mas outra tarefa pode ter
            # armado uma pergunta nova nesta linha entre a leitura e agora.
            db.consume_pending_action(user_id, pending)
            return None

        if resp_norm in _CANCEL_WORDS:
            db.consume_pending_action(user_id, pending)
            restantes = ", ".join(i.get("desc", "?") for i in queue)
            return f"❌ Beleza, deixei de lado: {restantes}."

        if perigo:
            # Fala do valor, mas o valor não serve. Recusa MANTENDO a pergunta
            # viva e a fila intacta (nada de CAS aqui — não avançamos nada):
            # apagar a pendência jogaria o usuário no fallback genérico e o
            # resto da fila sumiria com ela.
            recusa = ("O valor precisa ser maior que zero."
                      if perigo == "nao_positivo"
                      else "Não entendi o valor. Manda só o número, por exemplo: *132,50*")
            return f"{recusa}\n\n{_ask_value_question(queue[0])}"

        if valor is None or valor <= 0:
            # Não é um valor — o usuário mudou de assunto. Abandona a pendência e
            # deixa o roteador tratar a mensagem como um comando novo.
            db.consume_pending_action(user_id, pending)
            return None

        head, resto = queue[0], queue[1:]

        # Tira o head da fila ANTES de registrar: é isso que impede a outra
        # thread de registrar o MESMO item. Fila vazia = apaga a pendência; o
        # `add_from_entities` grava logo abaixo a dele ("categoria errada?").
        novo_payload = {"queue": resto, "platform": platform} if resto else None
        if not db.advance_pending_action(
                user_id, "multi_launch_values", payload, novo_payload,
                old_created_at=pending.get("created_at")):
            # Outra thread avançou a fila entre a leitura e agora. Relê e
            # reavalia: o item que sobrou é o próximo, não este.
            pending = db.get_pending_action(user_id)
            if not pending or pending.get("action_type") != "multi_launch_values":
                return None
            continue

        # Enquanto ainda há resto, suprime ofertas: `pending_actions` tem uma
        # linha só por usuário e a oferta apagaria a fila que o CAS acabou de
        # gravar. No último item, a oferta ainda pode aparecer, mas só com
        # criação condicional: se uma recuperação recriou a fila durante o
        # registro, a fila vence.
        try:
            resp = add_from_entities(
                user_id,
                tipo=head.get("tipo", "despesa"),
                valor=float(valor),
                alvo=head.get("desc"),
                nota=head.get("desc"),
                platform=platform,
                suppress_pending=bool(resto),
                conditional_pending=not bool(resto),
            )
        except Exception:
            # O item já saiu da fila (é o que impede a duplicação), mas o
            # trabalho não aconteceu — sem devolver, o lançamento some calado.
            # Não é hipótese: `check_can_create_launch` levanta
            # `PlanLimitExceeded` quando o usuário do Grátis bate o teto do mês
            # (`core/services/plan_service.py`) e quem captura é o
            # `core/handle_incoming.py`, que só responde o texto de upgrade.
            _devolve_head(user_id, head, platform)
            raise

        # RELÊ antes de perguntar: `resto` é do momento da reivindicação. Se
        # outra tarefa registrou itens enquanto esta demorava, perguntar pelo
        # `resto[0]` velho pede um valor de algo JÁ registrado — e a resposta
        # do usuário chega sem item correspondente na fila.
        depois = db.get_pending_action(user_id)
        fila_agora = []
        if depois and depois.get("action_type") == "multi_launch_values":
            fila_agora = (depois.get("payload") or {}).get("queue") or []
        if fila_agora:
            # Se a oferta de gasto fixo foi criada e outra tarefa a substituiu
            # por esta fila restaurada, o texto dela ficou órfão em `resp`:
            # sairiam DUAS perguntas incompatíveis ("responda sim ou não" e
            # "quanto foi X?"), e um "sim" não é valor — descartaria o
            # lançamento restaurado. A pendência que vale é a fila; o convite
            # sai do texto.
            resp = _sem_oferta_de_gasto_fixo(resp)
            return f"{resp}\n\n{_ask_value_question(fila_agora[0])}"
        # fila vazia: não re-arma multi_launch_values. O add_from_entities acima já
        # gravou o pending de "categoria errada?" (WhatsApp), que fica valendo.
        return resp

    return None




_OFERTA_GASTO_FIXO_RE = re.compile(
    r"\n\n💡 Você já lançou .*?Responda \*sim\* ou \*não\*\.", re.DOTALL)


def _sem_oferta_de_gasto_fixo(texto: str) -> str:
    """Tira o convite de gasto fixo de uma resposta já montada.

    Usado quando a pendência da oferta foi substituída por uma fila restaurada:
    o convite pede "sim ou não" e a fila pede um valor. Deixar os dois no mesmo
    texto faz o usuário responder "sim", que não é valor — e o item restaurado
    é descartado.
    """
    return _OFERTA_GASTO_FIXO_RE.sub("", texto)


def _devolve_head(user_id: int, head: dict, platform: str) -> None:
    """Põe `head` de volta na FRENTE da fila que existir agora.

    O item foi reivindicado (tirado da fila) antes de registrar, e o registro
    estourou — sem devolver, ele some. Restaurar o payload ANTIGO não serve:
    entre a reivindicação e a falha, outra thread pode ter avançado ou apagado
    a fila, e gravar o estado velho por cima ressuscitaria um item que ela já
    registrou. Por isso relemos e prependemos ao que estiver lá.

    CAS em laço porque a fila pode mudar entre a leitura e a escrita. A
    recuperação precisa ir até uma escrita condicional vencer; se ela desistir
    cedo, o item que já saiu da fila some.
    """
    while True:
        atual = db.get_pending_action(user_id)
        if atual and atual.get("action_type") != "multi_launch_values":
            # A linha está ocupada por OUTRA pendência — tipicamente a oferta
            # de "categoria errada?" que outra tarefa acabou de armar ao
            # terminar a fila. Sem desalojar, o insert condicional perde todas
            # as tentativas e o item some. Fila com dinheiro do usuário vale
            # mais que uma oferta de conveniência: desaloja, condicionado ao
            # que está lá, para não atropelar uma fila real.
            if db.advance_pending_action(
                    user_id, atual["action_type"], atual.get("payload") or {},
                    {"queue": [head], "platform": platform},
                    new_action_type="multi_launch_values",
                    old_created_at=atual.get("created_at")):
                return
            continue
        if not atual:
            # Condicional, não upsert: duas devoluções simultâneas veriam as
            # duas a fila vazia e a última apagaria a primeira. Quem perder a
            # inserção volta ao topo do laço e prepende na fila que a outra
            # acabou de criar.
            if db.create_pending_action_if_absent(
                    user_id, "multi_launch_values",
                    {"queue": [head], "platform": platform}):
                return
            continue
        antigo = atual.get("payload") or {}
        fila = [head] + list(antigo.get("queue") or [])
        if db.advance_pending_action(
                user_id, "multi_launch_values", antigo,
                {"queue": fila, "platform": antigo.get("platform", platform)},
                old_created_at=atual.get("created_at")):
            return


def _register_parsed(user_id: int, parsed: dict, fallback_note: str, platform: str) -> str:
    """Registra um lançamento já parseado por `parse_receita_despesa_natural`."""
    return add_from_entities(
        user_id,
        tipo=parsed["tipo"],
        valor=float(parsed["valor"]),
        alvo=parsed.get("alvo") or "",
        nota=parsed.get("nota") or fallback_note,
        categoria=parsed.get("categoria") or "outros",
        category_reason=parsed.get("category_reason"),
        criado_em=parsed.get("criado_em"),
        is_internal=parsed.get("is_internal_movement", False),
        platform=platform,
    )


def add(user_id: int, text: str, entities: dict, platform: str = "whatsapp") -> str:
    from core.handlers import credit as h_credit

    credit_response = h_credit.try_handle_natural_credit_purchase(user_id, text)
    if credit_response is not None:
        return credit_response

    # Múltiplos lançamentos na mesma mensagem ("gastei 500 no ifood e mais 800
    # no mercado") — separa e registra cada um. split_financial_transactions só
    # devolve >1 item quando detecta de fato mais de um lançamento. Pedaços com
    # verbo mas SEM valor ("... e paguei o aluguel") viram pergunta: o bot
    # registra o que deu, enfileira os que faltam valor e pergunta um a um.
    parts = split_financial_transactions(text)
    if len(parts) > 1:
        responses = []
        missing: list[dict] = []
        for part in parts:
            p = parse_receita_despesa_natural(user_id, part)
            if p:
                responses.append(_register_parsed(user_id, p, part, platform))
                continue
            info = describe_valueless_launch(part)
            if info:
                tipo, desc = info
                # Valor recorrente conhecido ("aluguel" que sempre é o mesmo) →
                # lança sozinho. Senão, enfileira pra perguntar.
                auto = register_if_recurring(user_id, tipo, desc, platform)
                if auto is not None:
                    responses.append(auto)
                else:
                    missing.append({"tipo": tipo, "desc": desc})
        if missing:
            db.set_pending_action(
                user_id, "multi_launch_values",
                {"queue": missing, "platform": platform},
            )
            question = _ask_value_question(missing[0])
            return "\n\n".join(responses + [question]) if responses else question
        if responses:
            return "\n\n".join(responses)
        # nenhum pedaço virou lançamento válido — cai no fluxo single abaixo

    parsed = parse_receita_despesa_natural(user_id, text)

    if parsed:
        if not (parsed.get("alvo") or "").strip():
            # Valor reconhecido, mas sem descrição nenhuma ("gastei cinquenta",
            # "gastei 50") — pergunta em vez de lançar direto em "outros" sem
            # contexto. A resposta é resolvida em
            # intent_router._resolve_clarification (branch launches.add, ramo
            # "já tínhamos o valor → resposta é a descrição").
            tipo_p = parsed["tipo"]
            verbo_q = "recebeu" if tipo_p == "receita" else "gastou"
            pergunta = f"🐷 Em que você {verbo_q} {fmt_brl(float(parsed['valor']))}?"
            db.set_pending_action(user_id, "clarification", {
                "intent": "launches.add",
                "entities": {"tipo": tipo_p, "valor": parsed["valor"]},
                "question": pergunta,
                "orig_text": text,
            })
            return pergunta
        return _register_parsed(user_id, parsed, text, platform)

    # Tem verbo + descrição mas falta o valor ("paguei o mercado", "gastei no
    # ifood") — describe_valueless_launch já detecta isso, mas até aqui só
    # era chamada dentro do laço de multi-lançamento; uma mensagem única com
    # esse padrão caía direto no fallback abaixo e virava "Não consegui
    # identificar o valor" em vez de perguntar. Mesmo pending "clarification"
    # do ramo de descrição faltando acima — intent_router._resolve_clarification
    # já sabe completar quando a resposta traz só o valor.
    info = describe_valueless_launch(text)
    if info:
        tipo_v, desc_v = info
        pergunta = f"🐷 Quanto foi {'de' if tipo_v == 'receita' else 'no'} *{desc_v}*?"
        db.set_pending_action(user_id, "clarification", {
            "intent": "launches.add",
            "entities": {"tipo": tipo_v},
            "question": pergunta,
            "orig_text": text,
        })
        return pergunta

    tipo = entities.get("tipo", "despesa")
    valor = float(entities.get("valor", 0))
    return add_from_entities(
        user_id,
        tipo=tipo,
        valor=valor,
        alvo=entities.get("alvo") or "",
        nota=text,
        categoria=entities.get("categoria"),
        category_reason="ai",
        criado_em=None,
        platform=platform,
    )


# ---------------------------------------------------------------------------
# propose_delete / undo
# ---------------------------------------------------------------------------

def propose_delete(user_id: int, launch_id: int) -> str:
    """Propõe apagar um lançamento. `launch_id` é o id interno (PK).

    O display usa o `user_seq` desse lançamento; se não conseguir resolver,
    cai pro id interno.
    """
    display_id = launch_id
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select user_seq from launches where id=%s and user_id=%s",
                    (launch_id, user_id),
                )
                row = cur.fetchone()
                if row and row.get("user_seq"):
                    display_id = int(row["user_seq"])
    except Exception:
        pass
    db.set_pending_action(
        user_id,
        "delete_launch",
        {"launch_id": int(launch_id), "display_id": int(display_id)},
    )
    return (
        f"⚠️ Isso vai apagar o lançamento **#{display_id}** e desfazer seus efeitos no saldo.\n"
        "Confirma? Responda **sim** ou **não**."
    )


def undo(user_id: int) -> str:
    # Último lançamento CRIADO (maior id), não o mais recente por data: "desfazer"
    # deve remover o que o usuário acabou de lançar, mesmo que seja retroativo
    # ("gastei 50 ontem" cria com criado_em no passado — list_launches ordena por
    # data e miraria o lançamento de hoje, apagando o errado).
    row = db.get_last_inserted_launch(user_id)
    if not row:
        return "Não há lançamentos para desfazer."
    last_id = int(row["id"])
    display_id = int(row.get("user_seq") or last_id)
    db.set_pending_action(
        user_id,
        "delete_launch",
        {"launch_id": last_id, "display_id": display_id},
    )
    tipo  = row.get("tipo", "")
    valor = fmt_brl(float(row.get("valor") or 0))
    return (
        f"⚠️ Desfazer o último lançamento: **#{display_id}** ({tipo} {valor})?\n"
        "Confirma? Responda **sim** ou **não**."
    )
