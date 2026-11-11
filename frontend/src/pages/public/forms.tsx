import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { z } from "zod";
import { Disclaimer, Logo } from "@/components/brand";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FieldError } from "@/components/ui/misc";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";

export const passwordSchema = z
  .string()
  .min(12, "Use at least 12 characters.")
  .max(128)
  .refine((p) => [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(p)).length >= 3, {
    message: "Mix at least three of: lowercase, uppercase, digits, symbols.",
  });

export const registerSchema = z
  .object({
    full_name: z.string().trim().min(2, "Enter your full name as on your Aadhaar card."),
    email: z.string().trim().email("Enter a valid email address."),
    password: passwordSchema,
    password_confirm: z.string(),
  })
  .refine((d) => d.password === d.password_confirm, { path: ["password_confirm"], message: "Passwords do not match." });

export function strength(p: string): { score: number; label: string } {
  let s = 0;
  if (p.length >= 12) s++;
  if (p.length >= 16) s++;
  s += [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(p)).length >= 3 ? 1 : 0;
  if (/[^A-Za-z0-9]/.test(p) && /\d/.test(p)) s++;
  return { score: s, label: ["Too weak", "Weak", "Fair", "Good", "Strong"][s] };
}

export function AuthFrame({ title, children, staff = false }: { title: string; children: React.ReactNode; staff?: boolean }) {
  return (
    <div className={staff ? "min-h-screen bg-primary/[0.04]" : "min-h-screen"}>
      <header className="mx-auto flex max-w-5xl items-center justify-between px-4 py-5">
        <Logo sub={staff ? "Staff portal" : undefined} />
        {staff ? <Link to="/" className="text-sm underline-offset-4 hover:underline">Applicant site</Link>
               : <Link to="/staff/login" className="text-sm underline-offset-4 hover:underline">Northbridge staff sign-in</Link>}
      </header>
      <main className="mx-auto max-w-md px-4 pb-16 pt-6">
        <h1 className="mb-6 text-3xl font-semibold">{title}</h1>
        {children}
      </main>
    </div>
  );
}

function Field({ id, label, error, children, hint }: { id: string; label: string; error?: string; children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint}
      <FieldError id={`${id}-err`}>{error}</FieldError>
    </div>
  );
}

export function RegisterPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const f = useForm<z.infer<typeof registerSchema>>({ resolver: zodResolver(registerSchema), mode: "onBlur" });
  const pw = f.watch("password") ?? "";
  const st = strength(pw);
  const onSubmit = f.handleSubmit(async (values) => {
    setErr(null);
    try {
      const u = await api<User>("/v1/auth/register", { json: values });
      qc.setQueryData(["me"], u);
      nav("/app");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Registration failed.");
    }
  });
  const e = f.formState.errors;
  return (
    <AuthFrame title="Create your account">
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {err && <Alert tone="danger">{err}</Alert>}
        <Field id="full_name" label="Full name" error={e.full_name?.message}>
          <Input id="full_name" autoComplete="name" aria-invalid={!!e.full_name} {...f.register("full_name")} />
        </Field>
        <Field id="email" label="Email" error={e.email?.message}>
          <Input id="email" type="email" autoComplete="email" aria-invalid={!!e.email} {...f.register("email")} />
        </Field>
        <Field id="password" label="Password" error={e.password?.message}
          hint={pw && <p className="text-xs text-muted-foreground" aria-live="polite">Strength: {st.label}</p>}>
          <Input id="password" type="password" autoComplete="new-password" aria-invalid={!!e.password} {...f.register("password")} />
        </Field>
        <Field id="password_confirm" label="Confirm password" error={e.password_confirm?.message}>
          <Input id="password_confirm" type="password" autoComplete="new-password" aria-invalid={!!e.password_confirm} {...f.register("password_confirm")} />
        </Field>
        <Button type="submit" className="w-full" disabled={f.formState.isSubmitting}>Create account</Button>
        <p className="text-center text-sm">Already registered? <Link className="text-stamp underline-offset-4 hover:underline" to="/login">Sign in</Link></p>
        <Disclaimer />
      </form>
    </AuthFrame>
  );
}

const loginSchema = z.object({ email: z.string().trim().email("Enter a valid email address."), password: z.string().min(1, "Enter your password.") });

