// src/EducationGenerator.jsx
// 기존의 "교육용 자동작성" 페이지 — 그대로 유지
// 흐름: DOCX → AI 분석 → 필드 편집 → 자연어 + 영수증 → AI 자동작성 → 최종 HTML

import { useRef, useState } from 'react'
import { previewTemplateFields, generateOnce } from './api'

// ============== 작은 UI 부품 ==============
function Spinner() {
  return (
    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" className="opacity-25" />
      <path d="M4 12a8 8 0 018-8" stroke="currentColor" strokeWidth="4" className="opacity-75" />
    </svg>
  )
}

function Section({ step, title, required, children }) {
  return (
    <section className="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center text-sm font-bold">
          {step}
        </div>
        <h2 className="text-lg font-semibold text-slate-800">
          {title}
          {required && <span className="ml-2 text-xs text-red-500 font-normal">필수</span>}
        </h2>
      </div>
      {children}
    </section>
  )
}

function Label({ children }) {
  return <div className="text-sm font-medium text-slate-700 mb-1">{children}</div>
}

function Input(props) {
  return (
    <input
      {...props}
      className="w-full bg-white border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200"
    />
  )
}

function Button({ onClick, loading, children, variant = 'primary', disabled }) {
  const base =
    'inline-flex items-center gap-2 px-5 py-2 rounded-lg font-medium text-sm transition disabled:opacity-50 disabled:cursor-not-allowed'
  const styles =
    variant === 'primary'
      ? 'bg-blue-600 hover:bg-blue-700 text-white'
      : 'bg-slate-100 hover:bg-slate-200 text-slate-700'
  return (
    <button onClick={onClick} disabled={loading || disabled} className={`${base} ${styles}`}>
      {loading && <Spinner />}
      {children}
    </button>
  )
}

function ErrorBox({ children }) {
  if (!children) return null
  return (
    <div className="mt-3 bg-red-50 border border-red-200 text-red-700 rounded-md px-3 py-2 text-sm">
      {children}
    </div>
  )
}

function DropZone({ accept, multiple = false, onFiles, hint, files }) {
  const [drag, setDrag] = useState(false)
  const inputRef = useRef(null)

  const handleFiles = (list) => {
    if (!list || list.length === 0) return
    const arr = Array.from(list)
    onFiles(multiple ? arr : arr.slice(0, 1))
  }

  return (
    <div
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); handleFiles(e.dataTransfer.files) }}
      className={`cursor-pointer border-2 border-dashed rounded-xl p-6 text-center transition ${
        drag ? 'border-blue-500 bg-blue-50' : 'border-slate-300 bg-slate-50 hover:border-blue-400'
      }`}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        onChange={(e) => handleFiles(e.target.files)}
        className="hidden"
      />
      <div className="text-2xl mb-1">📂</div>
      <div className="text-slate-600 text-sm">{hint}</div>
      {files && files.length > 0 && (
        <div className="mt-3 text-blue-700 text-sm font-medium break-all">
          {files.map((f) => f.name).join(', ')}
        </div>
      )}
    </div>
  )
}

