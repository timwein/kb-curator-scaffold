import type { NextConfig } from "next";

const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "X-Robots-Tag", value: "noindex, nofollow" },
];

const nextConfig: NextConfig = {
  typedRoutes: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
  // API routes read KB markdown from `.kb-content/` (populated by
  // scripts/sync-content.ts during prebuild). This lives inside the project
  // root so Vercel's file tracer picks it up naturally.
  outputFileTracingIncludes: {
    "/api/rate/route": [".kb-content/**/*.md"],
    "/api/chat/route": [".kb-content/**/*.md"],
  },
};

export default nextConfig;
