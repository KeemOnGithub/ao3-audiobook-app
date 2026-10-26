/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        storySans: ['"Plus Jakarta Sans"', 'system-ui', 'sans-serif'],
        storySerif: ['Lora', 'Georgia', 'serif'],
        heading: ['Outfit', '"Plus Jakarta Sans"', 'sans-serif'],
      },
      colors: {
        coral: {
          50: '#fff5f0',
          100: '#ffe8de',
          400: '#ff8c42',
          500: '#ff5a1f',
          600: '#e0450e',
        },
        charcoal: {
          900: '#17181e',
          950: '#111215',
        },
        sepia: {
          100: '#fbf8f2',
          200: '#f4ecd8',
          800: '#4a3e30',
          900: '#3d3226',
        },
      },
    },
  },
  plugins: [],
}
