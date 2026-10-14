import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import type { InboxItem } from "../api";
import { fakeApi } from "../test/fakeApi";
import { InboxPage } from "./InboxPage";

const item = (id: number, title: string, extra: Partial<InboxItem> = {}): InboxItem => ({
  id, read_at: null, created_at: new Date(Date.now() - id * 60_000).toISOString(), kind: "swapped", title,
  body: `${title} body`, chore_id: 7, date: "2026-10-21", ...extra,
});

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search}</p>;
}

const show = () => render(
  <MemoryRouter initialEntries={["/inbox"]}>
    <Routes>
      <Route path="/inbox" element={<InboxPage />} />
      <Route path="*" element={<Where />} />
    </Routes>
  </MemoryRouter>);

describe("inbox", () => {
  it("lists newest first with unread marked, and marks all read", async () => {
    const s = fakeApi({ inbox: [item(1, "Swapped: you now do Sunday"), item(2, "Missed: bins", { read_at: "x" })] });
    show();
    const list = await screen.findByRole("list", { name: "Notifications" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByRole("button", { name: "Unread: Swapped: you now do Sunday" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /1 unread/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Mark all read" }));
    await waitFor(() => expect(s.inbox.every((n) => n.read_at)).toBe(true));
    expect(screen.getByRole("button", { name: "Mark all read" })).toBeDisabled();
  });

  it("marks one read or unread again", async () => {
    const s = fakeApi({ inbox: [item(1, "A"), item(2, "B")] });
    show();
    await userEvent.click(await screen.findByRole("button", { name: 'Mark "B" read' }));
    await waitFor(() => expect(s.inbox.find((n) => n.id === 2)!.read_at).toBe("now"));
    await userEvent.click(await screen.findByRole("button", { name: 'Mark "B" unread' }));
    await waitFor(() => expect(s.inbox.find((n) => n.id === 2)!.read_at).toBeNull());
  });

  it("opening one marks it read and goes to that day on the calendar", async () => {
    const s = fakeApi({ inbox: [item(1, "Swapped: you now do Wednesday")] });
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Unread: Swapped: you now do Wednesday" }));
    expect(await screen.findByTestId("where")).toHaveTextContent("/calendar?date=2026-10-21");
    expect(s.inbox[0].read_at).toBe("now");
  });

  it("unread-only filter and an empty state that points to settings", async () => {
    fakeApi({ inbox: [item(1, "Old", { read_at: "x" })] });
    show();
    await userEvent.click(await screen.findByRole("checkbox", { name: /Unread only/ }));
    expect(await screen.findByText(/Nothing unread/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /notification settings/i })[0]).toHaveAttribute("href", "/notifications");
  });
});

describe("unread badge", () => {
  it("shows the count on the Inbox link", async () => {
    fakeApi({ inbox: [item(1, "A"), item(2, "B"), item(3, "C", { read_at: "x" })] });
    render(<MemoryRouter initialEntries={["/chores"]}><App /></MemoryRouter>);
    expect(await screen.findByRole("link", { name: "Inbox, 2 unread" })).toBeInTheDocument();
  });
});
