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
