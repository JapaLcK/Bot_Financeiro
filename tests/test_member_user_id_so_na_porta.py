"""`views.member_user_id` resolve ref -> uid do membro SEM checar consentimento: só pode ser chamado por
`api/v2/familia.py` (cadastro) e `core/services/family_reads.py` (o único ponto que autoriza e grava o log)."""
import pathlib
import re

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
_IGNORADOS = {".venv", ".claude", "node_modules", "mobile", ".git"}
_USO = re.compile(r"member_user_id\s*\(|import[^\n]*\bmember_user_id\b")
_PERMITIDOS = {"api/v2/familia.py", "core/services/family_reads.py", "db/family_views.py"}


def _chamadores():
    for arquivo in sorted(_RAIZ.rglob("*.py")):
        rel = arquivo.relative_to(_RAIZ)
        if not _IGNORADOS.intersection(rel.parts) and rel.parts[0] != "tests" \
                and rel.as_posix() not in _PERMITIDOS and _USO.search(arquivo.read_text(encoding="utf-8")):
            yield rel.as_posix()


def test_member_user_id_so_nos_arquivos_permitidos():
    assert list(_chamadores()) == []
