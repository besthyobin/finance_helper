/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        background: '#0a0b0d',
        surface: '#111318',
        'surface-2': '#1a1d24',
        border: '#252932',
        'text-primary': '#e5e7eb',
        'text-secondary': '#9ca3af',
        'text-muted': '#6b7280',
        green: {
          DEFAULT: '#22c55e',
          dim: '#16a34a',
          bg: '#052e16',
        },
        red: {
          DEFAULT: '#ef4444',
          dim: '#dc2626',
          bg: '#2b0a0a',
        },
        blue: {
          DEFAULT: '#3b82f6',
          dim: '#2563eb',
          bg: '#0c1a3d',
        },
        gold: '#f59e0b',
      },
      fontFamily: {
        sans: ['var(--font-inter)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'monospace'],
      },
    },
  },
  plugins: [],
};
