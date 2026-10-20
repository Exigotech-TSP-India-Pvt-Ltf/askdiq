"use client";

import React from "react";
import Link from "next/link";
import { ENTITY_LABEL, entityHref, entityId, type EntityRef } from "@/lib/entity-links";
import { useCarryHref } from "@/lib/entity-routes";
import { CopyButton } from "@/components/ui/CopyButton";
import { focusRing } from "@/components/ui/styles";

interface EntityLinkProps {
  entity: EntityRef;
  /** Visible text (defaults to the id). */
  children?: React.ReactNode;
  /** Show the copy-id button (default true when the entity has an id). */
  copyable?: boolean;
  /** Render the label in a monospace id style (default true when no children). */
  mono?: boolean;
  className?: string;
}

/**
 * Renders any entity id as a deep link to the page that owns it, plus a copy
 * button. Clicks do not bubble to clickable table rows.
 */
export function EntityLink({ entity, children, copyable, mono, className = "" }: EntityLinkProps) {
  const carry = useCarryHref();
  const id = entityId(entity);
  const kindLabel = ENTITY_LABEL[entity.kind];
  const showCopy = (copyable ?? true) && id !== null;
  const monospace = mono ?? children === undefined;
  const text = children ?? id ?? kindLabel;

  return (
    <span className={`inline-flex min-w-0 max-w-full items-center gap-1 ${className}`}>
      <Link
        href={carry(entityHref(entity))}
        title={id ? `${kindLabel} ${id}` : kindLabel}
        data-entity-kind={entity.kind}
        onClick={(event) => event.stopPropagation()}
        className={`truncate rounded text-brand-700 underline-offset-2 transition-colors hover:text-brand-800 hover:underline dark:text-brand-300 dark:hover:text-brand-200 ${
          monospace ? "font-mono text-[11px]" : ""
        } ${focusRing}`}
      >
        {text}
      </Link>
      {showCopy && id ? <CopyButton value={id} label={`Copy ${kindLabel.toLowerCase()} id`} /> : null}
    </span>
  );
}
