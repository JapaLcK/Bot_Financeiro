"""F2: o prompt só cita o que existe no código, o ERROR_MSG tem uma fonte só e o ADENDO_CARDS bate com os
cards e com as tools do app. Sem banco: import + ast.

Pontos cegos declarados (nome que EXISTE mas é o errado passa em tudo isto):
- trocar `get_largest_expenses` por `delete_launch` no prompt (as duas existem; o teste de canal só vê
  o app × WhatsApp);
- trocar `aviso_limite` por outro campo que o `launches.py` também tem (ex.: `motivos`);
- `ate` citado como ISO em vez de AAAA-MM.
"""
import ast
import re
from pathlib import Path

from core.services.ai_chat import cards, runner, system_prompt
from core.services.ai_chat.system_prompt import ADENDO_CARDS, ADENDO_CARDS_METAS, FORMATO_WHATSAPP, SYSTEM_PROMPT
from core.services.ai_chat.tools import OCULTAS_NO_APP, SCHEMAS, SCHEMAS_APP
from core.services.ai_chat.tools.metas import schemas_com_metas

RAIZ = Path(__file__).resolve().parent.parent
PASTA = RAIZ / "core/services/ai_chat"
FONTES = [p for p in PASTA.rglob("*.py") if p.name != "system_prompt.py"] + [
    RAIZ / f for f in ("api/nativo/mes_detalhes.py", "db/mes_detalhes.py", "db/resumo_mes.py", "db/accounts.py",
                       "core/services/cashflow.py", "core/services/comparacao_periodos.py")]
PRODUTORES_DA_REGRA_10 = [PASTA / "tools/launches.py", PASTA / "tools/mostrar_card.py"]
NOME = re.compile(r"`([a-z_][a-z0-9_]*)`")
TEXTO = SYSTEM_PROMPT.replace(FORMATO_WHATSAPP, "") + ADENDO_CARDS  # o bloco de formato fala de markdown, não do código
REGRA_10 = SYSTEM_PROMPT[SYSTEM_PROMPT.index("10. **Períodos"):SYSTEM_PROMPT.index("TEMPLATES DE RESPOSTA")]
NOMES_DE_TOOL = {s["function"]["name"] for s in SCHEMAS} | {s["function"]["name"] for s in SCHEMAS_APP}


def _str(n) -> str | None:
    return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None


def _conhecidos(fontes) -> set[str]:
    """Só o que o código EXPÕE: chaves de dict literal (campos de resultado e propriedades de schema), valores de
    `error`/`status`/`enum`, o 1º argumento de `_erro(...)`, campos anotados (pydantic), `x["campo"] = ...` e as
    constantes de motivos/IDS. Não é toda string do arquivo: um nome citado por engano não passa por docstring."""
    achados = {*NOMES_DE_TOOL, *cards.IDS}
    for f in fontes:
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if chave := _str(k):
                        achados.add(chave)
                        valores = v.elts if isinstance(v, (ast.List, ast.Tuple)) else [v]
                        if chave in ("error", "status", "enum"):
                            achados |= {s for s in map(_str, valores) if s}
            elif isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")).endswith(("_erro", "_falha")) and n.args:
                achados.add(_str(n.args[1 if getattr(n.func, "id", getattr(n.func, "attr", "")) == "_falha" else 0]))  # `_falha(card, codigo, msg)`
            elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store):
                achados.add(_str(n.slice))
            elif isinstance(n, (ast.Assign, ast.AnnAssign)):
                alvo = n.targets[0] if isinstance(n, ast.Assign) else n.target
                if isinstance(alvo, ast.Name):
                    if isinstance(n, ast.AnnAssign):
                        achados.add(alvo.id)
                    if "MOTIVO" in alvo.id or alvo.id == "IDS":
                        achados |= {c.value for c in ast.walk(n.value) if _str(c)}
    return achados - {None}


def _todas_as_strings(fontes) -> set[str]:
    return {c.value for f in fontes for c in ast.walk(ast.parse(f.read_text())) if _str(c)}


def _inexistentes(texto: str, conhecidos: set[str]) -> set[str]:
    return set(NOME.findall(texto)) - conhecidos


def test_todo_nome_entre_crases_existe_no_codigo():
    conhecidos = _conhecidos(FONTES)
    assert _inexistentes(TEXTO, conhecidos) == set()
    # controle negativo embutido: o checador pega um nome que não existe
    assert _inexistentes(TEXTO + " `limitado_a_doze_meses`", conhecidos) == {"limitado_a_doze_meses"}
    # controle positivo: os nomes desta rodada estão sendo conferidos, não ignorados
    assert {"limitado_a_12_meses", "historico_desde", "mes_futuro", "percentual"} <= set(NOME.findall(TEXTO))


def test_nomes_da_regra_10_sao_do_resultado_das_tools_de_periodo():
    """Mais estreito que o anterior: a regra 10 só cita campos de resultado, e esses têm de estar no
    `launches.py`/`mostrar_card.py` (que os produzem) — não em qualquer dict do pacote (`amount`, `categoria`)."""
    produzidos = _todas_as_strings(PRODUTORES_DA_REGRA_10)
    citados = set(NOME.findall(REGRA_10))
    assert {"aviso_limite", "historico_desde", "cartao_por", "fim_ajustado"} <= citados
    assert citados - produzidos == set()
    assert set(NOME.findall(REGRA_10 + " `amount`")) - produzidos == {"amount"}  # controle negativo


