Valor em reais a partir de **centavos inteiros**, nunca float, de `app/src/ui/componentes/Money.tsx`.

**Quem fornece:** `cents`, `type` (`saldo`, `entrada` ou `saida`), `variant` (`display`, `body`, `label`; padrão `body`) e `hidden` para o modo de saldo oculto.

- `entrada`: sinal `+` e cor `positive`. É o único uso do verde.
- `saida`: sinal `−` (U+2212) em `ink`. **Gasto nunca é rosa nem vermelho.**
- `saldo`: `ink`, com `−` só se negativo. Zero não leva sinal.
- `display`: os centavos ficam em `ink-muted` (ou no tom, se for entrada).

Sempre algarismos tabulares. O sinal e o texto carregam o significado; a cor só reforça.
