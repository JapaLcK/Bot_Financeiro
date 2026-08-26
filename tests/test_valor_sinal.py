"""A TABELA-VERDADE do sinal, em forma executável.

Fonte de verdade única (§0.7) da regra que já foi remendada TRÊS vezes neste
PR — captura do `_VALOR_RE`, depois `endswith("-")`, depois "encosta nos
dígitos / prefixo inteiro / termina". Cada remendo consertou uma forma e
quebrou outra; o último aceitava `foi - 10` como R$ 10,00 positivo enquanto
recusava `foi -10`.

A tabela em prosa está no docstring de `utils_text._sinal_negativo`. Aqui ela é
enumerada célula a célula, sobre os eixos que o remendo tratava como um só:

    ANTES do bloco numérico : vazio · espaço · moeda · enchimento · verbo ·
                              outro traço · combinações · palavra de CONTEÚDO
    DEPOIS do bloco numérico: vazio · espaço · unidade · prosa · `)` ·
                              traço terminal
    TRAÇO                   : ASCII + as 9 grafias Unicode do `_TRACOS`,
                              mais `menos` por extenso
    COLADO × SEPARADO       : `-10` e `- 10` — MESMO veredito em toda linha,
                              e é justamente aqui que a rodada 5 quebrou

Este arquivo não toca no banco: exercita o predicado direto. O caminho real
(as quatro portas, pelo `handle_incoming`/`process_message`) está em
`tests/test_bill_amount_pending.py` e `tests/test_full_handler_smoke.py`.
"""
from __future__ import annotations

import pytest

import utils_text
from utils_text import limpa_pontuacao_final, parse_money, valor_perigoso

# As dez grafias que viram `-`. O bloco U+2010–U+2015 inteiro, o menos
# matemático e os dois de largura plena.
TRACOS = ["-"] + [chr(c) for c in range(0x2010, 0x2016)] + ["−", "﹣",
                                                            "－"]

# --- Eixo ANTES: o que precede o traço, e o veredito de cada classe ---------
ANTES = [
    ("", "sinal"),                 # linha 1  "-10"
    ("  ", "sinal"),               # linha 2  "  - 10"
    ("r$ ", "sinal"),              # linha 3  "r$ -10"
    ("R$", "sinal"),               # linha 3  "R$-10" (sem espaço)
    ("foi ", "sinal"),             # linha 4  enchimento
    ("uns ", "sinal"),
    ("deu ", "sinal"),
    ("ai ", "sinal"),
    ("paguei ", "sinal"),          # linha 5  verbo de lançamento
    ("gastei ", "sinal"),
    ("recebi ", "sinal"),
    ("- ", "sinal"),               # linha 6  outro traço
    ("foi r$ ", "sinal"),          # linha 7  combinação
    ("acho que foi ", "sinal"),
    ("luz ", "prosa"),             # linha 8  palavra de CONTEÚDO
    ("mercado ", "prosa"),
    ("foi luz ", "prosa"),         # conteúdo em QUALQUER posição antes
    ("conta de luz ", "prosa"),
]

# --- Eixo DEPOIS do NÚMERO: não muda o veredito quando o traço vem antes ----
DEPOIS = ["", " ", " reais", " pila", " da luz", " no mercado"]


def _celulas_traco_antes():
    for antes, classe in ANTES:
        for traco in TRACOS:
            for cola in ("", " "):          # colado × separado por espaço
                for depois in DEPOIS:
                    texto = f"{antes}{traco}{cola}10{depois}"
                    yield texto, ("nao_positivo" if classe == "sinal" else None)


# --- Eixo DEPOIS: o traço vem DEPOIS do número ------------------------------
# Só a linha 9 (nada depois do traço) é sinal; qualquer conteúdo é prosa.
DEPOIS_DO_TRACO = [
    ("", "sinal"), (" ", "sinal"), ("  ", "sinal"),
    (" luz", "prosa"), (" da luz", "prosa"), (" reais", "prosa"),
    (" no mercado", "prosa"),
]
PREFIXOS = ["", "paguei ", "luz ", "acho que foi "]


def _celulas_traco_depois():
    for prefixo in PREFIXOS:
        for traco in TRACOS:
            for cola in ("", " "):
                for depois, classe in DEPOIS_DO_TRACO:
                    texto = f"{prefixo}132{cola}{traco}{depois}"
                    yield texto, ("nao_positivo" if classe == "sinal" else None)


