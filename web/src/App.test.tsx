import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { fakeApi } from "./test/fakeApi";

const renderAt = (path: string) => render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);

describe("first visit", () => {
  it("makes the signed-in person the first admin", async () => {
    const s = fakeApi({ me: { email: "eric@example.com", person: null, via: "pomerium", scopes: [], needs_setup: true } });
    renderAt("/");
    await userEvent.type(await screen.findByLabelText("Your name"), "Eric");
    await userEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(await screen.findByRole("heading", { name: /^Calendar/ })).toBeInTheDocument();
    expect(s.calls).toContainEqual({ method: "POST", path: "/api/setup", body: { name: "Eric" } });
  });
});

describe("people", () => {
  it("adds, edits and removes someone", async () => {
    const s = fakeApi();
    renderAt("/people");
    const add = await screen.findByRole("form", { name: "Add someone" });
    await userEvent.type(within(add).getByLabelText("Name"), "Tucker");
    await userEvent.type(within(add).getByLabelText("Sign-in email"), "tucker@example.com");
    await userEvent.click(within(add).getByRole("button", { name: "Add" }));
    expect(await screen.findByText("Tucker")).toBeInTheDocument();
    expect(s.people.at(-1)).toMatchObject({ name: "Tucker", email: "tucker@example.com", role: "member" });

    const row = screen.getByText("Tucker").closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Edit" }));
    const edit = await screen.findByRole("form", { name: "Edit Tucker" });
    await userEvent.selectOptions(within(edit).getByLabelText("Colour"), "mint");
    await userEvent.click(within(edit).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(s.people.at(-1)!.color).toBe("mint"));
  });
});

describe("chores", () => {
  it("adds a chore and goes straight to scheduling it", async () => {
    const s = fakeApi();
    renderAt("/chores");
    const add = await screen.findByRole("form", { name: "Add a chore" });
    await userEvent.type(within(add).getByLabelText("Chore"), "Walk the dog");
    await userEvent.click(within(add).getByRole("button", { name: "Add and schedule" }));
    expect(await screen.findByRole("heading", { name: "Weekly rotation" })).toBeInTheDocument();
    expect(s.chores[0]).toMatchObject({ title: "Walk the dog", points: 1 });
  });

  it("builds the dog's weekly rotation from the grid", async () => {
    const people = [1, 2, 3, 4].map((id, i) => ({
      id, name: ["Eric", "Tucker", "Cass", "Mom"][i], color: "", email: null, role: "member" as const, active: 1,
    }));
    const s = fakeApi({ people, chores: [{ id: 7, title: "Walk the dog", description: "", icon: "🐕", points: 3, active: 1 }] });
    renderAt("/chores/7");
    const grid = await screen.findByLabelText("Weekly rotation");
    for (const [day, who] of [["Mon", "Tucker"], ["Wed", "Tucker"], ["Tue", "Cass"], ["Sat", "Cass"],
                              ["Thu", "Mom"], ["Fri", "Mom"], ["Sun", "Eric"]]) {
      await userEvent.selectOptions(within(grid).getByLabelText(day), who);
    }
    await userEvent.click(within(grid).getByRole("button", { name: "Save rotation" }));
    await waitFor(() => expect(s.rules).toHaveLength(4));
    const byPerson = Object.fromEntries(s.rules.map((r) => [r.person_id, r.weekdays]));
    expect(byPerson).toEqual({ 2: [1, 3], 3: [2, 6], 4: [4, 5], 1: [7] });
  });

  it("adds an every-few-days schedule and shows API errors", async () => {
    const s = fakeApi({ chores: [{ id: 7, title: "Water plants", description: "", icon: "", points: 1, active: 1 }] });
    renderAt("/chores/7");
    const form = await screen.findByRole("form", { name: "Add a schedule" });
    await userEvent.clear(within(form).getByLabelText("Every (days)"));
    await userEvent.type(within(form).getByLabelText("Every (days)"), "3");
    await userEvent.click(within(form).getByRole("button", { name: "Add schedule" }));
    await waitFor(() => expect(s.rules).toHaveLength(1));
    expect(s.rules[0]).toMatchObject({ every_days: 3, weekdays: null, person_id: 1 });
    expect(await screen.findByText(/Every 3 days from/)).toBeInTheDocument();
  });
});

describe("chore icon", () => {
  it("picks from the list, and a compound emoji survives saving whole", async () => {
    const s = fakeApi({ chores: [{ id: 7, title: "Walk the dog", description: "", icon: "", points: 3, active: 1 }] });
    renderAt("/chores/7");
    const form = await screen.findByRole("form", { name: "Chore details" });
    await userEvent.click(within(form).getByRole("button", { name: "Choose an icon" }));
    await userEvent.click(within(form).getByRole("option", { name: "dog walk" }));
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(s.chores[0].icon).toBe("🦮"));

    const typed = within(form).getByLabelText("Icon (type any emoji)");
    await userEvent.clear(typed);
    await userEvent.type(typed, "🐕‍🦺");                 // dog + ZWJ + vest: 5 UTF-16 units
    await userEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(s.chores[0].icon).toBe("🐕‍🦺"));
  });
});

describe("rotation dates", () => {
  it("a rotation can start later, and another can be added", async () => {
    const people = [1, 2].map((id, i) => ({ id, name: ["Eric", "Tucker"][i], color: "", email: null, role: "member" as const, active: 1 }));
    const s = fakeApi({ people, chores: [{ id: 7, title: "Bins", description: "", icon: "🗑️", points: 1, active: 1 }] });
    renderAt("/chores/7");
    const grid = await screen.findByLabelText("Weekly rotation");
    await userEvent.selectOptions(within(grid).getByLabelText("Mon"), "Tucker");
    await userEvent.type(within(grid).getByLabelText("Starts"), "2026-11-02");
    await userEvent.click(within(grid).getByRole("button", { name: "Save rotation" }));
    await waitFor(() => expect(s.rules).toEqual([expect.objectContaining({ person_id: 2, weekdays: [1], start: "2026-11-02" })]));

    await userEvent.click(await screen.findByRole("button", { name: "Add another rotation" }));
    const fresh = await screen.findByLabelText("New rotation");
    await userEvent.selectOptions(within(fresh).getByLabelText("Fri"), "Eric");
    await userEvent.type(within(fresh).getByLabelText("Starts"), "2027-06-15");
    await userEvent.type(within(fresh).getByLabelText("Ends"), "2027-08-31");
    await userEvent.click(within(fresh).getByRole("button", { name: "Save rotation" }));
    await waitFor(() => expect(s.rules).toHaveLength(2));
    expect(s.rules[1]).toMatchObject({ person_id: 1, weekdays: [5], start: "2027-06-15", end: "2027-08-31" });
    expect(s.rules[0]).toMatchObject({ start: "2026-11-02" }), "the first rotation is untouched";
  });
});
