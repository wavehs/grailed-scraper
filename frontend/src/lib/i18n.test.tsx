import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { AppSidebar } from '@/components/app-sidebar';
import { LanguageSelect } from '@/components/language-select';
import { messages } from '@/lib/i18n';
import { renderApp } from '@/test/render';

describe('locale provider', () => {
  it('starts in English and persists a Russian selection', async () => {
    window.localStorage.clear();
    renderApp(
      <>
        <AppSidebar />
        <LanguageSelect />
      </>,
    );
    expect(screen.getByText('Trends')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Language'), 'ru');
    expect(screen.getByText('Тренды')).toBeInTheDocument();
    expect(window.localStorage.getItem('gla-locale')).toBe('ru');
    expect(document.documentElement.lang).toBe('ru');
  });
});

describe('dictionaries', () => {
  it('translate every key the screens pass to t()', () => {
    const root = path.resolve(__dirname, '..');
    const sources = readdirSync(root, { recursive: true, encoding: 'utf8' })
      .filter((file) => /\.tsx?$/.test(file) && !/\.test\.tsx?$/.test(file) && !file.startsWith('test'))
      .map((file) => readFileSync(path.join(root, file), 'utf8'));
    const used = Array.from(
      new Set(
        sources.flatMap((source) =>
          Array.from(source.matchAll(/\bt\('(\w+)'\)/g), (match) => match[1]),
        ),
      ),
    );
    expect(used.length).toBeGreaterThan(100);
    for (const locale of ['en', 'ru'] as const) {
      const missing = used.filter((key) => !(key in messages[locale]));
      expect(missing, locale).toEqual([]);
    }
  });
});
