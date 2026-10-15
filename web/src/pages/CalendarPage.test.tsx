import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";
import type { Entry, Person } from "../api";
import { addDays, today, weekStart } from "../dates";
import { fakeApi, type FakeState } from "../test/fakeApi";
import { CalendarPage } from "./CalendarPage";

const P = (id: number, name: string): Person => ({ id, name, color: "", email: `${name.toLowerCase()}@x`, role: "member", active: 1 });
const ERIC = { ...P(1, "Eric"), role: "admin" as const };
const PEOPLE = [ERIC, P(2, "Tucker"), P(3, "Cass"), P(4, "Mom")];
const MON = weekStart(today());

function entry(dayOffset: number, person: number, extra: Partial<Entry> = {}): Entry {
  const date = addDays(MON, dayOffset);
  return { id: `7:${date}:rule:${person}`, chore_id: 7, date, person_id: person, available: null, due: "18:00", source: "rule",
           rule_id: person, scheduled_person_id: null, override_id: null, skipped: false, reason: "", group_id: null,
           outcome: null, ...extra };
}

let s: FakeState;
beforeEach(() => {
  localStorage.clear();
  s = fakeApi({
    people: PEOPLE,
    chores: [{ id: 7, title: "Walk the dog", description: "", icon: "🐕", points: 3, active: 1 }],
    // Tucker Mon/Wed, Cass Tue/Sat, Mom Thu/Fri, Eric Sun -- and next week's Sunday too.
    entries: [entry(0, 2), entry(1, 3), entry(2, 2), entry(3, 4), entry(4, 4), entry(5, 3), entry(6, 1), entry(9, 2), entry(13, 1)],
    balances: { 2: 9 },
  });
});

const show = () => render(<MemoryRouter><CalendarPage me={ERIC} /></MemoryRouter>);
const open = async (dayOffset: number) => {
  const e = s.entries.find((x) => x.date === addDays(MON, dayOffset))!;
  const who = PEOPLE.find((p) => p.id === e.person_id)!.name;
  const buttons = await screen.findAllByRole("button", { name: new RegExp(`Walk the dog.*${who}`) });
  await userEvent.click(buttons[0]);
  return screen.findByRole("dialog");
};