# --- `menos` por extenso: mesmas regras do traço, com duas aproximações -----
MENOS = [
    ("menos 10", "nao_positivo"),
    ("foi menos 10", "nao_positivo"),
    ("paguei menos 10", "nao_positivo"),
    ("menos10", "nao_positivo"),          # sem espaço: `\b` não pegava
    ("menos R$ 10", "nao_positivo"),
    ("luz menos 10", None),               # conteúdo antes → prosa (linha 8)
    ("mais ou menos 10", None),           # aproximação, não sinal
    ("foi mais ou menos 10", None),
    ("menos de 10", None),                # "abaixo de 10"
    ("menos que 10", None),
    ("menosprezei 10", None),             # não é a palavra
]

# --- Parênteses: negativo contábil ------------------------------------------
PARENTESES = [
    ("(10)", "nao_positivo"),
    ("foi (10)", "nao_positivo"),
    ("R$ (10)", "nao_positivo"),
    ("(- 10)", "nao_positivo"),
    ("(10", None),                        # sem fechar não é notação
]

# --- Formas SEM traço que a tabela não pode passar a recusar ----------------
SEM_TRACO_ACEITA = [
    "132", "paguei 132", "R$ 132,50", "132, 50", "10 mil", "1 500",
    "12 345", "1 000 000", "1.234,56", "0,50", "132.", "paguei 132. foi isso",
    "1.234,56, foi isso", "132. da luz", "foi 132; da luz", "132 e 50",
]

# --- A 7ª forma: separador decimal SEM parte inteira ------------------------
# `parse_money(",50")` é 50.0 — R$ 50,00 no lugar de R$ 0,50, 100x. Achado
# nesta rodada, não pelo revisor.
SEM_PARTE_INTEIRA = [",50", ".50", "R$ ,50", "R$ .50", "foi ,50", "paguei .50"]

# --- As outras formas de `nao_entendi`, para os controles negativos ---------
AMBIGUAS = ["132 50", "paguei 132 50", "1.23.456", "paguei 1.23.456 da luz",
            "1.2.3", "132 50 reais"]


def _perigo(texto: str) -> str | None:
    return valor_perigoso(texto, parse_money(limpa_pontuacao_final(texto)))


# ---------------------------------------------------------------------------
# A tabela
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto,esperado", list(_celulas_traco_antes()))
def test_tabela_traco_antes_do_numero(texto, esperado):
    assert _perigo(texto) == esperado, repr(texto)


@pytest.mark.parametrize("texto,esperado", list(_celulas_traco_depois()))
def test_tabela_traco_depois_do_numero(texto, esperado):
    assert _perigo(texto) == esperado, repr(texto)


@pytest.mark.parametrize("texto,esperado", MENOS + PARENTESES)
def test_tabela_menos_e_parenteses(texto, esperado):
    assert _perigo(texto) == esperado, repr(texto)


@pytest.mark.parametrize("texto", SEM_TRACO_ACEITA)
def test_tabela_sem_traco_nao_vira_perigoso(texto):
    assert _perigo(texto) is None, repr(texto)


@pytest.mark.parametrize("texto", SEM_PARTE_INTEIRA)
def test_separador_decimal_sem_parte_inteira_repergunta(texto):
    """7ª forma. Sem a guarda o valor é 100x maior — a asserção do meio mede."""
    assert parse_money(limpa_pontuacao_final(texto)) == 50.0, texto
    assert _perigo(texto) == "nao_entendi", repr(texto)


@pytest.mark.parametrize("texto", AMBIGUAS)
def test_tabela_digitacao_ambigua(texto):
    assert _perigo(texto) == "nao_entendi", repr(texto)


# ---------------------------------------------------------------------------
# Os reprodutores exatos do P2 desta rodada, em linha própria: com enchimento
# ou verbo na frente e o sinal separado por espaço, `cru` terminava em `"- "` e
# `antes` em `"foi -"` — os dois escapavam das condições empilhadas.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto", ["foi - 10", "deu − 10", "uns - 10",
                                   "paguei - 10", "gastei - 10", "ai - 10",
                                   "acho que foi - 10", "foi r$ - 10"])
def test_regressao_enchimento_com_sinal_separado(texto):
    assert _perigo(texto) == "nao_positivo", repr(texto)


# ---------------------------------------------------------------------------
# Controles negativos, um por CLASSE de célula. Cada um desliga UMA regra e a
# asserção é dupla: as células daquela classe caem, as das OUTRAS classes não.
# ---------------------------------------------------------------------------

def test_controle_negativo_regra_do_sinal_desligada(monkeypatch):
    """Sem a tabela do sinal, TODA célula de sinal vira aceita.

    E nenhuma célula de ponto/espaço/parte-inteira muda — é isso que separa a
    regra do sinal das outras três.
    """
    monkeypatch.setattr(utils_text, "_sinal_negativo", lambda antes, depois: False)

    sinal = [t for t, e in list(_celulas_traco_antes()) + list(_celulas_traco_depois())
             if e == "nao_positivo"]
    assert sinal, "a tabela ficou sem célula de sinal"
    ainda_recusados = [t for t in sinal if _perigo(t) is not None]
    assert not ainda_recusados, ainda_recusados[:5]

    # as outras classes seguem intactas
    for t in AMBIGUAS + SEM_PARTE_INTEIRA:
        assert _perigo(t) == "nao_entendi", t
    for t in ("0", "0,001"):
        assert _perigo(t) == "nao_positivo", t


