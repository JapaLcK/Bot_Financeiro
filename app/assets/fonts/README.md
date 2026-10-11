# Fontes do app

Inter 4.001 estática (SIL OFL 1.1, licença em `Inter-LICENSE.txt`), com as
features `cv11`, `ss01` e `ss03` congeladas nos TTF (o React Native não liga
`cv11`). `tnum` não foi congelado: continua ligado por `fontVariant` no `Texto`.

Comando (opentype-feature-freezer 1.32.2), sem `-S`/`-U`: o nome da família não muda.

    pyftfeatfreeze -f 'cv11,ss01,ss03' Inter-<Peso>.ttf Inter-<Peso>.ttf

Pesos: Regular, Medium, SemiBold. Para trocar de versão da Inter, parta do TTF
original e repita o comando.
