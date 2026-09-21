import * as React from "react";
import {
  RiCheckLine,
  RiCloseLine,
  RiArrowRightLine,
  RiSparkling2Line,
} from "@remixicon/react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

type CellValue = boolean | string;

type Feature = {
  label: string;
  values: [CellValue, CellValue, CellValue];
};

type FeatureGroup = {
  section: string;
  features: Feature[];
};

// A MESMA fonte da tabela servida que este bloco substituiu: cada linha está
// ancorada em core/services/plan_limits.py — mudou lá, muda aqui.
const plans = [
  {
    id: "essencial",
    name: "Essencial",
    mensal: "R$ 9,90/mês",
    anual: "R$ 99/ano",
    eq: "R$ 8,25",
    sub: "Organize tudo, sem limites",
    highlighted: false,
  },
  {
    id: "plus",
    name: "Plus",
    mensal: "R$ 19,90/mês",
    anual: "R$ 199/ano",
    eq: "R$ 16,58",
    sub: "Seu dinheiro no automático",
    highlighted: true,
  },
  {
    id: "pro",
    name: "Pro",
    mensal: "R$ 49,90/mês",
    anual: "R$ 499/ano",
    eq: "R$ 41,58",
    sub: "Planejamento e previsão",
    highlighted: false,
  },
] as const;

// O que é IDÊNTICO nos três planos sai da grade: uma linha ✓✓✓ não ajuda a
// decidir e dilui os diferenciais. O trial vem primeiro de propósito — é o
// redutor de fricção que antes só aparecia nas notas pequenas da página.
const commons = [
  "15 dias grátis pra testar · cancele antes e não paga nada",
  "Registro por texto, áudio e foto de cupom no WhatsApp",
  "Lançamentos ilimitados",
  "Importar extrato (OFX, CSV, PDF)",
  "Investimentos no painel",
  "Caixinhas, metas e cartões ilimitados",
  "Boletos e gastos recorrentes com lembretes",
  "Categorização automática",
  "Exportar seus dados",
];

// Só os DIFERENCIAIS ficam na grade. "Agentes liberados" e "Energia" viraram
// uma linha só, porque a promessa era dos 7 e a energia é quem manda: a linha
// agora diz exatamente o que cada plano entrega. "Criar o seu próprio agente"
// saiu — era ✗ nos TRÊS, uma linha inteira anunciando ausência; quando a
// feature existir, ela volta (idealmente como exclusivo do Pro).
const groups: FeatureGroup[] = [
  {
    section: "Essenciais",
    features: [
      { label: "Bancos conectados (Open Finance)", values: ["1", "2", "5"] },
      // Plus/Pro: `ai_monthly_messages: None` cai no teto GLOBAL
      // AI_CHAT_MONTHLY_LIMIT, não em "ilimitado" (ver o card do Plus).
      { label: "Mensagens com a Piggy", values: ["200/mês", "1.000/mês", "1.000/mês"] },
      { label: "Histórico que você enxerga", values: ["90 dias", "12 meses", "24 meses"] },
    ],
  },
  {
    section: "Agentes do Piggy",
    features: [
      { label: "Agentes ligados ao mesmo tempo", values: [false, "3 · você escolhe quais", "Os 7 · a equipe inteira"] },
    ],
  },
  {
    section: "Planejamento e previsão",
    features: [
      { label: "Previsão de saldo 30/60/90 dias", values: [false, false, true] },
      { label: "Relatórios semanais", values: [false, false, true] },
    ],
  },
];

function Cell({
  value,
  highlighted,
}: {
  value: CellValue;
  highlighted: boolean;
}) {
  if (typeof value === "boolean") {
    return value ? (
      <span
        className={cn(
          "mx-auto flex size-5 items-center justify-center",
          highlighted ? "bg-primary" : "bg-foreground/80",
        )}
      >
        <RiCheckLine
          className={cn(
            "size-3.5",
            highlighted ? "text-primary-foreground" : "text-background",
          )}
          aria-hidden
        />
        <span className="sr-only">Incluído</span>
      </span>
    ) : (
      <span className="mx-auto flex size-5 items-center justify-center bg-muted">
        <RiCloseLine className="size-3.5 text-muted-foreground" aria-hidden />
        <span className="sr-only">Não incluído</span>
      </span>
    );
  }

  return (
    <span
      className={cn(
        "text-sm font-medium",
        highlighted ? "text-foreground" : "text-muted-foreground",
      )}
    >
      {value}
    </span>
  );
}

