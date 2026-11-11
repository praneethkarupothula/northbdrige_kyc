import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function parseTimestamp(v?: string | null): Date | null {
  if (!v) return null;
  // Older SQLite responses omitted the offset, but all stored timestamps are UTC.
  const value = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(v) && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(v)
    ? v.replace(" ", "T") + "Z" : v;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDate(v?: string | null) {
  return parseTimestamp(v)?.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) ?? "—";
}

export function formatTime(v?: string | null) {
  return parseTimestamp(v)?.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short" }) ?? "—";
}

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function ageLabel(hours?: number | null) {
  if (hours == null) return "—";
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${Math.round(hours)} h`;
  return `${Math.round(hours / 24)} days`;
}

export const CATEGORY_LABEL: Record<string, string> = {
  AADHAAR: "Aadhaar card",
  PAN: "PAN card",
  SALARY_SLIP: "Salary slip",
  BANK_STATEMENT: "Bank statement",
  OTHER: "Other",
};
