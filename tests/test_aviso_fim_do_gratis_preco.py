"""O equivalente mensal que o AVISO publica é o que a `/precos` publica.

Arquivo próprio por dois motivos: `test_aviso_fim_do_gratis_lote.py` está no
teto de 350 linhas (§0.5), e o assunto aqui é outro — lá se mede a copy, aqui
uma duplicação inevitável de ARITMÉTICA. O e-mail divide o anual por 12; a
`/precos` traz o resultado escrito à mão no markup ("Equivale a R$&nbsp;8,25 por
mês"). Nenhuma das duas cópias pode andar sozinha (§0.7), e a `/precos` é a
única fonte do mensal que NÃO compartilha a divisão com o e-mail.

Não é caso para o `test_pix_preco_bate_com_a_precos.py`: aquele exclui os
`price-eq` do conjunto dele de propósito (filtro `if "/ano" in texto`, senão o
rateio entraria como se fosse preço anual), então este gancho não conflita.

CONTROLE MEDIDO (`docs/controles_declarados.md`): troque o divisor `/ 12` do
equivalente mensal por `/ 10`, na produção E em toda cópia da mesma aritmética
que houver em `tests/`:
  VERMELHO: test_o_equivalente_mensal_do_email_e_o_que_a_precos_publica.
Injetar nos dois lados, e não só na produção, é o que faz este controle
DISCRIMINAR: aritmética duplicada erra junto e não separa os dois casos — é
exatamente a cegueira que este arquivo fecha.
"""

from __future__ import annotations

import pathlib
import re
from datetime import date

import core.services.email_service as es


def test_o_equivalente_mensal_do_email_e_o_que_a_precos_publica(monkeypatch):
    """O preço NÃO é trocado aqui: a comparação é contra o valor publicado.

    Cegueira declarada: casa contra o CONJUNTO dos equivalentes publicados,
    porque o markup não nomeia o plano no `price-eq`. O e-mail anunciar o equivalente de
    OUTRO plano passaria aqui — quem prende o valor à fonte é o
    `monkeypatch.setitem` do `test_aviso_fim_do_gratis_lote.py`.
    """
    capturado: dict = {}
    monkeypatch.setattr(es, "send_email",
                        lambda **kw: capturado.update(kw) or True)
    es.send_free_plan_sunset_email("quem@exemplo.com", date(2026, 9, 24))

    html = (pathlib.Path(__file__).resolve().parent.parent
            / "frontend" / "precos.html").read_text(encoding="utf-8")
    publicados = set(re.findall(
        r'class="price-eq"[^>]*>[^<]*?(R\$ ?[\d.,]+)',
        html.replace("&nbsp;", " ")))
    assert len(publicados) > 1, (
        f"a varredura dos `price-eq` da /precos achou {sorted(publicados)} — "
        "o markup mudou de forma e o comparador virou verde vazio"
    )
    assert any(p in capturado["html_body"] for p in publicados), (
        "o equivalente mensal do e-mail não é nenhum dos publicados na "
        f"/precos ({sorted(publicados)}) — a divisão das duas fontes divergiu"
    )
