Barras verticais de gasto por mês, com o mês atual em destaque, de `app/src/ui/componentes/BarrasMensais.tsx`.

**Quem fornece:** `months` (`[{key, label, value, current, dimmed}]`, valores em centavos) e `formatValue(value)` para o rótulo do mês atual.

Trilho de 26×120px em `surface-control`, barra `brand` (o mês atual cheio, os outros a 50%), e mês sem dado com `dimmed` a 40%. Só o mês atual mostra o valor; o rótulo dele fica em `brand-ink` com `*` e a nota "mês em andamento". As barras crescem em 450ms.
