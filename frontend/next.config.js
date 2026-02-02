/** @type {import('next').NextConfig} */
const nextConfig = {
  // Enable React strict mode for better development experience
  reactStrictMode: true,
  // Output standalone for Docker deployment
  output: 'standalone',
}

module.exports = nextConfig
