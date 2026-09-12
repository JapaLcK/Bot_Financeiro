"""
tests/test_portao_pendencias_interceptadas.py — o portão da CATEGORIA
"pendência consumida antes do `handle_incoming`".

Arquivo próprio porque `tests/test_wa_botao_velho_no_corte.py` está perto do teto
de 350 linhas (`tests/test_max_lines_python.py`) e porque o assunto é outro: lá
são CASOS (o cortado clica, o pagante clica), aqui é o PORTÃO que diz se a
categoria continua fechada.

**Por que ele existe**: neste PR a mesma categoria mordeu TRÊS vezes, e as três
pelo mesmo mecanismo — o buraco esteve sempre no ESCOPO da varredura, nunca no
predicado. Primeiro o portão dos laços proativos CONTAVA chamadas e estava
invertido; consertado para enumerar, ele olhava uma LISTA DE ARQUIVOS e não viu
`open_finance_proactive.py`; hoje varre a árvore. As pendências interceptadas
eram a quarta instância esperando: `_PENDENCIAS_QUE_ESCREVEM` foi escrito
LENDO o bloco à mão, e nada reprovava uma terceira interceptação.

`process_message` consome pendência ANTES de rotear, e o `_paywall_gate` mora
dentro do `handle_incoming`. Toda interceptação nova que escreva e não esteja no
conjunto é uma escrita sem gate — a pergunta de valor do boleto chama
`mark_bill_paid` e debita saldo.

CONTROLE DECLARADO (`docs/controles_declarados.md`)
────────────────────────────────────────────────────
As TRÊS injeções abaixo caem no mesmo nome de teste
(`test_toda_pendencia_interceptada_esta_declarada`), e por isso o que as separa
está escrito: é a MENSAGEM da asserção, e elas são bugs diferentes. Todas
medidas em 2026-09-11.

**(a) a interceptação NOVA, que é o caso que este portão existe para pegar** —
cole em `process_message`, ANTES da chamada de `handle_incoming`::

    if pending_recat and pending_recat.get("action_type") == "pagar_qualquer_coisa":
        _send_reply(reply_to, "ok")
        return

Reprova pela 1ª asserção::

    ['pagar_qualquer_coisa (linha 1010)'] é/são interceptada(s) e consumida(s)
    antes do `handle_incoming`, sem estar em `_PENDENCIAS_QUE_ESCREVEM` nem em
    `_ISENTAS_COM_RAZAO`.

Direção: escrita sem gate — a interceptação nova roda antes do `_paywall_gate` e
o cortado escreve por ela.

**(b) o nome tirado do conjunto** — em `wa_runtime`, remova `"bill_pay_amount"`
de `_PENDENCIAS_QUE_ESCREVEM`. Reprova pela **1ª** asserção também, e não pela
2ª: a interceptação continua no código e deixa de estar declarada. Direção: a
mesma de (a), por outro caminho — é como o gate do corte para de valer para uma
pendência que escreve.

**(c) o nome MORTO** — acrescente `"ja_saiu_do_codigo"` ao conjunto. Reprova
pela 2ª asserção::

    ['ja_saiu_do_codigo'] está/estão declarada(s) e não é/são interceptada(s)

Direção: custo e desinformação, não bug — o gate de produção paga uma consulta
por uma pendência que não existe mais. É também o alarme de que o predicado
deste portão parou de casar a forma do código.

**Positivo do par, VERDE sob a injeção (a)** (é o que o torna positivo): a mesma
interceptação colada DEPOIS da chamada de `handle_incoming` não reprova — `4
passed`. O portão é sobre a POSIÇÃO, e gatear o que já está atrás do gate seria
custo pago duas vezes, que é o defeito que a rodada anterior consertou.
  `test_interceptacao_depois_do_roteamento_nao_e_da_categoria`

**Este portão NÃO tem controle por "apague o conserto"**, e isso é de propósito:
ele não cobre um conserto, cobre uma DECLARAÇÃO. Desligá-lo é apagar o próprio
teste.

O QUE ELE NÃO PEGA (§3: "que classe de bug esta verificação nunca pegaria?")
────────────────────────────────────────────────────────────────────────────
1. **Interceptação que não compara `action_type` contra um literal** — um
   dicionário de handlers, um `match`, uma decisão pelo formato do `payload`.
   Duas grafias estão cobertas (a inline e a por variável local, esta última
   acrescentada depois de medir que ela passava verde); uma terceira forma não
   está. O portão denuncia a cegueira TOTAL — enumeração vazia reprova pela 2ª
   asserção —, mas não a PARCIAL em forma ainda não prevista.
2. **Outros adaptadores.** Ele lê `wa_runtime.process_message` e mais nada.
   Medido: `adapters/discord/cogs/general_cog.py:128` também lê
   `pending["action_type"]` (por subscrito, não por `.get`) e está FORA deste
   portão. Não foi ampliado aqui de propósito — o Discord é canal secundário e
   o caminho dele não foi levantado; fica como escopo declarado, não como
   cobertura suposta.
3. **Se a pendência realmente escreve.** O portão exige DECLARAÇÃO, não
   verifica a escrita. Alguém pode pôr um `action_type` que escreve em
   `_ISENTAS_COM_RAZAO` com uma razão falsa. O que ele garante é que a decisão
   seja TOMADA e fique escrita — que é o passo que faltou nas três vezes em que
   esta categoria mordeu.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from adapters.whatsapp.wa_runtime import _PENDENCIAS_QUE_ESCREVEM

FONTE = pathlib.Path(__file__).resolve().parent.parent / "adapters" / "whatsapp" / "wa_runtime.py"

# Interceptações que rodam antes do `handle_incoming` e NÃO escrevem — cada uma
# com a razão escrita, que é o que a torna uma decisão em vez de um esquecimento.
# **Hoje está vazio, e esse é o estado honesto**: as duas interceptações que
# existem escrevem, e as duas estão em `_PENDENCIAS_QUE_ESCREVEM`.
#
# Ela existe pelo próximo autor, não por especulação: sem este escape, uma
# interceptação legítima que só LEIA teria de entrar num conjunto chamado
# "QUE_ESCREVEM" — nome errado — ou o portão seria apagado. Mora aqui e não na
# produção porque o runtime não precisa dela para decidir nada; só o portão
# precisa.
_ISENTAS_COM_RAZAO: dict[str, str] = {}


def _interceptadas_antes_do_roteamento(fonte: str) -> dict[str, int]:
    """{action_type: linha} de toda pendência comparada dentro de
    `process_message` ANTES da chamada de `handle_incoming`.

    O predicado é `<algo>.get("action_type") == "<literal>"`, que é a forma que
    uma interceptação TEM de ter para escolher o ramo. O nome da variável não
    entra (`pending_recat` é onde ela mora hoje, não o que ela é), e a
    comparação contra um NOME — o próprio `in _PENDENCIAS_QUE_ESCREVEM` — é
    pulada: aquilo é o gate, não uma interceptação.

    **A fronteira é a chamada de `handle_incoming`, e ela é o ponto todo.**
    Depois dela a pendência é resolvida pelo `route()`, que roda atrás do
    `_paywall_gate` e já está coberto. Antes dela, não há gate nenhum senão o
    que `wa_runtime` chamar à mão.
    """
    arvore = ast.parse(fonte)
    pm = next(f for f in ast.walk(arvore)
              if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
              and f.name == "process_message")
    roteamento = min(
        (n.lineno for n in ast.walk(pm)
         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
         and n.func.id == "handle_incoming"),
        default=None,
    )
    assert roteamento is not None, (
        "não achei a chamada de `handle_incoming` em `process_message` — sem ela "
        "este portão não sabe onde fica a fronteira e não mede nada")

    def _le_o_action_type(e: ast.AST) -> bool:
        return (isinstance(e, ast.Call)
                and isinstance(e.func, ast.Attribute) and e.func.attr == "get"
                and bool(e.args) and isinstance(e.args[0], ast.Constant)
                and e.args[0].value == "action_type")

    # DUAS grafias, e a segunda não é zelo: medido em 2026-09-11, um
    # `tipo = pending.get("action_type")` seguido de `if tipo == "novo":` passava
    # VERDE com o predicado só da forma inline — a interceptação nova ficava
    # invisível enquanto as antigas mantinham a enumeração cheia, então nem a 2ª
    # asserção acusava. Cegueira PARCIAL não dispara o alarme da cegueira total.
    apelidos = {alvo.id for n in ast.walk(pm)
                if isinstance(n, ast.Assign) and _le_o_action_type(n.value)
                for alvo in n.targets if isinstance(alvo, ast.Name)}

    achadas: dict[str, int] = {}
    for n in ast.walk(pm):
        if not isinstance(n, ast.Compare) or n.lineno >= roteamento:
            continue
        alvo = n.left
        if not (_le_o_action_type(alvo)
                or (isinstance(alvo, ast.Name) and alvo.id in apelidos)):
            continue
        for comparador in n.comparators:
            if isinstance(comparador, ast.Constant) and isinstance(comparador.value, str):
                achadas.setdefault(comparador.value, n.lineno)
    return achadas


def test_toda_pendencia_interceptada_esta_declarada():
    """Duas asserções, e elas falham por motivos opostos.

    • a primeira pega a interceptação que existe no código e NÃO está declarada
      — seja porque é nova, seja porque alguém tirou o nome do conjunto. É o
      lado que vale dinheiro: escrita antes do gate;
    • a segunda pega o nome MORTO, declarado sem interceptação correspondente.
      Ele não causa bug, mas faz o gate de produção pagar uma consulta por uma
      pendência que não existe mais — e, sobretudo, é o alarme de que o
      predicado deste portão parou de casar a forma do código (ver
      `test_o_portao_reprova_quando_a_forma_do_codigo_muda`).

    Quem acrescentar interceptação legítima tem DUAS linhas para mexer: a
    interceptação e um dos dois conjuntos. É de propósito — a declaração é o
    registro de que alguém decidiu, e é exatamente o passo que faltou nas três
    vezes em que esta categoria mordeu.
    """
    achadas = _interceptadas_antes_do_roteamento(FONTE.read_text(encoding="utf-8"))
    declaradas = set(_PENDENCIAS_QUE_ESCREVEM) | set(_ISENTAS_COM_RAZAO)

    nao_declaradas = sorted(f"{nome} (linha {achadas[nome]})"
                            for nome in achadas if nome not in declaradas)
    assert not nao_declaradas, (
        f"{nao_declaradas} é/são interceptada(s) e consumida(s) antes do "
        "`handle_incoming`, sem estar em `_PENDENCIAS_QUE_ESCREVEM` nem em "
        "`_ISENTAS_COM_RAZAO`. Se escreve, entra no primeiro (o gate do corte "
        "passa a valer). Se não escreve, entra no segundo COM a razão.")

    mortas = sorted(declaradas - set(achadas))
    assert not mortas, (
        f"{mortas} está/estão declarada(s) e não é/são interceptada(s) em "
        "`process_message` — ou a interceptação saiu e o nome ficou, ou o "
        "predicado deste portão parou de casar a forma do código")


def test_o_portao_reprova_quando_a_forma_do_codigo_muda():
    """O portão não pode ficar VERDE quando deixa de enxergar.

    É a pergunta do §3 virada em caso: se alguém reescrever as interceptações
    numa forma que o predicado não casa (um `match`, um dicionário de handlers),
    a enumeração volta vazia. Sem este caso, vazio ⊆ declaradas passaria na
    primeira asserção e o portão morreria em silêncio — a patologia exata que
    ele existe para não repetir.

    Mede com uma fonte SINTÉTICA, e não injetando no arquivo real: o que está
    sob teste aqui é o portão, não o runtime.
    """
    fonte_cega = (
        "def process_message(message):\n"
        "    accao = pending.tipo\n"                      # forma que o predicado não casa
        "    if accao == 'bill_pay_amount':\n"
        "        return\n"
        "    handle_incoming(incoming)\n"
    )
    cegas = _interceptadas_antes_do_roteamento(fonte_cega)
    assert cegas == {}, cegas

    # E é a SEGUNDA asserção do portão que reprova esse estado, não a primeira:
    # aplicada a esta enumeração vazia, ela acusa as declaradas como MORTAS.
    # Escrito contra `cegas`, e não contra `set()`, de propósito — a versão com
    # `set()` daria o mesmo verde se a enumeração passasse a devolver qualquer
    # coisa, que é medir a si mesma.
    assert set(_PENDENCIAS_QUE_ESCREVEM) - set(cegas), (
        "o portão ficaria verde com a enumeração cega — é o modo de falha que "
        "este caso existe para impedir")


def test_interceptacao_depois_do_roteamento_nao_e_da_categoria():
    """POSITIVO: o portão discrimina por POSIÇÃO, não por existir `action_type`.

    Depois do `handle_incoming` a pendência já passou pelo `_paywall_gate`.
    Reprovar ali empurraria o próximo autor a gatear de novo o que já está
    gateado — o custo em dobro que a rodada anterior acabou de tirar do caminho
    quente. Sem este caso, um portão que ignorasse a linha passaria igual e
    ninguém veria a diferença.
    """
    fonte = (
        "def process_message(message):\n"
        "    if pending.get('action_type') == 'antes_do_gate':\n"
        "        return\n"
        "    outs = handle_incoming(incoming)\n"
        "    if pending.get('action_type') == 'depois_do_gate':\n"
        "        return\n"
    )
    assert set(_interceptadas_antes_do_roteamento(fonte)) == {"antes_do_gate"}


def test_o_portao_enxerga_as_duas_grafias_do_action_type():
    """A cegueira PARCIAL, que é pior que a total e não dispara o alarme dela.

    Medido antes de existir este caso: com o predicado só da forma inline, uma
    interceptação escrita como `tipo = pending.get("action_type")` + `if tipo ==
    "novo":` passava VERDE — a nova ficava invisível e as antigas mantinham a
    enumeração cheia, então nem a asserção do nome morto acusava. Cegueira total
    o portão denuncia (`test_o_portao_reprova_quando_a_forma_do_codigo_muda`);
    parcial, não.

    As duas grafias no mesmo fonte, para que casar uma não desculpe perder a
    outra.
    """
    fonte = (
        "def process_message(message):\n"
        "    tipo = pending.get('action_type')\n"
        "    if tipo == 'pela_variavel':\n"
        "        return\n"
        "    if pending.get('action_type') == 'inline':\n"
        "        return\n"
        "    handle_incoming(incoming)\n"
    )
    assert set(_interceptadas_antes_do_roteamento(fonte)) == {"pela_variavel", "inline"}


def test_o_portao_acha_a_fronteira_ou_reprova():
    """Sem a chamada de `handle_incoming` não há fronteira, e um portão sem
    fronteira aprovaria tudo. Ele levanta em vez de devolver `{}`."""
    with pytest.raises(AssertionError, match="handle_incoming"):
        _interceptadas_antes_do_roteamento(
            "def process_message(message):\n"
            "    if pending.get('action_type') == 'x':\n"
            "        return\n"
        )
