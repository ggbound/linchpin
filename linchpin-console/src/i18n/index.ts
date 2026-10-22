import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

// Locale files live in locales/<lng>/<namespace>.json and are picked up
// automatically, so adding a namespace never requires touching this file.
const zhModules = import.meta.glob('./locales/zh-CN/*.json', { eager: true });
const enModules = import.meta.glob('./locales/en/*.json', { eager: true });

function bundle(modules: Record<string, unknown>): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  for (const [path, mod] of Object.entries(modules)) {
    const ns = (path.split('/').pop() || '').replace(/\.json$/, '');
    const loaded = mod as { default?: Record<string, unknown> };
    out[ns] = loaded.default ?? (mod as Record<string, unknown>);
  }
  return out;
}

export const LANG_STORAGE_KEY = 'linchpin_lang';

void i18n.use(initReactI18next).init({
  resources: {
    'zh-CN': bundle(zhModules),
    en: bundle(enModules),
  },
  // Simplified Chinese is the default for every first visit; the choice is
  // persisted in localStorage after the user switches language once.
  lng: localStorage.getItem(LANG_STORAGE_KEY) || 'zh-CN',
  fallbackLng: 'zh-CN',
  interpolation: { escapeValue: false },
  returnEmptyString: false,
  // Resources are bundled inline, so initialization can complete
  // synchronously before the first render. Without this, react-i18next's
  // default useSuspense would suspend the tree on first paint and, with no
  // Suspense boundary in the app, React tears it down leaving a blank page.
  initAsync: false,
  react: { useSuspense: false },
});

export function setAppLanguage(lng: 'zh-CN' | 'en'): void {
  localStorage.setItem(LANG_STORAGE_KEY, lng);
  document.documentElement.lang = lng;
  document.title = lng === 'en' ? 'Linchpin Console' : 'Linchpin 控制台';
  void i18n.changeLanguage(lng);
}

export default i18n;
