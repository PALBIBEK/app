import { securityHeaders } from './security-headers.mjs';

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders({ production: process.env.NODE_ENV === 'production' }) }];
  },
};

export default nextConfig;
