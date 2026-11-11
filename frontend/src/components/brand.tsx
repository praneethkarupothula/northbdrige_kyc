import { Link } from "react-router-dom";

export function Logo({ to = "/", sub }: { to?: string; sub?: string }) {
  return (
    <Link to={to} className="flex items-center gap-2.5 rounded-sm" aria-label="Northbridge KYC home">
      <svg viewBox="0 0 32 32" className="size-8" aria-hidden>
        <rect x="1" y="1" width="30" height="30" rx="6" fill="#12343b" />
        <path d="M8 23V9l8 9V9M24 9v14l-8-9" stroke="#f4f7f3" strokeWidth="2.2" fill="none" strokeLinejoin="round" />
      </svg>
      <span className="leading-tight">
        <span className="block font-serif text-[17px] font-semibold">Northbridge</span>
        <span className="block text-xs text-muted-foreground">{sub ?? "Applicant verification"}</span>
      </span>
    </Link>
  );
}

export function Disclaimer({ className = "" }: { className?: string }) {
  return (
    <p className={`text-xs text-muted-foreground ${className}`}>
      Northbridge checks that the names on your documents are consistent, and a Northbridge reviewer approves every
      application. This is not an Aadhaar or PAN authentication with government records, and it is not a loan or credit decision.
    </p>
  );
}
