// IANA timezone options for deployment cron scheduling.
//
// `Intl.supportedValuesOf('timeZone')` requires the ES2022 Intl API which is
// missing from this project's tsconfig lib (ES2020) — access it defensively and
// fall back to a static list of common zones when unavailable.

const FALLBACK_TIMEZONES = [
  'UTC',
  'Asia/Shanghai',
  'Asia/Hong_Kong',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Asia/Seoul',
  'Asia/Kolkata',
  'Asia/Dubai',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Europe/Moscow',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Sao_Paulo',
  'Australia/Sydney',
  'Pacific/Auckland',
];

export const TIMEZONES: string[] = (() => {
  try {
    const supported = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] })
      .supportedValuesOf?.('timeZone');
    if (supported && supported.length > 0) {
      return supported.includes('UTC') ? [...supported] : ['UTC', ...supported];
    }
  } catch {
    // Older runtime without supportedValuesOf — use the fallback list.
  }
  return FALLBACK_TIMEZONES;
})();
