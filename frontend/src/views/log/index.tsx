import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/log'
const columns = [
  "记录编号",
  "流作业名",
  "日志级别",
  "日志内容",
  "发生时间",
  "异常类型",
  "处理状态",
  "记录状态"
]
const actions = [
  "分析日志",
  "标记处理",
  "归档日志"
]
const statuses = [
  "已记录",
  "已分析",
  "已处理",
  "已归档"
]
const stats = [
  {
    "label": "日志记录总数",
    "value": 0
  },
  {
    "label": "已分析记录",
    "value": 0
  },
  {
    "label": "已处理记录",
    "value": 0
  }
]

type Row = Record<string, string | number | null>
type OriginalLog = {
  记录编号?: string
  流作业名?: string
  日志级别?: string
  日志内容?: string
  异常类型?: string
}

const originalFields = ["日志级别", "日志内容", "异常类型"] as const

export default function LogView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [selected, setSelected] = useState<Row | null>(null)
  const filterFields = useMemo(() => ["记录编号", "流作业名"] as const, [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('日志记录列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      const counters = await Promise.all([
        request(`${ENDPOINT}?size=1`),
        request(`${ENDPOINT}?status=${encodeURIComponent('已分析')}&size=1`),
        request(`${ENDPOINT}?status=${encodeURIComponent('已处理')}&size=1`),
      ])
      const numbers = await Promise.all(counters.map(async (item) => {
        const body = await item.json()
        return Number(body.total ?? 0)
      }))
      setCounts({ 总计: numbers[0], 已分析: numbers[1], 已处理: numbers[2] })
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '流处理日志中心列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function openDetail(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}`)
      const body = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(body.detail ?? '日志详情读取失败')
      }
      setSelected(body)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '流处理日志中心详情读取失败')
    }
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok || body.ok === false) {
        throw new Error(body.message ?? body.detail ?? '流处理日志中心动作未生效，请稍后重试')
      }
      setNotice(body.message ?? `${action}已提交`)
      await reload()
      if (selected && selected.id === row.id) {
        const refreshed = await request(`${ENDPOINT}/${row.id}`)
        if (refreshed.ok) {
          setSelected(await refreshed.json())
        }
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '流处理日志中心操作失败')
    }
  }

  const statCards = stats.map((item, index) => ({
    ...item,
    value: index === 0 ? (counts.总计 ?? total) : index === 1 ? (counts.已分析 ?? 0) : (counts.已处理 ?? 0),
  }))

  return (
    <section className="page" data-module="log">
      <header className="page-head">
        <div>
          <h2>流处理日志中心管理</h2>
          <p className="page-desc">维护流处理日志的级别分类、异常记录与处理追踪，为流处理日志中心提供可追溯的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('日志记录登记入口尚未接入审批流')}>
            登记日志记录
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出流处理日志中心清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {statCards.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <label className="filter-item">
          <span>处理状态</span>
          <select
            value={filters.status ?? ''}
            onChange={(event) => setFilters((prev) => ({ ...prev, status: event.target.value }))}
          >
            <option value="">全部</option>
            {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => { setFilters({}); setSelected(null) }}>重置条件</button>
      </form>

      <div className="split-pane">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((column) => <th key={column}>{column}</th>)}
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row.id)} className={selected?.id === row.id ? 'row-selected' : undefined}>
                {columns.map((column) => (
                  <td key={column}>
                    {column === '记录编号' ? (
                      <button className="link" type="button" onClick={() => void openDetail(row)}>
                        {row[column] ?? '—'}
                      </button>
                    ) : (row[column] ?? '—')}
                  </td>
                ))}
                <td className="row-actions">
                  {actions.map((action) => (
                    <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                      {action}
                    </button>
                  ))}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td className="empty-state" colSpan={columns.length + 1}>暂无流处理日志中心数据，可先登记日志记录</td>
              </tr>
            )}
          </tbody>
        </table>

        {selected && (
          <aside className="detail-panel">
            <header className="detail-head">
              <h3>日志处理数据结果</h3>
              <button className="link" type="button" onClick={() => setSelected(null)}>关闭</button>
            </header>
            <dl className="detail-list">
              <div className="detail-line"><dt>记录编号</dt><dd>{selected['记录编号'] ?? '—'}</dd></div>
              <div className="detail-line"><dt>流作业名</dt><dd>{selected['流作业名'] ?? '—'}</dd></div>
              <div className="detail-line"><dt>处理状态</dt><dd>{selected['处理状态'] ?? selected.status ?? '—'}</dd></div>
              <div className="detail-line"><dt>异常计数</dt><dd>{selected['异常计数'] ?? 0}</dd></div>
              <div className="detail-line"><dt>分析结论</dt><dd>{selected['分析结论'] || '尚未分析'}</dd></div>
            </dl>
            <h4 className="detail-subtitle">原始日志</h4>
            <dl className="detail-list">
              {originalFields.map((field) => (
                <div key={field} className="detail-line">
                  <dt>{field}</dt>
                  <dd>{(selected.原始日志 as OriginalLog | undefined)?.[field] || selected[field] || '—'}</dd>
                </div>
              ))}
            </dl>
          </aside>
        )}
      </div>

      <footer className="page-foot">
        <span>共 {total} 条流处理日志中心记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
