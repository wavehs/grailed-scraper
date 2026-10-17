import type { Config } from 'tailwindcss';

/** Short names for the Linear tokens in globals.css; the variables stay the source of truth. */
const token = (name: string) => `var(--${name})`;

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
      colors: {
        canvas: token('bg-base'),
        sidebar: token('bg-sidebar'),
        surface: token('bg-surface'),
        raised: token('bg-surface-raised'),
        hover: token('bg-surface-hover'),
        control: { DEFAULT: token('bg-control'), hover: token('bg-control-hover') },
        selected: token('bg-selected'),
        line: { DEFAULT: token('border-subtle'), strong: token('border-default') },
        fg: {
          DEFAULT: token('text-primary'),
          body: token('text-body'),
          secondary: token('text-secondary'),
          muted: token('text-muted'),
        },
        accent: {
          DEFAULT: token('accent'),
          hover: token('accent-hover'),
          soft: token('accent-soft'),
        },
        success: token('success'),
        warning: token('warning'),
        danger: token('danger'),
        info: token('info'),
      },
      borderRadius: { DEFAULT: '5px', md: '6px', lg: '8px' },
      height: { topbar: token('topbar-height'), row: token('row-height') },
    },
  },
  plugins: [],
};
export default config;
