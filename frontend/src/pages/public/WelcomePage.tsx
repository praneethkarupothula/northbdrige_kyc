import { Link } from "react-router-dom";
import { Disclaimer, Logo } from "@/components/brand";
import { Button } from "@/components/ui/button";

const STEPS = [
  { t: "Upload your documents", d: "Your Aadhaar card and one or more recent salary slips. A PAN card is optional, and you can add a bank statement." },
  { t: "We check that the names agree", d: "Text is read on Northbridge's servers. Personal details are masked before any automated extraction, and you can watch each step." },
  { t: "A Northbridge reviewer decides", d: "Matching documents are never approved automatically. A person reviews the evidence and you are notified of the outcome." },
];

export function WelcomePage() {
  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-5">
        <Logo />
        <nav className="flex items-center gap-2 text-sm">
          <Link to="/staff/login" className="hidden px-2 underline-offset-4 hover:underline sm:inline">Staff sign-in</Link>
          <Button asChild variant="outline" size="sm"><Link to="/login">Sign in</Link></Button>
        </nav>
      </header>
      <main className="mx-auto grid max-w-6xl gap-12 px-4 pb-20 pt-10 lg:grid-cols-[1.1fr_1fr] lg:pt-16">
        <section className="max-w-xl">
          <h1 className="text-4xl font-semibold sm:text-5xl">Verify your documents with Northbridge Finance</h1>
          <p className="mt-5 text-lg text-muted-foreground">
            Create an account, upload your identity and salary documents, and follow the check as it runs.
            You will see exactly which document needs attention if something does not line up.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button asChild size="lg"><Link to="/register">Create an account</Link></Button>
            <Button asChild size="lg" variant="outline"><Link to="/login">I already have an account</Link></Button>
          </div>
          <Disclaimer className="mt-10 max-w-md" />
        </section>
        <section aria-labelledby="how" className="rounded-xl border bg-card p-6 sm:p-8">
          <h2 id="how" className="text-xl font-semibold">How it works</h2>
          <ol className="mt-6 space-y-6">
            {STEPS.map((s, i) => (
              <li key={s.t} className="flex gap-4">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-full border-2 border-primary font-serif font-semibold">{i + 1}</span>
                <div>
                  <p className="font-medium">{s.t}</p>
                  <p className="text-sm text-muted-foreground">{s.d}</p>
                </div>
              </li>
            ))}
          </ol>
          <div className="mt-8 flex items-center gap-6 border-t pt-6">
            <p className="nb-typed text-sm">Documents match — awaiting Northbridge review.</p>
            <span className="nb-stamp text-stamp" aria-hidden>
              <span className="text-sm leading-none">Verified</span>
              <span className="text-[10px] font-normal leading-none">Northbridge review</span>
            </span>
          </div>
          <p className="mt-3 text-xs text-muted-foreground">The automated check reads like a typed line. Only a Northbridge reviewer can stamp an application as verified.</p>
        </section>
      </main>
    </div>
  );
}
