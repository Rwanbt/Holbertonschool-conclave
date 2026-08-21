import { useEffect, useRef } from 'react'

const BOTTOM_TOLERANCE_PX = 48

/**
 * Suit un flux tant que l'utilisateur reste près du bas. Dès qu'il remonte
 * pour relire, aucun nouveau delta ne lui vole sa position de lecture.
 */
export function useStreamingScroll(text: string, active: boolean) {
  const containerRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)

  const onScroll = () => {
    const element = containerRef.current
    if (element === null) return
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight
    followRef.current = distance <= BOTTOM_TOLERANCE_PX
  }

  useEffect(() => {
    const element = containerRef.current
    if (active && element !== null && followRef.current) {
      element.scrollTop = element.scrollHeight
    }
  }, [active, text])

  return { containerRef, onScroll }
}