// ============== 메인 컴포넌트 ==============
export default function EducationGenerator() {
  const [templateName, setTemplateName] = useState('')
  const [description1, setDescription1] = useState('')
  const [docxFile, setDocxFile] = useState(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeError, setAnalyzeError] = useState('')

  const [analysis, setAnalysis] = useState(null)
  const [fields, setFields] = useState([])

  const [description2, setDescription2] = useState('')
  const [receipts, setReceipts] = useState([])
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState('')

  const [generated, setGenerated] = useState(null)

  const handleAnalyze = async () => {
    setAnalyzeError('')
    setGenerated(null)
    if (!docxFile) { setAnalyzeError('DOCX 파일을 업로드 해주세요.'); return }
    if (!docxFile.name.toLowerCase().endsWith('.docx')) {
      setAnalyzeError('DOCX 파일만 업로드 가능합니다. (HWP/HWPX 미지원)'); return
    }
    if (!templateName.trim()) { setAnalyzeError('양식 이름을 입력해주세요.'); return }

    setAnalyzing(true)
    try {
      const data = await previewTemplateFields({
        template: docxFile,
        template_name: templateName,
        description: description1,
      })
      setAnalysis(data)
      setFields(Array.isArray(data.fields) ? data.fields : [])
    } catch (e) {
      setAnalyzeError(e.message || String(e))
    } finally {
      setAnalyzing(false)
    }
  }

  const handleGenerate = async () => {
    setGenerateError('')
    if (!analysis) { setGenerateError('먼저 양식을 분석해주세요.'); return }
    if (fields.length === 0) { setGenerateError('필드가 비어있습니다.'); return }

    setGenerating(true)
    try {
      const data = await generateOnce({
        template_name: templateName,
        html_template: analysis.html_template,
        fields,
        description: description2,
        receipts,
      })
      setGenerated(data)
    } catch (e) {
      setGenerateError(e.message || String(e))
    } finally {
      setGenerating(false)
    }
  }

  const updateField = (i, patch) => setFields((fs) => fs.map((f, j) => (j === i ? { ...f, ...patch } : f)))
  const removeField = (i) => setFields((fs) => fs.filter((_, j) => j !== i))
  const addField = () =>
    setFields((fs) => [
      ...fs,
      { label: '새 필드', key: `new_field_${fs.length + 1}`, type: 'string', required: false, cell_id: '' },
    ])

  const downloadHtml = () => {
    if (!generated) return
    const wrap = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>${
      templateName || 'document'
    }</title></head><body>${generated.filled_html}</body></html>`
    const blob = new Blob([wrap], { type: 'text/html;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${templateName || 'document'}.html`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  return (
    <div>
      <Section step={1} title="결재 양식 업로드 & AI 분석" required>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <Label>양식 이름 *</Label>
            <Input value={templateName} onChange={(e) => setTemplateName(e.target.value)} placeholder="예: 출장 신청서" />
          </div>
          <div>
            <Label>양식 설명 (선택)</Label>
            <Input value={description1} onChange={(e) => setDescription1(e.target.value)} placeholder="예: 국내 출장용 양식" />
          </div>
        </div>
        <div className="mt-4">
          <Label>DOCX 파일 *</Label>
          <DropZone
            accept=".docx"
            files={docxFile ? [docxFile] : []}
            onFiles={(list) => setDocxFile(list[0])}
            hint="DOCX 파일을 끌어다 놓거나 클릭해서 선택하세요. (HWP/HWPX 미지원)"
          />
        </div>
        <ErrorBox>{analyzeError}</ErrorBox>
        <div className="mt-5 flex justify-end">
          <Button onClick={handleAnalyze} loading={analyzing}>
            {analyzing ? '분석 중...' : 'AI로 양식 분석'}
          </Button>
        </div>
      </Section>

      {analysis && (
        <Section step={2} title="분석된 입력 필드 (직접 수정 가능)">
          {fields.length === 0 ? (
            <div className="text-slate-500 text-sm">자동 입력 가능한 필드가 발견되지 않았습니다.</div>
          ) : (
            <>
              <div className="hidden md:grid grid-cols-12 gap-2 px-3 py-1 text-xs text-slate-500">
                <div className="col-span-3">라벨</div>
                <div className="col-span-3">key</div>
                <div className="col-span-2">타입</div>
                <div className="col-span-2">cell_id</div>
                <div className="col-span-2 text-right">옵션</div>
              </div>
              <div className="space-y-2">
                {fields.map((f, i) => (
                  <div key={i} className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center bg-slate-50 rounded-lg p-3">
                    <div className="md:col-span-3">
                      <input className="w-full bg-white border border-slate-200 rounded-md px-2 py-1 text-sm"
                        value={f.label || ''} onChange={(e) => updateField(i, { label: e.target.value })} placeholder="라벨" />
                    </div>
                    <div className="md:col-span-3">
                      <input className="w-full bg-white border border-slate-200 rounded-md px-2 py-1 text-sm font-mono"
                        value={f.key || ''} onChange={(e) => updateField(i, { key: e.target.value })} placeholder="key" />
                    </div>
                    <div className="md:col-span-2">
                      <select className="w-full bg-white border border-slate-200 rounded-md px-2 py-1 text-sm"
                        value={f.type || 'string'} onChange={(e) => updateField(i, { type: e.target.value })}>
                        <option value="string">string</option>
                        <option value="number">number</option>
                        <option value="date">date</option>
                        <option value="textarea">textarea</option>
                      </select>
                    </div>
                    <div className="md:col-span-2 text-xs text-slate-500 font-mono break-all">{f.cell_id || '-'}</div>
                    <div className="md:col-span-2 flex items-center justify-end gap-3 text-xs">
                      <label className="flex items-center gap-1">
                        <input type="checkbox" checked={!!f.required}
                          onChange={(e) => updateField(i, { required: e.target.checked })} /> 필수
                      </label>
                      <button onClick={() => removeField(i)} className="text-red-500 hover:text-red-700">삭제</button>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
          <div className="mt-3"><Button variant="secondary" onClick={addField}>+ 필드 추가</Button></div>
        </Section>
      )}

      {analysis && (
        <Section step="2b" title="양식 미리보기 (placeholder 삽입됨)">
          <div className="preview-area bg-white rounded-lg p-4 max-h-[28rem] overflow-auto border border-slate-200"
            dangerouslySetInnerHTML={{ __html: analysis.preview_html || '' }} />
          <p className="text-xs text-slate-400 mt-2">
            파란 글씨 <code className="bg-slate-100 px-1">{'{{key}}'}</code> 부분이 자동 채워질 위치입니다.
          </p>
        </Section>
      )}

      {analysis && (
        <Section step={3} title="자연어 설명 + 영수증 → AI 자동작성">
          <Label>자연어 설명</Label>
          <textarea rows={5}
            className="w-full bg-white border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200"
            placeholder="예) 2026-05-07 강남에서 ABC사와 미팅. 점심 38,000원 / 택시 24,000원."
            value={description2} onChange={(e) => setDescription2(e.target.value)} />
          <div className="mt-4">
            <Label>영수증/이미지 첨부 (선택, 여러 장 가능)</Label>
            <DropZone accept="image/*" multiple files={receipts}
              onFiles={(list) => setReceipts((prev) => [...prev, ...list])}
              hint="이미지를 끌어다 놓거나 클릭해서 추가하세요." />
            {receipts.length > 0 && (
              <div className="mt-2 flex items-center justify-between text-xs">
                <span className="text-slate-500">{receipts.length}개 첨부됨</span>
                <button className="text-red-500 hover:text-red-700" onClick={() => setReceipts([])}>첨부 모두 지우기</button>
              </div>
            )}
          </div>
          <ErrorBox>{generateError}</ErrorBox>
          <div className="mt-5 flex justify-end">
            <Button onClick={handleGenerate} loading={generating}>
              {generating ? '작성 중...' : 'AI로 자동 작성'}
            </Button>
          </div>
        </Section>
      )}

      {generated && (
        <Section step={4} title="자동 작성 결과 (최종 미리보기)">
          <div className="bg-white rounded-lg p-4 border border-slate-200 max-h-[40rem] overflow-auto"
            dangerouslySetInnerHTML={{ __html: generated.filled_html || '' }} />
          <details className="mt-4">
            <summary className="cursor-pointer text-sm text-slate-600 hover:text-slate-800">
              AI가 채운 데이터(JSON) 보기
            </summary>
            <pre className="mt-2 bg-slate-900 text-slate-100 rounded-lg p-3 text-xs overflow-auto">
{JSON.stringify(generated.expense_data, null, 2)}
            </pre>
          </details>
          <div className="mt-4 flex gap-2 justify-end">
            <Button variant="secondary" onClick={downloadHtml}>HTML 다운로드</Button>
            <Button onClick={handleGenerate} loading={generating}>다시 작성</Button>
          </div>
        </Section>
      )}
    </div>
  )
}