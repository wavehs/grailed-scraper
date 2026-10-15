import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MetricBadges } from '@/components/metric-badges';
import { liquidityEn, liquidityRu } from '@/lib/i18n-liquidity';
import { renderApp } from '@/test/render';

const CODES = ['low_data', 'hot', 'stable', 'slow', 'saturated'];

describe('MetricBadges', () => {
  it('shows each known badge as an emoji with its rule in the tooltip, in a fixed order', () => {
    renderApp(<MetricBadges badges={['saturated', 'hot', 'mystery']} />);

    const shown = screen.getAllByRole('img');
    expect(shown.map((badge) => badge.textContent)).toEqual(['🔥', '⚠️']);
    expect(shown.every((badge) => (badge.getAttribute('title') ?? '').length > 10)).toBe(true);
  });

  it('renders nothing without badges', () => {
    const { container } = renderApp(<MetricBadges badges={undefined} />);

    expect(container).toBeEmptyDOMElement();
  });

  it('has a name and a rule for every badge in both languages', () => {
    for (const strings of [liquidityEn, liquidityRu]) {
      for (const code of CODES) {
        expect(strings[`badge_${code}`]).toBeTruthy();
        expect(strings[`badge_${code}_help`]).toBeTruthy();
      }
    }
  });
});
