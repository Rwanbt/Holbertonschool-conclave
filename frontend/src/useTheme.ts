import { useCallback, useEffect, useState } from 'react'

export type ThemeChoice = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'conclave.theme.v1'

function readStoredChoice(): ThemeChoice {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw === 'light' || raw === 'dark' || raw === 'system') {
      return raw
    }
  } catch {
    // localStorage indisponible (mode privé, quota) : on retombe sur le
    // défaut système plutôt que de casser le rendu.
  }
  return 'system'
}

function systemPrefersDark(): boolean {
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  )
}

export function resolveTheme(choice: ThemeChoice, prefersDark: boolean): ResolvedTheme {
  if (choice === 'system') {
    return prefersDark ? 'dark' : 'light'
  }
  return choice
}

export interface Theme {
  /** Ce que l'utilisateur a choisi, `system` compris. */
  choice: ThemeChoice
  /** Le thème réellement appliqué après résolution. */
  resolved: ResolvedTheme
  /** Bascule clair <-> sombre en figeant un choix explicite. */
  toggle: () => void
}

/**
 * Thème clair/sombre avec trois états : choix explicite clair, choix explicite
 * sombre, ou suivi du système (défaut, aucun attribut posé sur <html>).
 *
 * L'attribut `data-theme` n'est écrit que pour un choix EXPLICITE : tant que
 * l'utilisateur n'a rien décidé, la feuille de style suit
 * `prefers-color-scheme`, et un changement de réglage système est répercuté en
 * direct grâce à l'écoute du media query.
 */
export function useTheme(): Theme {
  const [choice, setChoice] = useState<ThemeChoice>(readStoredChoice)
  const [prefersDark, setPrefersDark] = useState<boolean>(systemPrefersDark)

  // Suivre le réglage système tant qu'aucun choix explicite n'a été fait.
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') {
      return
    }
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (event: MediaQueryListEvent): void => setPrefersDark(event.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const resolved = resolveTheme(choice, prefersDark)

  useEffect(() => {
    const root = window.document.documentElement
    if (choice === 'system') {
      root.removeAttribute('data-theme')
    } else {
      root.setAttribute('data-theme', choice)
    }
    try {
      window.localStorage.setItem(STORAGE_KEY, choice)
    } catch {
      // Le thème reste appliqué pour cette session même sans persistance.
    }
  }, [choice])

  const toggle = useCallback(() => {
    // Bascule sur ce qui est RÉELLEMENT affiché : depuis `system`, on part
    // donc de l'inverse de ce que l'utilisateur voit, jamais d'un défaut
    // arbitraire. L'updater fonctionnel évite toute fermeture périmée.
    setChoice((current) =>
      resolveTheme(current, systemPrefersDark()) === 'dark' ? 'light' : 'dark',
    )
  }, [])

  return { choice, resolved, toggle }
}
