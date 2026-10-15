import type { Metadata } from "next";

export async function generateMetadata({ params }: { params: Promise<{ solicitationNumber: string }> }): Promise<Metadata> {
  const { solicitationNumber } = await params;
  return { title: decodeURIComponent(solicitationNumber).toUpperCase() };
}

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
