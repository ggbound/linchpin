// Lightweight mirror of the backend's croniter validation. Catches
// obvious typos (missing field, prose, */0) before a 422 round-trip;
// the backend remains the source of truth for exotic syntax.
const CRON_ITEM_RE = /^(\*|\d+|[a-z]{3})(?:-(\*|\d+|[a-z]{3}))?(?:\/(\d+))?$/i;
const CRON_ALIAS_RE = /^@(yearly|annually|monthly|weekly|daily|hourly|midnight)$/;

function isValidCronField(field: string): boolean {
  return field.split(',').every(item => {
    const match = CRON_ITEM_RE.exec(item);
    if (!match) return false;
    // Step must be a positive integer (*/0 would never fire).
    if (match[3] !== undefined && Number(match[3]) < 1) return false;
    return true;
  });
}

export function isValidCronExpr(expr: string): boolean {
  const trimmed = expr.trim();
  if (!trimmed) return false;
  if (CRON_ALIAS_RE.test(trimmed.toLowerCase())) return true;
  const fields = trimmed.split(/\s+/);
  if (fields.length !== 5) return false;
  return fields.every(isValidCronField);
}
