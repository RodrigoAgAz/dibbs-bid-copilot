import type { Metadata } from "next";

export const metadata: Metadata = { title: "All lines" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
