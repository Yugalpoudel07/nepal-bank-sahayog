// Welcome.jsx
// Shown before the first message: greeting and suggested questions in both languages.

import { useI18n } from '../i18n/context';

const Welcome = ({ currentFy, onPick }) => {
  const { t } = useI18n();
  const fyStart = currentFy ? currentFy.split('/')[0] : '';
  const suggestions = t.suggestions(fyStart);

  return (
    <div className="flex flex-col items-center text-center py-8 px-4 gap-4">
      <div
        className="w-24 h-24 rounded-2xl bg-gradient-to-br from-nepal-blue to-[#00246b] flex items-center justify-center text-5xl text-white shadow-lg"
        style={{ boxShadow: '0 6px 30px rgba(220,20,60,0.2)' }}
        aria-hidden="true"
      >
        ₨
      </div>
      <div>
        <h2 className="text-xl font-bold text-gray-800">{t.welcomeTitle}</h2>
        <p className="text-gray-500 text-sm mt-1 max-w-md">{t.welcomeBody}</p>
      </div>

      <p className="text-xs font-semibold uppercase tracking-widest text-gray-400 mt-2">{t.suggestionsLabel}</p>
      <ul className="flex flex-wrap justify-center gap-2 max-w-xl">
        {suggestions.map((s) => (
          <li key={s}>
            <button
              type="button"
              onClick={() => onPick(s)}
              className="px-4 py-2 bg-white border border-nepal-blue/20 text-nepal-blue text-xs font-medium rounded-full hover:bg-nepal-blue hover:text-white transition-all duration-200 shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-nepal-blue/60"
            >
              {s}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
};

export default Welcome;
