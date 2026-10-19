"""§0.7: todo id de card que o Python emite (`cards.IDS`) tem de estar no `cardId` do app
(`app/src/api/schemas/painel.ts` = `periodo` + chaves do `TITULOS` de `catalogo.ts`); o app descarta
o evento de id desconhecido. O front não importa Python, então este teste lê o `.ts`."""
import pathlib
import re

from core.services.ai_chat import cards

PAINEL = pathlib.Path(__file__).resolve().parent.parent / "app" / "src"
# Ids que o Python emite e o app ainda não conhece (o aviso completo está em docs/CLAUDE.md, "Para o E1"). Vazia desde o E1
# (`caixinhas` entrou no `cardId`, FORA do `TITULOS`: o `TITULOS` é o catálogo de widgets e `caixinhas` é só card de chat).
PENDENTE_NO_APP: set[str] = set()


def ids_do_app(painel_ts: str, catalogo_ts: str) -> set[str]:
    assert '"periodo", "caixinhas", ...Object.keys(TITULOS)' in painel_ts, "o cardId do app mudou de forma: ajuste este teste"
    titulos = re.search(r"export const TITULOS = \{(.*?)\};", catalogo_ts, re.S).group(1)
    return set(re.findall(r"(\w+):\s*\"", titulos)) | {"periodo", "caixinhas"}


def ids_lidos() -> set[str]:
    return ids_do_app((PAINEL / "api/schemas/painel.ts").read_text(encoding="utf-8"),
                      (PAINEL / "features/painel/catalogo.ts").read_text(encoding="utf-8"))


def test_ids_do_python_que_o_app_ainda_nao_conhece_sao_so_os_pendentes():
    assert set(cards.IDS) - ids_lidos() == PENDENTE_NO_APP, (
        "o app e o Python divergem: id novo no Python sem o app, ou o app já tem o pendente "
        "(esvazie PENDENTE_NO_APP)")


def test_controle_id_novo_no_python_sem_o_app_fica_vermelho(monkeypatch):
    monkeypatch.setattr(cards, "IDS", (*cards.IDS, "novo_card"))
    assert set(cards.IDS) - ids_lidos() == PENDENTE_NO_APP | {"novo_card"} != PENDENTE_NO_APP


# Cards da tool que o Python já emite (`cards.DA_TOOL`) e o app ainda não desenha. Vazia desde que a `comparacao` entrou no `schemasDaTool`.
PENDENTE_DA_TOOL: set[str] = set()


def ids_da_tool_do_app(painel_ts: str) -> set[str]:
    corpo = re.search(r"const schemasDaTool = \{(.*?)\};", painel_ts, re.S)
    assert corpo, "o `schemasDaTool` do app mudou de forma: ajuste este teste"
    return set(re.findall(r"(\w+):\s*\w+Schema", corpo.group(1)))


def test_cards_da_tool_do_app_sao_os_do_python():
    """O app manda `cards` = `CARDS_DA_TOOL` (= as chaves do `schemasDaTool`) no POST do chat; sem um id aqui o servidor não o emite,
    e id a mais no app que o Python não conhece é código morto. Fora de `cards.IDS` de propósito (ver `cards.DA_TOOL`)."""
    app = ids_da_tool_do_app((PAINEL / "api/schemas/painel.ts").read_text(encoding="utf-8"))
    assert set(cards.DA_TOOL) - app == PENDENTE_DA_TOOL and app - set(cards.DA_TOOL) == set()


def test_controle_card_da_tool_novo_no_python_sem_o_app_fica_vermelho(monkeypatch):
    monkeypatch.setattr(cards, "DA_TOOL", (*cards.DA_TOOL, "novo_card"))
    app = ids_da_tool_do_app((PAINEL / "api/schemas/painel.ts").read_text(encoding="utf-8"))
    assert set(cards.DA_TOOL) - app == PENDENTE_DA_TOOL | {"novo_card"} != PENDENTE_DA_TOOL