def _canal_do_app(adendo, ocultas, schemas):
    tools = NOMES_DE_TOOL | {"list_metas"}  # só quem tem a chave a recebe: fora de SCHEMAS e de SCHEMAS_APP
    linha = next(l for l in adendo.splitlines() if l.startswith("- No app NÃO existem"))
    antes, depois = linha.split("(ignore-as", 1)  # antes = o que NÃO existe; depois = o que o modelo deve usar no lugar
    assert {n for n in NOME.findall(antes) if n in tools} == set(ocultas)
    app = {s["function"]["name"] for s in schemas}
    citadas = {n for n in NOME.findall(adendo.replace(linha, "") + depois) if n in tools}
    assert citadas <= app, citadas - app


def test_adendo_respeita_o_canal_do_app():
    """Tool citada como inexistente no app = `OCULTAS_NO_APP`; qualquer outra tool citada no ADENDO existe nele.
    `list_metas` só existe para quem tem a chave das metas (o ADENDO de quem não tem nem a conhece)."""
    _canal_do_app(ADENDO_CARDS, OCULTAS_NO_APP - {"list_metas"}, SCHEMAS_APP)


def test_adendo_das_metas_respeita_o_canal_do_app():
    _canal_do_app(ADENDO_CARDS_METAS, OCULTAS_NO_APP, schemas_com_metas(SCHEMAS_APP, True))


def test_system_prompt_do_whatsapp_nao_cita_tool_so_do_app():
    """O SYSTEM_PROMPT vale no WhatsApp também: tool que só o app oferece (`show_card`) mora no ADENDO."""
    so_app = NOMES_DE_TOOL - {s["function"]["name"] for s in SCHEMAS}
    assert "show_card" in so_app  # controle: o conjunto não está vazio
    assert set(NOME.findall(SYSTEM_PROMPT)) & so_app == set()
    assert set(NOME.findall(SYSTEM_PROMPT + " `show_card`")) & so_app == {"show_card"}  # controle negativo


def test_campos_do_card_periodo_no_adendo_sao_do_modelo():
    """O bullet do TEXTO junto do card cita campos do `periodo` (`inicio`, `saldo`, `percentual`...): têm de ser
    campos do `Periodo` (ou das categorias dele), senão o modelo procura um campo que o card não traz."""
    from api.nativo.mes_detalhes import CategoriaDoPeriodo, Periodo
    campos = {*Periodo.model_fields, *CategoriaDoPeriodo.model_fields}
    linha = next(l for l in ADENDO_CARDS.splitlines() if l.startswith("- TEXTO junto do card"))
    citados = {n for n in NOME.findall(linha) if n not in NOMES_DE_TOOL}
    assert {"inicio", "fim", "saldo", "percentual", "historico_desde"} <= citados  # controle: está lendo o bullet
    assert citados - campos == set()
    assert set(NOME.findall(linha.replace("`saldo`", "`amount`"))) - NOMES_DE_TOOL - campos == {"amount"}  # negativo


def test_textos_da_comparacao_dizem_mes_inteiro_e_o_card_na_tela():
    """O que o modelo lê sobre `compare_periods` (onda 2): mês corrente INTEIRO (não "os mesmos dias") e o card sozinho."""
    mostram = next(l for l in ADENDO_CARDS.splitlines() if "mostram o card sozinhas" in l)
    assert "`compare_periods`" in mostram and "`card_na_tela: true`" in mostram
    linha = next(l for l in SYSTEM_PROMPT.splitlines() if "compara o mês corrente inteiro" in l)
    assert "mesmos dias" not in linha
    desc = next(s["function"]["description"] for s in SCHEMAS if s["function"]["name"] == "compare_periods")
    assert "mês inteiro" in desc and "mesmos dias" not in desc


def test_error_msg_tem_uma_fonte_so():
    assert runner.ERROR_MSG is system_prompt.ERROR_MSG
    assert SYSTEM_PROMPT.count(system_prompt.ERROR_MSG) == 1
    assert "Deu ruim" not in SYSTEM_PROMPT
    # uma só no FONTE: uma cópia literal no template 9 (em vez de `+ ERROR_MSG +`) fica verde no `count` acima
    fonte = ast.parse((PASTA / "system_prompt.py").read_text())
    copias = [c for c in ast.walk(fonte) if _str(c) and system_prompt.ERROR_MSG in c.value]
    assert len(copias) == 1  # só a atribuição de `ERROR_MSG`


def _ids_do_catalogo(adendo):
    linha = next(l for l in adendo.splitlines() if l.startswith("- Quando usar:"))
    return sorted(parte.split()[0] for parte in linha.removeprefix("- Quando usar:").strip().split(" · "))


def test_catalogo_do_adendo_tem_os_mesmos_ids_do_cards_ids():
    """Só compara ids (a descrição de cada card não é conferida). `caixinhas` é só de quem tem a chave das metas."""
    assert _ids_do_catalogo(ADENDO_CARDS) == sorted(i for i in cards.IDS if i != "caixinhas")


def test_catalogo_do_adendo_das_metas_tem_todos_os_ids():
    assert _ids_do_catalogo(ADENDO_CARDS_METAS) == sorted(cards.IDS)
