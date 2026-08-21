import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useStreamingScroll } from './useStreamingScroll'

describe('useStreamingScroll', () => {
  it('suit le flux au bas puis respecte une remontée manuelle', () => {
    const element = document.createElement('div')
    Object.defineProperties(element, {
      scrollHeight: { value: 1000, configurable: true },
      clientHeight: { value: 200, configurable: true },
      scrollTop: { value: 800, writable: true, configurable: true },
    })
    const { result, rerender } = renderHook(
      ({ text }) => useStreamingScroll(text, true),
      { initialProps: { text: 'début' } },
    )

    act(() => {
      Object.defineProperty(result.current.containerRef, 'current', {
        value: element,
        writable: true,
      })
    })
    rerender({ text: 'début suite' })
    expect(element.scrollTop).toBe(1000)

    act(() => {
      element.scrollTop = 100
      result.current.onScroll()
    })
    rerender({ text: 'début suite encore' })
    expect(element.scrollTop).toBe(100)
  })
})
