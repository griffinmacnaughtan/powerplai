/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Static export for GitHub Pages
  output: 'export',
  // Disable image optimization for static export
  images: {
    unoptimized: true,
  },
  // Base path for GitHub Pages (repository name)
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || '',
  // Trailing slash for static hosting
  trailingSlash: true,
}

module.exports = nextConfig
