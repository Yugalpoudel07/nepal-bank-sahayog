// CalculationCard.jsx
// Shows deterministic calculator output: loan-loss provision per loan, or the cash-limit check.

import { useI18n } from '../i18n/context';
import { formatNum, formatPct, formatRs } from '../format';

const ErrorCard = ({ message }) => {
  const { t } = useI18n();
  return (
    <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900" role="status">
      <p className="font-semibold">{t.calcUnavailable}</p>
      <p className="mt-0.5">{message}</p>
    </div>
  );
};

const Shell = ({ fy, children, source, notes }) => {
  const { t } = useI18n();
  return (
    <section className="mt-3 rounded-xl border border-nepal-blue/15 bg-blue-50/50 overflow-hidden text-sm" aria-label={t.calcTitle}>
      <header className="flex items-center justify-between px-4 py-2 bg-nepal-blue text-white">
        <span className="font-semibold">🧮 {t.calcTitle}</span>
        <span className="text-xs bg-white/15 rounded-full px-2 py-0.5">{t.calcFor(fy)}</span>
      </header>
      {children}
      {notes?.length > 0 && (
        <ul className="px-4 py-2 text-[11px] text-gray-600 border-t border-blue-100 list-disc list-inside">
          {notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
      {source && (
        <p className="px-4 py-2 text-[11px] text-gray-500 border-t border-blue-100">
          {t.calcSource}: {source}
        </p>
      )}
    </section>
  );
};

const ProvisionCard = ({ c }) => {
  const { t } = useI18n();
  return (
    <Shell fy={c.fiscal_year} source={c.rate_source} notes={c.notes}>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-xs text-gray-500">
            <tr>
              <th className="text-left font-medium px-3 py-2">#</th>
              <th className="text-right font-medium px-1.5 py-2">{t.loanAmount}</th>
              <th className="text-left font-medium px-1.5 py-2">{t.category}</th>
              <th className="text-right font-medium px-1.5 py-2">{t.rate}</th>
              <th className="text-right font-medium px-3 py-2">{t.provision}</th>
            </tr>
          </thead>
          <tbody>
            {c.rows.map((row) => (
              <tr key={row.loan} className="border-t border-blue-100">
                <td className="px-3 py-1.5 text-gray-500">{row.loan}</td>
                <td className="px-1.5 py-1.5 text-right tabular-nums whitespace-nowrap">{formatNum(row.amount)}</td>
                <td className="px-1.5 py-1.5 whitespace-nowrap">
                  {t.categoryNames[row.category]}
                  {row.months_overdue !== null && <span className="text-gray-400"> · {row.months_overdue}m</span>}
                </td>
                <td className="px-1.5 py-1.5 text-right tabular-nums">{formatPct(row.rate_percent)}</td>
                <td className="px-3 py-1.5 text-right tabular-nums font-medium whitespace-nowrap">{formatNum(row.provision)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <dl className="grid grid-cols-2 gap-2 px-4 py-3 border-t border-blue-100 bg-white/70">
        <div>
          <dt className="text-xs text-gray-500">{t.totalLoans}</dt>
          <dd className="font-semibold tabular-nums">{formatRs(c.total_amount)}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">{t.totalProvision}</dt>
          <dd className="font-bold text-nepal-red tabular-nums">{formatRs(c.total_provision)}</dd>
        </div>
      </dl>
    </Shell>
  );
};

const CashCard = ({ c }) => {
  const { t } = useI18n();
  return (
    <Shell fy={c.fiscal_year} source={c.rate_source} notes={c.notes.slice(1)}>
      <div className={`px-4 py-3 font-semibold ${c.at_or_above_limit ? 'text-nepal-red' : 'text-emerald-700'}`}>
        {c.at_or_above_limit ? `⚠ ${t.cashOver}` : `✓ ${t.cashUnder}`}
      </div>
      <dl className="grid grid-cols-2 gap-2 px-4 pb-3">
        <div>
          <dt className="text-xs text-gray-500">{t.cashAmount}</dt>
          <dd className="font-semibold tabular-nums">{formatRs(c.amount)}</dd>
        </div>
        <div>
          <dt className="text-xs text-gray-500">{t.cashLimit}</dt>
          <dd className="font-semibold tabular-nums">{formatRs(c.limit)}</dd>
        </div>
      </dl>
    </Shell>
  );
};

const CalculationCard = ({ calculation }) => {
  if (!calculation) return null;
  if (calculation.error) return <ErrorCard message={calculation.error} />;
  if (calculation.type === 'loan_provision') return <ProvisionCard c={calculation} />;
  if (calculation.type === 'cash_limit') return <CashCard c={calculation} />;
  return null;
};

export default CalculationCard;
