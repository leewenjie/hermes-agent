import { describe, expect, it } from "vitest";

import type { ManagedFileEntry } from "@/lib/api";
import { researchFileReference } from "@/lib/research-file-reference";

const entry = {
  name: "summary.csv",
  path: "/opt/data/workspace/reports/summary.csv",
} as ManagedFileEntry;

describe("researchFileReference", () => {
  it("uses a path relative to the locked research workspace", () => {
    expect(
      researchFileReference(entry, {
        locked_root: "/opt/data/workspace",
        root: "/opt/data/workspace",
      }),
    ).toBe('Use the research file "reports/summary.csv" in this request.');
  });

  it("never exposes an absolute path outside the managed root", () => {
    expect(
      researchFileReference(
        { ...entry, path: "/opt/data/private/summary.csv" },
        { locked_root: "/opt/data/workspace", root: "/opt/data/workspace" },
      ),
    ).toBe('Use the research file "summary.csv" in this request.');
  });
});