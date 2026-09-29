/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    const api = process.env.INFRABRIX_API_URL ?? "http://localhost:8000";
    return [{ source: "/api/backend/:path*", destination: `${api}/:path*` }];
  },
};
export default nextConfig;
