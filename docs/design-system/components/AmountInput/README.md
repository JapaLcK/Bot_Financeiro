Campo de valor em reais que digita da direita para a esquerda, como caixa registradora, de `app/src/ui/componentes/AmountInput.tsx`.

**Quem fornece:** `label`, `cents` (inteiro) e `onChange(cents)`, e opcionalmente `error` e `disabled`.

"R$" e o número usam o estilo `title` com algarismos tabulares. Cada dígito entra pela direita: digitar 4, 5, 9, 0 dá R$ 45,90. O teto é R$ 99.999.999,99. Teclado numérico no celular. O app é mais rígido com colagem de texto; esta versão web só guarda os dígitos.
