import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { NotificationsPage } from "./NotificationsPage";

const show = () => render(<MemoryRouter><NotificationsPage /></MemoryRouter>);

describe("notifications", () => {
  it("toggles an event per channel for all my chores", async () => {
    const s = fakeApi();
    show();
    const box = await screen.findByRole("checkbox", { name: "Someone swapped a chore with me by Email" });
    await userEvent.click(box);
    await waitFor(() => expect(s.subs).toEqual([expect.objectContaining({ event: "swapped", channel: "email", chore_id: null })]));
    await userEvent.click(await screen.findByRole("checkbox", { name: "Someone swapped a chore with me by Email" }));
    await waitFor(() => expect(s.subs).toEqual([]));
  });

  it("shows the sign-in email as the default and warns when a channel isn't set up", async () => {
    fakeApi();
    show();
    const email = await screen.findByRole("form", { name: "Email settings" });
    expect(within(email).getByPlaceholderText(/eric@example.com \(your sign-in\)/)).toBeInTheDocument();
    const discord = screen.getByRole("form", { name: "Discord settings" });
    expect(within(discord).getByText(/can't send discord yet/)).toBeInTheDocument();
    expect(within(discord).getByRole("button", { name: "Send a test" })).toBeDisabled();
  });

  it("refuses a Discord username where an id is needed", async () => {
    fakeApi({ configured: ["email", "discord"] });
    show();
    const discord = await screen.findByRole("form", { name: "Discord settings" });
    await userEvent.type(within(discord).getByLabelText(/Discord user id/), "tucker#1234");
    await userEvent.click(within(discord).getByRole("button", { name: "Save" }));
    expect(await within(discord).findByRole("alert")).toHaveTextContent("17-20 digit");
  });

  it("adds a notification for one chore only, offering only per-person events", async () => {
    const s = fakeApi({ chores: [{ id: 7, title: "Bins", description: "", icon: "🗑️", points: 1, active: 1 }] });
    show();
    const form = await screen.findByRole("form", { name: "Add a per-chore notification" });
    const when = within(form).getByLabelText("When");
    expect(within(when).queryByRole("option", { name: "A new chore was added" })).toBeNull();
    await userEvent.selectOptions(when, "missed");
    await userEvent.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(s.subs).toEqual([expect.objectContaining({ event: "missed", chore_id: 7 })]));
    expect(await screen.findByRole("list", { name: "Per-chore notifications" })).toHaveTextContent("Bins");
  });
});


describe("in-app channel", () => {
  it("offers In app as a column and as the per-chore default", async () => {
    const s = fakeApi({ chores: [{ id: 7, title: "Bins", description: "", icon: "", points: 1, active: 1 }] });
    show();
    await userEvent.click(await screen.findByRole("checkbox", { name: "My chore was marked missed by In app" }));
    await waitFor(() => expect(s.subs).toEqual([expect.objectContaining({ event: "missed", channel: "app" })]));
    const form = screen.getByRole("form", { name: "Add a per-chore notification" });
    expect(within(form).getByLabelText("How")).toHaveValue("app");
  });
});

describe("joining the Discord server", () => {
  it("shows the invite link when an admin set one, and asks the admins", async () => {
    const s = fakeApi({ configured: ["email", "discord"] });
    (s as any).invite = "https://discord.gg/abc123";
    show();
    const discord = await screen.findByRole("form", { name: "Discord settings" });
    expect(within(discord).getByRole("link", { name: "Join the server" })).toHaveAttribute("href", "https://discord.gg/abc123");
    await userEvent.click(within(discord).getByRole("button", { name: "Ask an admin to add me" }));
    expect(await within(discord).findByRole("status")).toHaveTextContent("2 admins have it in their inbox");
    expect(s.calls.some((c) => c.method === "POST" && c.path === "/api/me/discord/request")).toBe(true);
  });

  it("offers nothing while the server can't send Discord", async () => {
    fakeApi({ configured: ["email"] });
    show();
    const discord = await screen.findByRole("form", { name: "Discord settings" });
    expect(within(discord).queryByRole("button", { name: "Ask an admin to add me" })).toBeNull();
  });
});
