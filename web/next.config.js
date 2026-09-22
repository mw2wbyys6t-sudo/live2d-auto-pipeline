/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: {
    unoptimized: true,
  },
  trailingSlash: false,
  output: 'standalone',
  // Allow preview environments and local development
  allowedDevOrigins: [
    '127.0.0.1',
    'localhost',
    'run-agent-6a708b26e262f904bb42bec5-msd7q5pk-preview.agent-sandbox-bj-d1-gw.traecontent.cn',
  ],
  async rewrites() {
    const backend = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8080';
    return [
      {
        source: '/api/:path*',
        destination: `${backend}/api/:path*`,
      },
      {
        source: '/ws',
        destination: `${backend}/ws`,
      },
    ];
  },
};

module.exports = nextConfig;
