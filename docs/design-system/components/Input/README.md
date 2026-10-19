Campo de texto com rótulo, borda de 1px e erro embaixo, de `app/src/ui/componentes/Input.tsx`.

**Quem fornece:** `label` (sempre visível, nunca só placeholder), `value`, `onChange(value)`, e opcionalmente `error`, `icon`, `placeholder`, `type`, `disabled` e `readOnly`.

Fundo `surface-control`, raio `radius-control`, altura mínima 44px. A borda é `ink-muted` (5,5:1 no claro), `danger` com erro e `ink-faint` quando inativo. O erro aparece em `caption` `danger` e é anunciado ao leitor de tela. `readOnly` mostra o valor como texto, sem campo.
