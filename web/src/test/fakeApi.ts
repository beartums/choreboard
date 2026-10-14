// An in-memory stand-in for the HTTP API, behind `fetch`, so pages are tested
// against real request/response shapes without a server.
import { vi } from "vitest";
import type { Chore, Entry, Person, Rule } from "../api";

export interface FakeState {
  me: { email: string; person: Person | null; via: string; scopes: string[]; needs_setup: boolean };
  people: Person[];
  chores: Chore[];
  rules: Rule[];
  entries: Entry[];
  balances: Record<number, number>;
  subs: { id: number; event: string; channel: string; chore_id: number | null }[];
  channels: Record<string, { address: string | null; enabled: boolean; default: boolean }>;
  configured: string[];
  calls: { method: string; path: string; body?: unknown }[];
}

export function fakeApi(init: Partial<FakeState> = {}): FakeState {
  const eric: Person = { id: 1, name: "Eric", color: "", email: "eric@example.com", role: "admin", active: 1 };
  const s: FakeState = {
    me: { email: "eric@example.com", person: eric, via: "dev", scopes: ["read", "write"], needs_setup: false },
    people: [eric], chores: [], rules: [], entries: [], balances: {}, subs: [], configured: ["email"],
    channels: { email: { address: "eric@example.com", enabled: true, default: true },
                discord: { address: null, enabled: false, default: true } },
    calls: [], ...init,
  };
  let next = 100;
  const json = (status: number, body: unknown) =>
    Promise.resolve(new Response(status === 204 ? null : JSON.stringify(body), { status }));

  vi.spyOn(globalThis, "fetch").mockImplementation((input, opts) => {
    const url = new URL(String(input), "http://x");
    const method = (opts?.method ?? "GET").toUpperCase();
    const body = opts?.body ? JSON.parse(String(opts.body)) : undefined;
    s.calls.push({ method, path: url.pathname + url.search, body });
    const p = url.pathname;
    const id = Number(p.split("/").pop());

    if (p === "/api/me") return json(200, s.me);
    if (p === "/api/setup" && method === "POST") {
      const person = { id: 1, name: body.name, color: "", email: s.me.email, role: "admin" as const, active: 1 };
      s.people = [person];
      s.me = { ...s.me, person, needs_setup: false };
      return json(201, person);
    }
    if (p === "/api/people" && method === "GET") return json(200, s.people.filter((x) => x.active));
    if (p === "/api/people" && method === "POST") {
      const person = { id: next++, color: "", email: null, role: "member" as const, active: 1, ...body };
      s.people.push(person);
      return json(201, person);
    }
    if (p.startsWith("/api/people/") && method === "PATCH") {
      const person = s.people.find((x) => x.id === id)!;
      Object.assign(person, body, "active" in body ? { active: body.active ? 1 : 0 } : {});
      return json(200, person);
    }
    if (p === "/api/chores" && method === "GET") return json(200, s.chores.filter((x) => x.active));
    if (p === "/api/chores" && method === "POST") {
      if (!body.title) return json(400, { error: "title is required" });
      const chore = { id: next++, description: "", icon: "", points: 0, active: 1, ...body };
      s.chores.push(chore);
      return json(201, chore);
    }
    if (p.startsWith("/api/chores/") && method === "PATCH") {
      const chore = s.chores.find((x) => x.id === id)!;
      Object.assign(chore, body, "active" in body ? { active: body.active ? 1 : 0 } : {});
      return json(200, chore);
    }
    if (p === "/api/rules" && method === "GET") {
      const c = url.searchParams.get("chore_id");
      return json(200, s.rules.filter((r) => !c || r.chore_id === Number(c)));
    }
    if (p === "/api/rules" && method === "POST") {
      const rule = { id: next++, ...body };
      s.rules.push(rule);
      return json(201, rule);
    }
    if (p.startsWith("/api/rules/") && method === "PUT") {
      const i = s.rules.findIndex((r) => r.id === id);
      s.rules[i] = { id, ...body };
      return json(200, s.rules[i]);
    }
    if (p.startsWith("/api/rules/") && method === "DELETE") {
      s.rules = s.rules.filter((r) => r.id !== id);
      return json(204, null);
    }
    if (p === "/api/calendar") {
      const from = url.searchParams.get("from")!, to = url.searchParams.get("to")!;
      const who = url.searchParams.get("person_id");
      return json(200, s.entries.filter((e) => e.date >= from && e.date <= to && (!who || e.person_id === Number(who))));
    }
    if (/^\/api\/people\/\d+\/points$/.test(p)) {
      const pid = Number(p.split("/")[3]);
      return json(200, { person_id: pid, balance: s.balances[pid] ?? 0, entries: [] });
    }
    const occ = p.match(/^\/api\/occurrences\/([^/]+)\/(outcome|reassign|skip)$/);
    if (occ && method === "POST") {
      const e = s.entries.find((x) => x.id === decodeURIComponent(occ[1]))!;
      if (occ[2] === "outcome") {
        if (["failed", "covered", "failed_covered"].includes(body.status) && !body.note)
          return json(400, { error: `'${body.status}' needs a note saying why` });
        e.outcome = { id: next++, status: body.status, assignee_id: e.person_id, done_by_id: body.done_by ?? e.person_id,
                      note: body.note ?? "", recorded_by: 1, recorded_at: "now" };
        return json(201, e.outcome);
      }
      if (occ[2] === "reassign") Object.assign(e, { scheduled_person_id: e.person_id, person_id: body.person_id, override_id: next++, source: "override", reason: body.reason });
      if (occ[2] === "skip") Object.assign(e, { skipped: true, override_id: next++, source: "override", reason: body.reason });
      return json(200, e);
    }
    if (p === "/api/swaps" && method === "POST") {
      const a = s.entries.find((x) => x.id === body.a)!, b2 = s.entries.find((x) => x.id === body.b)!;
      const g = "sw_test";
      [a.person_id, b2.person_id] = [b2.person_id, a.person_id];
      for (const x of [a, b2]) Object.assign(x, { group_id: g, source: "override", override_id: next++, reason: body.reason });
      return json(201, { group_id: g, a, b: b2 });
    }
    if (p === "/api/notifications/catalog") {
      return json(200, {
        events: [{ kind: "swapped", label: "Someone swapped a chore with me", scope: "mine" },
                 { kind: "missed", label: "My chore was marked missed", scope: "mine" },
                 { kind: "new_chore", label: "A new chore was added", scope: "household" }],
        channels: ["app", "email", "discord"].map((c) => ({ channel: c, configured: c === "app" || s.configured.includes(c) })),
      });
    }
    if (p === "/api/me/notifications") return json(200, { channels: s.channels, subscriptions: s.subs });
    if (p === "/api/me/deliveries") return json(200, []);
    if (p === "/api/me/subscriptions" && method === "POST") {
      const sub = { id: next++, ...body };
      s.subs.push(sub);
      return json(201, sub);
    }
    if (p.startsWith("/api/me/subscriptions/") && method === "DELETE") {
      s.subs = s.subs.filter((x) => x.id !== id);
      return json(204, null);
    }
    const ch = p.match(/^\/api\/me\/channels\/(\w+)(\/test)?$/);
    if (ch && method === "PUT") {
      if (ch[1] === "discord" && body.address && !/^\d{15,21}$/.test(body.address))
        return json(400, { error: "a Discord user id is a 17-20 digit number" });
      s.channels[ch[1]] = { address: body.address, enabled: true, default: !body.address };
      return json(200, s.channels);
    }
    if (ch && ch[2] && method === "POST") return json(202, { status: "queued" });
    return json(404, { error: `fake: no route ${method} ${p}` });
  });
  return s;
}
