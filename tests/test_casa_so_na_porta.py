"""family_goals/family_budgets só são lidas e escritas em db/family_house.py: lá o membro ativo, reconferido sob o lock
do grupo, é a fronteira de autorização (item de outro grupo = 404). Exceções: o DDL (schema, reparo) e a contagem da
prévia de dissolução. Rota nova que toque as tabelas fora dali é vazamento entre grupos.
Limite: lê o texto de cada .py; SQL montado por concatenação/f-string (nome da tabela partido) fica fora: revisão humana."""
import pathlib
import re

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
_IGNORADOS = {".venv", ".claude", "node_modules", "mobile", ".git", "tests"}
_USO = re.compile(r"\bfamily_(goals|budgets)\b", re.I)
_CONTAGEM = re.compile(r"select count\(\*\) from family_(goals|budgets) where group_id", re.I)
_PERMITIDOS = {"db/family_house.py", "db/schema.py", "db/schema_repairs.py", "db/family_views.py"}


def _fora(arquivos):
    for arquivo in sorted(arquivos):
        rel = arquivo.relative_to(_RAIZ)
        if not _IGNORADOS.intersection(rel.parts) and rel.as_posix() not in _PERMITIDOS and _USO.search(arquivo.read_text(encoding="utf-8")):
            yield rel.as_posix()


def test_tabelas_da_casa_so_em_family_house():
    assert list(_fora(_RAIZ.rglob("*.py"))) == []


def test_guarda_pega_um_arquivo_que_toca_a_tabela(tmp_path, monkeypatch):  # controle negativo da própria guarda
    ruim = _RAIZ / "api" / "v2" / "_zz_casa_vazando.py"
    ruim.write_text('SQL = "select * from family_goals"\n', encoding="utf-8")
    try:
        assert list(_fora([ruim])) == ["api/v2/_zz_casa_vazando.py"]
    finally:
        ruim.unlink()


def test_guarda_e_o_formato_da_contagem_pegam_variantes():  # controles negativos dos dois ajustes
    assert _USO.search("SELECT * FROM FAMILY_GOALS")
    assert _CONTAGEM.search("(select count(*) from family_goals where group_id = %(g)s) as metas")
    assert not _CONTAGEM.search("select nome from family_goals where group_id = 1 -- count(*)")


def test_family_views_so_conta_a_casa():  # a exceção da prévia é só contagem: nada de nome, valor nem autor
    fonte = pathlib.Path(_RAIZ / "db/family_views.py").read_text(encoding="utf-8")
    for linha in fonte.splitlines():
        if _USO.search(linha) and not linha.lstrip().startswith("#"):
            assert len(_USO.findall(linha)) == len(_CONTAGEM.findall(linha)), linha
