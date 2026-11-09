import type { KeyboardEvent } from "react";
import type { FieldValue } from "@/api/types";
import { CitationChip } from "@/components/CitationChip";
import { ConfidenceBar } from "@/components/ConfidenceBar";
import { StatusBadge } from "@/components/StatusBadge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCitationDrawer } from "@/hooks/useCitationDrawer";
import { formatValue } from "@/lib/utils";

interface FieldsTableProps {
  bidId: string;
  fields: Record<string, FieldValue>;
}

const NOT_FOUND_TEXT = "Not found in documents";

/** The 20-field extraction table; clicking a row opens the citation drawer. */
export function FieldsTable({ bidId, fields }: FieldsTableProps) {
  const { open } = useCitationDrawer();

  const openRow = (name: string, field: FieldValue, activeIndex?: number) =>
    open({ title: name, bidId, citations: field.sources, notes: field.notes, activeIndex });

  const onKeyDown = (e: KeyboardEvent<HTMLTableRowElement>, name: string, field: FieldValue) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openRow(name, field);
    }
  };

  return (
    <Table className="table-fixed">
      <TableHeader>
        <TableRow>
          <TableHead className="w-[30%] md:w-[180px]">Field</TableHead>
          <TableHead>Value</TableHead>
          <TableHead className="hidden w-[120px] md:table-cell">Confidence</TableHead>
          <TableHead className="w-[88px]">Status</TableHead>
          <TableHead className="hidden lg:table-cell lg:w-[190px] xl:w-[280px]">Citations</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {Object.entries(fields).map(([name, field]) => {
          const text = formatValue(field.value);
          return (
            <TableRow
              key={name}
              tabIndex={0}
              role="button"
              aria-label={`Show citations for ${name}`}
              className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              onClick={() => openRow(name, field)}
              onKeyDown={(e) => onKeyDown(e, name, field)}
            >
              <TableCell className="align-top font-medium md:align-middle">{name}</TableCell>
              <TableCell className="align-top md:align-middle">
                {field.value === null ? (
                  <span className="italic text-muted-foreground [overflow-wrap:anywhere]">{field.notes || NOT_FOUND_TEXT}</span>
                ) : (
                  <span className="line-clamp-3 whitespace-pre-wrap [overflow-wrap:anywhere]" title={text}>
                    {text}
                  </span>
                )}
              </TableCell>
              <TableCell className="hidden md:table-cell">
                <ConfidenceBar confidence={field.confidence} />
              </TableCell>
              <TableCell className="whitespace-nowrap align-top md:align-middle">
                <StatusBadge status={field.status} />
              </TableCell>
              <TableCell className="hidden lg:table-cell">
                <div className="flex flex-wrap gap-1">
                  {field.sources.length === 0 && (
                    <span className="text-xs text-muted-foreground">-</span>
                  )}
                  {field.sources.slice(0, 3).map((c, i) => (
                    <CitationChip
                      key={`${c.chunk_id}-${i}`}
                      citation={c}
                      className="lg:max-w-[11rem] xl:max-w-[16rem]"
                      onClick={() => openRow(name, field, i)}
                    />
                  ))}
                  {field.sources.length > 3 && (
                    <span className="text-xs text-muted-foreground">+{field.sources.length - 3}</span>
                  )}
                </div>
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
