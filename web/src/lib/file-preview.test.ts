import { describe, expect, it } from "vitest";

import {
  managedFilePreviewKind,
  managedFilePreviewPath,
  managedFilePreviewUrl,
} from "./file-preview";
import { managedFileDownloadUrl } from "./api";

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

  it("builds a Files deep link that opens the requested preview", () => {
    const url = managedFilePreviewUrl("/research-results/SPY weekly review.md");

    expect(url).toBe(
      "/files?preview=%2Fresearch-results%2FSPY+weekly+review.md",
    );
    expect(managedFilePreviewPath(new URL(url, "https://example.com").search)).toBe(
      "/research-results/SPY weekly review.md",
    );
  });
});

describe("managedFilePreviewKind", () => {
  it.each([
    ["image/svg+xml", "chart.svg", "image"],
    ["image/png", "chart.png", "image"],
    [null, "chart.png", "image"],
    [null, "chart.jpg", "image"],
    [null, "chart.webp", "image"],
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

describe("managedFileDownloadUrl", () => {
  it("builds the dedicated attachment URL with a loopback token", () => {
    const url = managedFileDownloadUrl(
      "/opt/data/workspace/reports/market memo.pdf",
      "session-token",
    );

    expect(url).toBe(
      "/api/files/download?path=%2Fopt%2Fdata%2Fworkspace%2Freports%2Fmarket+memo.pdf&token=session-token",
    );
  });
});
