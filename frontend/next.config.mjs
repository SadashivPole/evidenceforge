/** @type {import('next').NextConfig} */
const backendOrigin = (
  process.env.EVIDENCEFORGE_API_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/backend-api/:path*",
        destination: `${backendOrigin}/:path*`,
      },
    ];
  },
};

export default nextConfig;
