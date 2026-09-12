import tailwindcss from "@tailwindcss/vite";

// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: "2025-07-15",
  // Keep the dashboard strictly client-side and deploy as a static SPA.
  // This avoids Node-server runtime mismatches on Cloudflare Pages / nginx.
  nitro: {
    preset: "static",
  },
  // Disabled while testing through a Cloudflare Tunnel — devtools' cross-origin
  // RPC connection gets rejected by the tunnel's origin and just adds noise.
  devtools: { enabled: false },
  // Dashboard behind auth, all data client-fetched from the FastAPI backend —
  // no need for SSR here, and it keeps localStorage-based JWT handling simple.
  ssr: false,
  css: ["~/assets/css/main.css"],
  // shadcn-vue's generated ui/<name>/index.ts barrels (named re-exports, for
  // `import { X } from '@/components/ui/x'`) aren't components themselves —
  // without this, Nuxt's default scan picks them up too and warns about
  // duplicate names clashing with the real `.vue` component right next to
  // them (e.g. ui/dialog/Dialog.vue vs ui/dialog/index.ts, both -> UiDialog).
  components: [
    { path: "~/components/ui", pathPrefix: false, extensions: ["vue"] },
    { path: "~/components", extensions: ["vue"] },
  ],
  vite: {
    plugins: [tailwindcss()],
    server: {
      // Vite's dev server rejects requests with an unrecognized Host header by
      // default (DNS-rebinding protection) — without this, a Cloudflare Tunnel
      // domain gets silently blocked even though the tunnel itself is fine.
      allowedHosts: true,
      // HMR config is conditional:
      // - Tunnel mode (TUNNEL_HOST set): use wss + clientPort 443 so the
      //   browser's HMR WebSocket goes to the tunnel's HTTPS endpoint.
      // - Local mode (no TUNNEL_HOST): leave hmr undefined so Vite picks
      //   sensible defaults (ws on the same port as the dev server).
      //   Using wss+443 locally causes @fs/ module URLs to fail MIME checks.
      ...(process.env.TUNNEL_HOST
        ? {
            hmr: {
              protocol: "wss",
              clientPort: 443,
              host: process.env.TUNNEL_HOST,
            },
          }
        : {}),
      fs: { strict: false },
    },
  },
  app: {
    head: {
      title: "Mivo AI — Smart Instagram AI Assistant",
      meta: [
        { charset: "utf-8" },
        // interactive-widget: Android shrinks the layout (and 100dvh) when the
        // keyboard opens, so the chat composer stays visible above it.
        {
          name: "viewport",
          content: "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, interactive-widget=resizes-content",
        },
        { name: "description", content: "Mivo AI — Instagram Direct sales automation and AI lead qualification platform." },
        { property: "og:title", content: "Mivo AI — Smart Instagram AI Assistant" },
        { property: "og:description", content: "Automation platform for Instagram Direct sales, customer support, and AI lead qualification." },
        { property: "og:image", content: "/logo.png" },
        { name: "theme-color", content: "#16151f" },
      ],
      link: [
        { rel: "icon", type: "image/x-icon", href: "/favicon.ico" },
        { rel: "icon", type: "image/png", href: "/favicon.png" },
        { rel: "apple-touch-icon", href: "/apple-touch-icon.png" },
      ],
    },
  },
  runtimeConfig: {
    public: {
      apiBase: process.env.NUXT_PUBLIC_API_BASE || "/api",
    },
  },
});
