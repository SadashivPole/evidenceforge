/** @type {import('next').NextConfig} */
const backendOrigin = (
  process.env.EVIDENCEFORGE_API_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

// Standalone output is opt-in (container images / OUTPUT_MODE=standalone);
// plain `npm run build` + `npm run start` keep the classic behavior used by
// local dev and the Playwright CI jobs.
const outputMode = process.env.OUTPUT_MODE;

const nextConfig = {
  ...(outputMode ? { output: outputMode } : {}),
  async redirects() {
    return [{ source: "/", destination: "/guided", permanent: false }];
  },
  // In standalone output mode the backend proxy moves to the runtime route
  // handler (app/backend-api/[...path]/route.ts), so EVIDENCEFORGE_API_URL is
  // resolved per process start instead of at build time. Keeping the build
  // rewrite out of standalone builds is intentional and verified.
  ...(outputMode === "standalone"
    ? {}
    : {
        async rewrites() {
          return [
            {
              source: "/backend-api/:path*",
              destination: `${backendOrigin}/:path*`,
            },
          ];
        },
      }),
};

export default nextConfig;
