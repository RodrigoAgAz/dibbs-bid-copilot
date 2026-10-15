import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: { default: "The Invisible Hand", template: "%s · The Invisible Hand" },
  description: "Winning-price predictions and bid recommendations for DLA DIBBS solicitations",
  icons: { icon: "/favicon.svg" },
};

// System font stack on purpose: no build-time font downloads, so builds work offline.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
