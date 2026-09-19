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

const plans = [
  {
    name: "Starter",
    price: "$0",
    cadence: "Free Forever",
    highlighted: false,
  },
  {
    name: "Growth",
    price: "$24",
    cadence: "Per User / Month",
    highlighted: true,
  },
  {
    name: "Enterprise",
    price: "Custom",
    cadence: "Talk to Sales",
    highlighted: false,
  },
] as const;

const groups: FeatureGroup[] = [
  {
    section: "Core",
    features: [
      { label: "Projects", values: ["3", "Unlimited", "Unlimited"] },
      { label: "Storage", values: ["2 GB", "100 GB", "1 TB+"] },
      { label: "API access", values: [false, true, true] },
      { label: "Custom workflows", values: [false, true, true] },
    ],
  },
  {
    section: "Collaboration",
    features: [
      { label: "Team members", values: ["Up to 3", "Up to 50", "Unlimited"] },
      { label: "Real-time editing", values: [true, true, true] },
      { label: "Guest access", values: [false, true, true] },
      {
        label: "Roles & permissions",
        values: ["Basic", "Advanced", "Granular"],
      },
    ],
  },
  {
    section: "Support",
    features: [
      {
        label: "Response time",
        values: ["Community", "Under 24h", "Under 1h"],
      },
      { label: "Priority queue", values: [false, true, true] },
      { label: "Dedicated manager", values: [false, false, true] },
      { label: "99.99% uptime SLA", values: [false, false, true] },
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
        <span className="sr-only">Included</span>
      </span>
    ) : (
      <span className="mx-auto flex size-5 items-center justify-center bg-muted">
        <RiCloseLine className="size-3.5 text-muted-foreground" aria-hidden />
        <span className="sr-only">Not included</span>
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
            Compare plans
          </Badge>
          <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
            Find the right plan for your team
          </h2>
          <p className="mt-3 text-sm text-muted-foreground">
            Every Acme plan scales with you. Compare features side by side and
            upgrade the moment you need more.
          </p>
        </div>

        <div className="relative">
          <Badge
            variant="default"
            className="absolute bottom-full left-[68%] z-20 mb-2 -translate-x-1/2"
          >
            Most Popular
          </Badge>
          <div className="overflow-x-auto border border-border">
            <Table className="table-fixed text-sm">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="sticky top-0 z-20 w-[36%] border-b border-border bg-background align-bottom">
                    <span className="inline-block pb-3 text-sm font-semibold tracking-wide text-muted-foreground uppercase">
                      Features
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
                        <span className="text-lg font-bold text-foreground">
                          {plan.price}
                        </span>
                        <span className="text-xs font-normal text-muted-foreground">
                          {plan.cadence}
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
                      <Button
                        asChild
                        size="sm"
                        variant={plan.highlighted ? "default" : "secondary"}
                        className="w-full"
                      >
                        <a href="#">
                          {plan.price === "Custom" ? "Contact us" : "Choose"}
                          <RiArrowRightLine data-icon="inline-end" />
                        </a>
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
