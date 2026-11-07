import type { NextConfig } from 'next';

const backend = process.env.THORAX_API_ORIGIN;
if (backend) {
  const origin = new URL(backend);
  if (!['http:', 'https:'].includes(origin.protocol) || origin.origin !== backend.replace(/\/$/, '')) {
    throw new Error('THORAX_API_ORIGIN must be an HTTP(S) origin without a path.');
  }
}

const config: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  distDir: process.env.THORAX_BUILD_DIR || '.next',
  logging: { incomingRequests: false },
  async headers() {
    const privateHeaders = [
      { key: 'Cache-Control', value: 'private, no-store' },
      { key: 'Referrer-Policy', value: 'no-referrer' },
      { key: 'X-Content-Type-Options', value: 'nosniff' },
    ];
    return ['/login', '/worklist', '/admin', '/patients/:path*', '/api/v1/:path*'].map(source => ({ source, headers: privateHeaders }));
  },
  async rewrites() {
    return { beforeFiles: backend ? [{ source: '/api/v1/:path*', destination: `${new URL(backend).origin}/api/v1/:path*` }] : [], afterFiles: [], fallback: [] };
  },
};

export default config;