export function LoginPage({ staff = false }: { staff?: boolean }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const f = useForm<z.infer<typeof loginSchema>>({ resolver: zodResolver(loginSchema) });
  const onSubmit = f.handleSubmit(async (values) => {
    setErr(null);
    try {
      const u = await api<User>(staff ? "/v1/auth/staff/login" : "/v1/auth/applicant/login", { json: values });
      qc.setQueryData(["me"], u);
      nav(staff ? "/staff" : "/app");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Sign-in failed.");
    }
  });
  const e = f.formState.errors;
  return (
    <AuthFrame title={staff ? "Northbridge staff sign-in" : "Sign in"} staff={staff}>
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {err && <Alert tone="danger">{err}</Alert>}
        <Field id="email" label="Email" error={e.email?.message}>
          <Input id="email" type="email" autoComplete="username" {...f.register("email")} />
        </Field>
        <Field id="password" label="Password" error={e.password?.message}>
          <Input id="password" type="password" autoComplete="current-password" {...f.register("password")} />
        </Field>
        <Button type="submit" className="w-full" disabled={f.formState.isSubmitting}>Sign in</Button>
        <div className="flex justify-between text-sm">
          <Link className="text-stamp underline-offset-4 hover:underline" to={staff ? "/staff/forgot-password" : "/forgot-password"}>Forgot password?</Link>
          {!staff && <Link className="text-stamp underline-offset-4 hover:underline" to="/register">Create account</Link>}
        </div>
        {staff && <p className="text-xs text-muted-foreground">Staff accounts are created by a Northbridge administrator. Applicants cannot sign in here.</p>}
      </form>
    </AuthFrame>
  );
}

export function ForgotPasswordPage({ staff = false }: { staff?: boolean }) {
  const [done, setDone] = useState(false);
  const f = useForm<{ email: string }>({ resolver: zodResolver(z.object({ email: z.string().email("Enter a valid email address.") })) });
  const onSubmit = f.handleSubmit(async (v) => {
    await api("/v1/auth/password-reset/request", { json: v }).catch(() => undefined);
    setDone(true);
  });
  return (
    <AuthFrame title="Reset your password" staff={staff}>
      {done ? <Alert tone="success">If an account exists for that email, we sent a reset link. It expires in 30 minutes and works once.</Alert> : (
        <form onSubmit={onSubmit} className="space-y-4" noValidate>
          <Field id="email" label="Email" error={f.formState.errors.email?.message}><Input id="email" type="email" {...f.register("email")} /></Field>
          <Button type="submit" className="w-full">Send reset link</Button>
        </form>
      )}
    </AuthFrame>
  );
}

const resetSchema = z.object({ password: passwordSchema, password_confirm: z.string() })
  .refine((d) => d.password === d.password_confirm, { path: ["password_confirm"], message: "Passwords do not match." });

export function ResetPasswordPage({ staff = false }: { staff?: boolean }) {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [err, setErr] = useState<string | null>(null);
  const f = useForm<z.infer<typeof resetSchema>>({ resolver: zodResolver(resetSchema) });
  const onSubmit = f.handleSubmit(async (v) => {
    try {
      await api("/v1/auth/password-reset/confirm", { json: { ...v, token: params.get("token") ?? "" } });
      nav(staff ? "/staff/login" : "/login");
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not reset the password.");
    }
  });
  return (
    <AuthFrame title={staff ? "Set your staff password" : "Choose a new password"} staff={staff}>
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {err && <Alert tone="danger">{err}</Alert>}
        <Field id="password" label="New password" error={f.formState.errors.password?.message}><Input id="password" type="password" autoComplete="new-password" {...f.register("password")} /></Field>
        <Field id="password_confirm" label="Confirm password" error={f.formState.errors.password_confirm?.message}><Input id="password_confirm" type="password" autoComplete="new-password" {...f.register("password_confirm")} /></Field>
        <Button type="submit" className="w-full">Save password</Button>
      </form>
    </AuthFrame>
  );
}

export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const qc = useQueryClient();
  const [state, setState] = useState<"idle" | "ok" | "error">("idle");
  const [msg, setMsg] = useState("");
  return (
    <AuthFrame title="Confirm your email">
      {state === "ok" ? <Alert tone="success" title="Email confirmed">You can continue to your application. <Link className="underline" to="/app">Go to dashboard</Link></Alert>
        : state === "error" ? <Alert tone="danger">{msg}</Alert> : (
        <Button onClick={async () => {
          try { await api("/v1/auth/verify-email", { json: { token: params.get("token") ?? "" } }); setState("ok"); qc.invalidateQueries({ queryKey: ["me"] }); }
          catch (e) { setState("error"); setMsg(e instanceof ApiError ? e.message : "Verification failed."); }
        }}>Confirm email address</Button>
      )}
    </AuthFrame>
  );
}
