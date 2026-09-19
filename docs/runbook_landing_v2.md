# Runbook — Landing v2 (Lovable) em produção

Data da subida: 2026-09-18 · Branch: `feature/landing-v2` · Merge na `main`

## O que mudou

- `/` passa a servir a **landing v2** (`frontend/landing-v2/`, espelho self-hosted
  da build do Lovable, sem badge/tracking deles, trial de 15 dias, CTAs → `/cadastro`).
- A **landing v1 antiga não foi apagada**: segue em `frontend/index.html` e fica
  acessível em **`/landing-v1`** (standby), com `<meta name="robots" content="noindex">`
  para não competir com a v2 no Google.

## Rollback — voltar para a landing v1

### Opção A — ~1 minuto, sem build (emergência)

1. Railway → projeto `reasonable-surprise` → ambiente **production** →
   serviço `Dashboard/whatsapp` → aba **Deployments**.
2. Abrir o deploy que estava no ar **antes** da LP e clicar **Redeploy**
   (reaproveita o build antigo; só reinicia o container).
3. Confirmar em `https://pigbankai.com/` que a v1 voltou.

Não precisa limpar cache no Cloudflare: o HTML sai com `Cache-Control: no-store`
e os assets da v2 têm nome com hash (não colidem com nada da v1).

### Opção B — caminho limpo (git revert)

```bash
git checkout main && git pull
git revert <sha-do-merge-da-landing-v2>   # desfaz rotas, noindex e testes de uma vez
git push                                  # deploy automático em produção
```

O `git revert` é o caminho correto porque também **remove o `noindex`** da v1
(servir a v1 com `noindex` derrubaria a homepage do Google). Trocar só a linha
da rota na mão deixa essa armadilha para trás.

## Atualizar a landing v2 (re-sync do Lovable)

A página é um espelho da build hospedada no Lovable. Quando ela for republicada
lá (os nomes dos arquivos mudam de hash):

1. Atualizar a lista de nomes em `scripts/sync_landing_v2.sh`.
2. `bash scripts/sync_landing_v2.sh` (re-baixa e reaplica limpeza: 15 dias,
   CTAs → `/cadastro`, lang pt-BR, sem badge).
3. Ajustar as rotas/testes se os nomes dos assets mudaram.
4. Merge na `main` → deploy automático.

## URLs

| Ambiente | URL |
|---|---|
| Produção | https://pigbankai.com/ |
| Staging | https://dashboard-staging-3369.up.railway.app/ |
| V1 em standby | https://pigbankai.com/landing-v1 |

## Testes de rota

`tests/test_static_pages_routes.py` cobre: `/` serve a v2 (15 dias, sem
badge/flock, CTAs → `/cadastro`), `/landing-v1` serve a v1 com noindex,
assets com cache imutável, 404 em desconhecido/traversal.
