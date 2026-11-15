import type { KeyboardEvent } from 'react'

/** Keep keyboard navigation inside the active native dialog, including nested editors. */
export function trapDialogFocus(event: KeyboardEvent<HTMLDialogElement>) {
  if (event.key !== 'Tab') return
  const controls = [...event.currentTarget.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, summary, [tabindex]')]
    .filter(element => element.tabIndex >= 0 && !element.matches(':disabled, [hidden]') && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden')
  const first = controls[0], last = controls.at(-1)
  if (!first || !last) { event.preventDefault(); return }
  if (event.shiftKey && (document.activeElement === first || !event.currentTarget.contains(document.activeElement))) {
    event.preventDefault(); last.focus()
  } else if (!event.shiftKey && (document.activeElement === last || !event.currentTarget.contains(document.activeElement))) {
    event.preventDefault(); first.focus()
  }
}
