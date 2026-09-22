/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // On slow/networked filesystems (e.g. mounted /mnt), Next's dev server can
  // fail writing its generated types. Allow pointing the build output at a
  // fast local disk via NEXT_DIST_DIR. Defaults to the standard ".next".
  distDir: process.env.NEXT_DIST_DIR || '.next',
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
        source: '/output/:path*',
        destination: `${backend}/output/:path*`,
      },
      {
        source: '/ws',
        destination: `${backend}/ws`,
      },
      {
        source: '/ws/progress',
        destination: `${backend}/ws/progress`,
      },
    ];
  },
};

module.exports = nextConfig;
