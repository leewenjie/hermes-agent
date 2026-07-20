import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FrozenWorkspaceNotice } from "./FrozenWorkspaceNotice";

describe("FrozenWorkspaceNotice", () => {
  it("keeps saved research visible while making the upgrade path clear", () => {
    const html = renderToStaticMarkup(
      <FrozenWorkspaceNotice billingUrl="https://oxaide.com/console/billing" />,
    );

    expect(html).toContain("Your saved research is available");
    expect(html).toContain("Read-only");
    expect(html).toContain("Browse and copy your existing research here.");
    expect(html).toContain("Upgrade to Researcher");
    expect(html).toContain('href="https://oxaide.com/console/billing"');
    expect(html).not.toContain("workspace is frozen");
  });

  it("does not render a broken upgrade link without a billing URL", () => {
    const html = renderToStaticMarkup(<FrozenWorkspaceNotice />);

    expect(html).toContain("Your saved research is available");
    expect(html).not.toContain("Upgrade to Researcher");
    expect(html).not.toContain("href=");
  });
});