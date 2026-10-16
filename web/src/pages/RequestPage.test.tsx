import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { fakeApi } from "../test/fakeApi";
import { RequestPage } from "./RequestPage";

const req = {
  id: 9, kind: "swap", status: "pending", reason: "practice", answer: "", from_person: 2, to_person: 1,
  from_name: "Tucker", to_name: "Eric", can: ["approve", "decline"],
  mine: { key: "a", date: "2026-10-21", person: "Tucker", what: "Walk the dog on Wed Oct 21" },
  theirs: { key: "b", date: "2026-10-25", person: "Eric", what: "Walk the dog on Sun Oct 25" },
};

describe("a request's page (the notification's link)", () => {
  it("says what's asked and approves it in one click", async () => {
    const s = fakeApi({ requests: [{ ...req }] } as any);
    render(<MemoryRouter initialEntries={["/requests/9"]}><Routes><Route path="/requests/:id" element={<RequestPage />} /></Routes></MemoryRouter>);
    expect(await screen.findByText(/Eric does Tucker's Walk the dog on Wed Oct 21/)).toBeInTheDocument();
    expect(screen.getByText("Reason: practice")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(s.calls).toContainEqual(expect.objectContaining({ method: "POST", path: "/api/swap-requests/9/approve" })));
    expect(await screen.findByText("Approved.")).toBeInTheDocument();
  });
});
