import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { fakeApi } from "../test/fakeApi";
import { SettingsPage } from "./SettingsPage";

describe("settings", () => {
  it("saves SMTP credentials; the password is write-only", async () => {
    const s = fakeApi();
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    const email = await screen.findByRole("form", { name: "Email settings" });
    expect(within(email).getByText("Not set up")).toBeInTheDocument();
    await userEvent.type(within(email).getByLabelText("SMTP user"), "bot@gmail.com");
    await userEvent.type(within(email).getByLabelText("SMTP password"), "abcd efgh ijkl mnop");
    await userEvent.click(within(email).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(s.calls.find((c) => c.method === "PUT")?.body).toEqual(
      { values: { "smtp.user": "bot@gmail.com", "smtp.password": "abcd efgh ijkl mnop" } }));
    expect(await within(email).findByText("Ready")).toBeInTheDocument();
    expect(within(email).getByLabelText("SMTP password")).toHaveValue("");
    expect(within(email).getByPlaceholderText(/set — type to replace/)).toBeInTheDocument();
  });

  it("shows server-provided values read-only", async () => {
    fakeApi();
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    const discord = await screen.findByRole("form", { name: "Discord settings" });
    expect(within(discord).getByText(/set by the server/)).toBeInTheDocument();
    expect(within(discord).queryByLabelText("Discord bot token")).toBeNull();
  });

  it("explains when secrets can't be stored yet", async () => {
    const s = fakeApi();
    s.settings.can_store_secrets = false;
    render(<MemoryRouter><SettingsPage /></MemoryRouter>);
    expect(await screen.findByText(/can.t be stored yet/)).toBeInTheDocument();
    expect(within(screen.getByRole("form", { name: "Email settings" })).getByLabelText("SMTP password")).toBeDisabled();
  });

  it("is in the nav for admins only", async () => {
    fakeApi();
    const { unmount } = render(<MemoryRouter initialEntries={["/chores"]}><App /></MemoryRouter>);
    expect(await screen.findByRole("link", { name: "Settings" })).toBeInTheDocument();
    unmount();
    fakeApi({ me: { email: "t@x", via: "dev", scopes: [], needs_setup: false,
                    person: { id: 2, name: "Tucker", color: "", email: "t@x", role: "member", active: 1 } } });
    render(<MemoryRouter initialEntries={["/chores"]}><App /></MemoryRouter>);
    await screen.findByRole("link", { name: "Chores" });
    expect(screen.queryByRole("link", { name: "Settings" })).toBeNull();
    expect(screen.queryByRole("link", { name: "People" })).toBeNull();
  });
});
