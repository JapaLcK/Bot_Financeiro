#!/usr/bin/env python3
"""Smoke de produção pós-deploy — HTTP, read-only, sem login.

Existe porque a suíte do CI mede o CÓDIGO e nada mais. As falhas que só
aparecem depois do deploy (rota que some, asset que não chega, o
"Application not found" do proxy no lugar da página) passam por 984 testes
verdes sem uma linha vermelha. Este script é a única coisa que olha o site
que está no ar.

Uso:
    python scripts/smoke_prod.py --wait-sha $GITHUB_SHA
    python scripts/smoke_prod.py --base https://pigbankai.com   # sem gate

Sai 1 na primeira categoria com falha, listando TODAS as falhas — não para
na primeira, para render o diagnóstico inteiro numa rodada só.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from urllib.parse import urljoin

import requests

BASE_PADRAO = "https://pigbankai.com"
TIMEOUT = 20

# Rotas públicas: as que qualquer visitante alcança sem sessão. NÃO inclui
# /home, /settings, /dashboard — essas redirecionam para o login e quem as
# mede é o smoke de UI, logado.
ROTAS_PUBLICAS = [
    "/", "/login", "/cadastro", "/precos", "/funcionalidades", "/como-funciona",
    "/comandos", "/termos", "/privacy", "/suporte", "/whatsapp", "/agents",
]

# Rotas que EXIGEM sessão: o contrato delas é o desvio, não o conteúdo. Medido:
# /changelog responde 302 -> /login?next=/changelog para anônimo (gate_pro_page).
# Estava na lista de públicas seguindo o redirect, então o smoke media a página
# de login achando que media o changelog — e os assets contados eram os dela.
ROTAS_COM_GATE = [("/changelog", "/login")]
ROTAS_TEXTO = ["/robots.txt", "/sitemap.xml", "/api/commands-catalog"]

# O 404 do Railway quando o serviço não está no ar. Ele responde no lugar do
# app, com cara de erro de aplicação — foi o que apareceu dentro do card
# "TODAS AS CATEGORIAS" do dashboard em 2026-08-26.
MARCA_PROXY_FORA = "Application not found"

# Assets carimbados: o stamp_asset_versions troca o ?v=N por hash do conteúdo.
# Se o hash no HTML não corresponder a um arquivo servido, o navegador busca
# um asset que não existe e a página quebra sem erro de servidor.
RE_ASSET = re.compile(r'(?:src|href)="([^"]+\.(?:css|js)\?v=[0-9a-f]+)"')


def _get(sessao: requests.Session, base: str, caminho: str) -> requests.Response:
    # Sem seguir redirect: uma rota que passe a desviar para outra página
    # saudável satisfaria o 200 e o <title> sem nunca ter sido medida.
    return sessao.get(urljoin(base, caminho), timeout=TIMEOUT, allow_redirects=False)


def espera_deploy(base: str, sha: str, limite_s: int, falhas: list[str]) -> bool:
    """Bloqueia até /health reportar `sha`. True se chegou, False se desistiu.

    O commit não é público: o /health só o devolve para quem manda o
    `X-Smoke-Token` que casa com o `SMOKE_HEALTH_TOKEN` do processo. Sem isso
    ele é infraestrutura vazando em endpoint aberto — tem teste de segurança
    dizendo isso (tests/test_auth_cookie.py).
    """
    token = os.getenv("SMOKE_HEALTH_TOKEN", "")
    if not token:
        falhas.append(
            "SMOKE_HEALTH_TOKEN ausente no ambiente: sem ele o /health não devolve "
            "o commit e não há como saber qual código está no ar."
        )
        return False

    prazo = time.monotonic() + limite_s
    visto = "<sem resposta>"
    while time.monotonic() < prazo:
        try:
            corpo = requests.get(urljoin(base, "/health"), timeout=TIMEOUT,
                                 headers={"X-Smoke-Token": token}).json()
            visto = str(corpo.get("commit", "<sem campo commit>"))
            if visto == "<sem campo commit>":
                # NÃO é erro de configuração: é o estado esperado enquanto a
                # produção ainda serve a versão anterior de /health, que não
                # tinha o campo. Falhar aqui reprovaria justamente o primeiro
                # run depois deste merge — antes de o deploy sequer começar.
                # Continua tentando; se persistir até o prazo, o timeout abaixo
                # reporta, e aí a hipótese do token entra na mensagem.
                pass
            elif visto == "unknown":
                falhas.append(
                    "/health devolveu commit='unknown': RAILWAY_GIT_COMMIT_SHA não "
                    "chegou ao processo. Sem isso não dá para saber QUAL código está "
                    "no ar, e um smoke verde não provaria nada."
                )
                return False
            if visto.startswith(sha) or sha.startswith(visto):
                print(f"deploy no ar: {visto}")
                return True
        except Exception as e:  # rede, JSON, 502 durante o restart — tudo é "ainda não"
            visto = f"<{type(e).__name__}>"
        print(f"aguardando deploy de {sha[:8]} (no ar: {visto})…", flush=True)
        time.sleep(15)
    falhas.append(
        f"deploy de {sha[:8]} não subiu em {limite_s}s (último: {visto}). "
        "Se o último for `<sem campo commit>`, as causas são: o deploy não "
        "aconteceu, ou o SMOKE_HEALTH_TOKEN daqui não bate com o do serviço, "
        "ou a variável não está no ambiente da produção."
    )
    return False


def confere_paginas(sessao: requests.Session, base: str, falhas: list[str]) -> set[str]:
    """200 + HTML de verdade em toda rota pública. Devolve os assets vistos."""
    assets: set[str] = set()
    for caminho in ROTAS_PUBLICAS:
        try:
            r = _get(sessao, base, caminho)
        except Exception as e:
            falhas.append(f"{caminho}: {type(e).__name__}: {e}")
            continue
        if r.status_code != 200:
            falhas.append(f"{caminho}: HTTP {r.status_code}")
            continue
        if MARCA_PROXY_FORA in r.text:
            falhas.append(f"{caminho}: proxy respondeu '{MARCA_PROXY_FORA}' no lugar da página")
            continue
        if "<title" not in r.text.lower():
            falhas.append(f"{caminho}: 200 sem <title> ({len(r.text)} bytes) — não é a página")
            continue
        assets.update(RE_ASSET.findall(r.text))
    for caminho, destino in ROTAS_COM_GATE:
        try:
            r = _get(sessao, base, caminho)
        except Exception as e:
            falhas.append(f"{caminho}: {type(e).__name__}: {e}")
            continue
        if r.status_code not in (301, 302, 303, 307, 308):
            falhas.append(f"{caminho}: esperado desvio para {destino}, veio HTTP {r.status_code}")
        elif not r.headers.get("location", "").startswith(destino):
            falhas.append(f"{caminho}: desviou para {r.headers.get('location')!r}, esperado {destino}")
    for caminho in ROTAS_TEXTO:
        try:
            r = _get(sessao, base, caminho)
            if r.status_code != 200:
                falhas.append(f"{caminho}: HTTP {r.status_code}")
        except Exception as e:
            falhas.append(f"{caminho}: {type(e).__name__}: {e}")
    return assets


def confere_assets(sessao: requests.Session, base: str, assets: set[str], falhas: list[str]) -> None:
    if not assets:
        falhas.append("nenhum asset com ?v=<hash> encontrado no HTML — o stamp parou de agir")
        return
    for asset in sorted(assets):
        try:
            r = _get(sessao, base, asset)
        except Exception as e:
            falhas.append(f"asset {asset}: {type(e).__name__}: {e}")
            continue
        if r.status_code != 200:
            falhas.append(f"asset {asset}: HTTP {r.status_code}")


def confere_pagina_de_erro(sessao: requests.Session, base: str, falhas: list[str]) -> None:
    """Os DOIS lados do 404 (o P1-06 do roteiro de regressão).

    A escolha é pelo `Accept`, não pela rota: navegador (`text/html`) recebe a
    página do `error_page_response`; cliente de API (`application/json`) recebe
    JSON. Medir só um lado deixa passar a metade que regride — devolver HTML
    para o app quebra o parse do cliente, e devolver JSON ao navegador mostra
    `{"detail":"Not Found"}` cru na tela.
    """
    caminho = "/rota-que-nao-existe-smoke"
    try:
        html = sessao.get(urljoin(base, caminho), timeout=TIMEOUT,
                          headers={"Accept": "text/html"})
        api = sessao.get(urljoin(base, caminho), timeout=TIMEOUT,
                         headers={"Accept": "application/json"})
    except Exception as e:
        falhas.append(f"404: {type(e).__name__}: {e}")
        return

    for nome, r in (("navegador", html), ("API", api)):
        if r.status_code != 404:
            falhas.append(f"404 ({nome}): esperado 404, veio {r.status_code}")
        if MARCA_PROXY_FORA in r.text:
            falhas.append(f"404 ({nome}): veio o '{MARCA_PROXY_FORA}' do proxy, não a do app")

    if "<title" not in html.text.lower():
        falhas.append("404 (navegador): não é HTML — o error_page_response não respondeu")
    if not api.headers.get("content-type", "").startswith("application/json"):
        falhas.append(
            f"404 (API): content-type {api.headers.get('content-type')!r}, esperado JSON — "
            "cliente de API passou a receber página"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=BASE_PADRAO)
    ap.add_argument("--wait-sha", default="", help="espera /health reportar este commit")
    ap.add_argument("--wait-timeout", type=int, default=900)
    args = ap.parse_args()

    falhas: list[str] = []
    if args.wait_sha and not espera_deploy(args.base, args.wait_sha, args.wait_timeout, falhas):
        print("\n".join(f"FALHA: {f}" for f in falhas), file=sys.stderr)
        return 1

    sessao = requests.Session()
    sessao.headers["User-Agent"] = "PigBankSmoke/1.0"
    sessao.headers["Accept"] = "text/html,application/xhtml+xml,*/*;q=0.8"
    assets = confere_paginas(sessao, args.base, falhas)
    confere_assets(sessao, args.base, assets, falhas)
    confere_pagina_de_erro(sessao, args.base, falhas)

    total = len(ROTAS_PUBLICAS) + len(ROTAS_COM_GATE) + len(ROTAS_TEXTO) + len(assets) + 2
    if falhas:
        print(f"\n{len(falhas)} falha(s) em {total} verificações:", file=sys.stderr)
        print("\n".join(f"  FALHA: {f}" for f in falhas), file=sys.stderr)
        return 1
    print(f"smoke HTTP ok: {total} verificações ({len(assets)} assets)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
