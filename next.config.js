/** @type {import('next').NextConfig} */
const nextConfig = {
  // yahoo-finance2를 서버 외부 패키지로 처리 (번들링 제외 → 테스트 모듈 누락 에러 방지)
  serverExternalPackages: ['yahoo-finance2'],
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: '**.yahoo.com' },
      { protocol: 'https', hostname: '**.reuters.com' },
      { protocol: 'https', hostname: '**.bloomberg.com' },
      { protocol: 'https', hostname: '**.cnbc.com' },
    ],
  },
  async headers() {
    return [
      {
        source: '/api/:path*',
        headers: [
          { key: 'Cache-Control', value: 's-maxage=60, stale-while-revalidate=300' },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
