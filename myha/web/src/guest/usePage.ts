import { useEffect } from "react";

/**
 * Per route: the document title, and focus on the page's h1 (tabIndex -1), so screen readers announce the new page
 * after a client-side navigation. `title` null while the page is still loading.
 */
export function usePage(title: string | null) {
  const ready = title !== null;
  useEffect(() => {
    if (title !== null) document.title = title;
  }, [title]);
  // Once per page, when its content is there: a later title change must not pull the focus away.
  useEffect(() => {
    if (ready) document.querySelector<HTMLElement>("main h1")?.focus();
  }, [ready]);
}
