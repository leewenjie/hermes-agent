import { describe, expect, it } from "vitest";

import {
  managedFilePreviewKind,
  managedFilePreviewPath,
} from "./file-preview";

describe("managedFilePreviewPath", () => {
  it("reads an encoded absolute file path", () => {
    expect(
      managedFilePreviewPath(
        "?preview=%2Fopt%2Fdata%2Frandom_chart.svg",
      ),
    ).toBe("/opt/data/random_chart.svg");
  });

  it("returns null when no preview was requested", () => {
    expect(managedFilePreviewPath("?path=%2Fopt%2Fdata")).toBeNull();
  });
});

describe("managedFilePreviewKind", () => {
  it.each([
    ["image/svg+xml", "chart.svg", "image"],
    ["image/png", "chart.png", "image"],
    ["application/pdf", "filing.pdf", "pdf"],
    ["text/html", "report.html", "html"],
    ["text/markdown", "memo.md", "markdown"],
    ["text/csv", "returns.csv", "text"],
    ["application/json", "metrics.json", "text"],
    [null, "analysis.py", "text"],
    ["application/octet-stream", "model.bin", "unsupported"],
  ] as const)("classifies %s %s as %s", (mime, name, expected) => {
    expect(managedFilePreviewKind(mime, name)).toBe(expected);
  });
});
