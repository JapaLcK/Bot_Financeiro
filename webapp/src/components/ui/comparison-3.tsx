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
    sub: "Organize tudo, sem limites",
    highlighted: false,
  },
  {
    id: "plus",
    name: "Plus",
    mensal: "R$ 19,90/mês",
    anual: "R$ 199/ano",
    sub: "Seu dinheiro no automático",
    highlighted: true,
  },
  {
    id: "pro",
    name: "Pro",
    mensal: "R$ 49,90/mês",
    anual: "R$ 499/ano",
    sub: "Planejamento e previsão",
    highlighted: false,
  },
] as const;

const groups: FeatureGroup[] = [
  {
    section: "Registro no WhatsApp",
    features: [
      { label: "Registro por texto", values: [true, true, true] },
      { label: "Lançamentos por mês", values: ["Ilimitados", "Ilimitados", "Ilimitados"] },
      { label: "Registro por áudio", values: [true, true, true] },
      { label: "Foto de cupom e comprovante", values: [true, true, true] },
      { label: "Importar extrato (OFX, CSV, PDF)", values: [true, true, true] },
    ],
  },
  {
    section: "Contas e organização",
    features: [
      { label: "Bancos conectados (Open Finance)", values: ["1", "2", "5"] },
      { label: "Investimentos no painel", values: [true, true, true] },
      { label: "Caixinhas e metas", values: ["Ilimitadas", "Ilimitadas", "Ilimitadas"] },
      { label: "Cartões", values: ["Ilimitados", "Ilimitados", "Ilimitados"] },
      { label: "Boletos com lembrete de vencimento", values: [true, true, true] },
      { label: "Gastos recorrentes", values: [true, true, true] },
    ],
  },
  {
    section: "Piggy IA",
    features: [
      // Plus/Pro: `ai_monthly_messages: None` cai no teto GLOBAL
      // AI_CHAT_MONTHLY_LIMIT, não em "ilimitado" (ver o card do Plus).
      { label: "Mensagens com a Piggy", values: ["200/mês", "1.000/mês", "1.000/mês"] },
      { label: "Categorização automática", values: [true, true, true] },
    ],
  },
  {
    section: "Agentes do Piggy",
    features: [
      { label: "Agentes liberados", values: [false, "Os 7", "Os 7"] },
      { label: "Energia para manter ligados", values: [false, "⚡ 4 · até 3 agentes", "⚡ 14 · a equipe inteira"] },
      { label: "Criar o seu próprio agente", values: [false, false, false] },
    ],
  },
  {
    section: "Histórico e relatórios",
    features: [
      { label: "Histórico que você enxerga", values: ["90 dias", "12 meses", "24 meses"] },
      { label: "Exportar seus dados", values: [true, true, true] },
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
                        <span className="text-xs font-normal text-muted-foreground">
                          {plan.sub}
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
