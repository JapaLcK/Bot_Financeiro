"""§0.7: todo id de card que o Python emite (`cards.IDS`) tem de estar no `CARDS` do /painel (`webapp/src/dashboard/parts/CardIa.tsx`), ou na lista
dos que ainda faltam (`PENDENTE_NO_PAINEL`); o /painel mostra o marcador "Este card ainda não aparece aqui." para id desconhecido. O front não importa
Python, então este teste lê o `.tsx` (as chaves ficam uma por linha, 2 espaços de recuo, de propósito). Molde: `tests/test_ai_chat_cards_ids_app.py`.

CONTROLES (mutação, medida; remeça se mexer):
  negativos: `cards.IDS` com "novo_card" -> test_controle_id_novo_no_python_sem_o_painel_fica_vermelho; a fonte com `contaz` no lugar de `contas`
             -> test_controle_chave_trocada_na_fonte_fica_vermelho; um id pendente que já foi feito (`contas` na lista) -> test_pendente_so_tem_id_que_o_python_emite_e_o_painel_nao_tem
  positivo: o estado atual passa."""
import pathlib
import re

from core.services.ai_chat import cards

FONTE = pathlib.Path(__file__).resolve().parent.parent / "webapp" / "src" / "dashboard" / "parts" / "CardIa.tsx"
# Os ids que o Python emite e o /painel ainda não desenha (PR 6 da Etapa 7): os legados. O PR 6 esvazia esta lista.
PENDENTE_NO_PAINEL = {"caixinhas", "fatura", "parcelas", "renda", "piggy", "rendimento"}


def ids_do_painel(fonte: str) -> set[str]:
    corpo = re.search(r"const CARDS: Record<string, Def> = \{\n(.*?)\n\};", fonte, re.S)
    assert corpo, "o `CARDS` do CardIa.tsx mudou de forma: ajuste este teste"
    return set(re.findall(r"^  (\w+): \{", corpo.group(1), re.M))


def ids_lidos() -> set[str]:
    return ids_do_painel(FONTE.read_text(encoding="utf-8"))


def test_o_painel_conhece_todo_id_do_python_menos_os_pendentes():
    assert set(cards.IDS) - ids_lidos() == PENDENTE_NO_PAINEL, (
        "o /painel e o Python divergem: id novo no Python sem o /painel, ou o /painel já tem o pendente (esvazie PENDENTE_NO_PAINEL)")


def test_o_painel_nao_desenha_id_que_o_python_nao_emite():
    assert ids_lidos() - set(cards.IDS) == set()


def test_pendente_so_tem_id_que_o_python_emite_e_o_painel_nao_tem():
    assert PENDENTE_NO_PAINEL <= set(cards.IDS) and not PENDENTE_NO_PAINEL & ids_lidos()


def test_a_fonte_tem_as_doze_entradas_dos_prs_3_a_5():
    assert ids_lidos() == {"contas", "hero", "compromissos", "patrimonio", "wealth", "assinaturas", "metas", "resumo", "categorias", "calendario", "periodo", "simulador"}


def test_controle_id_novo_no_python_sem_o_painel_fica_vermelho(monkeypatch):
    monkeypatch.setattr(cards, "IDS", (*cards.IDS, "novo_card"))
    assert set(cards.IDS) - ids_lidos() == PENDENTE_NO_PAINEL | {"novo_card"} != PENDENTE_NO_PAINEL


def test_controle_chave_trocada_na_fonte_fica_vermelho():
    fonte = FONTE.read_text(encoding="utf-8")
    assert "  contas: {" in fonte  # o controle só vale se o trecho existe
    ids = ids_do_painel(fonte.replace("  contas: {", "  contaz: {"))
    assert "contas" not in ids and set(cards.IDS) - ids != PENDENTE_NO_PAINEL and ids - set(cards.IDS) == {"contaz"}
