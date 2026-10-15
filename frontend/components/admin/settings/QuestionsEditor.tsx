"use client";

import React from "react";
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import { iconButton, secondaryButton } from "@/components/ui/styles";
import { MAX_QUESTION_CHARS, MAX_SAMPLE_QUESTIONS } from "@/lib/settings-api";
import { inputClass } from "./fields";

interface QuestionsEditorProps {
  value: string[];
  onChange: (value: string[]) => void;
  error?: string;
}

/** Ordered list editor for the chat sample questions (1 to 8, one line each). */
export function QuestionsEditor({ value, onChange, error }: QuestionsEditorProps) {
  const update = (index: number, text: string) => onChange(value.map((q, i) => (i === index ? text : q)));
  const remove = (index: number) => onChange(value.filter((_, i) => i !== index));
  const move = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    if (target < 0 || target >= value.length) return;
    const next = [...value];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };

  return (
    <div className="space-y-2">
      <ol className="space-y-2" aria-label="Chat sample questions">
        {value.map((question, index) => (
          // Index keys: rows have no identity beyond their position.
          <li key={index} className="flex items-center gap-1.5">
            <span className="w-5 shrink-0 text-right text-[11px] font-semibold tabular-nums text-fg-subtle">{index + 1}.</span>
            <input
              type="text"
              value={question}
              maxLength={MAX_QUESTION_CHARS}
              aria-label={`Sample question ${index + 1}`}
              aria-invalid={Boolean(error) && !question.trim()}
              onChange={(e) => update(index, e.target.value)}
              className={inputClass}
            />
            <button type="button" className={iconButton} aria-label={`Move question ${index + 1} up`} disabled={index === 0} onClick={() => move(index, -1)}>
              <ArrowUp className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </button>
            <button
              type="button"
              className={iconButton}
              aria-label={`Move question ${index + 1} down`}
              disabled={index === value.length - 1}
              onClick={() => move(index, 1)}
            >
              <ArrowDown className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </button>
            <button type="button" className={iconButton} aria-label={`Remove question ${index + 1}`} onClick={() => remove(index)}>
              <X className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ol>
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          className={secondaryButton}
          disabled={value.length >= MAX_SAMPLE_QUESTIONS}
          onClick={() => onChange([...value, ""])}
        >
          <Plus className="h-4 w-4" strokeWidth={1.75} aria-hidden="true" />
          Add question
        </button>
        <span className="text-[11px] tabular-nums text-fg-subtle">
          {value.length} / {MAX_SAMPLE_QUESTIONS}
        </span>
      </div>
      {error ? <p className="text-[11px] font-medium text-status-danger-fg">{error}</p> : null}
    </div>
  );
}
