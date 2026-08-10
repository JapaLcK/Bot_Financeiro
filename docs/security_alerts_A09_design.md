# A09 — Alertas de segurança (design, não implementado)

Objetivo: ser avisado **na hora** de dois eventos: pico de falha de login e
5xx em série. Reaproveita a infra que já existe — nada de canal novo.

## Infra reaproveitada

- **Canal:** `core/services/admin_notify.py` → `ADMIN_NOTIFY_WEBHOOK_URL`
  (Slack/Discord auto-detect). Já é **fire-and-forget** e **no-op silencioso**
  se a env estiver vazia. Todo alerta sai por `asyncio.to_thread(_send, msg)`
  pra não pesar no request.
- **Fonte de dados de auth:** `auth_login_events` (já gravado por
  `core/admin_dashboard.py::log_auth_login_event`, com `success`,
  `ip_address`, `email`, `created_at`).
- **Trilha de dedup:** `system_event_logs` (já existe, via `log_system_event`)
  — marca "já alertei sobre X" de forma cross-worker.

## Princípios (valem pros dois detectores)

1. **Nunca bloquear o request** — sempre fire-and-forget.
2. **Zero PII no alerta** — email sempre mascarado (`a***@dominio`), nunca
   token/senha/query string. Detalhe cru fica no log server-side.
3. **Anti-storm** — cooldown por chave (IP, rota) pra um ataque não virar 200
   mensagens. Cross-worker via marcador em `system_event_logs`.
4. **Feature flag** — `SECURITY_ALERTS_ENABLED` (default: ligado só se o webhook
   estiver setado). Desliga sem deploy.

---

## Detector 1 — Spike de falha de login  ✅ prioridade

**Threshold escolhido:** 5 falhas / 5 min. Duas chaves independentes:
- **por IP** — pega brute-force de um IP contra várias contas.
- **por email** — pega credential stuffing distribuído contra uma conta.

**Onde plugar:** ao fim de `log_auth_login_event`, quando `success=False`.
Depois de gravar a tentativa, roda a contagem da janela:

```sql
-- por IP (mesma ideia por email, trocando a coluna)
select count(*) from auth_login_events
 where ip_address = %s and success = false
   and created_at >= now() - interval '5 minutes';
```

**Fluxo:**
1. `count >= 5` numa das chaves →
2. checa cooldown: houve `system_event_logs` tipo `auth_spike_alert` pra essa
   chave nos últimos **30 min**? Se sim, silencia.
3. senão: grava o marcador e dispara `admin_notify`.

**Mensagem (exemplo):**
```
🚨 Spike de falha de login
IP 203.0.113.7 — 6 falhas em 5min
Alvos: le***@gmail.com, ad***@pigbank.com  (3 contas distintas)
```

**Envs novas:** `AUTH_ALERT_THRESHOLD=5`, `AUTH_ALERT_WINDOW_MIN=5`,
`AUTH_ALERT_COOLDOWN_MIN=30`.

**Custo:** 1 SELECT indexado por falha de login. Falha de login já é raro e já
rate-limited (5/min) — custo desprezível. Precisa de índice
`(ip_address, created_at)` e `(email, created_at)` em `auth_login_events`
(checar se já existe; senão, `create index concurrently`).

**Teste:** unit que insere 5 falhas na janela e afirma que `_send` é chamado 1x
(e que a 6ª, dentro do cooldown, não chama).

---

## Detector 2 — 5xx em série  (2º incremento, mais invasivo)

**Threshold sugerido:** 10 respostas 5xx / 5 min (global). Ajustável por env.

**Onde plugar:** o app já tem um `@app.middleware("http")` de headers de
segurança (perto da linha 1735). Adicionar um contador de 5xx em janela
deslizante **em memória** (por worker) — sem tocar no DB no hot path:
- deque de timestamps dos 5xx dos últimos 5 min;
- ao passar de 10, dispara alerta (com cooldown de 30 min via marcador).

**Cuidado (liga com o B1 da auditoria):** o alerta manda só `método + rota +
status` (ex.: `POST /ai/chat → 500`), **nunca** o corpo da exceção — detalhe
cru vai pro log server-side. Isso também é um empurrão pra fechar o B1
(erros verbosos ecoados ao cliente).

**Mensagem (exemplo):**
```
🔥 5xx em série: 12 erros em 5min
Top rotas: POST /lancamentos (7), POST /ai/chat (3)
```

**Por que é "2º incremento":** mexe no arquivo de 260KB e no caminho de TODA
resposta. Baixo risco (só conta e, no limiar, dispara async), mas quero testar
com calma — daí separar do detector 1.

**Envs novas:** `HTTP5XX_ALERT_THRESHOLD=10`, `HTTP5XX_ALERT_WINDOW_MIN=5`.

---

## Arquivos a criar/tocar quando for implementar

- **novo** `core/services/security_alerts.py` — lógica dos dois detectores +
  cooldown + máscara de email + montagem da mensagem. Isola tudo num módulo.
- `core/admin_dashboard.py` — 1 chamada no fim de `log_auth_login_event`.
- `frontend/finance_bot_websocket_custom.py` — contador 5xx no middleware
  existente (só no 2º incremento).
- `db/schema.py` — garantir índices `(ip_address, created_at)` /
  `(email, created_at)` em `auth_login_events`.
- `tests/test_security_alerts.py` — thresholds, cooldown, no-op sem webhook,
  máscara de PII.

## Decisões pendentes suas

- Ligar já o detector 1? (é o de maior valor e menor risco)
- 5xx: threshold 10/5min serve, ou prefere outro?
- Cooldown de 30 min está bom, ou quer mais/menos agressivo?
