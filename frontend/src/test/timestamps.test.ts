import { describe, expect, it } from "vitest";
import { formatDate, formatTime, parseTimestamp } from "@/lib/utils";

describe("UTC timestamps displayed in local time", () => {
  it("treats legacy timestamps without an offset as UTC", () => {
    for (const value of ["2026-11-11T08:00:00", "2026-11-11 08:00:00", "2026-11-11T08:00:00Z", "2026-11-11T13:30:00+05:30"]) {
      expect(parseTimestamp(value)?.toISOString()).toBe("2026-11-11T08:00:00.000Z");
      expect(formatDate(value)).toBe(formatDate("2026-11-11T08:00:00Z"));
      expect(formatTime(value)).toBe(formatTime("2026-11-11T08:00:00Z"));
    }
    expect(parseTimestamp("2026-11-11T08:00:00")?.toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false })).toBe("13:30:00");
  });
  it("handles empty or invalid timestamps", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatTime("invalid")).toBe("—");
  });
});
