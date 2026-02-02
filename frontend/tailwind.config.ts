import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Light theme base - ice rink inspired
        background: '#f8fafc',
        surface: '#ffffff',
        'surface-elevated': '#f1f5f9',
        'surface-hover': '#e2e8f0',
        border: '#e2e8f0',
        'border-dark': '#cbd5e1',

        // Text colors
        'text-primary': '#0f172a',
        'text-secondary': '#475569',
        'text-muted': '#94a3b8',

        // NHL Primary - Deep Blue
        primary: {
          DEFAULT: '#041E42',
          light: '#0a3161',
          dark: '#020f21',
          50: '#e8f4fc',
          100: '#c5e1f7',
          500: '#041E42',
          600: '#031733',
        },

        // Accent - Power Play Red
        accent: {
          DEFAULT: '#C8102E',
          light: '#e31837',
          dark: '#9a0c23',
          muted: '#fce8eb',
        },

        // Ice Blue - highlights
        ice: {
          DEFAULT: '#5bc0eb',
          light: '#a8ddf5',
          dark: '#2da8df',
        },

        // Success/Warning/Error
        success: '#10b981',
        warning: '#f59e0b',
        error: '#ef4444',
      },
      fontFamily: {
        sans: ['var(--font-inter)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'monospace'],
      },
      animation: {
        'fade-in': 'fadeIn 0.5s ease-out',
        'slide-up': 'slideUp 0.5s ease-out',
        'slide-in-right': 'slideInRight 0.3s ease-out',
        'pulse-slow': 'pulse 3s infinite',
        'gradient': 'gradient 8s ease infinite',
        'shimmer': 'shimmer 2s linear infinite',
        'float': 'float 6s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideInRight: {
          '0%': { opacity: '0', transform: 'translateX(20px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        gradient: {
          '0%, 100%': { backgroundPosition: '0% 50%' },
          '50%': { backgroundPosition: '100% 50%' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        float: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-10px)' },
        },
      },
      backgroundImage: {
        'gradient-radial': 'radial-gradient(var(--tw-gradient-stops))',
        'gradient-nhl': 'linear-gradient(135deg, #041E42 0%, #C8102E 100%)',
        'gradient-ice': 'linear-gradient(180deg, #e8f4fc 0%, #ffffff 100%)',
        'shimmer': 'linear-gradient(90deg, transparent, rgba(255,255,255,0.4), transparent)',
      },
      boxShadow: {
        'soft': '0 2px 15px -3px rgba(0, 0, 0, 0.07), 0 10px 20px -2px rgba(0, 0, 0, 0.04)',
        'card': '0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05)',
        'nhl': '0 4px 20px -2px rgba(4, 30, 66, 0.15)',
      },
    },
  },
  plugins: [],
}
export default config