describe("calendar", () => {
  it("shows the week's rotation and everyone's points", async () => {
    show();
    expect(await screen.findAllByRole("button", { name: /Walk the dog/ })).toHaveLength(7);
    expect(within(screen.getByRole("list", { name: "Points" })).getByText("9")).toBeInTheDocument();
  });

  it("records 'I did it'", async () => {
    show();
    const panel = await open(6);                      // Eric's Sunday
    await userEvent.click(within(panel).getByRole("button", { name: "I did it" }));
    await waitFor(() => expect(s.calls).toContainEqual(expect.objectContaining(
      { method: "POST", path: `/api/occurrences/7:${addDays(MON, 6)}:rule:1/outcome`, body: { status: "done" } })));
  });

  it("'missed' asks for a reason and defaults the deduction to the chore's points", async () => {
    show();
    const panel = await open(0);                      // Tucker's Monday
    await userEvent.click(within(panel).getByRole("button", { name: "Missed" }));
    const record = within(panel).getByRole("button", { name: "Record" });
    expect(record).toBeDisabled();
    expect(within(panel).getByLabelText(/Points to take from Tucker/)).toHaveValue(3);
    await userEvent.clear(within(panel).getByLabelText(/Points to take/));
    await userEvent.type(within(panel).getByLabelText(/Points to take/), "1");
    await userEvent.type(within(panel).getByLabelText("Why"), "forgot");
    await userEvent.click(record);
    await waitFor(() => expect(s.calls.find((c) => c.path.endsWith("/outcome"))?.body)
      .toEqual({ status: "failed", note: "forgot", deduct: 1 }));
  });

  it("'someone else did it' credits the person who covered", async () => {
    show();
    const panel = await open(3);                      // Mom's Thursday
    await userEvent.click(within(panel).getByRole("button", { name: "Someone else did it" }));
    await userEvent.selectOptions(within(panel).getByLabelText("Who did it"), "Cass");
    await userEvent.type(within(panel).getByLabelText("Why"), "Mom at work");
    await userEvent.click(within(panel).getByRole("button", { name: "Record" }));
    await waitFor(() => expect(s.calls.find((c) => c.path.endsWith("/outcome"))!.body)
      .toEqual({ status: "covered", note: "Mom at work", done_by: 3 }));
  });

  it("swaps with any later day and asks why", async () => {
    show();
    const panel = await open(6);                      // Eric's Sunday
    await userEvent.click(within(panel).getByRole("button", { name: "Swap with another day" }));
    const list = await within(panel).findByLabelText("Swap with");
    // Only other people's days are offered (not Eric's own next Sunday).
    const options = within(list).getAllByRole("option").map((o) => o.textContent);
    expect(options.some((t) => t!.includes("Eric"))).toBe(false);
    // From today (or the day itself, if earlier) onwards.
    const from = today() < addDays(MON, 6) ? today() : addDays(MON, 6);
    expect(options).toHaveLength(s.entries.filter((e) => e.person_id !== 1 && e.date >= from).length);
    const target = s.entries.filter((e) => e.person_id !== 1 && e.date >= from)[0];
    await userEvent.selectOptions(list, target.id);
    await userEvent.type(within(panel).getByLabelText("Why"), "Eric away Sunday");
    await userEvent.click(within(panel).getByRole("button", { name: "Swap" }));
    await waitFor(() => expect(s.calls.find((c) => c.path === "/api/swaps")!.body).toEqual(
      { a: `7:${addDays(MON, 6)}:rule:1`, b: target.id, reason: "Eric away Sunday" }));
    // Reloaded: the two days now show the swap.
    expect(await screen.findAllByText("Swapped")).toHaveLength(2);
  });

  it("a recorded day offers undo, with a reason", async () => {
    s.entries[0].outcome = { id: 55, status: "done", assignee_id: 2, done_by_id: 2, note: "", recorded_by: 2, recorded_at: "" };
    show();
    const panel = await open(0);
    expect(within(panel).getByText("Done by Tucker")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Missed" })).toBeNull();
    await userEvent.click(within(panel).getByRole("button", { name: "Undo this" }));
    expect(within(panel).getByRole("button", { name: "Undo" })).toBeDisabled();
  });

  it("navigates weeks and filters to mine", async () => {
    show();
    await screen.findAllByRole("button", { name: /Walk the dog/ });
    await userEvent.click(screen.getByRole("checkbox", { name: /Only mine/ }));
    await waitFor(() => expect(s.calls.some((c) => c.path.includes("person_id=1"))).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Next week" }));
    await waitFor(() => expect(s.calls.some((c) => c.path.includes(`from=${addDays(MON, 7)}`))).toBe(true));
  });

  it("closes on Escape", async () => {
    show();
    await open(0);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});


describe("calendar views", () => {
  it("week view: seven columns, a chip per chore, the same panel on click", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Week" }));
    const grid = await screen.findByRole("grid", { name: "Week" });
    expect(within(grid).getAllByRole("columnheader")).toHaveLength(7);
    expect(within(grid).getAllByRole("gridcell")).toHaveLength(7);
    const chip = within(grid).getAllByRole("button", { name: /Walk the dog, Eric/ })[0];
    await userEvent.click(chip);
    expect(await screen.findByRole("dialog", { name: /Walk the dog/ })).toBeInTheDocument();
  });

  it("month view: whole weeks covering the month, remembered next time", async () => {
    const { unmount } = show();
    await userEvent.click(await screen.findByRole("button", { name: "Month" }));
    const grid = await screen.findByRole("grid", { name: "Month" });
    const cells = within(grid).getAllByRole("gridcell");
    expect(cells.length % 7).toBe(0);
    expect(cells.length).toBeGreaterThanOrEqual(28);
    await waitFor(() => expect(s.calls.some((c) => c.path.startsWith("/api/calendar") && c.path.includes("from=")
      && !c.path.includes(`from=${MON}&to=${addDays(MON, 6)}`))).toBe(true));
    unmount();
    show();
    expect(await screen.findByRole("grid", { name: "Month" })).toBeInTheDocument();
  });

  it("month view steps a month at a time", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Month" }));
    await userEvent.click(screen.getByRole("button", { name: "Next month" }));
    const next = new Date(); next.setDate(1); next.setMonth(next.getMonth() + 1);
    const label = next.toLocaleDateString(undefined, { month: "long", year: "numeric" });
    expect(await screen.findByRole("heading", { name: new RegExp(label) })).toBeInTheDocument();
  });
});


describe("recorded but unscheduled", () => {
  it("still shows, flagged, and can be undone", async () => {
    s.entries.push({ ...entry(0, 3), id: "7:" + addDays(MON, 0) + ":rule:99", unscheduled: true,
      outcome: { id: 77, status: "done", assignee_id: 3, done_by_id: 3, note: "", recorded_by: 3, recorded_at: "" } });
    show();
    expect(await screen.findByText(/no longer scheduled/)).toBeInTheDocument();
    const btn = screen.getByText(/no longer scheduled/).closest("button")!;
    await userEvent.click(btn);
    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByText(/no longer on the schedule/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Undo this" })).toBeInTheDocument();
  });
});

describe("calendar ?date=", () => {
  it("opens on the week of the given date", async () => {
    render(<MemoryRouter initialEntries={["/calendar?date=2027-01-20"]}><CalendarPage me={ERIC} /></MemoryRouter>);
    await waitFor(() => expect(s.calls.some((c) => c.path.startsWith("/api/calendar?from=2027-01-18&to=2027-01-24"))).toBe(true));
  });
});


describe("what the server says I can do", () => {
  it("offers only the allowed actions, with the reason for done", async () => {
    s.entries[0].can = ["propose_swap"];
    s.entries[0].cannot = { done: "opens at 4 PM" };
    show();
    const panel = await open(0);
    expect(within(panel).queryByRole("button", { name: /did it/ })).toBeNull();
    expect(within(panel).getByText("opens at 4 PM.")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Missed" })).toBeNull();
    expect(within(panel).getByRole("button", { name: "Ask to swap…" })).toBeInTheDocument();
  });

  it("a pending swap shows as tentative with approve/decline for the person asked", async () => {
    s.entries[6].pending_swap = { id: 9, from_person: 2, to_person: 1, with: s.entries[0].id, reason: "practice", side: "to" };
    s.entries[6].can = ["approve_swap", "decline_swap"];
    show();
    expect(await screen.findAllByText("Swap pending")).not.toHaveLength(0);
    const panel = await open(6);
    expect(within(panel).getByText(/Nothing changes until Eric approves/)).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Approve swap" })).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Decline" })).toBeInTheDocument();
  });

  it("a member asks someone to take their chore; it shows as a pending hand-off", async () => {
    const TUCKER = PEOPLE[1];
    s.me = { ...s.me, person: TUCKER };
    s.entries[0].can = ["propose_swap", "propose_give"];
    render(<MemoryRouter><CalendarPage me={TUCKER} /></MemoryRouter>);
    const panel = await open(0);
    await userEvent.click(within(panel).getByRole("button", { name: "Ask someone to take it…" }));
    await userEvent.selectOptions(within(panel).getByLabelText("Ask"), "Mom");
    await userEvent.type(within(panel).getByLabelText("Why"), "practice");
    await userEvent.click(within(panel).getByRole("button", { name: "Ask them" }));
    await waitFor(() => expect(s.calls.find((c) => c.path === "/api/swap-requests/give")?.body)
      .toEqual({ key: s.entries[0].id, person_id: 4, reason: "practice" }));
  });

  it("an admin's change to a pending day asks whether to approve or decline it first", async () => {
    s.entries[6].pending_swap = { id: 9, from_person: 2, to_person: 1, with: s.entries[0].id, reason: "practice", side: "to" };
    s.entries[6].can = ["approve_swap", "decline_swap", "skip", "give", "swap", "failed"];
    show();
    const panel = await open(6);
    await userEvent.click(within(panel).getByRole("button", { name: "Skip this date" }));
    await userEvent.click(within(panel).getByRole("button", { name: "Skip this date" }));
    expect(await within(panel).findByText(/Doing this settles it first/)).toBeInTheDocument();
    expect(within(panel).getByText(/Tucker asked Eric — practice/)).toBeInTheDocument();
    await userEvent.click(within(panel).getByRole("button", { name: "Decline it, then do this" }));
    await waitFor(() => expect(s.calls.filter((c) => c.path.endsWith("/skip")).map((c) => (c.body as any).pending))
      .toEqual([undefined, "decline"]));
  });
});


describe("covered chores", () => {
  it("show the person who did it, and whose it was", async () => {
    s.entries[0].outcome = { id: 5, status: "covered", assignee_id: 2, done_by_id: 3, note: "practice", recorded_by: 1, recorded_at: "" };
    show();
    const btn = (await screen.findAllByRole("button", { name: /Walk the dog/ }))[0];
    expect(btn).toHaveTextContent("Cass");
    expect(btn).toHaveTextContent("covering for Tucker");
  });
});


describe("undoing a swap", () => {
  it("a member asks; the panel says why when they can't", async () => {
    s.entries[6] = { ...s.entries[6], group_id: "sw_1", source: "override", override_id: 3, scheduled_person_id: 2,
                     can: ["request_undo"], cannot: {} };
    show();
    const panel = await open(6);
    await userEvent.click(within(panel).getByRole("button", { name: "Ask to undo the swap…" }));
    expect(within(panel).getByLabelText("Why undo the swap?")).toBeInTheDocument();
  });

  it("an older swap under a newer one says to undo that first", async () => {
    s.entries[6] = { ...s.entries[6], group_id: "sw_1", can: [], cannot: { undo_swap: "a later swap on Sun Oct 25 has to be undone first" } };
    show();
    const panel = await open(6);
    expect(within(panel).getByText(/Undo: a later swap on Sun Oct 25 has to be undone first\./)).toBeInTheDocument();
  });
});
