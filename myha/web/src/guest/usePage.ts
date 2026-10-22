import { useEffect } from "react";

/**
 * Per route: the document title, and focus on the page's h1 (tabIndex -1), so screen readers announce the new page
 * after a client-side navigation. `title` null while the page is still loading.
 */
export function usePage(title: string | null) {
  useEffect(() => {
    if (title === null) return;
    document.title = title;
    document.querySelector<HTMLElement>("main h1")?.focus();
  }, [title]);
}
