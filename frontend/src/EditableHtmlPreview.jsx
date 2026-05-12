// src/EditableHtmlPreview.jsx
// contentEditable div 기반 HTML 미리보기 + 편집 컴포넌트
//
// 커서 튐 방지 전략:
//  - lastSetRef: 마지막으로 프로그래밍적으로 innerHTML에 설정한 값을 기억
//  - 사용자가 onInput으로 타이핑하면 lastSetRef도 함께 갱신
//  - html prop이 바뀌어도 lastSetRef와 같은 값이면 innerHTML 업데이트 생략
//  - editable prop이 바뀌면 항상 최신 html로 초기화 (편집 모드 진입/퇴장 시)

import { useRef, useEffect, useCallback } from 'react'

function sanitize(raw) {
  // script 태그 제거 (XSS 방지)
  return (raw || '').replace(/<script[\s\S]*?<\/script>/gi, '')
}

export default function EditableHtmlPreview({ html, editable, onChange }) {
  const divRef = useRef(null)
  // 마지막으로 프로그래밍적으로 설정한 innerHTML 값
  const lastSetRef = useRef('')

  // ── editable이 바뀔 때 항상 최신 html로 초기화 ──
  useEffect(() => {
    if (!divRef.current) return
    const clean = sanitize(html)
    divRef.current.innerHTML = clean
    lastSetRef.current = clean
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editable]) // 의도적으로 html 제외: editable 전환 시 초기화가 목적

  // ── html prop이 외부에서 바뀔 때 (새 문서 생성, 수정 저장 후 등) ──
  useEffect(() => {
    if (!divRef.current) return
    const clean = sanitize(html)
    // 사용자가 방금 입력한 값과 같으면 스킵 (커서 튐 방지)
    if (clean === lastSetRef.current) return
    divRef.current.innerHTML = clean
    lastSetRef.current = clean
  }, [html])

  // ── 사용자 타이핑 핸들러 ──
  const handleInput = useCallback(
    (e) => {
      const current = e.currentTarget.innerHTML
      // 사용자가 입력한 값을 추적 → 다음 html effect에서 덮어쓰지 않게
      lastSetRef.current = current
      onChange?.(current)
    },
    [onChange],
  )

  return (
    <div
      ref={divRef}
      contentEditable={editable}
      suppressContentEditableWarning
      onInput={handleInput}
      className={[
        'min-h-72 overflow-auto outline-none rounded-lg transition-all duration-150',
        'p-4',
        editable
          ? 'border-2 border-indigo-500 bg-white shadow-inner cursor-text'
          : 'border border-slate-200 bg-white cursor-default',
      ].join(' ')}
      style={{ fontFamily: 'inherit' }}
    />
  )
}
