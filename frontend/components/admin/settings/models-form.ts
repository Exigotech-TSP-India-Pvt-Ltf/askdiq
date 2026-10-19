/**
 * Pure form state for Settings > Models. A field left empty means "use the
 * .env value" (sent as `null`, which clears the override server-side).
 */
import {
  DEPLOYMENT_NAME_RE,
  MAX_PRICE_PER_MILLION,
  MODEL_FIELDS,
  PRICE_FIELDS,
  type ModelSettingsField,
  type ModelSettingsRead,
  type ModelSettingsUpdate,
} from "@/lib/settings-api";

export type ModelsForm = Record<ModelSettingsField, string>;

const FIELDS: readonly ModelSettingsField[] = [...MODEL_FIELDS, ...PRICE_FIELDS];
const isPrice = (field: ModelSettingsField) => (PRICE_FIELDS as readonly string[]).includes(field);

/** Overridden fields show their value; the rest stay empty (the .env value applies). */
export function formFromModelSettings(server: ModelSettingsRead): ModelsForm {
  const overridden = new Set(server.overridden);
  return Object.fromEntries(FIELDS.map((f) => [f, overridden.has(f) ? String(server[f]) : ""])) as ModelsForm;
}

export function validateModelsForm(form: ModelsForm): Partial<Record<ModelSettingsField, string>> {
  const errors: Partial<Record<ModelSettingsField, string>> = {};
  for (const field of FIELDS) {
    const value = form[field].trim();
    if (!value) continue;
    if (isPrice(field)) {
      const n = Number(value);
      if (!Number.isFinite(n) || n < 0 || n > MAX_PRICE_PER_MILLION) {
        errors[field] = `Enter a rate from 0 to ${MAX_PRICE_PER_MILLION} USD.`;
      }
    } else if (!DEPLOYMENT_NAME_RE.test(value)) {
      errors[field] = "Use letters, digits, '.', '_' or '-' (max 64).";
    }
  }
  return errors;
}

/** Fields that differ from `baseline`, as the PUT body expects them. */
export function diffModelsForm(baseline: ModelsForm, form: ModelsForm): Omit<ModelSettingsUpdate, "version"> {
  const changes: Record<string, string | number | null> = {};
  for (const field of FIELDS) {
    const before = baseline[field].trim();
    const after = form[field].trim();
    if (before === after) continue;
    changes[field] = after === "" ? null : isPrice(field) ? Number(after) : after;
  }
  return changes as Omit<ModelSettingsUpdate, "version">;
}
