Estado de uma conexão Open Finance: ícone ou spinner, rótulo no tom do estado, detalhe e ação, de `app/src/ui/componentes/ConnectionStatus.tsx`.

**Quem fornece:** `state` (o estado do backend), `label`, e opcionalmente `detail` e `action`.

Estados: `updated` (verde, check), `partial`, `error_recoverable` e `needs_user_action` (`warning`), `item_missing` (`danger`, elo quebrado), `updating` (spinner), `paused`, `removed`, `no_accounts` (`ink-muted`). Estado desconhecido mostra `Question` em `ink-muted`.
