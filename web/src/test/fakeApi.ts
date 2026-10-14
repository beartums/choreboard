// An in-memory stand-in for the HTTP API, behind `fetch`, so pages are tested
// against real request/response shapes without a server.
import { vi } from "vitest";
import type { Chore, Person, Rule } from "../api";

export interface FakeState {
  me: { email: string; person: Person | null; via: string; scopes: string[]; needs_setup: boolean };
  people: Person[];
  chores: Chore[];
  rules: Rule[];
  calls: { method: string; path: string; body?: unknown }[];
}

export function fakeApi(init: Partial<FakeState> = {}): FakeState {
  const eric: Person = { id: 1, name: "Eric", color: "", email: "eric@example.com", role: "admin", active: 1 };
  const s: FakeState = {
    me: { email: "eric@example.com", person: eric, via: "dev", scopes: ["read", "write"], needs_setup: false },
    people: [eric], chores: [], rules: [], calls: [], ...init,
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
    return json(404, { error: `fake: no route ${method} ${p}` });
  });
  return s;
}
