/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx}',
    './components/**/*.{js,ts,jsx,tsx}',
    './lib/**/*.{js,ts,jsx,tsx}',
    './hooks/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // 浅色底座：与开机页同源（#f7f5fa 纸白 + 粉紫青极光）
        // 数值越大越"高"（越接近纯白），便于面板/卡片叠加。
        ink: {
          950: '#f6f2fc',
          900: '#fbf9ff',
          850: '#ffffff',
          800: '#ffffff',
          750: '#ffffff',
          700: '#faf7ff',
          650: '#f2edfa',
          600: '#e7dff4',
        },
        // 主强调：樱花粉
        sakura: {
          200: '#ffd0e2',
          300: '#ffb3d1',
          400: '#ff87b7',
          500: '#ff5c9d',
          600: '#f0397f',
        },
        // 次强调：鸢尾紫
        iris: {
          200: '#ddd0ff',
          300: '#c4b5fd',
          400: '#a78bfa',
          500: '#8b5cf6',
          600: '#7c3aed',
        },
        // 信息/完成：薄荷青（600 用于浅底上的文字，保证对比度）
        mint: {
          300: '#7ff0dc',
          400: '#34e0c4',
          500: '#14b8a6',
          600: '#0f766e',
        },
        // 文本：雾紫灰（浅底上是深墨紫 → 灰）
        haze: {
          100: '#2b2438',
          200: '#3d3552',
          300: '#544c6b',
          400: '#6f6586',
          500: '#877d9b',
          600: '#998fa8',
        },
      },
      screens: {
        xs: '320px',
        sm: '480px',
        md: '768px',
        lg: '1024px',
        xl: '1280px',
        '2xl': '1536px',
      },
      borderRadius: {
        xl: '14px',
        '2xl': '18px',
        '3xl': '24px',
      },
      boxShadow: {
        panel: '0 30px 60px -40px rgba(58, 28, 110, 0.35)',
        card: '0 12px 24px -18px rgba(58, 28, 110, 0.28)',
        'glow-sakura': '0 0 0 1px rgba(255, 92, 157, 0.30), 0 16px 40px -18px rgba(255, 92, 157, 0.40)',
        'glow-iris': '0 0 0 1px rgba(139, 92, 246, 0.30), 0 16px 40px -18px rgba(139, 92, 246, 0.40)',
      },
      fontFamily: {
        sans: [
          'PingFang SC',
          'Microsoft YaHei',
          'Noto Sans SC',
          'Source Han Sans SC',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'Roboto',
          'sans-serif',
        ],
      },
      animation: {
        'pulse-glow': 'pulse-glow 2s ease-in-out infinite',
        gradient: 'gradient-shift 3s ease infinite',
        float: 'float 3s ease-in-out infinite',
        shimmer: 'shimmer 1.5s infinite',
        'scale-in': 'scale-in 0.3s ease-out forwards',
        'bounce-in': 'bounce-in 0.5s ease-out forwards',
        'stage-in': 'stage-in 0.18s ease-out both',
      },
      keyframes: {
        'stage-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
};
