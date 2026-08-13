import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { AppSidebar } from '@/components/app-sidebar';
import { renderApp } from '@/test/render';

describe('locale provider', () => {
  it('starts in English and persists a Russian selection', async () => {
    window.localStorage.clear();
    renderApp(<AppSidebar />);
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Language'), 'ru');
    expect(screen.getByText('Панель')).toBeInTheDocument();
    expect(window.localStorage.getItem('gla-locale')).toBe('ru');
    expect(document.documentElement.lang).toBe('ru');
  });
});
