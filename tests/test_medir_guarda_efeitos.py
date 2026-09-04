"""O `--autoteste` do `scripts/medir_guarda_efeitos_214.py` roda na suíte.

Aquele script é o que o dono roda contra produção para decidir o #214, e o
`--autoteste` é a ÚNICA prova de que ele DISCRIMINA (a guarda congelada do
`b4d0085` × a guarda viva, importada de `db/accounts.py`). Ele quebra sempre que
um predicado da guarda viva muda de veredito — e quebrou: o caso
`bill_id: "93"` esperava `recusa`, o `13fb792` passou a aceitar string de
dígitos, e o script ficou VERMELHO por dois commits sem ninguém saber, porque
nenhum teste o importava (`grep -rn "medir_guarda_efeitos" tests/` dava zero).

Este arquivo conserta a FRAGILIDADE, não o caso. Derivar o esperado da guarda
viva deixaria o autoteste tautológico (mediria o código contra ele mesmo); a
lista à mão é o valor dele. O que faltava era ela falhar junto com a suíte, e
não só no terminal do dono.

Quando o #214 for decidido e o script apagado, este arquivo vai junto.
"""
from __future__ import annotations

import importlib.util
import io
import logging
import pathlib
import subprocess
import sys

_CAMINHO = (pathlib.Path(__file__).resolve().parent.parent
            / "scripts" / "medir_guarda_efeitos_214.py")


