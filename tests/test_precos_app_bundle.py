"""O bundle da ilha React da /precos está buildado e é o build de verdade.

Molde do `tests/test_phosphor_subset.py`: assunto é um ARTEFATO commitado, e
artefato commitado apodrece calado. O bug que isto pega é "editei o `.jsx` e
esqueci de buildar" na metade em que ele mata a página: o arquivo apagado, vazio,
ou trocado por um placeholder que não renderiza card nenhum.

**O que ele NÃO prova**, e a divisão importa:

- **não prova que o artefato está EM DIA com `webapp/`.** Nada que leia o
  artefato de fora consegue provar isso: hash de fonte prova "as fontes não
  mudaram desde o último carimbo", nunca "este bundle veio destas fontes". Quem
  prova é o BUILD, e ele roda no CI (`.github/workflows/tests.yml`, step "Gate do
  artefato da ilha React": `npm --prefix webapp ci && npm --prefix webapp run
  build` e `git status --porcelain -- frontend/` limpo);
- **não prova que a rota existe.** Isso é o `tests/test_frontend_assets_e_rotas.py`,
  que pareia asset ↔ rota — sem ele o arquivo daria 404 em produção com CI verde;
- **não prova que o React renderiza.** Isso é o harness de frontend
  (`npm run test:frontend`), que abre a página num navegador de verdade.

*Controle negativo (§3): apague `frontend/precos-app.js` → os dois testes abaixo
ficam vermelhos. Reponha → verdes. Medido antes de commitar.*
"""

import pathlib

BUNDLE = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "precos-app.js"

# O contrato de DOM que o bundle é obrigado a emitir. Cada um destes é lido por
# OUTRO arquivo em tempo de execução, e perder qualquer um quebra dinheiro:
#
#   `#plans-v2`     — `pix-checkout.js:109`, onde o CTA de Pix é inserido;
#   `data-plan-btn` — o mesmo seletor, mais o `markUnavailable` e o
#                     `refreshPlanButtons` da precos.html;
#   `price-block`   — envelope dos `[data-price-*]` que o `setCycle` alterna;
#   `plan-badge`    — o "Mais popular" e o "Em breve".
#
# São strings que só existem no bundle porque o COMPONENTE as escreve: um build
# de `webapp/src` vazio, ou um placeholder, não tem nenhuma delas.
MARCAS = ("plans-v2", "data-plan-btn", "price-block", "plan-badge")


def test_o_bundle_existe_e_nao_esta_vazio():
    """PISO do arquivo. Sem ele, apagar o bundle deixaria o teste de baixo verde
    por vácuo — `""` não contém marca nenhuma, mas também não falha em `in`
    se alguém trocar a asserção por um `all(... or not texto)`."""
    assert BUNDLE.is_file(), (
        f"{BUNDLE.name} não existe. Rode `npm --prefix webapp ci && "
        "npm --prefix webapp run build` e commite o artefato."
    )
    # 50 kB: o bundle é React + react-dom minificados. O número não é um teto de
    # qualidade, é um piso contra placeholder — um arquivo de 3 linhas passa em
    # "não vazio" e não renderiza nada.
    assert BUNDLE.stat().st_size > 50_000, (
        f"{BUNDLE.name} tem {BUNDLE.stat().st_size} bytes — pequeno demais para "
        "ser o bundle do React. Placeholder ou build parcial?"
    )


def test_o_bundle_carrega_o_contrato_de_dom_da_precos():
    texto = BUNDLE.read_text(encoding="utf-8")
    faltando = [m for m in MARCAS if m not in texto]
    assert not faltando, (
        f"o bundle não menciona {faltando} — o componente parou de emitir o "
        "markup que a precos.html e o pix-checkout.js consomem, ou o build saiu "
        f"de outra fonte. Marcas exigidas: {list(MARCAS)}"
    )


def test_o_build_nao_emitiu_asset_fora_da_dupla_js_css():
    """`assetFileNames: "precos-app.[ext]"` (webapp/vite.config.js) nomeia TODO
    asset assim: uma fonte ou imagem que o bundle passe a importar sai em
    `frontend/precos-app.woff2`. Asset em `frontend/` só é servido por uma rota
    escrita à mão em `static_pages.py` (§5), e o
    `tests/test_frontend_assets_e_rotas.py` pareia asset ↔ rota apenas para
    `.html`/`.js`/`.css` — um terceiro tipo daria 404 em produção com o CI verde.

    *Controle negativo (§3): `touch frontend/precos-app.woff2` → vermelho.*
    """
    emitidos = sorted(p.name for p in BUNDLE.parent.glob("precos-app.*"))
    assert emitidos == ["precos-app.css", "precos-app.js"], (
        f"assets `precos-app.*` em frontend/: {emitidos}. O build emitiu um "
        "artefato novo; ele precisa de rota própria em "
        "frontend/routes/static_pages.py (e entrar nesta lista) — sem rota, 404 "
        "no navegador com o CI verde."
    )
