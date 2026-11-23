// Header.jsx
// Compact hero banner in the reference project's Nepal blue/red style, with the
// fiscal-year selector, the English/Nepali toggle and a new-chat button.

import { useI18n } from '../i18n/context';

const NepalFlag = () => (
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 75" width="22" height="28" aria-hidden="true">
    <polygon points="4,70 4,4 46,37 4,37 40,70" fill="#003893" />
    <polygon points="8,64 8,10 40,37 8,37 36,64" fill="#DC143C" />
    <circle cx="20" cy="28" r="3.5" fill="white" />
    <circle cx="23" cy="28" r="3.5" fill="#DC143C" />
    <circle cx="20" cy="46" r="5" fill="white" />
    <circle cx="24" cy="46" r="5" fill="#DC143C" />
    <circle cx="20" cy="46" r="2" fill="white" />
  </svg>
);

const Header = ({ fyOptions, selectedFy, onFyChange, onNewChat }) => {
  const { t, lang, setLang } = useI18n();

  return (
    <header
      className="relative w-full flex-shrink-0"
      style={{ background: 'linear-gradient(120deg, #003893 0%, #00246b 55%, #4a0d1f 100%)' }}
    >
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-3 sm:py-4 flex flex-col sm:flex-row sm:items-center gap-3">
        {/* Title */}
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <div className="w-11 h-11 rounded-xl bg-white/10 border border-white/20 flex items-center justify-center shadow-lg">
            <NepalFlag />
          </div>
          <div className="min-w-0">
            <h1 className="text-xl sm:text-2xl font-extrabold text-white leading-tight tracking-tight">
              {t.appName} <span className="text-[#FF6B6B]">{t.appNameAccent}</span>
            </h1>
            <p className="text-blue-200 text-xs sm:text-sm truncate">{t.tagline}</p>
          </div>
        </div>

        {/* Controls */}
        <div className="flex items-center gap-2 flex-wrap">
          <label className="flex items-center gap-2 text-white/80 text-xs font-medium">
            <span>{t.fiscalYear}</span>
            <select
              value={selectedFy}
              onChange={(e) => onFyChange(e.target.value)}
              disabled={!fyOptions.length}
              className="bg-white/10 border border-white/25 text-white text-sm font-semibold rounded-lg px-2 py-1.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
            >
              {fyOptions.map((o) => (
                <option key={o.value} value={o.value} className="text-gray-900">
                  {o.value} {o.isCurrent ? t.currentSuffix : ''} {o.hasDocs ? '' : t.noDocsSuffix}
                </option>
              ))}
            </select>
          </label>

          <button
            type="button"
            onClick={() => setLang(lang === 'en' ? 'ne' : 'en')}
            aria-label={t.languageToggleLabel}
            className="px-3 py-1.5 rounded-lg bg-white/10 border border-white/25 text-white text-sm font-semibold hover:bg-white/20 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
          >
            {t.languageToggle}
          </button>

          <button
            type="button"
            onClick={onNewChat}
            className="px-3 py-1.5 rounded-lg bg-gradient-to-br from-nepal-red to-nepal-red-dark text-white text-sm font-semibold shadow hover:brightness-110 transition focus:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
          >
            + {t.newChat}
          </button>
        </div>
      </div>
    </header>
  );
};

export default Header;
