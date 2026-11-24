/** The Grid & Hearth mark (public/logo.svg), inline so the house outline follows the theme's text colour. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg className="logo" viewBox="0 0 64 64" width={size} height={size} aria-hidden="true" focusable="false">
      <path d="M6 29 32 7l26 22M13 24v27a5 5 0 0 0 5 5h28a5 5 0 0 0 5-5V24" fill="none" stroke="currentColor"
        strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
      <path fill="var(--mint)" d="M18 51V41a4 4 0 0 1 8 0v10z" />
      <path fill="var(--coral)" d="M28 51V33a4 4 0 0 1 8 0v18z" />
      <path fill="var(--sunshine)" d="M38 51V30a4 4 0 0 1 8 0v21z" />
      <path fill="var(--sunshine)" d="M34 13Q34 20 41 20Q34 20 34 27Q34 20 27 20Q34 20 34 13Z" />
      <path fill="var(--mint)" d="M21 23Q21 27 25 27Q21 27 21 31Q21 27 17 27Q21 27 21 23Z" />
    </svg>
  );
}