def _carregar():
    spec = importlib.util.spec_from_file_location("medir_guarda_efeitos_214", _CAMINHO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_autoteste_do_script_de_medicao_passa():
    _carregar()._autoteste()  # `assert` interno: qualquer veredito mudado sai aqui


def _carregar_com_fronteira_isolada():
    """`executar` instala 3 hooks GLOBAIS do processo (`sys.excepthook`,
    `sys.unraisablehook`, `logging.disable`). Numa suíte isso é veneno: o
    `excepthook` chama `os._exit(5)` e mataria o pytest calado. Devolve
    (modulo, restaurar)."""
    modulo = _carregar()
    hooks = (sys.excepthook, sys.unraisablehook)

    def restaurar():
        sys.excepthook, sys.unraisablehook = hooks
        logging.disable(logging.NOTSET)

    return modulo, restaurar


def _payload_no_contexto():
    """1º Ctrl-C com uma recusa (mensagem = payload cru) no `__context__`."""
    try:
        raise ValueError("efeitos que não sei reverter: ['cliente_joao_12345678900']")
    except ValueError:
        raise KeyboardInterrupt


def test_fronteira_engole_a_falha_do_proprio_tratador(capsys):
    """2º Ctrl-C DURANTE o aviso do 1º — o gatilho que o `except` da fronteira,
    sozinho, não pega: a exceção nasce DENTRO dele, escapa, e o traceback padrão
    do interpretador imprime a cadeia inteira, com o payload na mensagem.

    Controle negativo medido: com o `try/except BaseException` de `_calado`
    desligado, a exceção escapa e `codigo` vira a string `'escapou: ...'`. O
    `except BaseException` daqui existe para isso — sem ele o `KeyboardInterrupt`
    ABORTA a sessão inteira do pytest, e o relatório sai `1 passed` (verde de
    mentira) em vez de `1 failed`.
    """
    modulo, restaurar = _carregar_com_fronteira_isolada()

    class StderrQuebrado(io.TextIOBase):
        def write(self, _texto):
            raise KeyboardInterrupt

    real = sys.stderr
    sys.stderr = StderrQuebrado()
    try:
        codigo = modulo.executar(_payload_no_contexto)
    except BaseException as escapou:  # noqa: BLE001 — só o TIPO, nunca `str`
        codigo = f"escapou: {type(escapou).__name__}"
    finally:
        sys.stderr = real
        restaurar()

    assert codigo == 5, "nem o aviso saiu: o codigo de saida e o unico aviso"
    capturado = capsys.readouterr()
    assert "12345678900" not in capturado.out + capturado.err


def test_stream_sem_reconfigure_nao_mata_a_medicao():
    """CONTROLE POSITIVO: o caminho legítimo continua produzindo o relatório.

    `io.StringIO` não tem `reconfigure` — nem o stream do pytest, nem o de um
    console de IDE. Chamar `stream.reconfigure` direto estourava `AttributeError`
    na PRIMEIRA linha de `executar`: rc=3, a medição nunca começava, e uma
    bateria inteira saía "limpa" sem ter medido nada.
    """
    modulo, restaurar = _carregar_com_fronteira_isolada()
    saida = io.StringIO()
    real = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = saida
    linhas = [("despesa", {"delta_conta": -50}),
              ("despesa", {"delta_conta": -50, "cliente_joao_12345678900": 1})]
    try:
        codigo = modulo.executar(
            lambda: modulo.relatar(*modulo.medir(iter(linhas)), "read_only=on"))
    finally:
        sys.stdout, sys.stderr = real
        restaurar()

    texto = saida.getvalue()
    assert codigo == 0, texto[:200]
    assert "linhas de 'launches' examinadas: 2" in texto
    assert "chave_desconhecida" in texto   # a medição DISCRIMINOU, não só rodou
    assert "12345678900" not in texto


# Processo separado: o `sys.excepthook` do script chama `os._exit`, que mataria
# o pytest. O `_calado` é anulado de propósito — o hook é o cinto para o escape
# que a enumeração da fronteira NÃO previu, e sem anular não há como alcançá-lo.
_ESCAPE_ALEM_DO_TRATADOR = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("m", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m._calado = lambda aviso, codigo: aviso()      # tira a rede do tratador
m._relatar_falha = lambda exc: 1 // 0          # e faz o tratador falhar
def estoura():
    raise ValueError("efeitos que nao sei reverter: ['cliente_joao_12345678900']")
sys.exit(m.executar(estoura))
"""


def test_escape_alem_do_tratador_nao_imprime_nada(tmp_path):
    """Controle negativo medido: sem `sys.excepthook = _encerrar_calado` o
    processo sai 1 e o traceback padrão leva o payload para o stderr do dono."""
    roteiro = tmp_path / "escape.py"
    roteiro.write_text(_ESCAPE_ALEM_DO_TRATADOR)
    fim = subprocess.run([sys.executable, str(roteiro), str(_CAMINHO)],
                         capture_output=True, text=True, timeout=60)

    assert "12345678900" not in fim.stdout + fim.stderr
    assert fim.stdout == "" and fim.stderr == ""
    assert fim.returncode == 5, "o codigo de saida e o unico aviso que sobra"


# ── o recorte do "apagar tudo": duas fontes, um teste (§0.7) ────────────────
#
# O script decide o bloco ('tudo' × 'singular') e o `escopo_conta_corrente` da
# guarda a partir de `_tipos_apagar_tudo()`; quem apaga de verdade é
# `_CONTA_CORRENTE_LAUNCH_FILTER`, em SQL. Enquanto forem duas expressões, uma
# tem de ser conferida contra a outra — senão o script volta a divergir calado,
# que foi o que aconteceu: com o literal `("despesa","receita")` congelado, a
# linha legada saía no bloco 'singular' com veredito "apaga" onde o código real
# devolve `kept_unsafe`/`fora_do_escopo`.
#
# TETO: isto mede o eixo `tipo`. Um termo que não seja de `tipo` no filtro (um
# `and is_internal_movement = false`) passaria por aqui — todas as linhas
# semeadas nascem com o default da coluna.
_TIPOS_SEMEADOS = (
    "despesa", "receita", "saida", "entrada",
    "aporte_investimento", "deposito_caixinha", "saque_caixinha",
    "resgate_investimento", "criar_caixinha",
)


def test_recorte_do_apagar_tudo_bate_com_o_sql(user_id):
    """Controle negativo: recongele `_tipos_apagar_tudo` em
    `("despesa","receita")` e o SQL devolve 'saida'/'entrada' a mais — vermelho.
    Controle positivo embutido: os tipos de caixinha/investimento continuam
    FORA dos dois lados, então o teste não passa num filtro que pega tudo."""
    import db
    from db.accounts import _CONTA_CORRENTE_LAUNCH_FILTER

    with db.get_conn() as conn, conn.cursor() as cur:
        for tipo in _TIPOS_SEMEADOS:
            cur.execute(
                "insert into launches (user_id, tipo, valor, categoria, nota) "
                "values (%s, %s, 1, 'outros', 'recorte')",
                (user_id, tipo),
            )
        cur.execute(
            f"select tipo from launches where user_id=%s "
            f"and {_CONTA_CORRENTE_LAUNCH_FILTER}",
            (user_id,),
        )
        do_sql = {r["tipo"] for r in cur.fetchall()}

    assert do_sql == set(_carregar()._tipos_apagar_tudo()), do_sql
    assert do_sql < set(_TIPOS_SEMEADOS), "o filtro passou a pegar TODO tipo"