export default function ComparisonBlock() {
  return (
    <section className="flex w-full justify-center bg-background px-6 py-10 text-foreground">
      <div className="mx-auto w-full max-w-5xl">
        <div className="mb-6 max-w-2xl">
          <Badge variant="outline" className="mb-4">
            <RiSparkling2Line data-icon="inline-start" />
            Compare os planos
          </Badge>
          <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
            Compare os planos lado a lado
          </h2>
          <p className="mt-3 text-sm text-muted-foreground">
            A mesma linha para todos os planos, para você ver exatamente o que
            ganha (e o que deixa de ter) em cada degrau.
          </p>
        </div>

        {/* O que é igual nos três fica FORA da grade: 12 linhas ✓✓✓ diluíam os
            diferenciais. O trial abre a lista porque é o que destrava a
            primeira decisão. */}
        <div className="mb-8 border border-border px-5 py-4">
          <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
            Tudo isso, em todos os planos
          </p>
          <ul className="mt-3 grid list-none grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
            {commons.map((item) => (
              <li key={item} className="flex items-center gap-2">
                <RiCheckLine className="size-4 shrink-0 text-primary" aria-hidden />
                {item}
              </li>
            ))}
          </ul>
        </div>

        <div className="relative">
          <Badge
            variant="default"
            className="absolute bottom-full left-[68%] z-20 mb-2 -translate-x-1/2 whitespace-nowrap"
          >
            Mais popular
          </Badge>
          <div className="overflow-x-auto border border-border">
            {/* min-w: sem ela o table-fixed espremeria as colunas no celular em
                vez de deixar o overflow-x-auto rolar (era o min-width:460px da
                tabela antiga). */}
            <Table className="table-fixed text-sm min-w-[560px]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="sticky top-0 z-20 w-[36%] border-b border-border bg-background align-bottom">
                    <span className="inline-block pb-3 text-sm font-semibold tracking-wide text-muted-foreground uppercase">
                      Recursos
                    </span>
                  </TableHead>
                  {plans.map((plan) => (
                    <TableHead
                      key={plan.name}
                      className={cn(
                        "sticky top-0 z-20 border-b border-border text-center align-bottom",
                        plan.highlighted ? "bg-primary/5" : "bg-background",
                      )}
                    >
                      <div className="flex flex-col items-center gap-1 py-3">
                        <span className="text-sm font-semibold text-foreground">
                          {plan.name}
                        </span>
                        {/* Os dois preços ficam no DOM e o `setCycle` da página
                            alterna pelo `style.display` — o mesmo contrato dos
                            [data-price-*] dos cards. */}
                        <span className="text-lg font-bold text-foreground whitespace-nowrap">
                          <span data-price-monthly>{plan.mensal}</span>
                          <span data-price-annual style={{ display: "none" }}>
                            {plan.anual}
                          </span>
                        </span>
                        {/* No ciclo anual a tagline cede lugar à equivalência
                            mensal — o mesmo argumento que os cards fazem com
                            o "Equivale a …". */}
                        <span className="text-xs font-normal text-muted-foreground">
                          <span data-price-monthly>{plan.sub}</span>
                          <span data-price-annual style={{ display: "none" }}>
                            Equivale a {plan.eq}/mês
                          </span>
                        </span>
                      </div>
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>

              <TableBody>
                {groups.map((group) => (
                  <React.Fragment key={group.section}>
                    <TableRow className="bg-muted/40 hover:bg-muted/40">
                      <TableCell
                        colSpan={4}
                        className="py-2 text-xs font-semibold tracking-wide text-foreground uppercase"
                      >
                        {group.section}
                      </TableCell>
                    </TableRow>
                    {group.features.map((feature) => (
                      <TableRow key={`${group.section}-${feature.label}`}>
                        <TableCell className="py-2.5 font-medium text-foreground">
                          {feature.label}
                        </TableCell>
                        {feature.values.map((value, i) => (
                          <TableCell
                            key={`${feature.label}-${plans[i].name}`}
                            className={cn(
                              "py-2.5 text-center",
                              plans[i].highlighted && "bg-primary/5",
                            )}
                          >
                            <Cell
                              value={value}
                              highlighted={plans[i].highlighted}
                            />
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </React.Fragment>
                ))}

                <TableRow className="hover:bg-transparent">
                  <TableCell className="py-4" />
                  {plans.map((plan) => (
                    <TableCell
                      key={`cta-${plan.name}`}
                      className={cn(
                        "py-4 text-center",
                        plan.highlighted && "bg-primary/5",
                      )}
                    >
                      {/* O onclick vai por ATRIBUTO, como nos cards (ver o
                          `crus` do Planos.jsx): o `refreshPlanButtons` troca o
                          handler pela PROPRIEDADE `btn.onclick`, e atributo e
                          propriedade dividem o mesmo slot — um onClick do
                          React (delegado na raiz) dispararia JUNTO com o da
                          troca de plano e abriria checkout + modal no mesmo
                          clique. */}
                      <Button
                        size="sm"
                        variant={plan.highlighted ? "default" : "secondary"}
                        className="w-full"
                        data-plan-btn={plan.id}
                        ref={(el) =>
                          el?.setAttribute(
                            "onclick",
                            `startCheckout(window.pbBillingCycle || "monthly", this, '${plan.id}')`,
                          )
                        }
                      >
                        Assinar {plan.name}
                        <RiArrowRightLine data-icon="inline-end" />
                      </Button>
                    </TableCell>
                  ))}
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </div>
      </div>
    </section>
  );
}