def test_controle_negativo_regra_de_prosa_desligada(monkeypatch):
    """Volta o "traço de qualquer lado = sinal": as células de PROSA caem.

    Foi a regra da rodada 4, e ela recusava `luz - 132` e `132 — luz`, que a
    `main` paga.
    """
    monkeypatch.setattr(
        utils_text, "_sinal_negativo",
        lambda antes, depois: "-" in antes or depois.lstrip().startswith("-"))

    prosa = [t for t, e in list(_celulas_traco_antes()) + list(_celulas_traco_depois())
             if e is None]
    assert prosa, "a tabela ficou sem célula de prosa"
    caidos = [t for t in prosa if _perigo(t) == "nao_positivo"]
    assert len(caidos) == len(prosa), (len(caidos), len(prosa), prosa[:3])

    # ponto e espaço não dependem desta regra
    for t in AMBIGUAS + SEM_PARTE_INTEIRA:
        assert _perigo(t) == "nao_entendi", t


def test_controle_negativo_colado_e_separado_como_casos_diferentes(monkeypatch):
    """A formulação DOS TRÊS REMENDOS, reinjetada: `foi - 10` volta a passar.

    `cru.endswith("-")` (colado) + prefixo inteiro + traço terminal. É a versão
    que estava no branch antes desta rodada, e o controle prova que a diferença
    entre ela e a tabela não é cosmética.
    """
    import re as _re

    def remendo(antes, depois):
        limpo = _re.sub(r"r\$", "", antes).strip()
        return (antes.endswith("-") or limpo == "-" or depois.rstrip() == "-"
                or (antes.rstrip().endswith("(") and depois.lstrip().startswith(")")))

    monkeypatch.setattr(utils_text, "_sinal_negativo", remendo)

    passam = [t for t in ("foi - 10", "deu − 10", "uns - 10", "paguei - 10")
              if _perigo(t) is None]
    assert passam == ["foi - 10", "deu − 10", "uns - 10", "paguei - 10"], passam


def test_controle_negativo_lista_de_cinco_tracos(monkeypatch):
    """A lista de cinco grafias deixava `‒` (U+2012) e `―` (U+2015) passarem."""
    cinco = str.maketrans({"−": "-", "–": "-", "—": "-",
                           "‐": "-", "‑": "-"})
    monkeypatch.setattr(utils_text, "_TRACOS", cinco)

    assert _perigo("‒10") is None       # figure dash escapava
    assert _perigo("―10") is None       # horizontal bar escapava
    assert _perigo("–10") == "nao_positivo"   # en dash já era pego


# ---------------------------------------------------------------------------
# §0.7: as listas duplicadas comparadas por teste
# ---------------------------------------------------------------------------

def test_enchimento_do_valor_re_vem_da_mesma_lista():
    """O `_VALOR_RE` da porta 1 e o `_sinal_negativo` dividem `_ENCHIMENTO`."""
    import core.handlers.bills as bills

    assert bills._ENCHIMENTO is utils_text._ENCHIMENTO
    for palavra in utils_text._ENCHIMENTO_PALAVRAS:
        assert palavra in utils_text._SEM_CONTEUDO, palavra
        assert bills._VALOR_RE.fullmatch(f"{palavra} 132"), palavra


def test_verbos_de_lancamento_nao_divergiram_das_outras_duas_copias():
    """Duplicação inevitável (§0.7) → um teste compara as três.

    `core.intent_router` corta os MESMOS 12 do prefixo da descrição;
    `parsers._extract_target_after_amount` usa um SUPERSET, porque lá o
    trabalho é outro (tirar o verbo de um texto que já tem valor).
    """
    import inspect
    import re

    import core.intent_router as router
    import parsers

    def verbos(fonte: str) -> set[str]:
        m = re.search(r"\^\\s\*\(([a-z|]+)\)\\b", fonte)
        assert m, fonte[:200]
        return set(m.group(1).split("|"))

    nossos = set(utils_text._VERBOS_LANCAMENTO)
    do_router = verbos(inspect.getsource(router._resolve_clarification))
    do_parsers = verbos(inspect.getsource(parsers._extract_target_after_amount))

    assert nossos == do_router, nossos ^ do_router
    assert nossos <= do_parsers, nossos - do_parsers
