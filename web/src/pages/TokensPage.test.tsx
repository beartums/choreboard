import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { TokensPage } from "./TokensPage";

const ERIC = { id: 1, name: "Eric", color: "", email: "eric@example.com", role: "admin" as const, active: 1 };
const show = () => render(<MemoryRouter><TokensPage me={ERIC} /></MemoryRouter>);

describe("tokens", () => {
  it("makes a read-only token and shows it once", async () => {
    const s = fakeApi();
    show();
    const form = await screen.findByRole("form", { name: "Make a token" });
    await userEvent.type(within(form).getByLabelText("Name"), "nav");
    expect(within(form).getByRole("checkbox", { name: "Read" })).toBeChecked();
    await userEvent.click(within(form).getByRole("button", { name: "Make token" }));
    const fresh = await screen.findByRole("status", { name: "New token" });
    expect(within(fresh).getByLabelText("Token")).toHaveValue("cb_abcdefgSECRET");
    expect(s.calls.find((c) => c.path === "/api/tokens" && c.method === "POST")!.body)
      .toEqual({ name: "nav", scopes: ["read"], person_id: 1 });
    await userEvent.click(within(fresh).getByRole("button", { name: "I've saved it" }));
    expect(screen.queryByLabelText("Token")).toBeNull();
    // Listed by name and prefix only.
    const list = screen.getByRole("list", { name: "Tokens" });
    expect(list).toHaveTextContent("nav") ;
    expect(list).toHaveTextContent("cb_abcdefg…");
    expect(list).not.toHaveTextContent("SECRET");
  });

  it("revokes after a confirmation", async () => {
    const s = fakeApi({ tokens: [{ id: 5, name: "old", prefix: "cb_zzz", person_id: 1, person_name: "Eric",
      scopes: ["read"], created_at: "2026-10-01T00:00:00Z", last_used: null, revoked_at: null }] });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(s.tokens[0].revoked_at).not.toBeNull());
    expect(await screen.findByText("1 revoked")).toBeInTheDocument();
  });
});
