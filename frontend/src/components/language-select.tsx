'use client';

import { useI18n, type Locale } from '@/lib/i18n';

/** The interface language; the provider saves the choice and sets <html lang>. */
export function LanguageSelect({ className }: { className?: string }) {
  const { locale, setLocale, t } = useI18n();
  return (
    <select
      id="app-language"
      name="language"
      aria-label={t('language')}
      className={className}
      value={locale}
      onChange={(event) => setLocale(event.target.value as Locale)}
    >
      <option value="ru">Русский</option>
      <option value="en">English</option>
    </select>
  );
}
