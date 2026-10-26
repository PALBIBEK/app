import type { Config } from 'tailwindcss';

/**
 * Design tokens, from the "Agent Platform" design canvas.
 *
 * One neutral ground, graphite text, a single indigo accent used only for what
 * needs action. Status is told apart by shape and lightness as well as colour.
 * `primary` keeps its old name so existing classes pick up the accent.
 */
const accent = {
  50: '#EEEEFD', // tint: selected nav, approval panels
  100: '#E0DFFC',
  200: '#D9D7FB', // border on tinted surfaces
  500: '#4F46E5', // the accent
  600: '#4338CA', // hover / pressed
  700: '#3730A3', // text on tint
  900: '#1E1B4B',
};

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        primary: accent,
        accent,
        // The pages written before the design use `slate-*`; these are the
        // design's neutrals, so they match without rewriting every class.
        // 400 is darkened to pass 4.5:1 on white (the stock one did not).
        slate: {
          50: '#F6F7F9',
          100: '#ECEEF2',
          200: '#E2E5EA',
          300: '#D3D7DE',
          400: '#6B7079',
          500: '#5F6570',
          600: '#4A505A',
          700: '#33373E',
          800: '#26292F',
          900: '#1C1F24',
        },
        canvas: '#F6F7F9', // page ground
        surface: '#FFFFFF', // cards, header, sidebar
        ink: {
          DEFAULT: '#1C1F24', // body text
          2: '#4A505A', // secondary text
          3: '#5F6570', // tertiary text, table headers (4.6:1 on white)
        },
        line: {
          DEFAULT: '#E2E5EA', // card borders
          soft: '#ECEEF2', // row dividers, chip borders
          strong: '#D3D7DE', // inputs, secondary buttons
        },
        good: '#1F7A4D',
        warn: '#9A5B0B',
        bad: '#B42318',
      },
      fontFamily: {
        sans: ['var(--font-geist-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['var(--font-geist-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        '2xs': ['11.5px', '16px'],
        xs: ['12px', '16px'],
        sm: ['13.5px', '20px'],
        base: ['14px', '21px'],
      },
      borderRadius: {
        card: '12px',
      },
      backgroundImage: {
        // Older pages used a subtle gradient; the design is a flat ground.
        'gradient-subtle': 'none',
      },
    },
  },
  plugins: [],
};

export default config;
