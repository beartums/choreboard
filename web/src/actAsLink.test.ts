import { describe, expect, it, vi } from "vitest";

describe("a link from nav while acting as someone", () => {
  it("keeps acting as them in this tab and drops the parameter from the address", async () => {
    sessionStorage.clear();
    window.history.replaceState(null, "", "/requests/9?act_as=2");
    vi.resetModules();
    const api = await import("./api");
    expect(api.getActingAs()).toBe(2);
    expect(window.location.pathname + window.location.search).toBe("/requests/9");
    api.setActingAs(null);
    window.history.replaceState(null, "", "/");
  });
});
