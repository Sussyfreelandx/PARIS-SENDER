/** @type {import('tailwindcss').Config} */
// Local Tailwind config for the Electron renderer. `content` points at the
// renderer sources so Tailwind can tree-shake unused utilities.
export default {
  content: ['./renderer/index.html', './renderer/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {},
  },
  plugins: [],
};
