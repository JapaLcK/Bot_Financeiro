"""§0.7: todo id de card que o Python emite (`cards.IDS`) tem de estar no `cardId` do app
(`app/src/api/schemas/painel.ts` = `periodo` + chaves do `TITULOS` de `catalogo.ts`); o app descarta
o evento de id desconhecido. O front não importa Python, então este teste lê o `.ts`."""
import pathlib
import re

from core.services.ai_chat import cards

PAINEL = pathlib.Path(__file__).resolve().parent.parent / "app" / "src"
# O E1 (build 27) precisa pôr `caixinhas` no `cardId` de app/src/api/schemas/painel.ts, como o `periodo`
# (`["periodo", "caixinhas", ...]`, FORA do `TITULOS`: o `TITULOS` é o catálogo de widgets e `caixinhas` é só
# card de chat); o aviso completo está em docs/CLAUDE.md, "Para o E1". Quando puser, o teste FALHA pedindo
# para esvaziar esta lista.
PENDENTE_NO_APP = {"caixinhas"}


def ids_do_app(painel_ts: str, catalogo_ts: str) -> set[str]:
    assert '"periodo", ...Object.keys(TITULOS)' in painel_ts, "o cardId do app mudou de forma: ajuste este teste"
    titulos = re.search(r"export const TITULOS = \{(.*?)\};", catalogo_ts, re.S).group(1)
    return set(re.findall(r"(\w+):\s*\"", titulos)) | {"periodo"}


def ids_lidos() -> set[str]:
    return ids_do_app((PAINEL / "api/schemas/painel.ts").read_text(encoding="utf-8"),
                      (PAINEL / "features/painel/catalogo.ts").read_text(encoding="utf-8"))


def test_ids_do_python_que_o_app_ainda_nao_conhece_sao_so_os_pendentes():
    assert set(cards.IDS) - ids_lidos() == PENDENTE_NO_APP, (
        "o app e o Python divergem: id novo no Python sem o app, ou o app já tem o pendente "
        "(esvazie PENDENTE_NO_APP)")


def test_controle_o_app_ganha_caixinhas_o_teste_pede_para_esvaziar_a_lista():
    catalogo = (PAINEL / "features/painel/catalogo.ts").read_text(encoding="utf-8")
    painel = (PAINEL / "api/schemas/painel.ts").read_text(encoding="utf-8")
    app = ids_do_app(painel, catalogo.replace("TITULOS = { ", 'TITULOS = { caixinhas: "Caixinhas", ', 1))
    assert "caixinhas" in app and set(cards.IDS) - app != PENDENTE_NO_APP


def test_controle_id_novo_no_python_sem_o_app_fica_vermelho(monkeypatch):
    monkeypatch.setattr(cards, "IDS", (*cards.IDS, "novo_card"))
    assert set(cards.IDS) - ids_lidos() == PENDENTE_NO_APP | {"novo_card"} != PENDENTE_NO_APP
