import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FieldError } from "@/components/ui/misc";
import { useMe } from "@/hooks/useAuth";
import { api, ApiError } from "@/lib/api";
import { passwordSchema } from "@/pages/public/forms";

const pwSchema = z.object({ current_password: z.string().min(1, "Enter your current password."), password: passwordSchema, password_confirm: z.string() })
  .refine((d) => d.password === d.password_confirm, { path: ["password_confirm"], message: "Passwords do not match." });

export function SettingsPage() {
  const me = useMe();
  const qc = useQueryClient();
  const profile = useForm<{ full_name: string }>({ values: { full_name: me.data?.full_name ?? "" } });
  const pw = useForm<z.infer<typeof pwSchema>>({ resolver: zodResolver(pwSchema) });
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <h1 className="text-3xl font-semibold">Account settings</h1>
      <Card>
        <CardHeader><CardTitle>Profile</CardTitle><CardDescription>Signed in as {me.data?.email}. Your documents, not this name, are used for the consistency check.</CardDescription></CardHeader>
        <CardContent>
          <form className="flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={profile.handleSubmit(async (v) => {
            try { const u = await api("/v1/auth/me", { method: "PATCH", json: v }); qc.setQueryData(["me"], u); toast.success("Name saved"); }
            catch (e) { toast.error(e instanceof ApiError ? e.message : "Could not save"); }
          })}>
            <div className="flex-1 space-y-1.5"><Label htmlFor="fn">Full name</Label><Input id="fn" {...profile.register("full_name")} /></div>
            <Button type="submit">Save name</Button>
          </form>
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Change password</CardTitle><CardDescription>Other signed-in sessions are signed out.</CardDescription></CardHeader>
        <CardContent>
          <form className="space-y-3" noValidate onSubmit={pw.handleSubmit(async (v) => {
            try { await api("/v1/auth/change-password", { json: v }); pw.reset(); toast.success("Password changed"); }
            catch (e) { toast.error(e instanceof ApiError ? e.message : "Could not change password"); }
          })}>
            {(["current_password", "password", "password_confirm"] as const).map((k) => (
              <div key={k} className="space-y-1.5">
                <Label htmlFor={k}>{{ current_password: "Current password", password: "New password", password_confirm: "Confirm new password" }[k]}</Label>
                <Input id={k} type="password" autoComplete={k === "current_password" ? "current-password" : "new-password"} {...pw.register(k)} />
                <FieldError>{pw.formState.errors[k]?.message}</FieldError>
              </div>
            ))}
            <Button type="submit">Change password</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
