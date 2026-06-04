// Local PostCSS config for the Electron renderer.
//
// Declaring this file inside electron/ makes PostCSS resolve its config here
// instead of walking up the directory tree and picking up an unrelated config
// from a parent folder (which caused "Cannot find module 'tailwindcss'").
//
// The package is ESM ("type": "module"), so this uses `export default` rather
// than `module.exports`.
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
