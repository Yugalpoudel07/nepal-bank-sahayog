// SourceCards.jsx
// Collapsible list of the documents the answer is based on.

import { useI18n } from '../i18n/context';

const DOC_TYPE_STYLE = {
  act: 'bg-blue-100 text-nepal-blue',
  rules: 'bg-sky-100 text-sky-800',
  directive: 'bg-red-100 text-nepal-red',
  circular: 'bg-emerald-100 text-emerald-800',
  notice: 'bg-violet-100 text-violet-800',
  policy: 'bg-teal-100 text-teal-800',
  monetary_policy: 'bg-orange-100 text-orange-800',
};

const SourceCards = ({ sources }) => {
  const { t } = useI18n();
  if (!sources?.length) return null;

  return (
    <details className="mt-3 group/src rounded-xl border border-gray-200 bg-gray-50/60">
      <summary className="cursor-pointer select-none px-3 py-2 text-xs font-semibold text-gray-600 flex items-center gap-2 rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-nepal-blue/50">
        <span className="transition-transform group-open/src:rotate-90" aria-hidden="true">▸</span>
        📚 {t.sourcesTitle(sources.length)}
      </summary>
      <ul className="px-3 pb-3 flex flex-col gap-2">
        {sources.map((s) => (
          <li key={s.chunk_id} className={`rounded-lg bg-white border border-gray-200 px-3 py-2 ${s.status !== 'current' ? 'opacity-75' : ''}`}>
            <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
              <span className="font-mono text-gray-400">[{s.id}]</span>
              <span className={`px-1.5 py-0.5 rounded font-semibold uppercase tracking-wide ${DOC_TYPE_STYLE[s.doc_type] || 'bg-gray-100 text-gray-700'}`}>
                {t.docTypes[s.doc_type] || s.doc_type.replace('_', ' ')}
              </span>
              {s.institutions?.filter((i) => i !== 'all').map((i) => (
                <span key={i} className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700">{t.institutions[i] || i}</span>
              ))}
              {s.fy && <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-700">FY {s.fy}</span>}
              {s.status === 'previous_version' && (
                <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-800">{t.statusPrevious}</span>
              )}
              {s.status === 'amendment' && (
                <span className="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800">{t.statusAmendment}</span>
              )}
              {s.status === 'overridden' && (
                <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-800">{t.statusOverridden}</span>
              )}
            </div>
            <p className="mt-1 text-sm font-semibold text-gray-800">
              {s.doc_label || s.title}
              {s.section && <span className="font-normal text-gray-600"> — {s.section}</span>}
              {s.page && <span className="font-normal text-gray-400"> · {t.page} {s.page}</span>}
            </p>
            {s.doc_label && s.doc_label !== s.title && <p className="text-[11px] text-gray-500">{s.title}</p>}
            <p className="mt-0.5 text-xs text-gray-600 leading-relaxed">{s.snippet}</p>
            {s.source_url && (
              <a
                href={s.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1 inline-block text-xs font-medium text-nepal-blue underline underline-offset-2"
              >
                {t.openSource} ↗
              </a>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
};

export default SourceCards;
