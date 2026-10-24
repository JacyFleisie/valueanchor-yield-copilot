/** @type {import('next').NextConfig} */
// The desktop build ships the interface inside the executable, so there is no
// Next.js server on the user's machine: `next build` writes plain HTML/CSS/JS to
// `out/` and the packaged Python server serves that folder.
const nextConfig = {
  output: 'export',
  // The static export has no server to resize images on demand.
  images: { unoptimized: true },
  // `out/index.html` is served at "/" by the desktop server; trailing slashes
  // keep asset URLs relative to that folder.
  trailingSlash: true,
}

module.exports = nextConfig
