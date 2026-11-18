import { Bomb, Crosshair, Rocket, ScanEye } from "lucide-react";

/** A tank in Lucide's style (Lucide has none). */
export function TankIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden>
      <rect x="2" y="14" width="20" height="6" rx="3" />
      <path d="M4 14l2-4h11l2 4" />
      <path d="M9 10V7.5h5V10" />
      <path d="M14 8.5h8" />
      <circle cx="7" cy="17" r="0.5" />
      <circle cx="12" cy="17" r="0.5" />
      <circle cx="17" cy="17" r="0.5" />
    </svg>
  );
}

export const CATEGORY_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  "2530": TankIcon,
  "1005": Crosshair,
  "1010": Bomb,
  "1055": Rocket,
  "5855": ScanEye,
};
