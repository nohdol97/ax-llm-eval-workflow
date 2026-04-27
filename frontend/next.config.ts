import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Docker 멀티스테이지 빌드의 runtime 스테이지에서 .next/standalone 만
  // 복사하여 최소 이미지를 만들기 위한 설정. (frontend/Dockerfile 참조)
  output: "standalone",
};

export default nextConfig;
