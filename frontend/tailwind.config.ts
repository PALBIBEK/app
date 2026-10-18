import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{js,ts,jsx,tsx,mdx}'],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#eff6ff',
          100: '#dbeafe',
          500: '#3b82f6',
          600: '#2563eb',
          900: '#1e3a8a',
        },
      },
      backgroundImage: {
        'gradient-subtle': 'linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%)',
      },
    },
  },
  plugins: [],
};

export default config;
