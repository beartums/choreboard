import { createContext, useContext } from "react";
import type { Person } from "./api";

/** The signed-in person, for pages that show admins more. The server enforces
 *  every rule; this only decides what's worth offering. */
export const MeContext = createContext<Person | null>(null);

export function useIsAdmin(): boolean {
  return useContext(MeContext)?.role === "admin";
}
