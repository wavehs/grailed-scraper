import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  outputFileTracingRoot: path.join(__dirname, '../'),
  output: process.env.NEXT_EXPORT === 'false' ? undefined : 'export',
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
